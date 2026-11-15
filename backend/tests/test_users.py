"""User and role management: admin only, no self-escalation, sessions revoked on changes."""
import pytest

from app.models import UserRole
from conftest import TEST_PASSWORD

NEW_USER = {"email": "Responder.New@Example.com", "password": "a-long-synthetic-passphrase", "role": "responder"}


def test_admin_creates_lists_and_updates_users(login_as, admin):
    client = login_as(admin)
    created = client.post("/users", json=NEW_USER)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["email"] == "responder.new@example.com"
    assert body["role"] == "responder" and body["is_active"] is True
    assert "password" not in created.text

    emails = [user["email"] for user in client.get("/users").json()]
    assert "responder.new@example.com" in emails and admin.email in emails

    updated = client.patch(f"/users/{body['id']}", json={"role": "admin"})
    assert updated.status_code == 200 and updated.json()["role"] == "admin"


def test_user_creation_validation(login_as, admin):
    client = login_as(admin)
    assert client.post("/users", json=NEW_USER).status_code == 201
    duplicate = client.post("/users", json={**NEW_USER, "email": "  RESPONDER.NEW@example.com"})
    assert duplicate.status_code == 409
    weak = client.post("/users", json={**NEW_USER, "email": "weak@example.com", "password": "short"})
    assert weak.status_code == 422 and "short" not in weak.text
    assert client.post("/users", json={**NEW_USER, "email": "not-an-email"}).status_code == 422
    assert client.post("/users", json={**NEW_USER, "email": "x@example.com", "role": "superuser"}).status_code == 422
    assert client.post("/users", json={**NEW_USER, "email": "y@example.com", "password_hash": "$argon2id$forged"}).status_code == 422


@pytest.mark.parametrize("role", [UserRole.REPORTER, UserRole.RESPONDER])
def test_non_admins_cannot_manage_users_or_escalate_themselves(role, make_user, login_as, admin):
    user = make_user(role)
    client = login_as(user)
    assert client.get("/users").status_code == 403
    assert client.post("/users", json={**NEW_USER, "role": "admin"}).status_code == 403
    assert client.patch(f"/users/{user.id}", json={"role": "admin"}).status_code == 403
    assert client.patch(f"/users/{admin.id}", json={"is_active": False}).status_code == 403
    assert client.get("/auth/me").json()["user"]["role"] == role.value


def test_admin_cannot_remove_their_own_access(login_as, admin):
    client = login_as(admin)
    assert client.patch(f"/users/{admin.id}", json={"role": "responder"}).status_code == 409
    assert client.patch(f"/users/{admin.id}", json={"is_active": False}).status_code == 409
    assert client.get("/auth/me").json()["user"]["role"] == "admin"


def test_role_change_and_deactivation_revoke_sessions(login_as, admin, make_user, client):
    admin_client = login_as(admin)
    promoted = make_user(UserRole.REPORTER)
    promoted_client = login_as(promoted)
    assert admin_client.patch(f"/users/{promoted.id}", json={"role": "responder"}).status_code == 200
    assert promoted_client.get("/auth/me").status_code == 401
    assert login_as(promoted).get("/auth/me").json()["user"]["role"] == "responder"

    leaver = make_user(UserRole.RESPONDER)
    leaver_client = login_as(leaver)
    assert admin_client.patch(f"/users/{leaver.id}", json={"is_active": False}).status_code == 200
    assert leaver_client.get("/incidents").status_code == 401
    assert client.post("/auth/login", json={"email": leaver.email, "password": TEST_PASSWORD}).status_code == 401


def test_unchanged_role_does_not_sign_the_user_out(login_as, admin, responder):
    responder_client = login_as(responder)
    assert login_as(admin).patch(f"/users/{responder.id}", json={"role": "responder"}).status_code == 200
    assert responder_client.get("/auth/me").status_code == 200


def test_assignee_directory(login_as, make_user, reporter, responder, admin):
    inactive = make_user(UserRole.RESPONDER, is_active=False)
    for user in (responder, admin):
        listing = login_as(user).get("/users/assignees")
        assert listing.status_code == 200
        ids = {entry["id"] for entry in listing.json()}
        assert {responder.id, admin.id} <= ids
        assert reporter.id not in ids and inactive.id not in ids
        assert set(listing.json()[0]) == {"id", "email"}
    assert login_as(reporter).get("/users/assignees").status_code == 403


def test_unknown_user_returns_404(login_as, admin):
    assert login_as(admin).patch("/users/999999999", json={"role": "admin"}).status_code == 404
