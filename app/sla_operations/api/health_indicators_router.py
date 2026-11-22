"""Operational health indicators router for the SLA operations workspace.

This module is intentionally isolated from the core application routing. It
exposes a FastAPI ``APIRouter`` with self-contained handlers that validate
inputs and outputs via workspace Pydantic schemas and delegate scoring logic to
the workspace health scoring service.

The router is NOT registered in ``app/api/v1/router.py`` or ``app/main.py`` as
part of this issue. Maintainers may wire it up later. The module must be
importable independently without requiring core router modifications.
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, HTTPException, status

from app.sla_operations.schemas.health_indicators import (
    HealthIndicator,
    HealthIndicatorListResponse,
    HealthIndicatorRequest,
    HealthIndicatorResponse,
    HealthScoreRequest,
    HealthScoreResponse,
)
from app.sla_operations.services.health_scoring import (
    compute_health_score,
    evaluate_indicator,
)

router = APIRouter(
    prefix="/sla-operations/health-indicators",
    tags=["sla-operations", "health-indicators"],
)


def _build_indicator_response(indicator: HealthIndicator) -> HealthIndicatorResponse:
    """Evaluate a single indicator and wrap the result deterministically."""
    evaluation = evaluate_indicator(indicator)
    return HealthIndicatorResponse(
        name=indicator.name,
        value=indicator.value,
        threshold=indicator.threshold,
        status=evaluation.status,
        breached=evaluation.breached,
        message=evaluation.message,
    )


@router.get(
    "/health",
    response_model=HealthIndicatorListResponse,
    summary="Liveness probe for the health indicators module",
)
def health_check() -> HealthIndicatorListResponse:
    """Return a deterministic liveness payload for the isolated module."""
    return HealthIndicatorListResponse(
        indicators=[],
        overall_status="ok",
        score=100.0,
    )


@router.post(
    "/evaluate",
    response_model=HealthIndicatorResponse,
    status_code=status.HTTP_200_OK,
    summary="Evaluate a single health indicator",
)
def evaluate_single_indicator(
    payload: HealthIndicatorRequest,
) -> HealthIndicatorResponse:
    """Validate and evaluate a single operational health indicator."""
    indicator = HealthIndicator(
        name=payload.name,
        value=payload.value,
        threshold=payload.threshold,
        comparison=payload.comparison,
    )
    return _build_indicator_response(indicator)


@router.post(
    "/evaluate/batch",
    response_model=HealthIndicatorListResponse,
    status_code=status.HTTP_200_OK,
    summary="Evaluate a batch of health indicators",
)
def evaluate_indicators_batch(
    payload: HealthScoreRequest,
) -> HealthIndicatorListResponse:
    """Evaluate a batch of indicators and compute an aggregate score."""
    if not payload.indicators:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="At least one health indicator is required.",
        )

    indicators: List[HealthIndicator] = [
        HealthIndicator(
            name=item.name,
            value=item.value,
            threshold=item.threshold,
            comparison=item.comparison,
        )
        for item in payload.indicators
    ]

    responses: List[HealthIndicatorResponse] = [
        _build_indicator_response(indicator) for indicator in indicators
    ]

    score_result = compute_health_score(indicators)

    return HealthIndicatorListResponse(
        indicators=responses,
        overall_status=score_result.overall_status,
        score=score_result.score,
    )


@router.post(
    "/score",
    response_model=HealthScoreResponse,
    status_code=status.HTTP_200_OK,
    summary="Compute an aggregate health score",
)
def compute_score(payload: HealthScoreRequest) -> HealthScoreResponse:
    """Compute an aggregate health score for a set of indicators."""
    if not payload.indicators:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="At least one health indicator is required.",
        )

    indicators: List[HealthIndicator] = [
        HealthIndicator(
            name=item.name,
            value=item.value,
            threshold=item.threshold,
            comparison=item.comparison,
        )
        for item in payload.indicators
    ]

    score_result = compute_health_score(indicators)

    return HealthScoreResponse(
        overall_status=score_result.overall_status,
        score=score_result.score,
        breached_count=score_result.breached_count,
        total_count=score_result.total_count,
    )


__all__ = ["router"]