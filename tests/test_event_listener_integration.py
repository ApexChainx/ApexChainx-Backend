"""Integration tests for the event listener (issue #735).

The unit tests pin the listener's decisions. These tests pin the two things
that only show up when the listener is wired to the real database and the
real delivery pipeline:

* the subscription filter really does exclude the rows it should --
  deactivated webhooks and soft-deleted tombstones must not be delivered
  to;
* an emitted event really does produce one delivery row per subscriber, and
  the delivery state machine really does run.

The database is an in-memory SQLite, so the tests need no service and are
deterministic.
"""

import json
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker

from app.models.webhook import Webhook, WebhookDelivery, WebhookDeliveryStatus, WebhookEvent


@compiles(JSONB, "sqlite")
def _compile_jsonb_on_sqlite(type_, **kw):
    """Render the PostgreSQL JSONB column as plain JSON on SQLite.

    SQLite has no JSONB. The column only holds the list of previous signing
    secrets, and plain JSON is enough for the test.
    """
    return "JSON"


@pytest.fixture
def engine():
    """An in-memory SQLite engine holding the webhook tables."""
    engine = create_engine("sqlite:///:memory:", echo=False)

    # Only the webhook tables are created: the rest of the schema uses
    # PostgreSQL types that SQLite cannot render.
    from app.db.base_class import Base

    Base.metadata.create_all(bind=engine, tables=[Webhook.__table__, WebhookDelivery.__table__])
    yield engine
    engine.dispose()


@pytest.fixture
def db(engine) -> Session:
    """A fresh session per test."""
    session_factory = sessionmaker(bind=engine)
    session = session_factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _register(db: Session, name: str, events: list[str], **kwargs) -> Webhook:
    webhook = Webhook(
        name=name,
        url=f"https://example.com/{name}",
        secret=None,
        is_active=kwargs.pop("is_active", True),
        events=json.dumps(events),
        max_retries=3,
        **kwargs,
    )
    db.add(webhook)
    db.commit()
    db.refresh(webhook)
    return webhook


class TestSubscriberResolution:
    def test_a_registered_webhook_is_a_subscriber(self, db):
        from app.services.event_listener import EventListener

        _register(db, "w-1", ["sla.violation"])

        subscribers = EventListener(db).subscribers(WebhookEvent.SLA_VIOLATION)

        assert [webhook.name for webhook in subscribers] == ["w-1"]

    def test_a_webhook_for_another_event_is_not_a_subscriber(self, db):
        from app.services.event_listener import EventListener

        _register(db, "w-1", ["sla.resolved"])

        assert EventListener(db).subscribers(WebhookEvent.SLA_VIOLATION) == []

    def test_a_deactivated_webhook_is_not_a_subscriber(self, db):
        from app.services.event_listener import EventListener

        _register(db, "w-1", ["sla.violation"], is_active=False)

        assert EventListener(db).subscribers(WebhookEvent.SLA_VIOLATION) == []

    def test_a_soft_deleted_webhook_is_not_a_subscriber(self, db):
        from app.services.event_listener import EventListener

        _register(db, "w-1", ["sla.violation"], is_active=False, deleted_at=datetime.now(UTC))

        assert EventListener(db).subscribers(WebhookEvent.SLA_VIOLATION) == []

    def test_only_the_subscribers_of_the_event_are_returned(self, db):
        from app.services.event_listener import EventListener

        _register(db, "w-1", ["sla.violation"])
        _register(db, "w-2", ["sla.resolved"])
        _register(db, "w-3", ["sla.warning", "sla.violation"])

        subscribers = EventListener(db).subscribers(WebhookEvent.SLA_VIOLATION)

        assert sorted(webhook.name for webhook in subscribers) == ["w-1", "w-3"]


class TestEmittedEvent:
    def test_an_emitted_event_produces_one_delivery_per_subscriber(self, db):
        from app.services.webhook_service import trigger_sla_violation_webhooks

        _register(db, "w-1", ["sla.violation"])
        _register(db, "w-2", ["sla.violation"])
        _register(db, "w-3", ["sla.resolved"])

        with patch("app.services.webhook_service._attempt_delivery", return_value=True):
            deliveries = trigger_sla_violation_webhooks(db, {"device_id": "d-1"})

        assert len(deliveries) == 2
        rows = db.query(WebhookDelivery).all()
        assert len(rows) == 2
        assert all(row.event == WebhookEvent.SLA_VIOLATION for row in rows)

    def test_the_delivery_reaches_the_success_state(self, db):
        from app.services.webhook_service import trigger_sla_violation_webhooks

        _register(db, "w-1", ["sla.violation"])

        with patch("app.services.webhook_service._attempt_delivery", return_value=True):
            trigger_sla_violation_webhooks(db, {"device_id": "d-1"})

        row = db.query(WebhookDelivery).one()
        assert row.status == WebhookDeliveryStatus.SUCCESS
        assert row.delivered_at is not None
        assert row.attempt_count == 1

    def test_the_payload_carries_the_event_and_the_data(self, db):
        from app.services.webhook_service import trigger_sla_violation_webhooks

        _register(db, "w-1", ["sla.violation"])

        with patch("app.services.webhook_service._attempt_delivery", return_value=True):
            trigger_sla_violation_webhooks(db, {"device_id": "d-1"})

        payload = json.loads(db.query(WebhookDelivery).one().payload)
        assert payload["event"] == "sla.violation"
        assert payload["data"] == {"device_id": "d-1"}
        assert payload["schema_version"] == "1"

    def test_a_failed_delivery_is_retried_rather_than_lost(self, db):
        from app.services.webhook_service import trigger_sla_violation_webhooks

        _register(db, "w-1", ["sla.violation"])

        with patch("app.services.webhook_service._attempt_delivery", return_value=False):
            trigger_sla_violation_webhooks(db, {"device_id": "d-1"})

        row = db.query(WebhookDelivery).one()
        assert row.status == WebhookDeliveryStatus.RETRYING
        assert row.next_retry_at is not None

    def test_no_subscriber_means_no_delivery(self, db):
        from app.services.webhook_service import trigger_sla_violation_webhooks

        _register(db, "w-1", ["sla.resolved"])

        with patch("app.services.webhook_service._attempt_delivery", return_value=True):
            deliveries = trigger_sla_violation_webhooks(db, {"device_id": "d-1"})

        assert deliveries == []
        assert db.query(WebhookDelivery).count() == 0
