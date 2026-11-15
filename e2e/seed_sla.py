"""Seeds synthetic SLA scenarios for the end-to-end suite (run by e2e/run.sh in the backend container).

Times are relative to the database clock, with at least 10 minutes of margin around every
boundary so the states hold for the whole run. Prints shell `export` lines for run.sh.
Synthetic data only: example.com address, random password from the environment.
"""
import os
import shlex
from datetime import timedelta

from sqlalchemy import func, select

from app import audit, sla_store
from app.database import SessionLocal
from app.models import (
    Incident, IncidentEvent, IncidentEventAction, IncidentStatus, Severity, SlaPolicySource, SlaPolicyTarget, SlaPolicyVersion, User, UserRole,
)
from app.security import hash_password
from app.sla import lock_policy_shared

EMAIL = "e2e.sla.reporter@example.com"


def main() -> None:
    with SessionLocal() as db:
        now = db.scalar(select(func.clock_timestamp()))
        default = db.scalar(select(SlaPolicyVersion).order_by(SlaPolicyVersion.id).limit(1))
        # A copy of the default policy effective 30 days ago, so the back-dated incidents are tracked.
        policy = SlaPolicyVersion(source=SlaPolicySource.DEFAULT, reason="End-to-end policy (back-dated copy of the default)",
                                  created_at=now - timedelta(days=30))
        db.add(policy)
        db.flush()
        for target in default.targets:
            db.add(SlaPolicyTarget(version_id=policy.id, severity=target.severity,
                                   response_minutes=target.response_minutes, resolution_minutes=target.resolution_minutes))
        reporter = User(email=EMAIL, password_hash=hash_password(os.environ["E2E_SLA_REPORTER_PASSWORD"]), role=UserRole.REPORTER)
        staff = db.scalar(select(User).where(User.role == UserRole.ADMIN).limit(1))
        db.add(reporter)
        db.flush()
        lock_policy_shared(db)

        def incident(title: str, created_ago: timedelta, changes: list[tuple[timedelta, dict]] = ()) -> int:
            at = now - created_ago
            row = Incident(title=title, description="Synthetic SLA scenario.", severity=Severity.HIGH, status=IncidentStatus.OPEN,
                           reporter_id=reporter.id, created_at=at, updated_at=at)
            db.add(row)
            db.flush()
            created = audit.creation_changes(row)
            db.add(IncidentEvent(incident_id=row.id, actor_id=reporter.id, action=IncidentEventAction.CREATED,
                                 changes=created, created_at=at))
            # The stored SLA cycles are maintained by the application's own write path.
            sla_store.record_change(db, row, IncidentEventAction.CREATED, created, at)
            for ago, fields in changes:
                before = audit.snapshot(row)
                for field, value in fields.items():
                    setattr(row, field, IncidentStatus(value) if field == "status" else value)
                diff = audit.diff(before, audit.snapshot(row))
                action = audit.classify(diff)
                db.add(IncidentEvent(incident_id=row.id, actor_id=staff.id, action=action, changes=diff, created_at=now - ago))
                sla_store.record_change(db, row, action, diff, now - ago)
            row.updated_at = now
            db.flush()
            return row.id

        # High severity, default targets: respond in 1 h, resolve in 24 h.
        ids = {
            "OVERDUE": incident("E2E SLA response overdue", timedelta(hours=2)),
            "APPROACHING": incident("E2E SLA response approaching", timedelta(minutes=48)),
            "MET": incident("E2E SLA met", timedelta(hours=3), [
                (timedelta(hours=2, minutes=55), {"status": "investigating"}), (timedelta(hours=2), {"status": "resolved"})]),
            "BREACHED": incident("E2E SLA breached", timedelta(days=3), [
                (timedelta(days=3) - timedelta(hours=2), {"status": "investigating"}), (timedelta(days=2, hours=12), {"status": "resolved"})]),
            "REOPENED": incident("E2E SLA reopened", timedelta(days=5), [
                (timedelta(days=5) - timedelta(minutes=30), {"status": "investigating"}),
                (timedelta(days=5) - timedelta(hours=2), {"status": "resolved"}),
                (timedelta(hours=1), {"status": "investigating"})]),
        }
        db.commit()
    print(f"export E2E_SLA_REPORTER_EMAIL={shlex.quote(EMAIL)}")
    for name, incident_id in ids.items():
        print(f"export E2E_SLA_{name}_ID={incident_id}")


if __name__ == "__main__":
    main()
