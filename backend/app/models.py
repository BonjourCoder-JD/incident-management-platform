import enum
from datetime import datetime
from typing import Any
from sqlalchemy import BigInteger, Boolean, CheckConstraint, Computed, DateTime, Enum, ForeignKey, Index, Integer, SmallInteger, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .database import Base

class Severity(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

class IncidentStatus(str, enum.Enum):
    OPEN = "open"
    INVESTIGATING = "investigating"
    RESOLVED = "resolved"
    CLOSED = "closed"

class UserRole(str, enum.Enum):
    REPORTER = "reporter"
    RESPONDER = "responder"
    ADMIN = "admin"

class ExportOutcome(str, enum.Enum):
    # Only what the server knows: the export was started (streaming began) or refused.
    # A completed download is not recorded because the server cannot confirm it.
    INITIATED = "initiated"
    REJECTED = "rejected"

class CommentVisibility(str, enum.Enum):
    # public: the incident's reporter and all staff can read it. internal: responders and admins only.
    PUBLIC = "public"
    INTERNAL = "internal"

class SlaTarget(str, enum.Enum):
    RESPONSE = "response"
    RESOLUTION = "resolution"

class SlaPolicySource(str, enum.Enum):
    # default: seeded by migration 0007 (no author). admin: created through the API by an admin.
    DEFAULT = "default"
    ADMIN = "admin"

class TriageOutcome(str, enum.Enum):
    VALID = "valid"                    # a schema-valid suggestion, ready for human review
    INVALID_OUTPUT = "invalid_output"  # the model's output failed validation; nothing is stored from it
    REFUSED = "refused"                # the model declined (stop_reason "refusal")
    TIMEOUT = "timeout"
    PROVIDER_ERROR = "provider_error"
    ABANDONED = "abandoned"            # the request never finished (crash); charged at its full reservation

class TriageDecisionKind(str, enum.Enum):
    ACCEPTED = "accepted"
    ACCEPTED_WITH_EDITS = "accepted_with_edits"
    REJECTED = "rejected"

class IncidentEventAction(str, enum.Enum):
    CREATED = "created"
    UPDATED = "updated"
    STATUS_CHANGED = "status_changed"
    ASSIGNMENT_CHANGED = "assignment_changed"


def _enum(enum_class, name):
    return Enum(enum_class, name=name, values_callable=lambda x: [e.value for e in x])


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("email = lower(email)", name="ck_users_email_lowercase"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Stored normalised (trimmed, lower-case); uniqueness is therefore case-insensitive.
    email: Mapped[str] = mapped_column(String(254), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(_enum(UserRole, "user_role"), nullable=False, default=UserRole.REPORTER, server_default="reporter")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class AuthSession(Base):
    """Server-side login session. Only a SHA-256 hash of the cookie token is stored."""
    __tablename__ = "auth_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    csrf_token: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    user: Mapped[User] = relationship(lazy="joined")


class Incident(Base):
    __tablename__ = "incidents"
    __table_args__ = (
        Index("ix_incidents_status_severity", "status", "severity"),
        Index("ix_incidents_created_at", "created_at"),  # analytics date ranges (migration 0004)
        # Full-text search for triage duplicate candidates (migration 0008).
        Index("ix_incidents_search", "search_vector", postgresql_using="gin"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[Severity] = mapped_column(_enum(Severity, "incident_severity"), nullable=False)
    status: Mapped[IncidentStatus] = mapped_column(_enum(IncidentStatus, "incident_status"), nullable=False, default=IncidentStatus.OPEN, server_default="open")
    # NULL for incidents created before authentication existed; only responders and admins see those.
    reporter_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    assignee_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    # Title and description as a text-search vector (migration 0008). Generated and stored by
    # PostgreSQL on every insert and update, so it can never drift from the text; never written by
    # the application, never part of an API response, and not loaded unless asked for.
    search_vector: Mapped[Any] = mapped_column(
        TSVECTOR, Computed("to_tsvector('english'::regconfig, (((title)::text || ' '::text) || description))", persisted=True),
        deferred=True,
    )

    reporter: Mapped[User | None] = relationship(foreign_keys=[reporter_id], lazy="joined")
    assignee: Mapped[User | None] = relationship(foreign_keys=[assignee_id], lazy="joined")


class IncidentEvent(Base):
    """Append-only audit record of one incident mutation.

    UPDATE and DELETE are rejected by a database trigger (migration 0003). Both foreign keys
    are RESTRICT so an event never loses its incident or its actor.
    """
    __tablename__ = "incident_events"
    __table_args__ = (
        Index("ix_incident_events_incident_id_id", "incident_id", "id"),
        # First-resolution lookups for analytics (migration 0004); predicate matches analytics._resolution_event().
        Index(
            "ix_incident_events_first_resolution",
            "incident_id",
            "created_at",
            postgresql_where=text("(changes -> 'status' ->> 'to') IN ('resolved', 'closed')"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id", ondelete="RESTRICT"), nullable=False)
    # Always the authenticated user; never taken from the request body.
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    action: Mapped[IncidentEventAction] = mapped_column(_enum(IncidentEventAction, "incident_event_action"), nullable=False)
    # {"field": {"from": old, "to": new}} for allowlisted incident fields only (see app/audit.py).
    changes: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    actor: Mapped[User] = relationship(lazy="joined")


class IncidentExport(Base):
    """Append-only record of each CSV export request (migration 0005).

    UPDATE, DELETE and TRUNCATE are rejected by a database trigger; the actor foreign key is
    RESTRICT so a record never loses its user.
    """
    __tablename__ = "incident_exports"
    __table_args__ = (Index("ix_incident_exports_actor_id_created_at", "actor_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    outcome: Mapped[ExportOutcome] = mapped_column(_enum(ExportOutcome, "export_outcome"), nullable=False)
    # {"severity": ..., "status": ...}; validated enum values or null.
    filters: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    # Rows matching the request (for a rejection, the count that exceeded the limit).
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    row_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


# PostgreSQL regex bracket contents for the comment body constraints (same text as migration 0006).
_COMMENT_BLANK_SQL = r" \t\n\u00a0\u1680\u2000-\u200b\u2028\u2029\u202f\u205f\u3000\ufeff"
_COMMENT_FORBIDDEN_SQL = r"\u0001-\u0008\u000b-\u001f\u007f-\u009f"


class IncidentComment(Base):
    """Append-only discussion entry on an incident (migration 0006).

    The row is its own audit record: author, visibility, body and time can never change, because
    UPDATE, DELETE and TRUNCATE are rejected by a database trigger. Both foreign keys are RESTRICT.
    Body rules are enforced twice, identically: app.schemas.clean_comment_body and the check
    constraints below (see migration 0006 for their exact SQL).
    """
    __tablename__ = "incident_comments"
    __table_args__ = (
        # Serves both the reporter query (incident + public) and the staff query (incident prefix).
        Index("ix_incident_comments_incident_id_visibility_id", "incident_id", "visibility", "id"),
        CheckConstraint("char_length(body) BETWEEN 1 AND 5000", name="ck_incident_comments_body_length"),
        CheckConstraint(f"body !~ '[{_COMMENT_FORBIDDEN_SQL}]'", name="ck_incident_comments_body_chars"),
        CheckConstraint(
            f"body !~ '^[{_COMMENT_BLANK_SQL}]' AND body !~ '[{_COMMENT_BLANK_SQL}]$'", name="ck_incident_comments_body_trimmed"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id", ondelete="RESTRICT"), nullable=False)
    # Always the authenticated user; never taken from the request body.
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    # No default: every insert must state who may read the comment.
    visibility: Mapped[CommentVisibility] = mapped_column(_enum(CommentVisibility, "comment_visibility"), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    author: Mapped[User] = relationship(lazy="joined")


class SlaPolicyVersion(Base):
    """One immutable version of the SLA policy (migration 0007).

    A version applies to SLA cycles that start at or after its `created_at` (ties: highest id).
    Versions are never edited or deleted (trigger); a change is a new version with a reason.
    `created_at` is taken from the database clock while holding the SLA policy advisory lock,
    so it can never precede the start of a cycle that was already visible (see app/sla.py).
    """
    __tablename__ = "sla_policy_versions"
    __table_args__ = (
        CheckConstraint("(source = 'admin') = (created_by IS NOT NULL)", name="ck_sla_policy_versions_author"),
        CheckConstraint("char_length(reason) BETWEEN 1 AND 500", name="ck_sla_policy_versions_reason"),
        Index("ix_sla_policy_versions_created_at_id", "created_at", "id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[SlaPolicySource] = mapped_column(_enum(SlaPolicySource, "sla_policy_source"), nullable=False)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    author: Mapped[User | None] = relationship(lazy="joined")
    targets: Mapped[list["SlaPolicyTarget"]] = relationship(lazy="selectin", order_by="SlaPolicyTarget.severity")


class SlaPolicyTarget(Base):
    """Response and resolution targets for one severity in one policy version (append-only)."""
    __tablename__ = "sla_policy_targets"
    __table_args__ = (
        CheckConstraint("response_minutes BETWEEN 1 AND 43200", name="ck_sla_policy_targets_response"),
        CheckConstraint("resolution_minutes BETWEEN 1 AND 525600", name="ck_sla_policy_targets_resolution"),
        CheckConstraint("response_minutes <= resolution_minutes", name="ck_sla_policy_targets_order"),
    )

    version_id: Mapped[int] = mapped_column(ForeignKey("sla_policy_versions.id", ondelete="RESTRICT"), primary_key=True)
    severity: Mapped[Severity] = mapped_column(_enum(Severity, "incident_severity"), primary_key=True)
    response_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    resolution_minutes: Mapped[int] = mapped_column(Integer, nullable=False)


class IncidentEscalation(Base):
    """Append-only record of one automatic escalation (migration 0007).

    Created only by the SLA worker, never by a person: `triggered_by` is always 'system'.
    At most one row per incident, cycle and target (unique constraint), and only for a target
    that was still outstanding, under the incident row lock, when it was recorded.
    """
    __tablename__ = "incident_escalations"
    __table_args__ = (
        UniqueConstraint("incident_id", "cycle", "target", name="uq_incident_escalations_incident_cycle_target"),
        CheckConstraint("cycle >= 0", name="ck_incident_escalations_cycle"),
        CheckConstraint("target <> 'response' OR cycle = 0", name="ck_incident_escalations_response_first_cycle"),
        CheckConstraint("triggered_by = 'system'", name="ck_incident_escalations_system"),
        Index("ix_incident_escalations_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id", ondelete="RESTRICT"), nullable=False)
    cycle: Mapped[int] = mapped_column(Integer, nullable=False)
    target: Mapped[SlaTarget] = mapped_column(_enum(SlaTarget, "sla_target"), nullable=False)
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    policy_version_id: Mapped[int] = mapped_column(ForeignKey("sla_policy_versions.id", ondelete="RESTRICT"), nullable=False)
    triggered_by: Mapped[str] = mapped_column(String(16), nullable=False, server_default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class IncidentSlaCycle(Base):
    """Stored SLA read model: one row per SLA cycle of an audited incident (migration 0007).

    Maintained by app.sla_store in the same transaction as each incident change, under the
    incident row lock; incident_events remains the source of truth, and app.sla derives the same
    values from it independently (tests and `python -m app.cli sla-verify` compare the two).
    A null policy_version_id means the cycle is not tracked (it started before any policy).
    A trigger keeps history final: rows are never deleted, a completed cycle never changes, the
    start and frozen policy version never change, and deadlines can only become stricter.
    Ranks: 1 low, 2 medium, 3 high, 4 critical.
    """
    __tablename__ = "incident_sla_cycles"
    __table_args__ = (
        CheckConstraint("cycle >= 0", name="ck_incident_sla_cycles_cycle"),
        CheckConstraint(
            "cycle = 0 OR (response_rank IS NULL AND response_minutes IS NULL AND response_due_at IS NULL "
            "AND response_warning_at IS NULL AND acknowledged_at IS NULL)",
            name="ck_incident_sla_cycles_response_first_cycle",
        ),
        CheckConstraint("(policy_version_id IS NULL) = (resolution_minutes IS NULL)", name="ck_incident_sla_cycles_tracked"),
        CheckConstraint("resolution_rank BETWEEN 1 AND 4 AND (response_rank IS NULL OR response_rank BETWEEN 1 AND 4)", name="ck_incident_sla_cycles_rank"),
        # Outstanding targets, for overdue/approaching filters, backlog figures and the escalation worker.
        Index("ix_incident_sla_cycles_open_resolution", "resolution_due_at",
              postgresql_where=text("resolved_at IS NULL AND policy_version_id IS NOT NULL")),
        Index("ix_incident_sla_cycles_open_response", "response_due_at",
              postgresql_where=text("acknowledged_at IS NULL AND cycle = 0 AND policy_version_id IS NOT NULL")),
        Index("ix_incident_sla_cycles_breached", "incident_id", "cycle",
              postgresql_where=text("response_breached OR resolution_breached")),
    )

    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id", ondelete="RESTRICT"), primary_key=True)
    cycle: Mapped[int] = mapped_column(Integer, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    policy_version_id: Mapped[int | None] = mapped_column(ForeignKey("sla_policy_versions.id", ondelete="RESTRICT"), nullable=True)
    response_rank: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    response_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    response_warning_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_rank: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    resolution_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    resolution_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_warning_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    response_breached: Mapped[bool | None] = mapped_column(Boolean, Computed("acknowledged_at > response_due_at", persisted=True))
    resolution_breached: Mapped[bool | None] = mapped_column(Boolean, Computed("resolved_at > resolution_due_at", persisted=True))


class TriageRequest(Base):
    """One AI triage request and its budget reservation (migration 0008). Append-only.

    Written (committed) before the provider is called, under the triage budget lock, so concurrent
    requests can never spend more than the limits. Until a TriageSuggestion records the result,
    the full reservation counts as spent; a reservation left unresolved is closed as abandoned at
    its full cost (app/triage.py). The input itself is never stored, only its SHA-256.
    """
    __tablename__ = "triage_requests"
    __table_args__ = (
        CheckConstraint("reserved_microusd >= 0", name="ck_triage_requests_reserved"),
        Index("ix_triage_requests_requested_by_created_at", "requested_by", "created_at"),
        Index("ix_triage_requests_incident_id_created_at", "incident_id", "created_at"),
        Index("ix_triage_requests_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id", ondelete="RESTRICT"), nullable=False)
    requested_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False)
    # The incident's latest audit event when the input was read; a suggestion is stale once it changes.
    basis_event_id: Mapped[int | None] = mapped_column(ForeignKey("incident_events.id", ondelete="RESTRICT"), nullable=True)
    input_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    # Ids of the duplicate candidates sent (all visible to the requester).
    candidate_ids: Mapped[list[int]] = mapped_column(JSONB, nullable=False)
    reserved_microusd: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    requester: Mapped[User] = relationship(lazy="joined")


class TriageSuggestion(Base):
    """The result of one triage request (migration 0008). Append-only, at most one per request.

    Only validated fields are stored; a model output that fails validation is discarded and only
    its error category is kept. Costs are in micro-US$ (US$ per million tokens = micro-US$ per token).
    """
    __tablename__ = "triage_suggestions"
    __table_args__ = (
        CheckConstraint("cost_microusd >= 0", name="ck_triage_suggestions_cost"),
        CheckConstraint(
            "(outcome = 'valid') = (severity IS NOT NULL AND rationale IS NOT NULL AND next_steps IS NOT NULL AND duplicate_ids IS NOT NULL)",
            name="ck_triage_suggestions_valid_fields",
        ),
        CheckConstraint("rationale IS NULL OR char_length(rationale) BETWEEN 1 AND 600", name="ck_triage_suggestions_rationale"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("triage_requests.id", ondelete="RESTRICT"), nullable=False, unique=True)
    outcome: Mapped[TriageOutcome] = mapped_column(_enum(TriageOutcome, "triage_outcome"), nullable=False)
    error_category: Mapped[str | None] = mapped_column(String(48), nullable=True)
    severity: Mapped[Severity | None] = mapped_column(_enum(Severity, "incident_severity"), nullable=True)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_steps: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    duplicate_ids: Mapped[list[int] | None] = mapped_column(JSONB, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_microusd: Mapped[int] = mapped_column(Integer, nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    request: Mapped[TriageRequest] = relationship(lazy="joined")


class TriageDecision(Base):
    """A person's decision on a valid suggestion (migration 0008). Append-only, one per suggestion.

    An accepted severity is applied through the normal audited update path with the deciding user
    as the actor; `resulting_event_id` links that audit event back to the suggestion.
    """
    __tablename__ = "triage_decisions"
    __table_args__ = (
        CheckConstraint("reason IS NULL OR char_length(reason) BETWEEN 1 AND 500", name="ck_triage_decisions_reason"),
        CheckConstraint(
            "decision <> 'rejected' OR (applied_severity IS NULL AND resulting_event_id IS NULL AND note_comment_id IS NULL)",
            name="ck_triage_decisions_rejected_changes_nothing",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    suggestion_id: Mapped[int] = mapped_column(ForeignKey("triage_suggestions.id", ondelete="RESTRICT"), nullable=False, unique=True)
    decided_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    decision: Mapped[TriageDecisionKind] = mapped_column(_enum(TriageDecisionKind, "triage_decision_kind"), nullable=False)
    applied_severity: Mapped[Severity | None] = mapped_column(_enum(Severity, "incident_severity"), nullable=True)
    resulting_event_id: Mapped[int | None] = mapped_column(ForeignKey("incident_events.id", ondelete="RESTRICT"), nullable=True, unique=True)
    note_comment_id: Mapped[int | None] = mapped_column(ForeignKey("incident_comments.id", ondelete="RESTRICT"), nullable=True, unique=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    decider: Mapped[User] = relationship(lazy="joined")



class RateLimitEvent(Base):
    """One counted event for a sliding-window limit (migration 0009): failed sign-ins per email and
    per client IP, and public-demo writes per client IP. Keys are stored only as SHA-256 hashes.
    Shared by every process and kept across restarts; pruned by app.maintenance."""
    __tablename__ = "rate_limit_events"
    __table_args__ = (
        Index("ix_rate_limit_events_bucket_key_hash_created_at", "bucket", "key_hash", "created_at"),
        Index("ix_rate_limit_events_created_at", "created_at"),
        CheckConstraint("char_length(key_hash) = 64", name="ck_rate_limit_events_key_hash"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    bucket: Mapped[str] = mapped_column(String(32), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class CommentRedaction(Base):
    """An administrator's redaction of one comment (migration 0010, ISSUE-18). Append-only (trigger).
    The comment row stays unchanged as evidence; API readers withhold its body once this row exists."""
    __tablename__ = "comment_redactions"
    __table_args__ = (
        UniqueConstraint("comment_id", name="uq_comment_redactions_comment_id"),
        CheckConstraint("char_length(reason) BETWEEN 1 AND 500", name="ck_comment_redactions_reason_length"),
        CheckConstraint(f"reason !~ '[{_COMMENT_FORBIDDEN_SQL}]'", name="ck_comment_redactions_reason_chars"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    comment_id: Mapped[int] = mapped_column(ForeignKey("incident_comments.id", ondelete="RESTRICT"), nullable=False)
    redacted_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
