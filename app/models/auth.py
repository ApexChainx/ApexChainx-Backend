from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models.enums import Role


class AuthUser(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "user-123",
                "email": "user@example.com",
                "full_name": "Example User",
                "role": "engineer",
                "stellar_wallet": "GCFX...EXAMPLE",
                "created_at": "2026-01-01T12:00:00Z",
            }
        }
    )

    id: str
    email: str
    full_name: str | None = None
    role: Role = Role.engineer
    stellar_wallet: str | None = None
    created_at: datetime


class LoginRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "email": "user@example.com",
                "password": "Password123!",
            }
        }
    )

    email: EmailStr
    password: str = Field(..., min_length=6)

    @field_validator("email", mode="before")
    @classmethod
    def _normalize_email(cls, value):
        """Trim surrounding whitespace and lowercase the address (#663)."""
        if isinstance(value, str):
            return value.strip().lower()
        return value


class RegisterRequest(LoginRequest):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "email": "user@example.com",
                "password": "Password123!",
                "full_name": "Example User",
            }
        }
    )

    full_name: str = Field(..., min_length=1)
    # role is intentionally omitted — public registration always creates
    # an engineer account.  Admin users must be created via the admin-only
    # POST /auth/admin/users endpoint.

    @field_validator("full_name")
    @classmethod
    def _sanitize_full_name(cls, value: str) -> str:
        """Strip surrounding whitespace and reject control characters (#663)."""
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("full_name must not be empty")
        if any(not ch.isprintable() for ch in cleaned):
            raise ValueError("full_name must contain printable characters only")
        return cleaned


class AuthSessionResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = 3600
    user: AuthUser


class AuthLogoutResponse(BaseModel):
    message: str


class SessionInfo(BaseModel):
    """Session information for session inventory (excludes full token material)."""

    access_token_preview: str | None = None
    refresh_token_preview: str | None = None
    email: str
    expires_at: datetime
    created_at: datetime
    is_active: bool


class SessionInventoryResponse(BaseModel):
    """Response for session inventory endpoint."""

    sessions: list[SessionInfo]
    total_count: int
    active_count: int


class LogoutAllSessionsResponse(BaseModel):
    """Response for logout-all-sessions endpoint."""

    message: str
    sessions_invalidated: int


class AdminCreateUserRequest(BaseModel):
    """Request body for the admin-only user creation endpoint."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "email": "newuser@example.com",
                "password": "Password123!",
                "full_name": "New User",
                "role": "engineer",
            }
        }
    )

    email: EmailStr
    password: str = Field(..., min_length=6)
    full_name: str = Field(..., min_length=1)
    role: Role = Role.engineer


class ProfileUpdateRequest(BaseModel):
    """Allowed mutable profile fields. Role and email changes are not permitted here."""

    full_name: str | None = Field(default=None, min_length=1, max_length=255)
    stellar_wallet: str | None = Field(default=None, max_length=255)
