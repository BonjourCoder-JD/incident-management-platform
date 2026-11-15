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
    from .escalation import run_escalations

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not 10 <= interval <= 3600:
        print("--interval must be between 10 and 3600 seconds.", file=sys.stderr)
        return 2
    while True:
        try:
            run = run_escalations()
            print(f"SLA escalation run: {'done' if run.ran else 'skipped (another worker is running)'}; "
                  f"{run.checked} checked, {run.recorded} recorded, {run.skipped} skipped", flush=True)
        except Exception:  # keep the worker alive; the log names the failure, never incident data
            logging.getLogger(__name__).exception("SLA escalation run failed")
            if not loop:
                return 1
        if not loop:
            return 0
        time.sleep(interval)


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="Incident platform administration")
    commands = parser.add_subparsers(dest="command", required=True)
    admin = commands.add_parser("create-admin", help="Create an admin account")
    admin.add_argument("--email", required=True)
    escalate = commands.add_parser("sla-escalate", help="Record escalations for overdue SLA targets")
    escalate.add_argument("--loop", action="store_true", help="Run repeatedly")
    escalate.add_argument("--interval", type=int, default=60, help="Seconds between runs with --loop (10-3600)")
    commands.add_parser("sla-verify", help="Check the stored SLA cycles against the audit trail")
    args = parser.parse_args(argv)

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
