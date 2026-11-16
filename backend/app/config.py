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



def _float(name: str, default: float | None) -> float | None:
    value = os.environ.get(name)
    return float(value) if value else default


# ---- AI triage (Milestone 5C) ------------------------------------------------------------
# Off unless TRIAGE_PROVIDER says otherwise. "fake" is the deterministic, offline provider used by
# tests, CI and development; "anthropic" makes paid calls and also needs ANTHROPIC_API_KEY.
TRIAGE_DEFAULT_MODEL = "claude-haiku-5-5"
# Published prices for the default model, US$ per million tokens (prompts up to 100K tokens).
TRIAGE_DEFAULT_PRICES = (0.10, 0.50)


@dataclass(frozen=True)
class TriageSettings:
    provider: str = os.environ.get("TRIAGE_PROVIDER", "off").strip().lower()
    model: str = os.environ.get("TRIAGE_MODEL", TRIAGE_DEFAULT_MODEL).strip()
    # Prices must be given explicitly for any other model, so budgets are never computed wrongly.
    price_input_per_mtok: float | None = _float("TRIAGE_PRICE_INPUT_PER_MTOK", None)
    price_output_per_mtok: float | None = _float("TRIAGE_PRICE_OUTPUT_PER_MTOK", None)
    timeout_seconds: float = float(os.environ.get("TRIAGE_TIMEOUT_SECONDS", "20"))
    max_output_tokens: int = _int("TRIAGE_MAX_OUTPUT_TOKENS", 1200)
    user_daily_usd: float = float(os.environ.get("TRIAGE_USER_DAILY_USD", "0.05"))
    user_hourly_requests: int = _int("TRIAGE_USER_HOURLY_REQUESTS", 10)
    incident_total_usd: float = float(os.environ.get("TRIAGE_INCIDENT_TOTAL_USD", "0.01"))
    incident_cooldown_seconds: int = _int("TRIAGE_INCIDENT_COOLDOWN_SECONDS", 60)
    global_daily_usd: float = float(os.environ.get("TRIAGE_GLOBAL_DAILY_USD", "0.50"))
    global_monthly_usd: float = float(os.environ.get("TRIAGE_GLOBAL_MONTHLY_USD", "5.00"))
    global_daily_requests: int = _int("TRIAGE_GLOBAL_DAILY_REQUESTS", 300)
    # A reservation with no recorded result after this long is closed as abandoned, at full cost.
    reservation_timeout_seconds: int = _int("TRIAGE_RESERVATION_TIMEOUT_SECONDS", 600)

    def prices(self) -> tuple[float, float] | None:
        """(input, output) US$ per million tokens, or None when they are not known for the model."""
        if self.price_input_per_mtok is not None and self.price_output_per_mtok is not None:
            return self.price_input_per_mtok, self.price_output_per_mtok
        if self.model == TRIAGE_DEFAULT_MODEL:
            return TRIAGE_DEFAULT_PRICES
        return None


triage_settings = TriageSettings()


settings = Settings()


# ---- Deployment environment (Milestone 6) --------------------------------------------------
# "production" turns off the interactive API docs and refuses to start with unsafe settings.
APP_ENV = os.environ.get("APP_ENV", "development").strip().lower()
# Passwords from the development defaults; never acceptable in production.
_DEVELOPMENT_PASSWORDS = ("localdevonly", ":incident@", "ci-only-disposable")
MIN_DATABASE_PASSWORD_LENGTH = 24  # generated, for example `openssl rand -hex 32`


def production_problems(environ=os.environ) -> list[str]:
    """Settings that must never reach a public deployment. Messages never include secret values."""
    problems = []
    if not _bool_from(environ, "SESSION_COOKIE_SECURE", True):
        problems.append("SESSION_COOKIE_SECURE must be true (HTTPS only).")
    database_url = environ.get("DATABASE_URL", "")
    if not database_url:
        problems.append("DATABASE_URL must be set.")
    elif any(weak in database_url for weak in _DEVELOPMENT_PASSWORDS):
        problems.append("DATABASE_URL uses a development password.")
    else:
        from sqlalchemy.engine import make_url

        try:
            password = make_url(database_url).password or ""
        except Exception:
            password = ""
        if len(password) < MIN_DATABASE_PASSWORD_LENGTH:
            problems.append(f"The database password must be at least {MIN_DATABASE_PASSWORD_LENGTH} characters.")
    if environ.get("TRIAGE_PROVIDER", "off").strip().lower() == "anthropic":
        problems.append("TRIAGE_PROVIDER=anthropic is not allowed in this deployment (use off or fake).")
    if environ.get("ANTHROPIC_API_KEY"):
        problems.append("ANTHROPIC_API_KEY must not be set in this deployment.")
    if not environ.get("FORWARDED_ALLOW_IPS") or environ.get("FORWARDED_ALLOW_IPS", "").strip() == "*":
        problems.append("FORWARDED_ALLOW_IPS must name the reverse proxy (not '*').")
    return problems


def _bool_from(environ, name: str, default: bool) -> bool:
    value = environ.get(name)
    return default if value is None else value.strip().lower() in {"1", "true", "yes", "on"}
