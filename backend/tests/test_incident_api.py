import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from app.database import engine, SessionLocal
from app.incidents import get_db
from app.main import app
from app.models import Incident

@pytest.fixture
def client():
    # API commits remain inside an outer transaction, rolled back after the test.
    with engine.connect() as connection:
        transaction = connection.begin()
        def override_db():
            with SessionLocal(bind=connection, join_transaction_mode="create_savepoint") as session:
                yield session
        app.dependency_overrides[get_db] = override_db
        try:
            with TestClient(app) as test_client:
                yield test_client
        finally:
            app.dependency_overrides.clear()
            transaction.rollback()

PAYLOAD = {"title": "Suspicious login", "description": "Synthetic test incident", "severity": "high"}

def test_create_read_update_and_close(client):
    response = client.post("/incidents", json=PAYLOAD)
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["status"] == "open"
    assert created["created_at"] and created["updated_at"]
    incident_id = created["id"]
    assert client.get(f"/incidents/{incident_id}").json()["title"] == PAYLOAD["title"]
    updated = client.patch(f"/incidents/{incident_id}", json={"status": "closed", "assignee_id": 12})
    assert updated.status_code == 200, updated.text
    assert updated.json()["status"] == "closed"
    assert updated.json()["assignee_id"] == 12
    assert client.patch(f"/incidents/{incident_id}", json={"assignee_id": None}).json()["assignee_id"] is None

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
