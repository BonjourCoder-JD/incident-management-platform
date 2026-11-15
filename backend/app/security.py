"""Password hashing, session tokens and login rate limiting."""
import hashlib
import re
import secrets
import threading
import time
from collections import defaultdict, deque

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

# argon2-cffi defaults: Argon2id with the RFC 9106 low-memory parameters.
_hasher = PasswordHasher()

PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 128  # bounds hashing cost per request
EMAIL_MAX_LENGTH = 254
_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


# Verified against when the email is unknown, so response time does not reveal which emails exist.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(32))


def burn_password_check(password: str) -> None:
    verify_password(_DUMMY_HASH, password)


def password_problem(password: str) -> str | None:
    """Returns a reason the password is unacceptable, or None."""
    if len(password) < PASSWORD_MIN_LENGTH:
        return f"Password must be at least {PASSWORD_MIN_LENGTH} characters."
    if len(password) > PASSWORD_MAX_LENGTH:
        return f"Password must be at most {PASSWORD_MAX_LENGTH} characters."
    if not password.strip():
        return "Password cannot be only whitespace."
    return None


def normalize_email(email: str) -> str:
    return email.strip().lower()


def is_valid_email(email: str) -> bool:
    return len(email) <= EMAIL_MAX_LENGTH and bool(_EMAIL_PATTERN.match(email))


def new_token() -> str:
    """256 bits of randomness, URL-safe."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    # Tokens are high-entropy random values, so a fast hash is appropriate (unlike passwords).
    return hashlib.sha256(token.encode()).hexdigest()


class LoginRateLimiter:
    """Sliding-window count of failed logins per key (email or client IP).

    In-memory: resets on restart and is per process. Adequate for a single
    uvicorn worker; a shared store (e.g. Redis or PostgreSQL) is needed for more.
    """

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._failures: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _prune(self, key: str, window: int) -> deque[float]:
        events = self._failures[key]
        cutoff = self._clock() - window
        while events and events[0] <= cutoff:
            events.popleft()
        if not events:
            self._failures.pop(key, None)
        return events

    def retry_after(self, key: str, limit: int, window: int) -> int | None:
        """Seconds until another attempt is allowed, or None if not limited."""
        with self._lock:
            events = self._prune(key, window)
            if len(events) < limit:
                return None
            return max(1, int(events[0] + window - self._clock()) + 1)

    def record_failure(self, key: str) -> None:
        with self._lock:
            self._failures[key].append(self._clock())

    def reset(self, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._failures.clear()
            else:
                self._failures.pop(key, None)


login_limiter = LoginRateLimiter()
