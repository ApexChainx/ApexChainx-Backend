"""Workspace-local router module for SLA operations settlement reports.

This module provides an isolated, integration-ready FastAPI router for
settlement breakdown/report endpoints. It is intentionally *not* wired into
``app/api/v1/router.py`` or ``app/main.py``; maintainers may register it later.

Design notes
------------
* All handlers are read-only and side-effect free.
* Responses are deterministic for sample inputs by relying on fixture data
  exposed through the workspace service layer.
* Inputs/outputs are validated via explicit Pydantic schemas from
  ``app.sla_operations.schemas.settlement_reports``.
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, status

from app.sla_operations.schemas.settlement_reports import (
    SettlementBreakdownItem,
    SettlementBreakdownResponse,
    SettlementReportListResponse,
    SettlementReportQuery,
    SettlementReportSummary,
)
from app.sla_operations.services import settlement_breakdown as settlement_service

router = APIRouter(
    prefix="/settlement-reports",
    tags=["SLA Operations Settlement Reports"],
)


def _build_breakdown_response(
    query: SettlementReportQuery,
) -> SettlementBreakdownResponse:
    """Build a deterministic breakdown response for the given query.

    Delegates data retrieval to the workspace service layer so that this
    router stays free of persistence or fixture coupling.
    """
    items: List[SettlementBreakdownItem] = settlement_service.get_settlement_breakdown(
        outage_id=query.outage_id,
        asset_code=query.asset_code,
        start_date=query.start_date,
        end_date=query.end_date,
    )
    total_amount = settlement_service.sum_breakdown_amounts(items)
    return SettlementBreakdownResponse(
        outage_id=query.outage_id,
        asset_code=query.asset_code,
        start_date=query.start_date,
        end_date=query.end_date,
        total_amount=total_amount,
        item_count=len(items),
        items=items,
    )


@router.get(
    "/breakdown",
    response_model=SettlementBreakdownResponse,
    summary="Get settlement breakdown for an outage",
    description=(
        "Returns a deterministic settlement breakdown for the requested "
        "outage and optional filters. Read-only and side-effect free."
    ),
)
def get_settlement_breakdown(
    outage_id: str = Query(
        ...,
        min_length=1,
        max_length=128,
        description="Outage identifier to build the settlement breakdown for.",
    ),
    asset_code: Optional[str] = Query(
        default=None,
        min_length=1,
        max_length=32,
        description="Optional asset code filter (e.g. 'USDC').",
    ),
    start_date: Optional[str] = Query(
        default=None,
        max_length=32,
        description="Optional inclusive start date filter (ISO-8601).",
    ),
    end_date: Optional[str] = Query(
        default=None,
        max_length=32,
        description="Optional inclusive end date filter (ISO-8601).",
    ),
) -> SettlementBreakdownResponse:
    """Return the settlement breakdown for the requested outage."""
    try:
        query = SettlementReportQuery(
            outage_id=outage_id,
            asset_code=asset_code,
            start_date=start_date,
            end_date=end_date,
        )
    except ValueError as exc:  # pragma: no cover - defensive validation
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    return _build_breakdown_response(query)


@router.get(
    "/summary",
    response_model=SettlementReportSummary,
    summary="Get settlement report summary",
    description=(
        "Returns a deterministic aggregate summary of settlement reports "
        "for the requested outage. Read-only and side-effect free."
    ),
)
def get_settlement_summary(
    outage_id: str = Query(
        ...,
        min_length=1,
        max_length=128,
        description="Outage identifier to summarize settlement reports for.",
    ),
    asset_code: Optional[str] = Query(
        default=None,
        min_length=1,
        max_length=32,
        description="Optional asset code filter (e.g. 'USDC').",
    ),
) -> SettlementReportSummary:
    """Return an aggregate settlement summary for the requested outage."""
    try:
        query = SettlementReportQuery(
            outage_id=outage_id,
            asset_code=asset_code,
        )
    except ValueError as exc:  # pragma: no cover - defensive validation
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    return settlement_service.get_settlement_summary(
        outage_id=query.outage_id,
        asset_code=query.asset_code,
    )


@router.get(
    "/reports",
    response_model=SettlementReportListResponse,
    summary="List settlement reports",
    description=(
        "Returns a deterministic list of settlement reports for the "
        "requested outage. Read-only and side-effect free."
    ),
)
def list_settlement_reports(
    outage_id: str = Query(
        ...,
        min_length=1,
        max_length=128,
        description="Outage identifier to list settlement reports for.",
    ),
    asset_code: Optional[str] = Query(
        default=None,
        min_length=1,
        max_length=32,
        description="Optional asset code filter (e.g. 'USDC').",
    ),
    limit: int = Query(
        default=50,
        ge=1,
        le=200,
        description="Maximum number of reports to return.",
    ),
) -> SettlementReportListResponse:
    """Return a deterministic list of settlement reports."""
    try:
        query = SettlementReportQuery(
            outage_id=outage_id,
            asset_code=asset_code,
        )
    except ValueError as exc:  # pragma: no cover - defensive validation
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    reports = settlement_service.list_settlement_reports(
        outage_id=query.outage_id,
        asset_code=query.asset_code,
        limit=limit,
    )
    return SettlementReportListResponse(
        outage_id=query.outage_id,
        asset_code=query.asset_code,
        count=len(reports),
        reports=reports,
    )


__all__ = ["router"]