"""Runtime settings read from environment variables. No secrets have defaults."""
import os
from dataclasses import dataclass


def _bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    value = os.environ.get(name)
    return int(value) if value else default


@dataclass(frozen=True)
class Settings:
    # Secure cookies are sent only over HTTPS; browsers also accept them on http://localhost.
    session_cookie_secure: bool = _bool("SESSION_COOKIE_SECURE", True)
    session_cookie_name: str = os.environ.get("SESSION_COOKIE_NAME", "imp_session")
    session_absolute_hours: int = _int("SESSION_ABSOLUTE_HOURS", 12)
    session_idle_minutes: int = _int("SESSION_IDLE_MINUTES", 120)
    login_max_failures_per_email: int = _int("LOGIN_MAX_FAILURES_PER_EMAIL", 5)
    login_max_failures_per_ip: int = _int("LOGIN_MAX_FAILURES_PER_IP", 30)
    login_failure_window_seconds: int = _int("LOGIN_FAILURE_WINDOW_SECONDS", 15 * 60)
    # CSV exports larger than this are refused (HTTP 413), never truncated.
    export_max_rows: int = _int("EXPORT_MAX_ROWS", 10_000)


settings = Settings()
