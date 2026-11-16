"""Automatic SLA escalation (Milestone 5B). Run by `python -m app.cli sla-escalate`.

One bounded run:
1. Takes the worker advisory lock (session level); if another worker holds it, does nothing.
2. Fixes `now` once (database clock unless given) and reads up to `limit` overdue targets with no
   escalation yet from the stored SLA cycles (partial indexes on outstanding deadlines).
3. Processes them in batches of about BATCH_SIZE, one transaction each:
   - locks the batch's incident rows with SELECT ... ORDER BY id FOR UPDATE: the same lock that
     incident updates take, acquired in a deterministic order so concurrent runs cannot deadlock;
   - in the same transaction, rechecks every target against the stored cycle (updated with each
     incident change under that same lock) and inserts only those still outstanding and overdue,
     with ON CONFLICT DO NOTHING on (incident, cycle, target). A target satisfied meanwhile is
     never escalated retrospectively, and repeated or concurrent runs never create duplicates.
Escalations never change the incident, its audit trail or its SLA cycles, and nothing here reads
comments. Logs contain counts and incident ids only.
"""
import logging
import time
from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import Connection, DateTime, Engine, bindparam, cast, exists, func, literal, select, text, tuple_, union_all
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from .database import engine as default_engine
from .models import Incident, IncidentEscalation, IncidentSlaCycle, IncidentStatus, SlaTarget
from .sla import SLA_WORKER_LOCK
from .sla_store import resolution_overdue, response_overdue

logger = logging.getLogger(__name__)

OPEN_STATUSES = (IncidentStatus.OPEN, IncidentStatus.INVESTIGATING)
BATCH_SIZE = 100
DEFAULT_LIMIT = 500  # one bounded run
LOCK_TIMEOUT = "5s"
TARGET_TYPE = IncidentEscalation.__table__.c.target.type  # the sla_target enum


@dataclass
class EscalationRun:
    ran: bool  # False when another worker held the lock
    checked: int = 0
    recorded: int = 0
    skipped: int = 0  # satisfied meanwhile, already escalated, or the batch's locks timed out
    batch_ms: list[float] = field(default_factory=list)


def _not_escalated(c, target: SlaTarget):
    return ~exists().where(IncidentEscalation.incident_id == c.incident_id, IncidentEscalation.cycle == c.cycle,
                           IncidentEscalation.target == target)


def _candidates(now_param):
    c = IncidentSlaCycle
    response = select(c.incident_id, c.cycle, cast(literal(SlaTarget.RESPONSE.value), TARGET_TYPE).label("target"), c.response_due_at.label("due"),
                      c.policy_version_id).where(response_overdue(c, now_param), _not_escalated(c, SlaTarget.RESPONSE))
    resolution = select(c.incident_id, c.cycle, cast(literal(SlaTarget.RESOLUTION.value), TARGET_TYPE).label("target"), c.resolution_due_at.label("due"),
                        c.policy_version_id).where(resolution_overdue(c, now_param), _not_escalated(c, SlaTarget.RESOLUTION))
    return union_all(response, resolution).subquery("due_targets")


def due_targets(conn, now: datetime, limit: int = DEFAULT_LIMIT) -> list[tuple[int, int, SlaTarget]]:
    """Overdue targets with no escalation yet, oldest incidents first."""
    due = _candidates(bindparam("sla_now", now, type_=DateTime(timezone=True)))
    rows = conn.execute(select(due.c.incident_id, due.c.cycle, due.c.target).order_by(due.c.incident_id, due.c.target).limit(limit)).all()
    return [(row.incident_id, row.cycle, SlaTarget(row.target)) for row in rows]


def escalate_batch(db: Session, targets: list[tuple[int, int, SlaTarget]], now: datetime) -> int:
    """Locks the incidents (in id order), rechecks every target under the lock and records the
    still-overdue ones. Returns the number recorded. Does not commit."""
    if not targets:
        return 0
    db.execute(text(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'"))
    ids = sorted({incident_id for incident_id, _, _ in targets})
    db.execute(select(Incident.id).where(Incident.id.in_(ids)).order_by(Incident.id).with_for_update()).all()
    # A fresh snapshot, taken after the locks were granted: changes committed meanwhile are visible.
    due = _candidates(bindparam("sla_now", now, type_=DateTime(timezone=True)))
    wanted = [(incident_id, cycle, target) for incident_id, cycle, target in targets]
    still_due = select(due.c.incident_id, due.c.cycle, due.c.target, due.c.due, due.c.policy_version_id, literal(now, DateTime(timezone=True))).where(
        tuple_(due.c.incident_id, due.c.cycle, due.c.target).in_(wanted)
    )
    inserted = db.execute(
        insert(IncidentEscalation)
        .from_select(["incident_id", "cycle", "target", "deadline_at", "policy_version_id", "created_at"], still_due)
        .on_conflict_do_nothing(constraint="uq_incident_escalations_incident_cycle_target")
        .returning(IncidentEscalation.incident_id, IncidentEscalation.cycle, IncidentEscalation.target)
    ).all()
    for incident_id, cycle, target in inserted:
        logger.info("SLA escalation recorded: incident %s, cycle %s, %s target", incident_id, cycle, target.value)
    return len(inserted)


def escalate_target(db: Session, incident_id: int, cycle: int, target: SlaTarget, now: datetime) -> bool:
    """A batch of one (used by tests and kept for clarity). Does not commit."""
    return escalate_batch(db, [(incident_id, cycle, target)], now) == 1


def run_escalations(bind: Engine | Connection | None = None, now: datetime | None = None, limit: int = DEFAULT_LIMIT,
                    batch_size: int = BATCH_SIZE) -> EscalationRun:
    """One bounded escalation run. `bind` is the engine (production) or a test's connection, in
    which case every commit becomes a savepoint of the test's transaction."""
    bind = bind if bind is not None else default_engine
    owns_connection = isinstance(bind, Engine)
    with bind.connect() if owns_connection else nullcontext(bind) as conn:
        if not conn.execute(select(func.pg_try_advisory_lock(SLA_WORKER_LOCK))).scalar_one():
            logger.info("SLA escalation run skipped: another worker holds the lock")
            return EscalationRun(ran=False)
        try:
            if now is None:
                now = conn.execute(select(func.clock_timestamp())).scalar_one()
            targets = due_targets(conn, now, limit)
            if owns_connection:
                conn.commit()  # ends the read; the session-level advisory lock is kept
            run = EscalationRun(ran=True, checked=len(targets))
            for start in range(0, len(targets), batch_size):
                batch = targets[start:start + batch_size]
                began = time.perf_counter()
                session = Session(bind=bind if owns_connection else conn, autoflush=False, expire_on_commit=False,
                                  join_transaction_mode="create_savepoint")
                with session:
                    try:
                        recorded = escalate_batch(session, batch, now)
                        session.commit()
                    except OperationalError:
                        session.rollback()
                        logger.warning("SLA escalation batch skipped (lock timeout): incidents %s-%s", batch[0][0], batch[-1][0])
                        recorded = 0
                run.recorded += recorded
                run.skipped += len(batch) - recorded
                run.batch_ms.append(round((time.perf_counter() - began) * 1000, 1))
            if run.recorded >= limit:
                # Clearing a backlog: refresh the planner's statistics now rather than waiting for
                # autovacuum, so the next run's anti-join keeps using the unique index (measured:
                # stale statistics during a 29,524-target catch-up made two runs take about 1 s).
                with bind.connect() if owns_connection else nullcontext(conn) as analyze_conn:
                    analyze_conn.execute(text("ANALYZE incident_escalations"))
                    if owns_connection:
                        analyze_conn.commit()
            logger.info("SLA escalation run: %d checked, %d recorded, %d skipped", run.checked, run.recorded, run.skipped)
            return run
        finally:
            if owns_connection:
                # After a failed statement the transaction is aborted and would refuse the unlock, hiding
                # the original error; rolling back keeps the session-level advisory lock until unlocked.
                conn.rollback()
            conn.execute(select(func.pg_advisory_unlock(SLA_WORKER_LOCK)))
            if owns_connection:
                conn.commit()
