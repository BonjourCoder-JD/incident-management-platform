"""Stored SLA read model (incident_sla_cycles): equivalence with the audit-derived reference, backfill,
rollback, history guards and concurrent updates (Milestone 5B). All data is synthetic."""
import importlib.util
import random
import secrets
import threading
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app import cli, sla_store
from app.database import engine
from app.main import app
from app.models import Incident, IncidentSlaCycle, Severity
from app.sla import current_sla, sla_cycles
from conftest import BASE_URL, TEST_PASSWORD
from sla_support import T0, Timeline, minutes

MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "versions" / "0007_sla_policies_and_escalations.py"


def _migration():
    spec = importlib.util.spec_from_file_location("migration_0007", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def tl(db, reporter, responder):
    timeline = Timeline(db, reporter, responder)
    timeline.policy(T0 - timedelta(days=1))
    return timeline


def _random_timeline(tl, rng: random.Random, start):
    """A random but valid incident history: status moves (including reopens), severity changes and
    assignments at increasing times."""
    incident = tl.incident(at=start, severity=rng.choice(["low", "medium", "high", "critical"]),
                           assignee=tl.staff if rng.random() < 0.15 else None)
    at = start
    for _ in range(rng.randint(0, 9)):
        at += timedelta(minutes=rng.choice([1, 7, 20, 45, 59, 60, 61, 90, 300, 500, 1500]))
        options = {
            "open": ["investigating", "resolved", "closed"],
            "investigating": ["open", "resolved", "closed"],
            "resolved": ["closed", "open", "investigating"],
            "closed": ["open", "investigating"],
        }[incident.status.value]
        change = {}
        roll = rng.random()
        if roll < 0.55:
            change["status"] = rng.choice(options)
        elif roll < 0.85:
            change["severity"] = rng.choice([s for s in ("low", "medium", "high", "critical") if s != incident.severity.value])
        else:
            change["assignee_id"] = None if incident.assignee_id else tl.staff.id
        if rng.random() < 0.2 and "severity" not in change:
            change["severity"] = rng.choice([s for s in ("low", "medium", "high", "critical") if s != incident.severity.value])
        tl.change(incident, at, **change)
    return incident


# ---- Equivalence with the audit-derived reference -------------------------------------------------

def test_random_histories_match_the_reference_in_every_field_state_and_filter(tl, db, connection):
    rng = random.Random(20261115)
    incidents = [_random_timeline(tl, rng, T0 + timedelta(minutes=i * 3)) for i in range(60)]
    ids = [i.id for i in incidents]
    # Escalate some current and some earlier cycles, so the escalated filter is exercised both ways.
    from app.escalation import run_escalations
    run_escalations(connection, now=T0 + timedelta(minutes=700), limit=40)
    candidates = select(Incident.id.label("id")).where(Incident.id.in_(ids))
    checked, mismatches, sample = sla_store.verify(connection, candidates)
    assert (mismatches, sample) == (0, []) and checked >= len(ids)
    assert db.scalar(select(func.count()).select_from(IncidentSlaCycle).where(IncidentSlaCycle.cycle > 0)) > 5  # reopens exercised
    columns = ("incident_id", "cycle", "tracked", "response_state", "response_due", "acknowledged_at", "resolution_state",
               "resolution_due", "resolved_at", "summary_state", "summary_target", "summary_due", "escalated")
    for now in [T0 + timedelta(minutes=m) for m in (0, 30, 59, 60, 61, 300, 479, 480, 481, 2000, 20000)]:
        derived = current_sla(candidates, now)
        stored = sla_store.current_state(candidates, now)
        d = {row.incident_id: tuple(getattr(row, c) for c in columns) for row in db.execute(select(derived))}
        s = {row.incident_id: tuple(getattr(row, c) for c in columns) for row in db.execute(select(stored))}
        assert s == d, now
        # The indexed list-filter conditions select exactly the incidents whose summary says so.
        for state in ("overdue", "approaching", "breached"):
            filtered = set(db.scalars(select(Incident.id).where(Incident.id.in_(ids), sla_store.filter_condition(state, now))))
            assert filtered == {i for i, row in d.items() if row[columns.index("summary_state")] == state}, (state, now)
        escalated = set(db.scalars(select(Incident.id).where(Incident.id.in_(ids), sla_store.filter_condition("escalated", now))))
        assert escalated == {i for i, row in d.items() if row[columns.index("escalated")]}, now


def test_cycles_before_the_first_policy_are_stored_untracked_and_never_become_tracked(db, reporter, responder, connection):
    timeline = Timeline(db, reporter, responder)
    old = timeline.incident(at=T0 - timedelta(days=3650))
    timeline.policy(T0)  # a policy that starts after the cycle
    [cycle] = timeline.stored(old, T0 + timedelta(days=1))
    assert (cycle.tracked, cycle.version_id, cycle.resolution_state) == (False, None, "not_tracked")
    timeline.change(old, T0 + timedelta(hours=1), status="resolved")
    timeline.change(old, T0 + timedelta(hours=2), status="open")  # the new cycle starts under the policy
    first, second = timeline.stored(old, T0 + timedelta(hours=3))
    assert (first.tracked, second.tracked, second.started_at) == (False, True, T0 + timedelta(hours=2))
    assert sla_store.verify(connection)[1] == 0


def test_legacy_incidents_have_no_cycles_in_the_store_or_the_reference(login_as, responder, db, connection):
    """Found by the 50,000-incident benchmark: legacy incidents with later audited status changes.
    Neither side may give them cycles (the reference once emitted untracked rows for them)."""
    legacy = Incident(title="Legacy", description="Before audit", severity=Severity.HIGH)
    db.add(legacy)
    db.commit()
    client = login_as(responder)
    for status in ("investigating", "resolved", "open", "closed"):
        client.patch(f"/incidents/{legacy.id}", json={"status": status})
    assert db.scalar(select(func.count()).select_from(IncidentSlaCycle).where(IncidentSlaCycle.incident_id == legacy.id)) == 0
    only_legacy = select(Incident.id.label("id")).where(Incident.id == legacy.id)
    assert db.execute(select(sla_cycles(only_legacy, T0).subquery())).all() == []
    assert sla_store.verify(connection, only_legacy)[1] == 0


def test_api_writes_maintain_the_read_model_including_reopens_and_severity(login_as, responder, reporter, db, connection):
    reporter_client = login_as(reporter)
    created = reporter_client.post("/incidents", json={"title": "Store", "description": "Synthetic", "severity": "low"}).json()
    staff = login_as(responder)
    for change in ({"severity": "critical"}, {"status": "investigating"}, {"severity": "medium"}, {"status": "resolved"},
                   {"status": "open"}, {"severity": "high"}, {"status": "closed"}, {"status": "investigating"}):
        assert staff.patch(f"/incidents/{created['id']}", json=change).status_code == 200
    rows = db.scalars(select(IncidentSlaCycle).where(IncidentSlaCycle.incident_id == created["id"]).order_by(IncidentSlaCycle.cycle)).all()
    assert [r.cycle for r in rows] == [0, 1, 2]
    assert (rows[0].response_rank, rows[0].resolution_rank) == (4, 4)  # raised to critical before acknowledgment
    assert rows[0].resolved_at is not None and rows[1].resolved_at is not None and rows[2].resolved_at is None
    assert sla_store.verify(connection, select(Incident.id.label("id")).where(Incident.id == created["id"]))[1] == 0


# ---- Migration backfill ---------------------------------------------------------------------------------

def test_migration_backfill_matches_the_reference_for_reopens_severity_and_untracked_cycles(db, reporter, responder, connection):
    timeline = Timeline(db, reporter, responder, maintain=False)  # history only, as before migration 0007
    timeline.policy(T0 - timedelta(days=1))
    rng = random.Random(7)
    incidents = [_random_timeline(timeline, rng, T0 + timedelta(minutes=i * 5)) for i in range(40)]
    untracked = timeline.incident(at=T0 - timedelta(days=3650))
    timeline.change(untracked, T0 + timedelta(hours=1), status="resolved")
    timeline.change(untracked, T0 + timedelta(hours=2), status="investigating")
    ids = [i.id for i in incidents] + [untracked.id]
    assert db.scalar(select(func.count()).select_from(IncidentSlaCycle).where(IncidentSlaCycle.incident_id.in_(ids))) == 0
    db.execute(text(_migration().BACKFILL_SQL))
    candidates = select(Incident.id.label("id")).where(Incident.id.in_(ids))
    checked, mismatches, sample = sla_store.verify(connection, candidates)
    assert (mismatches, sample) == (0, []) and checked > len(ids)
    rows = {(r.incident_id, r.cycle): r for r in db.scalars(select(IncidentSlaCycle).where(IncidentSlaCycle.incident_id == untracked.id))}
    assert rows[(untracked.id, 0)].policy_version_id is None and rows[(untracked.id, 1)].policy_version_id is not None


def test_backfilled_rows_continue_correctly_through_the_write_path(db, reporter, responder, connection):
    history = Timeline(db, reporter, responder, maintain=False)
    history.policy(T0 - timedelta(days=1))
    incident = history.incident(severity="medium")
    history.change(incident, T0 + minutes(10), status="investigating")
    db.execute(text(_migration().BACKFILL_SQL))
    live = Timeline(db, reporter, responder)  # from now on the write path maintains it
    live.change(incident, T0 + minutes(20), severity="critical")
    live.change(incident, T0 + minutes(30), status="resolved")
    live.change(incident, T0 + minutes(40), status="open")
    assert sla_store.verify(connection, select(Incident.id.label("id")).where(Incident.id == incident.id))[1] == 0


# ---- Transactions and history guards --------------------------------------------------------------------

@pytest.fixture
def failing_client(responder):
    client = TestClient(app, base_url=BASE_URL, raise_server_exceptions=False)
    login = client.post("/auth/login", json={"email": responder.email, "password": TEST_PASSWORD})
    client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
    yield client
    client.close()


def test_a_failed_change_rolls_back_the_incident_its_event_and_its_sla_cycle(failing_client, monkeypatch, db):
    created = failing_client.post("/incidents", json={"title": "Rollback", "description": "Synthetic", "severity": "high"}).json()
    snapshot = lambda: db.execute(text("SELECT ctid::text, resolved_at, acknowledged_at FROM incident_sla_cycles WHERE incident_id = :i"), {"i": created["id"]}).all()  # noqa: E731
    before = snapshot()
    original = sla_store.record_change

    def then_fail(*args, **kwargs):
        original(*args, **kwargs)  # the cycle row is changed in this transaction...
        raise RuntimeError("synthetic failure after the SLA update")  # ...and then everything must roll back

    monkeypatch.setattr(sla_store, "record_change", then_fail)
    assert failing_client.patch(f"/incidents/{created['id']}", json={"status": "resolved"}).status_code == 500
    db.expire_all()
    assert snapshot() == before
    assert db.get(Incident, created["id"]).status.value == "open"
    assert db.scalar(text("SELECT count(*) FROM incident_events WHERE incident_id = :i"), {"i": created["id"]}) == 1


@pytest.mark.parametrize("statement, message", [
    ("DELETE FROM incident_sla_cycles WHERE incident_id = :i", "keeps every cycle"),
    ("TRUNCATE incident_sla_cycles", "keeps every cycle"),
    ("UPDATE incident_sla_cycles SET started_at = started_at + interval '1 second' WHERE incident_id = :i AND cycle = 1", "keeps its start"),
    ("UPDATE incident_sla_cycles SET resolution_due_at = resolution_due_at + interval '1 hour' WHERE incident_id = :i AND cycle = 1", "only become stricter"),
    ("UPDATE incident_sla_cycles SET resolution_rank = 1 WHERE incident_id = :i AND cycle = 1", "only become stricter"),
    ("UPDATE incident_sla_cycles SET resolved_at = NULL WHERE incident_id = :i AND cycle = 0", "completed SLA cycle is final"),
    ("UPDATE incident_sla_cycles SET policy_version_id = NULL, resolution_minutes = NULL WHERE incident_id = :i AND cycle = 1", "keeps its start"),
])
def test_history_guard_keeps_completed_cycles_and_targets_final(statement, message, tl, db):
    incident = tl.incident(severity="high")
    tl.change(incident, T0 + minutes(5), status="resolved")
    tl.change(incident, T0 + minutes(10), status="open")
    with pytest.raises(DBAPIError, match=message):
        with db.begin_nested():
            db.execute(text(statement), {"i": incident.id})


def test_history_guard_keeps_an_acknowledged_response_final(tl, db):
    incident = tl.incident(severity="high")
    tl.change(incident, T0 + minutes(5), status="investigating")
    with pytest.raises(DBAPIError, match="acknowledged response target is final"):
        with db.begin_nested():
            db.execute(text("UPDATE incident_sla_cycles SET acknowledged_at = NULL WHERE incident_id = :i"), {"i": incident.id})


def test_reopening_never_changes_an_earlier_cycle(tl, db):
    incident = tl.incident(severity="high")
    tl.change(incident, T0 + minutes(70), status="resolved")
    version = lambda: db.scalar(text("SELECT ctid::text FROM incident_sla_cycles WHERE incident_id = :i AND cycle = 0"), {"i": incident.id})  # noqa: E731
    before = version()
    tl.change(incident, T0 + minutes(80), status="investigating")
    tl.change(incident, T0 + minutes(90), severity="critical")
    tl.change(incident, T0 + minutes(100), status="closed")
    tl.change(incident, T0 + minutes(110), status="open")
    assert version() == before
    assert [r.resolution_state for r in tl.stored(incident, T0 + minutes(120))] == ["met", "met", "on_track"]


# ---- Concurrent incident updates (real transactions) ------------------------------------------------------

def _api_client_for(user_email):
    client = TestClient(app, base_url=BASE_URL)
    login = client.post("/auth/login", json={"email": user_email, "password": TEST_PASSWORD})
    client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
    return client


def test_concurrent_updates_are_serialised_and_the_read_model_stays_consistent():
    """Real requests on committed data: many staff updates to one incident at once. The incident row
    lock serialises them, so the stored cycles match the audit trail exactly afterwards."""
    from app.security import hash_password

    tag = secrets.token_hex(4)
    with engine.begin() as c:
        staff_id = c.execute(text("INSERT INTO users (email, password_hash, role) VALUES (:e, :h, 'responder') RETURNING id"),
                             {"e": f"sla.concurrent.{tag}@example.com", "h": hash_password(TEST_PASSWORD)}).scalar_one()
    saved = dict(app.dependency_overrides)
    app.dependency_overrides.clear()  # real sessions on real, separate transactions
    incident_id = None
    try:
        client = _api_client_for(f"sla.concurrent.{tag}@example.com")
        incident_id = client.post("/incidents", json={"title": "Concurrent", "description": "Synthetic", "severity": "low"}).json()["id"]
        changes = [{"status": s} for s in ("investigating", "resolved", "open", "resolved", "investigating", "closed", "open")] + \
                  [{"severity": s} for s in ("medium", "high", "critical", "low", "high")]
        barrier = threading.Barrier(len(changes))
        statuses = []

        def send(change):
            worker = _api_client_for(f"sla.concurrent.{tag}@example.com")
            barrier.wait()
            statuses.append(worker.patch(f"/incidents/{incident_id}", json=change).status_code)
            worker.close()

        threads = [threading.Thread(target=send, args=(change,)) for change in changes]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        assert set(statuses) == {200}
        with engine.connect() as conn:
            checked, mismatches, sample = sla_store.verify(conn, select(Incident.id.label("id")).where(Incident.id == incident_id))
        assert (mismatches, sample) == (0, []) and checked >= 1
        client.close()
    finally:
        app.dependency_overrides.update(saved)
        with engine.begin() as c:
            c.execute(text("SET LOCAL session_replication_role = replica"))
            if incident_id is not None:
                for table in ("incident_sla_cycles", "incident_events"):
                    c.execute(text(f"DELETE FROM {table} WHERE incident_id = :i"), {"i": incident_id})
                c.execute(text("DELETE FROM incidents WHERE id = :i"), {"i": incident_id})
            c.execute(text("DELETE FROM auth_sessions WHERE user_id = :u"), {"u": staff_id})
            c.execute(text("DELETE FROM users WHERE id = :u"), {"u": staff_id})


def test_cli_verify_reports_a_consistent_read_model():
    assert cli.main(["sla-verify"]) == 0


def test_escalated_filter_follows_the_current_cycle(tl, db, connection, login_as, responder):
    """Found by the end-to-end suite: the filter compared with the highest cycle of all incidents."""
    from app.escalation import run_escalations

    plain = tl.incident(severity="high")  # escalated in cycle 0 and still in it
    reopened = tl.incident(severity="high")  # escalated in cycle 0, then resolved and reopened
    other = tl.incident(severity="high")
    tl.change(other, T0 + minutes(1), status="resolved")
    tl.change(other, T0 + minutes(2), status="open")  # gives the table a cycle 1 elsewhere
    tl.change(other, T0 + minutes(3), status="investigating")
    run_escalations(connection, now=T0 + minutes(61))
    tl.change(reopened, T0 + minutes(70), status="resolved")
    tl.change(reopened, T0 + minutes(80), status="open")
    now = T0 + minutes(90)
    matching = set(db.scalars(select(Incident.id).where(sla_store.filter_condition("escalated", now))))
    assert plain.id in matching and reopened.id not in matching and other.id not in matching


def test_filters_include_a_target_due_exactly_now(tl, db):
    """Deadline boundary for the indexed filters: due exactly now counts as overdue."""
    response_due = tl.incident(severity="high")  # response due T0 + 60
    resolution_due = tl.incident(severity="high")
    tl.change(resolution_due, T0 + minutes(1), status="investigating")  # resolution due T0 + 480
    for incident, due in ((response_due, T0 + minutes(60)), (resolution_due, T0 + minutes(480))):
        only = Incident.id == incident.id
        assert db.scalar(select(Incident.id).where(only, sla_store.filter_condition("overdue", due))) == incident.id
        assert db.scalar(select(Incident.id).where(only, sla_store.filter_condition("approaching", due))) is None
        before = due - timedelta(microseconds=1)
        assert db.scalar(select(Incident.id).where(only, sla_store.filter_condition("overdue", before))) is None
        assert db.scalar(select(Incident.id).where(only, sla_store.filter_condition("approaching", before))) == incident.id


def test_dashboard_counts_only_the_first_cycle_of_reopened_incidents(tl, login_as, responder, db):
    from app.sla import get_now

    incident = tl.incident(severity="high")
    tl.change(incident, T0 + minutes(5), status="resolved")   # first cycle: both targets met
    tl.change(incident, T0 + minutes(10), status="open")      # second cycle, outstanding
    app.dependency_overrides[get_now] = lambda: T0 + minutes(20)
    try:
        summary = login_as(responder).get("/analytics/summary", params={"from": "2030-01-01", "to": "2030-01-31", "tz": "UTC"}).json()["sla"]
    finally:
        app.dependency_overrides.pop(get_now, None)
    assert summary["response"] == {"met": 1, "breached": 0, "outstanding": 0, "not_tracked": 0}
    assert summary["resolution"] == {"met": 1, "breached": 0, "outstanding": 0, "not_tracked": 0}
