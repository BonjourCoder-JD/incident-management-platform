"""Role-based access control on incident endpoints, plus the BUG-1 regression."""
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.models import Incident, Severity, UserRole
from app.schemas import IncidentCreate, IncidentUpdate
from conftest import BASE_URL

PAYLOAD = {"title": "Phishing email", "description": "Synthetic report", "severity": "medium"}
INT32_MAX = 2_147_483_647


def _create(client, **overrides):
    response = client.post("/incidents", json={**PAYLOAD, **overrides})
    assert response.status_code == 201, response.text
    return response.json()


# ---- Unauthenticated --------------------------------------------------------------

@pytest.mark.parametrize("method, path", [
    ("GET", "/incidents"),
    ("GET", "/incidents/1"),
    ("POST", "/incidents"),
    ("PATCH", "/incidents/1"),
    ("GET", "/users"),
    ("GET", "/users/assignees"),
])
def test_incident_and_user_endpoints_require_authentication(method, path):
    response = TestClient(app, base_url=BASE_URL).request(method, path, json=PAYLOAD if method != "GET" else None)
    assert response.status_code == 401
    assert "Synthetic" not in response.text


def test_health_stays_public(client):
    assert client.get("/health").status_code == 200


# ---- Reporter ----------------------------------------------------------------------

def test_reporter_creates_incidents_owned_by_themselves(login_as, reporter, other_reporter):
    client = login_as(reporter)
    created = _create(client)
    assert created["reporter_id"] == reporter.id
    # Ownership can never be supplied by the client.
    spoof = client.post("/incidents", json={**PAYLOAD, "reporter_id": other_reporter.id})
    assert spoof.status_code == 422


def test_reporter_sees_only_their_own_incidents(login_as, reporter, other_reporter, responder, db):
    mine = _create(login_as(reporter), title="Mine")
    theirs = _create(login_as(other_reporter), title="Theirs")
    staff = _create(login_as(responder), title="Staff")
    legacy = Incident(title="Legacy", description="Created before accounts", severity=Severity.LOW)
    db.add(legacy)
    db.commit()

    client = login_as(reporter)
    listing = client.get("/incidents").json()
    assert [item["id"] for item in listing["items"]] == [mine["id"]]
    assert listing["total"] == 1
    assert client.get("/incidents?severity=medium").json()["total"] == 1

    # Other people's incidents look exactly like missing ones.
    for incident_id in (theirs["id"], staff["id"], legacy.id):
        assert client.get(f"/incidents/{incident_id}").status_code == 404
        assert client.patch(f"/incidents/{incident_id}", json={"title": "Hijacked"}).status_code == 404
    assert client.get("/incidents/999999999").json() == client.get(f"/incidents/{theirs['id']}").json()


def test_reporter_edits_own_details_but_not_status_or_assignment(login_as, reporter, responder):
    client = login_as(reporter)
    incident = _create(client)
    edited = client.patch(f"/incidents/{incident['id']}", json={"title": "Phishing email (updated)", "description": "More detail", "severity": "high"})
    assert edited.status_code == 200, edited.text
    assert edited.json()["severity"] == "high"

    for change in ({"status": "resolved"}, {"status": "open"}, {"assignee_id": responder.id}, {"assignee_id": None}):
        response = client.patch(f"/incidents/{incident['id']}", json=change)
        assert response.status_code == 403, change
    assert client.post("/incidents", json={**PAYLOAD, "assignee_id": responder.id}).status_code == 403
    after = client.get(f"/incidents/{incident['id']}").json()
    assert after["status"] == "open" and after["assignee_id"] is None


# ---- Responder and admin -----------------------------------------------------------------

@pytest.mark.parametrize("role", [UserRole.RESPONDER, UserRole.ADMIN])
def test_staff_see_all_incidents_and_can_triage(role, make_user, login_as, reporter, responder, db):
    reported = _create(login_as(reporter))
    legacy = Incident(title="Legacy", description="Created before accounts", severity=Severity.LOW)
    db.add(legacy)
    db.commit()

    client = login_as(make_user(role))
    ids = {item["id"] for item in client.get("/incidents?limit=100").json()["items"]}
    assert {reported["id"], legacy.id} <= ids

    triaged = client.patch(f"/incidents/{reported['id']}", json={"status": "investigating", "assignee_id": responder.id, "title": "Triaged"})
    assert triaged.status_code == 200, triaged.text
    assert triaged.json()["status"] == "investigating"
    assert triaged.json()["assignee"]["id"] == responder.id
    # The original reporter still owns it.
    assert triaged.json()["reporter_id"] == reporter.id
    assert client.patch(f"/incidents/{legacy.id}", json={"status": "closed"}).status_code == 200

    # Reporter can see the triage outcome on their own incident.
    assert login_as(reporter).get(f"/incidents/{reported['id']}").json()["status"] == "investigating"


def test_assignee_must_be_an_active_responder_or_admin(login_as, make_user, responder, reporter, admin):
    client = login_as(responder)
    incident = _create(client)
    inactive = make_user(UserRole.RESPONDER, is_active=False)
    for bad_id in (reporter.id, inactive.id, 999_999_999):
        response = client.patch(f"/incidents/{incident['id']}", json={"assignee_id": bad_id})
        assert response.status_code == 422, bad_id
        assert response.json()["detail"][0]["loc"] == ["body", "assignee_id"]
        assert client.post("/incidents", json={**PAYLOAD, "assignee_id": bad_id}).status_code == 422
    assert client.patch(f"/incidents/{incident['id']}", json={"assignee_id": admin.id}).status_code == 200


# ---- BUG-1: assignee_id above the PostgreSQL INTEGER range -------------------------------

def test_bug1_assignee_id_above_int32_returns_422_on_post_and_patch(login_as, responder):
    client = login_as(responder)
    incident = _create(client)
    post = client.post("/incidents", json={**PAYLOAD, "assignee_id": INT32_MAX + 1})
    patch = client.patch(f"/incidents/{incident['id']}", json={"assignee_id": INT32_MAX + 1})
    for response in (post, patch):
        assert response.status_code == 422, response.text
        assert response.json()["detail"][0]["loc"] == ["body", "assignee_id"]
        assert response.json()["detail"][0]["type"] == "less_than_equal"


def test_bug1_int32_max_passes_schema_validation():
    assert IncidentCreate(**PAYLOAD, assignee_id=INT32_MAX).assignee_id == INT32_MAX
    assert IncidentUpdate(assignee_id=INT32_MAX).assignee_id == INT32_MAX
    with pytest.raises(ValidationError):
        IncidentCreate(**PAYLOAD, assignee_id=INT32_MAX + 1)
    with pytest.raises(ValidationError):
        IncidentUpdate(assignee_id=INT32_MAX + 1)


def test_bug1_int32_max_reaches_the_assignee_check_not_a_server_error(login_as, responder):
    # No user has this ID, so the request fails the assignee rule (422) rather than the schema or the database.
    client = login_as(responder)
    response = client.post("/incidents", json={**PAYLOAD, "assignee_id": INT32_MAX})
    assert response.status_code == 422
    assert response.json()["detail"][0]["msg"] == "Assignee must be an active responder or admin."
