"""Incident discussion: public comments and internal (staff-only) notes.

Security rules:
- Incident visibility comes first and reuses incidents._get_visible, so a reporter gets the same
  404 for someone else's incident, a legacy incident or a missing one, whatever they send.
- Reporters read and write public comments only. The visibility filter is applied inside the
  SQL for both the rows and the total, and the response is checked again before it is sent.
- Comments are append-only (database trigger, migration 0006) and kept out of incident_events:
  the comment row is its own immutable audit record (author, visibility, body, time).
- Adding a comment never touches the incident row, so `updated_at` cannot reveal internal activity.
"""
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from .auth import CurrentUser
from .database import get_db
from .incidents import _get_visible
from .models import CommentVisibility, IncidentComment, User, UserRole
from .schemas import CommentCreate, CommentPage, CommentRead

router = APIRouter(prefix="/incidents/{incident_id}/comments", tags=["comments"])
logger = logging.getLogger(__name__)

DbSession = Annotated[Session, Depends(get_db)]

REPORTER_INTERNAL_FORBIDDEN = "Reporters can only post public comments."


def _comments_visible_to(query: Select, user: User) -> Select:
    """Reporters read public comments only; responders and admins read both kinds."""
    if user.role == UserRole.REPORTER:
        return query.where(IncidentComment.visibility == CommentVisibility.PUBLIC)
    return query


def _fail_closed(user: User, comments: list[IncidentComment]) -> None:
    """Defence in depth: never send an internal note to a reporter, even if a query regresses."""
    if user.role == UserRole.REPORTER and any(c.visibility != CommentVisibility.PUBLIC for c in comments):
        # Identifiers only; never the comment body.
        logger.error("Refused to send internal comments to reporter user_id=%s", user.id)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error")


@router.get("", response_model=CommentPage)
def list_comments(
    incident_id: int,
    user: CurrentUser,
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    _get_visible(db, incident_id, user)  # 404 if missing or not visible
    on_incident = IncidentComment.incident_id == incident_id
    total = db.scalar(_comments_visible_to(select(func.count()).select_from(IncidentComment).where(on_incident), user))
    comments = db.scalars(
        _comments_visible_to(select(IncidentComment).where(on_incident), user)
        .order_by(IncidentComment.created_at, IncidentComment.id)  # oldest first; append-only, so pages never shift
        .limit(limit)
        .offset(offset)
    ).all()
    _fail_closed(user, list(comments))
    return CommentPage(items=[CommentRead.model_validate(c) for c in comments], total=total or 0, limit=limit, offset=offset)


def add_comment(db: Session, incident_id: int, author: User, body: str, visibility: CommentVisibility) -> IncidentComment:
    """Adds a comment whose body has already passed clean_comment_body. Does not commit.
    The author always comes from the session; the incident row itself is not modified."""
    comment = IncidentComment(incident_id=incident_id, author_id=author.id, visibility=visibility, body=body)
    db.add(comment)
    db.flush()
    return comment


@router.post("", response_model=CommentRead, status_code=status.HTTP_201_CREATED)
def create_comment(incident_id: int, payload: CommentCreate, user: CurrentUser, db: DbSession):
    incident = _get_visible(db, incident_id, user)  # visibility first: 404 before any 403
    if user.role == UserRole.REPORTER and payload.visibility != CommentVisibility.PUBLIC:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=REPORTER_INTERNAL_FORBIDDEN)
    comment = add_comment(db, incident.id, user, payload.body, payload.visibility)
    db.commit()
    db.refresh(comment)
    return comment
