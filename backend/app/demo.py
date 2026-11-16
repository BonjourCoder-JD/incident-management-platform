"""Public demo mode (Milestone 6 Phase C).

With DEMO_MODE=true the public demo publishes two accounts with fixed credentials (a reporter and a
responder). Everything they see is synthetic and is recreated every day at 04:00 UTC by
`python -m app.cli demo-reset` (app/demo_reset.py). The administrator account stays private.

Demo accounts are protected so one visitor cannot spoil the demo for the next:
- their passwords cannot be changed (403);
- an administrator cannot change their role or deactivate them (403);
- failed sign-ins never lock them out by email; the per-IP limit still applies, so a visitor who
  hammers the sign-in form only locks out their own address.
Outside demo mode none of this applies and /demo reports that the demo is off.
"""
from dataclasses import dataclass

from fastapi import APIRouter

from .config import settings
from .models import UserRole

RESET_TIME_UTC = "04:00"


@dataclass(frozen=True)
class DemoAccount:
    role: UserRole
    email: str
    password: str  # published on purpose: shown on the sign-in page of the public demo


DEMO_ACCOUNTS = (
    DemoAccount(UserRole.REPORTER, "demo.reporter@example.com", "demo-reporter-2026"),
    DemoAccount(UserRole.RESPONDER, "demo.responder@example.com", "demo-responder-2026"),
)
DEMO_EMAILS = frozenset(account.email for account in DEMO_ACCOUNTS)


def enabled() -> bool:
    return settings.demo_mode


def is_demo_account(email: str) -> bool:
    """True only in demo mode, for the published accounts (emails are already normalised)."""
    return enabled() and email in DEMO_EMAILS


router = APIRouter(tags=["demo"])


@router.get("/demo")
def demo_info():
    """Public: whether this is the public demo, and if so its accounts and reset time."""
    if not enabled():
        return {"enabled": False, "reset_time_utc": None, "accounts": []}
    return {
        "enabled": True,
        "reset_time_utc": RESET_TIME_UTC,
        "accounts": [{"role": a.role.value, "email": a.email, "password": a.password} for a in DEMO_ACCOUNTS],
    }
