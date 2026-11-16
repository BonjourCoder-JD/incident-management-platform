"""Administrative comment redaction (Milestone 6 Phase B, ISSUE-18).

CANARY marks the redacted text; it must never come back from any reader endpoint, for any role.
The original comment row stays in the database unchanged, as evidence.
"""
import logging

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.models import CommentRedaction, IncidentComment

CANARY = "CANARY-redact-4e1b"
REASON = "Contained a synthetic phone number"


@pytest.fixture
def thread(login_as, reporter, responder, admin):
    """A reporter's incident with a public comment and an internal note, both containing CANARY."""
    reporter_client, responder_client, admin_client = login_as(reporter), login_as(responder), login_as(admin)
    incident_id = reporter_client.post("/incidents", json={"title": "Redaction probe", "description": "Synthetic", "severity": "low"}).json()["id"]
    public = reporter_client.post(f"/incidents/{incident_id}/comments", json={"body": f"Call me on {CANARY}", "visibility": "public"}).json()
    internal = responder_client.post(f"/incidents/{incident_id}/comments", json={"body": f"Internal {CANARY}", "visibility": "internal"}).json()
    kept = reporter_client.post(f"/incidents/{incident_id}/comments", json={"body": "Unrelated follow-up", "visibility": "public"}).json()
    return {"reporter": reporter_client, "responder": responder_client, "admin": admin_client,
            "incident_id": incident_id, "public": public["id"], "internal": internal["id"], "kept": kept["id"]}


def _redact(client, incident_id, comment_id, reason=REASON):
    return client.post(f"/incidents/{incident_id}/comments/{comment_id}/redaction", json={"reason": reason})


def test_admin_redacts_and_every_reader_withholds_the_text(thread, db):
    incident_id = thread["incident_id"]
    for comment_id in (thread["public"], thread["internal"]):
        response = _redact(thread["admin"], incident_id, comment_id)
        assert response.status_code == 201, response.text
        assert response.json()["body"] is None and response.json()["redacted"] is True
        assert CANARY not in response.text

    for role in ("reporter", "responder", "admin"):
        client = thread[role]
        items = client.get(f"/incidents/{incident_id}/comments").json()["items"]
        by_id = {item["id"]: item for item in items}
        assert by_id[thread["public"]]["body"] is None and by_id[thread["public"]]["redacted"] is True
        assert by_id[thread["kept"]]["body"] == "Unrelated follow-up"  # others are untouched
        expected_reason = None if role == "reporter" else REASON
        assert by_id[thread["public"]]["redaction_reason"] == expected_reason
        # Every reader endpoint the role can use.
        for path in (f"/incidents/{incident_id}/comments", f"/incidents/{incident_id}/comments?limit=1&offset=0",
                     f"/incidents/{incident_id}", f"/incidents/{incident_id}/events", f"/incidents/{incident_id}/sla",
                     "/incidents", "/exports/incidents.csv",
                     "/analytics/summary?from=2020-01-01&to=2020-12-31&tz=UTC"):
            response = client.get(path)
            assert response.status_code in (200, 422), (role, path, response.status_code)
            assert CANARY not in response.text, (role, path)
    for path in (f"/incidents/{incident_id}/triage-suggestions",):
        assert CANARY not in thread["responder"].get(path).text

    # The original rows are unchanged evidence in the database.
    db.expire_all()
    stored = db.get(IncidentComment, thread["public"])
    assert CANARY in stored.body
    redaction = db.scalars(select(CommentRedaction).where(CommentRedaction.comment_id == thread["public"])).one()
    assert redaction.reason == REASON


@pytest.mark.parametrize("role", ["reporter", "responder"])
def test_only_admins_can_redact(role, thread, db):
    assert _redact(thread[role], thread["incident_id"], thread["public"]).status_code == 403
    assert db.scalar(select(CommentRedaction.id)) is None


def test_redaction_requires_the_csrf_token(thread):
    response = thread["admin"].post(f"/incidents/{thread['incident_id']}/comments/{thread['public']}/redaction",
                                    json={"reason": REASON}, headers={"X-CSRF-Token": "wrong"})
    assert response.status_code == 403


def test_missing_or_mismatched_comments_are_404(thread, login_as, admin):
    admin_client = thread["admin"]
    other = admin_client.post("/incidents", json={"title": "Other", "description": "Synthetic", "severity": "low"}).json()["id"]
    assert _redact(admin_client, other, thread["public"]).status_code == 404  # comment belongs to another incident
    assert _redact(admin_client, thread["incident_id"], 2_000_000_000).status_code == 404
    assert _redact(admin_client, 2_000_000_000, thread["public"]).status_code == 404


def test_a_comment_is_redacted_once(thread):
    assert _redact(thread["admin"], thread["incident_id"], thread["public"]).status_code == 201
    again = _redact(thread["admin"], thread["incident_id"], thread["public"], "Second reason")
    assert (again.status_code, again.json()["detail"]) == (409, "This comment has already been removed.")


@pytest.mark.parametrize("reason", ["", "   ", "x" * 501, "bell" + chr(7), "nul" + chr(0)])
def test_the_reason_is_validated(reason, thread):
    assert _redact(thread["admin"], thread["incident_id"], thread["public"], reason).status_code == 422


def test_the_reason_is_trimmed(thread):
    response = _redact(thread["admin"], thread["incident_id"], thread["public"], "  Personal data  ")
    assert response.json()["redaction_reason"] == "Personal data"


def test_logs_contain_identifiers_only(thread, caplog):
    with caplog.at_level(logging.INFO):
        _redact(thread["admin"], thread["incident_id"], thread["public"])
    assert f"Comment {thread['public']} on incident {thread['incident_id']} redacted" in caplog.text
    assert CANARY not in caplog.text and REASON not in caplog.text


@pytest.mark.parametrize("statement", [
    "UPDATE comment_redactions SET reason = 'changed'",
    "DELETE FROM comment_redactions",
    "TRUNCATE comment_redactions",
])
def test_redactions_are_append_only(statement, thread, db):
    _redact(thread["admin"], thread["incident_id"], thread["public"])
    with pytest.raises(DBAPIError, match="append-only"):
        with db.begin_nested():
            db.execute(text(statement))


def test_the_database_refuses_a_second_redaction_or_a_bad_reason(thread, admin, db):
    _redact(thread["admin"], thread["incident_id"], thread["public"])
    for values in ({"c": thread["public"], "reason": "Again"}, {"c": thread["kept"], "reason": ""}):
        with pytest.raises(IntegrityError):
            with db.begin_nested():
                db.execute(text("INSERT INTO comment_redactions (comment_id, redacted_by, reason) VALUES (:c, :u, :reason)"),
                           {**values, "u": admin.id})


# ---- Concurrency (real transactions) ----------------------------------------------------------------

def test_concurrent_redactions_of_one_comment_record_exactly_one():
    """Both requests pass the "already removed" check, then commit at the same moment: the database's
    unique constraint decides. One 201, one controlled 409, never a server error, one record."""
    import secrets
    import threading

    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session, sessionmaker

    from app.database import engine, get_db
    from app.main import app
    from app.security import hash_password
    from conftest import BASE_URL

    tag = secrets.token_hex(4)
    with engine.begin() as conn:
        admin_id = conn.execute(text("INSERT INTO users (email, password_hash, role) VALUES (:e, :h, 'admin') RETURNING id"),
                                {"e": f"race.admin.{tag}@example.com", "h": hash_password("race-test-passphrase")}).scalar_one()
        incident_id = conn.execute(text("INSERT INTO incidents (title, description, severity, reporter_id) "
                                        "VALUES ('Race probe', 'Synthetic', 'low', :u) RETURNING id"), {"u": admin_id}).scalar_one()
        comment_id = conn.execute(text("INSERT INTO incident_comments (incident_id, author_id, body, visibility) "
                                       "VALUES (:i, :u, :b, 'public') RETURNING id"), {"i": incident_id, "u": admin_id, "b": f"Text {CANARY}"}).scalar_one()
    app.dependency_overrides.clear()  # real, committing sessions
    try:
        clients = []
        for _ in range(2):
            client = TestClient(app, base_url=BASE_URL)
            login = client.post("/auth/login", json={"email": f"race.admin.{tag}@example.com", "password": "race-test-passphrase"})
            assert login.status_code == 200, login.text
            client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
            clients.append(client)

        barrier = threading.Barrier(2)

        class RacingSession(Session):
            def commit(self):
                if any(isinstance(obj, CommentRedaction) for obj in self.new):
                    barrier.wait(10)  # both have passed the duplicate check; commit together
                super().commit()

        racing = sessionmaker(bind=engine, class_=RacingSession, autoflush=False, expire_on_commit=False)

        def racing_db():
            with racing() as session:
                yield session
        app.dependency_overrides[get_db] = racing_db

        results = []

        def redact(client, reason):
            response = client.post(f"/incidents/{incident_id}/comments/{comment_id}/redaction", json={"reason": reason})
            results.append((response.status_code, response.json()))

        threads = [threading.Thread(target=redact, args=(client, f"Reason {i}")) for i, client in enumerate(clients)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        assert sorted(code for code, _ in results) == [201, 409]
        conflict = next(body for code, body in results if code == 409)
        assert conflict == {"detail": "This comment has already been removed."}
        with engine.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM comment_redactions WHERE comment_id = :c"), {"c": comment_id}).scalar_one() == 1
    finally:
        app.dependency_overrides.clear()
        with engine.begin() as conn:
            conn.execute(text("SET LOCAL session_replication_role = replica"))
            conn.execute(text("DELETE FROM comment_redactions WHERE comment_id = :c"), {"c": comment_id})
            conn.execute(text("DELETE FROM incident_comments WHERE id = :c"), {"c": comment_id})
            conn.execute(text("DELETE FROM incidents WHERE id = :i"), {"i": incident_id})
            conn.execute(text("DELETE FROM auth_sessions WHERE user_id = :u"), {"u": admin_id})
            conn.execute(text("DELETE FROM rate_limit_events"))
            conn.execute(text("DELETE FROM users WHERE id = :u"), {"u": admin_id})
