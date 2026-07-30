from typing import Any, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import Severity


class SLACalculationError(BaseModel):
    """Error branch for SLA computation failures.

    Returned when compute_device_sla encounters a recoverable error
    instead of raising an exception.  Consumers can pattern-match on
    the discriminated type to avoid try/except in hot paths.
    """

    device_id: str
    period: str
    error_code: str
    detail: str


class SLACalculationResult(BaseModel):
    """Structured result of compute_device_sla.

    Replaces the loose dict previously returned so consumers get
    compile-time guarantees and OpenAPI can reflect the exact shape.
    """

    device_id: str
    period: str
    period_start: str
    period_end: str
    total_outages: int = Field(ge=0)
    violated_outages: int = Field(ge=0)
    avg_mttr_minutes: float = Field(ge=0.0)
    availability_percentage: float = Field(ge=0.0, le=100.0)
    is_violated: bool
    sla_thresholds: dict[str, float]
    violation_reasons: list[str] = Field(default_factory=list)
    outage_details: list[dict[str, Any]] = Field(default_factory=list)


class SLAPreviewRequest(BaseModel):
    severity: Severity
    mttr_minutes: int


class SLAResult(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": 1,
                "outage_id": "outage-001",
                "status": "met",
                "mttr_minutes": 30,
                "threshold_minutes": 60,
                "amount": 100,
                "payment_type": "reward",
                "rating": "excellent",
                "reason_code": "met_excellent",
                "decision_trace": "MTTR 30 < 60 threshold, performance ratio 50%",
                "compute_hash": "abc123def456",
            }
        }
    )

    id: int | None = None
    outage_id: str
    status: Literal["met", "violated"]
    mttr_minutes: int
    threshold_minutes: int
    amount: int
    payment_type: Literal["reward", "penalty"]
    rating: Literal["exceptional", "excellent", "good", "poor"]
    policy_version: str = Field(..., description="Version of SLA policy used for this calculation")
    threshold_source: str = Field(..., description="Source of threshold values (e.g., 'config', 'contract')")
    reason_code: Optional[str] = Field(None, description="Machine-readable reason code for the decision")
    decision_trace: Optional[str] = Field(None, description="Machine-readable decision trace for audit")
    compute_hash: Optional[str] = Field(None, description="SHA-256 hash of inputs for idempotent recompute (#35)")


class SLASeverityConfig(BaseModel):
    threshold_minutes: int = Field(..., ge=0)
    penalty_per_minute: int = Field(..., ge=0)
    reward_base: int = Field(..., ge=0)


class SLAConfigUpdateRequest(SLASeverityConfig):
    pass


class SLAConfigHistoryEntry(BaseModel):
    """Entry in the SLA config history audit log (#37)."""

    severity: str
    policy_version: int
    threshold_minutes: int
    penalty_per_minute: int
    reward_base: int
    content_hash: str
    published_at: str
    published_by: Optional[str] = None


class SLAPerformanceAggregation(BaseModel):
    total_outages: int = Field(ge=0)
    violation_rate: float = Field(ge=0.0, le=1.0)
    avg_mttr: float = Field(ge=0.0)
    payout_sum: float


class SLADashboardKPI(BaseModel):
    total_outages: int = Field(ge=0)
    total_violations: int = Field(ge=0)
    total_rewards: float = Field(ge=0.0)
    total_penalties: float = Field(ge=0.0)
    net_payout: float


class SLATrendPoint(BaseModel):
    date: str
    total_outages: int = Field(ge=0)
    violations: int = Field(ge=0)
    rewards: float = Field(ge=0.0)
    penalties: float = Field(ge=0.0)


class SLAPolicyContent(SLASeverityConfig):
    """Full SLA policy config with content hash for integrity verification (#37)."""

    severity: str
    policy_version: int
    content_hash: str


class SLAAnalyticsSnapshot(BaseModel):
    id: int | None = None
    snapshot_key: str
    total_outages: int = Field(ge=0)
    total_violations: int = Field(ge=0)
    total_rewards: float = Field(ge=0.0)
    total_penalties: float = Field(ge=0.0)
    net_payout: float
    avg_mttr: float = Field(ge=0.0)
    checksum: str
    created_at: str | None = None
