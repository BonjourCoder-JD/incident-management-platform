"""Shared fixtures: every test runs inside a transaction that is rolled back."""
import itertools

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.database import engine, get_db
from app.main import app
from app.models import User, UserRole
from app.security import hash_password, login_limiter

# Synthetic credentials for tests only. Emails use the reserved example.com domain.
TEST_PASSWORD = "correct-horse-battery-staple"
BASE_URL = "https://testserver"  # https so Secure cookies are sent, as in a browser

_password_hash_cache: dict[str, str] = {}
_email_counter = itertools.count(1)


def _hash(password: str) -> str:
    # Argon2id is deliberately slow; hash each distinct test password once per run.
    if password not in _password_hash_cache:
        _password_hash_cache[password] = hash_password(password)
    return _password_hash_cache[password]


@pytest.fixture
def connection():
    with engine.connect() as conn:
        transaction = conn.begin()
        try:
            yield conn
        finally:
            transaction.rollback()


@pytest.fixture
def session_factory(connection):
    # Commits inside requests become savepoints; the outer transaction is rolled back.
    return sessionmaker(bind=connection, autoflush=False, expire_on_commit=False, join_transaction_mode="create_savepoint")


@pytest.fixture
def db(session_factory):
    with session_factory() as session:
        yield session


@pytest.fixture(autouse=True)
def _isolate(session_factory):
    def override_db():
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    login_limiter.reset()
    yield
    app.dependency_overrides.clear()
    login_limiter.reset()


@pytest.fixture
def client():
    """An unauthenticated client."""
    with TestClient(app, base_url=BASE_URL) as test_client:
        yield test_client


@pytest.fixture
def make_user(db):
    def factory(role: UserRole = UserRole.REPORTER, *, email: str | None = None, password: str = TEST_PASSWORD, is_active: bool = True) -> User:
        user = User(
            email=email or f"{role.value}{next(_email_counter)}@example.com",
            password_hash=_hash(password),
            role=role,
            is_active=is_active,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user

    return factory


@pytest.fixture
def login_as():
    """Returns a client signed in as the given user, sending the CSRF header automatically."""
    clients: list[TestClient] = []

    def login(user: User, password: str = TEST_PASSWORD) -> TestClient:
        test_client = TestClient(app, base_url=BASE_URL)
        clients.append(test_client)
        response = test_client.post("/auth/login", json={"email": user.email, "password": password})
        assert response.status_code == 200, response.text
        test_client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
        return test_client

    yield login
    for test_client in clients:
        test_client.close()


@pytest.fixture
def reporter(make_user):
    return make_user(UserRole.REPORTER)


@pytest.fixture
def other_reporter(make_user):
    return make_user(UserRole.REPORTER)


@pytest.fixture
def responder(make_user):
    return make_user(UserRole.RESPONDER)


@pytest.fixture
def admin(make_user):
    return make_user(UserRole.ADMIN)
