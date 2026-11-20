"""Export request and generated artifact schemas for SLA operations."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_FILTER_KEYS = 50
MAX_FIELDS = 100
MAX_ERROR_LENGTH = 1000


class ExportFormat(str, Enum):
    csv = "csv"
    json = "json"
    xlsx = "xlsx"
    pdf = "pdf"

    @property
    def content_type(self) -> str:
        return {
            ExportFormat.csv: "text/csv",
            ExportFormat.json: "application/json",
            ExportFormat.xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ExportFormat.pdf: "application/pdf",
        }[self]


class ExportStatus(str, Enum):
    pending = "pending"
    processing = "processing"
    completed = "completed"
    failed = "failed"
    expired = "expired"


class ExportResource(str, Enum):
    outages = "outages"
    timeline_events = "timeline_events"
    sla_results = "sla_results"
    disputes = "disputes"
    payments = "payments"
    annotations = "annotations"


class ExportRequest(BaseModel):
    """Request to materialize a filtered dataset in a chosen format."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "resource": "outages",
                "format": "csv",
                "filters": {"status": "resolved"},
                "fields": ["id", "site_name"],
                "date_from": "2026-01-01T00:00:00Z",
            }
        }
    )

    resource: ExportResource
    format: ExportFormat
    filters: dict[str, Any] = Field(default_factory=dict, description="Service-defined exact-match filter keys")
    fields: list[str] | None = Field(default=None, description="Projected columns; None exports all available fields")
    date_from: datetime | None = None
    date_to: datetime | None = None
    include_metadata: bool = False
    requested_by: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("filters")
    @classmethod
    def validate_filters(cls, values: dict[str, Any]) -> dict[str, Any]:
        if len(values) > MAX_FILTER_KEYS:
            raise ValueError(f"filters cannot contain more than {MAX_FILTER_KEYS} keys")
        if any(not key.strip() for key in values):
            raise ValueError("filter keys cannot be blank")
        return values

    @field_validator("fields")
    @classmethod
    def validate_fields(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return None
        if not values:
            raise ValueError("fields cannot be empty when provided")
        if len(values) > MAX_FIELDS:
            raise ValueError(f"fields cannot contain more than {MAX_FIELDS} entries")
        if any(not value.strip() for value in values):
            raise ValueError("fields cannot contain blank values")
        return values

    @field_validator("date_from", "date_to")
    @classmethod
    def normalize_bound(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("export date bounds must be timezone-aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_range(self) -> ExportRequest:
        if self.date_from is not None and self.date_to is not None and self.date_from > self.date_to:
            raise ValueError("date_from cannot be after date_to")
        return self


class ExportArtifact(BaseModel):
    """Metadata for a generated file; the storage key is opaque, never a credential."""

    filename: str = Field(..., min_length=1, max_length=255)
    content_type: str = Field(..., min_length=1, description="MIME type of the generated file")
    size_bytes: int = Field(..., ge=0)
    checksum_sha256: str = Field(
        ..., pattern=r"^[0-9a-fA-F]{64}$", description="Hex-encoded SHA-256 digest of the artifact bytes"
    )
    storage_key: str = Field(..., min_length=1, description="Opaque object-store key, not a signed download URL")
    generated_at: datetime
    expires_at: datetime | None = None

    @field_validator("generated_at", "expires_at")
    @classmethod
    def normalize_timestamp(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("artifact timestamps must be timezone-aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_expiry(self) -> ExportArtifact:
        if self.expires_at is not None and self.generated_at is not None and self.expires_at < self.generated_at:
            raise ValueError("expires_at cannot be before generated_at")
        return self


class ExportResponse(BaseModel):
    """Status and optional artifact for an export job."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(..., min_length=1, max_length=128)
    resource: ExportResource
    format: ExportFormat
    status: ExportStatus
    requested_at: datetime
    completed_at: datetime | None = None
    artifact: ExportArtifact | None = None
    error: str | None = Field(default=None, max_length=MAX_ERROR_LENGTH)

    @field_validator("requested_at", "completed_at")
    @classmethod
    def normalize_timestamp(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("export timestamps must be timezone-aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_status(self) -> ExportResponse:
        if self.completed_at is not None and self.requested_at is not None and self.completed_at < self.requested_at:
            raise ValueError("completed_at cannot be before requested_at")
        if self.status is ExportStatus.completed and self.artifact is None:
            raise ValueError("a completed export must include its artifact")
        if self.status is ExportStatus.failed and not self.error:
            raise ValueError("a failed export must include an error message")
        return self
