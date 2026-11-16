"""Administrative commands.

Create the first admin for local development (no public sign-up exists):

    docker compose run --rm backend python -m app.cli create-admin --email admin@example.com

The password is read from a hidden prompt, or from BOOTSTRAP_ADMIN_PASSWORD when no
terminal is attached. It is never accepted as a command-line argument (which would
leak into shell history and process listings) and never printed.

Record escalations for overdue SLA targets (the sla-worker service runs this with --loop):

    docker compose run --rm backend python -m app.cli sla-escalate [--loop] [--interval 60]

Check the stored SLA cycles against the audit trail (exit status 1 on any difference):

    docker compose run --rm backend python -m app.cli sla-verify

Operations (Milestone 6 Phase D): `health-check NAME --max-age SECONDS` (the worker and demo-reset
health checks) and `verify-restore` (consistency of a restored copy; see docs/OPERATIONS.md).
"""
import argparse
import getpass
import logging
import os
import sys
import time

from sqlalchemy import select

from .database import SessionLocal
from .models import User, UserRole
from .security import hash_password, is_valid_email, normalize_email, password_problem

PASSWORD_ENV = "BOOTSTRAP_ADMIN_PASSWORD"


def _say(message: str, error: bool = False) -> None:
    """Status lines of the long-running services: JSON log lines in production (LOG_FORMAT=json),
    plain output otherwise (scripts and tests read stdout)."""
    from .logs import log_format

    if log_format() == "json":
        logging.getLogger("app.cli").log(logging.ERROR if error else logging.INFO, message)
    else:
        print(message, file=sys.stderr if error else sys.stdout, flush=True)


def _read_password() -> str:
    from_env = os.environ.get(PASSWORD_ENV)
    if from_env:
        return from_env
    if not sys.stdin.isatty():
        raise SystemExit(f"No terminal for a password prompt. Set {PASSWORD_ENV} or run with an interactive terminal (-it).")
    first = getpass.getpass("New admin password: ")
    if first != getpass.getpass("Repeat password: "):
        raise SystemExit("Passwords do not match.")
    return first


def create_admin(email: str, password: str, session_factory=None) -> User:
    email = normalize_email(email)
    if not is_valid_email(email):
        raise SystemExit("Enter a valid email address.")
    problem = password_problem(password)
    if problem:
        raise SystemExit(problem)
    with (session_factory or SessionLocal)() as db:
        if db.scalar(select(User.id).where(User.email == email)) is not None:
            raise SystemExit("A user with this email already exists. Nothing was changed.")
        user = User(email=email, password_hash=hash_password(password), role=UserRole.ADMIN)
        db.add(user)
        db.commit()
        db.refresh(user)
        return user


def sla_escalate(loop: bool, interval: int) -> int:
    """No clock override exists here on purpose: production always uses the database clock."""
    from . import heartbeat
    from .escalation import run_escalations
    from .health import readiness

    from .logs import configure

    configure()
    if not 10 <= interval <= 3600:
        print("--interval must be between 10 and 3600 seconds.", file=sys.stderr)
        return 2
    from .maintenance import CLEANUP_INTERVAL_SECONDS

    last_cleanup = None
    cleanup_ok = True
    while True:
        # Never run against a schema this code was not written for (for example before migrations).
        ready, reason = readiness()
        if not ready:
            _say(f"SLA escalation run skipped: database not ready ({reason}).", error=True)
            heartbeat.record("worker", False, f"database not ready ({reason})")
            if not loop:
                return 1
            time.sleep(interval)
            continue
        try:
            run = run_escalations()
            _say(f"SLA escalation run: {'done' if run.ran else 'skipped (another worker is running)'}; "
                 f"{run.checked} checked, {run.recorded} recorded, {run.skipped} skipped")
            heartbeat.record("worker", cleanup_ok, "" if cleanup_ok else "the last cleanup failed")
        except Exception as error:  # keep the worker alive; the log names the failure, never incident data
            logging.getLogger(__name__).exception("SLA escalation run failed")
            heartbeat.record("worker", False, f"escalation run failed ({type(error).__name__})")
            if not loop:
                return 1
        if not loop:
            return 0
        if last_cleanup is None or time.monotonic() - last_cleanup >= CLEANUP_INTERVAL_SECONDS:
            last_cleanup = time.monotonic()
            cleanup_ok = cleanup() == 0
            if not cleanup_ok:
                heartbeat.record("worker", False, "the last cleanup failed")
        time.sleep(interval)


def cleanup() -> int:
    """Removes expired sessions and old rate-limit events (hourly in the worker)."""
    from .database import SessionLocal
    from .maintenance import cleanup as run_cleanup

    try:
        with SessionLocal() as db:
            result = run_cleanup(db)
    except Exception:
        logging.getLogger(__name__).exception("Maintenance failed")
        return 1
    _say(f"Maintenance: removed {result.sessions} expired sessions and {result.rate_limit_events} old rate-limit events.")
    return 0


def db_roles() -> int:
    """Creates or updates the least-privilege runtime role. Run as the migration owner after
    `alembic upgrade head`. Reads APP_DB_USER (default imp_app) and APP_DB_PASSWORD."""
    from .database import engine
    from .db_roles import DEFAULT_RUNTIME_ROLE, apply

    role = os.environ.get("APP_DB_USER", DEFAULT_RUNTIME_ROLE).strip()
    password = os.environ.get("APP_DB_PASSWORD", "")
    try:
        with engine.begin() as conn:
            apply(conn, role, password)
    except ValueError as error:
        _say(f"db-roles: {error}", error=True)
        return 2
    _say(f"db-roles: runtime role {role} has least-privilege access.")
    return 0


def demo_reset(daily: bool) -> int:
    """Resets the public demo database (refused for any other database). Without --daily this is the
    administrator's "reset now" (same transaction, same guards); with --daily, seeds a fresh demo
    database immediately, then resets every day at 04:00 UTC. Run as the schema owner."""
    from datetime import datetime, timezone

    from . import heartbeat
    from .database import engine
    from .demo_reset import DemoResetRefused, needs_initial_seed, reset, seconds_until_next_reset

    from .logs import configure

    configure()

    def run_once() -> int:
        try:
            result = reset(engine)
        except DemoResetRefused as refused:
            _say(f"demo-reset refused: {refused}", error=True)
            heartbeat.record("demo-reset", False, "refused")
            return 2
        except Exception as error:  # one transaction: nothing was changed
            logging.getLogger(__name__).exception("Demo reset failed and was rolled back")
            heartbeat.record("demo-reset", False, f"failed and rolled back ({type(error).__name__})")
            return 1
        heartbeat.record("demo-reset", True)
        _say(f"Demo reset: {result.incidents} incidents, {result.comments} comments, {result.triage_suggestions} triage "
             f"suggestions; {result.cycles_checked} SLA cycles consistent.")
        return 0

    if not daily:
        return run_once()
    # A refused or failed reset is logged and retried at the next 04:00 UTC; the schedule never
    # exits (a refused target would otherwise restart in a loop under `restart: unless-stopped`).
    try:
        if needs_initial_seed(engine):
            run_once()
        else:
            heartbeat.record("demo-reset", True, "waiting for the next reset")
    except Exception:
        logging.getLogger(__name__).exception("Initial demo seed failed")
        heartbeat.record("demo-reset", False, "initial seed failed")
    while True:
        wait = seconds_until_next_reset(datetime.now(timezone.utc))
        _say(f"Next demo reset in {int(wait)} s (04:00 UTC).")
        time.sleep(wait)
        try:
            run_once()
        except Exception:  # keep the schedule; the log names the failure
            logging.getLogger(__name__).exception("Demo reset failed")


def sla_verify() -> int:
    """Exit status 1 if any stored SLA cycle differs from the audit-derived reference."""
    from .database import engine
    from .sla_store import verify

    with engine.connect() as conn:
        checked, mismatches, ids = verify(conn)
    if mismatches:
        print(f"SLA read model differs from the audit trail: {mismatches} differing rows; incidents {ids}", file=sys.stderr)
        return 1
    print(f"SLA read model consistent with the audit trail: {checked} cycles checked.")
    return 0


def health_check(name: str, max_age: int) -> int:
    """Docker health check for a long-running service: exit 0 if its latest attempt succeeded recently."""
    from .heartbeat import check

    healthy, reason = check(name, max_age)
    print(f"{name}: {reason}", file=sys.stdout if healthy else sys.stderr)
    return 0 if healthy else 1


def verify_restore(database: str | None = None) -> int:
    """Consistency of a restored copy: `database` on the server in DATABASE_URL, with the same
    credentials (none are typed), or the DATABASE_URL database itself. Exit status 1 on any problem."""
    import re

    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url

    from .database import DATABASE_URL, engine
    from .restore_check import verify_database

    target = engine
    if database is not None:
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", database):
            print("verify-restore: --database must be a database name (lowercase letters, digits, underscores).", file=sys.stderr)
            return 2
        target = create_engine(make_url(DATABASE_URL).set(database=database), hide_parameters=True)
    try:
        with target.connect() as conn:
            problems, counts = verify_database(conn)
    except Exception as error:
        print(f"verify-restore: cannot check the database ({type(error).__name__}).", file=sys.stderr)
        return 1
    finally:
        if target is not engine:
            target.dispose()
    for problem in problems:
        print(f"verify-restore: {problem}", file=sys.stderr)
    if problems:
        return 1
    print("verify-restore: consistent; " + ", ".join(f"{table} {count}" for table, count in counts.items()))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="Incident platform administration")
    commands = parser.add_subparsers(dest="command", required=True)
    admin = commands.add_parser("create-admin", help="Create an admin account")
    admin.add_argument("--email", required=True)
    escalate = commands.add_parser("sla-escalate", help="Record escalations for overdue SLA targets")
    escalate.add_argument("--loop", action="store_true", help="Run repeatedly")
    escalate.add_argument("--interval", type=int, default=60, help="Seconds between runs with --loop (10-3600)")
    commands.add_parser("sla-verify", help="Check the stored SLA cycles against the audit trail")
    commands.add_parser("cleanup", help="Remove expired sessions and old rate-limit events")
    commands.add_parser("db-roles", help="Create or update the least-privilege runtime database role (run as the owner)")
    reset_parser = commands.add_parser("demo-reset", help="Reset the public demo database (DEMO_MODE, DEMO_DATABASE ending in _demo)")
    reset_parser.add_argument("--daily", action="store_true", help="Seed if empty, then reset every day at 04:00 UTC")
    health = commands.add_parser("health-check", help="Exit 0 if a service's latest attempt succeeded recently")
    health.add_argument("name", choices=["worker", "demo-reset"])
    health.add_argument("--max-age", type=int, required=True, help="Seconds")
    verify = commands.add_parser("verify-restore", help="Check a restored database before it replaces the live one")
    verify.add_argument("--database", help="Database to check on the same server (default: the one in DATABASE_URL)")
    args = parser.parse_args(argv)
    from .logs import configure

    configure()  # JSON lines in production (LOG_FORMAT=json)

    if args.command == "health-check":
        return health_check(args.name, args.max_age)
    if args.command == "verify-restore":
        return verify_restore(args.database)

    if args.command == "demo-reset":
        return demo_reset(daily=args.daily)

    if args.command == "cleanup":
        return cleanup()
    if args.command == "db-roles":
        return db_roles()

    if args.command == "sla-escalate":
        return sla_escalate(loop=args.loop, interval=args.interval)
    if args.command == "sla-verify":
        return sla_verify()

    if args.command == "create-admin":
        user = create_admin(args.email, _read_password())
        print(f"Created admin user #{user.id} ({user.email}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
