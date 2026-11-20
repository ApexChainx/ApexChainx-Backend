"""Typed webhook monitoring schemas.

These models cover endpoint health, delivery attempt history, and aggregate
monitoring views without importing webhook ORM or dispatch internals.
"""

from datetime import UTC, datetime
from enum import Enum
from typing import Annotated
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator


class WebhookMonitoringSchema(BaseModel):
    """Base model that accepts both mapping payloads and attribute-based objects."""

    model_config = ConfigDict(from_attributes=True)


class WebhookDeliveryState(str, Enum):
    PENDING = "pending"
    SENDING = "sending"
    RETRYING = "retrying"
    SUCCESS = "success"
    FAILED = "failed"
    DEAD_LETTER = "dead_letter"
    BREAKER_OPEN = "breaker_open"


class WebhookHealthState(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


class WebhookEventType(str, Enum):
    SLA_VIOLATION = "sla.violation"
    SLA_WARNING = "sla.warning"
    SLA_RESOLVED = "sla.resolved"


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("datetimes must be timezone-aware")
    return value.astimezone(UTC)


UTCDateTime = Annotated[datetime, AfterValidator(_as_utc)]


def _validate_order(start: datetime, end: datetime, start_name: str, end_name: str) -> None:
    if end <= start:
        raise ValueError(f"{end_name} must be after {start_name}")


class DeliveryAttemptSummary(WebhookMonitoringSchema):
    """One outbound delivery attempt and its observable result."""

    attempt_number: int = Field(ge=1)
    state: WebhookDeliveryState
    attempted_at: UTCDateTime
    response_status_code: int | None = Field(default=None, ge=100, le=599)
    duration_ms: float | None = Field(default=None, ge=0.0)
    error_code: str | None = None


class WebhookDeliverySummary(WebhookMonitoringSchema):
    """Compact delivery record with optional recent attempt details."""

    delivery_id: UUID
    webhook_id: UUID
    event: WebhookEventType
    state: WebhookDeliveryState
    attempt_count: int = Field(ge=0, description="Total attempts made, including those omitted from attempts")
    created_at: UTCDateTime
    last_attempt_at: UTCDateTime | None = None
    next_retry_at: UTCDateTime | None = None
    delivered_at: UTCDateTime | None = None
    error_message: str | None = None
    attempts: list[DeliveryAttemptSummary] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_attempts(self) -> "WebhookDeliverySummary":
        for field_name in ("last_attempt_at", "next_retry_at", "delivered_at"):
            timestamp = getattr(self, field_name)
            if timestamp is not None and timestamp < self.created_at:
                raise ValueError(f"{field_name} cannot precede created_at")
        if self.attempts and max(item.attempt_number for item in self.attempts) > self.attempt_count:
            raise ValueError("attempt_number cannot exceed attempt_count")
        return self


class WebhookHealth(WebhookMonitoringSchema):
    """Operational health snapshot for a single registered webhook."""

    webhook_id: UUID
    name: str = Field(min_length=1)
    state: WebhookHealthState
    is_active: bool
    max_retries: int = Field(ge=0, le=10, description="Configured retry ceiling for a delivery")
    success_rate: float = Field(ge=0.0, le=100.0, description="Successful deliveries as a percentage")
    average_latency_ms: float = Field(ge=0.0)
    consecutive_failures: int = Field(ge=0)
    pending_deliveries: int = Field(ge=0)
    dead_lettered_deliveries: int = Field(ge=0)
    last_success_at: UTCDateTime | None = None
    last_failure_at: UTCDateTime | None = None


class WebhookMonitoringView(WebhookMonitoringSchema):
    """Aggregate monitoring view for a reporting window."""

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "generated_at": "2026-04-01T01:00:00Z",
                "window_start": "2026-04-01T00:00:00Z",
                "window_end": "2026-04-01T02:00:00Z",
                "total_webhooks": 1,
                "healthy_count": 1,
                "degraded_count": 0,
                "unhealthy_count": 0,
                "unknown_count": 0,
                "delivery_state_counts": {"success": 1},
                "webhooks": [],
                "deliveries": [],
            }
        },
    )

    generated_at: UTCDateTime
    window_start: UTCDateTime
    window_end: UTCDateTime
    total_webhooks: int = Field(ge=0)
    healthy_count: int = Field(ge=0)
    degraded_count: int = Field(ge=0)
    unhealthy_count: int = Field(ge=0)
    unknown_count: int = Field(default=0, ge=0)
    delivery_state_counts: dict[WebhookDeliveryState, int] = Field(default_factory=dict)
    webhooks: list[WebhookHealth] = Field(default_factory=list)
    deliveries: list[WebhookDeliverySummary] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_counts(self) -> "WebhookMonitoringView":
        _validate_order(self.window_start, self.window_end, "window_start", "window_end")
        health_total = self.healthy_count + self.degraded_count + self.unhealthy_count + self.unknown_count
        if health_total > self.total_webhooks:
            raise ValueError("health counts cannot exceed total_webhooks")
        return self


class WebhookMonitoringQuery(WebhookMonitoringSchema):
    """Validated parameters for a webhook monitoring view request."""

    window_start: UTCDateTime
    window_end: UTCDateTime
    webhook_ids: list[UUID] = Field(default_factory=list)
    states: list[WebhookDeliveryState] = Field(default_factory=list)
    include_successful: bool = True
    limit: int = Field(default=100, ge=1, le=500)

    @model_validator(mode="after")
    def validate_window(self) -> "WebhookMonitoringQuery":
        _validate_order(self.window_start, self.window_end, "window_start", "window_end")
        return self


DeliveryAttempt = DeliveryAttemptSummary
WebhookHealthSnapshot = WebhookHealth
WebhookMonitoringSnapshot = WebhookMonitoringView
