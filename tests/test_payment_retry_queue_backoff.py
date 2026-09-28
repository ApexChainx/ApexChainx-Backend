"""Tests for payment retry queue backoff visibility — Issue #240.

Validates that the retry queue endpoint returns computed backoff fields
and that the admin retry-now endpoint bypasses backoff.

Backoff semantics pinned here:
- attempt N < MAX_RETRIES: next retry at last_retried_at + min(30·2^N, 3600)s
- the 1h cap applies from attempt 5 on (30·2^5 = 960 > 3600 only at 2^7, but
  min() covers it), so ``retry_count=10`` still computes a 3600s delay
- attempt >= MAX_RETRIES (3): no next retry — the payment is exhausted
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.api.v1.endpoints.payments import _compute_next_retry_at, _retry_delay_seconds
from app.core.security import get_current_user
from app.main import app
from app.models.payment import PaymentTransaction

client = TestClient(app)


class _AdminUser:
    email = "retry-admin@example.com"
    role = "admin"


class _EngineerUser:
    email = "retry-engineer@example.com"
    role = "engineer"


def _override_auth() -> None:
    """These tests pin backoff/queue semantics, not auth plumbing."""
    app.dependency_overrides[get_current_user] = lambda: _EngineerUser()


class TestComputeNextRetryAt:
    def test_first_retry_30s(self):
        base = datetime(2025, 1, 1, tzinfo=UTC)
        result = _compute_next_retry_at(0, base)
        assert result == base + timedelta(seconds=30)

    def test_second_retry_60s(self):
        base = datetime(2025, 1, 1, tzinfo=UTC)
        result = _compute_next_retry_at(1, base)
        assert result == base + timedelta(seconds=60)

    def test_third_retry_120s(self):
        base = datetime(2025, 1, 1, tzinfo=UTC)
        result = _compute_next_retry_at(2, base)
        assert result == base + timedelta(seconds=120)

    def test_capped_at_1_hour(self):
        # The *delay* math caps at 1h for any attempt count; exhaustion (None)
        # is handled by the MAX_RETRIES branch in _compute_next_retry_at.
        assert _retry_delay_seconds(10) == 3600

    def test_returns_none_at_max_retries(self):
        result = _compute_next_retry_at(3, datetime.now(UTC))
        assert result is None


class TestRetryQueueEndpoint:
    def setup_method(self):
        _override_auth()

    def teardown_method(self):
        app.dependency_overrides.pop(get_current_user, None)

    @patch("app.api.v1.endpoints.payments.PaymentRepository")
    def test_returns_retry_queue_items(self, MockRepo):
        mock_repo = MagicMock()
        MockRepo.return_value = mock_repo
        now = datetime.now(UTC)
        mock_repo.list.return_value = (
            [
                PaymentTransaction(
                    id="pay_001",
                    transaction_hash="tx1",
                    type="penalty",
                    amount=10.0,
                    asset_code="USDC",
                    from_address="A",
                    to_address="B",
                    status="failed",
                    outage_id="out_001",
                    created_at=now,
                    retry_count=1,
                    last_retried_at=now,
                )
            ],
            1,
        )

        resp = client.get("/api/v1/payments/retry-queue")
        assert resp.status_code == 200
        data = resp.json()
        # GET /retry-queue returns the shared CursorPage envelope (#564),
        # not a bare list.
        assert data["has_more"] is False
        assert data["next_cursor"] is None
        assert len(data["items"]) == 1
        item = data["items"][0]
        assert item["id"] == "pay_001"
        assert item["attempt_count"] == 1
        assert item["backoff_seconds"] == 60
        assert item["next_retry_at"] is not None


class TestRetryNowEndpoint:
    def setup_method(self):
        app.dependency_overrides[get_current_user] = lambda: _AdminUser()

    def teardown_method(self):
        app.dependency_overrides.pop(get_current_user, None)

    @patch("app.api.v1.endpoints.payments.audit_log")
    @patch("app.api.v1.endpoints.payments.PaymentRepository")
    def test_admin_can_retry_immediately(self, MockRepo, mock_audit):
        mock_repo = MagicMock()
        MockRepo.return_value = mock_repo
        now = datetime.now(UTC)
        existing = PaymentTransaction(
            id="pay_002",
            transaction_hash="tx2",
            type="penalty",
            amount=20.0,
            asset_code="USDC",
            from_address="A",
            to_address="B",
            status="failed",
            outage_id="out_002",
            created_at=now,
            retry_count=0,
            last_retried_at=None,
        )
        retried = PaymentTransaction(
            id="pay_002",
            transaction_hash="tx2",
            type="penalty",
            amount=20.0,
            asset_code="USDC",
            from_address="A",
            to_address="B",
            status="pending",
            outage_id="out_002",
            created_at=now,
            retry_count=1,
            last_retried_at=now,
        )
        mock_repo.get.return_value = existing
        mock_repo.retry.return_value = retried

        resp = client.post("/api/v1/payments/retry-queue/pay_002/retry")
        assert resp.status_code == 200
        assert resp.json()["retry_count"] == 1
        mock_audit.log.assert_called_once()

    @patch("app.api.v1.endpoints.payments.PaymentRepository")
    def test_returns_404_when_not_found(self, MockRepo):
        mock_repo = MagicMock()
        MockRepo.return_value = mock_repo
        mock_repo.get.return_value = None

        resp = client.post("/api/v1/payments/retry-queue/nonexistent/retry")
        assert resp.status_code == 404

    @patch("app.api.v1.endpoints.payments.PaymentRepository")
    def test_returns_409_when_max_retries_reached(self, MockRepo):
        mock_repo = MagicMock()
        MockRepo.return_value = mock_repo
        now = datetime.now(UTC)
        existing = PaymentTransaction(
            id="pay_003",
            transaction_hash="tx3",
            type="penalty",
            amount=30.0,
            asset_code="USDC",
            from_address="A",
            to_address="B",
            status="failed",
            outage_id="out_003",
            created_at=now,
            retry_count=3,
            last_retried_at=now,
        )
        mock_repo.get.return_value = existing
        mock_repo.retry.return_value = None  # max retries

        resp = client.post("/api/v1/payments/retry-queue/pay_003/retry")
        assert resp.status_code == 409
