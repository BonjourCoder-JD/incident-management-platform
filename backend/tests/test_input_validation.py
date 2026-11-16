"""Hostile text in every accepted string input must be refused cleanly, never crash the API (BUG-2).

PostgreSQL text cannot store NUL, so a NUL that reaches the database raises a driver error (HTTP 500).
Every string the API accepts is checked here with NUL and other forbidden control characters.
"""
import json
from urllib.parse import quote

import pytest

from app.models import Incident, Severity

NUL = chr(0)
BELL = chr(7)
ESC = chr(27)
SURROGATE = chr(0xD800)


@pytest.fixture
def staff(login_as, admin):
    return login_as(admin)


@pytest.fixture
def incident_id(db, admin):
    incident = Incident(title="Validation probe", description="Synthetic", severity=Severity.LOW, reporter_id=admin.id)
    db.add(incident)
    db.commit()
    return incident.id


def _send(client, method, path, body=None, params=None):
    """Sends the hostile text the way a real client would: JSON with ASCII escapes (so a lone
    surrogate arrives as a JSON escape) and percent-encoded query strings (surrogates as their raw bytes)."""
    if params:
        encoded = "&".join(
            f"{key}={quote(value.encode('utf-8', 'surrogatepass'), safe='')}" for key, value in params.items())
        path = f"{path}?{encoded}"
    if body is None:
        return client.request(method, path)
    return client.request(method, path, content=json.dumps(body), headers={"Content-Type": "application/json"})


def _requests(incident_id: int, bad: str):
    targets = [{"severity": "low", "response_minutes": 60, "resolution_minutes": 600},
               {"severity": "medium", "response_minutes": 60, "resolution_minutes": 600},
               {"severity": "high", "response_minutes": 30, "resolution_minutes": 300},
               {"severity": "critical", "response_minutes": 15, "resolution_minutes": 120}]
    return [
        ("POST", "/incidents", {"title": f"Title {bad}", "description": "Synthetic", "severity": "low"}, None),
        ("POST", "/incidents", {"title": "Title", "description": f"Text {bad} more", "severity": "low"}, None),
        ("PATCH", f"/incidents/{incident_id}", {"title": f"Changed {bad}"}, None),
        ("PATCH", f"/incidents/{incident_id}", {"description": f"Changed {bad}"}, None),
        ("POST", f"/incidents/{incident_id}/comments", {"body": f"Comment {bad}", "visibility": "internal"}, None),
        ("POST", "/users", {"email": f"probe{bad}@example.com", "password": "a-long-synthetic-passphrase", "role": "reporter"}, None),
        ("POST", "/users", {"email": "probe.nul@example.com", "password": f"a-long-synthetic{bad}passphrase", "role": "reporter"}, None),
        ("PUT", "/auth/password", {"current_password": f"x{bad}", "new_password": "another-long-passphrase"}, None),
        ("PUT", "/auth/password", {"current_password": "correct-horse-battery-staple", "new_password": f"another{bad}long-passphrase"}, None),
        ("POST", "/sla/policy/versions", {"reason": f"Reason {bad}", "targets": targets}, None),
        ("GET", "/incidents", None, {"severity": bad}),
        ("GET", "/incidents", None, {"sla": bad}),
        ("GET", "/analytics/summary", None, {"from": "2026-11-01", "to": "2026-11-10", "tz": f"UTC{bad}"}),
        ("GET", "/analytics/summary", None, {"from": f"2026-11-01{bad}", "to": "2026-11-10", "tz": "UTC"}),
        ("GET", "/exports/incidents.csv", None, {"status": bad}),
    ]


@pytest.mark.parametrize("bad", [NUL, BELL, ESC, SURROGATE], ids=["nul", "bell", "escape", "lone-surrogate"])
def test_forbidden_characters_never_cause_a_server_error(bad, staff, incident_id):
    failures = []
    for method, path, body, params in _requests(incident_id, bad):
        try:
            response = _send(staff, method, path, body, params)
            if response.status_code >= 500:
                failures.append((method, path, list((body or params).keys()), response.status_code))
        except Exception as error:  # the test client re-raises server errors
            failures.append((method, path, list((body or params).keys()), type(error).__name__))
    assert failures == [], " | ".join(map(str, failures))


@pytest.mark.parametrize("bad", [NUL, BELL, ESC, SURROGATE], ids=["nul", "bell", "escape", "lone-surrogate"])
def test_login_with_forbidden_characters_is_refused_cleanly(bad, client):
    failures = []
    for field, payload in (("email", {"email": f"someone{bad}@example.com", "password": "whatever-password"}),
                           ("password", {"email": "someone@example.com", "password": f"pass{bad}word"})):
        try:
            response = _send(client, "POST", "/auth/login", payload)
            if response.status_code not in (401, 422):
                failures.append((field, response.status_code))
        except Exception as error:
            failures.append((field, type(error).__name__))
    assert failures == [], " | ".join(map(str, failures))


@pytest.mark.parametrize("field, value", [
    ("title", "Ransomware" + NUL),
    ("title", "Bell" + BELL),
    ("description", "Line one" + ESC + "line two"),
    ("description", "Lone surrogate " + SURROGATE),
], ids=["title-nul", "title-bell", "description-escape", "description-surrogate"])
def test_incident_text_rejects_control_characters_with_422(field, value, staff, incident_id, db):
    create = {"title": "Valid title", "description": "Valid description", "severity": "low", field: value}
    response = _send(staff, "POST", "/incidents", create)
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", field]
    update = _send(staff, "PATCH", f"/incidents/{incident_id}", {field: value})
    assert update.status_code == 422
    db.expire_all()
    assert db.get(Incident, incident_id).title == "Validation probe"


def test_ordinary_whitespace_is_still_accepted(staff):
    response = staff.post("/incidents", json={"title": "Tab	separated title", "description": "First line\nSecond line\r\nThird", "severity": "low"})
    assert response.status_code == 201, response.text
