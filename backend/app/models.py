import enum
from datetime import datetime
from typing import Any
from sqlalchemy import Boolean, CheckConstraint, DateTime, Enum, ForeignKey, Index, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
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
