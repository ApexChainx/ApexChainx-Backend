"""Workspace-local SLA summary API router.

This module exposes example read-only SLA summary endpoints backed by
workspace services and deterministic fixtures. It is intentionally isolated
from the core application routing (``app/api/v1/router.py`` and
``app/main.py``) and can be imported independently.

Assumptions:
- The workspace schemas live in ``app/sla_operations/schemas/sla_summary.py``.
- The aggregation helpers live in
  ``app/sla_operations/services/sla_metrics_aggregation.py``.
- Deterministic sample data lives in
  ``app/sla_operations/fixtures/sla_metrics_fixtures.py``.

All handlers are side-effect free and return deterministic responses for the
provided sample inputs.
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, status

from app.sla_operations.fixtures import sla_metrics_fixtures
from app.sla_operations.schemas.sla_summary import (
    SlaSummaryListResponse,
    SlaSummaryQuery,
    SlaSummaryResponse,
)
from app.sla_operations.services import sla_metrics_aggregation

router = APIRouter(
    prefix="/sla-summary",
    tags=["SLA Operations Summary"],
)


def _build_query(
    service: Optional[str],
    region: Optional[str],
    window_days: int,
) -> SlaSummaryQuery:
    """Construct and validate a summary query from raw request params."""
    return SlaSummaryQuery(
        service=service,
        region=region,
        window_days=window_days,
    )


@router.get(
    "",
    response_model=SlaSummaryListResponse,
    summary="List deterministic SLA summaries",
    description=(
        "Return a deterministic list of SLA summary records for the "
        "workspace sample data. Read-only and side-effect free."
    ),
)
def list_sla_summaries(
    service: Optional[str] = Query(
        default=None,
        description="Optional service name filter.",
    ),
    region: Optional[str] = Query(
        default=None,
        description="Optional region filter.",
    ),
    window_days: int = Query(
        default=30,
        ge=1,
        le=365,
        description="Aggregation window in days (1-365).",
    ),
) -> SlaSummaryListResponse:
    """List SLA summaries filtered by the supplied query parameters."""
    query = _build_query(service=service, region=region, window_days=window_days)

    records = sla_metrics_fixtures.get_sla_summary_fixtures()
    filtered = sla_metrics_aggregation.filter_summaries(
        records,
        service=query.service,
        region=query.region,
    )
    summaries = sla_metrics_aggregation.aggregate_summaries(
        filtered,
        window_days=query.window_days,
    )

    return SlaSummaryListResponse(
        items=[SlaSummaryResponse(**item) for item in summaries],
        total=len(summaries),
        window_days=query.window_days,
    )


@router.get(
    "/{service}",
    response_model=SlaSummaryResponse,
    summary="Get a deterministic SLA summary by service",
    description=(
        "Return a deterministic SLA summary for a single service from the "
        "workspace sample data. Read-only and side-effect free."
    ),
    responses={
        status.HTTP_404_NOT_FOUND: {
            "description": "No summary found for the requested service.",
        },
    },
)
def get_sla_summary(
    service: str,
    region: Optional[str] = Query(
        default=None,
        description="Optional region filter.",
    ),
    window_days: int = Query(
        default=30,
        ge=1,
        le=365,
        description="Aggregation window in days (1-365).",
    ),
) -> SlaSummaryResponse:
    """Return a single SLA summary for the given service."""
    query = _build_query(service=service, region=region, window_days=window_days)

    records = sla_metrics_fixtures.get_sla_summary_fixtures()
    filtered = sla_metrics_aggregation.filter_summaries(
        records,
        service=query.service,
        region=query.region,
    )
    summaries = sla_metrics_aggregation.aggregate_summaries(
        filtered,
        window_days=query.window_days,
    )

    if not summaries:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No SLA summary found for service '{service}'.",
        )

    return SlaSummaryResponse(**summaries[0])


@router.get(
    "/health",
    response_model=dict,
    summary="Workspace SLA summary router health check",
    description="Simple deterministic health check for the workspace router.",
)
def health() -> dict:
    """Return a deterministic health payload for the workspace router."""
    return {"status": "ok", "module": "sla_summary_router"}


__all__: List[str] = ["router"]