"""Deterministic webhook endpoints, deliveries, and attempt history.

Endpoint URLs use the reserved ``.test`` domain and no signing secret values
are included. Each helper returns fresh records for safe test mutation.
"""

from datetime import UTC, datetime
from typing import Literal, TypedDict


class WebhookEndpoint(TypedDict):
    id: str
    name: str
    url: str
    is_active: bool
    events: list[str]
    max_retries: int
    has_signing_secret: bool
    created_at: datetime


class WebhookDelivery(TypedDict):
    id: str
    webhook_id: str
    event: Literal["sla.violation", "sla.warning", "sla.resolved"]
    payload: str
    status: Literal["success", "retrying", "dead_letter", "breaker_open"]
    attempt_count: int
    next_retry_at: datetime | None
    response_status_code: int | None
    created_at: datetime
    delivered_at: datetime | None


class WebhookDeliveryAttempt(TypedDict):
    delivery_id: str
    attempt_number: int
    attempted_at: datetime
    response_status_code: int | None
    error: str | None
    next_retry_at: datetime | None


def webhook_endpoint_fixtures() -> list[WebhookEndpoint]:
    """Return active, inactive, and reduced-retry endpoint examples."""
    return [
        {
            "id": "9e112d8b-0000-4000-8000-000000000001",
            "name": "Operations primary",
            "url": "https://ops.example.test/hooks/sla",
            "is_active": True,
            "events": ["sla.violation", "sla.warning", "sla.resolved"],
            "max_retries": 3,
            "has_signing_secret": True,
            "created_at": datetime(2026, 11, 1, 9, 0, tzinfo=UTC),
        },
        {
            "id": "9e112d8b-0000-4000-8000-000000000002",
            "name": "Legacy reporting endpoint",
            "url": "https://reports.example.test/hooks/events",
            "is_active": False,
            "events": ["sla.resolved"],
            "max_retries": 0,
            "has_signing_secret": False,
            "created_at": datetime(2026, 10, 15, 12, 0, tzinfo=UTC),
        },
    ]


def webhook_delivery_fixtures() -> list[WebhookDelivery]:
    """Return successful, retrying, dead-letter, and breaker-open deliveries."""
    return [
        {
            "id": "a711dd1e-0000-4000-8000-000000000001",
            "webhook_id": "9e112d8b-0000-4000-8000-000000000001",
            "event": "sla.violation",
            "payload": '{"event_id":"evt-001","outage_id":"outage-001","status":"violated"}',
            "status": "success",
            "attempt_count": 1,
            "next_retry_at": None,
            "response_status_code": 204,
            "created_at": datetime(2026, 11, 18, 10, 5, tzinfo=UTC),
            "delivered_at": datetime(2026, 11, 18, 10, 5, 1, tzinfo=UTC),
        },
        {
            "id": "a711dd1e-0000-4000-8000-000000000002",
            "webhook_id": "9e112d8b-0000-4000-8000-000000000001",
            "event": "sla.warning",
            "payload": '{"event_id":"evt-002","outage_id":"outage-002","status":"in_progress"}',
            "status": "success",
            "attempt_count": 2,
            "next_retry_at": None,
            "response_status_code": 204,
            "created_at": datetime(2026, 11, 18, 10, 6, tzinfo=UTC),
            "delivered_at": datetime(2026, 11, 18, 10, 10, 1, tzinfo=UTC),
        },
        {
            "id": "a711dd1e-0000-4000-8000-000000000005",
            "webhook_id": "9e112d8b-0000-4000-8000-000000000001",
            "event": "sla.warning",
            "payload": '{"event_id":"evt-005","outage_id":"outage-005","status":"in_progress"}',
            "status": "retrying",
            "attempt_count": 1,
            "next_retry_at": datetime(2026, 11, 18, 10, 11, tzinfo=UTC),
            "response_status_code": 503,
            "created_at": datetime(2026, 11, 18, 10, 7, tzinfo=UTC),
            "delivered_at": None,
        },
        {
            "id": "a711dd1e-0000-4000-8000-000000000003",
            "webhook_id": "9e112d8b-0000-4000-8000-000000000001",
            "event": "sla.resolved",
            "payload": '{"event_id":"evt-003","outage_id":"outage-003","status":"met"}',
            "status": "dead_letter",
            "attempt_count": 4,
            "next_retry_at": None,
            "response_status_code": 410,
            "created_at": datetime(2026, 11, 18, 10, 7, tzinfo=UTC),
            "delivered_at": None,
        },
        {
            "id": "a711dd1e-0000-4000-8000-000000000004",
            "webhook_id": "9e112d8b-0000-4000-8000-000000000001",
            "event": "sla.violation",
            "payload": '{"event_id":"evt-004","outage_id":"outage-004","status":"violated"}',
            "status": "breaker_open",
            "attempt_count": 0,
            "next_retry_at": datetime(2026, 11, 18, 10, 15, tzinfo=UTC),
            "response_status_code": None,
            "created_at": datetime(2026, 11, 18, 10, 8, tzinfo=UTC),
            "delivered_at": None,
        },
    ]


def webhook_delivery_attempt_fixtures() -> list[WebhookDeliveryAttempt]:
    """Return a transient failure followed by a successful retry."""
    return [
        {
            "delivery_id": "a711dd1e-0000-4000-8000-000000000002",
            "attempt_number": 1,
            "attempted_at": datetime(2026, 11, 18, 10, 6, tzinfo=UTC),
            "response_status_code": 503,
            "error": "temporary upstream failure",
            "next_retry_at": datetime(2026, 11, 18, 10, 10, tzinfo=UTC),
        },
        {
            "delivery_id": "a711dd1e-0000-4000-8000-000000000002",
            "attempt_number": 2,
            "attempted_at": datetime(2026, 11, 18, 10, 10, tzinfo=UTC),
            "response_status_code": 204,
            "error": None,
            "next_retry_at": None,
        },
    ]
