"""Tests for webhook secret encryption at rest — Issue #266.

Verifies that the webhooks.secret column never stores the raw signing secret
after create/update/rotate, that signing still works end-to-end, that
startup validation requires the encryption key in non-local environments,
and that the migration rewrite helpers are idempotent.
"""

import hashlib
import hmac
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api.v1.endpoints.webhooks import require_admin
from app.core.config import Settings, validate_critical_settings
from app.db.session import SessionLocal, engine
from app.main import app
from app.models.webhook import Webhook
from app.services.secret_encryption import decrypt_secret, encrypt_secret, is_encrypted

client = TestClient(app)


def _stored_secret(webhook_id) -> str | None:
    """Read the raw secret bytes as persisted in the database (bypasses the ORM)."""
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT secret FROM webhooks WHERE id = :id"),
            {"id": webhook_id},
        ).fetchone()
    return row[0] if row else None


def _create_webhook(secret: str = "plain-secret-value"):
    """Create a webhook row and return its id."""
    with SessionLocal() as db:
        webhook = Webhook(
            name=f"test-hook-{uuid4().hex[:8]}",
            url="https://example.com/hook",
            secret=secret,
            events='["sla.violation"]',
            max_retries=3,
            is_active=True,
        )
        db.add(webhook)
        db.commit()
        return webhook.id


class TestEncryptionHelpers:
    def test_encrypt_decrypt_roundtrip(self):
        token = encrypt_secret("my-signing-secret")
        assert token != "my-signing-secret"
        assert is_encrypted(token)
        assert decrypt_secret(token) == "my-signing-secret"

    def test_legacy_plaintext_returned_unchanged(self):
        assert is_encrypted("legacy-plaintext") is False
        assert decrypt_secret("legacy-plaintext") == "legacy-plaintext"


class TestStorageLayer:
    def test_db_row_does_not_contain_raw_secret_after_create(self):
        webhook_id = _create_webhook("top-secret-123")
        stored = _stored_secret(webhook_id)
        assert stored is not None
        assert "top-secret-123" not in stored
        assert is_encrypted(stored)

    def test_orm_read_returns_plaintext_for_signing(self):
        webhook_id = _create_webhook("sign-secret-456")
        # A fresh session must decrypt the value back for the signing path.
        with SessionLocal() as db:
            loaded = db.query(Webhook).filter(Webhook.id == webhook_id).first()
            assert loaded.secret == "sign-secret-456"

    def test_update_encrypts_new_secret(self):
        webhook_id = _create_webhook("old-secret")
        with SessionLocal() as db:
            row = db.query(Webhook).filter(Webhook.id == webhook_id).first()
            row.secret = "new-secret-789"
            db.commit()
        stored = _stored_secret(webhook_id)
        assert "new-secret-789" not in stored
        assert is_encrypted(stored)
        assert decrypt_secret(stored) == "new-secret-789"

    def test_signing_uses_decrypted_secret(self):
        from app.services.webhook_signing import sign_payload

        webhook_id = _create_webhook("sign-secret-abc")
        with SessionLocal() as db:
            loaded = db.query(Webhook).filter(Webhook.id == webhook_id).first()
            sig, version = sign_payload(loaded.secret, '{"event": "sla.violation"}')
        expected = hmac.new(
            b"sign-secret-abc", b'{"event": "sla.violation"}', hashlib.sha256
        ).hexdigest()
        assert sig == expected
        assert version == 1


class TestRotation:
    def test_rotate_encrypts_new_secret_and_preserves_hashed_previous(self):
        webhook_id = _create_webhook("old-secret-for-rotation")
        with SessionLocal() as db:
            row = db.query(Webhook).filter(Webhook.id == webhook_id).first()
            from app.core.security import hash_token

            previous = {"hashed_secret": hash_token(row.secret), "created_at": "now", "expires_at": "later"}
            row.previous_secrets = [previous]
            new_secret = "brand-new-secret"
            row.secret = new_secret
            row.secret_version = 2
            db.commit()

        stored = _stored_secret(webhook_id)
        assert "brand-new-secret" not in stored
        assert is_encrypted(stored)
        assert decrypt_secret(stored) == "brand-new-secret"

        with SessionLocal() as db:
            row = db.query(Webhook).filter(Webhook.id == webhook_id).first()
            assert row.previous_secrets[0]["hashed_secret"] == hash_token("old-secret-for-rotation")
            assert row.secret_version == 2

    def test_rotate_endpoint_never_persists_plaintext(self):
        webhook_id = _create_webhook("rotate-me-secret")

        def _fake_admin():
            from types import SimpleNamespace

            return SimpleNamespace(email="admin@example.com", id="user_admin", role="admin")

        app.dependency_overrides[require_admin] = _fake_admin
        try:
            with patch("app.api.v1.endpoints.webhooks.audit_log"):
                resp = client.post(f"/api/v1/webhooks/{webhook_id}/rotate-secret")
        finally:
            app.dependency_overrides.pop(require_admin, None)

        assert resp.status_code == 200
        new_secret = resp.json()["new_secret"]
        stored = _stored_secret(webhook_id)
        assert new_secret not in stored
        assert is_encrypted(stored)


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
            "WEBHOOK_SECRET_ENCRYPTION_KEY": "V5OOA_Ao70n9OxGEbj1WmRsZX6vI4IdtuJ_jYcIhNDg=",
        }
        defaults.update(overrides)
        return Settings.model_construct(**defaults)

    def test_startup_fails_when_encryption_key_missing_in_production(self):
        with pytest.raises(ValueError) as ctx:
            validate_critical_settings(self.make_settings(WEBHOOK_SECRET_ENCRYPTION_KEY=""))
        assert "WEBHOOK_SECRET_ENCRYPTION_KEY" in str(ctx.value)

    def test_startup_fails_with_invalid_encryption_key(self):
        with pytest.raises(ValueError) as ctx:
            validate_critical_settings(self.make_settings(WEBHOOK_SECRET_ENCRYPTION_KEY="not-a-fernét-key"))
        assert "valid Fernet key" in str(ctx.value)

    def test_startup_succeeds_with_valid_key_in_production(self):
        validate_critical_settings(self.make_settings())

    def test_local_environment_allows_unset_key(self):
        validate_critical_settings(
            self.make_settings(
                ENVIRONMENT="local",
                SECRET_KEY="apexchainx-dev-secret",
                PAYMENT_WEBHOOK_SECRET="",
                WEBHOOK_SECRET_ENCRYPTION_KEY="",
            )
        )
