"""Webhook monitoring router for the SLA operations workspace.

This module exposes a small, self-contained FastAPI router that serves
webhook monitoring views and health information derived from deterministic
workspace fixtures. It intentionally avoids any dependency on core
application state (databases, external services, etc.) so that it can be
exercised safely in isolation.

Endpoints
---------
POST /webhook-monitoring/view
    Build a :class:`WebhookMonitoringView` for a requested time window.

GET /webhook-monitoring/health
    Return a list of :class:`WebhookHealth` entries computed from the
    workspace webhook fixtures.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, HTTPException

from app.sla_operations.api.error_mapping import WorkspaceAPIError, to_http_exception
from app.sla_operations.fixtures.webhook_fixtures import (
    webhook_delivery_fixtures,
    webhook_endpoint_fixtures,
)
from app.sla_operations.schemas.webhook_monitoring import (
    WebhookDeliverySummary,
    WebhookHealth,
    WebhookMonitoringQuery,
    WebhookMonitoringView,
)

router = APIRouter(
    prefix="/webhook-monitoring",
    tags=["SLA Operations Webhook Monitoring"],
)


# Maximum allowed window size for a monitoring query. This keeps responses
# bounded and deterministic regardless of caller input.
_MAX_WINDOW = timedelta(days=30)


def _resolve_window(
    query: WebhookMonitoringQuery,
) -> tuple[datetime, datetime]:
    """Resolve the effective ``[start, end]`` window for a query.

    The query may specify explicit ``start``/``end`` timestamps or a
    ``window_hours`` value relative to ``now``. When neither is provided a
    default 24 hour window ending at ``now`` is used.

    Raises:
        WorkspaceAPIError: If the window is invalid (end before start,
            non-positive duration, or larger than the maximum allowed).
    """

    now = query.now or datetime.now(timezone.utc)

    if query.start is not None or query.end is not None:
        start = query.start
        end = query.end
        if start is None:
            if end is None:  # pragma: no cover - defensive
                raise WorkspaceAPIError(
                    "Invalid monitoring window: missing start and end.",
                    status_code=422,
                )
            start = end - timedelta(hours=24)
        if end is None:
            end = now
    elif query.window_hours is not None:
        if query.window_hours <= 0:
            raise WorkspaceAPIError(
                "Invalid monitoring window: window_hours must be positive.",
                status_code=422,
            )
        end = now
        start = end - timedelta(hours=query.window_hours)
    else:
        end = now
        start = end - timedelta(hours=24)

    if end < start:
        raise WorkspaceAPIError(
            "Invalid monitoring window: end must not precede start.",
            status_code=422,
        )

    if (end - start) > _MAX_WINDOW:
        raise WorkspaceAPIError(
            "Invalid monitoring window: window exceeds maximum of 30 days.",
            status_code=422,
        )

    return start, end


def _filter_deliveries(
    start: datetime,
    end: datetime,
    endpoint_id: Optional[str],
) -> List[dict]:
    """Return delivery fixtures that fall within the window and endpoint."""

    results: List[dict] = []
    for delivery in webhook_delivery_fixtures:
        if endpoint_id is not None and delivery.get("endpoint_id") != endpoint_id:
            continue
        delivered_at = delivery.get("delivered_at")
        if delivered_at is None:
            continue
        if start <= delivered_at <= end:
            results.append(delivery)
    return results


def _build_view(
    query: WebhookMonitoringQuery,
    start: datetime,
    end: datetime,
) -> WebhookMonitoringView:
    """Construct a :class:`WebhookMonitoringView` from fixtures."""

    deliveries = _filter_deliveries(start, end, query.endpoint_id)

    summaries: List[WebhookDeliverySummary] = []
    for delivery in deliveries:
        summaries.append(
            WebhookDeliverySummary(
                delivery_id=delivery["delivery_id"],
                endpoint_id=delivery["endpoint_id"],
                status=delivery["status"],
                delivered_at=delivery["delivered_at"],
                attempts=delivery.get("attempts", 1),
                response_code=delivery.get("response_code"),
            )
        )

    total = len(summaries)
    succeeded = sum(1 for s in summaries if s.status == "success")
    failed = sum(1 for s in summaries if s.status == "failed")
    pending = total - succeeded - failed

    return WebhookMonitoringView(
        start=start,
        end=end,
        endpoint_id=query.endpoint_id,
        total_deliveries=total,
        succeeded=succeeded,
        failed=failed,
        pending=pending,
        deliveries=summaries,
    )


def _build_health() -> List[WebhookHealth]:
    """Compute health entries for each configured webhook endpoint."""

    health_entries: List[WebhookHealth] = []
    for endpoint in webhook_endpoint_fixtures:
        endpoint_id = endpoint["endpoint_id"]
        deliveries = [
            d
            for d in webhook_delivery_fixtures
            if d.get("endpoint_id") == endpoint_id
        ]
        total = len(deliveries)
        failed = sum(1 for d in deliveries if d.get("status") == "failed")
        last_delivery = max(
            (d["delivered_at"] for d in deliveries if d.get("delivered_at")),
            default=None,
        )

        if total == 0:
            status = "unknown"
        elif failed == 0:
            status = "healthy"
        elif failed < total:
            status = "degraded"
        else:
            status = "unhealthy"

        health_entries.append(
            WebhookHealth(
                endpoint_id=endpoint_id,
                url=endpoint.get("url", ""),
                status=status,
                total_deliveries=total,
                failed_deliveries=failed,
                last_delivery_at=last_delivery,
            )
        )

    return health_entries


@router.post("/view", response_model=WebhookMonitoringView)
def get_monitoring_view(query: WebhookMonitoringQuery) -> WebhookMonitoringView:
    """Return a monitoring view for the requested window."""

    try:
        start, end = _resolve_window(query)
        view = _build_view(query, start, end)
    except WorkspaceAPIError as exc:
        raise to_http_exception(exc) from exc

    if query.endpoint_id is not None and not any(
        e.get("endpoint_id") == query.endpoint_id
        for e in webhook_endpoint_fixtures
    ):
        raise to_http_exception(
            WorkspaceAPIError(
                f"Webhook endpoint '{query.endpoint_id}' not found.",
                status_code=404,
            )
        )

    return view


@router.get("/health", response_model=List[WebhookHealth])
def get_webhook_health() -> List[WebhookHealth]:
    """Return health information for all configured webhook endpoints."""

    try:
        return _build_health()
    except WorkspaceAPIError as exc:  # pragma: no cover - defensive
        raise to_http_exception(exc) from exc
    except HTTPException:  # pragma: no cover - defensive
        raise