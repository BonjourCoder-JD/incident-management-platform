"""SLA cycles, deadlines and states, derived from the audit trail (Milestone 5B).

Nothing about SLA state is stored except escalations: every figure is computed here from
incident_events and the policy versions, so the detail page, list filter, dashboard, CSV export
and escalation worker cannot disagree. Callers pass the candidate incidents already restricted
with incidents._visible_to, and the authoritative `now` (see get_now).

Definitions (docs/PROJECT_SPEC.md, "SLA design"):
- Events are sequenced by id: per incident, writes are serialised by the incident row lock, so id
  order is commit order. Times come from each event's created_at (database clock).
- Cycle 0 starts at the `created` event. Every reopen (status from resolved/closed to
  open/investigating) starts the next cycle. Legacy incidents (no `created` event) are not tracked.
- Acknowledgment: the first event that moves the status away from `open` or sets an assignee
  (both staff-only actions); a staff-created incident with an assignee is acknowledged at
  creation. Comments never count. It is never undone. The response target exists in cycle 0 only.
- Resolution of a cycle: its first event moving the status to resolved or closed.
- Policy version: frozen at cycle start, the latest version with created_at <= the cycle start
  (ties: highest id). Cycles that start before the first version are not tracked.
- Severity: the strictest severity held from the cycle start until the target is satisfied (or
  until now while outstanding). Deadlines are measured from the original cycle start, so a
  severity change can only tighten a target, never reset or relax it.
- Due = cycle start + target. Met = satisfied at or before due; breached = satisfied after due.
  Outstanding: overdue at or after due, approaching from 75% of the target, otherwise on track.
"""
import enum
from datetime import datetime
from typing import Annotated

from fastapi import Depends
from sqlalchemy import DateTime, Select, and_, bindparam, case, exists, func, or_, select, true
from sqlalchemy.dialects.postgresql import aggregate_order_by, array
from sqlalchemy.orm import Session, aliased

from .database import get_db
from .models import IncidentEscalation, Incident, IncidentEvent, IncidentEventAction, Severity, SlaPolicyTarget, SlaPolicyVersion

# Advisory lock keys. Incident writes take the policy lock in shared mode and policy changes take
# it exclusively, so a new version's created_at is always later than the start of any cycle that
# was already committed: an existing cycle can never switch to a newer policy version.
SLA_POLICY_LOCK = 51_000_001
# Held (session level) by the escalation worker so two workers never run at once.
SLA_WORKER_LOCK = 51_000_002

WARNING_PERCENT = 75  # approved D4: "approaching" from 75% of the target
SEVERITY_RANK = {Severity.LOW: 1, Severity.MEDIUM: 2, Severity.HIGH: 3, Severity.CRITICAL: 4}
RANK_SEVERITY = {rank: severity for severity, rank in SEVERITY_RANK.items()}
RESOLVED_VALUES = ("resolved", "closed")
OPEN_VALUES = ("open", "investigating")


class SlaState(str, enum.Enum):
    ON_TRACK = "on_track"
    APPROACHING = "approaching"
    OVERDUE = "overdue"
    MET = "met"
    BREACHED = "breached"
    NOT_TRACKED = "not_tracked"


OUTSTANDING = (SlaState.ON_TRACK.value, SlaState.APPROACHING.value, SlaState.OVERDUE.value)
# Urgency order for an incident's summary: any outstanding target outranks a finished one.
SUMMARY_PRIORITY = {"overdue": 5, "approaching": 4, "on_track": 3, "breached": 2, "met": 1}


# ---- Clock and locks ----------------------------------------------------------------------------

def database_now(db: Session) -> datetime:
    """The authoritative clock: the database's, the same one that stamps every audit event."""
    return db.scalar(select(func.clock_timestamp()))


def get_now(db: Annotated[Session, Depends(get_db)]) -> datetime:
    """FastAPI dependency for "now"; tests override it with a fixed time. Browser time is never used."""
    return database_now(db)


Now = Annotated[datetime, Depends(get_now)]


def lock_policy_shared(db: Session) -> None:
    """Taken by every incident write before it commits an event that can start a cycle."""
    db.execute(select(func.pg_advisory_xact_lock_shared(SLA_POLICY_LOCK)))


def lock_policy_exclusive(db: Session) -> None:
    db.execute(select(func.pg_advisory_xact_lock(SLA_POLICY_LOCK)))


# ---- Query builders -------------------------------------------------------------------------------

def _rank(expression):
    return case(*((expression == severity.value, rank) for severity, rank in SEVERITY_RANK.items()), else_=None)


def _enum_rank(column):
    return case(*((column == severity, rank) for severity, rank in SEVERITY_RANK.items()), else_=None)


def _plus_minutes(start, minutes):
    return start + func.make_interval(0, 0, 0, 0, 0, minutes)


def _plus_warning(start, minutes):
    # 75% of the target, in whole seconds: minutes * 60 * 0.75 = minutes * 45.
    return start + func.make_interval(0, 0, 0, 0, 0, 0, minutes * (60 * WARNING_PERCENT // 100))


def _state(tracked, due, warning, satisfied_at, now):
    return case(
        (~tracked, SlaState.NOT_TRACKED.value),
        (due.is_(None), SlaState.NOT_TRACKED.value),
        (satisfied_at.isnot(None), case((satisfied_at <= due, SlaState.MET.value), else_=SlaState.BREACHED.value)),
        (now >= due, SlaState.OVERDUE.value),
        (now >= warning, SlaState.APPROACHING.value),
        else_=SlaState.ON_TRACK.value,
    )


def sla_cycles(candidates: Select, now: datetime):
    """Every SLA cycle of every candidate incident, with deadlines and states as of `now`.

    `candidates` selects one column of incident ids, already restricted to what the user may see.
    One row per (incident_id, cycle); `last_cycle` is the incident's current cycle. Earlier cycles
    keep their final met/breached outcome, so history is always reconstructible.

    Performance: the relevant events are sorted once by (incident_id, id) and feed a single
    window specification. "Latest value so far" is a running max of id * 8 + value (or of the
    id itself), so nothing is re-sorted; then one aggregate per cycle.
    """
    now_param = bindparam("sla_now", now, type_=DateTime(timezone=True))
    cand = candidates.subquery("sla_candidates")
    e = IncidentEvent
    status_from = e.changes["status"]["from"].astext
    status_to = e.changes["status"]["to"].astext
    is_reopen = func.coalesce(and_(status_from.in_(RESOLVED_VALUES), status_to.in_(OPEN_VALUES)), False)
    is_resolution = func.coalesce(status_to.in_(RESOLVED_VALUES), False)
    is_ack = func.coalesce(or_(and_(status_from == "open", status_to != "open"), e.changes["assignee_id"]["to"].astext.isnot(None)), False)
    is_created = e.action == IncidentEventAction.CREATED
    ev1 = (
        select(
            e.incident_id, e.id, e.created_at,
            is_reopen.label("is_reopen"), is_resolution.label("is_resolution"), is_ack.label("is_ack"), is_created.label("is_created"),
            (e.id * 8 + _rank(e.changes["severity"]["to"].astext)).label("sev_key"),
        )
        .join(cand, cand.c.id == e.incident_id)
        # Only events that can matter: creation, and changes to status, assignee or severity.
        .where(or_(is_created, e.changes.has_any(array(["status", "assignee_id", "severity"]))))
        .subquery("sla_events")
    )
    running = {"partition_by": ev1.c.incident_id, "order_by": ev1.c.id}
    whole = {"partition_by": ev1.c.incident_id}
    ev2 = select(
        ev1.c.incident_id, ev1.c.id, ev1.c.created_at, ev1.c.is_resolution, ev1.c.is_ack,
        func.sum(case((ev1.c.is_reopen, 1), else_=0)).over(**running).label("cycle"),
        (func.max(ev1.c.sev_key).over(**running) % 8).label("sev_at"),
        func.max(case((or_(ev1.c.is_reopen, ev1.c.is_created), ev1.c.id), else_=None)).over(**running).label("start_id"),
        func.max(case((ev1.c.is_resolution, ev1.c.id), else_=None)).over(**running).label("last_res_id"),
        func.max(case((ev1.c.is_ack, ev1.c.id), else_=None)).over(**running).label("last_ack_id"),
        func.sum(case((ev1.c.is_reopen, 1), else_=0)).over(**whole).label("last_cycle"),
        func.bool_or(ev1.c.is_created).over(**whole).label("audited"),
    ).subquery("sla_running")
    # Rows of a cycle up to (not including) its resolution, and rows before the first
    # acknowledgment: the strictest severity of a target is taken over exactly these rows.
    before_resolution = or_(ev2.c.last_res_id.is_(None), ev2.c.last_res_id < ev2.c.start_id)
    before_ack = ev2.c.last_ack_id.is_(None)
    is_start = ev2.c.id == ev2.c.start_id

    def first_time(condition):
        # created_at of the lowest-id matching event (events are sequenced by id).
        return func.array_agg(aggregate_order_by(ev2.c.created_at, ev2.c.id)).filter(condition)[1]

    cycles = (
        select(
            ev2.c.incident_id,
            ev2.c.cycle,
            func.max(ev2.c.last_cycle).label("last_cycle"),
            func.bool_or(ev2.c.audited).label("audited"),
            func.max(ev2.c.created_at).filter(is_start).label("started_at"),
            first_time(ev2.c.is_resolution).label("resolved_at"),
            first_time(ev2.c.is_ack).label("acknowledged_at"),
            func.coalesce(func.max(ev2.c.sev_at).filter(before_resolution), func.max(ev2.c.sev_at).filter(is_start)).label("res_sev"),
            func.coalesce(func.max(ev2.c.sev_at).filter(before_ack), func.max(ev2.c.sev_at).filter(is_start)).label("ack_sev"),
        )
        .group_by(ev2.c.incident_id, ev2.c.cycle)
        .subquery("sla_cycle_events")
    )
    version = aliased(SlaPolicyVersion, name="sla_version")
    # Picked once per cycle with a lateral join, so the state expressions below refer to a plain
    # column instead of re-running the lookup for every reference.
    picked = (
        select(version.id.label("version_id"))
        .where(version.created_at <= cycles.c.started_at)
        .order_by(version.created_at.desc(), version.id.desc())
        .limit(1)
        .lateral("sla_version_pick")
    )
    current_rank = _enum_rank(Incident.severity)  # fallback if no event recorded a severity
    layer = (
        select(
            cycles,
            picked.c.version_id,
            func.coalesce(func.nullif(cycles.c.res_sev, 0), current_rank).label("res_rank"),
            func.coalesce(func.nullif(cycles.c.ack_sev, 0), current_rank).label("ack_rank"),
        )
        .select_from(cycles)
        .join(Incident, Incident.id == cycles.c.incident_id)
        .outerjoin(picked, true())
        # Legacy incidents (no `created` event) have no SLA cycles at all, whatever happened later.
        .where(cycles.c.audited)
        .subquery("sla_cycle_policy")
    )
    res_target = aliased(SlaPolicyTarget, name="sla_res_target")
    ack_target = aliased(SlaPolicyTarget, name="sla_ack_target")
    tracked = and_(layer.c.audited, layer.c.version_id.isnot(None))
    first = layer.c.cycle == 0
    res_minutes = res_target.resolution_minutes
    ack_minutes = case((first, ack_target.response_minutes), else_=None)
    res_due = _plus_minutes(layer.c.started_at, res_minutes)
    res_warning = _plus_warning(layer.c.started_at, res_minutes)
    ack_due = _plus_minutes(layer.c.started_at, ack_minutes)
    ack_warning = _plus_warning(layer.c.started_at, ack_minutes)
    acknowledged_at = case((first, layer.c.acknowledged_at), else_=None)
    return (
        select(
            layer.c.incident_id,
            layer.c.cycle,
            layer.c.last_cycle,
            layer.c.started_at,
            layer.c.version_id,
            tracked.label("tracked"),
            case((first, layer.c.ack_rank), else_=None).label("response_rank"),
            ack_minutes.label("response_minutes"),
            ack_due.label("response_due"),
            ack_warning.label("response_warning"),
            acknowledged_at.label("acknowledged_at"),
            case((first, _state(tracked, ack_due, ack_warning, acknowledged_at, now_param)), else_=None).label("response_state"),
            layer.c.res_rank.label("resolution_rank"),
            res_minutes.label("resolution_minutes"),
            res_due.label("resolution_due"),
            res_warning.label("resolution_warning"),
            layer.c.resolved_at,
            _state(tracked, res_due, res_warning, layer.c.resolved_at, now_param).label("resolution_state"),
        )
        .outerjoin(res_target, and_(res_target.version_id == layer.c.version_id, _enum_rank(res_target.severity) == layer.c.res_rank))
        .outerjoin(ack_target, and_(ack_target.version_id == layer.c.version_id, _enum_rank(ack_target.severity) == layer.c.ack_rank))
    )


def _priority(state):
    return case(*((state == name, rank) for name, rank in SUMMARY_PRIORITY.items()), else_=0)


def current_sla(candidates: Select, now: datetime):
    """One row per candidate incident: its current cycle, the first-cycle response target and a
    summary (the most urgent current target). Response counts in the summary only in cycle 0.

    Filtered aggregates over the cycle rows (no self-join), so the cost stays linear."""
    c = sla_cycles(candidates, now).subquery("sla_all_cycles")
    current = c.c.cycle == c.c.last_cycle
    first = c.c.cycle == 0

    def now_(column):
        return func.max(column).filter(current)

    def first_(column):
        return func.max(column).filter(first)

    per_incident = (
        select(
            c.c.incident_id,
            func.max(c.c.last_cycle).label("cycle"),
            func.bool_or(c.c.tracked).filter(current).label("tracked"),
            now_(c.c.started_at).label("started_at"),
            now_(c.c.version_id).label("version_id"),
            first_(c.c.response_state).label("response_state"),
            first_(c.c.response_due).label("response_due"),
            first_(c.c.acknowledged_at).label("acknowledged_at"),
            func.bool_or(c.c.tracked).filter(first).label("response_tracked"),
            first_(c.c.response_rank).label("response_rank"),
            now_(c.c.resolution_state).label("resolution_state"),
            now_(c.c.resolution_due).label("resolution_due"),
            now_(c.c.resolved_at).label("resolved_at"),
            now_(c.c.resolution_rank).label("resolution_rank"),
        )
        .group_by(c.c.incident_id)
        .subquery("sla_per_incident")
    )
    q = per_incident.c
    response_now = case((q.cycle == 0, q.response_state), else_=None)
    priority = func.greatest(_priority(response_now), _priority(q.resolution_state))
    response_first = and_(
        response_now.in_(OUTSTANDING),
        or_(q.resolution_state.notin_(OUTSTANDING), q.response_due <= q.resolution_due),
    )
    summary_state = case(*((priority == rank, name) for name, rank in SUMMARY_PRIORITY.items()), else_=None)
    escalated = exists().where(IncidentEscalation.incident_id == q.incident_id, IncidentEscalation.cycle == q.cycle)
    return select(
        per_incident,
        summary_state.label("summary_state"),
        case((response_first, "response"), (summary_state.isnot(None), "resolution"), else_=None).label("summary_target"),
        case((response_first, q.response_due), (summary_state.isnot(None), q.resolution_due), else_=None).label("summary_due"),
        escalated.label("escalated"),
    ).subquery("sla_current_state")


def severity_name(rank: int | None) -> Severity | None:
    return RANK_SEVERITY.get(rank) if rank is not None else None


def current_policy(db: Session) -> SlaPolicyVersion | None:
    return db.scalar(select(SlaPolicyVersion).order_by(SlaPolicyVersion.created_at.desc(), SlaPolicyVersion.id.desc()).limit(1))

