"""Public demo mode and the daily demo reset (Milestone 6 Phase C). Synthetic data only."""
import os
import secrets
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from app import cli, demo, demo_reset
from app.config import settings
from app.database import DATABASE_URL, engine as main_engine, get_db
from app.main import app
from app.models import User, UserRole
from app.security import hash_password, verify_password
from conftest import BASE_URL, TEST_PASSWORD

REPORTER, RESPONDER = demo.DEMO_ACCOUNTS


@pytest.fixture
def demo_on(monkeypatch):
    monkeypatch.setattr(demo, "enabled", lambda: True)


@pytest.fixture
def demo_users(db):
    users = {}
    for account in demo.DEMO_ACCOUNTS:
        user = User(email=account.email, password_hash=hash_password(account.password), role=account.role)
        db.add(user)
        db.commit()
        users[account.role] = user
    return users


def _login(client, email, password):
    return client.post("/auth/login", json={"email": email, "password": password})


# ---- Public information ---------------------------------------------------------------------------

def test_demo_information_is_off_by_default(client):
    assert demo.enabled() is False and settings.demo_mode is False
    assert client.get("/demo").json() == {"enabled": False, "reset_time_utc": None, "accounts": []}


def test_demo_information_lists_the_published_accounts(client, demo_on):
    body = client.get("/demo").json()
    assert body["enabled"] is True and body["reset_time_utc"] == "04:00"
    assert body["accounts"] == [
        {"role": "reporter", "email": "demo.reporter@example.com", "password": "demo-reporter-2026"},
        {"role": "responder", "email": "demo.responder@example.com", "password": "demo-responder-2026"},
    ]
    assert all(a["email"].endswith("@example.com") for a in body["accounts"])


# ---- Account protection -------------------------------------------------------------------------

def test_demo_accounts_cannot_be_locked_out_by_email(client, demo_on, demo_users):
    for _ in range(settings.login_max_failures_per_email * 3):
        assert _login(client, REPORTER.email, "wrong-password-123").status_code == 401
    assert _login(client, REPORTER.email, REPORTER.password).status_code == 200


def test_the_per_ip_limit_still_applies_to_demo_accounts(client, demo_on, demo_users):
    for _ in range(settings.login_max_failures_per_ip):
        assert _login(client, RESPONDER.email, "wrong-password-123").status_code == 401
    assert _login(client, RESPONDER.email, RESPONDER.password).status_code == 429  # this address only


def test_other_accounts_are_still_locked_by_email(client, demo_on, reporter):
    for _ in range(settings.login_max_failures_per_email):
        _login(client, reporter.email, "wrong-password-123")
    assert _login(client, reporter.email, TEST_PASSWORD).status_code == 429


def test_without_demo_mode_the_demo_addresses_are_ordinary_accounts(client, demo_users):
    for _ in range(settings.login_max_failures_per_email):
        _login(client, REPORTER.email, "wrong-password-123")
    assert _login(client, REPORTER.email, REPORTER.password).status_code == 429


def test_demo_passwords_cannot_be_changed(client, demo_on, demo_users, login_as, reporter):
    login = _login(client, REPORTER.email, REPORTER.password)
    client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
    response = client.put("/auth/password", json={"current_password": REPORTER.password, "new_password": "another-long-passphrase"})
    assert (response.status_code, response.json()["detail"]) == (403, "The password of a public demo account cannot be changed.")
    assert _login(TestClient(app, base_url=BASE_URL), REPORTER.email, REPORTER.password).status_code == 200
    # Other accounts can still change theirs.
    other = login_as(reporter)
    assert other.put("/auth/password", json={"current_password": TEST_PASSWORD, "new_password": "another-long-passphrase"}).status_code == 204


@pytest.mark.parametrize("change", [{"role": "admin"}, {"role": "reporter"}, {"is_active": False}])
def test_demo_accounts_cannot_be_changed_by_an_administrator(change, demo_on, demo_users, login_as, admin, reporter, db):
    client = login_as(admin)
    response = client.patch(f"/users/{demo_users[UserRole.RESPONDER].id}", json=change)
    assert (response.status_code, response.json()["detail"]) == (403, "Public demo accounts cannot be changed.")
    db.expire_all()
    stored = db.get(User, demo_users[UserRole.RESPONDER].id)
    assert (stored.role, stored.is_active) == (UserRole.RESPONDER, True)
    assert client.patch(f"/users/{reporter.id}", json={"is_active": False}).status_code == 200  # others unaffected


# ---- Reset guards ---------------------------------------------------------------------------------

def test_reset_refuses_without_demo_mode(monkeypatch):
    monkeypatch.setenv("DEMO_DATABASE", "anything_demo")
    with main_engine.connect() as conn, pytest.raises(demo_reset.DemoResetRefused, match="DEMO_MODE"):
        demo_reset.check_target(conn)


@pytest.mark.parametrize("configured, message", [
    (None, "DEMO_DATABASE must name"),
    ("other_demo", "but DEMO_DATABASE is"),
    ("__current__", "must end with '_demo'"),  # the test database itself: not a demo name
])
def test_reset_refuses_any_other_database(configured, message, demo_on, monkeypatch, db):
    current = db.scalar(text("SELECT current_database()"))
    if configured is None:
        monkeypatch.delenv("DEMO_DATABASE", raising=False)
    else:
        monkeypatch.setenv("DEMO_DATABASE", current if configured == "__current__" else configured)
    with pytest.raises(demo_reset.DemoResetRefused, match=message):
        demo_reset.reset(main_engine)


def test_the_cli_reports_a_refused_reset(demo_on, monkeypatch, capsys):
    monkeypatch.setenv("DEMO_DATABASE", "other_demo")
    assert cli.demo_reset(daily=False) == 2
    assert "demo-reset refused" in capsys.readouterr().err


def test_next_reset_is_at_four_utc():
    assert demo_reset.seconds_until_next_reset(datetime(2026, 11, 16, 3, 0, tzinfo=timezone.utc)) == 3600
    assert demo_reset.seconds_until_next_reset(datetime(2026, 11, 16, 4, 0, tzinfo=timezone.utc)) == 24 * 3600
    assert demo_reset.seconds_until_next_reset(datetime(2026, 11, 16, 23, 30, tzinfo=timezone.utc)) == 4.5 * 3600


# ---- The reset itself, on a real, separate demo database ---------------------------------------

@pytest.fixture(scope="module")
def demo_database():
    """A temporary database named *_demo, migrated to head, with a private admin; dropped afterwards."""
    name = f"imp_test_{secrets.token_hex(4)}_demo"
    admin_engine = create_engine(DATABASE_URL, isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as conn:
        conn.exec_driver_sql(f"CREATE DATABASE {name}")
    url = make_url(DATABASE_URL).set(database=name)
    env = {**os.environ, "DATABASE_URL": url.render_as_string(hide_password=False)}
    migrated = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], env=env, capture_output=True, text=True, timeout=180)
    assert migrated.returncode == 0, migrated.stderr[-2000:]
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO users (email, password_hash, role) VALUES ('private.admin@example.com', :h, 'admin')"),
                     {"h": hash_password("private-admin-passphrase")})
    try:
        yield name, engine
    finally:
        engine.dispose()
        with admin_engine.connect() as conn:
            conn.exec_driver_sql(f"DROP DATABASE {name} WITH (FORCE)")
        admin_engine.dispose()


@pytest.fixture
def demo_target(demo_database, demo_on, monkeypatch):
    name, engine = demo_database
    monkeypatch.setenv("DEMO_DATABASE", name)
    return engine


def _fingerprint(engine):
    with engine.connect() as conn:
        return {
            "incidents": conn.execute(text(
                "SELECT i.id, i.title, i.severity, i.status, r.email, a.email FROM incidents i JOIN users r ON r.id = i.reporter_id "
                "LEFT JOIN users a ON a.id = i.assignee_id ORDER BY i.id")).all(),
            "events": conn.execute(text("SELECT incident_id, action, changes::text FROM incident_events ORDER BY id")).all(),
            "comments": conn.execute(text("SELECT incident_id, visibility, body FROM incident_comments ORDER BY id")).all(),
            "triage": conn.execute(text("SELECT r.incident_id, s.severity, s.rationale FROM triage_suggestions s "
                                        "JOIN triage_requests r ON r.id = s.request_id ORDER BY s.id")).all(),
            "users": conn.execute(text("SELECT email, role, is_active FROM users ORDER BY email")).all(),
        }


def test_reset_replaces_everything_and_is_repeatable(demo_target):
    engine = demo_target
    with engine.begin() as conn:  # leftovers from visitors
        conn.execute(text("INSERT INTO users (email, password_hash, role) VALUES ('visitor.made@example.com', 'x', 'responder')"))
        conn.execute(text("INSERT INTO incidents (title, description, severity) VALUES ('Visitor incident', 'Synthetic', 'low')"))
    first = demo_reset.reset(engine)
    assert (first.incidents, first.comments, first.triage_suggestions) == (15, 6, 3)
    assert first.cycles_checked > 15  # one per incident, plus the reopened one
    before = _fingerprint(engine)
    second = demo_reset.reset(engine)
    assert second.incidents == 15
    assert _fingerprint(engine) == before  # deterministic: same rows, same ids

    emails = {row.email for row in before["users"]}
    assert emails == {"private.admin@example.com", REPORTER.email, RESPONDER.email, *demo_reset.OTHER_REPORTERS}
    assert "visitor.made@example.com" not in emails
    assert all(title != "Visitor incident" for _, title, *_ in before["incidents"])
    with engine.connect() as conn:
        for account in demo.DEMO_ACCOUNTS:
            stored = conn.execute(text("SELECT password_hash, role FROM users WHERE email = :e"), {"e": account.email}).one()
            assert verify_password(stored.password_hash, account.password) and stored.role == account.role.value
        assert conn.execute(text("SELECT count(*) FROM auth_sessions")).scalar_one() == 0


def test_reset_leaves_protections_active_and_the_data_consistent(demo_target):
    engine = demo_target
    demo_reset.reset(engine)
    with engine.connect() as conn:
        assert conn.execute(text("SHOW session_replication_role")).scalar_one() == "origin"
        with pytest.raises(DBAPIError, match="append-only"):
            conn.execute(text("UPDATE incident_events SET action = action"))
        conn.rollback()
        from app.sla_store import verify
        checked, mismatches, _ = verify(conn)
        assert mismatches == 0 and checked > 0
        states = dict(conn.execute(text(
            "SELECT i.title, c.response_due_at < now() AND c.acknowledged_at IS NULL FROM incidents i "
            "JOIN incident_sla_cycles c ON c.incident_id = i.id AND c.cycle = 0")).all())
        assert states["Suspicious VPN sign-in from a new country"] is True  # response overdue
        assert states["Phishing email reported by the finance team"] is False
        assert conn.execute(text("SELECT count(*) FROM incident_sla_cycles WHERE cycle = 1")).scalar_one() == 1  # the reopened badge


def test_demo_roles_see_what_their_role_allows(demo_target):
    engine = demo_target
    demo_reset.reset(engine)
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def demo_db():
        with sessions() as session:
            yield session
    app.dependency_overrides[get_db] = demo_db
    reporter_titles = {title for key, title, _d, _s, who, *_ in demo_reset.SCENARIOS if who == "reporter"}
    try:
        reporter = TestClient(app, base_url=BASE_URL)
        assert _login(reporter, REPORTER.email, REPORTER.password).status_code == 200
        mine = {item["title"] for item in reporter.get("/incidents?limit=100").json()["items"]}
        assert mine == reporter_titles
        responder = TestClient(app, base_url=BASE_URL)
        assert _login(responder, RESPONDER.email, RESPONDER.password).status_code == 200
        everything = responder.get("/incidents?limit=100").json()
        assert everything["total"] == 15
        assert responder.get("/incidents?sla=overdue").json()["total"] >= 2
        suggestions = [s for i in everything["items"] for s in responder.get(f"/incidents/{i['id']}/triage-suggestions").json()["items"]]
        assert len(suggestions) == 3 and all(s["provider"] == "fake" and not s["stale"] for s in suggestions)
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_a_fresh_demo_database_needs_its_initial_seed(demo_target):
    engine = demo_target
    with engine.begin() as conn:
        conn.execute(text("SET LOCAL session_replication_role = replica"))
        conn.execute(text("DELETE FROM auth_sessions"))
        conn.execute(text("TRUNCATE incidents, incident_events, incident_comments, comment_redactions, incident_sla_cycles, "
                          "incident_escalations, triage_requests, triage_suggestions, triage_decisions, incident_exports, "
                          "rate_limit_events RESTART IDENTITY CASCADE"))
        conn.execute(text("DELETE FROM users WHERE role <> 'admin'"))
    assert demo_reset.needs_initial_seed(engine) is True
    demo_reset.reset(engine)
    assert demo_reset.needs_initial_seed(engine) is False


def test_an_inconsistent_seed_rolls_back_and_keeps_the_previous_demo(demo_target, monkeypatch):
    engine = demo_target
    demo_reset.reset(engine)
    before = _fingerprint(engine)
    with engine.begin() as conn:  # a visitor's incident, which a successful reset would remove
        conn.execute(text("INSERT INTO incidents (title, description, severity) VALUES ('Still here', 'Synthetic', 'low')"))
    from app import sla_store
    monkeypatch.setattr(sla_store, "verify", lambda conn: (16, 1, [7]))
    with pytest.raises(RuntimeError, match="inconsistent SLA cycles"):
        demo_reset.reset(engine)
    after = _fingerprint(engine)
    assert after["users"] == before["users"] and after["comments"] == before["comments"]
    assert after["incidents"] == before["incidents"]
    with engine.connect() as conn:  # nothing was emptied: the visitor's incident is still there
        assert conn.execute(text("SELECT count(*) FROM incidents WHERE title = 'Still here'")).scalar_one() == 1


# ---- Reset now, concurrency and failure (Phase C final verification) ------------------------------

@contextmanager
def _app_on(engine):
    """The API bound to the demo database (real sessions)."""
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def demo_db():
        with sessions() as session:
            yield session
    app.dependency_overrides[get_db] = demo_db
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_db, None)


def _wait_for_lock_waiter(engine, timeout=10.0):
    deadline = time.monotonic() + timeout
    with engine.connect() as probe:
        while time.monotonic() < deadline:
            if probe.execute(text("SELECT count(*) FROM pg_locks WHERE NOT granted")).scalar_one():
                return
            probe.rollback()
            time.sleep(0.05)
    raise AssertionError("the reset never waited for a lock")


def test_the_reset_now_command_works_only_on_the_demo_database(demo_target, monkeypatch, capsys):
    import app.database
    monkeypatch.setattr(app.database, "engine", demo_target)
    assert cli.demo_reset(daily=False) == 0
    assert "Demo reset: 15 incidents, 6 comments, 3 triage suggestions" in capsys.readouterr().out
    monkeypatch.setattr(app.database, "engine", main_engine)  # the test database, with DEMO_DATABASE still the demo's
    assert cli.demo_reset(daily=False) == 2


def test_a_failed_reset_now_exits_with_an_error_and_keeps_the_demo(demo_target, monkeypatch):
    import app.database
    monkeypatch.setattr(app.database, "engine", demo_target)
    demo_reset.reset(demo_target)
    before = _fingerprint(demo_target)

    def broken_seed(db, now):
        raise RuntimeError("synthetic seed failure")
    monkeypatch.setattr(demo_reset, "_seed", broken_seed)
    assert cli.demo_reset(daily=False) == 1
    assert _fingerprint(demo_target) == before


def test_reset_waits_for_an_in_flight_write_and_then_replaces_it(demo_target):
    engine = demo_target
    demo_reset.reset(engine)
    result = {}
    with engine.connect() as writer:
        writer.execute(text("INSERT INTO incidents (title, description, severity) VALUES ('In flight', 'Synthetic', 'low')"))
        worker = threading.Thread(target=lambda: result.update(run=demo_reset.reset(engine)))
        worker.start()
        _wait_for_lock_waiter(engine)
        assert worker.is_alive()  # waiting for the write, not failing
        writer.commit()
    worker.join(60)
    assert result["run"].incidents == 15
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM incidents WHERE title = 'In flight'")).scalar_one() == 0


def test_a_stuck_transaction_makes_the_reset_give_up_and_the_demo_stays_usable(demo_target):
    engine = demo_target
    demo_reset.reset(engine)
    before = _fingerprint(engine)
    with engine.connect() as stuck:
        stuck.execute(text("SELECT count(*) FROM incidents"))  # holds a lock the reset needs
        outcome = {}

        def attempt():
            try:
                demo_reset.reset(engine, lock_timeout_seconds=1)
                outcome["error"] = None
            except DBAPIError as error:
                outcome["error"] = error
        thread = threading.Thread(target=attempt, daemon=True)
        thread.start()
        thread.join(15)
        gave_up = not thread.is_alive()
        if not gave_up:  # still queued behind the stuck transaction: free it so the test can finish
            stuck.execute(text("SELECT pg_cancel_backend(pid) FROM pg_stat_activity WHERE query LIKE 'TRUNCATE%' AND pid <> pg_backend_pid()"))
            thread.join(15)
        stuck.rollback()
    assert gave_up, "the reset kept waiting for the stuck transaction"
    assert "lock timeout" in str(outcome["error"])
    assert _fingerprint(engine) == before  # rolled back: yesterday's demo is intact
    with _app_on(engine):
        client = TestClient(app, base_url=BASE_URL)
        assert _login(client, RESPONDER.email, RESPONDER.password).status_code == 200
        assert client.get("/incidents").json()["total"] == 15


def test_active_sessions_end_at_the_reset_and_signing_in_again_works(demo_target):
    engine = demo_target
    demo_reset.reset(engine)
    with _app_on(engine):
        client = TestClient(app, base_url=BASE_URL)
        login = _login(client, REPORTER.email, REPORTER.password)
        client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
        assert client.get("/auth/me").status_code == 200
        demo_reset.reset(engine)
        assert client.get("/auth/me").status_code == 401
        again = _login(client, REPORTER.email, REPORTER.password)
        client.headers["X-CSRF-Token"] = again.json()["csrf_token"]
        created = client.post("/incidents", json={"title": "After the reset", "description": "Synthetic", "severity": "low"})
        assert created.status_code == 201, created.text


def test_the_reset_waits_for_a_worker_run_in_progress(demo_target):
    from app.sla import SLA_WORKER_LOCK

    engine = demo_target
    demo_reset.reset(engine)
    result = {}
    with engine.connect() as worker:  # a worker run holds its session-level lock
        worker.execute(select(func.pg_advisory_lock(SLA_WORKER_LOCK)))
        worker.commit()
        thread = threading.Thread(target=lambda: result.update(run=demo_reset.reset(engine)))
        thread.start()
        _wait_for_lock_waiter(engine)
        assert thread.is_alive()
        worker.execute(select(func.pg_advisory_unlock(SLA_WORKER_LOCK)))
        worker.commit()
    thread.join(60)
    assert result["run"].incidents == 15


def test_a_worker_run_during_a_reset_is_skipped(demo_target, monkeypatch):
    from app.escalation import run_escalations

    engine = demo_target
    runs = []
    seed = demo_reset._seed

    def seed_while_the_worker_wakes(db, now):
        runs.append(run_escalations(engine))  # another connection, mid-reset
        return seed(db, now)
    monkeypatch.setattr(demo_reset, "_seed", seed_while_the_worker_wakes)
    demo_reset.reset(engine)
    assert [run.ran for run in runs] == [False]
    assert run_escalations(engine).ran is True  # and runs normally afterwards


def test_worker_runs_during_resets_stay_consistent(demo_target):
    from app.escalation import run_escalations
    from app.sla_store import verify

    engine = demo_target
    demo_reset.reset(engine)
    errors, stop = [], threading.Event()

    def worker():
        while not stop.is_set():
            try:
                run_escalations(engine)
            except Exception as error:  # noqa: BLE001
                errors.append(repr(error))
    thread = threading.Thread(target=worker)
    thread.start()
    try:
        for _ in range(3):
            demo_reset.reset(engine)
    finally:
        stop.set()
        thread.join(60)
    assert errors == []
    run_escalations(engine)
    with engine.connect() as conn:
        assert verify(conn)[1] == 0
        assert conn.execute(text("SELECT count(*) FROM incident_escalations")).scalar_one() >= 2  # overdue seeds


def test_a_reset_that_deadlocks_with_a_request_is_retried(demo_target, caplog):
    engine = demo_target
    demo_reset.reset(engine)
    result = {}
    with engine.connect() as writer:
        writer.execute(text("SET deadlock_timeout = '10s'"))  # the reset detects the deadlock first and is the victim
        writer.execute(text("SELECT count(*) FROM sla_policy_versions"))  # late in the reset's lock order
        thread = threading.Thread(target=lambda: result.update(run=demo_reset.reset(engine)))
        thread.start()
        _wait_for_lock_waiter(engine)  # the reset holds the earlier tables and waits for this one
        writer.execute(text("SELECT count(*) FROM auth_sessions"))  # needs a table the reset holds: deadlock
        writer.commit()
    thread.join(60)
    assert result["run"].incidents == 15
    assert "deadlocked with a concurrent request (attempt 1 of 3)" in caplog.text


def test_demo_accounts_cannot_be_locked_out_from_many_addresses(demo_on, demo_users, reporter):
    attempts = 0
    for octet in range(10):  # 10 attacking addresses, each below its own per-IP limit
        attacker = TestClient(app, base_url=BASE_URL, client=(f"203.0.113.{octet}", 40000))
        for _ in range(5):
            assert _login(attacker, REPORTER.email, "wrong-password-123").status_code == 401
            attempts += 1
    assert attempts == 50
    visitor = TestClient(app, base_url=BASE_URL, client=("198.51.100.20", 40000))
    assert _login(visitor, REPORTER.email, REPORTER.password).status_code == 200
    # An ordinary account is still protected by its per-email limit, whatever the addresses.
    for octet in range(settings.login_max_failures_per_email):
        _login(TestClient(app, base_url=BASE_URL, client=(f"192.0.2.{octet}", 40000)), reporter.email, "wrong-password-123")
    assert _login(visitor, reporter.email, TEST_PASSWORD).status_code == 429


def test_the_demo_never_builds_a_paid_ai_provider(demo_on, monkeypatch):
    import anthropic
    from app.config import TRIAGE_DEFAULT_MODEL, TriageSettings, production_problems
    from app.triage_providers import build_provider

    constructed = []
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kwargs: constructed.append(kwargs))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-synthetic-canary")
    provider, reason = build_provider(TriageSettings(provider="anthropic", model=TRIAGE_DEFAULT_MODEL))
    assert (provider, reason) == (None, "AI triage with a paid provider is disabled in the public demo.")
    assert constructed == []
    # Production refuses to start with the paid provider or any key at all (Phase A).
    problems = production_problems({"DATABASE_URL": "postgresql+psycopg://app:" + "x" * 32 + "@db/demo_demo", "TRIAGE_PROVIDER": "anthropic",
                                    "ANTHROPIC_API_KEY": "sk-ant-synthetic-canary", "FORWARDED_ALLOW_IPS": "172.30.0.10"})
    assert any("TRIAGE_PROVIDER=anthropic" in p for p in problems) and any("ANTHROPIC_API_KEY" in p for p in problems)
