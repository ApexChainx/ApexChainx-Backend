"""Workspace-local input/output contracts for SLA outage analytics (#852).

The query model bounds every unbounded input (date range, metric list, page
size) so workspace services cannot build runaway aggregations. Monetary metrics
are expressed in major currency units, never minor units.
"""

from datetime import datetime, timedelta
from enum import Enum
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_QUERY_RANGE_DAYS = 366
MAX_METRICS_PER_QUERY = 10
MAX_SITE_IDS_PER_QUERY = 100
MAX_BUCKETS_PER_PAGE = 1000

SiteId = Annotated[str, Field(min_length=1, max_length=255)]
MetricValue = Annotated[float, Field(ge=0.0)]


class AnalyticsGranularity(str, Enum):
    HOUR = "hour"
    DAY = "day"
    WEEK = "week"
    MONTH = "month"


class AnalyticsMetric(str, Enum):
    OUTAGE_COUNT = "outage_count"
    VIOLATION_COUNT = "violation_count"
    AVAILABILITY_PERCENTAGE = "availability_percentage"
    MTTR_MINUTES = "mttr_minutes"
    PENALTY_AMOUNT = "penalty_amount"
    REWARD_AMOUNT = "reward_amount"


class AnalyticsGroupBy(str, Enum):
    SEVERITY = "severity"
    SITE_ID = "site_id"
    STATUS = "status"


class AnalyticsSeverity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class AnalyticsOrderBy(str, Enum):
    BUCKET_START = "bucket_start"
    SAMPLE_COUNT = "sample_count"


class AnalyticsSortDirection(str, Enum):
    ASC = "asc"
    DESC = "desc"


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value


class AnalyticsQueryParams(BaseModel):
    """Validated parameters for an outage/SLA analytics aggregation."""

    model_config = ConfigDict(extra="forbid")

    period_start: datetime
    period_end: datetime
    granularity: AnalyticsGranularity = AnalyticsGranularity.DAY
    metrics: list[AnalyticsMetric] = Field(..., min_length=1, max_length=MAX_METRICS_PER_QUERY)
    group_by: AnalyticsGroupBy | None = None
    site_ids: list[SiteId] = Field(default_factory=list, max_length=MAX_SITE_IDS_PER_QUERY)
    severities: list[AnalyticsSeverity] = Field(default_factory=list, max_length=len(AnalyticsSeverity))
    order_by: AnalyticsOrderBy = AnalyticsOrderBy.BUCKET_START
    sort_direction: AnalyticsSortDirection = AnalyticsSortDirection.ASC
    limit: int = Field(default=100, ge=1, le=MAX_BUCKETS_PER_PAGE)
    offset: int = Field(default=0, ge=0)
    include_partial: bool = Field(default=False, description="Include buckets whose period has not closed")

    @field_validator("period_start", "period_end")
    @classmethod
    def period_boundaries_are_aware(cls, value: datetime) -> datetime:
        return _require_aware(value)

    @model_validator(mode="after")
    def query_is_bounded(self) -> Self:
        if self.period_end <= self.period_start:
            raise ValueError("period_end must be after period_start")
        if self.period_end - self.period_start > timedelta(days=MAX_QUERY_RANGE_DAYS):
            raise ValueError(f"query range cannot exceed {MAX_QUERY_RANGE_DAYS} days")
        if len(set(self.metrics)) != len(self.metrics):
            raise ValueError("metrics must not contain duplicates")
        if len(set(self.site_ids)) != len(self.site_ids):
            raise ValueError("site_ids must not contain duplicates")
        if len(set(self.severities)) != len(self.severities):
            raise ValueError("severities must not contain duplicates")
        return self


class AnalyticsBucket(BaseModel):
    """One aggregated time bucket, optionally split by a dimension value."""

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    bucket_start: datetime
    bucket_end: datetime
    dimension_value: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="Value of the group_by dimension; null for an ungrouped bucket",
    )
    metric_values: dict[AnalyticsMetric, MetricValue]
    sample_count: int = Field(..., ge=0, description="Source records contributing to this bucket")

    @field_validator("bucket_start", "bucket_end")
    @classmethod
    def bucket_boundaries_are_aware(cls, value: datetime) -> datetime:
        return _require_aware(value)

    @model_validator(mode="after")
    def bucket_range_is_valid(self) -> Self:
        if self.bucket_end <= self.bucket_start:
            raise ValueError("bucket_end must be after bucket_start")
        return self


class AnalyticsAggregateResponse(BaseModel):
    """Paginated buckets plus totals for one analytics query."""

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    query: AnalyticsQueryParams
    buckets: list[AnalyticsBucket] = Field(default_factory=list)
    totals: dict[AnalyticsMetric, MetricValue]
    total_buckets: int = Field(..., ge=0, description="Bucket count before pagination")
    returned_buckets: int = Field(..., ge=0)
    total_records: int = Field(..., ge=0, description="Source records represented by all buckets")
    has_more: bool = False
    generated_at: datetime

    @field_validator("generated_at")
    @classmethod
    def generated_at_is_aware(cls, value: datetime) -> datetime:
        return _require_aware(value)

    @model_validator(mode="after")
    def response_matches_query(self) -> Self:
        if self.returned_buckets != len(self.buckets):
            raise ValueError("returned_buckets must equal the number of buckets")
        if self.returned_buckets > self.total_buckets:
            raise ValueError("returned_buckets cannot exceed total_buckets")
        if len(self.buckets) > self.query.limit:
            raise ValueError("a page cannot contain more buckets than query.limit")
        if sum(bucket.sample_count for bucket in self.buckets) > self.total_records:
            raise ValueError("bucket sample counts cannot exceed total_records")
        if set(self.query.metrics) - set(self.totals):
            raise ValueError("totals must contain every requested metric")
        if any(set(bucket.metric_values) - set(self.query.metrics) for bucket in self.buckets):
            raise ValueError("buckets may only contain requested metrics")
        expected_has_more = self.query.offset + self.returned_buckets < self.total_buckets
        if self.has_more is not expected_has_more:
            raise ValueError("has_more does not match the pagination window")
        return self


__all__ = [
    "MAX_BUCKETS_PER_PAGE",
    "MAX_QUERY_RANGE_DAYS",
    "AnalyticsAggregateResponse",
    "AnalyticsBucket",
    "AnalyticsGranularity",
    "AnalyticsGroupBy",
    "AnalyticsMetric",
    "AnalyticsOrderBy",
    "AnalyticsQueryParams",
    "AnalyticsSeverity",
    "AnalyticsSortDirection",
    "MetricValue",
    "SiteId",
]
