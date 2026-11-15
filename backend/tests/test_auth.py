"""Login, logout, sessions, CSRF, password storage and login abuse protection."""
import hashlib
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import settings
from app.main import app
from app.models import AuthSession, User, UserRole
from app.security import verify_password
from conftest import BASE_URL, TEST_PASSWORD

COOKIE = settings.session_cookie_name


def _login(client, email, password=TEST_PASSWORD):
    return client.post("/auth/login", json={"email": email, "password": password})


# ---- Login and session cookie ---------------------------------------------------

def test_login_sets_hardened_session_cookie_and_returns_user(client, reporter):
    response = _login(client, reporter.email)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["user"] == {
        "id": reporter.id, "email": reporter.email, "role": "reporter", "is_active": True, "created_at": body["user"]["created_at"],
    }
    assert len(body["csrf_token"]) >= 40
    assert "password" not in response.text

    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f"{COOKIE}=")
    for attribute in ("HttpOnly", "Secure", "SameSite=strict", "Path=/"):
        assert attribute.lower() in cookie.lower(), cookie


def test_session_token_is_stored_only_as_a_hash(client, reporter, db):
    _login(client, reporter.email)
    token = client.cookies[COOKIE]
    stored = db.scalars(select(AuthSession).where(AuthSession.user_id == reporter.id)).one()
    assert stored.token_hash == hashlib.sha256(token.encode()).hexdigest()
    assert token not in (stored.token_hash, stored.csrf_token)


def test_email_is_case_insensitive(client, reporter):
    assert _login(client, f"  {reporter.email.upper()} ").status_code == 200


def test_me_requires_authentication(client, reporter):
    assert client.get("/auth/me").status_code == 401
    _login(client, reporter.email)
    me = client.get("/auth/me")
    assert me.status_code == 200
    assert me.json()["user"]["email"] == reporter.email


def _with_cookie(token):
    """A fresh client that sends exactly this session token, like a replayed or stolen cookie."""
    return TestClient(app, base_url=BASE_URL, headers={"Cookie": f"{COOKIE}={token}"})


def test_unknown_or_forged_session_cookie_is_rejected(client, reporter):
    _login(client, reporter.email)
    # Control: replaying the real token works, so the forged request below is a fair test.
    assert _with_cookie(client.cookies[COOKIE]).get("/auth/me").status_code == 200
    assert _with_cookie("forged-token-value").get("/auth/me").status_code == 401


# ---- Failed authentication ----------------------------------------------------

def test_failures_share_one_generic_message(client, make_user):
    active = make_user(UserRole.REPORTER)
    inactive = make_user(UserRole.REPORTER, is_active=False)
    responses = [
        _login(client, active.email, "wrong-password-123"),
        _login(client, "nobody@example.com"),
        _login(client, inactive.email),  # correct password, deactivated account
    ]
    assert [r.status_code for r in responses] == [401, 401, 401]
    assert {r.json()["detail"] for r in responses} == {"Invalid email or password."}
    assert all(COOKIE not in r.headers.get("set-cookie", "") for r in responses)


def test_login_validation_errors_do_not_echo_the_password(client):
    secret = "x" * 1025
    response = client.post("/auth/login", json={"email": "someone@example.com", "password": secret})
    assert response.status_code == 422
    assert secret not in response.text


def test_login_requires_json(client, reporter):
    # A cross-site HTML form cannot send application/json, which blocks login CSRF.
    response = client.post("/auth/login", content=f'{{"email":"{reporter.email}","password":"{TEST_PASSWORD}"}}', headers={"Content-Type": "text/plain"})
    assert response.status_code == 422


# ---- Logout and expiry ----------------------------------------------------------

def test_logout_revokes_the_session_server_side(client, reporter):
    login = _login(client, reporter.email)
    token = client.cookies[COOKIE]
    assert _with_cookie(token).get("/auth/me").status_code == 200
    response = client.post("/auth/logout", headers={"X-CSRF-Token": login.json()["csrf_token"]})
    assert response.status_code == 204
    assert client.get("/auth/me").status_code == 401

    # The server forgot the session: replaying the old cookie does not work.
    assert _with_cookie(token).get("/auth/me").status_code == 401


def test_logout_requires_csrf_token_for_a_live_session(client, reporter):
    _login(client, reporter.email)
    assert client.post("/auth/logout").status_code == 403
    assert client.get("/auth/me").status_code == 200


def test_expired_and_idle_sessions_are_rejected_and_deleted(client, reporter, db):
    _login(client, reporter.email)
    session = db.scalars(select(AuthSession).where(AuthSession.user_id == reporter.id)).one()
    session.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()
    assert client.get("/auth/me").status_code == 401
    assert db.scalars(select(AuthSession).where(AuthSession.user_id == reporter.id)).all() == []

    _login(client, reporter.email)
    session = db.scalars(select(AuthSession).where(AuthSession.user_id == reporter.id)).one()
    session.last_seen_at = datetime.now(timezone.utc) - timedelta(minutes=settings.session_idle_minutes + 1)
    db.commit()
    assert client.get("/auth/me").status_code == 401


def test_sessions_of_deactivated_users_stop_working(login_as, reporter, db):
    user_client = login_as(reporter)
    reporter.is_active = False
    db.commit()
    assert user_client.get("/auth/me").status_code == 401


# ---- CSRF -----------------------------------------------------------------------

def test_state_changing_requests_require_the_csrf_token(client, responder):
    login = _login(client, responder.email)
    payload = {"title": "CSRF probe", "description": "Synthetic", "severity": "low"}
    assert client.post("/incidents", json=payload).status_code == 403
    assert client.post("/incidents", json=payload, headers={"X-CSRF-Token": "wrong"}).status_code == 403
    assert client.get("/incidents").status_code == 200  # safe methods need no token
    ok = client.post("/incidents", json=payload, headers={"X-CSRF-Token": login.json()["csrf_token"]})
    assert ok.status_code == 201


SAFE = {"GET", "HEAD", "OPTIONS"}
# Login is the only exemption: there is no session yet, and it accepts only application/json (see test_login_requires_json).
CSRF_EXEMPT = {("POST", "/auth/login")}


def _state_changing_routes():
    """Every non-safe route the app exposes, read from the app so new endpoints are covered automatically."""
    routes = []
    for path, operations in app.openapi()["paths"].items():
        for method in operations:
            if method.upper() not in SAFE and (method.upper(), path) not in CSRF_EXEMPT:
                routes.append((method.upper(), path))
    return routes


def test_csrf_route_inventory_is_complete():
    # Guards the parametrized test below against silently checking nothing.
    assert set(_state_changing_routes()) == {
        ("POST", "/auth/logout"), ("PUT", "/auth/password"),
        ("POST", "/incidents"), ("PATCH", "/incidents/{incident_id}"),
        ("POST", "/users"), ("PATCH", "/users/{user_id}"),
    }


@pytest.mark.parametrize("method, path", _state_changing_routes())
def test_every_state_changing_route_rejects_missing_or_wrong_csrf(method, path, login_as, admin, db):
    client = login_as(admin)
    url = path.replace("{incident_id}", "1").replace("{user_id}", str(admin.id))
    for headers in ({"X-CSRF-Token": ""}, {"X-CSRF-Token": "wrong-token"}):
        response = client.request(method, url, json={}, headers=headers)
        assert response.status_code == 403, (method, path, headers, response.text)
        assert response.json()["detail"] == "CSRF token missing or invalid."
    # Nothing changed: the session is still alive and the admin is unchanged.
    assert client.get("/auth/me").json()["user"]["role"] == "admin"


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_csrf_check_treats_every_non_safe_method_as_state_changing(method):
    # No DELETE endpoint exists yet; this proves any future one is protected by the same check.
    from fastapi import HTTPException
    from starlette.requests import Request
    from app.auth import _check_csrf

    session = AuthSession(csrf_token="expected-token")
    request = Request({"type": "http", "method": method, "headers": [], "path": "/"})
    with pytest.raises(HTTPException) as excinfo:
        _check_csrf(request, session)
    assert excinfo.value.status_code == 403
    ok = Request({"type": "http", "method": method, "headers": [(b"x-csrf-token", b"expected-token")], "path": "/"})
    _check_csrf(ok, session)  # does not raise
    _check_csrf(Request({"type": "http", "method": "GET", "headers": [], "path": "/"}), session)


# ---- Password storage and changes -----------------------------------------------

def test_passwords_are_stored_as_argon2id_hashes(login_as, admin, db):
    response = login_as(admin).post("/users", json={"email": "new.user@example.com", "password": "a-long-synthetic-passphrase", "role": "reporter"})
    assert response.status_code == 201, response.text
    stored = db.scalar(select(User).where(User.email == "new.user@example.com"))
    assert stored.password_hash.startswith("$argon2id$")
    assert "a-long-synthetic-passphrase" not in stored.password_hash
    assert verify_password(stored.password_hash, "a-long-synthetic-passphrase")


def test_change_password_revokes_other_sessions(login_as, reporter, client):
    laptop = login_as(reporter)
    phone = login_as(reporter)
    wrong = laptop.put("/auth/password", json={"current_password": "not-my-password", "new_password": "another-long-passphrase"})
    assert wrong.status_code == 422
    assert wrong.json()["detail"][0]["loc"] == ["body", "current_password"]
    too_short = laptop.put("/auth/password", json={"current_password": TEST_PASSWORD, "new_password": "short"})
    assert too_short.status_code == 422

    assert laptop.put("/auth/password", json={"current_password": TEST_PASSWORD, "new_password": "another-long-passphrase"}).status_code == 204
    assert laptop.get("/auth/me").status_code == 200
    assert phone.get("/auth/me").status_code == 401
    assert _login(client, reporter.email).status_code == 401
    assert _login(client, reporter.email, "another-long-passphrase").status_code == 200


# ---- Login abuse protection ------------------------------------------------------

def test_repeated_failures_are_rate_limited_per_email(client, reporter):
    for _ in range(settings.login_max_failures_per_email):
        assert _login(client, reporter.email, "wrong-password-123").status_code == 401
    limited = _login(client, reporter.email)  # even the correct password is refused now
    assert limited.status_code == 429
    assert int(limited.headers["Retry-After"]) > 0
    # Other accounts are unaffected by one account's limit.
    assert _login(client, "someone.else@example.com", "wrong-password-123").status_code == 401


def test_successful_login_clears_earlier_failures(client, reporter):
    for _ in range(settings.login_max_failures_per_email - 1):
        _login(client, reporter.email, "wrong-password-123")
    assert _login(client, reporter.email).status_code == 200
    for _ in range(settings.login_max_failures_per_email - 1):
        assert _login(client, reporter.email, "wrong-password-123").status_code == 401
