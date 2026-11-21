from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class HealthState(str, Enum):
    """Normalized health state for operational indicators.

    Values align with common SLA/outage/payment/webhook monitoring semantics:
    healthy operations, degraded but functional behavior, or unhealthy failures.
    """

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


class HealthDomain(str, Enum):
    """Operational domain represented by a health indicator."""

    SLA = "sla"
    OUTAGE = "outage"
    PAYMENT = "payment"
    WEBHOOK = "webhook"


class DomainHealthIndicator(BaseModel):
    """Health indicator for a single operational domain.

    The `details` payload is intentionally untyped-by-domain to allow reuse across
    SLA, outage, payment, and webhook sources without coupling to ORM or service
    implementations.
    """

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        json_schema_extra={
            "example": {
                "domain": "payment",
                "state": "degraded",
                "summary": "Retry backlog above threshold",
                "observed_at": "2026-11-20T13:15:00Z",
                "metric_value": 42.0,
                "metric_unit": "pending_payments",
                "details": {"pending": 42, "failed_last_hour": 3},
            }
        },
    )

    domain: HealthDomain
    state: HealthState
    summary: str = Field(..., min_length=1, max_length=500)
    observed_at: datetime | None = Field(
        default=None,
        description="Timestamp when the indicator was observed; may be omitted for synthetic checks.",
    )
    metric_value: float | None = Field(
        default=None,
        description="Optional primary numeric signal for the indicator (e.g. error rate or backlog size).",
    )
    metric_unit: str | None = Field(
        default=None,
        max_length=64,
        description="Unit or label for `metric_value` when provided.",
    )
    details: dict[str, Any] = Field(default_factory=dict)

    @field_validator("summary")
    @classmethod
    def validate_summary_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("summary must not be blank")
        return value


class OperationalHealthSummary(BaseModel):
    """Aggregate counts derived from domain health indicators."""

    model_config = ConfigDict(extra="forbid")

    total_indicators: int = Field(..., ge=0)
    healthy_count: int = Field(..., ge=0)
    degraded_count: int = Field(..., ge=0)
    unhealthy_count: int = Field(..., ge=0)

    @model_validator(mode="after")
    def validate_counts_match_total(self) -> "OperationalHealthSummary":
        computed = self.healthy_count + self.degraded_count + self.unhealthy_count
        if computed != self.total_indicators:
            raise ValueError("summary counts must equal total_indicators")
        return self


class AggregatedOperationalHealthResponse(BaseModel):
    """Aggregated operational health response across monitored domains."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        json_schema_extra={
            "example": {
                "overall_state": "degraded",
                "generated_at": "2026-11-20T13:16:00Z",
                "summary": {
                    "total_indicators": 3,
                    "healthy_count": 2,
                    "degraded_count": 1,
                    "unhealthy_count": 0,
                },
                "indicators": [
                    {
                        "domain": "sla",
                        "state": "healthy",
                        "summary": "SLA computations within threshold",
                    },
                    {
                        "domain": "outage",
                        "state": "healthy",
                        "summary": "No open critical outages",
                    },
                    {
                        "domain": "payment",
                        "state": "degraded",
                        "summary": "Retry backlog above threshold",
                        "metric_value": 42.0,
                        "metric_unit": "pending_payments",
                    },
                ],
            }
        },
    )

    overall_state: HealthState
    indicators: list[DomainHealthIndicator] = Field(..., min_length=1)
    summary: OperationalHealthSummary
    generated_at: datetime | None = None

    @model_validator(mode="after")
    def validate_aggregation_consistency(self) -> "AggregatedOperationalHealthResponse":
        counts = {
            HealthState.HEALTHY: 0,
            HealthState.DEGRADED: 0,
            HealthState.UNHEALTHY: 0,
        }
        for indicator in self.indicators:
            counts[indicator.state] += 1

        if counts[HealthState.UNHEALTHY] > 0:
            expected_overall = HealthState.UNHEALTHY
        elif counts[HealthState.DEGRADED] > 0:
            expected_overall = HealthState.DEGRADED
        else:
            expected_overall = HealthState.HEALTHY

        if self.overall_state != expected_overall:
            raise ValueError("overall_state does not match indicator states")
        if self.summary.total_indicators != len(self.indicators):
            raise ValueError("summary.total_indicators must match number of indicators")
        if self.summary.healthy_count != counts[HealthState.HEALTHY]:
            raise ValueError("summary.healthy_count does not match indicators")
        if self.summary.degraded_count != counts[HealthState.DEGRADED]:
            raise ValueError("summary.degraded_count does not match indicators")
        if self.summary.unhealthy_count != counts[HealthState.UNHEALTHY]:
            raise ValueError("summary.unhealthy_count does not match indicators")
        return self
