from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class OperationalNotificationEventType(str, Enum):
    """Operational event types for SLA/outage-derived notifications."""

    SLA_VIOLATION = "sla.violation"
    SLA_WARNING = "sla.warning"
    SLA_RESOLVED = "sla.resolved"
    OUTAGE_DETECTED = "outage.detected"
    OUTAGE_RESOLVED = "outage.resolved"


class NotificationSeverity(str, Enum):
    """Operator-facing severity for notification routing and prioritization."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class NotificationSubject(BaseModel):
    """Resource identifiers associated with an operational notification."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    outage_id: str | None = Field(default=None, min_length=1, max_length=128)
    sla_result_id: int | None = Field(default=None, ge=1)
    payment_id: str | None = Field(default=None, min_length=1, max_length=128)
    site_id: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_has_identifier(self) -> "NotificationSubject":
        if not any([self.outage_id, self.sla_result_id, self.payment_id, self.site_id]):
            raise ValueError("subject must include at least one identifier")
        return self


class OperationalNotificationPayload(BaseModel):
    """Notification payload derived from SLA/outage operational events.

    `dedupe_key` can be supplied by producers to suppress duplicate downstream
    alerts for the same logical event occurrence.
    """

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        json_schema_extra={
            "example": {
                "event_id": "evt-20261120-0001",
                "event_type": "sla.violation",
                "severity": "critical",
                "occurred_at": "2026-11-20T13:30:00Z",
                "source": "sla_operations",
                "subject": {"outage_id": "outage-001", "sla_result_id": 101},
                "title": "SLA violated for outage-001",
                "message": "MTTR exceeded threshold for critical outage.",
                "attributes": {"mttr_minutes": 95, "threshold_minutes": 60},
                "dedupe_key": "sla.violation:outage-001:101",
            }
        },
    )

    event_id: str = Field(..., min_length=1, max_length=128)
    event_type: OperationalNotificationEventType
    severity: NotificationSeverity
    occurred_at: datetime
    source: str = Field(default="sla_operations", min_length=1, max_length=128)
    subject: NotificationSubject
    title: str = Field(..., min_length=1, max_length=200)
    message: str = Field(..., min_length=1, max_length=4000)
    attributes: dict[str, Any] = Field(default_factory=dict)
    dedupe_key: str | None = Field(default=None, min_length=1, max_length=256)

    @field_validator("title", "message", "source")
    @classmethod
    def validate_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must not be blank")
        return value

    @model_validator(mode="after")
    def validate_event_alignment(self) -> "OperationalNotificationPayload":
        if self.event_type.value.startswith("sla.") and self.subject.sla_result_id is None and self.subject.outage_id is None:
            raise ValueError("SLA notifications require subject.sla_result_id or subject.outage_id")
        if self.event_type.value.startswith("outage.") and self.subject.outage_id is None:
            raise ValueError("outage notifications require subject.outage_id")
        return self
