"""Incident CRUD behaviour, exercised as a responder (who may use every field).

Role-specific rules are covered in test_authorization.py.
"""
import pytest
from app.models import UserRole

PAYLOAD = {"title": "Suspicious login", "description": "Synthetic test incident", "severity": "high"}


@pytest.fixture
def client(login_as, responder):
    return login_as(responder)


def test_create_read_update_and_close(client, make_user, responder):
    response = client.post("/incidents", json=PAYLOAD)
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["status"] == "open"
    assert created["created_at"] and created["updated_at"]
    assert created["reporter_id"] == responder.id
    assert created["reporter"] == {"id": responder.id, "email": responder.email}
    incident_id = created["id"]
    assert client.get(f"/incidents/{incident_id}").json()["title"] == PAYLOAD["title"]

    teammate = make_user(UserRole.RESPONDER)
    updated = client.patch(f"/incidents/{incident_id}", json={"status": "closed", "assignee_id": teammate.id})
    assert updated.status_code == 200, updated.text
    assert updated.json()["status"] == "closed"
    assert updated.json()["assignee_id"] == teammate.id
    assert updated.json()["assignee"] == {"id": teammate.id, "email": teammate.email}
    cleared = client.patch(f"/incidents/{incident_id}", json={"assignee_id": None}).json()
    assert cleared["assignee_id"] is None
    assert cleared["assignee"] is None

def test_filter_and_pagination(client):
    client.post("/incidents", json=PAYLOAD)
    client.post("/incidents", json={**PAYLOAD, "severity": "low"})
    result = client.get("/incidents?severity=high&limit=1&offset=0")
    assert result.status_code == 200
    data = result.json()
    assert data["total"] >= 1
    assert len(data["items"]) == 1
    assert data["items"][0]["severity"] == "high"
    assert client.get("/incidents?limit=101").status_code == 422
    assert client.get("/incidents?offset=-1").status_code == 422

def test_invalid_inputs_and_not_found(client):
    assert client.post("/incidents", json={**PAYLOAD, "severity": "urgent"}).status_code == 422
    assert client.post("/incidents", json={**PAYLOAD, "title": ""}).status_code == 422
    assert client.post("/incidents", json={**PAYLOAD, "unexpected": 1}).status_code == 422
    assert client.get("/incidents/999999999").status_code == 404
    assert client.patch("/incidents/999999999", json={"status": "closed"}).status_code == 404
    created = client.post("/incidents", json=PAYLOAD).json()
    assert client.patch(f"/incidents/{created['id']}", json={"title": None}).status_code == 422
    assert client.patch(f"/incidents/{created['id']}", json={"status": "invalid"}).status_code == 422

def test_validation_errors_do_not_echo_input(client):
    response = client.post("/incidents", json={**PAYLOAD, "severity": "secret-value"})
    assert response.status_code == 422
    assert "secret-value" not in response.text
    assert response.json()["detail"][0]["loc"] == ["body", "severity"]
