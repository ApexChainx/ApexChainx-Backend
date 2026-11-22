"""Workspace-local webhook monitoring router for SLA operations.

This module provides an isolated, integration-ready FastAPI router exposing
read-only webhook health and delivery insight endpoints. It is intentionally
NOT registered in ``app/api/v1/router.py`` or ``app/main.py``; maintainers may
wire it later.

Design notes / assumptions:
- All handlers are read-only and side-effect free.
- Responses are deterministic for sample inputs, backed by fixtures in
  ``app/sla_operations/fixtures/webhook_fixtures.py`` and helpers in
  ``app/sla_operations/services/webhook_delivery_insights.py``.
- Explicit Pydantic request/response models provide input/output validation.
- The module can be imported independently without core router changes.
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, status

from app.sla_operations.fixtures import webhook_fixtures
from app.sla_operations.schemas.webhook_monitoring import (
    WebhookDeliveryInsight,
    WebhookDeliveryInsightListResponse,
    WebhookHealthResponse,
    WebhookHealthSummary,
    WebhookMonitoringQuery,
)
from app.sla_operations.services import webhook_delivery_insights

router = APIRouter(
    prefix="/webhook-monitoring",
    tags=["SLA Operations Webhook Monitoring"],
)


def _resolve_webhook_id(webhook_id: str) -> str:
    """Normalize and validate a webhook identifier.

    Raises ``HTTPException`` (404) when the identifier is unknown to the
    deterministic fixture dataset.
    """
    normalized = webhook_id.strip()
    if not normalized or not webhook_fixtures.webhook_exists(normalized):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Webhook '{webhook_id}' not found.",
        )
    return normalized


@router.get(
    "/health",
    response_model=WebhookHealthSummary,
    summary="Get aggregate webhook health summary",
)
def get_webhook_health_summary() -> WebhookHealthSummary:
    """Return an aggregate, deterministic health summary for all webhooks."""
    return webhook_delivery_insights.build_health_summary()


@router.get(
    "/health/{webhook_id}",
    response_model=WebhookHealthResponse,
    summary="Get health status for a single webhook",
)
def get_webhook_health(webhook_id: str) -> WebhookHealthResponse:
    """Return deterministic health status for a single webhook."""
    resolved_id = _resolve_webhook_id(webhook_id)
    return webhook_delivery_insights.build_webhook_health(resolved_id)


@router.get(
    "/deliveries",
    response_model=WebhookDeliveryInsightListResponse,
    summary="List webhook delivery insights",
)
def list_webhook_deliveries(
    webhook_id: Optional[str] = Query(
        default=None,
        description="Optional webhook identifier to filter deliveries.",
    ),
    limit: int = Query(
        default=50,
        ge=1,
        le=500,
        description="Maximum number of delivery insights to return.",
    ),
    offset: int = Query(
        default=0,
        ge=0,
        description="Number of delivery insights to skip.",
    ),
) -> WebhookDeliveryInsightListResponse:
    """Return a deterministic, paginated list of webhook delivery insights."""
    query = WebhookMonitoringQuery(
        webhook_id=webhook_id.strip() if webhook_id else None,
        limit=limit,
        offset=offset,
    )

    resolved_id: Optional[str] = None
    if query.webhook_id is not None:
        resolved_id = _resolve_webhook_id(query.webhook_id)

    return webhook_delivery_insights.list_delivery_insights(
        webhook_id=resolved_id,
        limit=query.limit,
        offset=query.offset,
    )


@router.get(
    "/deliveries/{delivery_id}",
    response_model=WebhookDeliveryInsight,
    summary="Get a single webhook delivery insight",
)
def get_webhook_delivery(delivery_id: str) -> WebhookDeliveryInsight:
    """Return a single deterministic delivery insight by identifier."""
    normalized = delivery_id.strip()
    insight = webhook_delivery_insights.get_delivery_insight(normalized)
    if insight is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Webhook delivery '{delivery_id}' not found.",
        )
    return insight


__all__: List[str] = ["router"]