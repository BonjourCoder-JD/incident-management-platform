from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session
from .auth import CurrentUser
from .database import get_db
from .models import Incident, IncidentStatus, Severity, User, UserRole
from .schemas import IncidentCreate, IncidentPage, IncidentRead, IncidentUpdate

router = APIRouter(prefix="/incidents", tags=["incidents"])

DbSession = Annotated[Session, Depends(get_db)]

# Fields only responders and admins may set.
TRIAGE_FIELDS = ("status", "assignee_id")


def _visible_to(query: Select, user: User) -> Select:
    """Reporters see only incidents they reported; responders and admins see all.

    Future audit history, analytics and exports must reuse this rule.
    """
    if user.role == UserRole.REPORTER:
        return query.where(Incident.reporter_id == user.id)
    return query


def _get_visible(db: Session, incident_id: int, user: User) -> Incident:
    incident = db.scalar(_visible_to(select(Incident).where(Incident.id == incident_id), user))
    if incident is None:
        # Same response whether the incident is missing or belongs to someone else.
        raise HTTPException(status_code=404, detail="Incident not found")
    return incident


def _forbid_triage_fields(user: User, fields: set[str]) -> None:
    if user.role == UserRole.REPORTER and fields & set(TRIAGE_FIELDS):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Reporters cannot change status or assignment.")


def _check_assignee(db: Session, assignee_id: int) -> None:
    assignee = db.get(User, assignee_id)
    if assignee is None or not assignee.is_active or assignee.role not in (UserRole.RESPONDER, UserRole.ADMIN):
        raise HTTPException(
            status_code=422,
            detail=[{"loc": ["body", "assignee_id"], "msg": "Assignee must be an active responder or admin.", "type": "value_error"}],
        )


@router.post("", response_model=IncidentRead, status_code=status.HTTP_201_CREATED)
def create_incident(payload: IncidentCreate, user: CurrentUser, db: DbSession):
    data = payload.model_dump(exclude_unset=True)
    _forbid_triage_fields(user, {key for key, value in data.items() if value is not None})
    if payload.assignee_id is not None:
        _check_assignee(db, payload.assignee_id)
    # Ownership always comes from the session, never from the request body.
    incident = Incident(**payload.model_dump(), reporter_id=user.id)
    db.add(incident)
    db.commit()
    db.refresh(incident)
    return incident

@router.get("", response_model=IncidentPage)
def list_incidents(
    user: CurrentUser,
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    severity: Severity | None = None,
    status: IncidentStatus | None = None,
):
    filters = []
    if severity is not None:
        filters.append(Incident.severity == severity)
    if status is not None:
        filters.append(Incident.status == status)
    total = db.scalar(_visible_to(select(func.count()).select_from(Incident).where(*filters), user))
    items = db.scalars(_visible_to(select(Incident).where(*filters), user).order_by(Incident.id.desc()).limit(limit).offset(offset)).all()
    return IncidentPage(items=items, total=total or 0, limit=limit, offset=offset)

@router.get("/{incident_id}", response_model=IncidentRead)
def get_incident(incident_id: int, user: CurrentUser, db: DbSession):
    return _get_visible(db, incident_id, user)

@router.patch("/{incident_id}", response_model=IncidentRead)
def update_incident(incident_id: int, payload: IncidentUpdate, user: CurrentUser, db: DbSession):
    incident = _get_visible(db, incident_id, user)
    changes = payload.model_dump(exclude_unset=True)
    _forbid_triage_fields(user, set(changes))
    for field in ("title", "description", "severity", "status"):
        if field in changes and changes[field] is None:
            raise HTTPException(status_code=422, detail=f"{field} cannot be null")
    if changes.get("assignee_id") is not None:
        _check_assignee(db, changes["assignee_id"])
    for field, value in changes.items():
        setattr(incident, field, value)
    db.commit()
    db.refresh(incident)
    return incident
