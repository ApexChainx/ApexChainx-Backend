from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class OutageCreatedDetail(BaseModel):
    event_type: Literal["created"] = "created"
    site_name: str


class OutageUpdatedDetail(BaseModel):
    event_type: Literal["updated"] = "updated"
    changes: dict[str, Any] = Field(default_factory=dict)


class OutagePatchedDetail(BaseModel):
    event_type: Literal["patched"] = "patched"
    changes: dict[str, Any] = Field(default_factory=dict)


class OutageResolvedDetail(BaseModel):
    event_type: Literal["resolved"] = "resolved"
    mttr_minutes: int


class SLAComputedDetail(BaseModel):
    event_type: Literal["sla_computed"] = "sla_computed"
    status: str  # "met" | "violated"


class SLARecomputedDetail(BaseModel):
    event_type: Literal["sla_recomputed"] = "sla_recomputed"
    status: str  # "met" | "violated"


OutageEventDetail = (
    OutageCreatedDetail
    | OutageUpdatedDetail
    | OutagePatchedDetail
    | OutageResolvedDetail
    | SLAComputedDetail
    | SLARecomputedDetail
)

_DETAIL_MAP: dict[str, type] = {
    "created": OutageCreatedDetail,
    "updated": OutageUpdatedDetail,
    "patched": OutagePatchedDetail,
    "resolved": OutageResolvedDetail,
    "sla_computed": SLAComputedDetail,
    "sla_recomputed": SLARecomputedDetail,
}


def validate_event_detail(event_type: str, detail: dict[str, Any] | None) -> dict[str, Any]:
    """Validate and return the detail dict for a given event_type.

    Raises ValueError for unknown event types or invalid payloads.
    """
    model_cls = _DETAIL_MAP.get(event_type)
    if model_cls is None:
        raise ValueError(f"Unknown event_type: '{event_type}'")
    payload = {**(detail or {}), "event_type": event_type}
    validated = model_cls(**payload)
    return validated.model_dump(exclude={"event_type"})


class OutageEventResponse(BaseModel):
    id: str
    outage_id: str
    event_type: str
    schema_version: str
    detail: dict[str, Any] | None = None
    occurred_at: datetime
