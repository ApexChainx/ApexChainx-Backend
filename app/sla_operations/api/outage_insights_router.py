"""Workspace-local FastAPI router for outage insight/report endpoints.

This module is intentionally isolated from the core application routing
(``app/api/v1/router.py`` and ``app/main.py``). It provides an
``APIRouter`` instance that maintainers may wire up later, along with
self-contained request/response models and helpers.

Design notes
------------
* All handlers are side-effect free: they only read from the provided
  service/analysis helpers and never mutate global state.
* Inputs and outputs are validated via explicit Pydantic models.
* Responses are deterministic for a given input.
* No secrets are logged or returned.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator

from app.sla_operations.schemas.outage_insights import (
    OutageInsightQuery,
    OutageInsightsReport,
)
from app.sla_operations.services import outage_mttr_analysis


router = APIRouter(
    prefix="/sla-operations/outage-insights",
    tags=["sla-operations", "outage-insights"],
)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class OutageInsightQueryRequest(BaseModel):
    """Explicit request model for the outage insight query endpoint.

    Mirrors :class:`OutageInsightQuery` but is defined here so the router
    can evolve independently of the workspace schema module.
    """

    start: Optional[datetime] = Field(
        default=None,
        description="Inclusive lower bound for the analysis window (UTC).",
    )
    end: Optional[datetime] = Field(
        default=None,
        description="Inclusive upper bound for the analysis window (UTC).",
    )
    service: Optional[str] = Field(
        default=None,
        description="Optional service identifier to filter outages by.",
    )
    severity: Optional[str] = Field(
        default=None,
        description="Optional severity label to filter outages by.",
    )
    limit: int = Field(
        default=100,
        ge=1,
        le=1000,
        description="Maximum number of outages to consider (1-1000).",
    )

    @field_validator("start", "end")
    @classmethod
    def _ensure_timezone(cls, value: Optional[datetime]) -> Optional[datetime]:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def to_query(self) -> OutageInsightQuery:
        """Convert to the workspace schema used by the analysis service."""
        return OutageInsightQuery(
            start=self.start,
            end=self.end,
            service=self.service,
            severity=self.severity,
            limit=self.limit,
        )


class OutageInsightSummary(BaseModel):
    """Deterministic summary returned by the summary endpoint."""

    total_outages: int = Field(ge=0)
    total_downtime_minutes: float = Field(ge=0.0)
    mean_time_to_repair_minutes: Optional[float] = Field(default=None, ge=0.0)
    median_time_to_repair_minutes: Optional[float] = Field(default=None, ge=0.0)
    generated_at: datetime


class OutageInsightsReportResponse(BaseModel):
    """Response wrapper around :class:`OutageInsightsReport`."""

    report: OutageInsightsReport
    generated_at: datetime


class OutageInsightErrorResponse(BaseModel):
    """Standard error envelope for this router."""

    detail: str
    code: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


def _build_query(
    start: Optional[datetime],
    end: Optional[datetime],
    service: Optional[str],
    severity: Optional[str],
    limit: int,
) -> OutageInsightQuery:
    """Validate raw query parameters into a workspace schema object."""
    request = OutageInsightQueryRequest(
        start=start,
        end=end,
        service=service,
        severity=severity,
        limit=limit,
    )
    if request.start and request.end and request.start > request.end:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="'start' must be less than or equal to 'end'.",
        )
    return request.to_query()


def _safe_report(query: OutageInsightQuery) -> OutageInsightsReport:
    """Invoke the analysis service and normalize failures to HTTP errors."""
    try:
        return outage_mttr_analysis.build_outage_insights_report(query)
    except ValueError as exc:  # invalid input surfaced by the service
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except Exception as exc:  # pragma: no cover - defensive guard
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to build outage insights report.",
        ) from exc


def _summarize(report: OutageInsightsReport) -> OutageInsightSummary:
    """Derive a deterministic summary from an insights report."""
    outages: List[Any] = list(getattr(report, "outages", []) or [])
    total_outages = len(outages)

    total_downtime = 0.0
    repairs: List[float] = []
    for outage in outages:
        downtime = getattr(outage, "downtime_minutes", None)
        if downtime is not None:
            total_downtime += float(downtime)
        mttr = getattr(outage, "time_to_repair_minutes", None)
        if mttr is not None:
            repairs.append(float(mttr))

    mean_mttr: Optional[float] = None
    median_mttr: Optional[float] = None
    if repairs:
        mean_mttr = sum(repairs) / len(repairs)
        ordered = sorted(repairs)
        mid = len(ordered) // 2
        if len(ordered) % 2 == 0:
            median_mttr = (ordered[mid - 1] + ordered[mid]) / 2.0
        else:
            median_mttr = ordered[mid]

    return OutageInsightSummary(
        total_outages=total_outages,
        total_downtime_minutes=total_downtime,
        mean_time_to_repair_minutes=mean_mttr,
        median_time_to_repair_minutes=median_mttr,
        generated_at=_utcnow(),
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/report",
    response_model=OutageInsightsReportResponse,
    responses={422: {"model": OutageInsightErrorResponse}},
    summary="Build an outage insights report for the given window.",
)
def get_outage_insights_report(
    start: Optional[datetime] = Query(default=None),
    end: Optional[datetime] = Query(default=None),
    service: Optional[str] = Query(default=None),
    severity: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
) -> OutageInsightsReportResponse:
    """Return a full outage insights report.

    The handler is deterministic for a given input and performs no writes.
    """
    query = _build_query(start, end, service, severity, limit)
    report = _safe_report(query)
    return OutageInsightsReportResponse(report=report, generated_at=_utcnow())


@router.post(
    "/report",
    response_model=OutageInsightsReportResponse,
    responses={422: {"model": OutageInsightErrorResponse}},
    summary="Build an outage insights report from an explicit request body.",
)
def post_outage_insights_report(
    payload: OutageInsightQueryRequest,
) -> OutageInsightsReportResponse:
    """Same as the GET variant but accepts an explicit JSON body."""
    if payload.start and payload.end and payload.start > payload.end:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="'start' must be less than or equal to 'end'.",
        )
    report = _safe_report(payload.to_query())
    return OutageInsightsReportResponse(report=report, generated_at=_utcnow())


@router.get(
    "/summary",
    response_model=OutageInsightSummary,
    responses={422: {"model": OutageInsightErrorResponse}},
    summary="Return a compact summary of outage insights.",
)
def get_outage_insights_summary(
    start: Optional[datetime] = Query(default=None),
    end: Optional[datetime] = Query(default=None),
    service: Optional[str] = Query(default=None),
    severity: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
) -> OutageInsightSummary:
    """Return a deterministic summary derived from the insights report."""
    query = _build_query(start, end, service, severity, limit)
    report = _safe_report(query)
    return _summarize(report)


@router.get(
    "/health",
    response_model=Dict[str, str],
    summary="Liveness probe for the outage insights router.",
)
def get_outage_insights_health() -> Dict[str, str]:
    """Simple deterministic health payload for the isolated router."""
    return {"status": "ok", "module": "outage_insights_router"}


__all__ = [
    "router",
    "OutageInsightQueryRequest",
    "OutageInsightSummary",
    "OutageInsightsReportResponse",
    "OutageInsightErrorResponse",
    "get_outage_insights_report",
    "post_outage_insights_report",
    "get_outage_insights_summary",
    "get_outage_insights_health",
]