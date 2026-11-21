"""Typed outage insight payload schemas.

Severity mixes, MTTR aggregates, and lifecycle breakdowns are modeled without
SQLAlchemy or core ORM dependencies so reporting producers can emit them from
any data source.
"""

from datetime import UTC, datetime
from enum import Enum
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator


class OutageInsightsSchema(BaseModel):
    """Base model that accepts both mapping payloads and attribute-based objects."""

    model_config = ConfigDict(from_attributes=True)


class OutageSeverityLevel(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class OutageLifecycleStatus(str, Enum):
    ACTIVE = "active"
    INVESTIGATING = "investigating"
    RESOLVED = "resolved"


class OutageInsightGrouping(str, Enum):
    SEVERITY = "severity"
    STATUS = "status"
    SITE = "site"


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("datetimes must be timezone-aware")
    return value.astimezone(UTC)


UTCDateTime = Annotated[datetime, AfterValidator(_as_utc)]


def _validate_order(start: datetime, end: datetime, start_name: str, end_name: str) -> None:
    if end <= start:
        raise ValueError(f"{end_name} must be after {start_name}")


class SeverityDistributionEntry(OutageInsightsSchema):
    """Outage counts attributed to one severity level."""

    severity: OutageSeverityLevel
    outage_count: int = Field(ge=0)
    violation_count: int = Field(ge=0)
    affected_subscribers: int = Field(ge=0, description="Subscribers impacted by outages of this severity")
    percentage: float = Field(ge=0.0, le=100.0, description="Share of total outages, expressed as a percentage")

    @model_validator(mode="after")
    def validate_counts(self) -> "SeverityDistributionEntry":
        if self.violation_count > self.outage_count:
            raise ValueError("violation_count cannot exceed outage_count")
        return self


class MTTRAggregate(OutageInsightsSchema):
    """Summary statistics for mean time to repair, all values in minutes."""

    sample_size: int = Field(ge=0, description="Number of resolved outages contributing to these statistics")
    minimum_minutes: float = Field(ge=0.0)
    average_minutes: float = Field(ge=0.0)
    median_minutes: float = Field(ge=0.0)
    p95_minutes: float = Field(ge=0.0, description="95th percentile repair duration in minutes")
    maximum_minutes: float = Field(ge=0.0)

    @model_validator(mode="after")
    def validate_statistical_order(self) -> "MTTRAggregate":
        if self.minimum_minutes > self.average_minutes or self.average_minutes > self.maximum_minutes:
            raise ValueError("minimum_minutes <= average_minutes <= maximum_minutes is required")
        if self.minimum_minutes > self.median_minutes or self.median_minutes > self.maximum_minutes:
            raise ValueError("minimum_minutes <= median_minutes <= maximum_minutes is required")
        if self.p95_minutes > self.maximum_minutes:
            raise ValueError("p95_minutes cannot exceed maximum_minutes")
        return self


class StatusBreakdownEntry(OutageInsightsSchema):
    """Outage counts grouped by lifecycle status."""

    status: OutageLifecycleStatus
    outage_count: int = Field(ge=0)
    percentage: float = Field(ge=0.0, le=100.0)


class SiteOutageInsight(OutageInsightsSchema):
    """Per-site concentration used to highlight recurring outage hotspots."""

    site_id: str = Field(min_length=1)
    outage_count: int = Field(ge=0)
    critical_count: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_counts(self) -> "SiteOutageInsight":
        if self.critical_count > self.outage_count:
            raise ValueError("critical_count cannot exceed outage_count")
        return self


class OutageInsightQuery(OutageInsightsSchema):
    """Validated parameters for building an outage insight report."""

    window_start: UTCDateTime
    window_end: UTCDateTime
    grouping: OutageInsightGrouping = OutageInsightGrouping.SEVERITY
    severities: list[OutageSeverityLevel] = Field(default_factory=list)
    statuses: list[OutageLifecycleStatus] = Field(default_factory=list)
    site_id: str | None = None
    limit: int = Field(default=100, ge=1, le=1000)

    @model_validator(mode="after")
    def validate_window(self) -> "OutageInsightQuery":
        _validate_order(self.window_start, self.window_end, "window_start", "window_end")
        return self


class OutageInsightsReport(OutageInsightsSchema):
    """Outage insight payload with distributions, MTTR, and status breakdown."""

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "report_id": "outage-insights-2026-02",
                "window_start": "2026-02-01T00:00:00Z",
                "window_end": "2026-03-01T00:00:00Z",
                "generated_at": "2026-03-01T01:00:00Z",
                "total_outages": 10,
                "severity_distribution": [
                    {
                        "severity": "critical",
                        "outage_count": 3,
                        "violation_count": 2,
                        "affected_subscribers": 1200,
                        "percentage": 30.0,
                    }
                ],
                "mttr_aggregate": {
                    "sample_size": 5,
                    "minimum_minutes": 10.0,
                    "average_minutes": 20.0,
                    "median_minutes": 18.0,
                    "p95_minutes": 30.0,
                    "maximum_minutes": 30.0,
                },
                "status_breakdown": [{"status": "resolved", "outage_count": 7, "percentage": 70.0}],
            }
        },
    )

    report_id: str = Field(min_length=1)
    window_start: UTCDateTime
    window_end: UTCDateTime
    generated_at: UTCDateTime
    total_outages: int = Field(ge=0)
    severity_distribution: list[SeverityDistributionEntry]
    mttr_aggregate: MTTRAggregate
    status_breakdown: list[StatusBreakdownEntry]
    recurring_sites: list[SiteOutageInsight] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_report(self) -> "OutageInsightsReport":
        _validate_order(self.window_start, self.window_end, "window_start", "window_end")
        severity_count = sum(entry.outage_count for entry in self.severity_distribution)
        status_count = sum(entry.outage_count for entry in self.status_breakdown)
        if severity_count > self.total_outages:
            raise ValueError("severity_distribution counts cannot exceed total_outages")
        if status_count > self.total_outages:
            raise ValueError("status_breakdown counts cannot exceed total_outages")
        return self


SeverityDistribution = SeverityDistributionEntry
StatusBreakdown = StatusBreakdownEntry
OutageInsightReport = OutageInsightsReport
