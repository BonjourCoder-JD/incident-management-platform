"""Administrative commands.

Create the first admin for local development (no public sign-up exists):

    docker compose run --rm backend python -m app.cli create-admin --email admin@example.com

The password is read from a hidden prompt, or from BOOTSTRAP_ADMIN_PASSWORD when no
terminal is attached. It is never accepted as a command-line argument (which would
leak into shell history and process listings) and never printed.
"""
import argparse
import getpass
import os
import sys

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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="Incident platform administration")
    commands = parser.add_subparsers(dest="command", required=True)
    admin = commands.add_parser("create-admin", help="Create an admin account")
    admin.add_argument("--email", required=True)
    args = parser.parse_args(argv)

    if args.command == "create-admin":
        user = create_admin(args.email, _read_password())
        print(f"Created admin user #{user.id} ({user.email}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
