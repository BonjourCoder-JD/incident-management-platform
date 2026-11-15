"""SLA endpoints: per-incident SLA state and admin-only policy versions (Milestone 5B).

- GET /incidents/{id}/sla follows incident visibility (incidents._get_visible: the same 404 for
  missing and not-visible incidents). Escalations are returned to responders and admins only.
- Policy versions are append-only. Creating one requires an admin session and the CSRF token;
  it takes the SLA policy lock exclusively and is stamped with the database clock (see app/sla.py).
- Values come from the stored read model (app/sla_store.py); nothing here reads comments, so
  internal notes cannot reach any SLA response.
"""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .auth import CurrentUser, require_roles
from .database import get_db
from .incidents import _get_visible
from .models import IncidentEscalation, Incident, SlaPolicySource, SlaPolicyTarget, SlaPolicyVersion, User, UserRole
from .schemas import IncidentSla, SlaCycle, SlaEscalationRead, SlaPolicyCreate, SlaPolicyRead, SlaTargetStatus
from . import sla_store
from .sla import Now, current_policy, lock_policy_exclusive, severity_name

router = APIRouter(tags=["sla"])
DbSession = Annotated[Session, Depends(get_db)]
Admin = Annotated[User, Depends(require_roles(UserRole.ADMIN))]
STAFF = (UserRole.RESPONDER, UserRole.ADMIN)


def _target(row, prefix: str, satisfied_at) -> SlaTargetStatus:
    return SlaTargetStatus(
        target_minutes=getattr(row, f"{prefix}_minutes"),
        severity=severity_name(getattr(row, f"{prefix}_rank")),
        due_at=getattr(row, f"{prefix}_due"),
        warning_at=getattr(row, f"{prefix}_warning"),
        satisfied_at=satisfied_at,
        state=getattr(row, f"{prefix}_state"),
    )


@router.get("/incidents/{incident_id}/sla", response_model=IncidentSla)
def get_incident_sla(incident_id: int, user: CurrentUser, db: DbSession, now: Now):
    _get_visible(db, incident_id, user)  # 404 if missing or not visible
    cycles = sla_store.cycle_rows(select(Incident.id.label("id")).where(Incident.id == incident_id), now).subquery()
    rows = db.execute(select(cycles).order_by(cycles.c.cycle)).all()
    items = [
        SlaCycle(
            cycle=row.cycle,
            started_at=row.started_at,
            policy_version_id=row.version_id,
            tracked=row.tracked,
            response=_target(row, "response", row.acknowledged_at) if row.cycle == 0 else None,
            resolution=_target(row, "resolution", row.resolved_at),
        )
        for row in rows
    ]
    escalations = None
    if user.role in STAFF:
        escalations = [
            SlaEscalationRead.model_validate(e)
            for e in db.scalars(select(IncidentEscalation).where(IncidentEscalation.incident_id == incident_id).order_by(IncidentEscalation.id))
        ]
    current = items[-1] if items else None
    return IncidentSla(
        now=now,
        tracked=bool(current and current.tracked),
        current_cycle=current.cycle if current else 0,
        cycles=items,
        escalations=escalations,
    )


@router.get("/sla/policy", response_model=SlaPolicyRead)
def get_current_policy(_: CurrentUser, db: DbSession):
    policy = current_policy(db)
    if policy is None:
        raise HTTPException(status_code=404, detail="No SLA policy is configured.")
    return policy


@router.get("/sla/policy/versions", response_model=list[SlaPolicyRead])
def list_policy_versions(_: Admin, db: DbSession):
    return db.scalars(select(SlaPolicyVersion).order_by(SlaPolicyVersion.created_at.desc(), SlaPolicyVersion.id.desc())).all()


@router.post("/sla/policy/versions", response_model=SlaPolicyRead, status_code=status.HTTP_201_CREATED)
def create_policy_version(payload: SlaPolicyCreate, admin: Admin, db: DbSession):
    # Wait for in-flight incident writes, then stamp the version with the database clock, so it
    # applies only to cycles that start after this moment (refinement 6).
    lock_policy_exclusive(db)
    version = SlaPolicyVersion(source=SlaPolicySource.ADMIN, created_by=admin.id, reason=payload.reason, created_at=func.clock_timestamp())
    db.add(version)
    db.flush()
    db.add_all(
        SlaPolicyTarget(version_id=version.id, severity=t.severity, response_minutes=t.response_minutes, resolution_minutes=t.resolution_minutes)
        for t in payload.targets
    )
    db.commit()
    db.refresh(version)
    return version
