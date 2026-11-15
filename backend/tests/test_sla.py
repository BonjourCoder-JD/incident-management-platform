"""SLA cycles, acknowledgment, reopening, severity and policy rules, and the SLA API (Milestone 5B).

Every time comes from an explicit timestamp or the injected clock; nothing depends on wall time.
"""
import csv
import io
from datetime import timedelta

import pytest
from sqlalchemy import event, func, select, text

from app.main import app
from app.models import CommentVisibility, Incident, IncidentComment, IncidentEscalation, Severity, SlaTarget
from app import sla_store
from app.sla import get_now
from sla_support import T0, US, Timeline, minutes

CANARY = "CANARY-sla-internal-4b1e"


@pytest.fixture
def tl(db, reporter, responder, connection):
    timeline = Timeline(db, reporter, responder)
    timeline.policy(T0 - timedelta(days=1))
    yield timeline
    # Every scenario doubles as a consistency check: the stored read model, maintained by the
    # write path, must equal the values derived independently from incident_events.
    checked, mismatches, ids = sla_store.verify(connection)
    assert mismatches == 0, f"stored SLA cycles differ from the audit-derived reference for incidents {ids}"


@pytest.fixture
def frozen():
    """Sets the API's clock: `frozen(at)`."""
    def freeze(at):
        app.dependency_overrides[get_now] = lambda: at
    yield freeze
    app.dependency_overrides.pop(get_now, None)


# ---- Exact boundaries -------------------------------------------------------------------------

@pytest.mark.parametrize("offset, response_state", [
    (minutes(45) - US, "on_track"),
    (minutes(45), "approaching"),      # 75% of 60 minutes
    (minutes(60) - US, "approaching"),
    (minutes(60), "overdue"),          # due exactly now: overdue
    (minutes(60) + US, "overdue"),
])
def test_outstanding_response_states_at_exact_boundaries(offset, response_state, tl):
    incident = tl.incident(severity="high")
    [cycle] = tl.cycles(incident, T0 + offset)
    assert (cycle.response_state, cycle.response_due, cycle.response_warning) == (response_state, T0 + minutes(60), T0 + minutes(45))
    assert cycle.resolution_state == "on_track"


@pytest.mark.parametrize("acknowledged_after, state", [(minutes(60) - US, "met"), (minutes(60), "met"), (minutes(60) + US, "breached")])
def test_satisfied_at_or_before_the_deadline_is_met(acknowledged_after, state, tl):
    incident = tl.incident(severity="high")
    tl.change(incident, T0 + acknowledged_after, status="investigating")
    [cycle] = tl.cycles(incident, T0 + timedelta(days=30))
    assert (cycle.response_state, cycle.acknowledged_at) == (state, T0 + acknowledged_after)


@pytest.mark.parametrize("resolved_after, state", [(minutes(480), "met"), (minutes(480) + US, "breached")])
def test_resolution_boundary(resolved_after, state, tl):
    incident = tl.incident(severity="high")
    tl.change(incident, T0 + minutes(5), status="investigating")
    tl.change(incident, T0 + resolved_after, status="resolved")
    [cycle] = tl.cycles(incident, T0 + timedelta(days=30))
    assert (cycle.resolution_state, cycle.resolved_at, cycle.resolution_due) == (state, T0 + resolved_after, T0 + minutes(480))


def test_resolution_warning_is_75_percent_of_the_target(tl):
    incident = tl.incident(severity="high")
    tl.change(incident, T0 + minutes(1), status="investigating")
    assert tl.cycles(incident, T0 + minutes(360) - US)[0].resolution_state == "on_track"
    assert tl.cycles(incident, T0 + minutes(360))[0].resolution_state == "approaching"
    assert tl.cycles(incident, T0 + minutes(480))[0].resolution_state == "overdue"


# ---- Acknowledgment ---------------------------------------------------------------------------

@pytest.mark.parametrize("change", [{"status": "investigating"}, {"status": "resolved"}, {"status": "closed"}, {"assignee_id": "staff"}])
def test_first_staff_status_change_or_assignment_acknowledges(change, tl, responder):
    incident = tl.incident()
    value = {k: (responder.id if v == "staff" else v) for k, v in change.items()}
    tl.change(incident, T0 + minutes(10), **value)
    assert tl.cycles(incident, T0 + minutes(20))[0].acknowledged_at == T0 + minutes(10)


def test_staff_created_incident_with_an_assignee_is_acknowledged_at_creation(tl, responder):
    incident = tl.incident(assignee=responder)
    [cycle] = tl.cycles(incident, T0 + minutes(1))
    assert (cycle.acknowledged_at, cycle.response_state) == (T0, "met")


def test_comments_internal_notes_and_reporter_edits_do_not_acknowledge(tl, db, reporter, responder):
    incident = tl.incident()
    for visibility in CommentVisibility:
        db.add(IncidentComment(incident_id=incident.id, author_id=responder.id, visibility=visibility, body="Looking", created_at=T0 + minutes(1)))
    db.commit()
    tl.change(incident, T0 + minutes(2), actor=reporter, title="Edited by the reporter", description="More detail")
    tl.change(incident, T0 + minutes(3), actor=reporter, severity="medium")
    [cycle] = tl.cycles(incident, T0 + minutes(61))
    assert (cycle.acknowledged_at, cycle.response_state) == (None, "overdue")


def test_acknowledgment_is_never_reversed(tl, responder):
    incident = tl.incident()
    tl.change(incident, T0 + minutes(10), status="investigating", assignee_id=responder.id)
    tl.change(incident, T0 + minutes(20), status="open")
    tl.change(incident, T0 + minutes(30), assignee_id=None)
    [cycle] = tl.cycles(incident, T0 + timedelta(days=1))
    assert (cycle.acknowledged_at, cycle.response_state) == (T0 + minutes(10), "met")


# ---- Reopened incidents ------------------------------------------------------------------------

@pytest.mark.parametrize("done", ["resolved", "closed"])
@pytest.mark.parametrize("reopened_to", ["open", "investigating"])
def test_each_reopen_starts_a_new_resolution_cycle_and_keeps_earlier_outcomes(done, reopened_to, tl):
    incident = tl.incident(severity="high")
    tl.change(incident, T0 + minutes(10), status="investigating")
    tl.change(incident, T0 + minutes(100), status=done)  # met (due T0 + 480 min)
    reopen_at = T0 + timedelta(days=2)
    tl.change(incident, reopen_at, status=reopened_to)
    first, second = tl.cycles(incident, reopen_at + minutes(30))
    assert (first.cycle, first.resolution_state, first.response_state, first.resolved_at) == (0, "met", "met", T0 + minutes(100))
    assert (second.cycle, second.started_at, second.resolution_due) == (1, reopen_at, reopen_at + minutes(480))
    assert (second.resolution_state, second.response_state, second.response_minutes) == ("on_track", None, None)
    assert first.last_cycle == second.last_cycle == 1


def test_earlier_cycle_results_are_reconstructible_and_never_change(tl):
    incident = tl.incident(severity="high")
    tl.change(incident, T0 + minutes(10), status="investigating")
    tl.change(incident, T0 + minutes(600), status="resolved")  # breached (due 480)
    later = T0 + timedelta(days=1)
    before = tl.cycles(incident, later)
    tl.change(incident, later, status="investigating")
    tl.change(incident, later + minutes(60), status="resolved")
    tl.change(incident, later + timedelta(days=1), status="open")
    after = tl.cycles(incident, later + timedelta(days=5))
    assert [c.cycle for c in after] == [0, 1, 2]
    assert [c.resolution_state for c in after] == ["breached", "met", "overdue"]
    for column in ("started_at", "resolved_at", "resolution_due", "resolution_state", "response_state", "acknowledged_at", "version_id"):
        assert getattr(after[0], column) == getattr(before[0], column), column


@pytest.mark.parametrize("change", [
    [("investigating", 10), ("open", 20)],             # never resolved: not a reopen
    [("investigating", 10), ("resolved", 30), ("closed", 40)],  # resolved to closed: same cycle
])
def test_moves_that_are_not_reopens_keep_one_cycle(change, tl):
    incident = tl.incident()
    for status, after in change:
        tl.change(incident, T0 + minutes(after), status=status)
    assert [c.cycle for c in tl.cycles(incident, T0 + timedelta(days=1))] == [0]


# ---- Severity -----------------------------------------------------------------------------------

def test_raising_severity_tightens_the_deadline_from_the_original_start(tl):
    incident = tl.incident(severity="medium")  # 240 / 4320
    tl.change(incident, T0 + minutes(30), severity="critical")  # 15 / 240
    [cycle] = tl.cycles(incident, T0 + minutes(31))
    assert (cycle.response_due, cycle.resolution_due) == (T0 + minutes(15), T0 + minutes(240))
    assert cycle.response_state == "overdue"  # already past the stricter deadline: no reset


def test_lowering_severity_never_relaxes_a_target(tl):
    incident = tl.incident(severity="critical")
    tl.change(incident, T0 + minutes(5), severity="low")
    [cycle] = tl.cycles(incident, T0 + minutes(16))
    assert (cycle.response_due, cycle.response_state, cycle.response_rank) == (T0 + minutes(15), "overdue", 4)


def test_severity_changes_after_a_target_is_satisfied_do_not_change_its_outcome(tl):
    incident = tl.incident(severity="low")
    tl.change(incident, T0 + minutes(100), status="investigating")  # met (low response 1440)
    tl.change(incident, T0 + minutes(200), severity="critical")
    [cycle] = tl.cycles(incident, T0 + minutes(300))
    assert (cycle.response_state, cycle.response_due) == ("met", T0 + minutes(1440))
    # The resolution target is still open, so the stricter severity applies to it.
    assert (cycle.resolution_due, cycle.resolution_state) == (T0 + minutes(240), "overdue")


def test_a_reopened_cycle_starts_from_the_severity_in_force_at_the_reopen(tl):
    incident = tl.incident(severity="critical")
    tl.change(incident, T0 + minutes(1), status="resolved")
    tl.change(incident, T0 + minutes(2), severity="low")
    reopen = T0 + timedelta(days=1)
    tl.change(incident, reopen, status="open")
    second = tl.cycles(incident, reopen)[1]
    assert (second.resolution_rank, second.resolution_due) == (1, reopen + minutes(10080))


# ---- Policy versions -------------------------------------------------------------------------------

def test_policy_version_is_frozen_at_cycle_start(tl):
    incident = tl.incident(severity="high")
    tl.policy(T0 + minutes(1), targets={s: (1, 2) for s in ("critical", "high", "medium", "low")})
    [cycle] = tl.cycles(incident, T0 + minutes(10))
    assert (cycle.response_due, cycle.response_state) == (T0 + minutes(60), "on_track")
    # A cycle that starts after the change uses the new version.
    tl.change(incident, T0 + minutes(20), status="resolved")
    tl.change(incident, T0 + minutes(30), status="open")
    second = tl.cycles(incident, T0 + minutes(31.5))[1]  # 75% of the new 2-minute target
    assert (second.resolution_due, second.resolution_state) == (T0 + minutes(32), "approaching")


def test_policy_selection_at_exact_timestamp_boundaries(tl):
    exact = tl.policy(T0, targets={s: (5, 10) for s in ("critical", "high", "medium", "low")})
    tl.policy(T0 + US, targets={s: (7, 14) for s in ("critical", "high", "medium", "low")})
    incident = tl.incident(at=T0)
    assert tl.cycles(incident, T0)[0].version_id == exact.id  # created_at == cycle start: applies
    # Two versions at the same instant: the higher id wins, every time.
    same_a = tl.policy(T0 + timedelta(hours=1), targets={s: (20, 40) for s in ("critical", "high", "medium", "low")})
    same_b = tl.policy(T0 + timedelta(hours=1), targets={s: (30, 60) for s in ("critical", "high", "medium", "low")})
    later = tl.incident(at=T0 + timedelta(hours=1))
    assert same_b.id > same_a.id
    assert {tl.cycles(later, T0 + timedelta(hours=2))[0].version_id for _ in range(3)} == {same_b.id}


def test_cycles_before_any_policy_and_legacy_incidents_are_not_tracked(tl, db):
    old = tl.incident(at=T0 - timedelta(days=3650))  # before every version, including migration 0007's
    [cycle] = tl.cycles(old, T0)
    assert (cycle.tracked, cycle.response_state, cycle.resolution_state, cycle.version_id) == (False, "not_tracked", "not_tracked", None)
    legacy = Incident(title="Legacy", description="Before audit", severity=Severity.LOW, created_at=T0, updated_at=T0)
    db.add(legacy)
    db.commit()
    assert tl.cycles(legacy, T0) == []  # no audit events: nothing to track


# ---- Incident writes and the policy lock ------------------------------------------------------------

def test_incident_writes_take_the_shared_policy_lock(login_as, responder, connection):
    client = login_as(responder)
    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(connection, "before_cursor_execute", capture)
    try:
        created = client.post("/incidents", json={"title": "Lock", "description": "Synthetic", "severity": "low"}).json()
        client.patch(f"/incidents/{created['id']}", json={"status": "resolved"})
    finally:
        event.remove(connection, "before_cursor_execute", capture)
    assert sum("pg_advisory_xact_lock_shared" in s for s in statements) == 2


# ---- API -------------------------------------------------------------------------------------------

def _api_incident(client, severity="high"):
    response = client.post("/incidents", json={"title": "SLA API incident", "description": "Synthetic", "severity": severity})
    assert response.status_code == 201
    return response.json()


def _created_at(db, incident_id):
    return db.scalar(select(Incident.created_at).where(Incident.id == incident_id))


def test_incident_sla_endpoint_uses_the_server_clock_and_hides_escalations_from_reporters(login_as, reporter, responder, db, frozen):
    reporter_client = login_as(reporter)
    incident = _api_incident(reporter_client)
    created = _created_at(db, incident["id"])
    frozen(created + minutes(61))  # default policy: high response 60 min
    db.add(IncidentEscalation(incident_id=incident["id"], cycle=0, target=SlaTarget.RESPONSE, deadline_at=created + minutes(60),
                              policy_version_id=db.scalar(text("SELECT min(id) FROM sla_policy_versions")), created_at=created + minutes(61)))
    db.commit()

    mine = reporter_client.get(f"/incidents/{incident['id']}/sla").json()
    assert mine["now"].startswith((created + minutes(61)).isoformat()[:19])
    assert (mine["tracked"], mine["current_cycle"], mine["escalations"]) == (True, 0, None)
    [cycle] = mine["cycles"]
    assert cycle["response"]["state"] == "overdue" and cycle["response"]["target_minutes"] == 60
    assert cycle["response"]["severity"] == "high" and cycle["resolution"]["state"] == "on_track"

    staff_view = login_as(responder).get(f"/incidents/{incident['id']}/sla").json()
    assert [(e["target"], e["triggered_by"]) for e in staff_view["escalations"]] == [("response", "system")]


def test_incident_sla_follows_incident_visibility(login_as, reporter, other_reporter, responder, db):
    theirs = _api_incident(login_as(other_reporter))
    client = login_as(reporter)
    missing = client.get("/incidents/2000000000/sla")
    assert missing.status_code == 404
    other = client.get(f"/incidents/{theirs['id']}/sla")
    assert (other.status_code, other.json()) == (404, missing.json())
    legacy = Incident(title="Legacy", description="Before accounts", severity=Severity.LOW)
    db.add(legacy)
    db.commit()
    assert client.get(f"/incidents/{legacy.id}/sla").status_code == 404
    staff_legacy = login_as(responder).get(f"/incidents/{legacy.id}/sla").json()
    assert (staff_legacy["tracked"], staff_legacy["cycles"]) == (False, [])
    assert client.post(f"/incidents/{theirs['id']}/sla", json={}).status_code == 405


def test_list_items_carry_an_sla_summary_and_staff_only_escalation_flag(login_as, reporter, responder, db, frozen):
    reporter_client = login_as(reporter)
    incident = _api_incident(reporter_client)
    created = _created_at(db, incident["id"])
    frozen(created + minutes(50))
    [mine] = [i for i in reporter_client.get("/incidents").json()["items"] if i["id"] == incident["id"]]
    assert mine["sla"] == {"state": "approaching", "target": "response", "due_at": mine["sla"]["due_at"]}
    assert mine["escalated"] is None
    [staff_item] = [i for i in login_as(responder).get("/incidents").json()["items"] if i["id"] == incident["id"]]
    assert staff_item["escalated"] is False


def test_sla_list_filters_are_scoped_and_escalated_is_staff_only(login_as, reporter, other_reporter, responder, tl, frozen):
    # Created now with the default policy, so overdue by the frozen time below.
    mine = _api_incident(login_as(reporter))
    theirs = _api_incident(login_as(other_reporter))
    # Acknowledged late and resolved on time: every target finished, one breached.
    done = tl.incident(severity="high")
    tl.change(done, T0 + minutes(70), status="resolved")
    frozen(T0 + minutes(120))

    reporter_client = login_as(reporter)
    overdue = reporter_client.get("/incidents", params={"sla": "overdue"}).json()
    assert {i["id"] for i in overdue["items"]} == {mine["id"]} and overdue["total"] == 1
    breached = reporter_client.get("/incidents", params={"sla": "breached"}).json()["items"]
    assert [(i["id"], i["sla"]["state"]) for i in breached] == [(done.id, "breached")]
    assert reporter_client.get("/incidents", params={"sla": "approaching"}).json()["total"] == 0
    assert reporter_client.get("/incidents", params={"sla": "escalated"}).status_code == 403
    assert reporter_client.get("/incidents", params={"sla": "nonsense"}).status_code == 422
    staff = login_as(responder)
    assert {mine["id"], theirs["id"]} <= {i["id"] for i in staff.get("/incidents", params={"sla": "overdue", "limit": 100}).json()["items"]}
    assert staff.get("/incidents", params={"sla": "escalated"}).json()["total"] == 0


def test_dashboard_sla_block_is_scoped_and_escalations_are_staff_only(login_as, reporter, other_reporter, responder, db, frozen):
    reporter_client = login_as(reporter)
    mine = _api_incident(reporter_client)
    _api_incident(login_as(other_reporter))
    frozen(_created_at(db, mine["id"]) + minutes(61))
    own = reporter_client.get("/analytics/summary").json()["sla"]
    assert own["response"] == {"met": 0, "breached": 0, "outstanding": 1, "not_tracked": 0}
    assert sum(s["count"] for s in own["overdue_by_severity"]) == 1 and own["escalations"] is None
    staff = login_as(responder).get("/analytics/summary").json()["sla"]
    assert staff["response"]["outstanding"] >= 2 and staff["escalations"] == 0


def test_csv_export_includes_sla_columns(login_as, reporter, responder, db, frozen):
    client = login_as(reporter)
    incident = _api_incident(client)
    created = _created_at(db, incident["id"])
    frozen(created + minutes(30))
    login_as(responder).patch(f"/incidents/{incident['id']}", json={"status": "investigating"})
    response = client.get("/exports/incidents.csv")
    rows = {r["id"]: r for r in csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"), newline=""))}
    row = rows[str(incident["id"])]
    assert row["response_state"] == "met" and row["resolution_state"] == "on_track"
    assert row["response_due_utc"].endswith("Z") and row["resolution_due_utc"].endswith("Z")


def test_internal_notes_never_appear_in_sla_responses_dashboards_or_exports(login_as, reporter, responder, admin, db):
    reporter_client = login_as(reporter)
    incident = _api_incident(reporter_client)
    staff = login_as(responder)
    assert staff.post(f"/incidents/{incident['id']}/comments", json={"body": f"{CANARY} note", "visibility": "internal"}).status_code == 201
    paths = [f"/incidents/{incident['id']}/sla", "/incidents", "/incidents?sla=overdue", "/incidents?sla=approaching",
             "/incidents?sla=breached", "/analytics/summary", "/exports/incidents.csv", "/sla/policy"]
    for client in (reporter_client, staff, login_as(admin)):
        for path in paths:
            response = client.get(path)
            assert response.status_code == 200, path
            assert CANARY not in response.text, path
    assert CANARY not in login_as(admin).get("/sla/policy/versions").text


def test_sla_reads_do_not_write(login_as, reporter, db):
    client = login_as(reporter)
    incident = _api_incident(client)
    counts = lambda: db.scalar(text("SELECT (SELECT count(*) FROM incident_events) + (SELECT count(*) FROM incident_escalations)"))  # noqa: E731
    before = (counts(), db.scalar(select(func.count()).select_from(Incident)))
    for path in (f"/incidents/{incident['id']}/sla", "/incidents?sla=overdue", "/analytics/summary", "/exports/incidents.csv"):
        client.get(path)
    assert (counts(), db.scalar(select(func.count()).select_from(Incident))) == before
