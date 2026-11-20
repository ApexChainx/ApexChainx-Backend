import json
from datetime import UTC

from app.sla_operations.fixtures.bulk_import_fixtures import bulk_import_row_fixtures
from app.sla_operations.fixtures.settlement_fixtures import (
    reconciliation_fixtures,
    settlement_payment_fixtures,
)
from app.sla_operations.fixtures.timeline_fixtures import timeline_event_fixtures
from app.sla_operations.fixtures.webhook_fixtures import (
    webhook_delivery_attempt_fixtures,
    webhook_delivery_fixtures,
    webhook_endpoint_fixtures,
)


def test_settlement_fixtures_are_deterministic_and_mutation_safe() -> None:
    first = settlement_payment_fixtures()
    second = settlement_payment_fixtures()

    assert first == second
    assert first is not second
    assert first[0] is not second[0]
    assert {payment["type"] for payment in first} == {"reward", "penalty"}
    assert {payment["status"] for payment in first} >= {"pending", "confirmed", "failed"}
    assert all(payment["created_at"].tzinfo == UTC for payment in first)
    assert any(not item["matches"] for item in reconciliation_fixtures())


def test_webhook_fixtures_cover_endpoint_and_delivery_states_without_secrets() -> None:
    endpoints = webhook_endpoint_fixtures()
    deliveries = webhook_delivery_fixtures()

    assert {endpoint["is_active"] for endpoint in endpoints} == {True, False}
    assert all(".test/" in endpoint["url"] for endpoint in endpoints)
    assert all("secret" not in endpoint for endpoint in endpoints)
    assert {delivery["status"] for delivery in deliveries} >= {
        "success",
        "retrying",
        "dead_letter",
        "breaker_open",
    }
    assert all(isinstance(json.loads(delivery["payload"]), dict) for delivery in deliveries)
    assert next(item for item in deliveries if item["id"].endswith("0002"))["attempt_count"] == 2
    assert [attempt["attempt_number"] for attempt in webhook_delivery_attempt_fixtures()] == [1, 2]


def test_bulk_import_fixtures_mark_valid_invalid_and_edge_rows() -> None:
    rows = bulk_import_row_fixtures()

    assert any(row["expected_valid"] for row in rows)
    assert any(not row["expected_valid"] for row in rows)
    assert rows[1]["data"]["affected_subscribers"] == "0"
    assert {error for row in rows for error in row["expected_errors"]} >= {
        "invalid_severity",
        "resolved_before_detected",
        "duplicate_outage_id",
    }


def test_timeline_fixtures_are_ordered_and_timezone_aware() -> None:
    events = timeline_event_fixtures()

    assert [event["occurred_at"] for event in events] == sorted(event["occurred_at"] for event in events)
    assert {event["event_type"] for event in events} >= {"created", "resolved", "sla_computed", "sla_recomputed"}
    assert all(event["occurred_at"].tzinfo == UTC for event in events)
    assert events[2]["occurred_at"] == events[3]["occurred_at"]
