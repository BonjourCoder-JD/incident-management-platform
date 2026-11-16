"""Incident discussion: public comments and internal (staff-only) notes.

Security rules:
- Incident visibility comes first and reuses incidents._get_visible, so a reporter gets the same
  404 for someone else's incident, a legacy incident or a missing one, whatever they send.
- Reporters read and write public comments only. The visibility filter is applied inside the
  SQL for both the rows and the total, and the response is checked again before it is sent.
- Comments are append-only (database trigger, migration 0006) and kept out of incident_events:
  the comment row is its own immutable audit record (author, visibility, body, time).
- Adding a comment never touches the incident row, so `updated_at` cannot reveal internal activity.
- Administrators can redact a comment (ISSUE-18). The comment row stays unchanged as evidence; an
  append-only comment_redactions row records who, when and why, and every reader below withholds the
  body from then on. All comment responses are built by _read, the single place that applies this.
"""
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from . import rate_limit
from .auth import CurrentUser, require_roles
from .database import get_db
from .incidents import _get_visible
from .models import CommentRedaction, CommentVisibility, IncidentComment, User, UserRole
from .schemas import CommentCreate, CommentPage, CommentRead, CommentRedactionCreate

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


Admin = Annotated[User, Depends(require_roles(UserRole.ADMIN))]


def _read(comment: IncidentComment, redaction: CommentRedaction | None, user: User) -> CommentRead:
    """The only way a comment leaves the API: a redacted comment never carries its body."""
    read = CommentRead.model_validate(comment)
    if redaction is not None:
        staff = user.role != UserRole.REPORTER
        read = read.model_copy(update={
            "body": None, "redacted": True, "redacted_at": redaction.created_at,
            "redaction_reason": redaction.reason if staff else None,
        })
    return read


def _redactions(db: Session, comment_ids: list[int]) -> dict[int, CommentRedaction]:
    if not comment_ids:
        return {}
    rows = db.scalars(select(CommentRedaction).where(CommentRedaction.comment_id.in_(comment_ids))).all()
    return {row.comment_id: row for row in rows}


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
    redactions = _redactions(db, [c.id for c in comments])
    return CommentPage(items=[_read(c, redactions.get(c.id), user) for c in comments], total=total or 0, limit=limit, offset=offset)


def add_comment(db: Session, incident_id: int, author: User, body: str, visibility: CommentVisibility) -> IncidentComment:
    """Adds a comment whose body has already passed clean_comment_body. Does not commit.
    The author always comes from the session; the incident row itself is not modified."""
    comment = IncidentComment(incident_id=incident_id, author_id=author.id, visibility=visibility, body=body)
    db.add(comment)
    db.flush()
    return comment


@router.post("", response_model=CommentRead, status_code=status.HTTP_201_CREATED)
def create_comment(incident_id: int, payload: CommentCreate, user: CurrentUser, db: DbSession,
                   _limit: Annotated[None, Depends(rate_limit.limit_writes(rate_limit.WRITE_COMMENT))]):
    incident = _get_visible(db, incident_id, user)  # visibility first: 404 before any 403
    if user.role == UserRole.REPORTER and payload.visibility != CommentVisibility.PUBLIC:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=REPORTER_INTERNAL_FORBIDDEN)
    comment = add_comment(db, incident.id, user, payload.body, payload.visibility)
    db.commit()
    db.refresh(comment)
    return _read(comment, None, user)


@router.post("/{comment_id}/redaction", response_model=CommentRead, status_code=status.HTTP_201_CREATED)
def redact_comment(incident_id: int, comment_id: int, payload: CommentRedactionCreate, user: Admin, db: DbSession):
    """Administrators only. Irreversible: the body is withheld from every reader from now on."""
    _get_visible(db, incident_id, user)
    comment = db.get(IncidentComment, comment_id)
    if comment is None or comment.incident_id != incident_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Comment not found")
    if db.scalar(select(CommentRedaction.id).where(CommentRedaction.comment_id == comment_id)) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This comment has already been removed.")
    redaction = CommentRedaction(comment_id=comment_id, redacted_by=user.id, reason=payload.reason)
    db.add(redaction)
    try:
        db.commit()
    except IntegrityError:  # a concurrent redaction won
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This comment has already been removed.") from None
    db.refresh(redaction)
    # Identifiers only; never the comment body or the reason.
    logger.info("Comment %s on incident %s redacted by user %s", comment_id, incident_id, user.id)
    return _read(comment, redaction, user)
