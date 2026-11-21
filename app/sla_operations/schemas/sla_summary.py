"""Typed transport schemas for SLA operation summary reporting.

The models describe analytics payloads only and deliberately avoid ORM imports so
they can be reused by API handlers, background tasks, and report exporters.
"""

from datetime import UTC, datetime
from enum import Enum
from typing import Annotated

from pydantic import AfterValidator, AliasChoices, BaseModel, ConfigDict, Field, model_validator


class SLAOperationsSchema(BaseModel):
    """Base model that accepts both mapping payloads and attribute-based objects."""

    model_config = ConfigDict(from_attributes=True)


class SLASummaryPeriod(str, Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"


class SLASeverityLevel(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class SLATrendGranularity(str, Enum):
    HOUR = "hour"
    DAY = "day"
    WEEK = "week"
    MONTH = "month"


class SLASummaryScope(str, Enum):
    GLOBAL = "global"
    SITE = "site"
    DEVICE = "device"
    SEVERITY = "severity"


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("datetimes must be timezone-aware")
    return value.astimezone(UTC)


UTCDateTime = Annotated[datetime, AfterValidator(_as_utc)]


def _validate_order(start: datetime, end: datetime, start_name: str, end_name: str) -> None:
    if end <= start:
        raise ValueError(f"{end_name} must be after {start_name}")


class SLAOperationKPIs(SLAOperationsSchema):
    """Core KPI values shared by operation summaries and KPI snapshots."""

    total_outages: int = Field(ge=0, description="Number of outages included in the reporting window")
    total_violations: int = Field(ge=0, description="Number of outages that breached their SLA target")
    availability_percentage: float = Field(ge=0.0, le=100.0)
    avg_mttr_minutes: float = Field(
        ge=0.0,
        validation_alias=AliasChoices("avg_mttr_minutes", "avg_mttr", "average_mttr_minutes"),
        description="Mean time to repair in minutes; accepts legacy 'avg_mttr' payloads",
    )
    total_rewards: float = Field(ge=0.0, description="Sum of positive SLA settlement amounts")
    total_penalties: float = Field(ge=0.0, description="Magnitude of negative SLA settlement amounts")
    net_payout: float = Field(description="Rewards minus penalties; negative when penalties dominate")

    @model_validator(mode="after")
    def validate_violation_count(self) -> "SLAOperationKPIs":
        if self.total_violations > self.total_outages:
            raise ValueError("total_violations cannot exceed total_outages")
        return self


class SLATrendPoint(SLAOperationsSchema):
    """One bucket of time-series SLA metrics."""

    bucket_start: UTCDateTime
    bucket_end: UTCDateTime
    total_outages: int = Field(ge=0)
    violations: int = Field(ge=0)
    availability_percentage: float = Field(ge=0.0, le=100.0)
    avg_mttr_minutes: float = Field(ge=0.0)
    rewards: float = Field(ge=0.0)
    penalties: float = Field(ge=0.0)
    net_payout: float
    severity: SLASeverityLevel | None = Field(
        default=None, description="Set when the series is filtered to a single severity"
    )

    @model_validator(mode="after")
    def validate_point(self) -> "SLATrendPoint":
        _validate_order(self.bucket_start, self.bucket_end, "bucket_start", "bucket_end")
        if self.violations > self.total_outages:
            raise ValueError("violations cannot exceed total_outages")
        return self


class SLAKPISnapshot(SLAOperationsSchema):
    """Immutable point-in-time capture of SLA KPIs for a reporting window."""

    snapshot_id: str = Field(min_length=1)
    scope: SLASummaryScope = SLASummaryScope.GLOBAL
    scope_key: str | None = Field(default=None, description="Site, device, or severity identifier when scoped")
    captured_at: UTCDateTime
    window_start: UTCDateTime
    window_end: UTCDateTime
    kpis: SLAOperationKPIs
    checksum: str = Field(min_length=8, description="Integrity digest of the captured KPI values")

    @model_validator(mode="after")
    def validate_window(self) -> "SLAKPISnapshot":
        _validate_order(self.window_start, self.window_end, "window_start", "window_end")
        return self


class SLAOperationSummary(SLAOperationsSchema):
    """Complete SLA operations report for one window and scope."""

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "summary_id": "sla-2026-w1",
                "scope": "global",
                "period": "weekly",
                "window_start": "2026-01-01T00:00:00Z",
                "window_end": "2026-01-08T00:00:00Z",
                "generated_at": "2026-01-08T01:00:00Z",
                "kpis": {
                    "total_outages": 10,
                    "total_violations": 2,
                    "availability_percentage": 99.5,
                    "avg_mttr_minutes": 18.0,
                    "total_rewards": 300.0,
                    "total_penalties": 50.0,
                    "net_payout": 250.0,
                },
                "trend": [],
            }
        },
    )

    summary_id: str = Field(min_length=1)
    scope: SLASummaryScope = SLASummaryScope.GLOBAL
    scope_key: str | None = None
    period: SLASummaryPeriod
    window_start: UTCDateTime
    window_end: UTCDateTime
    generated_at: UTCDateTime
    kpis: SLAOperationKPIs
    trend: list[SLATrendPoint] = Field(default_factory=list)
    generated_by: str | None = None

    @model_validator(mode="after")
    def validate_window(self) -> "SLAOperationSummary":
        _validate_order(self.window_start, self.window_end, "window_start", "window_end")
        return self


class SLASummaryQuery(SLAOperationsSchema):
    """Validated parameters for requesting an SLA operation summary."""

    period: SLASummaryPeriod
    window_start: UTCDateTime
    window_end: UTCDateTime
    granularity: SLATrendGranularity = SLATrendGranularity.DAY
    severity: SLASeverityLevel | None = None
    site_id: str | None = None
    limit: int = Field(default=100, ge=1, le=1000)

    @model_validator(mode="after")
    def validate_window(self) -> "SLASummaryQuery":
        _validate_order(self.window_start, self.window_end, "window_start", "window_end")
        return self


SLAOperationKPI = SLAOperationKPIs
SLAKpiSnapshot = SLAKPISnapshot
