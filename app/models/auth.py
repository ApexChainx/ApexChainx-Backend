from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

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

    email: str
    password: str = Field(..., min_length=6)


class RegisterRequest(LoginRequest):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "email": "user@example.com",
                "password": "Password123!",
                "full_name": "Example User",
                "role": "engineer",
            }
        }
    )

    full_name: str = Field(..., min_length=1)
    role: Role = Role.engineer


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


class ProfileUpdateRequest(BaseModel):
    """Allowed mutable profile fields. Role and email changes are not permitted here."""
    full_name: str | None = Field(default=None, min_length=1, max_length=255)
    stellar_wallet: str | None = Field(default=None, max_length=255)
