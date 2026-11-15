"""Incident comments and internal notes: authorization, leakage, validation, ordering, immutability.

All data is synthetic. CANARY strings mark internal content that a reporter must never receive.
"""
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app import comments as comments_module
from app.main import app
from app.models import CommentVisibility, Incident, IncidentComment, IncidentEvent, Severity, UserRole
from app.schemas import COMMENT_BLANK_CHARS, COMMENT_MAX_LENGTH, clean_comment_body
from conftest import BASE_URL

MISSING_ID = 2_000_000_000
CANARY = "CANARY-internal-7f3a9c"


def _incident(client, title="Comment test incident") -> int:
    response = client.post("/incidents", json={"title": title, "description": "Synthetic", "severity": "medium"})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _post(client, incident_id, body="A synthetic comment", visibility="public"):
    return client.post(f"/incidents/{incident_id}/comments", json={"body": body, "visibility": visibility})


def _list(client, incident_id, **params):
    return client.get(f"/incidents/{incident_id}/comments", params=params)


def _count(db, incident_id=None) -> int:
    query = select(func.count()).select_from(IncidentComment)
    if incident_id is not None:
        query = query.where(IncidentComment.incident_id == incident_id)
    return db.scalar(query)


@pytest.fixture
def legacy(db):
    row = Incident(title="Legacy", description="Created before accounts", severity=Severity.LOW)
    db.add(row)
    db.commit()
    return row.id


# ---- Authentication and CSRF -----------------------------------------------------------------

@pytest.mark.parametrize("method", ["get", "post"])
def test_requires_authentication(method, client, login_as, reporter):
    incident_id = _incident(login_as(reporter))
    kwargs = {"json": {"body": "x", "visibility": "public"}} if method == "post" else {}
    response = getattr(client, method)(f"/incidents/{incident_id}/comments", **kwargs)
    assert response.status_code == 401


def test_posting_requires_the_csrf_token(login_as, responder, db):
    client = login_as(responder)
    incident_id = _incident(client)
    del client.headers["X-CSRF-Token"]
    assert _post(client, incident_id).status_code == 403
    client.headers["X-CSRF-Token"] = "not-the-token"
    assert _post(client, incident_id).status_code == 403
    assert _count(db, incident_id) == 0


# ---- Who may read and write what ---------------------------------------------------------------

def test_reporter_reads_and_posts_public_comments_on_own_incident(login_as, reporter):
    client = login_as(reporter)
    incident_id = _incident(client)
    response = _post(client, incident_id, "Is there an update?")
    assert response.status_code == 201
    comment = response.json()
    assert comment["visibility"] == "public"
    assert comment["body"] == "Is there an update?"
    assert comment["author"] == {"id": reporter.id, "email": reporter.email}
    assert set(comment) == {"id", "visibility", "body", "author", "created_at"}
    page = _list(client, incident_id).json()
    assert page["total"] == 1 and page["items"] == [comment]


@pytest.mark.parametrize("role", [UserRole.RESPONDER, UserRole.ADMIN])
def test_staff_post_and_read_both_kinds_on_any_incident(role, make_user, login_as, reporter, legacy):
    staff = login_as(make_user(role))
    for incident_id in (_incident(login_as(reporter)), legacy):
        assert _post(staff, incident_id, "Public reply").status_code == 201
        assert _post(staff, incident_id, "Internal note", "internal").status_code == 201
        page = _list(staff, incident_id).json()
        assert page["total"] == 2
        assert [(c["body"], c["visibility"]) for c in page["items"]] == [("Public reply", "public"), ("Internal note", "internal")]


def test_reporter_cannot_post_internal_notes(login_as, reporter, db):
    client = login_as(reporter)
    incident_id = _incident(client)
    response = _post(client, incident_id, f"{CANARY} forced", "internal")
    assert response.status_code == 403
    assert response.json() == {"detail": comments_module.REPORTER_INTERNAL_FORBIDDEN}
    assert CANARY not in response.text
    assert _count(db, incident_id) == 0


def test_reporter_gets_the_same_404_for_other_legacy_and_missing_incidents(login_as, reporter, other_reporter, legacy, db):
    theirs = _incident(login_as(other_reporter))
    client = login_as(reporter)
    expected = _list(client, MISSING_ID)
    assert expected.status_code == 404
    for incident_id in (theirs, legacy, MISSING_ID):
        for response in (
            _list(client, incident_id),
            _list(client, incident_id, limit=1, offset=5),
            _post(client, incident_id),
            # Visibility is checked before the internal-note rule: still 404, never 403.
            _post(client, incident_id, visibility="internal"),
        ):
            assert (response.status_code, response.json()) == (404, expected.json())
    assert _count(db) == 0


def test_author_and_metadata_cannot_be_supplied(login_as, responder, reporter, db):
    client = login_as(responder)
    incident_id = _incident(client)
    for extra in ({"author_id": reporter.id}, {"created_at": "2020-01-01T00:00:00Z"}, {"id": 1}, {"incident_id": incident_id}):
        response = client.post(f"/incidents/{incident_id}/comments", json={"body": "x", "visibility": "public", **extra})
        assert response.status_code == 422, extra
    assert _count(db, incident_id) == 0


@pytest.mark.parametrize("payload", [{"body": "x"}, {"body": "x", "visibility": "secret"}, {"body": "x", "visibility": None}, {"visibility": "public"}, {"body": 5, "visibility": "public"}])
def test_body_and_visibility_are_required_and_typed(payload, login_as, responder, db):
    client = login_as(responder)
    incident_id = _incident(client)
    assert client.post(f"/incidents/{incident_id}/comments", json=payload).status_code == 422
    assert _count(db, incident_id) == 0


# ---- Internal notes never reach reporters ---------------------------------------------------

def test_reporter_list_and_total_exclude_internal_notes(login_as, reporter, responder):
    reporter_client = login_as(reporter)
    staff = login_as(responder)
    incident_id = _incident(reporter_client)
    for i in range(3):
        _post(staff, incident_id, f"{CANARY} {i}", "internal")
    _post(staff, incident_id, "Public 1")
    _post(reporter_client, incident_id, "Public 2")
    page = _list(reporter_client, incident_id).json()
    assert page["total"] == 2
    assert [c["body"] for c in page["items"]] == ["Public 1", "Public 2"]
    # Every page and offset: the total never counts internal notes and none is ever returned.
    for offset in range(6):
        response = _list(reporter_client, incident_id, limit=1, offset=offset)
        assert response.json()["total"] == 2
        assert CANARY not in response.text
    assert _list(staff, incident_id).json()["total"] == 5


def test_internal_notes_never_reach_a_reporter_through_any_endpoint(login_as, reporter, responder, admin):
    reporter_client = login_as(reporter)
    incident_id = _incident(reporter_client)
    _post(reporter_client, incident_id, "Reporter question")
    for staff in (login_as(responder), login_as(admin)):
        assert _post(staff, incident_id, f"{CANARY} suspected insider", "internal").status_code == 201
        staff.patch(f"/incidents/{incident_id}", json={"status": "investigating"})
    # Sanity check: staff can see the canary, so its absence below is meaningful.
    assert CANARY in _list(login_as(responder), incident_id).text

    paths = [
        "/incidents",
        f"/incidents/{incident_id}",
        f"/incidents/{incident_id}/events",
        f"/incidents/{incident_id}/comments",
        f"/incidents/{incident_id}/comments?limit=100&offset=0",
        f"/incidents/{incident_id}/comments?limit=1&offset=1",
        "/analytics/summary",
        "/exports/incidents.csv",
        "/auth/me",
    ]
    for path in paths:
        response = reporter_client.get(path)
        assert response.status_code == 200, path
        assert CANARY not in response.text, path
        assert "internal" not in response.text.lower(), path

    # Error responses never echo submitted content.
    for response in (
        _post(reporter_client, incident_id, f"{CANARY} forced", "internal"),
        _post(reporter_client, incident_id, CANARY + "x" * COMMENT_MAX_LENGTH),
        _post(reporter_client, incident_id, f"{CANARY}\x00"),
    ):
        assert response.status_code in (403, 422)
        assert CANARY not in response.text


def test_a_regressed_query_fails_closed_instead_of_leaking(login_as, reporter, responder, monkeypatch):
    reporter_client = login_as(reporter)
    incident_id = _incident(reporter_client)
    _post(login_as(responder), incident_id, f"{CANARY} internal", "internal")
    monkeypatch.setattr(comments_module, "_comments_visible_to", lambda query, user: query)
    response = _list(reporter_client, incident_id)
    assert response.status_code == 500
    assert CANARY not in response.text


def test_comments_do_not_change_analytics_or_export(login_as, reporter, responder):
    reporter_client = login_as(reporter)
    staff = login_as(responder)
    incident_id = _incident(reporter_client)

    def snapshot():
        return [(c.get("/analytics/summary").json(), c.get("/exports/incidents.csv").content) for c in (reporter_client, staff)]

    before = snapshot()
    _post(staff, incident_id, f"{CANARY} internal", "internal")
    _post(staff, incident_id, "Public reply")
    _post(reporter_client, incident_id, "Thanks")
    assert snapshot() == before


def test_adding_comments_does_not_touch_the_incident_or_its_history(login_as, reporter, responder, db):
    reporter_client = login_as(reporter)
    incident_id = _incident(reporter_client)
    # Tests share one transaction, so now() would not change: backdate the row to make any touch visible.
    db.execute(text("UPDATE incidents SET updated_at = '2020-01-01T00:00:00Z' WHERE id = :id"), {"id": incident_id})
    db.commit()
    row_version = lambda: db.scalar(text("SELECT ctid::text FROM incidents WHERE id = :id"), {"id": incident_id})  # noqa: E731
    version_before = row_version()
    before = reporter_client.get(f"/incidents/{incident_id}").json()
    assert before["updated_at"].startswith("2020-01-01")
    events_before = db.scalar(select(func.count()).select_from(IncidentEvent).where(IncidentEvent.incident_id == incident_id))
    _post(login_as(responder), incident_id, "Internal", "internal")
    _post(reporter_client, incident_id, "Public")
    assert reporter_client.get(f"/incidents/{incident_id}").json() == before  # includes updated_at
    assert row_version() == version_before  # no UPDATE at all, not even one that changes nothing
    assert db.scalar(select(func.count()).select_from(IncidentEvent).where(IncidentEvent.incident_id == incident_id)) == events_before


# ---- Ordering and pagination ---------------------------------------------------------------

def test_oldest_first_with_id_tie_breaker_and_stable_paging(login_as, responder, db):
    client = login_as(responder)
    incident_id = _incident(client)
    # One transaction: identical created_at values, so the id decides the order.
    rows = [IncidentComment(incident_id=incident_id, author_id=responder.id, visibility=CommentVisibility.PUBLIC, body=f"c{i}") for i in range(5)]
    db.add_all(rows)
    db.commit()
    assert len({r.created_at for r in rows}) == 1
    assert [c["body"] for c in _list(client, incident_id).json()["items"]] == [f"c{i}" for i in range(5)]
    page1 = _list(client, incident_id, limit=2, offset=0).json()
    page2 = _list(client, incident_id, limit=2, offset=2).json()
    assert [c["body"] for c in page1["items"] + page2["items"]] == ["c0", "c1", "c2", "c3"]
    assert (page1["total"], page1["limit"], page1["offset"]) == (5, 2, 0)
    assert _list(client, incident_id, offset=5).json()["items"] == []
    assert _list(client, incident_id).json()["limit"] == 50


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 101}, {"offset": -1}, {"limit": "x"}])
def test_paging_parameters_are_validated(params, login_as, responder):
    client = login_as(responder)
    assert _list(client, _incident(client), **params).status_code == 422


# ---- Append-only -----------------------------------------------------------------------------

def test_no_endpoint_edits_or_deletes_comments(login_as, responder, db):
    client = login_as(responder)
    incident_id = _incident(client)
    comment = _post(client, incident_id, "Keep me").json()
    for method in ("put", "patch", "delete"):
        assert getattr(client, method)(f"/incidents/{incident_id}/comments").status_code == 405
        assert client.request(method.upper(), f"/incidents/{incident_id}/comments/{comment['id']}").status_code in (404, 405)
    assert [c["body"] for c in _list(client, incident_id).json()["items"]] == ["Keep me"]


@pytest.mark.parametrize("statement", [
    "UPDATE incident_comments SET body = 'changed' WHERE id = :id",
    "UPDATE incident_comments SET visibility = 'public' WHERE id = :id",
    "DELETE FROM incident_comments WHERE id = :id",
    "TRUNCATE incident_comments",
])
def test_database_rejects_changes_to_comments(statement, login_as, responder, db):
    client = login_as(responder)
    incident_id = _incident(client)
    comment = _post(client, incident_id, "Original", "internal").json()
    with pytest.raises(DBAPIError, match="append-only"):
        with db.begin_nested():
            db.execute(text(statement), {"id": comment["id"]})
    db.expire_all()
    row = db.get(IncidentComment, comment["id"])
    assert (row.body, row.visibility) == ("Original", CommentVisibility.INTERNAL)


def test_author_and_incident_references_are_preserved(login_as, responder, db):
    client = login_as(responder)
    incident_id = _incident(client)
    _post(client, incident_id)
    with pytest.raises(DBAPIError):
        with db.begin_nested():
            db.execute(text("DELETE FROM users WHERE id = :id"), {"id": responder.id})
    with pytest.raises(DBAPIError):
        with db.begin_nested():
            db.execute(text("DELETE FROM incidents WHERE id = :id"), {"id": incident_id})


def test_visibility_has_no_database_default(responder, db, login_as):
    incident_id = _incident(login_as(responder))
    with pytest.raises(DBAPIError):
        with db.begin_nested():
            db.execute(text("INSERT INTO incident_comments (incident_id, author_id, body) VALUES (:i, :a, 'x')"), {"i": incident_id, "a": responder.id})


# ---- Body validation (API) ---------------------------------------------------------------------

@pytest.mark.parametrize("raw, stored", [
    ("  padded  ", "padded"),
    ("\n\tIndented first line\nsecond\n\n", "Indented first line\nsecond"),
    ("a\r\nb\rc", "a\nb\nc"),
    ("tab\tinside", "tab\tinside"),
    ("\u00a0\u3000\u200b\ufeffword\u2028\u202f", "word"),
    ("caf\u00e9 \u0645\u0631\u062d\u0628\u0627 \u4f60\u597d e\u0301 \U0001f6a8", "caf\u00e9 \u0645\u0631\u062d\u0628\u0627 \u4f60\u597d e\u0301 \U0001f6a8"),
    ("<script>alert(1)</script> & \"quotes\"", "<script>alert(1)</script> & \"quotes\""),
    ("=HYPERLINK(\"x\")", "=HYPERLINK(\"x\")"),
])
def test_bodies_are_normalised_and_otherwise_kept_exactly(raw, stored, login_as, responder):
    client = login_as(responder)
    response = _post(client, _incident(client), raw)
    assert response.status_code == 201, response.text
    assert response.json()["body"] == stored


@pytest.mark.parametrize("body, accepted", [
    ("x" * COMMENT_MAX_LENGTH, True),
    ("x" * (COMMENT_MAX_LENGTH + 1), False),
    ("\U0001f6a8" * COMMENT_MAX_LENGTH, True),  # astral characters count once each, as in PostgreSQL
    ("\U0001f6a8" * (COMMENT_MAX_LENGTH + 1), False),
    (" " * 50 + "x" * COMMENT_MAX_LENGTH + "\r\n" * 50, True),  # measured after trimming
    ("a\r\n" * (COMMENT_MAX_LENGTH // 2), True),  # CRLF counts as one character once normalised
    ("x" * (4 * COMMENT_MAX_LENGTH + 1), False),  # raw-size guard
])
def test_length_limit_counts_code_points_after_normalisation(body, accepted, login_as, responder):
    client = login_as(responder)
    response = _post(client, _incident(client), body)
    assert response.status_code == (201 if accepted else 422)
    if not accepted:
        assert response.json()["detail"][0]["msg"].endswith("Comments can be at most 5,000 characters.")


@pytest.mark.parametrize("body", ["", " ", "\t\n", "\r\n", "\u3000", "\u200b\ufeff", "a\x00b", "\x00", "a\x01b", "a\x0bb", "a\x0cb", "a\x1bb", "a\x7fb", "a\x85b", "a\x9fb"])
def test_blank_and_control_character_bodies_are_rejected(body, login_as, responder, db):
    client = login_as(responder)
    incident_id = _incident(client)
    response = _post(client, incident_id, body)
    assert response.status_code == 422
    assert "input" not in response.json()["detail"][0]
    assert _count(db, incident_id) == 0


def test_lone_surrogates_are_rejected_not_a_server_error(login_as, responder, db):
    client = login_as(responder)
    incident_id = _incident(client)
    raw = '{"body": "half \\ud800 surrogate", "visibility": "internal"}'
    response = client.post(f"/incidents/{incident_id}/comments", content=raw, headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    assert _count(db, incident_id) == 0


# ---- API and PostgreSQL agree ------------------------------------------------------------------

def _api_stores_unchanged(body: str) -> bool:
    try:
        return clean_comment_body(body) == body
    except ValueError:
        return False


def _db_accepts(db, incident_id: int, author_id: int, body: str) -> bool:
    try:
        with db.begin_nested():
            db.execute(
                text("INSERT INTO incident_comments (incident_id, author_id, visibility, body) VALUES (:i, :a, 'public', :b)"),
                {"i": incident_id, "a": author_id, "b": body},
            )
        return True
    except (DBAPIError, UnicodeEncodeError, ValueError):
        return False


AGREEMENT_BODIES = [
    "ok", "", " ", "\t", "\n", "\r", "a\r\nb", "a\rb", "a\nb", "a\tb", " a", "a ", "\ta", "a\n",
    "a\x00b", "\x00", "\x01", "a\x08b", "a\x0bb", "a\x1fb", "a\x7fb", "a\x80b", "a\x85b", "a\x9fb",
    "a\u00a0b", "\u00a0a", "a\u00a0", "\u200b", "a\u200b", "\ufeffa", "a\u2028", "\u3000a", "\u202eRTL",
    "e\u0301", "\U0001f6a8", "x" * 5000, "x" * 5001, "\U0001f6a8" * 5000, "\U0001f6a8" * 5001,
    "half \ud800 surrogate",
]


@pytest.mark.parametrize("body", AGREEMENT_BODIES, ids=lambda b: json.dumps(b[:12]))
def test_database_accepts_exactly_the_bodies_the_api_stores(body, login_as, responder, db):
    incident_id = _incident(login_as(responder))
    assert _db_accepts(db, incident_id, responder.id, body) == _api_stores_unchanged(body)


def test_constraints_agree_with_the_api_for_every_character_position(login_as, responder, db):
    """Each code point alone, leading, trailing and inside a body, checked against the live constraints."""
    definitions = db.scalars(text(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conrelid = 'incident_comments'::regclass AND conname LIKE 'ck_incident_comments_body_%' ORDER BY conname"
    )).all()
    assert len(definitions) == 3
    condition = " AND ".join(d.removeprefix("CHECK ") for d in definitions)
    code_points = [c for c in [*range(1, 0x3100), *range(0xFE00, 0xFF10)] if not 0xD800 <= c <= 0xDFFF]
    bodies = [form.format(c=chr(c)) for c in code_points for form in ("{c}", "{c}a", "a{c}", "a{c}b")]
    db_ok = set(db.scalars(text(f"SELECT body FROM unnest(CAST(:bodies AS text[])) AS t(body) WHERE {condition}"), {"bodies": bodies}).all())
    mismatches = [b for b in bodies if (b in db_ok) != _api_stores_unchanged(b)]
    assert mismatches == []
    # The sets in the API and the constraints really are the ones under test.
    assert all(not _api_stores_unchanged(ch) for ch in COMMENT_BLANK_CHARS)
