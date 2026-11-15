"""Stored SLA read model (incident_sla_cycles): maintenance on writes and indexed reads (Milestone 5B).

Write path (record_change): called by every incident create and update after its audit event is
recorded, in the same transaction, under the incident row lock and the shared SLA policy lock. It
applies the change to the incident's current cycle incrementally:
- `created` starts cycle 0 under the policy version in force at that moment;
- a reopen (status from resolved/closed to open/investigating) starts the next cycle and leaves
  every earlier cycle untouched;
- otherwise acknowledgment and resolution are recorded first, then a severity change tightens
  only the targets that are still outstanding (a satisfying event's own severity change never
  counts for that target, exactly as in the audit-derived reference).
This is deliberately a different algorithm from app/sla.py, which derives the same values from
incident_events; tests and `python -m app.cli sla-verify` check that the two always agree.

Read path: one row per cycle with states as of `now`, plus indexed predicates for the list
filters, dashboard figures and the escalation worker.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import DateTime, Select, and_, bindparam, case, exists, func, or_, select, union_all
from sqlalchemy.orm import Session, aliased

from .models import IncidentEscalation, Incident, IncidentEventAction, IncidentSlaCycle, IncidentStatus, SlaPolicyTarget, SlaPolicyVersion
from .sla import OPEN_VALUES, RESOLVED_VALUES, SEVERITY_RANK, SUMMARY_PRIORITY, WARNING_PERCENT, OUTSTANDING, _state, sla_cycles

C = IncidentSlaCycle


# ---- Write path ---------------------------------------------------------------------------------

def _status(changes: dict, key: str):
    return (changes.get("status") or {}).get(key)


def is_reopen(changes: dict) -> bool:
    return _status(changes, "from") in RESOLVED_VALUES and _status(changes, "to") in OPEN_VALUES


def is_resolution(changes: dict) -> bool:
    return _status(changes, "to") in RESOLVED_VALUES


def is_acknowledgment(changes: dict) -> bool:
    moved_from_open = _status(changes, "from") == "open" and _status(changes, "to") not in (None, "open")
    return moved_from_open or (changes.get("assignee_id") or {}).get("to") is not None


def _targets(db: Session, version_id: int | None, rank: int) -> SlaPolicyTarget | None:
    if version_id is None:
        return None
    severity = next(s for s, r in SEVERITY_RANK.items() if r == rank)
    return db.get(SlaPolicyTarget, (version_id, severity))


def _deadline(start: datetime, minutes: int | None) -> tuple[datetime | None, datetime | None]:
    if minutes is None:
        return None, None
    return start + timedelta(minutes=minutes), start + timedelta(seconds=minutes * 60 * WARNING_PERCENT // 100)


def _set_response(db: Session, cycle: IncidentSlaCycle, rank: int) -> None:
    target = _targets(db, cycle.policy_version_id, rank)
    cycle.response_rank = rank
    cycle.response_minutes = target.response_minutes if target else None
    cycle.response_due_at, cycle.response_warning_at = _deadline(cycle.started_at, cycle.response_minutes)


def _set_resolution(db: Session, cycle: IncidentSlaCycle, rank: int) -> None:
    target = _targets(db, cycle.policy_version_id, rank)
    cycle.resolution_rank = rank
    cycle.resolution_minutes = target.resolution_minutes if target else None
    cycle.resolution_due_at, cycle.resolution_warning_at = _deadline(cycle.started_at, cycle.resolution_minutes)


def _start_cycle(db: Session, incident: Incident, number: int, at: datetime) -> IncidentSlaCycle:
    # Frozen at the start: the latest version effective at or before this moment (ties: highest id).
    version_id = db.scalar(
        select(SlaPolicyVersion.id).where(SlaPolicyVersion.created_at <= at)
        .order_by(SlaPolicyVersion.created_at.desc(), SlaPolicyVersion.id.desc()).limit(1)
    )
    rank = SEVERITY_RANK[incident.severity]
    cycle = IncidentSlaCycle(incident_id=incident.id, cycle=number, started_at=at, policy_version_id=version_id)
    _set_resolution(db, cycle, rank)
    if number == 0:
        _set_response(db, cycle, rank)
    db.add(cycle)
    return cycle


def record_change(db: Session, incident: Incident, action: IncidentEventAction, changes: dict, at: datetime) -> None:
    """Applies one audited change, recorded at `at` (its event's created_at), to the read model.

    The caller holds the incident row lock (or has just created the incident) and the shared
    SLA policy lock, and commits this together with the incident change and its audit event."""
    if action == IncidentEventAction.CREATED:
        cycle = _start_cycle(db, incident, 0, at)
        if is_acknowledgment(changes):  # a staff-created incident with an assignee
            cycle.acknowledged_at = at
        db.flush()
        return
    current = db.scalar(select(IncidentSlaCycle).where(IncidentSlaCycle.incident_id == incident.id)
                        .order_by(IncidentSlaCycle.cycle.desc()).limit(1).with_for_update())
    if current is None:
        return  # legacy incident (no `created` event): never tracked
    if is_reopen(changes):
        _start_cycle(db, incident, current.cycle + 1, at)  # earlier cycles stay exactly as they are
        db.flush()
        return
    if current.cycle == 0 and current.acknowledged_at is None and is_acknowledgment(changes):
        current.acknowledged_at = at
    if current.resolved_at is None and is_resolution(changes):
        current.resolved_at = at
    severity = (changes.get("severity") or {}).get("to")
    if severity is not None:
        rank = SEVERITY_RANK[next(s for s in SEVERITY_RANK if s.value == severity)]
        if current.cycle == 0 and current.acknowledged_at is None and rank > (current.response_rank or 0):
            _set_response(db, current, rank)
        if current.resolved_at is None and rank > current.resolution_rank:
            _set_resolution(db, current, rank)
    db.flush()


# ---- Read path ------------------------------------------------------------------------------------

def _tracked(c):
    return c.policy_version_id.isnot(None)


def response_overdue(c, now):
    return and_(c.cycle == 0, _tracked(c), c.acknowledged_at.is_(None), c.response_due_at <= now)


def resolution_overdue(c, now):
    return and_(_tracked(c), c.resolved_at.is_(None), c.resolution_due_at <= now)


def response_approaching(c, now):
    return and_(c.cycle == 0, _tracked(c), c.acknowledged_at.is_(None), c.response_warning_at <= now, c.response_due_at > now)


def resolution_approaching(c, now):
    return and_(_tracked(c), c.resolved_at.is_(None), c.resolution_warning_at <= now, c.resolution_due_at > now)


def _now(now: datetime):
    return bindparam("sla_now", now, type_=DateTime(timezone=True))


def cycle_rows(candidates: Select, now: datetime):
    """Every stored cycle of the candidate incidents, with states, in the reference's column names."""
    n = _now(now)
    cand = candidates.subquery("sla_candidates")
    c = aliased(IncidentSlaCycle, name="sla_cycle")
    tracked = _tracked(c)
    last_cycle = select(func.max(IncidentSlaCycle.cycle)).where(IncidentSlaCycle.incident_id == c.incident_id).correlate(c).scalar_subquery()
    return (
        select(
            c.incident_id,
            c.cycle,
            last_cycle.label("last_cycle"),
            c.started_at,
            c.policy_version_id.label("version_id"),
            tracked.label("tracked"),
            c.response_rank,
            c.response_minutes,
            c.response_due_at.label("response_due"),
            c.response_warning_at.label("response_warning"),
            c.acknowledged_at,
            case((c.cycle == 0, _state(tracked, c.response_due_at, c.response_warning_at, c.acknowledged_at, n)), else_=None).label("response_state"),
            c.resolution_rank,
            c.resolution_minutes,
            c.resolution_due_at.label("resolution_due"),
            c.resolution_warning_at.label("resolution_warning"),
            c.resolved_at,
            _state(tracked, c.resolution_due_at, c.resolution_warning_at, c.resolved_at, n).label("resolution_state"),
        )
        .join(cand, cand.c.id == c.incident_id)
    )


def _priority(state):
    return case(*((state == name, rank) for name, rank in SUMMARY_PRIORITY.items()), else_=0)


def current_state(candidates: Select, now: datetime):
    """One row per candidate incident (current cycle plus first-cycle response), in the same
    columns as app.sla.current_sla. Only the latest cycle is read: no later cycle exists."""
    n = _now(now)
    cand = candidates.subquery("sla_candidates")
    cur = aliased(IncidentSlaCycle, name="sla_current")
    first = aliased(IncidentSlaCycle, name="sla_first")
    later = aliased(IncidentSlaCycle, name="sla_later")
    is_latest = ~exists().where(later.incident_id == cur.incident_id, later.cycle > cur.cycle)
    tracked = _tracked(cur)
    first_tracked = _tracked(first)
    response_state = _state(first_tracked, first.response_due_at, first.response_warning_at, first.acknowledged_at, n)
    resolution_state = _state(tracked, cur.resolution_due_at, cur.resolution_warning_at, cur.resolved_at, n)
    response_now = case((cur.cycle == 0, response_state), else_=None)
    priority = func.greatest(_priority(response_now), _priority(resolution_state))
    response_first = and_(
        response_now.in_(OUTSTANDING),
        or_(resolution_state.notin_(OUTSTANDING), first.response_due_at <= cur.resolution_due_at),
    )
    summary_state = case(*((priority == rank, name) for name, rank in SUMMARY_PRIORITY.items()), else_=None)
    escalated = exists().where(IncidentEscalation.incident_id == cur.incident_id, IncidentEscalation.cycle == cur.cycle)
    return (
        select(
            cur.incident_id,
            cur.cycle,
            tracked.label("tracked"),
            cur.started_at,
            cur.policy_version_id.label("version_id"),
            response_state.label("response_state"),
            first.response_due_at.label("response_due"),
            first.acknowledged_at,
            first_tracked.label("response_tracked"),
            first.response_rank,
            resolution_state.label("resolution_state"),
            cur.resolution_due_at.label("resolution_due"),
            cur.resolved_at,
            cur.resolution_rank,
            summary_state.label("summary_state"),
            case((response_first, "response"), (summary_state.isnot(None), "resolution"), else_=None).label("summary_target"),
            case((response_first, first.response_due_at), (summary_state.isnot(None), cur.resolution_due_at), else_=None).label("summary_due"),
            escalated.label("escalated"),
        )
        .select_from(cur)
        .join(cand, cand.c.id == cur.incident_id)
        .join(first, and_(first.incident_id == cur.incident_id, first.cycle == 0))
        .where(is_latest)
        .subquery("sla_current_state")
    )


def filter_condition(sla: str, now: datetime):
    """WHERE condition on Incident for a list filter, equal to `summary_state == sla` (or the
    escalated flag) of current_state, but written so the partial indexes can be used."""
    n = _now(now)
    c = aliased(IncidentSlaCycle, name="sla_filter_cycle")
    on_incident = c.incident_id == Incident.id
    overdue = exists().where(on_incident, or_(response_overdue(c, n), resolution_overdue(c, n)))
    if sla == "overdue":
        return overdue
    if sla == "approaching":
        return and_(exists().where(on_incident, or_(response_approaching(c, n), resolution_approaching(c, n))), ~overdue)
    if sla == "breached":
        later = aliased(IncidentSlaCycle, name="sla_filter_later")
        return exists().where(
            on_incident,
            _tracked(c),
            c.resolved_at.isnot(None),
            or_(c.resolution_breached, and_(c.cycle == 0, c.response_breached)),
            ~exists().where(later.incident_id == c.incident_id, later.cycle > c.cycle),
        )
    if sla == "escalated":
        # Correlated explicitly to the outer incident (auto-correlation would not reach it from
        # inside the EXISTS, and the subquery would then take the highest cycle of all incidents).
        current_cycle = (
            select(func.max(c.cycle)).where(c.incident_id == Incident.id).correlate(Incident).scalar_subquery()
        )
        return exists().where(IncidentEscalation.incident_id == Incident.id, IncidentEscalation.cycle == current_cycle).correlate(Incident)
    raise ValueError(sla)


OPEN_STATUSES = (IncidentStatus.OPEN, IncidentStatus.INVESTIGATING)


# ---- Consistency check against the audit-derived reference ------------------------------------------

COMPARED = (
    "incident_id", "cycle", "started_at", "version_id", "response_rank", "response_minutes", "response_due", "acknowledged_at",
    "resolution_rank", "resolution_minutes", "resolution_due", "resolved_at",
)


def verify(conn, candidates: Select | None = None, sample: int = 20) -> tuple[int, int, list[int]]:
    """Compares every stored cycle with the values derived from incident_events (app/sla.py).

    Returns (cycles checked, mismatching rows, sample of incident ids). States are not compared:
    both sides compute them from these fields with the same rule."""
    candidates = candidates if candidates is not None else select(Incident.id.label("id"))
    now = datetime.now(timezone.utc)  # irrelevant to the compared fields
    derived = sla_cycles(candidates, now).subquery("derived")
    stored = cycle_rows(candidates, now).subquery("stored")
    d = select(*(derived.c[name] for name in COMPARED))
    st = select(*(stored.c[name] for name in COMPARED))
    differences = union_all(d.except_(st), st.except_(d)).subquery("differences")
    mismatches = conn.execute(select(func.count()).select_from(differences)).scalar_one()
    ids = conn.execute(select(differences.c.incident_id).distinct().order_by(differences.c.incident_id).limit(sample)).scalars().all()
    checked = conn.execute(select(func.count()).select_from(stored)).scalar_one()
    return checked, mismatches, list(ids)
