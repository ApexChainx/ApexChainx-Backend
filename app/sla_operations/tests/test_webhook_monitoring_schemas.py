from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.sla_operations.schemas.webhook_monitoring import (
    DeliveryAttemptSummary,
    WebhookDeliveryState,
    WebhookDeliverySummary,
    WebhookEventType,
    WebhookHealth,
    WebhookHealthState,
    WebhookMonitoringQuery,
    WebhookMonitoringView,
)

WEBHOOK_ID = UUID("11111111-1111-1111-1111-111111111111")
DELIVERY_ID = UUID("22222222-2222-2222-2222-222222222222")
START = datetime(2026, 4, 1, tzinfo=UTC)


def health_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "webhook_id": WEBHOOK_ID,
        "name": "sla-alerts",
        "state": "healthy",
        "is_active": True,
        "max_retries": 3,
        "success_rate": 99.5,
        "average_latency_ms": 120.0,
        "consecutive_failures": 0,
        "pending_deliveries": 0,
        "dead_lettered_deliveries": 0,
    }
    payload.update(overrides)
    return payload


def test_health_delivery_and_monitoring_view_accept_representative_payloads() -> None:
    health = WebhookHealth(**health_payload())
    attempt = DeliveryAttemptSummary(
        attempt_number=1,
        state="success",
        attempted_at=START + timedelta(seconds=1),
        response_status_code=200,
        duration_ms=95.5,
    )
    delivery = WebhookDeliverySummary(
        delivery_id=DELIVERY_ID,
        webhook_id=WEBHOOK_ID,
        event="sla.violation",
        state="success",
        attempt_count=1,
        created_at=START,
        delivered_at=START + timedelta(seconds=1),
        attempts=[attempt],
    )
    view = WebhookMonitoringView(
        generated_at=START + timedelta(hours=1),
        window_start=START,
        window_end=START + timedelta(hours=1),
        total_webhooks=1,
        healthy_count=1,
        delivery_state_counts={"success": 1},
        webhooks=[health],
        deliveries=[delivery],
    )

    assert health.state is WebhookHealthState.HEALTHY
    assert delivery.event is WebhookEventType.SLA_VIOLATION
    assert delivery.state is WebhookDeliveryState.SUCCESS
    assert view.delivery_state_counts == {WebhookDeliveryState.SUCCESS: 1}
    assert view.webhooks[0].webhook_id == WEBHOOK_ID


def test_boundaries_are_accepted() -> None:
    health = WebhookHealth(**health_payload(max_retries=10, success_rate=100.0, average_latency_ms=0.0))
    attempt = DeliveryAttemptSummary(
        attempt_number=1,
        state="failed",
        attempted_at=START,
        response_status_code=599,
        duration_ms=0.0,
    )
    query = WebhookMonitoringQuery(
        window_start=START,
        window_end=START + timedelta(seconds=1),
        webhook_ids=[WEBHOOK_ID],
        states=["dead_letter"],
        limit=500,
    )

    assert health.max_retries == 10
    assert attempt.response_status_code == 599
    assert query.states[0] is WebhookDeliveryState.DEAD_LETTER


@pytest.mark.parametrize(
    "overrides",
    [
        {"max_retries": 11},
        {"success_rate": 100.1},
        {"average_latency_ms": -1.0},
        {"state": "unknown_state"},
    ],
)
def test_health_rejects_invalid_metrics(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        WebhookHealth(**health_payload(**overrides))


def test_attempts_reject_invalid_statuses_numbers_and_times() -> None:
    with pytest.raises(ValidationError):
        DeliveryAttemptSummary(attempt_number=0, state="success", attempted_at=START)
    with pytest.raises(ValidationError):
        DeliveryAttemptSummary(attempt_number=1, state="success", attempted_at=START, response_status_code=99)
    with pytest.raises(ValidationError):
        DeliveryAttemptSummary(attempt_number=1, state="success", attempted_at=START, response_status_code=600)
    with pytest.raises(ValidationError):
        DeliveryAttemptSummary(attempt_number=1, state="success", attempted_at=START, duration_ms=-1.0)
    with pytest.raises(ValidationError):
        WebhookDeliverySummary(
            delivery_id=DELIVERY_ID,
            webhook_id=WEBHOOK_ID,
            event="sla.violation",
            state="retrying",
            attempt_count=1,
            created_at=START,
            attempts=[
                DeliveryAttemptSummary(attempt_number=2, state="failed", attempted_at=START + timedelta(seconds=1))
            ],
        )
    with pytest.raises(ValidationError):
        WebhookDeliverySummary(
            delivery_id=DELIVERY_ID,
            webhook_id=WEBHOOK_ID,
            event="sla.violation",
            state="retrying",
            attempt_count=1,
            created_at=START,
            last_attempt_at=START - timedelta(seconds=1),
        )
    with pytest.raises(ValidationError):
        WebhookDeliverySummary(
            delivery_id=DELIVERY_ID,
            webhook_id="not-a-uuid",
            event="sla.violation",
            state="pending",
            attempt_count=0,
            created_at=START,
        )


def test_monitoring_view_and_query_reject_inconsistent_values() -> None:
    with pytest.raises(ValidationError):
        WebhookMonitoringView(
            generated_at=START,
            window_start=START + timedelta(hours=1),
            window_end=START,
            total_webhooks=0,
        )
    with pytest.raises(ValidationError):
        WebhookMonitoringView(
            generated_at=START,
            window_start=START,
            window_end=START + timedelta(hours=1),
            total_webhooks=1,
            healthy_count=1,
            degraded_count=1,
        )
    with pytest.raises(ValidationError):
        WebhookMonitoringQuery(
            window_start=START,
            window_end=START + timedelta(hours=1),
            limit=0,
        )
    with pytest.raises(ValidationError):
        WebhookMonitoringQuery(
            window_start=START,
            window_end=START + timedelta(hours=1),
            states=["unknown"],
        )
