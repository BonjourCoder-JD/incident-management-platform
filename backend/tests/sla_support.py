"""Builds incident timelines with explicit timestamps for SLA tests. All data is synthetic.

By default each change also updates the stored read model through the application's own write
path (app.sla_store.record_change); `maintain=False` leaves it untouched (for backfill tests).
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app import audit
from app.models import (
    Incident, IncidentEventAction, IncidentEvent, IncidentStatus, Severity, SlaPolicySource, SlaPolicyTarget, SlaPolicyVersion,
)
from app import sla_store
from app.sla import current_sla, sla_cycles

UTC = timezone.utc
# Far later than the policy seeded by migration 0007, so a test's own policy version decides.
T0 = datetime(2030, 1, 7, 9, 0, tzinfo=UTC)
US = timedelta(microseconds=1)
# Test policy, in minutes: (response, resolution). High: respond in 1 h, resolve in 8 h.
TARGETS = {"critical": (15, 240), "high": (60, 480), "medium": (240, 4320), "low": (1440, 10080)}


def minutes(n: float) -> timedelta:
    return timedelta(minutes=n)


class Timeline:
    def __init__(self, db, reporter, staff, maintain: bool = True):
        self.db, self.reporter, self.staff, self.maintain = db, reporter, staff, maintain

    def policy(self, at: datetime, targets: dict | None = None, reason: str = "Synthetic test policy") -> SlaPolicyVersion:
        version = SlaPolicyVersion(source=SlaPolicySource.DEFAULT, reason=reason, created_at=at)
        self.db.add(version)
        self.db.flush()
        for severity, (response, resolution) in (targets or TARGETS).items():
            self.db.add(SlaPolicyTarget(version_id=version.id, severity=Severity(severity), response_minutes=response, resolution_minutes=resolution))
        self.db.commit()
        return version

    def incident(self, at: datetime = T0, severity: str = "high", assignee=None, reporter=None) -> Incident:
        """An incident and its `created` event, exactly as the API records them."""
        reporter = reporter or self.reporter
        incident = Incident(
            title="SLA timeline incident", description="Synthetic", severity=Severity(severity), status=IncidentStatus.OPEN,
            reporter_id=reporter.id, assignee_id=assignee.id if assignee else None, created_at=at, updated_at=at,
        )
        self.db.add(incident)
        self.db.flush()
        creator = self.staff if assignee else reporter
        changes = audit.creation_changes(incident)
        self.db.add(IncidentEvent(incident_id=incident.id, actor_id=creator.id, action=IncidentEventAction.CREATED,
                                  changes=changes, created_at=at))
        if self.maintain:
            sla_store.record_change(self.db, incident, IncidentEventAction.CREATED, changes, at)
        self.db.commit()
        return incident

    def change(self, incident: Incident, at: datetime, actor=None, **fields) -> None:
        """One audited update at `at`, with the same diff and classification as the API."""
        before = audit.snapshot(incident)
        for field, value in fields.items():
            if field == "severity":
                value = Severity(value)
            elif field == "status":
                value = IncidentStatus(value)
            setattr(incident, field, value)
        diff = audit.diff(before, audit.snapshot(incident))
        assert diff, "a timeline change must change something"
        action = audit.classify(diff)
        self.db.add(IncidentEvent(incident_id=incident.id, actor_id=(actor or self.staff).id, action=action,
                                  changes=diff, created_at=at))
        if self.maintain:
            sla_store.record_change(self.db, incident, action, diff, at)
        self.db.commit()

    def cycles(self, incident: Incident, now: datetime):
        query = sla_cycles(select(Incident.id.label("id")).where(Incident.id == incident.id), now).subquery()
        return self.db.execute(select(query).order_by(query.c.cycle)).all()

    def current(self, incident: Incident, now: datetime):
        state = current_sla(select(Incident.id.label("id")).where(Incident.id == incident.id), now)
        return self.db.execute(select(state)).one()

    def stored(self, incident: Incident, now: datetime):
        query = sla_store.cycle_rows(select(Incident.id.label("id")).where(Incident.id == incident.id), now).subquery()
        return self.db.execute(select(query).order_by(query.c.cycle)).all()

    def stored_current(self, incident: Incident, now: datetime):
        state = sla_store.current_state(select(Incident.id.label("id")).where(Incident.id == incident.id), now)
        return self.db.execute(select(state)).one()
