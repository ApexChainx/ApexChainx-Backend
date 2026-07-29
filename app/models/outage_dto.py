from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.core.config import settings
from app.models.enums import OutageStatus, Severity

from .outage import Location


class OutageSortField(str, Enum):
    detected_at = "detected_at"
    site_name = "site_name"
    severity = "severity"
    status = "status"
    id = "id"


class OutageSortDirection(str, Enum):
    asc = "asc"
    desc = "desc"


class OutageCreate(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "outage-001",
                "site_name": "Site A",
                "site_id": "site-A",
                "severity": "high",
                "status": "open",
                "detected_at": "2026-01-01T00:00:00Z",
                "description": "Example outage affecting core API.",
                "affected_services": ["core-api"],
                "affected_subscribers": 50,
                "assigned_to": "oncall@example.com",
                "created_by": "reporter@example.com",
                "location": {"latitude": 40.7128, "longitude": -74.0060},
            }
        }
    )

    id: str = Field(..., min_length=1)
    site_name: str = Field(..., min_length=1)
    site_id: str | None = None
    severity: Severity
    status: OutageStatus
    detected_at: datetime
    description: str = Field(..., min_length=1)
    affected_services: list[str] = Field(..., min_length=1)
    affected_subscribers: int | None = Field(default=None, ge=0)
    assigned_to: str | None = None
    created_by: str | None = None
    location: Location | None = None

    @field_validator("site_name")
    @classmethod
    def validate_site_name_length(cls, v: str) -> str:
        if len(v) > settings.MAX_SITE_NAME_LENGTH:
            raise ValueError(f"site_name too long. Maximum length is {settings.MAX_SITE_NAME_LENGTH} characters.")
        return v

    @field_validator("description")
    @classmethod
    def validate_description_length(cls, v: str) -> str:
        if len(v) > settings.MAX_DESCRIPTION_LENGTH:
            raise ValueError(f"description too long. Maximum length is {settings.MAX_DESCRIPTION_LENGTH} characters.")
        return v

    @field_validator("affected_services")
    @classmethod
    def validate_affected_services_count(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("affected_services must contain at least one entry")
        if len(v) > settings.MAX_AFFECTED_SERVICES_COUNT:
            raise ValueError(f"too many affected services. Maximum allowed is {settings.MAX_AFFECTED_SERVICES_COUNT}.")
        return v

    @field_validator("detected_at")
    @classmethod
    def validate_detected_at_timezone(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValidationError("detected_at must be timezone-aware")
        # Normalize to UTC
        if v.tzinfo != UTC:
            v = v.astimezone(UTC)
        return v


class OutageUpdate(BaseModel):
    site_name: str | None = None
    severity: Severity | None = None
    status: OutageStatus | None = None
    resolved_at: datetime | None = None
    description: str | None = None
    affected_services: list[str] | None = None
    affected_subscribers: int | None = None
    assigned_to: str | None = None
    created_by: str | None = None
    location: Location | None = None


class ImportConsistency(str, Enum):
    atomic = "atomic"
    partial = "partial"


class BulkOutageCreate(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "outages": [
                    {
                        "id": "outage-001",
                        "site_name": "Site A",
                        "site_id": "site-A",
                        "severity": "high",
                        "status": "open",
                        "detected_at": "2026-01-01T00:00:00Z",
                        "description": "Example outage affecting core API.",
                        "affected_services": ["core-api"],
                        "assigned_to": "oncall@example.com",
                        "created_by": "reporter@example.com",
                        "location": {"latitude": 40.7128, "longitude": -74.0060},
                    }
                ]
            }
        }
    )

    outages: list[OutageCreate]

    @field_validator("outages")
    @classmethod
    def validate_bulk_count(cls, v: list[OutageCreate]) -> list[OutageCreate]:
        if len(v) > settings.MAX_BULK_OUTAGES_COUNT:
            raise ValueError(f"too many outages in bulk request. Maximum allowed is {settings.MAX_BULK_OUTAGES_COUNT}.")
        return v


# --- #215: Stable machine-readable import error shapes ---


class ImportFieldError(BaseModel):
    """A single field-level validation error within an import row."""

    field: Optional[str] = None
    type: Optional[str] = None
    message: str


class ImportRowResult(BaseModel):
    """Machine-readable result for a single import row."""

    row: int
    id: str | None = None
    status: str  # "ok" | "error"
    errors: list[ImportFieldError] | None = None
    outage_id: str | None = None
    persisted: bool | None = None
    duplicate: bool | None = None
    existing_id: str | None = None


class ImportResponse(BaseModel):
    """Top-level response for the import endpoint."""

    mode: str  # "dry_run" | "import"
    consistency: ImportConsistency
    total_rows: int
    persisted: int
    validated: int
    error_count: int
    errors: list[ImportRowResult]
    rows: list[ImportRowResult]
