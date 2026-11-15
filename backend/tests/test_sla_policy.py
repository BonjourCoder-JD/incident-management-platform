"""SLA policy versions: admin-only changes, validation, history and when versions apply (Milestone 5B)."""
from datetime import timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.models import SlaPolicyVersion, UserRole
from sla_support import US, Timeline

URL = "/sla/policy/versions"
VALID = [
    {"severity": "critical", "response_minutes": 10, "resolution_minutes": 120},
    {"severity": "high", "response_minutes": 30, "resolution_minutes": 480},
    {"severity": "medium", "response_minutes": 120, "resolution_minutes": 2880},
    {"severity": "low", "response_minutes": 480, "resolution_minutes": 10080},
]


def _payload(**overrides):
    return {"reason": "Tighter targets after the quarterly review", "targets": [dict(t) for t in VALID], **overrides}


def _with(severity, **values):
    return [dict(t, **values) if t["severity"] == severity else dict(t) for t in VALID]


def test_everyone_signed_in_can_read_the_current_policy(client, login_as, reporter):
    assert client.get("/sla/policy").status_code == 401
    policy = login_as(reporter).get("/sla/policy").json()
    assert policy["source"] == "default" and policy["author"] is None
    assert [(t["severity"], t["response_minutes"], t["resolution_minutes"]) for t in policy["targets"]] == [
        ("low", 1440, 10080), ("medium", 240, 4320), ("high", 60, 1440), ("critical", 15, 240),
    ]  # the approved defaults (D1), seeded by migration 0007


def test_admin_creates_a_version_that_becomes_current_with_its_history(login_as, admin, db):
    client = login_as(admin)
    response = client.post(URL, json=_payload(reason="  Tighter targets after the quarterly review  "))
    assert response.status_code == 201, response.text
    created = response.json()
    assert (created["source"], created["author"], created["reason"]) == ("admin", {"id": admin.id, "email": admin.email},
                                                                         "Tighter targets after the quarterly review")
    assert client.get("/sla/policy").json()["id"] == created["id"]
    history = client.get(URL).json()
    assert [v["id"] for v in history][:1] == [created["id"]] and history[-1]["source"] == "default"
    stored = db.get(SlaPolicyVersion, created["id"])
    assert stored.created_at > db.scalar(select(func.min(SlaPolicyVersion.created_at)))


@pytest.mark.parametrize("role", [UserRole.REPORTER, UserRole.RESPONDER])
def test_only_admins_can_list_or_change_versions(role, make_user, login_as, db):
    client = login_as(make_user(role))
    before = db.scalar(select(func.count()).select_from(SlaPolicyVersion))
    assert client.get(URL).status_code == 403
    assert client.post(URL, json=_payload()).status_code == 403
    assert db.scalar(select(func.count()).select_from(SlaPolicyVersion)) == before


def test_policy_changes_require_the_csrf_token(login_as, admin, db):
    client = login_as(admin)
    del client.headers["X-CSRF-Token"]
    assert client.post(URL, json=_payload()).status_code == 403


@pytest.mark.parametrize("targets", [
    VALID[:3],                                                                        # a severity missing
    VALID + [VALID[0]],                                                               # five targets
    VALID[:3] + [dict(VALID[2])],                                                     # a severity twice
    _with("high", response_minutes=600),                                              # response longer than resolution
    _with("critical", response_minutes=60),                                           # critical slower than high
    _with("medium", resolution_minutes=400),                                          # medium faster than high
    _with("low", response_minutes=0),
    _with("low", resolution_minutes=365 * 24 * 60 + 1),
    _with("low", response_minutes=30 * 24 * 60 + 1),
    _with("high", response_minutes=True),
    _with("high", response_minutes=30.5),
    _with("high", response_minutes="30"),
    [dict(t, severity="urgent") if t["severity"] == "high" else dict(t) for t in VALID],
    [dict(VALID[0], extra=1)] + VALID[1:],
])
def test_invalid_targets_are_refused(targets, login_as, admin, db):
    before = db.scalar(select(func.count()).select_from(SlaPolicyVersion))
    response = login_as(admin).post(URL, json=_payload(targets=targets))
    assert response.status_code == 422, response.text
    assert all("input" not in issue for issue in response.json()["detail"])  # submitted values are never echoed
    assert db.scalar(select(func.count()).select_from(SlaPolicyVersion)) == before


@pytest.mark.parametrize("reason", ["", "   ", "x" * 501, "bad\x00reason", "bad\x07reason"])
def test_invalid_reasons_are_refused(reason, login_as, admin):
    assert login_as(admin).post(URL, json=_payload(reason=reason)).status_code == 422


@pytest.mark.parametrize("extra", [{"source": "default"}, {"created_by": 1}, {"created_at": "2020-01-01T00:00:00Z"}, {"id": 99}])
def test_server_fields_cannot_be_supplied(extra, login_as, admin):
    assert login_as(admin).post(URL, json=_payload(**extra)).status_code == 422


def test_a_new_version_applies_only_to_cycles_that_start_after_it(login_as, admin, reporter, responder, db):
    """Uses the version's real created_at: requests in a test share one transaction (one now()),
    so cycle starts are placed explicitly around it. Production ordering is covered by
    test_escalation.py::test_policy_change_waits_for_in_flight_incident_writes."""
    version = login_as(admin).post(URL, json=_payload()).json()
    effective = db.get(SlaPolicyVersion, version["id"]).created_at
    timeline = Timeline(db, reporter, responder)
    before = timeline.incident(at=effective - US)
    exact = timeline.incident(at=effective)
    after = timeline.incident(at=effective + US)
    now = effective + timedelta(minutes=1)
    assert timeline.cycles(before, now)[0].response_minutes == 60  # default policy, frozen at its start
    assert timeline.cycles(exact, now)[0].response_minutes == 30   # created_at == cycle start: applies
    assert timeline.cycles(after, now)[0].response_minutes == 30
    # Reopening the earlier incident starts a cycle under the new version; cycle 0 keeps the old one.
    timeline.change(before, effective + timedelta(minutes=2), status="resolved")
    timeline.change(before, effective + timedelta(minutes=3), status="open")
    cycles = timeline.cycles(before, effective + timedelta(minutes=4))
    assert [(c.cycle, c.version_id, c.resolution_minutes) for c in cycles] == [
        (0, cycles[0].version_id, 1440), (1, version["id"], 480),
    ]
    assert cycles[0].version_id != version["id"]


def test_database_requires_an_author_for_admin_versions_and_bounds_targets(db):
    with pytest.raises(IntegrityError, match="ck_sla_policy_versions_author"):
        with db.begin_nested():
            db.execute(text("INSERT INTO sla_policy_versions (source, reason) VALUES ('admin', 'No author')"))
    version_id = db.scalar(text("SELECT min(id) FROM sla_policy_versions"))
    for response, resolution in ((0, 10), (20, 10), (10, 525601)):
        with pytest.raises(IntegrityError):
            with db.begin_nested():
                db.execute(text("INSERT INTO sla_policy_targets VALUES (:v, 'low', :r, :d)"), {"v": version_id, "r": response, "d": resolution})


def test_policy_reads_include_no_unrelated_data(login_as, admin):
    policy = login_as(admin).get("/sla/policy").json()
    assert set(policy) == {"id", "source", "reason", "author", "created_at", "targets"}
