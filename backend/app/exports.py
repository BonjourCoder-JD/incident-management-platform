"""CSV export of the incidents a user may see (GET /exports/incidents.csv).

Order of operations for every request:
1. Open a dedicated REPEATABLE READ, READ ONLY connection (one snapshot for count and rows).
2. Count the matching visible incidents. Over the limit: record a `rejected` export, close
   the connection, answer 413. Nothing is ever truncated.
3. Record an `initiated` export (committed in the normal request session) before any data
   is sent, so no export happens unrecorded.
4. Stream the rows from a server-side cursor in batches; the connection is closed when the
   stream ends, fails, or is abandoned.
"""
import csv
import io
from datetime import datetime, timezone
from typing import Annotated, Callable, Iterator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import Connection, func, select
from sqlalchemy.orm import Session, aliased
from starlette.background import BackgroundTask

from .auth import CurrentUser
from .config import settings
from .database import engine, get_db
from .incidents import _visible_to, incident_filters
from .models import ExportOutcome, Incident, IncidentExport, IncidentStatus, Severity, User

router = APIRouter(prefix="/exports", tags=["exports"])

COLUMNS = (
    "id", "title", "description", "severity", "status",
    "reporter_email", "assignee_email", "created_at_utc", "updated_at_utc",
)
BATCH_SIZE = 500
BOM = "\ufeff"  # lets Excel detect UTF-8

# A spreadsheet may evaluate a cell as a formula when it starts with one of these
# (OWASP CSV injection guidance), including the full-width forms some applications accept.
FORMULA_TRIGGERS = frozenset("=+-@\t\r\n") | frozenset("\uff1d\uff0b\uff0d\uff20")  # full-width = + - @


def neutralize_cell(value: str | None) -> str:
    """Prefix an apostrophe when a text cell could start a formula.

    Checked on the first character and on the first non-whitespace character, because some
    applications ignore leading spaces. Spreadsheets show the apostrophe-prefixed value as text.
    """
    if not value:
        return ""
    first_visible = value.lstrip()[:1]
    if value[0] in FORMULA_TRIGGERS or (first_visible and first_visible in FORMULA_TRIGGERS):
        return "'" + value
    return value


def _utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def csv_row(row) -> list[str]:
    """One incident as CSV cells. Server-generated values (id, enums, timestamps) are not altered."""
    return [
        str(row.id),
        neutralize_cell(row.title),
        neutralize_cell(row.description),
        row.severity.value,
        row.status.value,
        neutralize_cell(row.reporter_email),
        neutralize_cell(row.assignee_email),
        _utc(row.created_at),
        _utc(row.updated_at),
    ]


# ---- Data access --------------------------------------------------------------------------

def open_export_connection() -> Connection:
    """A new connection whose transaction is REPEATABLE READ, READ ONLY (transaction-scoped)."""
    conn = engine.connect()
    try:
        conn.begin()
        conn.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
    except Exception:
        conn.close()
        raise
    return conn


def get_export_opener() -> Callable[[], Connection]:
    """Dependency so tests can supply their own connection."""
    return open_export_connection


def export_query(user: User, severity: Severity | None, status: IncidentStatus | None):
    reporter = aliased(User, name="reporter")
    assignee = aliased(User, name="assignee")
    query = (
        select(
            Incident.id, Incident.title, Incident.description, Incident.severity, Incident.status,
            reporter.email.label("reporter_email"), assignee.email.label("assignee_email"),
            Incident.created_at, Incident.updated_at,
        )
        .outerjoin(reporter, reporter.id == Incident.reporter_id)
        .outerjoin(assignee, assignee.id == Incident.assignee_id)
        .where(*incident_filters(severity, status))
        .order_by(Incident.id.desc())
    )
    return _visible_to(query, user)


def count_query(user: User, severity: Severity | None, status: IncidentStatus | None):
    return _visible_to(select(func.count()).select_from(Incident).where(*incident_filters(severity, status)), user)


def stream_csv(conn: Connection, query) -> Iterator[bytes]:
    """Yield the CSV (BOM + header, then rows in batches). Always closes `conn`."""
    try:
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\r\n")  # RFC 4180: CRLF, minimal quoting
        buffer.write(BOM)
        writer.writerow(COLUMNS)
        yield buffer.getvalue().encode("utf-8")
        result = conn.execute(query, execution_options={"stream_results": True, "yield_per": BATCH_SIZE})
        for batch in result.partitions(BATCH_SIZE):
            buffer.seek(0)
            buffer.truncate()
            writer.writerows(csv_row(row) for row in batch)
            yield buffer.getvalue().encode("utf-8")
    finally:
        conn.close()


def _filename(severity: Severity | None, status: IncidentStatus | None) -> str:
    # Built only from the date and validated enum values: always a safe ASCII name.
    parts = ["incidents", datetime.now(timezone.utc).date().isoformat()]
    parts += [value.value for value in (severity, status) if value is not None]
    return "-".join(parts) + ".csv"


def _record(db: Session, user: User, outcome: ExportOutcome, severity, status, row_count: int, row_limit: int) -> None:
    db.add(IncidentExport(
        actor_id=user.id,
        outcome=outcome,
        filters={"severity": severity.value if severity else None, "status": status.value if status else None},
        row_count=row_count,
        row_limit=row_limit,
    ))
    db.commit()


def row_limit() -> int:
    return settings.export_max_rows


@router.get("/incidents.csv")
def export_incidents_csv(
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    open_connection: Annotated[Callable[[], Connection], Depends(get_export_opener)],
    severity: Severity | None = None,
    status: IncidentStatus | None = None,
):
    limit = row_limit()
    conn = open_connection()
    try:
        total = conn.execute(count_query(user, severity, status)).scalar_one()
        if total > limit:
            _record(db, user, ExportOutcome.REJECTED, severity, status, total, limit)
            raise HTTPException(
                status_code=413,
                detail=f"This export would contain {total:,} incidents; the limit is {limit:,}. Narrow the filters and try again.",
                headers={"X-Export-Row-Count": str(total)},
            )
        # Recorded (and committed) before any data leaves the server.
        _record(db, user, ExportOutcome.INITIATED, severity, status, total, limit)
    except BaseException:
        conn.close()
        raise

    filename = _filename(severity, status)
    return StreamingResponse(
        stream_csv(conn, export_query(user, severity, status)),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
            "X-Export-Row-Count": str(total),
        },
        # Runs after the response; closes the connection if the stream never started.
        background=BackgroundTask(conn.close),
    )
