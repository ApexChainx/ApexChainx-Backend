"""Operator annotation and note schemas for SLA operations records."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_BODY_LENGTH = 5000
MAX_TAG_LENGTH = 64
MAX_TAGS = 20
MAX_REFERENCE_LENGTH = 128


class AnnotationKind(str, Enum):
    note = "note"
    investigation = "investigation"
    decision = "decision"
    escalation = "escalation"
    resolution = "resolution"
    follow_up = "follow_up"


class AnnotationVisibility(str, Enum):
    internal = "internal"
    team = "team"
    public = "public"


class AnnotationRecordType(str, Enum):
    outage = "outage"
    sla_result = "sla_result"
    dispute = "dispute"
    payment = "payment"
    timeline_event = "timeline_event"
    export = "export"


class RecordReference(BaseModel):
    """Logical link to an operational record; intentionally not an ORM reference."""

    record_type: AnnotationRecordType
    record_id: str = Field(..., min_length=1, max_length=MAX_REFERENCE_LENGTH)


class AnnotationContent(BaseModel):
    """Note content shared by create, update, and stored annotation models."""

    kind: AnnotationKind = Field(default=AnnotationKind.note)
    visibility: AnnotationVisibility = Field(default=AnnotationVisibility.internal)
    body: str = Field(..., min_length=1, max_length=MAX_BODY_LENGTH, description="Note text, plain text or Markdown")
    tags: list[str] = Field(default_factory=list, max_length=MAX_TAGS)

    @field_validator("body")
    @classmethod
    def body_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("body cannot be blank")
        return value

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("tags cannot contain blank values")
        if any(len(value) > MAX_TAG_LENGTH for value in values):
            raise ValueError(f"tags cannot exceed {MAX_TAG_LENGTH} characters")
        return values


class AnnotationCreate(AnnotationContent, RecordReference):
    """Operator note submission; timestamps and identity are supplied explicitly."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "record_type": "outage",
                "record_id": "outage-001",
                "body": "Confirmed fiber cut with the field team.",
                "kind": "investigation",
                "visibility": "internal",
                "tags": ["field"],
            }
        }
    )

    author: str = Field(..., min_length=1, max_length=MAX_REFERENCE_LENGTH, description="Operator identity")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Client-provided creation time; naive values are rejected and aware values normalized to UTC",
    )

    @field_validator("created_at")
    @classmethod
    def normalize_created_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")
        return value.astimezone(UTC)


class Annotation(AnnotationContent, RecordReference):
    """Persisted annotation returned to workspace callers."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(..., min_length=1, max_length=MAX_REFERENCE_LENGTH)
    author: str = Field(..., min_length=1, max_length=MAX_REFERENCE_LENGTH)
    created_at: datetime
    updated_at: datetime | None = None

    @field_validator("created_at", "updated_at")
    @classmethod
    def normalize_timestamp(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("annotation timestamps must be timezone-aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_update_order(self) -> Annotation:
        if self.updated_at is not None and self.created_at is not None and self.updated_at < self.created_at:
            raise ValueError("updated_at cannot be before created_at")
        return self


class AnnotationUpdate(BaseModel):
    """Partial annotation edit; at least one field must be provided."""

    kind: AnnotationKind | None = None
    visibility: AnnotationVisibility | None = None
    body: str | None = Field(default=None, min_length=1, max_length=MAX_BODY_LENGTH)
    tags: list[str] | None = Field(default=None, max_length=MAX_TAGS)

    @field_validator("body")
    @classmethod
    def body_must_not_be_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("body cannot be blank")
        return value

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return None
        if any(not value.strip() for value in values):
            raise ValueError("tags cannot contain blank values")
        if any(len(value) > MAX_TAG_LENGTH for value in values):
            raise ValueError(f"tags cannot exceed {MAX_TAG_LENGTH} characters")
        return values

    @model_validator(mode="after")
    def require_a_change(self) -> AnnotationUpdate:
        if not self.model_fields_set:
            raise ValueError("at least one annotation field must be provided")
        return self
