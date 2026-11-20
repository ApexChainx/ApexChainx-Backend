from datetime import UTC, datetime, timedelta

from app.sla_operations.services.webhook_delivery_insights import (
    WebhookDeliveryInsightsService,
    summarize_webhook_deliveries,
)


def test_summarize_webhook_deliveries_groups_outcomes_and_retry_signals():
    start = datetime(2026, 1, 1, tzinfo=UTC)
    records = [
        {
            "delivery_id": "d1",
            "webhook_id": "w1",
            "event": "sla.violation",
            "status": "success",
            "attempt_count": 1,
            "response_status_code": 200,
            "delivered_at": start,
        },
        {
            "delivery_id": "d2",
            "webhook_id": "w1",
            "event": "sla.violation",
            "status": "retrying",
            "attempt_count": 2,
            "next_retry_at": start + timedelta(minutes=5),
            "response_status_code": 503,
            "error_message": "timeout",
        },
        {
            "delivery_id": "d3",
            "webhook_id": "w2",
            "event": "sla.resolved",
            "status": "dead_letter",
            "attempt_count": 4,
            "dead_lettered_at": start + timedelta(minutes=10),
            "response_status_code": 500,
            "error_message": "failed",
        },
        {
            "delivery_id": "d4",
            "webhook_id": "w2",
            "event": "sla.resolved",
            "status": "breaker_open",
            "attempt_count": 0,
            "next_retry_at": start + timedelta(minutes=20),
            "response_status_code": 429,
        },
        {
            "delivery_id": "d5",
            "webhook_id": "w1",
            "event": "sla.violation",
            "status": "failed",
            "attempt_count": 3,
            "response_status_code": 404,
            "error_message": "bad request",
        },
    ]

    report = summarize_webhook_deliveries(records, retry_attempt_threshold=3)

    assert report.total_records == 5
    assert report.invalid_records == 0
    assert report.totals.success == 1
    assert report.totals.failed == 1
    assert report.totals.retrying == 1
    assert report.totals.dead_letter == 1
    assert report.totals.breaker_open == 1
    assert report.totals.attempted_records == 4
    assert report.totals.average_attempts == 2.0
    assert report.totals.max_attempts == 4
    assert report.success_rate_percent == 20.0
    assert report.failure_rate_percent == 40.0
    assert report.retry_rate_percent == 60.0
    assert report.retry_attempt_threshold == 3
    assert report.retry_signals.records_with_attempts == 4
    assert report.retry_signals.retried_records == 3
    assert report.retry_signals.retries_scheduled == 2
    assert report.retry_signals.at_or_above_attempt_threshold == 2
    assert report.retry_signals.dead_lettered == 1
    assert report.retry_signals.breaker_open == 1
    assert report.retry_signals.success_responses == 1
    assert report.retry_signals.client_error_responses == 2
    assert report.retry_signals.server_error_responses == 2
    assert report.retry_signals.error_message_present == 3
    assert [
        (group.name, group.totals.total, group.success_rate_percent, group.retry_rate_percent)
        for group in report.by_event
    ] == [
        ("sla.resolved", 2, 0.0, 50.0),
        ("sla.violation", 3, 33.33, 66.67),
    ]
    assert [(group.name, group.totals.total) for group in report.by_webhook] == [("w1", 3), ("w2", 2)]


def test_summarize_webhook_deliveries_handles_invalid_values_without_raising():
    records = [
        object(),
        {"status": "unknown-status", "attempt_count": -5.0, "response_status_code": 799, "error_message": "   "},
        {"status": "succeeded", "attempt_count": None},
        {"status": "success", "attempt_count": "not-a-number"},
    ]

    report = WebhookDeliveryInsightsService.summarize(records, retry_attempt_threshold=0)

    assert report.total_records == 4
    assert report.invalid_records == 2
    assert report.totals.unknown == 3
    assert report.totals.success == 1
    assert report.totals.average_attempts == 0.0
    assert report.totals.max_attempts == 0
    assert report.retry_attempt_threshold == 3
    assert report.retry_signals.records_with_attempts == 0
    assert report.retry_signals.error_message_present == 0
    assert [(group.name, group.totals.total) for group in report.by_event] == [("unknown", 4)]


def test_summarize_webhook_deliveries_handles_empty_input():
    report = summarize_webhook_deliveries([])

    assert report.total_records == 0
    assert report.invalid_records == 0
    assert report.totals.total == 0
    assert report.success_rate_percent == 0.0
    assert report.failure_rate_percent == 0.0
    assert report.retry_rate_percent == 0.0
    assert report.by_event == []
    assert report.by_webhook == []
