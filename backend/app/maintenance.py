"""Periodic housekeeping (Milestone 6 Phase B, ISSUE-9): expired sessions and old rate-limit events.

Sessions are already refused once expired or idle; this removes their rows so auth_sessions does not
grow forever. Run hourly by the worker (`sla-escalate --loop`) and on demand with
`python -m app.cli cleanup`. Logs counts only.
"""
import logging
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import delete, func, or_
from sqlalchemy.orm import Session

from . import rate_limit
from .config import settings
from .models import AuthSession

logger = logging.getLogger(__name__)
CLEANUP_INTERVAL_SECONDS = 3600


@dataclass(frozen=True)
class CleanupResult:
    sessions: int
    rate_limit_events: int


def rate_limit_retention_seconds() -> int:
    """How long rate-limit events are kept: the longest window any limit uses. An event inside its
    window is never removed, so cleanup can never shorten or lift an active lockout."""
    return max(settings.login_failure_window_seconds, rate_limit.WRITE_WINDOW_SECONDS)


def cleanup(db: Session) -> CleanupResult:
    idle_cutoff = func.now() - timedelta(minutes=settings.session_idle_minutes)
    sessions = db.execute(
        delete(AuthSession).where(or_(AuthSession.expires_at <= func.now(), AuthSession.last_seen_at <= idle_cutoff))
    ).rowcount or 0
    # Keep events for the longest window in use; older ones can no longer affect any limit.
    events = rate_limit.prune(db, rate_limit_retention_seconds())
    db.commit()
    logger.info("Maintenance: removed %d expired sessions and %d old rate-limit events", sessions, events)
    return CleanupResult(sessions, events)
