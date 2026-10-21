"""Tests for payment provider-callback authentication hardening — Issue #264.

Covers the callback entry point security contract:
- Nonce-less callbacks are rejected with a dedicated 400 error.
- When PAYMENT_WEBHOOK_SECRET is configured, callbacks without a valid
  HMAC signature are rejected with 401 and audit-logged.
- Startup validation fails in non-local environments when
  PAYMENT_WEBHOOK_SECRET is unset.
"""

import hashlib
import hmac
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, settings, validate_critical_settings
from app.main import app
from app.models.payment import PaymentTransaction

client = TestClient(app)

CALLBACK_SECRET = "test-webhook-secret-1234"


def _sign(transaction_id: str, status: str, nonce: str) -> str:
    """Build the canonical HMAC-SHA256 callback signature."""
    message = f"{transaction_id}:{status}:{nonce}"
    return hmac.new(CALLBACK_SECRET.encode(), message.encode(), hashlib.sha256).hexdigest()


def _callback_headers(transaction_id: str, status: str, nonce: str) -> dict[str, str]:
    return {
        "X-Webhook-Signature": _sign(transaction_id, status, nonce),
        "X-Callback-Nonce": nonce,
    }


def _payment(status: str = "pending", payment_id: str = "pay_001") -> PaymentTransaction:
    now = datetime.now(UTC)
    return PaymentTransaction(
        id=payment_id,
        transaction_hash="tx_abc",
        type="penalty",
        amount=10.0,
        asset_code="USDC",
        from_address="A",
        to_address="B",
        status=status,
        outage_id="out_001",
        created_at=now,
        retry_count=0,
    )


class TestNonceRequired:
    @patch("app.api.v1.endpoints.payments.audit_log")
    def test_nonce_less_callback_rejected_with_dedicated_error(self, mock_audit, monkeypatch):
        """A callback without a nonce is rejected with a dedicated 400 code."""
        monkeypatch.setattr(settings, "PAYMENT_WEBHOOK_SECRET", CALLBACK_SECRET)
        resp = client.post(
            "/api/v1/payments/provider-callback",
            json={"transaction_id": "pay_001", "status": "confirmed"},
        )
        assert resp.status_code == 400
        assert "nonce" in resp.json()["detail"].lower()
        mock_audit.log.assert_called_once()
        assert mock_audit.log.call_args[0][0] == "callback_rejected_missing_nonce"

    @patch("app.api.v1.endpoints.payments.audit_log")
    def test_nonce_less_callback_rejected_even_without_secret(self, mock_audit, monkeypatch):
        """Nonce enforcement must not depend on PAYMENT_WEBHOOK_SECRET being set."""
        monkeypatch.setattr(settings, "PAYMENT_WEBHOOK_SECRET", "")
        resp = client.post(
            "/api/v1/payments/provider-callback",
            json={"transaction_id": "pay_001", "status": "confirmed"},
        )
        assert resp.status_code == 400
        assert mock_audit.log.call_args[0][0] == "callback_rejected_missing_nonce"


class TestSignatureRequired:
    @patch("app.api.v1.endpoints.payments.audit_log")
    def test_missing_signature_rejected_401(self, mock_audit, monkeypatch):
        monkeypatch.setattr(settings, "PAYMENT_WEBHOOK_SECRET", CALLBACK_SECRET)
        resp = client.post(
            "/api/v1/payments/provider-callback",
            json={"transaction_id": "pay_001", "status": "confirmed", "nonce": "n1"},
            headers={"X-Callback-Nonce": "n1"},
        )
        assert resp.status_code == 401
        assert mock_audit.log.call_args[0][0] == "callback_rejected_missing_signature"

    @patch("app.api.v1.endpoints.payments.audit_log")
    def test_invalid_signature_rejected_401(self, mock_audit, monkeypatch):
        monkeypatch.setattr(settings, "PAYMENT_WEBHOOK_SECRET", CALLBACK_SECRET)
        resp = client.post(
            "/api/v1/payments/provider-callback",
            json={"transaction_id": "pay_001", "status": "confirmed", "nonce": "n2"},
            headers={
                "X-Callback-Nonce": "n2",
                "X-Webhook-Signature": "0" * 64,
            },
        )
        assert resp.status_code == 401
        assert mock_audit.log.call_args[0][0] == "callback_rejected_bad_signature"

    @patch("app.api.v1.endpoints.payments.audit_log")
    @patch("app.api.v1.endpoints.payments.PaymentRepository")
    def test_valid_signature_and_nonce_updates_payment(self, MockRepo, mock_audit, monkeypatch):
        monkeypatch.setattr(settings, "PAYMENT_WEBHOOK_SECRET", CALLBACK_SECRET)
        mock_repo = MagicMock()
        MockRepo.return_value = mock_repo
        existing = _payment(status="pending")
        mock_repo.get.return_value = existing
        mock_repo.reconcile.return_value = existing.model_copy(update={"status": "confirmed"})

        nonce = f"n_{uuid4().hex}"
        resp = client.post(
            "/api/v1/payments/provider-callback",
            json={"transaction_id": "pay_001", "status": "confirmed", "nonce": nonce},
            headers=_callback_headers("pay_001", "confirmed", nonce),
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "confirmed"
        mock_repo.reconcile.assert_called_once_with("pay_001", "confirmed")


class TestReplayProtection:
    @patch("app.api.v1.endpoints.payments.audit_log")
    @patch("app.api.v1.endpoints.payments.PaymentRepository")
    def test_duplicate_nonce_rejected_409(self, MockRepo, mock_audit, monkeypatch):
        """The same nonce submitted twice within the window is rejected as a replay."""
        monkeypatch.setattr(settings, "PAYMENT_WEBHOOK_SECRET", CALLBACK_SECRET)
        mock_repo = MagicMock()
        MockRepo.return_value = mock_repo
        existing = _payment(status="pending")
        mock_repo.get.return_value = existing
        mock_repo.reconcile.return_value = existing.model_copy(update={"status": "confirmed"})

        nonce = f"n_{uuid4().hex}"
        first = client.post(
            "/api/v1/payments/provider-callback",
            json={"transaction_id": "pay_001", "status": "confirmed", "nonce": nonce},
            headers=_callback_headers("pay_001", "confirmed", nonce),
        )
        assert first.status_code == 200

        second = client.post(
            "/api/v1/payments/provider-callback",
            json={"transaction_id": "pay_001", "status": "confirmed", "nonce": nonce},
            headers=_callback_headers("pay_001", "confirmed", nonce),
        )
        assert second.status_code == 409


class TestStartupValidation:
    def make_settings(self, **overrides):
        defaults = {
            "PROJECT_NAME": "ApexChainx API",
            "VERSION": "1.0.0",
            "DEBUG": False,
            "DATABASE_URL": "postgresql://postgres:password@localhost:5432/apexchainx",
            "API_V1_PREFIX": "/api/v1",
            "ALLOWED_ORIGINS": ["http://localhost:3000"],
            "CELERY_BROKER_URL": "redis://localhost:6379/0",
            "CELERY_RESULT_BACKEND": "redis://localhost:6379/0",
            "CELERY_TASK_ALWAYS_EAGER": True,
            "SLA_CONTRACT_ADDRESS": "local-sla-calculator",
            "STELLAR_NETWORK": "testnet",
            "CONTRACT_EXECUTION_MODE": "local_adapter",
            "ENVIRONMENT": "production",
            "SECRET_KEY": "a-very-long-secure-production-secret-key-1234567890",
            "PAYMENT_WEBHOOK_SECRET": "some-secret",
        }
        defaults.update(overrides)
        return Settings.model_construct(**defaults)

    def test_startup_fails_when_webhook_secret_unset_in_production(self):
        with pytest.raises(ValueError) as ctx:
            validate_critical_settings(self.make_settings(PAYMENT_WEBHOOK_SECRET=""))
        assert "PAYMENT_WEBHOOK_SECRET" in str(ctx.value)

    def test_startup_succeeds_with_secret_in_production(self):
        validate_critical_settings(self.make_settings(PAYMENT_WEBHOOK_SECRET="s3cr3t"))

    def test_local_environment_allows_unset_secret(self):
        validate_critical_settings(
            self.make_settings(ENVIRONMENT="local", SECRET_KEY="apexchainx-dev-secret", PAYMENT_WEBHOOK_SECRET="")
        )
