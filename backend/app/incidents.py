from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from .database import SessionLocal
from .models import Incident, IncidentStatus, Severity
from .schemas import IncidentCreate, IncidentPage, IncidentRead, IncidentUpdate

router = APIRouter(prefix="/incidents", tags=["incidents"])

def get_db():
    with SessionLocal() as session:
        yield session

DbSession = Annotated[Session, Depends(get_db)]

@router.post("", response_model=IncidentRead, status_code=status.HTTP_201_CREATED)
def create_incident(payload: IncidentCreate, db: DbSession):
    incident = Incident(**payload.model_dump())
    db.add(incident)
    db.commit()
    db.refresh(incident)
    return incident

@router.get("", response_model=IncidentPage)
def list_incidents(
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
    total = db.scalar(select(func.count()).select_from(Incident).where(*filters))
    items = db.scalars(select(Incident).where(*filters).order_by(Incident.id.desc()).limit(limit).offset(offset)).all()
    return IncidentPage(items=items, total=total or 0, limit=limit, offset=offset)

@router.get("/{incident_id}", response_model=IncidentRead)
def get_incident(incident_id: int, db: DbSession):
    incident = db.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    return incident

@router.patch("/{incident_id}", response_model=IncidentRead)
def update_incident(incident_id: int, payload: IncidentUpdate, db: DbSession):
    incident = db.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    changes = payload.model_dump(exclude_unset=True)
    for field in ("title", "description", "severity", "status"):
        if field in changes and changes[field] is None:
            raise HTTPException(status_code=422, detail=f"{field} cannot be null")
    for field, value in changes.items():
        setattr(incident, field, value)
    db.commit()
    db.refresh(incident)
    return incident
