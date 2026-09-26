import asyncio
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fakeredis.aioredis import FakeRedis
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.core.rate_limiter import RedisRateLimiter
from app.models.auth_attempt import AuthAttemptLedger
from app.models.webhook import WebhookDelivery, WebhookDeliveryStatus, WebhookEvent
from app.services.auth_attempt_ledger import (
    get_credential_prefix_count,
    record_credential_prefix,
    record_rate_limit_attempt,
)
from app.services.webhook_service import _attempt_delivery, dispatch_delivery


@pytest.fixture
def ledger_db():
    engine = create_engine("sqlite://")
    AuthAttemptLedger.__table__.create(engine)
    session_factory = sessionmaker(bind=engine)
    db = session_factory()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


def test_rate_limit_survives_lost_redis_counter(ledger_db, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_RATE_LIMIT_REQUESTS", 2)
    monkeypatch.setattr(settings, "AUTH_RATE_LIMIT_WINDOW_SECONDS", 300)
    monkeypatch.setattr(settings, "USE_REDIS_RATE_LIMITER", True)
    monkeypatch.setattr(settings, "CELERY_TASK_ALWAYS_EAGER", False)

    redis_client = FakeRedis()
    limiter = RedisRateLimiter()
    limiter.client = redis_client
    try:
        assert limiter.is_allowed("login_ip_test", db=ledger_db)
        assert limiter.is_allowed("login_ip_test", db=ledger_db)

        asyncio.run(redis_client.flushall())

        assert not limiter.is_allowed("login_ip_test", db=ledger_db)
    finally:
        asyncio.run(redis_client.close())


def test_credential_prefixes_survive_redis_counter_loss(ledger_db, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_CREDENTIAL_STUFFING_WINDOW_MINUTES", 5)
    record_credential_prefix(ledger_db, "user@example.com", "first-password", 3)
    record_credential_prefix(ledger_db, "user@example.com", "second-password", 3)
    record_credential_prefix(ledger_db, "user@example.com", "third-password", 3)

    assert get_credential_prefix_count(ledger_db, "user@example.com", 300) == 3
    assert get_credential_prefix_count(ledger_db, "other@example.com", 300) == 0
    assert record_rate_limit_attempt(ledger_db, "different-scope", 1, 300)


def test_crashed_delivery_claim_cannot_be_dispatched_twice():
    delivery_id = uuid4()
    delivery = MagicMock(spec=WebhookDelivery)
    delivery.id = delivery_id
    delivery.attempt_count = 1
    delivery.status = WebhookDeliveryStatus.SENDING
    delivery.webhook = MagicMock()

    query = MagicMock()
    query.filter.return_value = query
    query.update.side_effect = [1, 0]
    query.first.return_value = delivery
    db = MagicMock()
    db.query.return_value = query

    with patch(
        "app.services.webhook_service._attempt_delivery",
        side_effect=RuntimeError("simulated worker crash after send"),
    ) as attempt:
        with pytest.raises(RuntimeError, match="simulated worker crash"):
            dispatch_delivery(db, delivery_id)
        dispatch_delivery(db, delivery_id)

    attempt.assert_called_once()


def test_delivery_request_includes_stable_delivery_id(monkeypatch):
    delivery_id = uuid4()
    delivery = MagicMock(spec=WebhookDelivery)
    delivery.id = delivery_id
    delivery.payload = "{}"
    delivery.event = WebhookEvent.SLA_VIOLATION
    delivery.signature_version = 1

    webhook = MagicMock()
    webhook.secret = None
    webhook.url = "https://receiver.example/webhook"

    response = MagicMock(is_success=True, text="ok")
    client = MagicMock()
    client.__enter__.return_value = client
    client.post.return_value = response
    monkeypatch.setattr(
        "app.services.webhook_service.validate_webhook_url",
        lambda url: [],
    )
    monkeypatch.setattr(
        "app.services.webhook_service.httpx.Client",
        lambda **kwargs: client,
    )

    assert _attempt_delivery(delivery, webhook)
    assert client.post.call_args.kwargs["headers"]["X-Webhook-Delivery-ID"] == str(
        delivery_id
    )