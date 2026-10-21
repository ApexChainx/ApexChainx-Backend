from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.sla_dispute import DisputeStatus


class DisputeFlagRequest(BaseModel):
    dispute_reason: str = Field(..., min_length=10, description="Reason for disputing the SLA calculation")


class DisputeResolveRequest(BaseModel):
    resolution_notes: str = Field(..., min_length=10, description="Notes explaining the resolution decision")
    status: DisputeStatus = Field(..., description="Resolution outcome: resolved or rejected")
    apply_proposed: bool = Field(
        default=False, description="Whether to apply the proposed SLA result as the new latest"
    )


class DisputeResponse(BaseModel):
    id: UUID
    sla_result_id: int
    baseline_sla_result_id: int | None = None
    proposed_sla_result_id: int | None = None
    flagged_by: str
    dispute_reason: str
    flagged_at: datetime
    status: DisputeStatus
    resolved_by: str | None = None
    resolution_notes: str | None = None
    resolved_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class PaginatedDisputeList(BaseModel):
    """Envelope for `GET /sla/disputes` (#580).

    The endpoint used to answer with a bare array materialized from
    ``query.all()`` — every dispute in one response, with no way to page or to
    tell "end of list" from "this page happens to be short". The items array
    is still present, under `items`, alongside the metadata needed to page
    correctly (same shape as the webhook list envelope, #554).
    """

    items: list[DisputeResponse]
    total: int
    page: int
    page_size: int
    returned: int
    has_more: bool


class DisputeAuditLogResponse(BaseModel):
    id: UUID
    dispute_id: UUID
    action: str
    actor: str
    notes: str | None = None
    recorded_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CreateProposedSLARequest(BaseModel):
    severity: str
    mttr_minutes: int
    policy_version: str = "1.0"
    threshold_source: str = "config"
    notes: str | None = None
