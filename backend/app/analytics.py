"""Incident analytics computed with PostgreSQL aggregates.

Definitions (see docs/PROJECT_SPEC.md, "Analytics design"):
- The selected range is calendar dates [start_date, end_date] in the viewer's IANA timezone,
  applied as the half-open timestamp interval [start_date 00:00, end_date + 1 day 00:00).
- Counts, breakdowns and the created trend use incidents.created_at (database-set, reliable
  for legacy incidents too) and the incidents' current status and severity.
- "Current open backlog" ignores the range.
- Resolution = an incident's first audit event whose status change moves it into resolved or
  closed, whichever event type recorded it (status_changed or a multi-field updated event).
- Legacy incidents (no `created` audit event, i.e. created before migration 0003) are excluded
  from resolution metrics, even if later status changes were audited, and counted instead.
- Every query is restricted with incidents._visible_to (reporters: own incidents only).
"""
from datetime import date, timedelta
from typing import Annotated, Iterator

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Connection, Date, DateTime, Select, and_, bindparam, case, cast, exists, extract, func, or_, select, text
from sqlalchemy.orm import aliased
from sqlalchemy.sql.elements import ColumnElement

from .auth import CurrentUser
from .database import engine
from .incidents import _visible_to
from . import sla_store
from .sla import Now
from .models import Incident, IncidentEscalation, IncidentEvent, IncidentEventAction, IncidentSlaCycle, IncidentStatus, Severity, User, UserRole
from .schemas import (
    AnalyticsRange,
    AnalyticsSummary,
    AnalyticsTotals,
    ResolutionMetrics,
    SeverityCount,
    SeverityResolution,
    SlaAnalytics,
    SlaComplianceCounts,
    StatusCount,
    TrendPoint,
)

router = APIRouter(prefix="/analytics", tags=["analytics"])

DEFAULT_RANGE_DAYS = 30
MAX_RANGE_DAYS = 366
MAX_DAILY_BUCKET_DAYS = 92
RESOLVED = (IncidentStatus.RESOLVED, IncidentStatus.CLOSED)
OPEN = (IncidentStatus.OPEN, IncidentStatus.INVESTIGATING)
# Audit diffs store plain strings.
RESOLVED_VALUES = tuple(s.value for s in RESOLVED)
OPEN_VALUES = tuple(s.value for s in OPEN)
BY_FIRST_COLUMN = text("1")  # GROUP BY 1: groups by an expression containing bound parameters



# ---- Read-only snapshot ------------------------------------------------------------------

def get_analytics_connection() -> Iterator[Connection]:
    """A dedicated connection whose transaction is REPEATABLE READ and READ ONLY.

    SET TRANSACTION applies to this transaction only, so the pooled connection and the normal
    request sessions keep their default behaviour. Every figure comes from one snapshot.
    """
    with engine.connect() as conn:
        with conn.begin():
            conn.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            yield conn


AnalyticsConnection = Annotated[Connection, Depends(get_analytics_connection)]


# ---- Range handling ------------------------------------------------------------------------

_timezone_names: frozenset[str] | None = None


def _known_timezones(conn: Connection) -> frozenset[str]:
    global _timezone_names
    if _timezone_names is None:
        _timezone_names = frozenset(conn.execute(text("SELECT name FROM pg_timezone_names")).scalars())
    return _timezone_names


def _range_error(field: str, message: str) -> HTTPException:
    return HTTPException(status_code=422, detail=[{"loc": ["query", field], "msg": message, "type": "value_error"}])


class Window:
    """The validated range: calendar dates plus the half-open UTC instants that bound them."""

    def __init__(self, conn: Connection, start_date: date, end_date: date, tz: str):
        self.start_date, self.end_date, self.tz = start_date, end_date, tz
        self.bucket = "day" if (end_date - start_date).days + 1 <= MAX_DAILY_BUCKET_DAYS else "week"
        row = conn.execute(
            text("SELECT CAST(:start AS timestamp) AT TIME ZONE :tz, CAST(:end AS timestamp) AT TIME ZONE :tz"),
            {"start": start_date, "end": end_date + timedelta(days=1), "tz": tz},
        ).one()
        self.start, self.end = row[0], row[1]  # [start, end)

    def contains(self, column) -> ColumnElement[bool]:
        return and_(column >= self.start, column < self.end)

    def local_bucket(self, column):
        local = func.timezone(self.tz, column)  # wall-clock time in the viewer's zone
        if self.bucket == "day":
            return cast(local, Date)
        return cast(func.date_trunc("week", local), Date)  # ISO weeks start on Monday

    def periods(self) -> list[tuple[date, date, date]]:
        """(bucket key, period_start, period_end) covering the range; partial weeks are clipped."""
        if self.bucket == "day":
            days = (self.end_date - self.start_date).days + 1
            return [(d, d, d) for d in (self.start_date + timedelta(days=i) for i in range(days))]
        week = self.start_date - timedelta(days=self.start_date.weekday())
        periods = []
        while week <= self.end_date:
            periods.append((week, max(week, self.start_date), min(week + timedelta(days=6), self.end_date)))
            week += timedelta(days=7)
        return periods


def resolve_window(conn: Connection, start_date: date | None, end_date: date | None, tz: str) -> Window:
    if tz not in _known_timezones(conn):
        raise _range_error("tz", "Unknown time zone. Use an IANA name such as Europe/London.")
    if end_date is None:
        if start_date is None:
            end_date = conn.execute(text("SELECT CAST(now() AT TIME ZONE :tz AS date)"), {"tz": tz}).scalar_one()
        else:
            end_date = start_date + timedelta(days=DEFAULT_RANGE_DAYS - 1)
    if start_date is None:
        start_date = end_date - timedelta(days=DEFAULT_RANGE_DAYS - 1)
    if start_date > end_date:
        raise _range_error("from", "The start date must be on or before the end date.")
    if (end_date - start_date).days + 1 > MAX_RANGE_DAYS:
        raise _range_error("to", f"The range can be at most {MAX_RANGE_DAYS} days.")
    return Window(conn, start_date, end_date, tz)


# ---- Query builders (each one applies the visibility rule) ------------------------------------

def _resolution_event(alias: str):
    """Status moved into resolved or closed. Literal SQL (fixed alias, no user input) so the
    planner can match the partial indexes built on exactly this predicate."""
    return text(f"({alias}.changes -> 'status' ->> 'to') IN ('resolved', 'closed')")


def first_resolutions_in(user: User, window: Window):
    """Visible incidents whose first audited resolution falls in the range.

    A resolution event in [start, end) is the incident's first one exactly when the incident
    has no resolution event before `start`, so only events near the range are examined.
    Both status_changed and multi-field updated events count.
    """
    resolution = aliased(IncidentEvent, name="resolution_event")
    prior = aliased(IncidentEvent, name="prior_resolution")
    no_earlier = ~exists().where(
        prior.incident_id == resolution.incident_id,
        _resolution_event("prior_resolution"),
        prior.created_at < window.start,
    )
    query = (
        select(resolution.incident_id.label("incident_id"), func.min(resolution.created_at).label("resolved_at"))
        .join(Incident, Incident.id == resolution.incident_id)
        .where(_resolution_event("resolution_event"), window.contains(resolution.created_at), no_earlier)
        .group_by(resolution.incident_id)
    )
    return _visible_to(query, user).subquery("first_resolution")


def resolved_cohort(user: User, window: Window):
    """One row per visible incident first resolved in the range.

    `hours` is NULL for legacy incidents (no `created` audit event), so percentiles and
    COUNT(hours) ignore them; they are counted separately through `legacy`.
    """
    first = first_resolutions_in(user, window)
    # Every audited incident has exactly one `created` event, so this outer join adds no rows
    # and evaluates the legacy test once per incident.
    created = aliased(IncidentEvent, name="created_event")
    legacy = created.id.is_(None)
    # Incidents ever moved from resolved/closed back to open/investigating: one hashed set.
    reopen = aliased(IncidentEvent, name="reopen_event")
    reopened_ids = select(reopen.incident_id).where(
        reopen.changes["status"]["from"].astext.in_(RESOLVED_VALUES),
        reopen.changes["status"]["to"].astext.in_(OPEN_VALUES),
    )
    hours = extract("epoch", first.c.resolved_at - Incident.created_at) / 3600.0
    query = (
        select(
            Incident.severity.label("severity"),
            window.local_bucket(first.c.resolved_at).label("bucket"),
            case((legacy, None), else_=hours).label("hours"),
            legacy.label("legacy"),
            and_(~legacy, Incident.id.in_(reopened_ids)).label("reopened"),
        )
        .join(first, first.c.incident_id == Incident.id)
        .outerjoin(created, and_(created.incident_id == Incident.id, created.action == IncidentEventAction.CREATED))
    )
    return _visible_to(query, user).subquery("resolved_cohort")


def build_queries(user: User, window: Window) -> dict[str, Select]:
    in_range = window.contains(Incident.created_at)
    created_bucket = window.local_bucket(Incident.created_at).label("bucket")
    cohort = resolved_cohort(user, window)
    return {
        "by_status": _visible_to(select(Incident.status, func.count()).where(in_range).group_by(Incident.status), user),
        "by_severity": _visible_to(select(Incident.severity, func.count()).where(in_range).group_by(Incident.severity), user),
        "open_backlog": _visible_to(
            select(Incident.severity, func.count()).where(Incident.status.in_(OPEN)).group_by(Incident.severity), user
        ),
        "created_trend": _visible_to(select(created_bucket, func.count()).where(in_range).group_by(BY_FIRST_COLUMN), user),
        # One pass over the cohort: overall, per severity and per bucket.
        "resolution": select(
            func.grouping(cohort.c.severity).label("by_all_severities"),
            func.grouping(cohort.c.bucket).label("by_all_buckets"),
            cohort.c.severity,
            cohort.c.bucket,
            func.count(cohort.c.hours).label("resolved"),
            func.percentile_cont(0.5).within_group(cohort.c.hours).label("median"),
            func.percentile_cont(0.9).within_group(cohort.c.hours).label("p90"),
            func.count().filter(cohort.c.reopened).label("reopened"),
            func.count().filter(cohort.c.legacy).label("legacy"),
        )
        .select_from(cohort)
        .group_by(text("GROUPING SETS ((), (resolved_cohort.severity), (resolved_cohort.bucket))")),
    }


def sla_queries(user: User, window: Window, now) -> dict[str, Select]:
    """SLA figures from the stored read model; every candidate set is restricted with _visible_to."""
    in_range = _visible_to(select(Incident.id.label("id")).where(window.contains(Incident.created_at)), user)
    first = sla_store.cycle_rows(in_range, now).subquery("sla_first_cycles")
    # Current backlog: open incidents with an outstanding target (an unresolved cycle is always current).
    n = bindparam("sla_now", now, type_=DateTime(timezone=True))
    c = IncidentSlaCycle
    overdue = or_(sla_store.response_overdue(c, n), sla_store.resolution_overdue(c, n))
    approaching = or_(sla_store.response_approaching(c, n), sla_store.resolution_approaching(c, n))
    backlog = _visible_to(
        select(Incident.severity, case((overdue, "overdue"), else_="approaching").label("state"), func.count(func.distinct(Incident.id)))
        .join(c, c.incident_id == Incident.id)
        .where(Incident.status.in_(OPEN), or_(overdue, approaching))
        .group_by(Incident.severity, text("2")),
        user,
    )
    return {
        "first_cycle": select(first.c.response_state, first.c.resolution_state, func.count())
        .where(first.c.cycle == 0)
        .group_by(first.c.response_state, first.c.resolution_state),
        "backlog": backlog,
        "escalations": select(func.count()).select_from(IncidentEscalation).where(window.contains(IncidentEscalation.created_at)),
    }


def _compliance(counts: dict[str, int]) -> SlaComplianceCounts:
    return SlaComplianceCounts(
        met=counts.get("met", 0),
        breached=counts.get("breached", 0),
        outstanding=sum(counts.get(s, 0) for s in ("on_track", "approaching", "overdue")),
        not_tracked=counts.get("not_tracked", 0),
    )


def compute_sla(conn: Connection, user: User, window: Window, now) -> SlaAnalytics:
    q = sla_queries(user, window, now)
    response: dict[str, int] = {}
    resolution: dict[str, int] = {}
    for response_state, resolution_state, count in conn.execute(q["first_cycle"]).all():
        response[response_state] = response.get(response_state, 0) + count
        resolution[resolution_state] = resolution.get(resolution_state, 0) + count
    backlog = {(severity, state): count for severity, state, count in conn.execute(q["backlog"]).all()}
    staff = user.role != UserRole.REPORTER
    return SlaAnalytics(
        response=_compliance(response),
        resolution=_compliance(resolution),
        overdue_by_severity=[SeverityCount(severity=s, count=backlog.get((s, "overdue"), 0)) for s in Severity],
        approaching_by_severity=[SeverityCount(severity=s, count=backlog.get((s, "approaching"), 0)) for s in Severity],
        escalations=conn.execute(q["escalations"]).scalar_one() if staff else None,
    )


def _hours(value) -> float | None:
    # Two decimals (36 s) so short resolutions are not reported as zero.
    return None if value is None else round(float(value), 2)


def compute_summary(conn: Connection, user: User, window: Window, now) -> AnalyticsSummary:
    q = build_queries(user, window)
    by_status = dict(conn.execute(q["by_status"]).all())
    by_severity = dict(conn.execute(q["by_severity"]).all())
    backlog = dict(conn.execute(q["open_backlog"]).all())
    created = dict(conn.execute(q["created_trend"]).all())

    overall = None
    per_severity, resolved_per_bucket = {}, {}
    for row in conn.execute(q["resolution"]).mappings():
        if row["by_all_severities"] and row["by_all_buckets"]:
            overall = row
        elif not row["by_all_severities"]:
            per_severity[row["severity"]] = row
        else:
            resolved_per_bucket[row["bucket"]] = row["resolved"]

    return AnalyticsSummary(
        scope="own" if user.role == UserRole.REPORTER else "all",
        range=AnalyticsRange(start_date=window.start_date, end_date=window.end_date, timezone=window.tz, bucket=window.bucket),
        totals=AnalyticsTotals(
            incidents=sum(by_status.values()),
            open=sum(by_status.get(s, 0) for s in OPEN),
            resolved_or_closed=sum(by_status.get(s, 0) for s in RESOLVED),
        ),
        by_status=[StatusCount(status=s, count=by_status.get(s, 0)) for s in IncidentStatus],
        by_severity=[SeverityCount(severity=s, count=by_severity.get(s, 0)) for s in Severity],
        open_backlog_by_severity=[SeverityCount(severity=s, count=backlog.get(s, 0)) for s in Severity],
        trend=[
            TrendPoint(period_start=start, period_end=end, created=created.get(key, 0), resolved=resolved_per_bucket.get(key, 0))
            for key, start, end in window.periods()
        ],
        resolution=ResolutionMetrics(
            resolved_count=overall["resolved"],
            median_hours=_hours(overall["median"]),
            p90_hours=_hours(overall["p90"]),
            reopened_count=overall["reopened"],
            excluded_legacy=overall["legacy"],
            by_severity=[
                SeverityResolution(
                    severity=s,
                    resolved_count=per_severity[s]["resolved"] if s in per_severity else 0,
                    median_hours=_hours(per_severity[s]["median"]) if s in per_severity else None,
                )
                for s in Severity
            ],
        ),
        sla=compute_sla(conn, user, window, now),
    )


@router.get("/summary", response_model=AnalyticsSummary)
def analytics_summary(
    user: CurrentUser,
    conn: AnalyticsConnection,
    now: Now,
    start_date: Annotated[date | None, Query(alias="from")] = None,
    end_date: Annotated[date | None, Query(alias="to")] = None,
    tz: Annotated[str, Query(max_length=64)] = "UTC",
):
    window = resolve_window(conn, start_date, end_date, tz)
    return compute_summary(conn, user, window, now)
