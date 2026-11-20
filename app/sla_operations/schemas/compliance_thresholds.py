"""Workspace-local contracts for SLA thresholds and compliance evaluations (#853).

Definitions, comparisons, and evaluation results are plain Pydantic models so
workspace evaluators can reuse them without depending on core ORM entities.
"""

from datetime import datetime
from enum import Enum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_COMPARISONS_PER_EVALUATION = 50


class ThresholdMetric(str, Enum):
    AVAILABILITY = "availability"
    MTTR_MINUTES = "mttr_minutes"
    VIOLATION_RATE = "violation_rate"
    PENALTY_AMOUNT = "penalty_amount"


class ThresholdOperator(str, Enum):
    GTE = "gte"
    GT = "gt"
    LTE = "lte"
    LT = "lt"
    EQ = "eq"


class SeverityLevel(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ThresholdSource(str, Enum):
    CONFIG = "config"
    CONTRACT = "contract"
    POLICY = "policy"


class ComplianceEntityType(str, Enum):
    DEVICE = "device"
    SITE = "site"
    OUTAGE = "outage"
    SLA_RESULT = "sla_result"


class ComplianceStatus(str, Enum):
    COMPLIANT = "compliant"
    NON_COMPLIANT = "non_compliant"


def meets_threshold(operator: ThresholdOperator, observed: float, limit: float) -> bool:
    """Return whether an observed value satisfies the comparison against a limit."""
    if operator is ThresholdOperator.GTE:
        return observed >= limit
    if operator is ThresholdOperator.GT:
        return observed > limit
    if operator is ThresholdOperator.LTE:
        return observed <= limit
    if operator is ThresholdOperator.LT:
        return observed < limit
    if operator is ThresholdOperator.EQ:
        return observed == limit
    raise ValueError(f"unsupported threshold operator: {operator}")


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value


class ThresholdDefinition(BaseModel):
    """One configured SLA limit for a metric and optional severity."""

    model_config = ConfigDict(extra="forbid")

    metric: ThresholdMetric
    severity: SeverityLevel | None = Field(
        default=None, description="Null means the limit applies to every severity"
    )
    operator: ThresholdOperator
    value: float = Field(..., ge=0.0, le=1_000_000.0)
    source: ThresholdSource = ThresholdSource.CONFIG
    policy_version: str = Field(default="1", pattern=r"^[1-9][0-9]*$")
    description: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def value_fits_metric(self) -> Self:
        if self.metric is ThresholdMetric.AVAILABILITY and not 0 < self.value <= 100:
            raise ValueError("availability thresholds must be within (0, 100]")
        if self.metric is ThresholdMetric.VIOLATION_RATE and not 0 <= self.value <= 1:
            raise ValueError("violation rate thresholds must be within [0, 1]")
        return self


class ThresholdComparison(BaseModel):
    """Observed metric value compared with a single threshold definition."""

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    threshold: ThresholdDefinition
    observed_value: float
    meets_threshold: bool
    delta: float = Field(
        default=0.0, description="Observed value minus the limit; recomputed during validation"
    )
    site_id: str | None = Field(default=None, min_length=1, max_length=255)
    evaluated_at: datetime | None = None

    @field_validator("evaluated_at")
    @classmethod
    def evaluated_at_is_aware(cls, value: datetime | None) -> datetime | None:
        return _require_aware(value) if value is not None else None

    @model_validator(mode="after")
    def result_matches_operator(self) -> Self:
        expected = meets_threshold(self.threshold.operator, self.observed_value, self.threshold.value)
        if self.meets_threshold is not expected:
            raise ValueError("meets_threshold does not match the configured operator and values")
        self.delta = self.observed_value - self.threshold.value
        return self


class ComplianceEvaluationResult(BaseModel):
    """Compliance verdict for one device, site, outage, or SLA result."""

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    entity_id: str = Field(..., min_length=1, max_length=255)
    entity_type: ComplianceEntityType
    status: ComplianceStatus
    score: float = Field(..., ge=0.0, le=100.0, description="Weighted compliance percentage")
    comparisons: list[ThresholdComparison] = Field(
        ..., min_length=1, max_length=MAX_COMPARISONS_PER_EVALUATION
    )
    violated_metrics: list[ThresholdMetric] = Field(
        default_factory=list, description="Derived from comparisons that do not meet their threshold"
    )
    evaluated_at: datetime

    @field_validator("evaluated_at")
    @classmethod
    def evaluated_at_is_aware(cls, value: datetime) -> datetime:
        return _require_aware(value)

    @model_validator(mode="after")
    def verdict_matches_comparisons(self) -> Self:
        policy_versions = {comparison.threshold.policy_version for comparison in self.comparisons}
        if len(policy_versions) != 1:
            raise ValueError("all comparisons must use the same policy version")
        failed = [comparison.threshold.metric for comparison in self.comparisons if not comparison.meets_threshold]
        if self.violated_metrics and set(self.violated_metrics) != set(failed):
            raise ValueError("violated_metrics does not match failed comparisons")
        self.violated_metrics = failed
        expected_status = (
            ComplianceStatus.NON_COMPLIANT if failed else ComplianceStatus.COMPLIANT
        )
        if self.status is not expected_status:
            raise ValueError("status does not match the comparison results")
        return self


__all__ = [
    "MAX_COMPARISONS_PER_EVALUATION",
    "ComplianceEntityType",
    "ComplianceEvaluationResult",
    "ComplianceStatus",
    "SeverityLevel",
    "ThresholdComparison",
    "ThresholdDefinition",
    "ThresholdMetric",
    "ThresholdOperator",
    "ThresholdSource",
    "meets_threshold",
]
