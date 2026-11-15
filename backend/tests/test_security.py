"""Unit tests for password hashing, tokens, the rate limiter and the admin bootstrap command."""
import io

import pytest
from sqlalchemy import select

from app import cli
from app.models import User, UserRole
from app.security import (
    LoginRateLimiter,
    hash_password,
    hash_token,
    new_token,
    password_needs_rehash,
    password_problem,
    verify_password,
)


def test_argon2id_hash_and_verify():
    hashed = hash_password("a-long-synthetic-passphrase")
    assert hashed.startswith("$argon2id$")
    assert hashed != hash_password("a-long-synthetic-passphrase")  # salted
    assert verify_password(hashed, "a-long-synthetic-passphrase")
    assert not verify_password(hashed, "a-long-synthetic-passphrasE")
    assert not verify_password("not-a-hash", "anything")
    assert not password_needs_rehash(hashed)


@pytest.mark.parametrize("password, ok", [
    ("short", False),
    (" " * 20, False),
    ("x" * 129, False),
    ("twelve chars", True),
    ("x" * 128, True),
])
def test_password_policy(password, ok):
    assert (password_problem(password) is None) is ok


def test_tokens_are_random_and_hashed():
    first, second = new_token(), new_token()
    assert first != second and len(first) >= 43
    assert len(hash_token(first)) == 64 and hash_token(first) != first


def test_rate_limiter_sliding_window():
    now = [1000.0]
    limiter = LoginRateLimiter(clock=lambda: now[0])
    for _ in range(3):
        limiter.record_failure("email:a")
    assert limiter.retry_after("email:a", limit=3, window=60) == 61
    assert limiter.retry_after("email:b", limit=3, window=60) is None
    now[0] += 61
    assert limiter.retry_after("email:a", limit=3, window=60) is None
    limiter.record_failure("email:a")
    limiter.reset("email:a")
    assert limiter.retry_after("email:a", limit=1, window=60) is None


# ---- Admin bootstrap command --------------------------------------------------------

def test_create_admin_hashes_the_password(session_factory, db):
    user = cli.create_admin(" First.Admin@Example.com ", "a-long-synthetic-passphrase", session_factory=session_factory)
    stored = db.scalar(select(User).where(User.id == user.id))
    assert stored.email == "first.admin@example.com"
    assert stored.role == UserRole.ADMIN
    assert stored.password_hash.startswith("$argon2id$")


def test_create_admin_refuses_duplicates_and_weak_passwords(session_factory):
    cli.create_admin("admin@example.com", "a-long-synthetic-passphrase", session_factory=session_factory)
    with pytest.raises(SystemExit, match="already exists"):
        cli.create_admin("ADMIN@example.com", "a-long-synthetic-passphrase", session_factory=session_factory)
    with pytest.raises(SystemExit, match="at least 12"):
        cli.create_admin("other@example.com", "short", session_factory=session_factory)
    with pytest.raises(SystemExit, match="valid email"):
        cli.create_admin("not-an-email", "a-long-synthetic-passphrase", session_factory=session_factory)


def test_cli_reads_password_from_environment_and_never_prints_it(monkeypatch, session_factory, capsys):
    monkeypatch.setenv(cli.PASSWORD_ENV, "a-long-synthetic-passphrase")
    monkeypatch.setattr(cli, "SessionLocal", session_factory)
    assert cli.main(["create-admin", "--email", "boot@example.com"]) == 0
    output = capsys.readouterr()
    assert "boot@example.com" in output.out
    assert "a-long-synthetic-passphrase" not in output.out + output.err


def test_cli_without_terminal_or_env_refuses(monkeypatch):
    monkeypatch.delenv(cli.PASSWORD_ENV, raising=False)
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO())
    with pytest.raises(SystemExit, match="BOOTSTRAP_ADMIN_PASSWORD"):
        cli.main(["create-admin", "--email", "boot@example.com"])
