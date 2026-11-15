from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session
from . import audit
from .auth import CurrentUser
from .database import get_db
from .models import Incident, IncidentEvent, IncidentEventAction, IncidentStatus, Severity, User, UserRole
from .schemas import IncidentCreate, IncidentEventPage, IncidentEventRead, IncidentPage, IncidentRead, IncidentUpdate, UserSummary

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


def _get_visible(db: Session, incident_id: int, user: User, *, for_update: bool = False) -> Incident:
    query = _visible_to(select(Incident).where(Incident.id == incident_id), user)
    if for_update:
        # Row lock until commit: concurrent updates are serialised, so each audit diff starts
        # from the value actually persisted by the previous change. populate_existing discards
        # anything already cached in this session.
        query = query.with_for_update(of=Incident).execution_options(populate_existing=True)
    incident = db.scalar(query)
    if incident is None:
        # Same response whether the incident is missing or belongs to someone else.
        raise HTTPException(status_code=404, detail="Incident not found")
    return incident


def incident_filters(severity: Severity | None, status: IncidentStatus | None) -> list:
    """WHERE conditions for the list filters, shared by the list endpoint and CSV export."""
    filters = []
    if severity is not None:
        filters.append(Incident.severity == severity)
    if status is not None:
        filters.append(Incident.status == status)
    return filters


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
    db.flush()  # assigns the id; nothing is committed until the audit event is added too
    audit.record_incident_event(db, incident, user, IncidentEventAction.CREATED, audit.creation_changes(incident))
    db.commit()  # incident and its audit event commit (or roll back) together
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
    filters = incident_filters(severity, status)
    total = db.scalar(_visible_to(select(func.count()).select_from(Incident).where(*filters), user))
    items = db.scalars(_visible_to(select(Incident).where(*filters), user).order_by(Incident.id.desc()).limit(limit).offset(offset)).all()
    return IncidentPage(items=items, total=total or 0, limit=limit, offset=offset)

@router.get("/{incident_id}", response_model=IncidentRead)
def get_incident(incident_id: int, user: CurrentUser, db: DbSession):
    return _get_visible(db, incident_id, user)

def _apply_changes(incident: Incident, changes: dict) -> None:
    for field, value in changes.items():
        setattr(incident, field, value)


@router.patch("/{incident_id}", response_model=IncidentRead)
def update_incident(incident_id: int, payload: IncidentUpdate, user: CurrentUser, db: DbSession):
    incident = _get_visible(db, incident_id, user, for_update=True)
    changes = payload.model_dump(exclude_unset=True)
    _forbid_triage_fields(user, set(changes))
    for field in ("title", "description", "severity", "status"):
        if field in changes and changes[field] is None:
            raise HTTPException(status_code=422, detail=f"{field} cannot be null")
    if changes.get("assignee_id") is not None:
        _check_assignee(db, changes["assignee_id"])

    before = audit.snapshot(incident)
    _apply_changes(incident, changes)
    diff = audit.diff(before, audit.snapshot(incident))
    if not diff:
        # Nothing actually changed: no row update and no audit event.
        db.rollback()
        return _get_visible(db, incident_id, user)
    audit.record_incident_event(db, incident, user, audit.classify(diff), diff)
    db.commit()  # incident change and its audit event commit (or roll back) together
    db.refresh(incident)
    return incident


@router.get("/{incident_id}/events", response_model=IncidentEventPage)
def list_incident_events(
    incident_id: int,
    user: CurrentUser,
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    # Visibility first: reporters get 404 for other people's and legacy incidents.
    _get_visible(db, incident_id, user)
    total = db.scalar(select(func.count()).select_from(IncidentEvent).where(IncidentEvent.incident_id == incident_id))
    events = db.scalars(
        select(IncidentEvent)
        .where(IncidentEvent.incident_id == incident_id)
        .order_by(IncidentEvent.created_at.desc(), IncidentEvent.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    referenced = {
        value
        for event in events
        for value in (event.changes.get("assignee_id") or {}).values()
        if isinstance(value, int)
    }
    users = db.scalars(select(User).where(User.id.in_(referenced)).order_by(User.id)).all() if referenced else []
    return IncidentEventPage(
        items=[IncidentEventRead.model_validate(event) for event in events],
        total=total or 0,
        limit=limit,
        offset=offset,
        users=[UserSummary.model_validate(u) for u in users],
    )
