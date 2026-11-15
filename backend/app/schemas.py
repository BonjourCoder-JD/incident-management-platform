from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field
from .models import IncidentStatus, Severity

class IncidentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1)
    severity: Severity
    assignee_id: int | None = Field(default=None, gt=0)

class IncidentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, min_length=1)
    severity: Severity | None = None
    status: IncidentStatus | None = None
    assignee_id: int | None = Field(default=None, gt=0)

class IncidentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    title: str
    description: str
    severity: Severity
    status: IncidentStatus
    assignee_id: int | None
    created_at: datetime
    updated_at: datetime

class IncidentPage(BaseModel):
    items: list[IncidentRead]
    total: int
    limit: int
    offset: int
