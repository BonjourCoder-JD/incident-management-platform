"""Least-privilege runtime database role (Milestone 6 Phase B, ISSUE-12).

Two roles in production:
- the migration owner (POSTGRES_USER) owns every table, function and trigger and runs `alembic` and
  `python -m app.cli db-roles`;
- the runtime role (APP_DB_USER, default imp_app) is what the API and the worker connect as. It owns
  nothing and holds only the privileges below, so it cannot ALTER, DISABLE or DROP the append-only
  triggers, TRUNCATE anything, create objects, or UPDATE or DELETE audit records.

Every table must be listed here: `apply` refuses to run when a migration adds a table without a
decision, so new tables never get privileges by accident.
"""
import re

from sqlalchemy import Connection, text

DEFAULT_RUNTIME_ROLE = "imp_app"

# Audit and history tables: the application may read and add rows, never change or remove them.
APPEND_ONLY_TABLES = frozenset({
    "incident_events", "incident_exports", "incident_comments", "comment_redactions",
    "sla_policy_versions", "sla_policy_targets", "incident_escalations",
    "triage_requests", "triage_suggestions", "triage_decisions",
})

RUNTIME_PRIVILEGES: dict[str, tuple[str, ...]] = {
    "users": ("SELECT", "INSERT", "UPDATE"),
    "auth_sessions": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "incidents": ("SELECT", "INSERT", "UPDATE"),
    "incident_sla_cycles": ("SELECT", "INSERT", "UPDATE"),  # its own trigger keeps history final
    "rate_limit_events": ("SELECT", "INSERT", "DELETE"),
    "alembic_version": ("SELECT",),  # readiness check
    # The worker refreshes planner statistics after a large escalation run (PostgreSQL 17 MAINTAIN).
    "incident_escalations": ("SELECT", "INSERT", "MAINTAIN"),
    **{table: ("SELECT", "INSERT") for table in APPEND_ONLY_TABLES - {"incident_escalations"}},
}

_ROLE_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
MIN_PASSWORD_LENGTH = 24


def public_tables(conn: Connection) -> set[str]:
    return set(conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")).scalars())


def apply(conn: Connection, role: str, password: str) -> None:
    """Creates or updates the runtime role and sets exactly the privileges above. Idempotent. Run as
    the owner, in a transaction the caller commits. The password never appears in logs or errors."""
    if not _ROLE_NAME.match(role):
        raise ValueError("The runtime role name must be lower-case letters, digits and underscores.")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"The runtime role password must be at least {MIN_PASSWORD_LENGTH} characters.")
    if conn.execute(text("SELECT rolname FROM pg_roles WHERE rolname = current_user AND rolname = :r"), {"r": role}).first():
        raise ValueError("The runtime role must differ from the migration owner.")
    unclassified = public_tables(conn) - set(RUNTIME_PRIVILEGES)
    if unclassified:
        raise ValueError(f"Tables without a privilege decision in app/db_roles.py: {sorted(unclassified)}")

    exists = conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": role}).first() is not None
    verb = "ALTER" if exists else "CREATE"
    # format() quotes the identifier and the literal inside PostgreSQL; nothing is interpolated in Python.
    statement = conn.execute(
        text("SELECT format('%s ROLE %I WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS "
             "NOINHERIT PASSWORD %L', CAST(:verb AS text), CAST(:role AS text), CAST(:password AS text))"),
        {"verb": verb, "role": role, "password": password},
    ).scalar_one()
    conn.exec_driver_sql(statement)

    quoted = conn.execute(text("SELECT quote_ident(:r)"), {"r": role}).scalar_one()
    database = conn.execute(text("SELECT quote_ident(current_database())")).scalar_one()
    conn.exec_driver_sql(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {quoted}")
    conn.exec_driver_sql(f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {quoted}")
    conn.exec_driver_sql(f"REVOKE ALL ON SCHEMA public FROM {quoted}")
    conn.exec_driver_sql("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
    conn.exec_driver_sql(f"GRANT CONNECT ON DATABASE {database} TO {quoted}")
    conn.exec_driver_sql(f"GRANT USAGE ON SCHEMA public TO {quoted}")
    for table, privileges in sorted(RUNTIME_PRIVILEGES.items()):
        conn.exec_driver_sql(f"GRANT {', '.join(privileges)} ON TABLE public.{table} TO {quoted}")
    conn.exec_driver_sql(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {quoted}")


def role_problems(conn: Connection) -> list[str]:
    """Why the current connection's role is unsafe for the running application (empty when safe)."""
    problems = []
    if conn.execute(text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")).scalar_one():
        problems.append("superuser")
    if conn.execute(text("SELECT count(*) FROM pg_tables WHERE schemaname = 'public' AND tableowner = current_user")).scalar_one():
        problems.append("owns tables")
    writable = conn.execute(text(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename = ANY(:tables) "
        "AND has_table_privilege(current_user, format('public.%I', tablename), 'UPDATE, DELETE, TRUNCATE') ORDER BY 1"),
        {"tables": sorted(APPEND_ONLY_TABLES)}).scalars().all()
    if writable:
        problems.append(f"can modify {', '.join(writable)}")
    return problems
