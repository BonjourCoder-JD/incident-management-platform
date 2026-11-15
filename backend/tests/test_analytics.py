"""Analytics: exact aggregates, resolution rules, date/timezone handling, isolation and safety."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app import analytics
from app.database import engine
from app.models import Incident, IncidentEvent, IncidentEventAction, IncidentStatus, Severity, User, UserRole

UTC = timezone.utc
# Dates far from any real data, so these tests are unaffected by an existing development database.
DAY = datetime(2001, 3, 5, 12, 0, tzinfo=UTC)  # a Monday
RANGE = {"from": "2001-03-01", "to": "2001-03-31", "tz": "UTC"}


@pytest.fixture
def seed(db, make_user):
    staff = make_user(UserRole.RESPONDER)

    class Seed:
        responder = staff

        def incident(self, reporter, created_at, severity="medium", status="open", audited=True):
            incident = Incident(
                title="Analytics probe", description="Synthetic", severity=Severity(severity), status=IncidentStatus(status),
                reporter_id=reporter.id if reporter else None, created_at=created_at,
                updated_at=created_at + timedelta(days=400),  # deliberately misleading: must never be used
            )
            db.add(incident)
            db.flush()
            if audited:
                self.event(incident, "created", {"status": {"from": None, "to": "open"}}, created_at, actor=reporter or staff)
            return incident

        def event(self, incident, action, changes, at, actor=None):
            db.add(IncidentEvent(incident_id=incident.id, actor_id=(actor or staff).id, action=IncidentEventAction(action), changes=changes, created_at=at))
            db.flush()

        def status(self, incident, frm, to, at, action="status_changed", extra=None):
            self.event(incident, action, {"status": {"from": frm, "to": to}, **(extra or {})}, at)

    yield Seed()
    db.commit()


def summary(client, **params):
    response = client.get("/analytics/summary", params={**RANGE, **params})
    assert response.status_code == 200, response.text
    return response.json()


def counts(items, key):
    return {item[key]: item["count"] for item in items}


# ---- Counts, breakdowns, backlog ----------------------------------------------------------

def test_counts_and_breakdowns_use_creation_date_and_current_state(login_as, seed, responder, reporter):
    client = login_as(responder)
    baseline = summary(client)
    seed.incident(reporter, DAY, "critical", "open")
    seed.incident(reporter, DAY + timedelta(days=1), "high", "investigating")
    seed.incident(reporter, DAY + timedelta(days=2), "low", "resolved")
    seed.incident(reporter, DAY + timedelta(days=3), "low", "closed")
    seed.incident(reporter, datetime(2001, 2, 20, tzinfo=UTC), "critical", "open")  # before the range

    data = summary(client)
    assert data["scope"] == "all"
    assert data["totals"] == {"incidents": 4, "open": 2, "resolved_or_closed": 2}
    assert counts(data["by_status"], "status") == {"open": 1, "investigating": 1, "resolved": 1, "closed": 1}
    assert counts(data["by_severity"], "severity") == {"low": 2, "medium": 0, "high": 1, "critical": 1}
    # Current open backlog ignores the range: the February incident counts.
    before = counts(baseline["open_backlog_by_severity"], "severity")
    after = counts(data["open_backlog_by_severity"], "severity")
    assert {k: after[k] - before[k] for k in after} == {"low": 0, "medium": 0, "high": 1, "critical": 2}


def test_empty_range_returns_zeros_and_no_timings(login_as, reporter):
    data = summary(login_as(reporter))
    assert data["scope"] == "own"
    assert data["totals"] == {"incidents": 0, "open": 0, "resolved_or_closed": 0}
    assert all(point["created"] == 0 and point["resolved"] == 0 for point in data["trend"])
    assert data["resolution"]["resolved_count"] == 0
    assert data["resolution"]["median_hours"] is None and data["resolution"]["p90_hours"] is None
    assert all(s["median_hours"] is None for s in data["resolution"]["by_severity"])


# ---- Date ranges, time zones and buckets -----------------------------------------------------

def test_range_is_half_open_in_the_viewer_time_zone(login_as, seed, reporter):
    client = login_as(reporter)
    # Tokyo is UTC+9: 10 March 2001 local is [9 March 15:00 UTC, 10 March 15:00 UTC).
    seed.incident(reporter, datetime(2001, 3, 9, 15, 0, tzinfo=UTC))                          # first instant: included
    seed.incident(reporter, datetime(2001, 3, 10, 14, 59, 59, 999999, tzinfo=UTC))            # last instant: included
    seed.incident(reporter, datetime(2001, 3, 10, 15, 0, tzinfo=UTC))                         # next local day: excluded
    seed.incident(reporter, datetime(2001, 3, 9, 14, 59, 59, tzinfo=UTC))                     # previous local day: excluded
    tokyo = summary(client, **{"from": "2001-03-10", "to": "2001-03-10", "tz": "Asia/Tokyo"})
    assert tokyo["totals"]["incidents"] == 2
    assert tokyo["range"] == {"start_date": "2001-03-10", "end_date": "2001-03-10", "timezone": "Asia/Tokyo", "bucket": "day"}
    utc = summary(client, **{"from": "2001-03-10", "to": "2001-03-10", "tz": "UTC"})
    assert utc["totals"]["incidents"] == 2  # a different pair of incidents
    assert summary(client, **{"from": "2001-03-09", "to": "2001-03-11", "tz": "Asia/Tokyo"})["totals"]["incidents"] == 4


def test_daily_buckets_follow_local_dates_across_dst(login_as, seed, reporter):
    client = login_as(reporter)
    # US daylight saving began on 1 April 2001 (02:00 EST -> 03:00 EDT).
    seed.incident(reporter, datetime(2001, 4, 1, 4, 30, tzinfo=UTC))   # 31 March 23:30 EST
    seed.incident(reporter, datetime(2001, 4, 2, 3, 30, tzinfo=UTC))   # 1 April 23:30 EDT
    seed.incident(reporter, datetime(2001, 4, 2, 4, 30, tzinfo=UTC))   # 2 April 00:30 EDT
    data = summary(client, **{"from": "2001-03-31", "to": "2001-04-02", "tz": "America/New_York"})
    assert [(p["period_start"], p["period_end"], p["created"]) for p in data["trend"]] == [
        ("2001-03-31", "2001-03-31", 1), ("2001-04-01", "2001-04-01", 1), ("2001-04-02", "2001-04-02", 1),
    ]


def test_daily_trend_is_zero_filled(login_as, seed, reporter):
    client = login_as(reporter)
    seed.incident(reporter, datetime(2001, 3, 1, 9, tzinfo=UTC))
    seed.incident(reporter, datetime(2001, 3, 3, 9, tzinfo=UTC))
    seed.incident(reporter, datetime(2001, 3, 3, 18, tzinfo=UTC))
    data = summary(client, **{"from": "2001-03-01", "to": "2001-03-05"})
    assert [p["created"] for p in data["trend"]] == [1, 0, 2, 0, 0]
    assert len(data["trend"]) == 5


def test_weekly_buckets_are_iso_weeks_clipped_to_the_range(login_as, seed, reporter):
    client = login_as(reporter)
    # 3 January 2001 is a Wednesday; 120 days ends Thursday 2 May 2001 -> weekly buckets.
    seed.incident(reporter, datetime(2001, 1, 7, 23, 0, tzinfo=UTC))   # Sunday: first (partial) week
    seed.incident(reporter, datetime(2001, 1, 8, 0, 0, tzinfo=UTC))    # Monday: second week
    seed.incident(reporter, datetime(2001, 5, 2, 10, 0, tzinfo=UTC))   # last (partial) week
    data = summary(client, **{"from": "2001-01-03", "to": "2001-05-02"})
    assert data["range"]["bucket"] == "week"
    trend = data["trend"]
    assert (trend[0]["period_start"], trend[0]["period_end"], trend[0]["created"]) == ("2001-01-03", "2001-01-07", 1)
    assert (trend[1]["period_start"], trend[1]["period_end"], trend[1]["created"]) == ("2001-01-08", "2001-01-14", 1)
    assert (trend[-1]["period_start"], trend[-1]["period_end"], trend[-1]["created"]) == ("2001-04-30", "2001-05-02", 1)
    middle = trend[1:-1]
    assert all(datetime.fromisoformat(p["period_start"]).weekday() == 0 for p in middle)
    assert all((datetime.fromisoformat(p["period_end"]) - datetime.fromisoformat(p["period_start"])).days == 6 for p in middle)
    assert sum(p["created"] for p in trend) == 3


def test_92_days_is_daily_and_93_days_is_weekly(login_as, reporter):
    client = login_as(reporter)
    assert summary(client, **{"from": "2001-01-01", "to": "2001-04-02"})["range"]["bucket"] == "day"   # 92 days
    assert summary(client, **{"from": "2001-01-01", "to": "2001-04-03"})["range"]["bucket"] == "week"  # 93 days


# ---- Resolution metrics from the audit trail ---------------------------------------------------

def test_resolution_metrics_come_from_audit_events(login_as, seed, responder, reporter):
    client = login_as(reporter)
    t = DAY
    a = seed.incident(reporter, t, "high")
    seed.status(a, "open", "resolved", t + timedelta(hours=10))
    b = seed.incident(reporter, t, "high")  # resolved inside a multi-field "updated" event
    seed.status(b, "open", "closed", t + timedelta(hours=20), action="updated", extra={"title": {"from": "x", "to": "y"}})
    c = seed.incident(reporter, t, "low")   # reopened: first resolution counts
    seed.status(c, "investigating", "resolved", t + timedelta(hours=30))
    seed.status(c, "resolved", "investigating", t + timedelta(hours=31))
    seed.status(c, "investigating", "resolved", t + timedelta(hours=40))
    d = seed.incident(reporter, t, "low")   # never resolved
    seed.status(d, "open", "investigating", t + timedelta(hours=5))
    e = seed.incident(reporter, datetime(2001, 2, 1, tzinfo=UTC))  # first resolved before the range
    seed.status(e, "open", "resolved", datetime(2001, 2, 2, tzinfo=UTC))
    seed.status(e, "resolved", "open", datetime(2001, 2, 3, tzinfo=UTC))
    seed.status(e, "open", "resolved", t + timedelta(hours=50))

    res = summary(client)["resolution"]
    assert res["resolved_count"] == 3
    assert res["median_hours"] == 20.0
    assert res["p90_hours"] == 28.0          # percentile_cont(0.9) of [10, 20, 30]
    assert res["reopened_count"] == 1
    assert res["excluded_legacy"] == 0
    by = {s["severity"]: (s["resolved_count"], s["median_hours"]) for s in res["by_severity"]}
    assert by == {"low": (1, 30.0), "medium": (0, None), "high": (2, 15.0), "critical": (0, None)}
    resolved_trend = {p["period_start"]: p["resolved"] for p in summary(client)["trend"] if p["resolved"]}
    # 5 March 12:00 UTC + 10 h is still 5 March; +20 h and +30 h fall on 6 March.
    assert resolved_trend == {"2001-03-05": 1, "2001-03-06": 2}


def test_status_change_inside_an_updated_event_counts_as_resolution(login_as, seed, reporter):
    incident = seed.incident(reporter, DAY)
    seed.status(incident, "investigating", "resolved", DAY + timedelta(hours=6), action="updated",
                extra={"severity": {"from": "medium", "to": "low"}, "assignee_id": {"from": None, "to": seed.responder.id}})
    res = summary(login_as(reporter))["resolution"]
    assert (res["resolved_count"], res["median_hours"]) == (1, 6.0)


def test_legacy_incidents_are_excluded_from_timings_but_counted(login_as, seed, responder):
    client = login_as(responder)
    baseline = summary(client)
    legacy = seed.incident(None, DAY, "critical", "resolved", audited=False)
    # Partial later history must not make it eligible.
    seed.status(legacy, "open", "investigating", DAY + timedelta(hours=1))
    seed.status(legacy, "investigating", "resolved", DAY + timedelta(hours=2))
    data = summary(client)
    assert data["resolution"]["excluded_legacy"] - baseline["resolution"]["excluded_legacy"] == 1
    assert data["resolution"]["resolved_count"] == baseline["resolution"]["resolved_count"]
    assert data["resolution"]["median_hours"] == baseline["resolution"]["median_hours"]
    assert sum(p["resolved"] for p in data["trend"]) == sum(p["resolved"] for p in baseline["trend"])
    # ...while it still counts as an incident created in the range.
    assert data["totals"]["incidents"] - baseline["totals"]["incidents"] == 1


# ---- Visibility -------------------------------------------------------------------------------------

def test_reporters_see_only_their_own_incidents_and_events(login_as, seed, reporter, other_reporter, responder):
    mine = seed.incident(reporter, DAY, "low", "resolved")
    seed.status(mine, "open", "resolved", DAY + timedelta(hours=4))
    for hours in (100, 200):  # the other reporter's incidents would shift every figure if they leaked
        theirs = seed.incident(other_reporter, DAY + timedelta(days=1), "critical", "resolved")
        seed.status(theirs, "open", "resolved", DAY + timedelta(days=1, hours=hours))
    seed.status(theirs, "resolved", "open", DAY + timedelta(days=20))
    seed.incident(other_reporter, datetime(2000, 6, 1, tzinfo=UTC), "high", "open")  # backlog outside range
    legacy = seed.incident(None, DAY, "medium", "resolved", audited=False)
    seed.status(legacy, "open", "resolved", DAY + timedelta(hours=1))

    own = summary(login_as(reporter))
    assert own["scope"] == "own"
    assert own["totals"] == {"incidents": 1, "open": 0, "resolved_or_closed": 1}
    assert counts(own["by_severity"], "severity") == {"low": 1, "medium": 0, "high": 0, "critical": 0}
    assert sum(counts(own["open_backlog_by_severity"], "severity").values()) == 0
    assert sum(p["created"] for p in own["trend"]) == 1 and sum(p["resolved"] for p in own["trend"]) == 1
    assert own["resolution"] | {"by_severity": None} == {
        "resolved_count": 1, "median_hours": 4.0, "p90_hours": 4.0, "reopened_count": 0, "excluded_legacy": 0, "by_severity": None,
    }

    other = summary(login_as(other_reporter))
    assert other["totals"]["incidents"] == 2 and other["resolution"]["median_hours"] == 150.0  # median of 100 h and 200 h
    assert other["resolution"]["reopened_count"] == 1
    assert counts(other["open_backlog_by_severity"], "severity")["high"] == 1

    staff = summary(login_as(responder))
    assert staff["scope"] == "all"
    assert staff["totals"]["incidents"] >= 4
    assert staff["resolution"]["resolved_count"] >= 3 and staff["resolution"]["excluded_legacy"] >= 1


@pytest.mark.parametrize("role", [UserRole.RESPONDER, UserRole.ADMIN])
def test_staff_scope(role, make_user, login_as):
    assert summary(login_as(make_user(role)))["scope"] == "all"


# ---- Validation, access and safety --------------------------------------------------------------------

def test_requires_authentication(client):
    assert client.get("/analytics/summary").status_code == 401


@pytest.mark.parametrize("params, field", [
    ({"from": "2001-03-10", "to": "2001-03-09"}, "from"),
    ({"from": "2001-01-01", "to": "2002-01-02"}, "to"),               # 367 days
    ({"tz": "Mars/Olympus_Mons"}, "tz"),
    ({"tz": "UTC'; DROP TABLE incidents; --"}, "tz"),
    ({"from": "2001-13-01"}, "from"),
    ({"tz": "x" * 65}, "tz"),
])
def test_invalid_ranges_are_rejected(params, field, login_as, reporter):
    response = login_as(reporter).get("/analytics/summary", params={**RANGE, **params})
    assert response.status_code == 422, response.text
    assert response.json()["detail"][0]["loc"][-1] == field
    assert "DROP TABLE" not in response.text


def test_366_days_is_allowed_and_defaults_cover_30_days(login_as, reporter):
    client = login_as(reporter)
    assert client.get("/analytics/summary", params={"from": "2001-01-01", "to": "2002-01-01"}).status_code == 200
    data = client.get("/analytics/summary", params={"tz": "Pacific/Auckland"}).json()
    start, end = (datetime.fromisoformat(data["range"][k]) for k in ("start_date", "end_date"))
    assert (end - start).days == 29 and data["range"]["timezone"] == "Pacific/Auckland"
    only_from = client.get("/analytics/summary", params={"from": "2001-03-01"}).json()["range"]
    assert (only_from["start_date"], only_from["end_date"]) == ("2001-03-01", "2001-03-30")


def test_no_write_methods(login_as, responder):
    client = login_as(responder)
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        assert client.request(method, "/analytics/summary", json={}).status_code == 405


def test_real_dependency_uses_a_read_only_repeatable_read_snapshot():
    dependency = analytics.get_analytics_connection()
    conn = next(dependency)
    try:
        assert conn.execute(text("SHOW transaction_isolation")).scalar_one() == "repeatable read"
        assert conn.execute(text("SHOW transaction_read_only")).scalar_one() == "on"
        with pytest.raises(DBAPIError, match="read-only transaction"):
            conn.execute(text("UPDATE incidents SET title = title WHERE false"))
    finally:
        dependency.close()
    # The setting was transaction-scoped: pooled connections are back to normal afterwards.
    with engine.connect() as normal:
        assert normal.execute(text("SHOW transaction_read_only")).scalar_one() == "off"
        assert normal.execute(text("SHOW transaction_isolation")).scalar_one() == "read committed"


def test_analytics_queries_apply_the_visibility_rule():
    """Structural guard: every incident-backed query is filtered for a reporter."""
    reporter = User(id=987654, role=UserRole.REPORTER)
    window = type("W", (), {})()
    window.start, window.end, window.tz, window.bucket = DAY, DAY + timedelta(days=1), "UTC", "day"
    window.contains = analytics.Window.contains.__get__(window)
    window.local_bucket = analytics.Window.local_bucket.__get__(window)
    for name, query in analytics.build_queries(reporter, window).items():
        sql = str(query.compile(compile_kwargs={"literal_binds": True}))
        # For "resolution", both the first-resolution subquery and the cohort are filtered.
        expected = 2 if name == "resolution" else 1
        assert sql.count("reporter_id = 987654") >= expected, (name, sql)
