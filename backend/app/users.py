"""User administration (admins) and the assignee directory (responders and admins)."""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import demo
from .auth import require_roles
from .database import get_db
from .models import AuthSession, User, UserRole
from .schemas import UserCreate, UserRead, UserSummary, UserUpdate
from .security import hash_password

router = APIRouter(prefix="/users", tags=["users"])

DbSession = Annotated[Session, Depends(get_db)]
Admin = Annotated[User, Depends(require_roles(UserRole.ADMIN))]
Staff = Annotated[User, Depends(require_roles(UserRole.RESPONDER, UserRole.ADMIN))]
ASSIGNABLE_ROLES = (UserRole.RESPONDER, UserRole.ADMIN)


@router.get("/assignees", response_model=list[UserSummary])
def list_assignees(_: Staff, db: DbSession):
    """Active responders and admins: the people an incident can be assigned to."""
    return db.scalars(
        select(User).where(User.is_active.is_(True), User.role.in_(ASSIGNABLE_ROLES)).order_by(User.email)
    ).all()


@router.get("", response_model=list[UserRead])
def list_users(_: Admin, db: DbSession):
    return db.scalars(select(User).order_by(User.id)).all()


@router.post("", response_model=UserRead, status_code=status.HTTP_201_CREATED)
def create_user(payload: UserCreate, _: Admin, db: DbSession):
    if db.scalar(select(User.id).where(User.email == payload.email)) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A user with this email already exists.")
    user = User(email=payload.email, password_hash=hash_password(payload.password), role=payload.role)
    db.add(user)
    try:
        db.commit()
    except IntegrityError:  # concurrent creation with the same email
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A user with this email already exists.")
    db.refresh(user)
    return user


@router.patch("/{user_id}", response_model=UserRead)
def update_user(user_id: int, payload: UserUpdate, admin: Admin, db: DbSession):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    changes = payload.model_dump(exclude_unset=True)
    for field in ("role", "is_active"):
        if field in changes and changes[field] is None:
            raise HTTPException(status_code=422, detail=f"{field} cannot be null")

    if demo.is_demo_account(user.email) and ("role" in changes or "is_active" in changes):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Public demo accounts cannot be changed.")

    # An admin cannot remove their own access, so at least one active admin always remains.
    if user.id == admin.id and (changes.get("role", UserRole.ADMIN) != UserRole.ADMIN or changes.get("is_active", True) is False):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="You cannot remove your own admin access or deactivate yourself.")

    privileges_changed = ("role" in changes and changes["role"] != user.role) or ("is_active" in changes and changes["is_active"] != user.is_active)
    for field, value in changes.items():
        setattr(user, field, value)
    if privileges_changed:
        # Force the user to sign in again so every request sees the new role or status.
        db.execute(delete(AuthSession).where(AuthSession.user_id == user.id))
    db.commit()
    db.refresh(user)
    return user
