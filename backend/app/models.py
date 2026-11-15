import enum
from datetime import datetime
from sqlalchemy import DateTime, Enum, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column
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

class Incident(Base):
    __tablename__ = "incidents"
    __table_args__ = (Index("ix_incidents_status_severity", "status", "severity"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[Severity] = mapped_column(Enum(Severity, name="incident_severity", values_callable=lambda x: [e.value for e in x]), nullable=False)
    status: Mapped[IncidentStatus] = mapped_column(Enum(IncidentStatus, name="incident_status", values_callable=lambda x: [e.value for e in x]), nullable=False, default=IncidentStatus.OPEN, server_default="open")
    assignee_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
