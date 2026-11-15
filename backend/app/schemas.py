from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field, field_validator
from .models import IncidentStatus, Severity, UserRole
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
