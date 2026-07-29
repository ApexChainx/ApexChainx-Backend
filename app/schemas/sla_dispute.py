from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field

from app.models.sla_dispute import DisputeStatus


class DisputeFlagRequest(BaseModel):
    flagged_by: str = Field(..., description="Identifier of the operator flagging the dispute")
    dispute_reason: str = Field(..., min_length=10, description="Reason for disputing the SLA calculation")


class DisputeResolveRequest(BaseModel):
    resolved_by: str = Field(..., description="Identifier of the operator resolving the dispute")
    resolution_notes: str = Field(..., min_length=10, description="Notes explaining the resolution decision")
    status: DisputeStatus = Field(..., description="Resolution outcome: resolved or rejected")
    apply_proposed: bool = Field(default=False, description="Whether to apply the proposed SLA result as the new latest")


class DisputeResponse(BaseModel):
    id: str
    sla_result_id: int
    baseline_sla_result_id: Optional[int] = None
    proposed_sla_result_id: Optional[int] = None
    flagged_by: str
    dispute_reason: str
    flagged_at: datetime
    status: DisputeStatus
    resolved_by: Optional[str] = None
    resolution_notes: Optional[str] = None
    resolved_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class DisputeAuditLogResponse(BaseModel):
    id: str
    dispute_id: str
    action: str
    actor: str
    notes: Optional[str] = None
    recorded_at: datetime

    class Config:
        from_attributes = True


class CreateProposedSLARequest(BaseModel):
    created_by: str
    severity: str
    mttr_minutes: int
    policy_version: str = "1.0"
    threshold_source: str = "config"
    notes: Optional[str] = None
