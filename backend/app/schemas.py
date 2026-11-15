import re
from datetime import date, datetime
from pydantic import BaseModel, ConfigDict, Field, field_validator
from typing import Any, Literal
from .models import CommentVisibility, IncidentEventAction, IncidentStatus, Severity, UserRole
from .security import EMAIL_MAX_LENGTH, PASSWORD_MAX_LENGTH, is_valid_email, normalize_email, password_problem

# PostgreSQL INTEGER upper bound. Larger IDs must fail validation (422), not reach the database (BUG-1).
INT32_MAX = 2_147_483_647


def _email(value: str) -> str:
    value = normalize_email(value)
    if not is_valid_email(value):
        raise ValueError("Enter a valid email address.")
    return value


def _new_password(value: str) -> str:
    problem = password_problem(value)
    if problem:
        raise ValueError(problem)
    return value


# ---- Users and authentication -------------------------------------------------

class UserSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    email: str

class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    email: str
    role: UserRole
    is_active: bool
    created_at: datetime

class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(max_length=EMAIL_MAX_LENGTH)
    # Loose bound only: an over-long password is treated as wrong credentials, not described.
    password: str = Field(max_length=1024)

class SessionInfo(BaseModel):
    user: UserRead
    csrf_token: str

class PasswordChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_password: str = Field(max_length=1024)
    new_password: str = Field(max_length=PASSWORD_MAX_LENGTH)

    @field_validator("new_password")
    @classmethod
    def check_new_password(cls, value: str) -> str:
        return _new_password(value)

class UserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(max_length=EMAIL_MAX_LENGTH)
    password: str = Field(max_length=PASSWORD_MAX_LENGTH)
    role: UserRole = UserRole.REPORTER

    @field_validator("email")
    @classmethod
    def check_email(cls, value: str) -> str:
        return _email(value)

    @field_validator("password")
    @classmethod
    def check_password(cls, value: str) -> str:
        return _new_password(value)

class UserUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: UserRole | None = None
    is_active: bool | None = None


# ---- Incidents -----------------------------------------------------------------

class IncidentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1)
    severity: Severity
    assignee_id: int | None = Field(default=None, gt=0, le=INT32_MAX)

class IncidentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, min_length=1)
    severity: Severity | None = None
    status: IncidentStatus | None = None
    assignee_id: int | None = Field(default=None, gt=0, le=INT32_MAX)

class IncidentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    title: str
    description: str
    severity: Severity
    status: IncidentStatus
    reporter_id: int | None
    assignee_id: int | None
    reporter: UserSummary | None
    assignee: UserSummary | None
    created_at: datetime
    updated_at: datetime

class IncidentPage(BaseModel):
    items: list[IncidentRead]
    total: int
    limit: int
    offset: int


# ---- Audit trail -------------------------------------------------------------------

class IncidentEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    action: IncidentEventAction
    # {"field": {"from": old, "to": new}}; only audited incident fields.
    changes: dict[str, dict[str, Any]]
    actor: UserSummary
    created_at: datetime

class IncidentEventPage(BaseModel):
    items: list[IncidentEventRead]
    total: int
    limit: int
    offset: int
    # People referenced by assignee changes on this page, so the client can show emails.
    users: list[UserSummary]


# ---- Comments ----------------------------------------------------------------------
# These rules are enforced identically by PostgreSQL check constraints (migration 0006);
# tests/test_comments.py checks that the API and the database accept and reject the same bodies.
# Lengths count Unicode code points, as PostgreSQL's char_length does in a UTF-8 database.

COMMENT_MAX_LENGTH = 5000
# Upper bound on the raw input before line endings are normalised and the ends trimmed.
COMMENT_RAW_MAX_LENGTH = 4 * COMMENT_MAX_LENGTH
# Trimmed from both ends: Unicode White_Space (other than the control characters refused below),
# plus zero-width space and the byte-order mark, so a comment can never be blank or invisible.
COMMENT_BLANK_CHARS = " \t\n\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u200b\u2028\u2029\u202f\u205f\u3000\ufeff"
# C0 controls except tab and line feed, DEL and C1 controls, and lone UTF-16 surrogates
# (which PostgreSQL cannot store). NUL is included: PostgreSQL text cannot hold it.
_COMMENT_FORBIDDEN = re.compile("[\x00-\x08\x0b-\x1f\x7f-\x9f\ud800-\udfff]")
COMMENT_TOO_LONG = f"Comments can be at most {COMMENT_MAX_LENGTH:,} characters."


def clean_comment_body(value: str) -> str:
    """Normalises line endings to LF, trims blank characters and enforces the comment rules."""
    if len(value) > COMMENT_RAW_MAX_LENGTH:
        raise ValueError(COMMENT_TOO_LONG)
    value = value.replace("\r\n", "\n").replace("\r", "\n").strip(COMMENT_BLANK_CHARS)
    if not value:
        raise ValueError("Enter a comment.")
    if _COMMENT_FORBIDDEN.search(value):
        raise ValueError("Comments cannot contain control characters.")
    if len(value) > COMMENT_MAX_LENGTH:
        raise ValueError(COMMENT_TOO_LONG)
    return value


class CommentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body: str
    # Required, with no default: the client must always say who may read the comment.
    visibility: CommentVisibility

    @field_validator("body")
    @classmethod
    def check_body(cls, value: str) -> str:
        return clean_comment_body(value)

class CommentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    visibility: CommentVisibility
    body: str
    author: UserSummary
    created_at: datetime

class CommentPage(BaseModel):
    items: list[CommentRead]
    # Counts only the comments this user may read.
    total: int
    limit: int
    offset: int


# ---- Analytics -----------------------------------------------------------------------

class AnalyticsRange(BaseModel):
    start_date: date
    end_date: date
    timezone: str
    bucket: Literal["day", "week"]

class AnalyticsTotals(BaseModel):
    incidents: int
    open: int
    resolved_or_closed: int

class StatusCount(BaseModel):
    status: IncidentStatus
    count: int

class SeverityCount(BaseModel):
    severity: Severity
    count: int

class TrendPoint(BaseModel):
    # Inclusive calendar dates; weekly periods are clipped to the selected range.
    period_start: date
    period_end: date
    created: int
    resolved: int

class SeverityResolution(BaseModel):
    severity: Severity
    resolved_count: int
    median_hours: float | None

class ResolutionMetrics(BaseModel):
    resolved_count: int
    median_hours: float | None
    p90_hours: float | None
    reopened_count: int
    # Legacy incidents (no `created` audit event) resolved in range, left out of the timings.
    excluded_legacy: int
    by_severity: list[SeverityResolution]

class AnalyticsSummary(BaseModel):
    scope: Literal["own", "all"]
    range: AnalyticsRange
    totals: AnalyticsTotals
    by_status: list[StatusCount]
    by_severity: list[SeverityCount]
    open_backlog_by_severity: list[SeverityCount]
    trend: list[TrendPoint]
    resolution: ResolutionMetrics
