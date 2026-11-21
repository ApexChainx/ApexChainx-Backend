"""Health indicators router for the SLA operations workspace.

This module exposes read-only/aggregation endpoints that compute an overall
operational health state from a collection of domain health indicators.

The router is intentionally self-contained and isolated from core application
state so that it can be exercised deterministically in tests.
"""

from __future__ import annotations

from typing import List

from fastapi import APIRouter, HTTPException, status

from app.sla_operations.fixtures.operational_samples import get_sample_outage_summaries
from app.sla_operations.schemas.health_indicators import (
    AggregatedOperationalHealthResponse,
    DomainHealthIndicator,
    HealthState,
)

router = APIRouter(
    prefix="/health-indicators",
    tags=["SLA Operations Health Indicators"],
)

# Precedence ordering: higher value == worse health.
_STATE_PRECEDENCE = {
    HealthState.HEALTHY: 0,
    HealthState.DEGRADED: 1,
    HealthState.UNHEALTHY: 2,
}


def _coerce_state(value: object) -> HealthState:
    """Best-effort coercion of an arbitrary value into a ``HealthState``."""
    if isinstance(value, HealthState):
        return value
    try:
        return HealthState(str(value).lower())
    except (ValueError, KeyError):
        return HealthState.UNHEALTHY


def _compute_overall_state(indicators: List[DomainHealthIndicator]) -> HealthState:
    """Compute the overall state using unhealthy > degraded > healthy precedence."""
    overall = HealthState.HEALTHY
    for indicator in indicators:
        state = _coerce_state(indicator.state)
        if _STATE_PRECEDENCE[state] > _STATE_PRECEDENCE[overall]:
            overall = state
    return overall


def _build_summary(indicators: List[DomainHealthIndicator]) -> str:
    """Build a deterministic human-readable summary of the indicators."""
    counts = {
        HealthState.HEALTHY: 0,
        HealthState.DEGRADED: 0,
        HealthState.UNHEALTHY: 0,
    }
    for indicator in indicators:
        counts[_coerce_state(indicator.state)] += 1

    total = len(indicators)
    return (
        f"{total} indicator(s) evaluated: "
        f"{counts[HealthState.HEALTHY]} healthy, "
        f"{counts[HealthState.DEGRADED]} degraded, "
        f"{counts[HealthState.UNHEALTHY]} unhealthy."
    )


def _aggregate(indicators: List[DomainHealthIndicator]) -> AggregatedOperationalHealthResponse:
    """Aggregate a list of indicators into a response model."""
    return AggregatedOperationalHealthResponse(
        overall_state=_compute_overall_state(indicators),
        summary=_build_summary(indicators),
        indicators=list(indicators),
    )


def _indicator_from_summary(summary: object) -> DomainHealthIndicator:
    """Map a sample outage summary into a ``DomainHealthIndicator``.

    The mapping is deterministic and defensive: it tolerates both mapping-like
    and attribute-based summary objects.
    """

    def _get(key: str, default: object = None) -> object:
        if isinstance(summary, dict):
            return summary.get(key, default)
        return getattr(summary, key, default)

    domain = _get("domain") or _get("name") or _get("service") or "unknown"
    raw_state = _get("state") or _get("health") or _get("status") or "healthy"
    message = _get("message") or _get("detail") or _get("summary") or ""

    return DomainHealthIndicator(
        domain=str(domain),
        state=_coerce_state(raw_state),
        message=str(message),
    )


@router.post(
    "/aggregate",
    response_model=AggregatedOperationalHealthResponse,
    status_code=status.HTTP_200_OK,
    summary="Aggregate domain health indicators into an overall operational state.",
)
def aggregate_health_indicators(
    indicators: List[DomainHealthIndicator],
) -> AggregatedOperationalHealthResponse:
    """Aggregate a list of domain health indicators.

    An empty list is considered an invalid payload and yields a 422 response.
    """
    if not indicators:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="At least one health indicator is required.",
        )
    return _aggregate(indicators)


@router.get(
    "/sample",
    response_model=AggregatedOperationalHealthResponse,
    status_code=status.HTTP_200_OK,
    summary="Return a deterministic sample aggregated operational health response.",
)
def get_sample_health_indicators() -> AggregatedOperationalHealthResponse:
    """Return a deterministic aggregated response built from sample fixtures."""
    summaries = get_sample_outage_summaries()
    indicators = [_indicator_from_summary(summary) for summary in summaries]
    if not indicators:
        # Guarantee a meaningful, deterministic response even if fixtures are empty.
        indicators = [
            DomainHealthIndicator(
                domain="sample",
                state=HealthState.HEALTHY,
                message="No sample outage summaries available.",
            )
        ]
    return _aggregate(indicators)