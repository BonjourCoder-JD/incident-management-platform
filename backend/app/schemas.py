import re
from datetime import date, datetime
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from typing import Annotated, Any, Literal
from .models import (
    CommentVisibility, IncidentEventAction, IncidentStatus, Severity, SlaPolicySource, SlaTarget, TriageDecisionKind, TriageOutcome, UserRole,
)
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

    @field_validator("email")
    @classmethod
    def check_email_characters(cls, value: str) -> str:
        # Only malformed input is described; whether an account exists is never revealed.
        if _COMMENT_FORBIDDEN.search(value):
            raise ValueError("Enter a valid email address.")
        return value

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

# C0 controls except tab, line feed and carriage return; DEL and C1 controls; lone surrogates.
_INCIDENT_TEXT_FORBIDDEN = re.compile(
    "[" + chr(0) + "-" + chr(8) + chr(11) + chr(12) + chr(14) + "-" + chr(0x1F) + chr(0x7F) + "-" + chr(0x9F)
    + chr(0xD800) + "-" + chr(0xDFFF) + "]")


def _incident_text(value: str) -> str:
    """BUG-2: control characters (including NUL, which PostgreSQL cannot store) and lone surrogates
    are refused with 422. Tab, line feed and carriage return stay allowed."""
    if _INCIDENT_TEXT_FORBIDDEN.search(value):
        raise ValueError("Remove control characters from this text.")
    return value


class IncidentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1)
    severity: Severity
    assignee_id: int | None = Field(default=None, gt=0, le=INT32_MAX)

    @field_validator("title", "description")
    @classmethod
    def check_text(cls, value: str) -> str:
        return _incident_text(value)

class IncidentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, min_length=1)
    severity: Severity | None = None
    status: IncidentStatus | None = None
    assignee_id: int | None = Field(default=None, gt=0, le=INT32_MAX)

    @field_validator("title", "description")
    @classmethod
    def check_text(cls, value: str | None) -> str | None:
        return None if value is None else _incident_text(value)

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

SlaStateName = Literal["on_track", "approaching", "overdue", "met", "breached", "not_tracked"]


class SlaSummary(BaseModel):
    """The most urgent current SLA target of an incident (see app/sla.py)."""
    state: Literal["on_track", "approaching", "overdue", "met", "breached"]
    target: SlaTarget
    due_at: datetime

class IncidentListItem(IncidentRead):
    # null when the incident is not tracked (legacy, or its cycle started before any policy).
    sla: SlaSummary | None = None
    # Staff only: an escalation exists for the current cycle. Always null for reporters.
    escalated: bool | None = None

class IncidentPage(BaseModel):
    items: list[IncidentListItem]
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
    # Staff only: the AI triage suggestion this change came from, if any (Milestone 5C). Null for reporters.
    triage_suggestion_id: int | None = None

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
    # None once an administrator has redacted the comment (ISSUE-18): the text is never sent again.
    body: str | None
    author: UserSummary
    created_at: datetime
    redacted: bool = False
    redacted_at: datetime | None = None
    # Responders and admins only; always None for reporters.
    redaction_reason: str | None = None

class CommentRedactionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(max_length=2000)

    @field_validator("reason")
    @classmethod
    def check_reason(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Enter a reason for removing this comment.")
        if _COMMENT_FORBIDDEN.search(value):
            raise ValueError("The reason cannot contain control characters.")
        if len(value) > 500:
            raise ValueError("The reason can be at most 500 characters.")
        return value

class CommentPage(BaseModel):
    items: list[CommentRead]
    # Counts only the comments this user may read.
    total: int
    limit: int
    offset: int


# ---- SLA -----------------------------------------------------------------------------

class SlaTargetStatus(BaseModel):
    target_minutes: int | None
    # The strictest severity held in the cycle until the target was satisfied (or until now).
    severity: Severity | None
    due_at: datetime | None
    warning_at: datetime | None
    satisfied_at: datetime | None
    state: SlaStateName

class SlaCycle(BaseModel):
    cycle: int
    started_at: datetime
    policy_version_id: int | None
    tracked: bool
    # Only cycle 0 has a response target.
    response: SlaTargetStatus | None
    resolution: SlaTargetStatus

class SlaEscalationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    cycle: int
    target: SlaTarget
    deadline_at: datetime
    created_at: datetime
    triggered_by: Literal["system"]

class IncidentSla(BaseModel):
    # Server time used for every state, so clients never need their own clock.
    now: datetime
    tracked: bool
    current_cycle: int
    cycles: list[SlaCycle]
    # Responders and admins only; always null for reporters.
    escalations: list[SlaEscalationRead] | None

SLA_RESPONSE_MAX_MINUTES = 30 * 24 * 60
SLA_RESOLUTION_MAX_MINUTES = 365 * 24 * 60
SLA_REASON_MAX_LENGTH = 500
class SlaTargetValues(BaseModel):
    model_config = ConfigDict(extra="forbid")
    severity: Severity
    response_minutes: Annotated[int, Field(strict=True, ge=1, le=SLA_RESPONSE_MAX_MINUTES)]
    resolution_minutes: Annotated[int, Field(strict=True, ge=1, le=SLA_RESOLUTION_MAX_MINUTES)]

    @model_validator(mode="after")
    def response_within_resolution(self):
        if self.response_minutes > self.resolution_minutes:
            raise ValueError("The response target cannot be longer than the resolution target.")
        return self

SEVERITY_ORDER = (Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW)

class SlaPolicyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str
    targets: list[SlaTargetValues] = Field(min_length=4, max_length=4)

    @field_validator("reason")
    @classmethod
    def check_reason(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Enter a reason for the change.")
        if _COMMENT_FORBIDDEN.search(value):
            raise ValueError("The reason cannot contain control characters.")
        if len(value) > SLA_REASON_MAX_LENGTH:
            raise ValueError(f"The reason can be at most {SLA_REASON_MAX_LENGTH} characters.")
        return value

    @model_validator(mode="after")
    def one_target_per_severity_in_order(self):
        by_severity = {target.severity: target for target in self.targets}
        if set(by_severity) != set(Severity):
            raise ValueError("Give exactly one target for each severity.")
        ordered = [by_severity[severity] for severity in SEVERITY_ORDER]
        for stricter, lighter in zip(ordered, ordered[1:]):
            if stricter.response_minutes > lighter.response_minutes or stricter.resolution_minutes > lighter.resolution_minutes:
                raise ValueError("A more severe incident cannot have a longer target than a less severe one.")
        return self

class SlaTargetRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    severity: Severity
    response_minutes: int
    resolution_minutes: int

class SlaPolicyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    source: SlaPolicySource
    reason: str
    author: UserSummary | None
    created_at: datetime
    targets: list[SlaTargetRead]


# ---- AI triage (Milestone 5C) ------------------------------------------------------------

class TriageLimits(BaseModel):
    user_daily_usd: float
    user_daily_remaining_usd: float
    user_hourly_requests: int
    user_hourly_remaining: int
    incident_cooldown_seconds: int

class TriageStatus(BaseModel):
    enabled: bool
    reason: str | None
    provider: Literal["off", "fake", "anthropic"]
    provider_label: str | None
    # False for the deterministic test provider, which must never be presented as AI.
    is_ai: bool
    model: str | None
    prompt_version: str
    # What the model receives: these incident fields and up to five candidate titles, nothing else.
    sent_fields: list[str]
    typical_cost_usd: float | None
    limits: TriageLimits | None

class TriageCandidate(BaseModel):
    id: int
    title: str

class TriageDecisionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    decision: TriageDecisionKind
    applied_severity: Severity | None
    resulting_event_id: int | None
    note_comment_id: int | None
    reason: str | None
    decider: UserSummary
    created_at: datetime

class TriageSuggestionRead(BaseModel):
    id: int
    incident_id: int
    outcome: TriageOutcome
    # Plain-language explanation for any outcome other than "valid".
    message: str | None
    severity: Severity | None
    rationale: str | None
    next_steps: list[str]
    duplicates: list[TriageCandidate]
    candidates_sent: list[TriageCandidate]
    provider: str
    model: str
    prompt_version: str
    requested_by: UserSummary
    created_at: datetime
    cost_usd: float
    # True once the incident has changed since the suggestion was made; it can then no longer be accepted.
    stale: bool
    decision: TriageDecisionRead | None

class TriageSuggestionPage(BaseModel):
    items: list[TriageSuggestionRead]

class TriageDecisionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["accept", "reject"]
    # Accept only: the severity to apply (defaults to the suggested one).
    severity: Severity | None = None
    add_internal_note: bool = False
    # Accept only: the internal note text (defaults to the rationale and next steps). Comment rules apply.
    note: str | None = Field(default=None, max_length=COMMENT_RAW_MAX_LENGTH)
    reason: str | None = Field(default=None, max_length=2000)

    @field_validator("reason")
    @classmethod
    def check_reason(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            return None
        if _COMMENT_FORBIDDEN.search(value):
            raise ValueError("The reason cannot contain control characters.")
        if len(value) > 500:
            raise ValueError("The reason can be at most 500 characters.")
        return value


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

class SlaComplianceCounts(BaseModel):
    met: int
    breached: int
    outstanding: int
    not_tracked: int

class SlaAnalytics(BaseModel):
    # First cycle of the incidents created in the range (reopened cycles are on the detail page).
    response: SlaComplianceCounts
    resolution: SlaComplianceCounts
    # Current backlog, whatever the range: open incidents with an overdue or approaching target.
    overdue_by_severity: list[SeverityCount]
    approaching_by_severity: list[SeverityCount]
    # Escalations recorded in the range. Responders and admins only; null for reporters.
    escalations: int | None

class AnalyticsSummary(BaseModel):
    scope: Literal["own", "all"]
    range: AnalyticsRange
    totals: AnalyticsTotals
    by_status: list[StatusCount]
    by_severity: list[SeverityCount]
    open_backlog_by_severity: list[SeverityCount]
    trend: list[TrendPoint]
    resolution: ResolutionMetrics
    sla: SlaAnalytics
