"""CSV export: format, formula-injection defence, visibility, limits, records and cleanup."""
import csv
import io
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app import exports
from app.database import engine
from app.main import app
from app.models import ExportOutcome, Incident, IncidentExport, IncidentStatus, Severity, UserRole
from conftest import BASE_URL, TEST_PASSWORD

URL = "/exports/incidents.csv"
BOM_BYTES = b"\xef\xbb\xbf"
T0 = datetime(2026, 3, 2, 9, 30, tzinfo=timezone.utc)


@pytest.fixture
def add(db):
    def incident(reporter, title="Exported incident", description="Synthetic", severity="medium", status="open", assignee=None, at=T0):
        row = Incident(
            title=title, description=description, severity=Severity(severity), status=IncidentStatus(status),
            reporter_id=reporter.id if reporter else None, assignee_id=assignee.id if assignee else None,
            created_at=at, updated_at=at + timedelta(hours=1),
        )
        db.add(row)
        db.commit()
        return row
    return incident


def parse(response) -> list[list[str]]:
    assert response.content.startswith(BOM_BYTES)
    return list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"), newline="")))


def rows_by_id(response) -> dict[str, dict[str, str]]:
    header, *rows = parse(response)
    return {row[0]: dict(zip(header, row)) for row in rows}


# ---- Format ------------------------------------------------------------------------------------

def test_headers_bom_crlf_and_columns(login_as, reporter, add):
    add(reporter)
    response = login_as(reporter).get(URL)
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/csv; charset=utf-8"
    today = datetime.now(timezone.utc).date().isoformat()
    assert response.headers["content-disposition"] == f'attachment; filename="incidents-{today}.csv"'
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-export-row-count"] == "1"
    assert response.content.startswith(BOM_BYTES)
    body = response.content.decode("utf-8-sig")
    assert body.startswith(",".join(exports.COLUMNS) + "\r\n")
    assert body.endswith("\r\n") and "\n" not in body.replace("\r\n", "")  # every record ends in CRLF
    assert parse(response)[0] == list(exports.COLUMNS)


def test_quoting_unicode_and_timestamps_round_trip(login_as, reporter, responder, add):
    title = 'Comma, "quoted" text and a line\nbreak'
    description = "Line one\r\nLine two — café, naïve, 日本語, emoji 🔐"
    incident = add(reporter, title=title, description=description, severity="high", status="investigating", assignee=responder)
    row = rows_by_id(login_as(reporter).get(URL))[str(incident.id)]
    assert row == {
        "id": str(incident.id), "title": title, "description": description, "severity": "high", "status": "investigating",
        "reporter_email": reporter.email, "assignee_email": responder.email,
        "created_at_utc": "2026-03-02T09:30:00Z", "updated_at_utc": "2026-03-02T10:30:00Z",
        # No `created` audit event, so not tracked (SLA columns are covered in test_sla.py).
        "response_due_utc": "", "response_state": "not_tracked", "resolution_due_utc": "", "resolution_state": "not_tracked",
    }


def test_empty_export_has_only_the_header(login_as, reporter):
    response = login_as(reporter).get(URL)
    assert response.status_code == 200 and parse(response) == [list(exports.COLUMNS)]
    assert response.headers["x-export-row-count"] == "0"


# ---- Formula injection -------------------------------------------------------------------------

DANGEROUS = [
    "=1+1", "+1", "-1", "@SUM(A1:A2)", "\t=1", "\r=1", "\n=1",
    "=HYPERLINK(\"http://example.com\",\"x\")", "=cmd|' /C calc'!A0",
    "   =1+1", " \t-2", "\u3000=1",                      # leading whitespace, including ideographic space
    "\uff1d1+1", "\uff0b1", "\uff0d1", "\uff20SUM(A1)",  # full-width = + - @
]


@pytest.mark.parametrize("value", DANGEROUS)
def test_formula_triggers_are_neutralised_in_every_text_column(value, login_as, make_user, add):
    reporter = make_user(UserRole.REPORTER, email=f"{'=' if value.startswith('=') else '+'}cell{len(value)}@example.com")
    incident = add(reporter, title=value, description=value)
    row = rows_by_id(login_as(reporter).get(URL))[str(incident.id)]
    assert row["title"] == "'" + value
    assert row["description"] == "'" + value
    assert row["reporter_email"] == "'" + reporter.email


@pytest.mark.parametrize("value", ["Normal title", "A=B", "Temperature -5", "user@example.com", "  leading spaces", "", "100"])
def test_harmless_text_is_unchanged(value):
    assert exports.neutralize_cell(value) == value


def test_server_generated_values_are_not_altered(login_as, reporter, add):
    incident = add(reporter, title="=1")
    row = rows_by_id(login_as(reporter).get(URL))[str(incident.id)]
    assert row["id"] == str(incident.id) and row["severity"] == "medium" and row["created_at_utc"].startswith("2026-")


# ---- Filters, ordering, visibility ----------------------------------------------------------------

def test_filters_and_newest_first(login_as, reporter, add):
    a = add(reporter, severity="high", status="open", at=T0)
    b = add(reporter, severity="high", status="resolved", at=T0 + timedelta(days=1))
    c = add(reporter, severity="low", status="open", at=T0 + timedelta(days=2))
    client = login_as(reporter)
    ids = lambda **params: [row[0] for row in parse(client.get(URL, params=params))[1:]]
    assert ids() == [str(c.id), str(b.id), str(a.id)]
    assert ids(severity="high") == [str(b.id), str(a.id)]
    assert ids(status="open") == [str(c.id), str(a.id)]
    assert ids(severity="high", status="open") == [str(a.id)]
    response = client.get(URL, params={"severity": "high", "status": "open"})
    assert response.headers["content-disposition"].endswith('-high-open.csv"')
    assert client.get(URL, params={"severity": "urgent"}).status_code == 422
    assert client.get(URL, params={"status": "deleted"}).status_code == 422


def test_reporters_export_only_their_own_incidents(login_as, reporter, other_reporter, responder, add):
    mine = add(reporter, title="Mine only")
    theirs = add(other_reporter, title="Other reporter secret")
    legacy = add(None, title="Legacy secret")

    response = login_as(reporter).get(URL)
    assert set(rows_by_id(response)) == {str(mine.id)}
    assert response.headers["x-export-row-count"] == "1"
    raw = response.content.decode("utf-8-sig")
    assert "Other reporter secret" not in raw and "Legacy secret" not in raw and other_reporter.email not in raw

    assert set(rows_by_id(login_as(other_reporter).get(URL))) == {str(theirs.id)}

    staff = rows_by_id(login_as(responder).get(URL))
    assert {str(mine.id), str(theirs.id), str(legacy.id)} <= set(staff)
    assert staff[str(legacy.id)]["reporter_email"] == ""


@pytest.mark.parametrize("role", [UserRole.RESPONDER, UserRole.ADMIN])
def test_staff_export_everything(role, make_user, login_as, reporter, add):
    incident = add(reporter)
    assert str(incident.id) in rows_by_id(login_as(make_user(role)).get(URL))


def test_requires_authentication_and_is_read_only(client, login_as, responder):
    assert client.get(URL).status_code == 401
    staff = login_as(responder)
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        assert staff.request(method, URL, json={}).status_code == 405


# ---- Limit and export records -----------------------------------------------------------------------

def _records(db, user):
    db.expire_all()
    return db.scalars(select(IncidentExport).where(IncidentExport.actor_id == user.id).order_by(IncidentExport.id)).all()


def test_over_limit_is_refused_before_streaming_and_never_truncated(login_as, reporter, add, monkeypatch, db, export_connections):
    monkeypatch.setattr(exports, "row_limit", lambda: 3)
    client = login_as(reporter)
    for _ in range(3):
        add(reporter)
    at_limit = client.get(URL)
    assert at_limit.status_code == 200 and len(parse(at_limit)) == 4  # header + 3 rows

    add(reporter)
    over = client.get(URL, params={"severity": "medium"})
    assert over.status_code == 413
    assert over.headers["x-export-row-count"] == "4"
    assert over.json()["detail"] == "This export would contain 4 incidents; the limit is 3. Narrow the filters and try again."
    assert not over.content.startswith(BOM_BYTES) and "Exported incident" not in over.text
    assert all(conn.closed for conn in export_connections.opened)

    records = _records(db, reporter)
    assert [(r.outcome, r.row_count, r.row_limit, r.filters) for r in records] == [
        (ExportOutcome.INITIATED, 3, 3, {"severity": None, "status": None}),
        (ExportOutcome.REJECTED, 4, 3, {"severity": "medium", "status": None}),
    ]
    assert all(r.created_at is not None for r in records)


def test_every_export_is_recorded_with_actor_and_filters(login_as, reporter, responder, add, db):
    add(reporter, severity="high", status="open")
    login_as(responder).get(URL, params={"severity": "high", "status": "open"})
    [record] = _records(db, responder)
    assert record.outcome == ExportOutcome.INITIATED
    assert record.filters == {"severity": "high", "status": "open"}
    assert record.row_count >= 1 and record.row_limit == exports.row_limit()
    assert _records(db, reporter) == []


@pytest.mark.parametrize("statement", [
    "UPDATE incident_exports SET row_count = 0 WHERE id = :id",
    "DELETE FROM incident_exports WHERE id = :id",
    "TRUNCATE incident_exports",
])
def test_export_records_are_append_only(statement, login_as, reporter, db):
    login_as(reporter).get(URL)
    [record] = _records(db, reporter)
    with pytest.raises(DBAPIError, match="append-only"):
        with db.begin_nested():
            db.execute(text(statement), {"id": record.id})
    with pytest.raises(DBAPIError):  # the actor cannot be deleted while records refer to them
        with db.begin_nested():
            db.execute(text("DELETE FROM users WHERE id = :id"), {"id": reporter.id})


# ---- Connection cleanup -------------------------------------------------------------------------------

@pytest.fixture
def lenient_client(reporter):
    """Signed in as `reporter`; server errors become 500 responses instead of test exceptions."""
    test_client = TestClient(app, base_url=BASE_URL, raise_server_exceptions=False)
    test_client.post("/auth/login", json={"email": reporter.email, "password": TEST_PASSWORD})
    yield test_client
    test_client.close()


def test_connection_closed_after_successful_stream(login_as, reporter, add, export_connections):
    add(reporter)
    assert login_as(reporter).get(URL).status_code == 200
    assert len(export_connections.opened) == 1 and export_connections.opened[0].closed


def test_connection_closed_when_failing_before_streaming(lenient_client, monkeypatch, export_connections):
    def broken_count(*args, **kwargs):
        raise RuntimeError("count failed")
    monkeypatch.setattr(exports, "count_query", broken_count)
    assert lenient_client.get(URL).status_code == 500
    assert export_connections.opened and all(conn.closed for conn in export_connections.opened)


def test_connection_closed_and_export_recorded_when_stream_fails(lenient_client, reporter, add, monkeypatch, export_connections, db):
    for _ in range(3):
        add(reporter)
    real_row = exports.csv_row
    calls = {"n": 0}

    def failing_row(row):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("row failed mid-stream")
        return real_row(row)

    monkeypatch.setattr(exports, "csv_row", failing_row)
    try:
        lenient_client.get(URL)
    except RuntimeError:
        pass  # depending on the transport, a mid-stream failure may surface here
    assert export_connections.opened[0].closed
    # Recorded as initiated: streaming began, completion is never claimed.
    assert [r.outcome for r in _records(db, reporter)] == [ExportOutcome.INITIATED]


def test_abandoned_stream_closes_the_real_connection():
    """A client disconnect closes the generator; the pooled connection must be returned."""
    checked_out = engine.pool.checkedout()
    conn = exports.open_export_connection()
    stream = exports.stream_csv(conn, select(Incident.id))
    first = next(stream)
    assert first.startswith(BOM_BYTES)
    assert engine.pool.checkedout() == checked_out + 1
    stream.close()
    assert conn.closed
    assert engine.pool.checkedout() == checked_out


def test_real_export_connection_is_a_read_only_repeatable_read_snapshot():
    conn = exports.open_export_connection()
    try:
        assert conn.execute(text("SHOW transaction_isolation")).scalar_one() == "repeatable read"
        assert conn.execute(text("SHOW transaction_read_only")).scalar_one() == "on"
        with pytest.raises(DBAPIError, match="read-only transaction"):
            conn.execute(text("UPDATE incidents SET title = title WHERE false"))
    finally:
        conn.close()
    with engine.connect() as normal:
        assert normal.execute(text("SHOW transaction_read_only")).scalar_one() == "off"


def test_export_query_applies_the_visibility_rule():
    from app.models import User
    reporter = User(id=424242, role=UserRole.REPORTER)
    for query in (exports.export_query(reporter, None, None, T0), exports.count_query(reporter, Severity.HIGH, None)):
        assert "reporter_id = 424242" in str(query.compile(compile_kwargs={"literal_binds": True}))
