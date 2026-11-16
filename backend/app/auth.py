"""Cookie-based server-side sessions, CSRF protection and role checks."""
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .config import settings
from .database import get_db
from . import demo, rate_limit
from .models import AuthSession, User, UserRole
from .schemas import LoginRequest, PasswordChange, SessionInfo, UserRead
from .security import (
    PASSWORD_MAX_LENGTH,
    burn_password_check,
    hash_password,
    hash_token,
    new_token,
    normalize_email,
    password_needs_rehash,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])

DbSession = Annotated[Session, Depends(get_db)]
CSRF_HEADER = "X-CSRF-Token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
# Avoid a database write on every request; idle expiry is accurate to this interval.
LAST_SEEN_RESOLUTION = timedelta(minutes=1)
INVALID_CREDENTIALS = "Invalid email or password."


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _unauthorized(detail: str = "Authentication required.") -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        settings.session_cookie_name,
        token,
        max_age=settings.session_absolute_hours * 3600,
        path="/",
        secure=settings.session_cookie_secure,
        httponly=True,
        samesite="strict",
    )


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        settings.session_cookie_name, path="/", secure=settings.session_cookie_secure, httponly=True, samesite="strict"
    )


def _find_session(request: Request, db: Session) -> AuthSession | None:
    """Returns the live session for the request cookie, deleting it if it has expired."""
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        return None
    session = db.scalar(select(AuthSession).where(AuthSession.token_hash == hash_token(token)))
    if session is None:
        return None
    now = _now()
    idle_deadline = session.last_seen_at + timedelta(minutes=settings.session_idle_minutes)
    if session.expires_at <= now or idle_deadline <= now or not session.user.is_active:
        db.delete(session)
        db.commit()
        return None
    if now - session.last_seen_at >= LAST_SEEN_RESOLUTION:
        session.last_seen_at = now
        db.commit()
    return session


def _check_csrf(request: Request, session: AuthSession) -> None:
    if request.method in SAFE_METHODS:
        return
    supplied = request.headers.get(CSRF_HEADER, "")
    if not supplied or not secrets.compare_digest(supplied, session.csrf_token):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF token missing or invalid.")


def get_current_session(request: Request, db: DbSession) -> AuthSession:
    session = _find_session(request, db)
    if session is None:
        raise _unauthorized()
    _check_csrf(request, session)
    return session


def get_current_user(session: Annotated[AuthSession, Depends(get_current_session)]) -> User:
    return session.user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_roles(*roles: UserRole):
    allowed = set(roles)

    def dependency(user: CurrentUser) -> User:
        if user.role not in allowed:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not have permission to do this.")
        return user

    return dependency


def _client_ip(request: Request) -> str:
    """The client address. Behind the production proxy, uvicorn sets it from X-Forwarded-For only
    when the request comes from the trusted proxy (FORWARDED_ALLOW_IPS); otherwise it is the peer."""
    return request.client.host if request.client else "unknown"


def _session_info(session: AuthSession) -> SessionInfo:
    return SessionInfo(user=UserRead.model_validate(session.user), csrf_token=session.csrf_token)


@router.post("/login", response_model=SessionInfo)
def login(payload: LoginRequest, request: Request, response: Response, db: DbSession):
    email = normalize_email(payload.email)
    window = settings.login_failure_window_seconds
    limits = [
        rate_limit.Limit(rate_limit.LOGIN_EMAIL, email, settings.login_max_failures_per_email, window),
        rate_limit.Limit(rate_limit.LOGIN_IP, _client_ip(request), settings.login_max_failures_per_ip, window),
    ]
    if demo.is_demo_account(email):
        # Published demo accounts: anyone could lock them by email, so only the per-IP limit applies.
        limits = limits[1:]
    # Shared by every process and kept across restarts (PostgreSQL); attempts on the same email or
    # address are serialised until this transaction ends.
    rate_limit.lock(db, limits)
    for limit in limits:
        retry_after = rate_limit.retry_after(db, limit)
        if retry_after is not None:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many failed sign-in attempts. Try again later.",
                headers={"Retry-After": str(retry_after)},
            )

    user = db.scalar(select(User).where(User.email == email))
    password_ok = False
    if user is None or len(payload.password) > PASSWORD_MAX_LENGTH:
        burn_password_check(payload.password[:PASSWORD_MAX_LENGTH])
    else:
        password_ok = verify_password(user.password_hash, payload.password)

    # Inactive accounts get the same answer as wrong passwords: nothing about the account is revealed.
    if user is None or not password_ok or not user.is_active:
        for limit in limits:
            rate_limit.record(db, limit)
        db.commit()  # the failure must count even though the request fails
        raise _unauthorized(INVALID_CREDENTIALS)

    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(payload.password)

    # Replace any session the browser already had, and drop this user's expired sessions.
    old = _find_session(request, db)
    if old is not None:
        db.delete(old)
    db.execute(delete(AuthSession).where(AuthSession.user_id == user.id, AuthSession.expires_at <= _now()))

    token = new_token()
    session = AuthSession(
        token_hash=hash_token(token),
        csrf_token=new_token(),
        user_id=user.id,
        expires_at=_now() + timedelta(hours=settings.session_absolute_hours),
    )
    db.add(session)
    if limits[0].bucket == rate_limit.LOGIN_EMAIL:
        rate_limit.reset(db, limits[0])  # a successful sign-in clears that email's failures
    db.commit()
    db.refresh(session)
    _set_session_cookie(response, token)
    return _session_info(session)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response, db: DbSession):
    session = _find_session(request, db)
    if session is not None:
        _check_csrf(request, session)
        db.delete(session)
        db.commit()
    _clear_session_cookie(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me", response_model=SessionInfo)
def me(session: Annotated[AuthSession, Depends(get_current_session)]):
    return _session_info(session)


@router.put("/password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(payload: PasswordChange, session: Annotated[AuthSession, Depends(get_current_session)], db: DbSession):
    user = session.user
    if demo.is_demo_account(user.email):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="The password of a public demo account cannot be changed.")
    if not verify_password(user.password_hash, payload.current_password[:PASSWORD_MAX_LENGTH]) or len(payload.current_password) > PASSWORD_MAX_LENGTH:
        raise HTTPException(
            status_code=422,
            detail=[{"loc": ["body", "current_password"], "msg": "Current password is incorrect.", "type": "value_error"}],
        )
    user.password_hash = hash_password(payload.new_password)
    # Sign out every other device; keep the current session.
    db.execute(delete(AuthSession).where(AuthSession.user_id == user.id, AuthSession.id != session.id))
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
