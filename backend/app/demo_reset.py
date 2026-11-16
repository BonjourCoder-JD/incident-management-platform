"""Daily reset of the public demo database (Milestone 6 Phase C).

`python -m app.cli demo-reset` (once) or `demo-reset --daily` (every day at 04:00 UTC) replaces all
demo data with the same synthetic scenarios, in one transaction:
1. refuse unless DEMO_MODE is on, DEMO_DATABASE names the connected database, and that name ends
   in "_demo" (the development, test and end-to-end databases never qualify);
2. empty every data table except users and alembic_version (owner role: the append-only triggers are
   skipped only inside this transaction), keep administrator accounts, remove all other accounts;
3. recreate the default SLA policy, the published demo accounts and the scenarios below through the
   application's own audit and SLA code, with back-dated times, plus fake triage suggestions;
4. check that the stored SLA cycles match the audit trail before committing.
It must run as the schema owner (the runtime role cannot remove audit records). Everything is
synthetic: example.com addresses, made-up incidents, and the offline test provider for triage.
"""
import logging
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

import psycopg
from sqlalchemy import Engine, func, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from . import audit, demo, sla_store
from .db_roles import public_tables
from .models import (
    CommentVisibility, Incident, IncidentComment, IncidentEvent, IncidentEventAction, IncidentStatus, Severity, SlaPolicySource,
    SlaPolicyTarget, SlaPolicyVersion, TriageOutcome, TriageRequest, TriageSuggestion, User, UserRole,
)
from .security import hash_password
from .sla import SLA_WORKER_LOCK

logger = logging.getLogger(__name__)
DEMO_RESET_LOCK = 51_000_004
DEMO_DATABASE_SUFFIX = "_demo"
KEPT_TABLES = {"users", "alembic_version"}
# The approved default targets (migration 0007), minutes: (response, resolution).
DEFAULT_TARGETS = {Severity.CRITICAL: (15, 240), Severity.HIGH: (60, 1440), Severity.MEDIUM: (240, 4320), Severity.LOW: (1440, 10080)}
OTHER_REPORTERS = ("avery.analyst@example.com", "morgan.finance@example.com")


class DemoResetRefused(Exception):
    pass


@dataclass
class ResetResult:
    incidents: int = 0
    comments: int = 0
    triage_suggestions: int = 0
    cycles_checked: int = 0


def check_target(conn) -> str:
    """Raises DemoResetRefused unless this is unmistakably the demo database. Returns its name."""
    if not demo.enabled():
        raise DemoResetRefused("DEMO_MODE is not enabled.")
    name = conn.execute(text("SELECT current_database()")).scalar_one()
    expected = os.environ.get("DEMO_DATABASE", "").strip()
    if not expected:
        raise DemoResetRefused("DEMO_DATABASE must name the demo database.")
    if name != expected:
        raise DemoResetRefused(f"Connected to {name!r}, but DEMO_DATABASE is {expected!r}.")
    if not name.endswith(DEMO_DATABASE_SUFFIX):
        raise DemoResetRefused(f"The demo database name must end with {DEMO_DATABASE_SUFFIX!r}.")
    return name


# ---- Scenarios ------------------------------------------------------------------------------------
# (key, title, description, severity, reporter, created hours ago, changes [(hours ago, fields)]).
# "reporter" is the demo reporter; "avery" and "morgan" are other synthetic reporters, whose incidents
# only responders see. "responder" in an assignment means the demo responder.

H = 1.0
DAY = 24.0
SCENARIOS = [
    ("phishing", "Phishing email reported by the finance team", "Several staff received an email asking them to confirm payroll details on an external page. Nobody has entered a password yet.",
     Severity.MEDIUM, "reporter", 0.5, []),
    ("vpn", "Suspicious VPN sign-in from a new country", "My VPN account signed in from a country I have never visited while I was in the office.",
     Severity.HIGH, "reporter", 2.0, []),
    ("ransomware", "Ransomware note on the shared engineering drive", "Files on the engineering share were renamed and a ransom note appeared in every folder.",
     Severity.CRITICAL, "avery", 6.0, [(5.9, {"status": "investigating", "assignee_id": "responder"})]),
    ("webmail", "Credential reuse detected on webmail", "Sign-ins with passwords from a public breach list are being tried against webmail accounts.",
     Severity.HIGH, "morgan", 20.0, [(19.7, {"status": "investigating", "assignee_id": "responder"})]),
    ("sso", "Brute force attempts against single sign-on", "Thousands of failed single sign-on attempts from one address range in the last few hours.",
     Severity.MEDIUM, "avery", 3.2, []),
    ("buildserver", "Unusual outbound traffic from the build server", "The build server is sending large amounts of data to an unknown storage service.",
     Severity.MEDIUM, "reporter", 26.0, [(25.0, {"status": "investigating", "assignee_id": "responder"})]),
    ("paste", "Domain admin credentials found on a paste site", "A monitoring alert found what looks like a domain administrator password on a public paste site.",
     Severity.CRITICAL, "morgan", 0.1, []),
    ("laptop", "Lost laptop with an encrypted disk", "I left my laptop on the train. The disk is encrypted and I have reported it to the train company.",
     Severity.LOW, "reporter", 3 * DAY, [(3 * DAY - 2, {"status": "investigating"}), (2 * DAY, {"status": "resolved"})]),
    ("kiosk", "Malware alert on a lobby kiosk", "Endpoint protection quarantined a trojan on the visitor sign-in kiosk.",
     Severity.HIGH, "avery", 4 * DAY, [(4 * DAY - 0.5, {"status": "investigating"}), (4 * DAY - 30, {"status": "resolved"}), (2 * DAY, {"status": "closed"})]),
    ("badge", "Cloned access badge used at reception", "Reception reports a badge that opened doors while its owner was on leave.",
     Severity.MEDIUM, "reporter", 5 * DAY, [(5 * DAY - 1, {"status": "investigating"}), (4 * DAY, {"status": "resolved"}), (3.0, {"status": "investigating"})]),
    ("certificate", "Expired certificate on the test site", "The test site shows a certificate warning. It holds no real data.",
     Severity.LOW, "morgan", 10 * DAY, [(10 * DAY - 4, {"status": "investigating"}), (9 * DAY, {"status": "resolved"}), (8 * DAY, {"status": "closed"})]),
    ("spam", "Spam newsletter reaching inboxes", "A marketing newsletter nobody signed up for is reaching inboxes. It has no links to unknown sites.",
     Severity.LOW, "avery", 0.2, []),
    ("usb", "USB stick found in the car park", "Security found an unlabelled USB stick in the car park. Nobody has plugged it in.",
     Severity.LOW, "reporter", 15 * DAY, [(15 * DAY - 6, {"status": "investigating"}), (14 * DAY, {"status": "closed"})]),
    ("printer", "Printer default admin password", "A scan found a printer on floor 3 still using its default admin password.",
     Severity.MEDIUM, "morgan", 21 * DAY, [(21 * DAY - 3, {"status": "investigating"}), (19 * DAY, {"status": "resolved"})]),
    ("payroll", "Payroll staff targeted by credential phishing", "Payroll staff received emails linking to a fake sign-in page that collects passwords.",
     Severity.HIGH, "avery", 27 * DAY, [(27 * DAY - 0.5, {"status": "investigating"}), (26 * DAY, {"status": "resolved"}), (25 * DAY, {"status": "closed"})]),
]

COMMENTS = [
    # (scenario, author, hours ago, visibility, body)
    ("vpn", "reporter", 1.8, CommentVisibility.PUBLIC, "I have not travelled recently and did not approve any sign-in."),
    ("buildserver", "responder", 24.5, CommentVisibility.PUBLIC, "Thanks for reporting this. We have blocked the destination while we investigate."),
    ("buildserver", "responder", 24.0, CommentVisibility.INTERNAL, "Destination is a consumer storage service. Checking build logs for leaked artefacts."),
    ("ransomware", "responder", 5.5, CommentVisibility.INTERNAL, "Engineering share isolated. Backups from last night verified."),
    ("badge", "responder", 2.5, CommentVisibility.PUBLIC, "Reopened: the badge was used again this morning. A replacement is ready at reception."),
    ("laptop", "responder", 2 * DAY + 1, CommentVisibility.PUBLIC, "Device locked remotely and removed from the directory. Closing as resolved."),
]

TRIAGE = [("phishing", 0.4), ("sso", 3.0), ("paste", 0.05)]


def _field_value(name: str, value, people: dict[str, int]):
    if name == "status":
        return IncidentStatus(value)
    if name == "assignee_id":
        return people[value]
    return value


def _seed(db: Session, now: datetime) -> ResetResult:
    result = ResetResult()
    hours = lambda h: now - timedelta(hours=h)  # noqa: E731

    version = SlaPolicyVersion(source=SlaPolicySource.DEFAULT, reason="Default policy (public demo)", created_at=hours(60 * DAY))
    db.add(version)
    db.flush()
    for severity, (response, resolution) in DEFAULT_TARGETS.items():
        db.add(SlaPolicyTarget(version_id=version.id, severity=severity, response_minutes=response, resolution_minutes=resolution))

    people: dict[str, int] = {}
    for account in demo.DEMO_ACCOUNTS:
        user = User(email=account.email, password_hash=hash_password(account.password), role=account.role)
        db.add(user)
        db.flush()
        people[account.role.value] = user.id
    for email in OTHER_REPORTERS:
        # Synthetic reporters nobody can sign in as (random password, never published).
        user = User(email=email, password_hash=hash_password(secrets.token_urlsafe(32)), role=UserRole.REPORTER)
        db.add(user)
        db.flush()
        people[email.split(".")[0]] = user.id

    incidents: dict[str, Incident] = {}
    # Oldest first, so ids follow creation time and the newest incidents lead the list.
    for key, title, description, severity, reporter, created_ago, changes in sorted(SCENARIOS, key=lambda scenario: -scenario[5]):
        at = hours(created_ago)
        reporter_id = people[reporter]
        row = Incident(title=title, description=description, severity=severity, status=IncidentStatus.OPEN,
                       reporter_id=reporter_id, created_at=at, updated_at=at)
        db.add(row)
        db.flush()
        created = audit.creation_changes(row)
        db.add(IncidentEvent(incident_id=row.id, actor_id=reporter_id, action=IncidentEventAction.CREATED, changes=created, created_at=at))
        sla_store.record_change(db, row, IncidentEventAction.CREATED, created, at)
        for ago, fields in changes:
            before = audit.snapshot(row)
            for name, value in fields.items():
                setattr(row, name, _field_value(name, value, people))
            diff = audit.diff(before, audit.snapshot(row))
            action = audit.classify(diff)
            db.add(IncidentEvent(incident_id=row.id, actor_id=people["responder"], action=action, changes=diff, created_at=hours(ago)))
            sla_store.record_change(db, row, action, diff, hours(ago))
            row.updated_at = hours(ago)
        db.flush()
        incidents[key] = row
        result.incidents += 1

    for key, author, ago, visibility, body in COMMENTS:
        db.add(IncidentComment(incident_id=incidents[key].id, author_id=people[author], visibility=visibility, body=body, created_at=hours(ago)))
        result.comments += 1
    db.flush()

    result.triage_suggestions = _seed_triage(db, incidents, people, hours)
    return result


def _seed_triage(db: Session, incidents: dict[str, Incident], people: dict[str, int], hours) -> int:
    """Suggestions from the offline test provider, recorded as the application records them."""
    from .config import TriageSettings
    from .triage import build_input, cost_microusd, duplicate_candidates, input_digest, latest_event_id, validate_output, worst_case_microusd
    from .triage_providers import PROMPT_VERSION, FakeProvider

    settings = TriageSettings(provider="fake")
    provider = FakeProvider()
    responder = db.get(User, people["responder"])
    for key, ago in TRIAGE:
        incident = incidents[key]
        candidates = duplicate_candidates(db, incident, responder)
        triage_input = build_input(incident, candidates)
        result = provider.suggest(triage_input)
        valid = validate_output(result.raw, [cid for cid, _ in candidates])
        request = TriageRequest(
            incident_id=incident.id, requested_by=responder.id, provider=provider.name, model=provider.model, prompt_version=PROMPT_VERSION,
            basis_event_id=latest_event_id(db, incident.id), input_sha256=input_digest(triage_input), candidate_ids=[cid for cid, _ in candidates],
            reserved_microusd=worst_case_microusd(triage_input, settings, settings.prices()), created_at=hours(ago))
        db.add(request)
        db.flush()
        db.add(TriageSuggestion(
            request_id=request.id, outcome=TriageOutcome.VALID, severity=valid.severity, rationale=valid.rationale,
            next_steps=valid.next_steps, duplicate_ids=valid.duplicate_ids, input_tokens=result.input_tokens,
            output_tokens=result.output_tokens, cost_microusd=cost_microusd(result.input_tokens, result.output_tokens, settings.prices()),
            latency_ms=result.latency_ms, created_at=hours(ago)))
    db.flush()
    return len(TRIAGE)


def reset(engine: Engine, lock_timeout_seconds: int = 30, attempts: int = 3) -> ResetResult:
    """Replaces all demo data in one transaction. Raises DemoResetRefused for any other database.

    Concurrency: the reset first takes the SLA worker lock, so it waits for a worker run in progress and
    the worker skips its runs until the reset commits. API requests are serialised by PostgreSQL's table
    locks: the reset waits for transactions already running, and new ones wait for the reset (a few
    seconds). If a request and the reset deadlock, PostgreSQL aborts one of them; an aborted reset is
    retried (`attempts`). If the locks are not granted within `lock_timeout_seconds` (a stuck
    transaction), the reset gives up and rolls back instead of making every request queue behind it;
    the demo keeps yesterday's data until the next run."""
    for attempt in range(1, attempts + 1):
        try:
            return _reset_once(engine, lock_timeout_seconds)
        except OperationalError as error:
            if not isinstance(error.orig, psycopg.errors.DeadlockDetected) or attempt == attempts:
                raise
            logger.warning("Demo reset deadlocked with a concurrent request (attempt %d of %d); retrying", attempt, attempts)
    raise AssertionError("unreachable")


def _reset_once(engine: Engine, lock_timeout_seconds: int) -> ResetResult:
    with Session(bind=engine, autoflush=False, expire_on_commit=False) as db:
        conn = db.connection()
        name = check_target(conn)
        db.execute(text(f"SET LOCAL lock_timeout = '{int(lock_timeout_seconds)}s'"))
        db.execute(select(func.pg_advisory_xact_lock(DEMO_RESET_LOCK)))
        db.execute(select(func.pg_advisory_xact_lock(SLA_WORKER_LOCK)))  # no worker run during the reset
        tables = sorted(public_tables(conn) - KEPT_TABLES)
        # Only inside this transaction: skip the append-only triggers so the owner can empty the tables.
        db.execute(text("SET LOCAL session_replication_role = replica"))
        db.execute(text(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE"))
        db.execute(text("DELETE FROM users WHERE role <> 'admin'"))
        # Recreated accounts get the same ids every day (administrators are kept, so continue after them).
        db.execute(text("SELECT setval(pg_get_serial_sequence('users', 'id'), COALESCE((SELECT max(id) FROM users), 0) + 1, false)"))
        db.execute(text("SET LOCAL session_replication_role = origin"))
        now = db.scalar(select(func.clock_timestamp()))
        result = _seed(db, now)
        checked, mismatches, ids = sla_store.verify(conn)
        if mismatches:
            raise RuntimeError(f"Demo seed produced inconsistent SLA cycles for incidents {ids}")
        result.cycles_checked = checked
        db.commit()
    logger.info("Demo database %s reset: %d incidents, %d comments, %d triage suggestions", name, result.incidents,
                result.comments, result.triage_suggestions)
    return result


def seconds_until_next_reset(now: datetime) -> float:
    """Seconds from `now` (UTC-aware) to the next 04:00 UTC."""
    hour, minute = (int(part) for part in demo.RESET_TIME_UTC.split(":"))
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def needs_initial_seed(engine: Engine) -> bool:
    """True on a fresh demo database (no demo accounts yet), so the first start seeds immediately."""
    with engine.connect() as conn:
        return conn.execute(select(func.count()).select_from(User).where(User.email.in_(demo.DEMO_EMAILS))).scalar_one() == 0

