"""Milestone 6 Phase D: structured logs, request IDs, service health and the restore check."""
import json
import logging
import re
import sys

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app import cli, heartbeat, logs, sla_store
from app.config import settings
from app.database import get_db
from app.main import app
from app.restore_check import verify_database
from conftest import BASE_URL, TEST_PASSWORD

CANARY = "canary-7f3a-sensitive-incident-text"


class Collector(logging.Handler):
    """Every record from every logger, formatted as production formats it."""

    def __init__(self):
        super().__init__(logging.DEBUG)
        self.setFormatter(logs.JsonFormatter())
        self.lines: list[str] = []

    def emit(self, record):
        if not record.name.startswith(("httpx", "httpcore")):  # the test's own HTTP client, not the application
            self.lines.append(self.format(record))

    def entries(self):
        return [json.loads(line) for line in self.lines]

    def requests(self):
        return [entry for entry in self.entries() if entry["logger"] == "app.request"]


@pytest.fixture
def collected():
    root = logging.getLogger()
    handler, level = Collector(), root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    yield handler
    root.removeHandler(handler)
    root.setLevel(level)


def _record(message, *args, exc_info=None, **extra):
    record = logging.LogRecord("app.test", logging.WARNING, __file__, 1, message, args, exc_info)
    for key, value in extra.items():
        setattr(record, key, value)
    return record


# ---- Formatter and scrubbing ----------------------------------------------------------------------

def test_json_lines_have_the_standard_fields_and_extras():
    token = logs.request_id_var.set("a" * 32)
    try:
        entry = json.loads(logs.JsonFormatter().format(_record("Incident %s updated", 42, status=200, path="/incidents/{incident_id}")))
    finally:
        logs.request_id_var.reset(token)
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z", entry.pop("time"))
    assert entry == {"level": "WARNING", "logger": "app.test", "message": "Incident 42 updated", "request_id": "a" * 32,
                     "status": 200, "path": "/incidents/{incident_id}"}


def test_exceptions_are_recorded_with_type_and_a_scrubbed_traceback():
    try:
        raise RuntimeError("connect postgresql+psycopg://incident:hunter2-password@db/x failed")
    except RuntimeError:
        entry = json.loads(logs.JsonFormatter().format(_record("failed", exc_info=sys.exc_info())))
    assert entry["exc_type"] == "RuntimeError"
    assert "Traceback" in entry["exc"] and "incident:***@db" in entry["exc"]
    assert "hunter2" not in json.dumps(entry)


@pytest.mark.parametrize("raw, secret", [
    ("postgresql+psycopg://imp_app:Sup3r-Secret@db:5432/incidents", "Sup3r-Secret"),
    ("password=hunter2 user=incident", "hunter2"),
    ('{"password": "hunter2"}', "hunter2"),
    ("csrf_token: abcdef123456", "abcdef123456"),
    ("APP_DB_PASSWORD='quoted secret value'", "quoted secret value"),
    ("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig", "eyJhbGciOiJIUzI1NiJ9"),
    ("key sk-ant-api03-AbCdEf_123-xyz in use", "AbCdEf_123"),
    ("duplicate key\nDETAIL:  Key (email)=(someone@example.com) already exists.", "someone@example.com"),
    (f'new row violates check constraint\nFailing row contains (7, {CANARY}, low).', CANARY),
    (f"[SQL: INSERT INTO incidents] [parameters: {{'title': '{CANARY}'}}]", CANARY),
])
def test_secrets_and_row_values_are_scrubbed(raw, secret):
    assert secret not in logs.scrub(raw)
    assert secret not in logs.JsonFormatter().format(_record(raw))


def test_database_errors_never_reveal_parameters_or_row_values(connection):
    email = f"{CANARY}@example.com"
    insert = text("INSERT INTO users (email, password_hash, role) VALUES (:email, 'not-a-hash', 'reporter')")
    connection.execute(insert, {"email": email})
    with pytest.raises(IntegrityError) as caught:
        connection.execute(insert, {"email": email})
    message = str(caught.value)
    assert "[SQL parameters hidden due to hide_parameters=True]" in message
    assert message.count(email) == 1  # only PostgreSQL's DETAIL line, which the log formatter withholds
    line = logs.JsonFormatter().format(_record("insert failed", exc_info=(caught.type, caught.value, caught.tb)))
    assert CANARY not in line and "IntegrityError" in line


# ---- Request IDs and request logs -----------------------------------------------------------------

def test_every_response_has_a_fresh_request_id_and_client_ids_are_ignored():
    client = TestClient(app, base_url=BASE_URL)
    first, second = client.get("/health"), client.get("/health", headers={"X-Request-ID": "attacker-chosen-id"})
    ids = [first.headers["x-request-id"], second.headers["x-request-id"]]
    assert all(re.fullmatch(r"[0-9a-f]{32}", value) for value in ids) and ids[0] != ids[1]


def test_the_request_log_records_the_route_template_status_and_duration_only(collected, login_as, responder):
    client = login_as(responder)
    response = client.get(f"/incidents?q={CANARY}&status=open")
    requests = collected.requests()
    last = requests[-1]
    assert last["request_id"] == response.headers["x-request-id"]
    assert (last["method"], last["path"], last["status"]) == ("GET", "/incidents", response.status_code)
    assert isinstance(last["duration_ms"], float)
    client.get("/incidents/987654321")
    assert collected.requests()[-1]["path"] == "/incidents/{incident_id}"
    client.get("/no/such/route")
    assert collected.requests()[-1]["path"] == "<unmatched>"
    assert CANARY not in "\n".join(collected.lines)


def test_no_password_token_or_incident_text_reaches_any_log(collected, client, responder):
    wrong = client.post("/auth/login", json={"email": responder.email, "password": "wrong-password-canary-123"})
    assert wrong.status_code == 401
    login = client.post("/auth/login", json={"email": responder.email, "password": TEST_PASSWORD})
    csrf = login.json()["csrf_token"]
    session = client.cookies.get(settings.session_cookie_name)
    assert session
    client.headers["X-CSRF-Token"] = csrf
    created = client.post("/incidents", json={"title": f"{CANARY} title", "description": f"{CANARY} description", "severity": "low"})
    assert created.status_code == 201
    client.post(f"/incidents/{created.json()['id']}/comments", json={"body": f"{CANARY} comment", "visibility": "internal"})
    client.put("/auth/password", json={"current_password": TEST_PASSWORD, "new_password": "new-password-canary-456"})
    everything = "\n".join(collected.lines)
    assert collected.lines  # the request log ran
    for secret in (CANARY, TEST_PASSWORD, "wrong-password-canary-123", "new-password-canary-456", csrf, session):
        assert secret not in everything


def test_an_unhandled_error_returns_a_json_500_naming_the_request_id_and_is_logged(collected):
    def broken_db():
        raise RuntimeError("database exploded; password=hunter2")
        yield  # pragma: no cover

    app.dependency_overrides[get_db] = broken_db
    try:
        response = TestClient(app, base_url=BASE_URL).get("/incidents")
    finally:
        app.dependency_overrides.pop(get_db, None)
    request_id = response.headers["x-request-id"]
    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error", "request_id": request_id}
    error = next(e for e in collected.entries() if e["level"] == "ERROR")
    assert (error["message"], error["exc_type"], error["request_id"]) == ("Unhandled error", "RuntimeError", request_id)
    assert "hunter2" not in "\n".join(collected.lines)
    assert collected.requests()[-1]["status"] == 500


@pytest.fixture
def clean_logging():
    root, loggers = logging.getLogger(), [logging.getLogger(n) for n in ("uvicorn", "uvicorn.error", "uvicorn.access")]
    saved = (root.handlers[:], root.level, [(lg.handlers[:], lg.propagate, lg.disabled) for lg in loggers])
    yield
    root.handlers, root.level = saved[0], saved[1]
    for lg, (handlers, propagate, disabled) in zip(loggers, saved[2]):
        lg.handlers, lg.propagate, lg.disabled = handlers, propagate, disabled


def test_json_logging_takes_over_uvicorn_and_drops_its_access_log(clean_logging, monkeypatch):
    monkeypatch.setenv("LOG_FORMAT", "json")
    logs.configure()
    logs.configure()  # idempotent
    root = logging.getLogger()
    assert [type(h.formatter) for h in root.handlers] == [logs.JsonFormatter]
    assert logging.getLogger("uvicorn.error").propagate and not logging.getLogger("uvicorn.error").handlers
    assert logging.getLogger("uvicorn.access").disabled  # it would record client addresses and query strings


def test_text_logging_is_the_development_default(clean_logging, monkeypatch):
    monkeypatch.delenv("LOG_FORMAT", raising=False)
    logs.configure()
    assert not any(isinstance(h.formatter, logs.JsonFormatter) for h in logging.getLogger().handlers)
    assert not logging.getLogger("uvicorn.access").disabled


# ---- Service health -------------------------------------------------------------------------------

@pytest.fixture
def status_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("STATUS_DIR", str(tmp_path / "status"))
    return tmp_path / "status"


def test_heartbeat_states(status_dir):
    assert heartbeat.check("worker", 300) == (False, "no status recorded yet")
    heartbeat.record("worker", True)
    assert heartbeat.check("worker", 300)[0] is True
    assert heartbeat.check("worker", 300, now=heartbeat.time.time() + 301)[1].startswith("no successful attempt for 301 s")
    heartbeat.record("worker", False, "escalation run failed (OperationalError)")
    healthy, reason = heartbeat.check("worker", 300)
    assert not healthy and "escalation run failed (OperationalError)" in reason
    assert oct((status_dir / "worker.json").parent.stat().st_mode)[-3:] in {"700", "777"}  # 777 only on Windows hosts


def test_recording_a_heartbeat_never_raises(monkeypatch, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("not a directory")
    monkeypatch.setenv("STATUS_DIR", str(blocker / "status"))
    heartbeat.record("worker", True)  # cannot be written; the service carries on


def test_the_health_check_command(status_dir, capsys):
    assert cli.main(["health-check", "worker", "--max-age", "300"]) == 1
    heartbeat.record("worker", True)
    assert cli.main(["health-check", "worker", "--max-age", "300"]) == 0
    heartbeat.record("demo-reset", False, "refused")
    assert cli.main(["health-check", "demo-reset", "--max-age", "90000"]) == 1
    assert "refused" in capsys.readouterr().err


def test_the_worker_records_each_run(status_dir, monkeypatch):
    import app.escalation
    import app.health

    assert cli.sla_escalate(loop=False, interval=60) == 0
    assert heartbeat.check("worker", 300)[0]

    def broken(*args, **kwargs):
        raise RuntimeError("synthetic")
    monkeypatch.setattr(app.escalation, "run_escalations", broken)
    assert cli.sla_escalate(loop=False, interval=60) == 1
    assert heartbeat.check("worker", 300)[1].endswith("escalation run failed (RuntimeError)")

    monkeypatch.setattr(app.health, "readiness", lambda *a, **k: (False, "migrations_pending"))
    assert cli.sla_escalate(loop=False, interval=60) == 1
    assert "database not ready (migrations_pending)" in heartbeat.check("worker", 300)[1]


def test_a_failed_cleanup_marks_the_worker_unhealthy(status_dir, monkeypatch):
    class Stop(Exception):
        pass

    def stop_after_first_iteration(seconds):
        raise Stop
    monkeypatch.setattr(cli, "cleanup", lambda: 1)
    monkeypatch.setattr(cli.time, "sleep", stop_after_first_iteration)
    with pytest.raises(Stop):
        cli.sla_escalate(loop=True, interval=60)
    healthy, reason = heartbeat.check("worker", 300)
    assert not healthy and "the last cleanup failed" in reason


def test_a_refused_demo_reset_marks_the_service_unhealthy(status_dir, monkeypatch):
    monkeypatch.delenv("DEMO_DATABASE", raising=False)
    assert cli.demo_reset(daily=False) == 2  # demo mode is off in the test settings
    healthy, reason = heartbeat.check("demo-reset", 90000)
    assert not healthy and "refused" in reason


# ---- Restore check --------------------------------------------------------------------------------

def test_the_restore_check_passes_on_a_consistent_database(connection):
    problems, counts = verify_database(connection)
    assert problems == []
    assert set(counts) >= {"users", "incidents", "incident_events", "sla_cycles_checked"}


def test_the_restore_check_finds_a_missing_append_only_trigger(connection):
    trigger = connection.execute(text(
        "SELECT t.tgname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
        "WHERE c.relname = 'incident_events' AND NOT t.tgisinternal")).scalars().first()
    connection.execute(text(f'ALTER TABLE incident_events DISABLE TRIGGER "{trigger}"'))  # rolled back afterwards
    problems, _ = verify_database(connection)
    assert any("append-only protection missing on incident_events" in p for p in problems)


def test_the_restore_check_finds_a_wrong_schema_revision(connection):
    connection.execute(text("UPDATE alembic_version SET version_num = '0001'"))
    problems, counts = verify_database(connection)
    assert problems and "differs from the code" in problems[0] and counts == {}


def test_the_restore_check_finds_orphaned_rows(connection):
    connection.execute(text("SET LOCAL session_replication_role = replica"))  # skip the foreign keys, inside this transaction
    connection.execute(text("INSERT INTO incident_events (incident_id, actor_id, action, changes) VALUES (2147483000, 2147483000, 'created', '{}')"))
    connection.execute(text("SET LOCAL session_replication_role = origin"))
    problems, _ = verify_database(connection)
    assert any("refer to missing users or incidents" in p for p in problems)


def test_the_restore_check_reports_sla_differences(connection, monkeypatch):
    monkeypatch.setattr(sla_store, "verify", lambda conn: (3, 1, [17]))
    problems, _ = verify_database(connection)
    assert problems == ["SLA read model differs from the audit trail for incidents [17]"]


def test_the_verify_restore_command(capsys, monkeypatch):
    assert cli.main(["verify-restore"]) == 0
    assert "verify-restore: consistent" in capsys.readouterr().out
    import app.restore_check
    monkeypatch.setattr(app.restore_check, "verify_database", lambda conn: (["synthetic problem"], {}))
    assert cli.main(["verify-restore"]) == 1
    assert "synthetic problem" in capsys.readouterr().err


def test_verify_restore_checks_another_database_with_the_same_credentials(capsys, connection):
    name = connection.execute(text("SELECT current_database()")).scalar_one()
    assert cli.main(["verify-restore", "--database", name]) == 0
    assert cli.main(["verify-restore", "--database", "no_such_database_restore_1"]) == 1  # cannot connect: reported, not raised
    assert "cannot check the database (OperationalError)" in capsys.readouterr().err
    for unsafe in ("Incidents", "x; DROP DATABASE y", "../etc", ""):
        assert cli.main(["verify-restore", "--database", unsafe]) == 2


def test_service_status_lines_are_json_in_production_and_plain_otherwise(collected, capsys, monkeypatch):
    monkeypatch.setenv("LOG_FORMAT", "json")
    assert cli.cleanup() == 0
    line = [e for e in collected.entries() if e["logger"] == "app.cli"][-1]
    assert line["level"] == "INFO" and line["message"].startswith("Maintenance: removed")
    assert "Maintenance" not in capsys.readouterr().out
    monkeypatch.setenv("LOG_FORMAT", "text")
    assert cli.cleanup() == 0
    assert capsys.readouterr().out.startswith("Maintenance: removed")
