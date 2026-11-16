"""Consistency check for a restored database (Milestone 6 Phase D).

`python -m app.cli verify-restore`, run as the schema owner against the restored copy (DATABASE_URL),
before it replaces the live database:
1. its schema revision is exactly the migration head of this code;
2. every append-only table still has its enabled UPDATE/DELETE trigger;
3. the stored SLA read model matches the audit trail (sla_store.verify);
4. every incident's reporter exists and every audit event belongs to an incident (the foreign keys
   were re-validated by the restore; this confirms the data arrived, not only the schema).
It reads only, and prints table counts so they can be compared with the source.
"""
from sqlalchemy import Connection, text

from . import sla_store
from .db_roles import APPEND_ONLY_TABLES
from .health import database_revisions, expected_revisions

COUNTED_TABLES = ("users", "incidents", "incident_events", "incident_comments", "incident_sla_cycles", "triage_suggestions")


def verify_database(conn: Connection) -> tuple[list[str], dict[str, int]]:
    """(problems, counts); no problems means the copy is consistent."""
    problems: list[str] = []
    current, expected = database_revisions(conn), expected_revisions()
    if current != expected:
        problems.append(f"schema revision {sorted(current) or 'missing'} differs from the code's {sorted(expected)}")
        return problems, {}
    protected = set(conn.execute(text(
        "SELECT DISTINCT c.relname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
        "WHERE NOT t.tgisinternal AND t.tgenabled = 'O' AND (t.tgtype & 16) <> 0")).scalars())  # 16 = UPDATE
    if missing := sorted(APPEND_ONLY_TABLES - protected):
        problems.append(f"append-only protection missing on {', '.join(missing)}")
    checked, mismatches, ids = sla_store.verify(conn)
    if mismatches:
        problems.append(f"SLA read model differs from the audit trail for incidents {ids}")
    orphans = conn.execute(text(
        "SELECT (SELECT count(*) FROM incidents i LEFT JOIN users u ON u.id = i.reporter_id WHERE i.reporter_id IS NOT NULL AND u.id IS NULL)"
        " + (SELECT count(*) FROM incident_events e LEFT JOIN incidents i ON i.id = e.incident_id WHERE i.id IS NULL)")).scalar_one()
    if orphans:
        problems.append(f"{orphans} rows refer to missing users or incidents")
    counts = {table: conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() for table in COUNTED_TABLES}
    counts["sla_cycles_checked"] = checked
    return problems, counts
