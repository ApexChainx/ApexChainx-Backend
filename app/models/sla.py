from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import Severity


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
                "decision_trace": "MTTR 30 < 60 threshold, performance ratio 50%"
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
    reason_code: str | None = Field(None, description="Machine-readable reason code for the decision")
    decision_trace: str | None = Field(None, description="Machine-readable decision trace for audit")


class SLASeverityConfig(BaseModel):
    threshold_minutes: int = Field(..., ge=0)
    penalty_per_minute: int = Field(..., ge=0)
    reward_base: int = Field(..., ge=0)


class SLAConfigUpdateRequest(SLASeverityConfig):
    pass


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
