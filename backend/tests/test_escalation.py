"""Automatic SLA escalation: idempotency, rechecks, concurrency and the policy lock (Milestone 5B).

Most tests run inside the test's rolled-back transaction. The concurrency tests need real,
separate transactions: they commit synthetic rows and remove them afterwards (see `committed`).
"""
import logging
import secrets
import threading
import time
from contextlib import contextmanager
from datetime import timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app import cli
from app.database import engine
from app.escalation import escalate_target, run_escalations
from app.models import (
    CommentVisibility, Incident, IncidentComment, IncidentEscalation, IncidentEvent, IncidentEventAction, IncidentStatus, SlaTarget,
)
from app import audit, sla_store
from app.sla import SLA_WORKER_LOCK, lock_policy_exclusive, lock_policy_shared
from sla_support import T0, Timeline, minutes

CANARY = "CANARY-escalation-9d2c"


@pytest.fixture
def tl(db, reporter, responder, connection):
    timeline = Timeline(db, reporter, responder)
    timeline.policy(T0 - timedelta(days=1))
    yield timeline
    # Every scenario doubles as a consistency check: the stored read model, maintained by the
    # write path, must equal the values derived independently from incident_events.
    checked, mismatches, ids = sla_store.verify(connection)
    assert mismatches == 0, f"stored SLA cycles differ from the audit-derived reference for incidents {ids}"


def _escalations(db, incident_id=None):
    query = select(IncidentEscalation).order_by(IncidentEscalation.id)
    if incident_id is not None:
        query = query.where(IncidentEscalation.incident_id == incident_id)
    db.expire_all()
    return db.scalars(query).all()


# ---- One worker --------------------------------------------------------------------------------

def test_overdue_response_is_escalated_once(tl, db, connection):
    incident = tl.incident(severity="high")  # response due T0 + 60 min
    now = T0 + minutes(61)
    first = run_escalations(connection, now=now)
    second = run_escalations(connection, now=now + minutes(5))
    assert (first.ran, first.checked, first.recorded) == (True, 1, 1)
    assert (second.checked, second.recorded) == (0, 0)
    [escalation] = _escalations(db, incident.id)
    assert (escalation.cycle, escalation.target, escalation.deadline_at) == (0, SlaTarget.RESPONSE, T0 + minutes(60))
    assert (escalation.triggered_by, escalation.created_at) == ("system", now)
    assert escalation.policy_version_id == tl.cycles(incident, now)[0].version_id


@pytest.mark.parametrize("now_offset", [minutes(30), minutes(45), minutes(60) - timedelta(microseconds=1)])
def test_targets_not_yet_overdue_are_not_escalated(now_offset, tl, db, connection):
    incident = tl.incident(severity="high")
    assert run_escalations(connection, now=T0 + now_offset).recorded == 0
    assert _escalations(db, incident.id) == []


def test_exactly_at_the_deadline_is_escalated(tl, db, connection):
    incident = tl.incident(severity="high")
    assert run_escalations(connection, now=T0 + minutes(60)).recorded == 1
    assert len(_escalations(db, incident.id)) == 1


def test_targets_satisfied_before_the_run_are_never_escalated_retrospectively(tl, db, connection):
    incident = tl.incident(severity="high")
    tl.change(incident, T0 + minutes(90), status="investigating")  # acknowledged late: breached
    now = T0 + minutes(120)
    assert tl.cycles(incident, now)[0].response_state == "breached"
    assert run_escalations(connection, now=now).recorded == 0
    assert _escalations(db, incident.id) == []


def test_resolution_is_escalated_per_cycle_and_response_only_in_the_first(tl, db, connection):
    incident = tl.incident(severity="high")
    tl.change(incident, T0 + minutes(5), status="investigating")
    run_escalations(connection, now=T0 + minutes(481))  # resolution due T0 + 480
    tl.change(incident, T0 + minutes(500), status="resolved")
    reopen = T0 + timedelta(days=1)
    tl.change(incident, reopen, status="open")
    run_escalations(connection, now=reopen + minutes(481))
    assert [(e.cycle, e.target) for e in _escalations(db, incident.id)] == [(0, SlaTarget.RESOLUTION), (1, SlaTarget.RESOLUTION)]


def test_untracked_closed_and_legacy_incidents_are_ignored(tl, db, connection):
    old = tl.incident(at=T0 - timedelta(days=3650))
    closed = tl.incident()
    tl.change(closed, T0 + minutes(1), status="closed")
    assert run_escalations(connection, now=T0 + timedelta(days=30)).recorded == 0
    assert _escalations(db, old.id) == _escalations(db, closed.id) == []


def test_escalation_does_not_touch_the_incident_or_its_history(tl, db, connection):
    incident = tl.incident()
    version = lambda: db.scalar(text("SELECT ctid::text FROM incidents WHERE id = :id"), {"id": incident.id})  # noqa: E731
    events = lambda: db.scalar(select(func.count()).select_from(IncidentEvent).where(IncidentEvent.incident_id == incident.id))  # noqa: E731
    before = (version(), events())
    assert run_escalations(connection, now=T0 + minutes(61)).recorded == 1
    assert (version(), events()) == before


def test_logs_contain_counts_and_ids_only(tl, db, connection, responder, caplog):
    incident = tl.incident()
    incident.title = f"{CANARY} title"
    db.add(IncidentComment(incident_id=incident.id, author_id=responder.id, visibility=CommentVisibility.INTERNAL, body=f"{CANARY} note"))
    db.commit()
    with caplog.at_level(logging.INFO, logger="app.escalation"):
        run_escalations(connection, now=T0 + minutes(61))
    assert "recorded" in caplog.text and str(incident.id) in caplog.text
    assert CANARY not in caplog.text


def test_a_run_is_skipped_while_another_worker_holds_the_lock(tl, db, connection):
    tl.incident()
    with engine.connect() as other:
        assert other.execute(select(func.pg_try_advisory_lock(SLA_WORKER_LOCK))).scalar_one()
        try:
            run = run_escalations(connection, now=T0 + minutes(61))
        finally:
            other.execute(select(func.pg_advisory_unlock(SLA_WORKER_LOCK)))
    assert (run.ran, run.recorded) == (False, 0)
    assert run_escalations(connection, now=T0 + minutes(61)).recorded == 1


def test_cli_rejects_out_of_range_intervals():
    assert cli.main(["sla-escalate", "--interval", "5"]) == 2


# ---- Database guarantees ------------------------------------------------------------------------

def _row(incident_id, version_id, **overrides):
    values = {"incident_id": incident_id, "cycle": 0, "target": "response", "deadline_at": T0, "policy_version_id": version_id, **overrides}
    columns = ", ".join(values)
    return f"INSERT INTO incident_escalations ({columns}) VALUES ({', '.join(':' + c for c in values)})", values


@pytest.mark.parametrize("overrides", [{"triggered_by": "admin"}, {"cycle": 1, "target": "response"}, {"cycle": -1, "target": "resolution"}])
def test_database_refuses_invalid_escalations(overrides, tl, db):
    incident = tl.incident()
    version_id = tl.cycles(incident, T0)[0].version_id
    sql, values = _row(incident.id, version_id, **overrides)
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            db.execute(text(sql), values)


def test_database_refuses_a_duplicate_escalation(tl, db):
    incident = tl.incident()
    version_id = tl.cycles(incident, T0)[0].version_id
    sql, values = _row(incident.id, version_id)
    db.execute(text(sql), values)
    with pytest.raises(IntegrityError, match="uq_incident_escalations_incident_cycle_target"):
        with db.begin_nested():
            db.execute(text(sql), values)


@pytest.mark.parametrize("statement", [
    "UPDATE incident_escalations SET deadline_at = deadline_at",
    "DELETE FROM incident_escalations",
    "TRUNCATE incident_escalations",
    "UPDATE sla_policy_versions SET reason = 'changed'",
    "DELETE FROM sla_policy_targets",
    "TRUNCATE sla_policy_versions CASCADE",
])
def test_sla_tables_are_append_only(statement, tl, db, connection):
    tl.incident()
    run_escalations(connection, now=T0 + minutes(61))
    with pytest.raises(DBAPIError, match="append-only"):
        with db.begin_nested():
            db.execute(text(statement))


# ---- Concurrency (real transactions) ----------------------------------------------------------------

@contextmanager
def committed():
    """Commits a reporter, a policy and one high-severity incident created at T0; removes them afterwards.

    The SLA tables and incident_events are append-only, so cleanup runs with
    session_replication_role = replica (test database only), which skips their row triggers.
    """
    tag = secrets.token_hex(4)
    with engine.begin() as c:
        user_id = c.execute(text("INSERT INTO users (email, password_hash, role) VALUES (:e, 'unused', 'reporter') RETURNING id"),
                            {"e": f"sla.concurrency.{tag}@example.com"}).scalar_one()
        version_id = c.execute(text("INSERT INTO sla_policy_versions (source, reason, created_at) VALUES ('default', 'Concurrency test', :at) RETURNING id"),
                               {"at": T0 - timedelta(days=1)}).scalar_one()
        for severity, (response, resolution) in {"critical": (15, 240), "high": (60, 480), "medium": (240, 4320), "low": (1440, 10080)}.items():
            c.execute(text("INSERT INTO sla_policy_targets VALUES (:v, CAST(:s AS incident_severity), :r, :d)"), {"v": version_id, "s": severity, "r": response, "d": resolution})
        incident_id = c.execute(text(
            "INSERT INTO incidents (title, description, severity, status, reporter_id, created_at, updated_at) "
            "VALUES ('Concurrency', 'Synthetic', 'high', 'open', :u, :t, :t) RETURNING id"), {"u": user_id, "t": T0}).scalar_one()
        with Session(bind=c) as db:
            incident = db.get(Incident, incident_id)
            changes = audit.creation_changes(incident)
            db.add(IncidentEvent(incident_id=incident_id, actor_id=user_id, action=IncidentEventAction.CREATED, changes=changes, created_at=T0))
            sla_store.record_change(db, incident, IncidentEventAction.CREATED, changes, T0)
            db.flush()
    try:
        yield incident_id, user_id
    finally:
        with engine.begin() as c:
            c.execute(text("SET LOCAL session_replication_role = replica"))
            c.execute(text("DELETE FROM incident_escalations WHERE incident_id = :i"), {"i": incident_id})
            c.execute(text("DELETE FROM incident_sla_cycles WHERE incident_id = :i"), {"i": incident_id})
            c.execute(text("DELETE FROM incident_events WHERE incident_id = :i"), {"i": incident_id})
            c.execute(text("DELETE FROM incidents WHERE id = :i"), {"i": incident_id})
            c.execute(text("DELETE FROM sla_policy_targets WHERE version_id = :v"), {"v": version_id})
            c.execute(text("DELETE FROM sla_policy_versions WHERE id = :v OR created_by = :u"), {"v": version_id, "u": user_id})
            c.execute(text("DELETE FROM users WHERE id = :u"), {"u": user_id})


def _wait_for_lock_waiters(expected: int = 1, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    with engine.connect() as probe:
        while time.monotonic() < deadline:
            if probe.execute(text("SELECT count(*) FROM pg_locks WHERE NOT granted")).scalar_one() >= expected:
                return
            probe.rollback()
            time.sleep(0.02)
    raise AssertionError("no transaction started waiting for a lock")


def _resolve(conn, incident_id, user_id, at):
    """What PATCH /incidents/{id} does for a status change: row lock, update, audit event and the
    stored SLA cycle, in one transaction (the caller commits)."""
    db = Session(bind=conn, autoflush=False)
    incident = db.scalars(select(Incident).where(Incident.id == incident_id).with_for_update(of=Incident)).one()  # as the app does
    before = audit.snapshot(incident)
    incident.status = IncidentStatus.RESOLVED
    diff = audit.diff(before, audit.snapshot(incident))
    action = audit.classify(diff)
    db.add(IncidentEvent(incident_id=incident_id, actor_id=user_id, action=action, changes=diff, created_at=at))
    lock_policy_shared(db)
    sla_store.record_change(db, incident, action, diff, at)
    db.flush()


def _count(incident_id):
    with engine.connect() as c:
        return c.execute(text("SELECT count(*) FROM incident_escalations WHERE incident_id = :i"), {"i": incident_id}).scalar_one()


def test_resolution_committed_while_the_worker_waits_prevents_the_escalation():
    with committed() as (incident_id, user_id):
        with engine.connect() as user_conn:
            user_tx = user_conn.begin()
            _resolve(user_conn, incident_id, user_id, T0 + minutes(70))  # holds the incident lock
            result = {}
            worker = threading.Thread(target=lambda: result.update(run=run_escalations(engine, now=T0 + minutes(90))))
            worker.start()
            _wait_for_lock_waiters()  # the worker found the overdue target and now waits for the row
            user_tx.commit()
            worker.join(15)
        run = result["run"]
        assert (run.checked, run.recorded, run.skipped) == (1, 0, 1)
        assert _count(incident_id) == 0


def test_worker_first_then_resolution_records_exactly_one_escalation():
    with committed() as (incident_id, user_id):
        with Session(bind=engine) as worker:
            assert escalate_target(worker, incident_id, 0, SlaTarget.RESPONSE, T0 + minutes(90))  # holds the row lock
            done = threading.Event()

            def user_update():
                with engine.begin() as conn:
                    conn.execute(text("SET LOCAL lock_timeout = '15s'"))
                    _resolve(conn, incident_id, user_id, T0 + minutes(95))
                done.set()

            user = threading.Thread(target=user_update)
            user.start()
            _wait_for_lock_waiters()  # the update waits for the worker
            assert not done.is_set()
            worker.commit()
            user.join(15)
        assert done.is_set() and _count(incident_id) == 1
        with engine.connect() as c:
            assert c.execute(text("SELECT status FROM incidents WHERE id = :i"), {"i": incident_id}).scalar_one() == "resolved"


def test_two_concurrent_escalations_of_one_target_record_one_row():
    with committed() as (incident_id, _):
        barrier = threading.Barrier(2)
        results = []

        def escalate():
            with Session(bind=engine) as db:
                barrier.wait()
                results.append(escalate_target(db, incident_id, 0, SlaTarget.RESPONSE, T0 + minutes(90)))
                db.commit()

        threads = [threading.Thread(target=escalate) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(15)
        assert sorted(results) == [False, True]
        assert _count(incident_id) == 1


def test_two_concurrent_worker_runs_record_one_escalation():
    with committed() as (incident_id, _):
        barrier = threading.Barrier(2)
        runs = []

        def work():
            barrier.wait()
            runs.append(run_escalations(engine, now=T0 + minutes(90)))

        threads = [threading.Thread(target=work) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        assert sum(r.recorded for r in runs) == 1
        assert _count(incident_id) == 1


def test_policy_change_waits_for_in_flight_incident_writes():
    """Refinement 6: a new version's created_at is always later than any cycle start already
    being committed, so existing cycles can never switch to it."""
    with committed() as (_, user_id):
        with engine.connect() as writer:
            writer_tx = writer.begin()
            writer.execute(text("SELECT pg_advisory_xact_lock_shared(:k)"), {"k": 51_000_001})
            write_started = writer.execute(text("SELECT now()")).scalar_one()  # an event would carry this time
            created = {}

            def change_policy():
                with Session(bind=engine) as db:
                    lock_policy_exclusive(db)
                    created["at"] = db.execute(text(
                        "INSERT INTO sla_policy_versions (source, created_by, reason, created_at) "
                        "VALUES ('admin', :u, 'Concurrency test', clock_timestamp()) RETURNING created_at"), {"u": user_id}).scalar_one()
                    db.commit()

            policy = threading.Thread(target=change_policy)
            policy.start()
            _wait_for_lock_waiters()
            assert "at" not in created  # still waiting for the in-flight write
            writer_tx.commit()
            committed_at = time.time()
            policy.join(15)
        assert created["at"] > write_started
        assert created["at"].timestamp() >= committed_at - 1


def test_a_failed_run_reports_its_own_error_and_releases_the_lock(monkeypatch):
    """A database error during a run (seen when migrations had not been applied) must surface as itself,
    not as a failed unlock in the aborted transaction, and must release the worker lock."""
    from app import escalation

    def broken(conn, now, limit):
        conn.execute(text("SELECT * FROM table_that_does_not_exist"))
    monkeypatch.setattr(escalation, "due_targets", broken)
    with pytest.raises(DBAPIError, match="table_that_does_not_exist"):
        run_escalations(engine)
    with engine.connect() as probe:
        assert probe.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": SLA_WORKER_LOCK}).scalar_one() is True
        probe.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": SLA_WORKER_LOCK})
        probe.commit()
