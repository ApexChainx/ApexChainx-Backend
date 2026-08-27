"""Tests for Redis-backed single-use token store — Issue #267.

Verifies that payment callback nonce replay protection is shared across
independent store instances (simulating separate workers), survives the
TTL window semantics, and degrades to a bounded in-process fallback without
5xx errors when Redis is unavailable.
"""

import hashlib
import hmac
import time
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch
from uuid import uuid4

import fakeredis
from fastapi.testclient import TestClient
from redis.exceptions import RedisError

import app.api.v1.endpoints.payments as payments_module
from app.main import app
from app.models.payment import PaymentTransaction
from app.services.single_use_token_store import SingleUseTokenStore

client = TestClient(app)

CALLBACK_SECRET = "test-webhook-secret-1234"


class _BrokenRedis:
    """Redis client that always fails, to exercise the fallback path."""

    def set(self, *args, **kwargs):  # noqa: ANN002, ANN003
        raise RedisError("connection refused")


class TestCrossWorkerReplayProtection:
    def test_duplicate_nonce_rejected_across_independent_stores(self):
        """Two store instances backed by the same Redis reject the same nonce."""
        shared_redis = fakeredis.FakeRedis()
        store_a = SingleUseTokenStore(ttl_seconds=300, redis_client=shared_redis)
        store_b = SingleUseTokenStore(ttl_seconds=300, redis_client=shared_redis)

        nonce = f"nonce-{uuid4().hex}"
        assert store_a.consume(nonce) is False  # first use
        assert store_b.consume(nonce) is True  # replay on the "other worker"

    def test_distinct_nonces_accepted(self):
        shared_redis = fakeredis.FakeRedis()
        store = SingleUseTokenStore(ttl_seconds=300, redis_client=shared_redis)
        assert store.consume("nonce-one") is False
        assert store.consume("nonce-two") is False

    def test_replay_window_expires(self):
        shared_redis = fakeredis.FakeRedis()
        store = SingleUseTokenStore(ttl_seconds=1, redis_client=shared_redis)
        nonce = f"nonce-{uuid4().hex}"
        assert store.consume(nonce) is False
        time.sleep(1.1)
        # The token has expired — the same value is accepted again.
        assert store.consume(nonce) is False


class TestRedisOutagePolicy:
    def test_fallback_used_when_redis_down_without_error(self):
        store = SingleUseTokenStore(ttl_seconds=300, redis_client=_BrokenRedis())
        nonce = f"nonce-{uuid4().hex}"
        assert store.consume(nonce) is False  # accepted via fallback, no exception
        assert store.consume(nonce) is True  # fallback still detects the replay

    def test_circuit_trips_after_redis_failure(self):
        store = SingleUseTokenStore(ttl_seconds=300, redis_client=_BrokenRedis())
        store.consume("any-nonce")
        assert store._circuit_open() is True
        # The fallback is bounded: entries do not grow without limit.
        assert len(store._fallback) <= 1

    def test_empty_broker_url_uses_fallback(self, monkeypatch):
        monkeypatch.setattr("app.services.single_use_token_store.settings.CELERY_BROKER_URL", "")
        store = SingleUseTokenStore(ttl_seconds=300, redis_client=_BrokenRedis())
        assert store.consume("nonce-x") is False
        assert store.consume("nonce-x") is True


class TestCallbackEndpointReplay:
    @patch("app.api.v1.endpoints.payments.audit_log")
    @patch("app.api.v1.endpoints.payments.PaymentRepository")
    def test_duplicate_nonce_across_workers_rejected_409(self, MockRepo, mock_audit, monkeypatch):
        """A nonce consumed on one store instance is rejected at the endpoint level."""
        from app.core.config import settings

        monkeypatch.setattr(settings, "PAYMENT_WEBHOOK_SECRET", CALLBACK_SECRET)
        shared_redis = fakeredis.FakeRedis()
        monkeypatch.setattr(
            payments_module,
            "single_use_token_store",
            SingleUseTokenStore(ttl_seconds=300, redis_client=shared_redis),
        )

        mock_repo = MagicMock()
        MockRepo.return_value = mock_repo
        now = datetime.now(UTC)
        existing = PaymentTransaction(
            id="pay_replay_001",
            transaction_hash="tx",
            type="penalty",
            amount=10.0,
            asset_code="USDC",
            from_address="A",
            to_address="B",
            status="pending",
            outage_id="out_1",
            created_at=now,
            retry_count=0,
        )
        mock_repo.get.return_value = existing
        mock_repo.reconcile.return_value = existing.model_copy(update={"status": "confirmed"})

        nonce = f"n_{uuid4().hex}"
        body = {"transaction_id": "pay_replay_001", "status": "confirmed", "nonce": nonce}
        message = f"pay_replay_001:confirmed:{nonce}"
        signature = hmac.new(CALLBACK_SECRET.encode(), message.encode(), hashlib.sha256).hexdigest()
        headers = {"X-Webhook-Signature": signature, "X-Callback-Nonce": nonce}

        # Simulate the callback landing on two different workers: consume the
        # nonce through a separate store instance first, then hit the endpoint.
        assert SingleUseTokenStore(ttl_seconds=300, redis_client=shared_redis).consume(nonce) is False

        resp = client.post("/api/v1/payments/provider-callback", json=body, headers=headers)
        assert resp.status_code == 409
