"""Database-backed rate limiting, public-demo write limits and housekeeping (Milestone 6 Phase B)."""
import os
import secrets
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

from app import cli, rate_limit
from app.config import settings
from app.database import engine
from app.main import app
from app.maintenance import cleanup
from app.models import AuthSession, RateLimitEvent
from app.rate_limit import Limit, WriteLimits, get_write_limits
from conftest import BASE_URL, TEST_PASSWORD

WINDOW = settings.login_failure_window_seconds


def _login(client, email, password="wrong-password-123", **headers):
    return client.post("/auth/login", json={"email": email, "password": password}, headers=headers)


# ---- The limiter itself -----------------------------------------------------------------------

def test_sliding_window_counts_only_recent_events(db):
    limit = Limit("login_email", "window@example.com", 3, 60)
    assert rate_limit.retry_after(db, limit) is None
    now = db.scalar(select(func.now()))
    db.add(RateLimitEvent(bucket=limit.bucket, key_hash=limit.hashed, created_at=now - timedelta(seconds=61)))  # outside
    for _ in range(2):
        rate_limit.record(db, limit)
    assert rate_limit.retry_after(db, limit) is None  # 2 inside the window
    rate_limit.record(db, limit)
    assert rate_limit.retry_after(db, limit) == 60  # oldest inside event is "now"
    other = Limit("login_email", "other@example.com", 3, 60)
    assert rate_limit.retry_after(db, other) is None
    rate_limit.reset(db, limit)
    assert rate_limit.retry_after(db, limit) is None
    assert rate_limit.retry_after(db, Limit("login_email", "x", 0, 60)) is None  # 0 = off


def test_keys_are_stored_only_as_hashes(client, db):
    _login(client, "Private.Person@example.com")
    stored = db.scalars(select(RateLimitEvent.key_hash)).all()
    assert len(stored) == 2 and all(len(h) == 64 for h in stored)
    dump = " ".join(db.execute(text("SELECT bucket || key_hash FROM rate_limit_events")).scalars())
    assert "private.person" not in dump.lower() and "testclient" not in dump


def test_prune_removes_only_expired_events(db):
    now = db.scalar(select(func.now()))
    old = Limit("write_comment", "203.0.113.5", 10, 3600)
    db.add(RateLimitEvent(bucket=old.bucket, key_hash=old.hashed, created_at=now - timedelta(hours=2)))
    rate_limit.record(db, old)
    assert rate_limit.prune(db, 3600) == 1
    assert db.scalar(select(func.count()).select_from(RateLimitEvent)) == 1


# ---- Sign-in limits ----------------------------------------------------------------------------

def test_per_ip_limit_applies_across_emails_and_ignores_forwarded_headers(client):
    # The app trusts only the connection's address; X-Forwarded-For is resolved by uvicorn, and only
    # for the trusted proxy, so a client cannot pick a fresh address per attempt.
    for attempt in range(settings.login_max_failures_per_ip):
        assert _login(client, f"spray{attempt}@example.com", **{"X-Forwarded-For": f"198.51.100.{attempt}"}).status_code == 401
    limited = _login(client, "fresh@example.com", **{"X-Forwarded-For": "192.0.2.77"})
    assert limited.status_code == 429
    assert 1 <= int(limited.headers["Retry-After"]) <= WINDOW


def test_failures_are_kept_even_though_the_request_fails(client, db):
    assert _login(client, "kept@example.com").status_code == 401
    email_events = db.scalar(select(func.count()).select_from(RateLimitEvent).where(
        RateLimitEvent.key_hash == rate_limit.key_hash(rate_limit.LOGIN_EMAIL, "kept@example.com")))
    assert email_events == 1


def test_a_successful_sign_in_clears_only_that_emails_failures(client, reporter, db):
    for _ in range(3):
        _login(client, reporter.email)
    assert _login(client, reporter.email, TEST_PASSWORD).status_code == 200
    email_key = rate_limit.key_hash(rate_limit.LOGIN_EMAIL, reporter.email)
    ip_key = rate_limit.key_hash(rate_limit.LOGIN_IP, "testclient")
    counts = dict(db.execute(select(RateLimitEvent.key_hash, func.count()).group_by(RateLimitEvent.key_hash)).all())
    assert counts.get(email_key) is None and counts.get(ip_key) == 3


# ---- Shared across processes, after restarts and under concurrency (real transactions) ------------

@pytest.fixture
def committed_login_key():
    """A unique email for committed sign-in attempts; its events and the test client's IP events
    are removed afterwards (rate_limit_events is not append-only)."""
    email = f"shared.{secrets.token_hex(4)}@example.com"
    yield email
    with engine.begin() as conn:
        for bucket, key in ((rate_limit.LOGIN_EMAIL, email), (rate_limit.LOGIN_IP, "testclient")):
            conn.execute(text("DELETE FROM rate_limit_events WHERE bucket = :b AND key_hash = :k"),
                         {"b": bucket, "k": rate_limit.key_hash(bucket, key)})


ATTEMPTS = """
import sys
from fastapi.testclient import TestClient
from app.main import app
client = TestClient(app, base_url="https://testserver")
codes = [client.post("/auth/login", json={"email": sys.argv[1], "password": "wrong-password-123"}).status_code
         for _ in range(int(sys.argv[2]))]
print(" ".join(map(str, codes)))
"""


def _process(email: str, attempts: int) -> list[int]:
    """A separate Python process (its own interpreter, engine and memory), like a second worker or a
    restarted server."""
    result = subprocess.run([sys.executable, "-c", ATTEMPTS, email, str(attempts)], capture_output=True, text=True,
                            timeout=120, env=os.environ.copy())
    assert result.returncode == 0, result.stderr[-2000:]
    return [int(code) for code in result.stdout.split()]


def test_limits_are_shared_across_processes_and_survive_restarts(committed_login_key):
    limit = settings.login_max_failures_per_email  # 5
    first = _process(committed_login_key, 3)
    second = _process(committed_login_key, 3)  # another process: sees the first one's failures
    assert first == [401, 401, 401]
    assert second == [401] * (limit - 3) + [429] * (3 - (limit - 3))
    restarted = _process(committed_login_key, 1)  # a fresh process, after both have exited
    assert restarted == [429]


def test_concurrent_attempts_cannot_exceed_the_limit(committed_login_key):
    limit = settings.login_max_failures_per_email
    codes, start = [], threading.Barrier(10)

    def attempt():
        client = TestClient(app, base_url=BASE_URL)
        start.wait(10)
        codes.append(_login(client, committed_login_key).status_code)

    # Real sessions: these requests commit to the database, outside the test transaction.
    app.dependency_overrides.clear()
    threads = [threading.Thread(target=attempt) for _ in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(60)
    assert sorted(codes) == [401] * limit + [429] * (10 - limit)
    with engine.connect() as conn:
        recorded = conn.execute(text("SELECT count(*) FROM rate_limit_events WHERE key_hash = :k"),
                                {"k": rate_limit.key_hash(rate_limit.LOGIN_EMAIL, committed_login_key)}).scalar_one()
    assert recorded == limit


# ---- Public-demo write limits ------------------------------------------------------------------

@pytest.fixture
def write_limits():
    def install(incidents=0, comments=0, triage=0):
        app.dependency_overrides[get_write_limits] = lambda: WriteLimits(incidents, comments, triage)
    return install


def test_write_limits_are_off_by_default(login_as, responder):
    assert get_write_limits() == WriteLimits(0, 0, 0)
    staff = login_as(responder)
    for i in range(5):
        assert staff.post("/incidents", json={"title": f"Default {i}", "description": "Synthetic", "severity": "low"}).status_code == 201


def test_incident_writes_are_limited_per_ip_across_accounts(write_limits, login_as, reporter, responder):
    write_limits(incidents=2)
    first, second = login_as(reporter), login_as(responder)  # same client address
    payload = {"title": "Demo", "description": "Synthetic", "severity": "low"}
    assert first.post("/incidents", json=payload).status_code == 201
    assert second.post("/incidents", json=payload).status_code == 201
    limited = first.post("/incidents", json=payload)
    assert limited.status_code == 429
    assert limited.json()["detail"] == "Too many new incidents from your network. Try again later."
    assert 1 <= int(limited.headers["Retry-After"]) <= 3600


def test_rejected_writes_and_anonymous_requests_are_not_counted(write_limits, login_as, reporter, client, db):
    write_limits(incidents=1)
    staff = login_as(reporter)
    assert client.post("/incidents", json={"title": "x", "description": "y", "severity": "low"}).status_code == 401
    assert staff.post("/incidents", json={"title": "", "description": "y", "severity": "low"}).status_code == 422
    assert staff.post("/incidents", json={"title": "Counted", "description": "Synthetic", "severity": "low"}).status_code == 201
    assert db.scalar(select(func.count()).select_from(RateLimitEvent).where(RateLimitEvent.bucket == rate_limit.WRITE_INCIDENT)) == 1


def test_comment_writes_are_limited(write_limits, login_as, reporter):
    write_limits(comments=2)
    client = login_as(reporter)
    incident_id = client.post("/incidents", json={"title": "Comments", "description": "Synthetic", "severity": "low"}).json()["id"]
    codes = [client.post(f"/incidents/{incident_id}/comments", json={"body": f"Note {i}", "visibility": "public"}).status_code
             for i in range(3)]
    assert codes == [201, 201, 429]


def test_triage_requests_are_limited(write_limits, login_as, responder):
    from app.config import TriageSettings
    from app.triage_api import get_triage_provider, get_triage_settings
    from app.triage_providers import FakeProvider

    write_limits(triage=1)
    app.dependency_overrides[get_triage_settings] = lambda: TriageSettings(provider="fake", incident_cooldown_seconds=0)
    app.dependency_overrides[get_triage_provider] = lambda: (FakeProvider(), "enabled")
    staff = login_as(responder)
    incident_id = staff.post("/incidents", json={"title": "Triage", "description": "Synthetic", "severity": "low"}).json()["id"]
    assert staff.post(f"/incidents/{incident_id}/triage-suggestions").status_code == 201
    assert staff.post(f"/incidents/{incident_id}/triage-suggestions").json()["detail"] == "Too many triage requests from your network. Try again later."


# ---- Housekeeping --------------------------------------------------------------------------

def test_cleanup_removes_expired_and_idle_sessions_and_old_events(db, reporter):
    now = db.scalar(select(func.now()))
    keep = AuthSession(token_hash="a" * 64, csrf_token="c", user_id=reporter.id, expires_at=now + timedelta(hours=1), last_seen_at=now)
    expired = AuthSession(token_hash="b" * 64, csrf_token="c", user_id=reporter.id, expires_at=now - timedelta(seconds=1), last_seen_at=now)
    idle = AuthSession(token_hash="d" * 64, csrf_token="c", user_id=reporter.id, expires_at=now + timedelta(hours=1),
                       last_seen_at=now - timedelta(minutes=settings.session_idle_minutes + 1))
    old_event = RateLimitEvent(bucket="login_ip", key_hash="e" * 64, created_at=now - timedelta(hours=2))
    db.add_all([keep, expired, idle, old_event])
    db.commit()
    result = cleanup(db)
    assert (result.sessions, result.rate_limit_events) == (2, 1)
    assert db.scalars(select(AuthSession.token_hash)).all() == ["a" * 64]


def test_cleanup_command(monkeypatch, capsys):
    from app import maintenance
    monkeypatch.setattr(maintenance, "cleanup", lambda db: maintenance.CleanupResult(3, 4))
    assert cli.cleanup() == 0
    assert "removed 3 expired sessions and 4 old rate-limit events" in capsys.readouterr().out


def test_the_worker_loop_runs_the_hourly_cleanup(monkeypatch):
    from app import escalation, health, maintenance

    calls = []

    class Stop(Exception):
        pass

    def stop_after_first_iteration(seconds):
        raise Stop
    monkeypatch.setattr(health, "readiness", lambda bind=None: (True, "ok"))
    monkeypatch.setattr(escalation, "run_escalations", lambda *a, **k: escalation.EscalationRun(ran=True))
    monkeypatch.setattr(cli, "cleanup", lambda: calls.append("cleanup") or 0)
    monkeypatch.setattr(cli.time, "sleep", stop_after_first_iteration)
    assert maintenance.CLEANUP_INTERVAL_SECONDS == 3600
    with pytest.raises(Stop):
        cli.sla_escalate(loop=True, interval=60)
    assert calls == ["cleanup"]


def test_cleanup_never_lifts_an_active_lockout(client, db):
    email = "locked.out@example.com"
    for _ in range(settings.login_max_failures_per_email):
        assert _login(client, email).status_code == 401
    assert _login(client, email).status_code == 429
    cleanup(db)  # hourly housekeeping runs in the middle of the lockout
    assert _login(client, email).status_code == 429


def test_cleanup_keeps_every_event_inside_any_window(db, monkeypatch):
    from dataclasses import replace
    from app import maintenance

    now = db.scalar(select(func.now()))
    login = Limit(rate_limit.LOGIN_EMAIL, "edge@example.com", 1, WINDOW)
    write = Limit(rate_limit.WRITE_COMMENT, "198.51.100.9", 1, rate_limit.WRITE_WINDOW_SECONDS)
    db.add_all([
        RateLimitEvent(bucket=login.bucket, key_hash=login.hashed, created_at=now - timedelta(seconds=WINDOW - 1)),
        RateLimitEvent(bucket=write.bucket, key_hash=write.hashed, created_at=now - timedelta(seconds=rate_limit.WRITE_WINDOW_SECONDS - 1)),
    ])
    db.commit()
    assert cleanup(db).rate_limit_events == 0
    assert rate_limit.retry_after(db, login) == 1 and rate_limit.retry_after(db, write) == 1  # both still enforced

    # A longer configured sign-in window extends retention to match.
    monkeypatch.setattr(maintenance, "settings", replace(settings, login_failure_window_seconds=7200))
    assert maintenance.rate_limit_retention_seconds() == 7200
    long_login = Limit(rate_limit.LOGIN_EMAIL, "long@example.com", 1, 7200)
    db.add(RateLimitEvent(bucket=long_login.bucket, key_hash=long_login.hashed, created_at=now - timedelta(seconds=7199)))
    db.commit()
    assert cleanup(db).rate_limit_events == 0
    assert rate_limit.retry_after(db, long_login) == 1
