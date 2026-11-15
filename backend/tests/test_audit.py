"""Incident audit trail: recording, visibility, transactions, immutability and concurrency."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select, text
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.orm import Session

from app import audit, incidents
from app.database import engine
from app.main import app
from app.models import Incident, IncidentEvent, IncidentStatus, Severity, User, UserRole
from conftest import BASE_URL, TEST_PASSWORD

PAYLOAD = {"title": "Phishing email", "description": "Synthetic report", "severity": "medium"}
SECRET_WORDS = ("password", "hash", "token", "csrf", "session", "cookie")


def _events(db, incident_id):
    db.expire_all()
    return db.scalars(select(IncidentEvent).where(IncidentEvent.incident_id == incident_id).order_by(IncidentEvent.id)).all()


def _create(client, **overrides):
    response = client.post("/incidents", json={**PAYLOAD, **overrides})
    assert response.status_code == 201, response.text
    return response.json()


# ---- What is recorded ---------------------------------------------------------------

def test_create_records_one_created_event_with_the_session_actor(login_as, reporter, db):
    incident = _create(login_as(reporter))
    [created] = _events(db, incident["id"])
    assert created.action.value == "created"
    assert created.actor_id == reporter.id
    assert created.created_at is not None
    assert created.changes == {
        "title": {"from": None, "to": "Phishing email"},
        "description": {"from": None, "to": "Synthetic report"},
        "severity": {"from": None, "to": "medium"},
        "status": {"from": None, "to": "open"},
    }


def test_responder_create_with_assignee_records_the_assignee(login_as, responder, db):
    incident = _create(login_as(responder), assignee_id=responder.id)
    [created] = _events(db, incident["id"])
    assert created.changes["assignee_id"] == {"from": None, "to": responder.id}


@pytest.mark.parametrize("change, expected_action", [
    ({"status": "investigating"}, "status_changed"),
    ("ASSIGN", "assignment_changed"),
    ({"title": "Phishing email (confirmed)"}, "updated"),
    ({"title": "Phishing email (confirmed)", "severity": "high"}, "updated"),
    ("STATUS_AND_ASSIGN", "updated"),
])
def test_each_update_records_exactly_one_correctly_classified_event(change, expected_action, login_as, responder, db):
    client = login_as(responder)
    incident = _create(client)
    if change == "ASSIGN":
        change = {"assignee_id": responder.id}
    elif change == "STATUS_AND_ASSIGN":
        change = {"status": "resolved", "assignee_id": responder.id}
    assert client.patch(f"/incidents/{incident['id']}", json=change).status_code == 200

    events = _events(db, incident["id"])
    assert [e.action.value for e in events] == ["created", expected_action]
    latest = events[-1]
    assert latest.actor_id == responder.id
    before = {"title": "Phishing email", "severity": "medium", "status": "open", "assignee_id": None}
    assert latest.changes == {field: {"from": before[field], "to": value} for field, value in change.items()}


def test_full_description_is_kept_before_and_after(login_as, reporter, db):
    client = login_as(reporter)
    long_before = "Line one\n" + "x" * 5000
    incident = _create(client, description=long_before)
    client.patch(f"/incidents/{incident['id']}", json={"description": "Replaced"})
    latest = _events(db, incident["id"])[-1]
    assert latest.changes == {"description": {"from": long_before, "to": "Replaced"}}


def test_noop_update_records_nothing_and_does_not_touch_the_row(login_as, responder, db):
    client = login_as(responder)
    incident = _create(client)
    response = client.patch(f"/incidents/{incident['id']}", json={"title": "Phishing email", "status": "open", "assignee_id": None})
    assert response.status_code == 200
    assert response.json()["updated_at"] == incident["updated_at"]
    assert [e.action.value for e in _events(db, incident["id"])] == ["created"]


def test_unassign_records_previous_assignee(login_as, responder, db):
    client = login_as(responder)
    incident = _create(client, assignee_id=responder.id)
    client.patch(f"/incidents/{incident['id']}", json={"assignee_id": None})
    latest = _events(db, incident["id"])[-1]
    assert latest.action.value == "assignment_changed"
    assert latest.changes == {"assignee_id": {"from": responder.id, "to": None}}


def test_actor_is_whoever_made_the_change_not_the_reporter(login_as, reporter, responder, admin, db):
    incident = _create(login_as(reporter))
    login_as(responder).patch(f"/incidents/{incident['id']}", json={"status": "investigating"})
    login_as(admin).patch(f"/incidents/{incident['id']}", json={"severity": "critical"})
    login_as(reporter).patch(f"/incidents/{incident['id']}", json={"title": "Phishing email (more detail)"})
    assert [(e.action.value, e.actor_id) for e in _events(db, incident["id"])] == [
        ("created", reporter.id),
        ("status_changed", responder.id),
        ("updated", admin.id),
        ("updated", reporter.id),
    ]


def test_client_cannot_supply_actor_or_audit_values(login_as, reporter, responder, db):
    client = login_as(reporter)
    for extra in ({"actor_id": responder.id}, {"changes": {"title": {"from": "a", "to": "b"}}}, {"action": "created"}):
        assert client.post("/incidents", json={**PAYLOAD, **extra}).status_code == 422
    incident = _create(client)
    assert client.patch(f"/incidents/{incident['id']}", json={"title": "New", "actor_id": responder.id}).status_code == 422
    assert all(e.actor_id == reporter.id for e in _events(db, incident["id"]))


def test_audit_records_only_allowlisted_fields(login_as, responder, db):
    client = login_as(responder)
    incident = _create(client)
    client.patch(f"/incidents/{incident['id']}", json={"title": "T2", "description": "D2", "severity": "low", "status": "closed", "assignee_id": responder.id})
    for recorded in _events(db, incident["id"]):
        assert set(recorded.changes) <= set(audit.AUDITED_FIELDS)
        assert not any(word in str(recorded.changes).lower() for word in SECRET_WORDS)
    stub = Incident(id=1)
    with pytest.raises(ValueError, match="non-allowlisted"):
        audit.record_incident_event(db, stub, responder, audit.IncidentEventAction.UPDATED, {"password_hash": {"from": "a", "to": "b"}})


# ---- Reading history: visibility, pagination ----------------------------------------------

def test_history_visibility_by_role(login_as, make_user, reporter, other_reporter, responder, db, client):
    mine = _create(login_as(reporter))
    theirs = _create(login_as(other_reporter))
    legacy = Incident(title="Legacy", description="Before accounts", severity=Severity.LOW)
    db.add(legacy)
    db.commit()

    reporter_client = login_as(reporter)
    assert reporter_client.get(f"/incidents/{mine['id']}/events").status_code == 200
    not_found = reporter_client.get("/incidents/999999999/events").json()
    for hidden in (theirs["id"], legacy.id):
        response = reporter_client.get(f"/incidents/{hidden}/events")
        assert response.status_code == 404
        assert response.json() == not_found  # indistinguishable from a missing incident
        assert "Synthetic" not in response.text

    for role in (UserRole.RESPONDER, UserRole.ADMIN):
        staff = login_as(make_user(role))
        assert staff.get(f"/incidents/{theirs['id']}/events").json()["total"] == 1
        legacy_history = staff.get(f"/incidents/{legacy.id}/events")
        assert legacy_history.status_code == 200 and legacy_history.json()["total"] == 0  # no backfill

    assert client.get(f"/incidents/{mine['id']}/events").status_code == 401


def test_history_is_paginated_newest_first_with_actor_and_users(login_as, responder, make_user, db):
    teammate = make_user(UserRole.RESPONDER)
    client = login_as(responder)
    incident = _create(client)
    client.patch(f"/incidents/{incident['id']}", json={"status": "investigating"})
    client.patch(f"/incidents/{incident['id']}", json={"assignee_id": teammate.id})
    client.patch(f"/incidents/{incident['id']}", json={"assignee_id": responder.id})

    first = client.get(f"/incidents/{incident['id']}/events?limit=2&offset=0").json()
    assert first["total"] == 4 and first["limit"] == 2 and first["offset"] == 0
    assert [e["action"] for e in first["items"]] == ["assignment_changed", "assignment_changed"]
    assert first["items"][0]["changes"] == {"assignee_id": {"from": teammate.id, "to": responder.id}}
    assert first["items"][0]["actor"] == {"id": responder.id, "email": responder.email}
    assert {u["id"]: u["email"] for u in first["users"]} == {teammate.id: teammate.email, responder.id: responder.email}
    second = client.get(f"/incidents/{incident['id']}/events?limit=2&offset=2").json()
    assert [e["action"] for e in second["items"]] == ["status_changed", "created"]
    assert client.get(f"/incidents/{incident['id']}/events?limit=0").status_code == 422
    assert client.get(f"/incidents/{incident['id']}/events?limit=101").status_code == 422


def test_history_has_no_write_endpoints(login_as, responder):
    client = login_as(responder)
    incident = _create(client)
    url = f"/incidents/{incident['id']}/events"
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        assert client.request(method, url, json={}).status_code == 405


# ---- One transaction for the change and its event ---------------------------------------------

@pytest.fixture
def failing_client(responder):
    """Signed in as a responder; server errors become 500 responses instead of test exceptions."""
    test_client = TestClient(app, base_url=BASE_URL, raise_server_exceptions=False)
    login = test_client.post("/auth/login", json={"email": responder.email, "password": TEST_PASSWORD})
    test_client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
    yield test_client
    test_client.close()


def _broken_event_writer(db, incident, actor, action, changes):
    # The event INSERT violates NOT NULL (action), so it fails at commit.
    db.add(IncidentEvent(incident_id=incident.id, actor_id=actor.id, action=None, changes=changes))


def test_failed_event_write_rolls_back_the_update(failing_client, monkeypatch, db):
    incident = _create(failing_client)
    monkeypatch.setattr(audit, "record_incident_event", _broken_event_writer)
    response = failing_client.patch(f"/incidents/{incident['id']}", json={"title": "Should not persist", "status": "closed"})
    assert response.status_code == 500
    db.expire_all()
    saved = db.get(Incident, incident["id"])
    assert saved.title == "Phishing email" and saved.status == IncidentStatus.OPEN
    assert [e.action.value for e in _events(db, incident["id"])] == ["created"]


def test_failed_event_write_rolls_back_the_creation(failing_client, monkeypatch, db):
    db.expire_all()
    before = db.scalar(select(func.count()).select_from(Incident))
    monkeypatch.setattr(audit, "record_incident_event", _broken_event_writer)
    assert failing_client.post("/incidents", json={**PAYLOAD, "title": "Ghost incident"}).status_code == 500
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(Incident)) == before
    assert db.scalar(select(Incident).where(Incident.title == "Ghost incident")) is None


def test_failed_incident_write_rolls_back_the_event(failing_client, monkeypatch, db):
    incident = _create(failing_client)

    def overflow_title(target, changes):
        target.title = "x" * 300  # exceeds varchar(200): the incident UPDATE fails at commit

    monkeypatch.setattr(incidents, "_apply_changes", overflow_title)
    response = failing_client.patch(f"/incidents/{incident['id']}", json={"title": "Anything"})
    assert response.status_code == 500
    db.expire_all()
    assert db.get(Incident, incident["id"]).title == "Phishing email"
    assert [e.action.value for e in _events(db, incident["id"])] == ["created"]


# ---- Immutability -----------------------------------------------------------------------------

@pytest.mark.parametrize("statement", [
    "UPDATE incident_events SET action = 'updated' WHERE id = :id",
    "UPDATE incident_events SET actor_id = actor_id WHERE id = :id",
    "DELETE FROM incident_events WHERE id = :id",
    "TRUNCATE incident_events",
])
def test_database_rejects_changes_to_audit_events(statement, login_as, responder, db):
    incident = _create(login_as(responder))
    [created] = _events(db, incident["id"])
    with pytest.raises(DBAPIError, match="append-only"):
        with db.begin_nested():
            db.execute(text(statement), {"id": created.id})
    db.expire_all()
    assert _events(db, incident["id"])[0].action.value == "created"


def test_actor_and_incident_references_are_preserved(login_as, responder, db):
    incident = _create(login_as(responder))
    with pytest.raises(DBAPIError):
        with db.begin_nested():
            db.execute(text("DELETE FROM users WHERE id = :id"), {"id": responder.id})
    with pytest.raises(DBAPIError):
        with db.begin_nested():
            db.execute(text("DELETE FROM incidents WHERE id = :id"), {"id": incident["id"]})


# ---- Concurrency ---------------------------------------------------------------------------------

def test_update_locks_the_incident_row(login_as, responder, connection):
    client = login_as(responder)
    incident = _create(client)
    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(connection, "before_cursor_execute", capture)
    try:
        client.patch(f"/incidents/{incident['id']}", json={"status": "investigating"})
    finally:
        event.remove(connection, "before_cursor_execute", capture)
    assert any("FROM incidents" in s and "FOR UPDATE OF incidents" in s for s in statements)


def test_concurrent_writers_are_serialised_by_the_row_lock():
    """Two real transactions: while one holds the incident lock, another cannot read it for update."""
    staff = User(id=0, email="lock.probe@example.com", password_hash="unused", role=UserRole.RESPONDER)
    with engine.begin() as setup:
        incident_id = setup.execute(
            text("INSERT INTO incidents (title, description, severity) VALUES ('Lock probe', 'Synthetic', 'low') RETURNING id")
        ).scalar_one()
    try:
        with engine.connect() as first, engine.connect() as second:
            first_tx, second_tx = first.begin(), second.begin()
            try:
                holder = incidents._get_visible(Session(bind=first), incident_id, staff, for_update=True)
                assert holder.title == "Lock probe"
                second.execute(text("SET LOCAL lock_timeout = '300ms'"))
                waiting = Session(bind=second)
                # Plain reads are not blocked...
                assert incidents._get_visible(waiting, incident_id, staff).id == incident_id
                # ...but a second writer must wait for the first to commit or roll back.
                with pytest.raises(OperationalError, match="lock"):
                    incidents._get_visible(waiting, incident_id, staff, for_update=True)
            finally:
                second_tx.rollback()
                first_tx.rollback()
    finally:
        with engine.begin() as cleanup:  # no events reference this row, so it can be removed
            cleanup.execute(text("DELETE FROM incidents WHERE id = :id"), {"id": incident_id})
