"""Normalized outage/SLA timeline event and grouped timeline schemas."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_ID_LENGTH = 128
DEFAULT_SCHEMA_VERSION = "1.0"


class TimelineEventType(str, Enum):
    created = "created"
    detected = "detected"
    acknowledged = "acknowledged"
    updated = "updated"
    patched = "patched"
    status_changed = "status_changed"
    severity_changed = "severity_changed"
    mitigated = "mitigated"
    resolved = "resolved"
    sla_computed = "sla_computed"
    sla_recomputed = "sla_recomputed"


class TimelineGrouping(str, Enum):
    outage = "outage"
    hour = "hour"
    day = "day"
    event_type = "event_type"
    actor = "actor"


class TimelineEventBase(BaseModel):
    """Fields common to timeline event writes and normalized events."""

    event_type: TimelineEventType
    occurred_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Time the event happened; naive values are rejected and aware values are normalized to UTC",
    )
    actor: str | None = Field(
        default=None, min_length=1, max_length=128, description="Operator or system that caused the event"
    )
    detail: dict[str, Any] | None = Field(default=None, description="Event-specific payload with string keys")
    source: str | None = Field(
        default=None, min_length=1, max_length=64, description="Originating component or importer"
    )

    @field_validator("occurred_at")
    @classmethod
    def normalize_occurred_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("occurred_at must be timezone-aware")
        return value.astimezone(UTC)


class TimelineEventCreate(TimelineEventBase):
    """Input for recording a normalized event against an outage."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "outage_id": "outage-001",
                "event_type": "sla_computed",
                "occurred_at": "2026-01-01T10:00:00Z",
                "actor": "sla-worker",
                "detail": {"status": "met"},
            }
        }
    )

    outage_id: str = Field(..., min_length=1, max_length=MAX_ID_LENGTH)


class TimelineEvent(TimelineEventCreate):
    """Stored timeline event, decoupled from any persistence model."""

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "id": "evt_001",
                "outage_id": "outage-001",
                "event_type": "sla_computed",
                "occurred_at": "2026-01-01T10:00:00Z",
                "detail": {"status": "met"},
                "schema_version": "1.0",
            }
        },
    )

    id: str = Field(..., min_length=1, max_length=MAX_ID_LENGTH)
    schema_version: str = Field(default=DEFAULT_SCHEMA_VERSION, min_length=1)


class TimelineEventGroup(BaseModel):
    """A bucket of events sharing ``key``; ``total`` may exceed the events shown."""

    key: str = Field(..., min_length=1)
    group_by: TimelineGrouping
    total: int = Field(..., ge=0, description="All events in this bucket, including any omitted from the page")
    events: list[TimelineEvent] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_event_count(self) -> TimelineEventGroup:
        if len(self.events) > self.total:
            raise ValueError("a group cannot contain more events than its total")
        return self


class GroupedTimeline(BaseModel):
    """Timeline buckets for one or more outages with a reconciled total."""

    outage_id: str | None = Field(default=None, min_length=1, max_length=MAX_ID_LENGTH)
    groups: list[TimelineEventGroup]
    total_events: int = Field(..., ge=0, description="Sum of all group totals in the response")

    @model_validator(mode="after")
    def validate_groups(self) -> GroupedTimeline:
        keys = [(group.group_by, group.key) for group in self.groups]
        if len(set(keys)) != len(keys):
            raise ValueError("group keys must be unique within a timeline")
        if sum(group.total for group in self.groups) != self.total_events:
            raise ValueError("total_events must equal the sum of group totals")
        return self
