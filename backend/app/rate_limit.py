"""Sliding-window limits stored in PostgreSQL (Milestone 6 Phase B, ISSUE-7).

Each counted event is a row in rate_limit_events: a bucket, the SHA-256 of the key and the database
time. Counting the rows inside the window gives a limit that every process shares and that
survives restarts. Keys (email addresses and client IP addresses) are never stored in clear.

The check and the record for one key run under a transaction-level advisory lock on that key, so
concurrent attempts are counted one after another and cannot overshoot the limit. The lock is held
until the caller's transaction ends, which is short (no network calls happen in between).
"""
import hashlib
import math
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .config import settings
from .database import get_db
from .models import RateLimitEvent

LOGIN_EMAIL = "login_email"
LOGIN_IP = "login_ip"
WRITE_INCIDENT = "write_incident"
WRITE_COMMENT = "write_comment"
WRITE_TRIAGE = "write_triage"


def key_hash(bucket: str, key: str) -> str:
    return hashlib.sha256(f"{bucket}\x1f{key}".encode("utf-8", "surrogatepass")).hexdigest()


@dataclass(frozen=True)
class Limit:
    bucket: str
    key: str
    limit: int
    window_seconds: int

    @property
    def hashed(self) -> str:
        return key_hash(self.bucket, self.key)


def lock(db: Session, limits: list[Limit]) -> None:
    """Serialises attempts on the same keys until the transaction ends (fixed order: no deadlocks)."""
    for hashed in sorted({limit.hashed for limit in limits}):
        db.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(hashed, 0))))


def retry_after(db: Session, limit: Limit) -> int | None:
    """Seconds until another event is allowed, or None while under the limit."""
    if limit.limit <= 0:
        return None
    since = func.now() - func.make_interval(0, 0, 0, 0, 0, 0, limit.window_seconds)
    count, oldest, now = db.execute(
        select(func.count(), func.min(RateLimitEvent.created_at), func.now())
        .where(RateLimitEvent.bucket == limit.bucket, RateLimitEvent.key_hash == limit.hashed, RateLimitEvent.created_at > since)
    ).one()
    if count < limit.limit:
        return None
    return max(1, math.ceil(limit.window_seconds - (now - oldest).total_seconds()))


def record(db: Session, limit: Limit) -> None:
    db.add(RateLimitEvent(bucket=limit.bucket, key_hash=limit.hashed))
    db.flush()


def reset(db: Session, limit: Limit) -> None:
    db.execute(delete(RateLimitEvent).where(RateLimitEvent.bucket == limit.bucket, RateLimitEvent.key_hash == limit.hashed))


def prune(db: Session, older_than_seconds: int) -> int:
    """Removes events older than every window in use. Returns the number removed."""
    cutoff = func.now() - func.make_interval(0, 0, 0, 0, 0, 0, older_than_seconds)
    return db.execute(delete(RateLimitEvent).where(RateLimitEvent.created_at < cutoff)).rowcount or 0


# ---- Public-demo write limits ----------------------------------------------------------------

WRITE_WINDOW_SECONDS = 3600


@dataclass(frozen=True)
class WriteLimits:
    incidents: int
    comments: int
    triage: int


def get_write_limits() -> WriteLimits:
    """Dependency (tests replace it). 0 turns a limit off, which is the default outside the demo."""
    return WriteLimits(settings.write_limit_incidents_per_hour, settings.write_limit_comments_per_hour,
                       settings.write_limit_triage_per_hour)


_WRITE_LIMITS = {
    WRITE_INCIDENT: ("incidents", "Too many new incidents from your network. Try again later."),
    WRITE_COMMENT: ("comments", "Too many comments from your network. Try again later."),
    WRITE_TRIAGE: ("triage", "Too many triage requests from your network. Try again later."),
}


def limit_writes(bucket: str):
    """A dependency that counts one write per client IP in `bucket` and refuses it (429) over the
    hourly limit. Declared after the authentication dependency, so anonymous requests never count.
    The event is flushed into the request's transaction: it is only kept if the write commits."""
    attribute, message = _WRITE_LIMITS[bucket]

    def dependency(request: Request, db: Annotated[Session, Depends(get_db)],
                   limits: Annotated[WriteLimits, Depends(get_write_limits)]) -> None:
        allowed = getattr(limits, attribute)
        if allowed <= 0:
            return
        limit = Limit(bucket, request.client.host if request.client else "unknown", allowed, WRITE_WINDOW_SECONDS)
        lock(db, [limit])
        wait = retry_after(db, limit)
        if wait is not None:
            db.rollback()
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=message, headers={"Retry-After": str(wait)})
        record(db, limit)

    return dependency
