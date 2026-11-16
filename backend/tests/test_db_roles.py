"""Least-privilege runtime role, tested against real PostgreSQL roles (Milestone 6 Phase B, ISSUE-12).

These tests commit: they create a uniquely named login role, connect as it, and remove the role and
any rows they created afterwards. The owner connection is the test suite's normal one.
"""
import secrets

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.orm import sessionmaker

from app import db_roles
from app.database import DATABASE_URL, engine as owner_engine, get_db
from app.health import get_health_bind, readiness
from app.main import app
from app.security import hash_password
from conftest import BASE_URL


@pytest.fixture(scope="module")
def runtime():
    """A fresh runtime role with least privilege, and an engine connected as it."""
    role = f"imp_app_test_{secrets.token_hex(4)}"
    password = secrets.token_hex(24)
    with owner_engine.begin() as conn:
        db_roles.apply(conn, role, password)
    engine = create_engine(make_url(DATABASE_URL).set(username=role, password=password), pool_pre_ping=True)
    try:
        yield role, password, engine
    finally:
        engine.dispose()
        with owner_engine.begin() as conn:
            conn.exec_driver_sql(f"DROP OWNED BY {role}")
            conn.exec_driver_sql(f"DROP ROLE {role}")


def _refused(engine, statement: str):
    with engine.connect() as conn:
        with pytest.raises((ProgrammingError, DBAPIError)) as error:
            conn.execute(text(statement))
        conn.rollback()
    return str(error.value)


# ---- The privilege map ----------------------------------------------------------------------------

def test_every_table_has_a_privilege_decision():
    with owner_engine.connect() as conn:
        assert db_roles.public_tables(conn) == set(db_roles.RUNTIME_PRIVILEGES)


def test_every_append_only_table_has_its_triggers():
    with owner_engine.connect() as conn:
        protected = set(conn.execute(text(
            "SELECT DISTINCT c.relname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
            "WHERE NOT t.tgisinternal AND t.tgenabled = 'O' AND (t.tgtype & 16) <> 0")).scalars())  # 16 = UPDATE
    assert db_roles.APPEND_ONLY_TABLES <= protected


def test_the_runtime_role_is_not_privileged(runtime):
    role, _, engine = runtime
    with engine.connect() as conn:
        assert db_roles.role_problems(conn) == []
        attributes = conn.execute(text(
            "SELECT rolsuper, rolcreaterole, rolcreatedb, rolreplication, rolbypassrls FROM pg_roles WHERE rolname = current_user")).one()
        assert not any(attributes)
    with owner_engine.connect() as conn:
        assert "superuser" in db_roles.role_problems(conn)  # the owner would be refused in production


@pytest.mark.parametrize("table", sorted(db_roles.APPEND_ONLY_TABLES))
def test_audit_tables_cannot_be_changed_or_removed(runtime, table):
    _, _, engine = runtime
    with owner_engine.connect() as conn:
        column = conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name = :t "
                                   "ORDER BY ordinal_position LIMIT 1"), {"t": table}).scalar_one()
    for statement in (f"UPDATE {table} SET {column} = {column}", f"DELETE FROM {table}", f"TRUNCATE {table}"):
        assert "permission denied" in _refused(engine, statement), statement


@pytest.mark.parametrize("statement", [
    "ALTER TABLE incident_events DISABLE TRIGGER ALL",
    "ALTER TABLE incident_comments DISABLE TRIGGER incident_comments_no_update_delete",
    "DROP TRIGGER incident_comments_no_update_delete ON incident_comments",
    "DROP FUNCTION incident_comments_immutable()",
    "CREATE OR REPLACE FUNCTION incident_comments_immutable() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$",
    "ALTER TABLE comment_redactions DROP CONSTRAINT uq_comment_redactions_comment_id",
    "DROP TABLE incident_events",
    "CREATE TABLE public.attacker (id int)",
    "SET session_replication_role = replica",
    "ALTER ROLE CURRENT_USER SUPERUSER",
    "SELECT rolpassword FROM pg_authid",
    "TRUNCATE incidents",
    "DELETE FROM incidents",
    "DELETE FROM users",
])
def test_the_runtime_role_cannot_weaken_the_protections(runtime, statement):
    _, _, engine = runtime
    message = _refused(engine, statement)
    assert "permission denied" in message or "must be owner" in message or "must be superuser" in message, message


def test_the_runtime_role_cannot_grant_anything(runtime):
    _, _, engine = runtime
    with engine.connect() as conn:
        conn.execute(text("GRANT ALL ON incident_events TO PUBLIC"))  # PostgreSQL only warns: nothing to grant
        conn.commit()
    with owner_engine.connect() as conn:
        assert conn.execute(text("SELECT has_table_privilege('public', 'incident_events', 'UPDATE')")).scalar_one() is False


def test_the_runtime_role_can_do_what_the_application_needs(runtime):
    _, _, engine = runtime
    with engine.connect() as conn:
        for table in sorted(db_roles.RUNTIME_PRIVILEGES):
            conn.execute(text(f"SELECT count(*) FROM {table}"))
        conn.execute(text("ANALYZE incident_escalations"))
        conn.execute(text("SELECT pg_advisory_xact_lock(1)"))
        conn.rollback()


def test_apply_is_idempotent_and_rotates_the_password(runtime):
    role, _, _ = runtime
    new_password = secrets.token_hex(24)
    with owner_engine.begin() as conn:
        db_roles.apply(conn, role, new_password)
        db_roles.apply(conn, role, new_password)
    rotated = create_engine(make_url(DATABASE_URL).set(username=role, password=new_password))
    with rotated.connect() as conn:
        assert conn.execute(text("SELECT current_user")).scalar_one() == role
    rotated.dispose()


@pytest.mark.parametrize("role, password, message", [
    ("Bad-Name", "x" * 32, "lower-case letters"),
    ("imp_ok", "short", "at least 24"),
])
def test_apply_refuses_unsafe_arguments(role, password, message):
    with owner_engine.begin() as conn:
        with pytest.raises(ValueError, match=message):
            db_roles.apply(conn, role, password)


def test_apply_refuses_the_owner_as_runtime_role():
    with owner_engine.connect() as conn:
        owner = conn.execute(text("SELECT current_user")).scalar_one()
        with pytest.raises(ValueError, match="must differ from the migration owner"):
            db_roles.apply(conn, owner, "x" * 32)
        conn.rollback()


def test_apply_refuses_tables_without_a_decision():
    with owner_engine.connect() as conn:
        conn.execute(text("CREATE TABLE public.unclassified_probe (id int)"))
        with pytest.raises(ValueError, match="unclassified_probe"):
            db_roles.apply(conn, "imp_unused_probe", "x" * 32)
        conn.rollback()


def test_the_cli_refuses_a_missing_password(monkeypatch, capsys):
    from app import cli
    monkeypatch.delenv("APP_DB_PASSWORD", raising=False)
    assert cli.db_roles() == 2
    assert "at least 24" in capsys.readouterr().err


def test_production_readiness_refuses_an_unsafe_role(runtime):
    _, _, engine = runtime
    assert readiness(engine, check_role=True) == (True, "ok")
    assert readiness(owner_engine, check_role=True) == (False, "unsafe_database_role")
    assert readiness(owner_engine, check_role=False) == (True, "ok")  # development: the owner is fine


# ---- The application works as the runtime role ------------------------------------------------

def test_the_application_runs_as_the_runtime_role(runtime):
    """Sign-in, incidents, comments, redaction, history, analytics, export, SLA worker and housekeeping
    as the least-privilege role. Rows are removed afterwards by the owner."""
    from app.escalation import run_escalations
    from app.maintenance import cleanup

    _, _, engine = runtime
    tag = secrets.token_hex(4)
    admin_email = f"runtime.admin.{tag}@example.com"
    with owner_engine.begin() as conn:  # the bootstrap admin is created by the owner, as in production
        admin_id = conn.execute(text("INSERT INTO users (email, password_hash, role) VALUES (:e, :h, 'admin') RETURNING id"),
                                {"e": admin_email, "h": hash_password("runtime-role-passphrase")}).scalar_one()
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def runtime_db():
        with sessions() as session:
            yield session
    app.dependency_overrides[get_db] = runtime_db
    app.dependency_overrides[get_health_bind] = lambda: engine
    incident_id = None
    try:
        client = TestClient(app, base_url=BASE_URL)
        login = client.post("/auth/login", json={"email": admin_email, "password": "runtime-role-passphrase"})
        assert login.status_code == 200, login.text
        client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
        assert client.post("/auth/login", json={"email": admin_email, "password": "wrong-password-123"}).status_code == 401
        created = client.post("/incidents", json={"title": f"Runtime {tag}", "description": "Synthetic", "severity": "high"})
        assert created.status_code == 201, created.text
        incident_id = created.json()["id"]
        assert client.patch(f"/incidents/{incident_id}", json={"status": "investigating", "severity": "critical"}).status_code == 200
        comment = client.post(f"/incidents/{incident_id}/comments", json={"body": "Synthetic note", "visibility": "internal"}).json()
        assert client.post(f"/incidents/{incident_id}/comments/{comment['id']}/redaction", json={"reason": "Test"}).status_code == 201
        for path in (f"/incidents/{incident_id}/events", f"/incidents/{incident_id}/sla", f"/incidents/{incident_id}/comments",
                     "/incidents?sla=overdue", "/analytics/summary?from=2026-01-01&to=2026-12-31&tz=UTC",
                     "/exports/incidents.csv", "/health/ready", "/users"):
            assert client.get(path).status_code == 200, path
        assert client.post("/auth/logout").status_code == 204
        run_escalations(engine)
        with sessions() as session:
            cleanup(session)
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_health_bind, None)
        with owner_engine.begin() as conn:
            conn.execute(text("SET LOCAL session_replication_role = replica"))
            if incident_id is not None:
                for table in ("comment_redactions",):
                    conn.execute(text(f"DELETE FROM {table} WHERE comment_id IN (SELECT id FROM incident_comments WHERE incident_id = :i)"), {"i": incident_id})
                for table in ("incident_comments", "incident_escalations", "incident_sla_cycles", "incident_events"):
                    conn.execute(text(f"DELETE FROM {table} WHERE incident_id = :i"), {"i": incident_id})
                conn.execute(text("DELETE FROM incidents WHERE id = :i"), {"i": incident_id})
            conn.execute(text("DELETE FROM incident_exports WHERE actor_id = :u"), {"u": admin_id})
            conn.execute(text("DELETE FROM auth_sessions WHERE user_id = :u"), {"u": admin_id})
            conn.execute(text("DELETE FROM rate_limit_events"))  # only this test commits events here
            conn.execute(text("DELETE FROM users WHERE id = :u"), {"u": admin_id})
