"""Tests for standardized webhook error codes — Issue #569.

Webhook routes used to emit bare ``detail`` strings; every 4xx now carries a
stable ``error_code`` registered in docs/ERROR_CODES.md (surfaced through the
RFC 7807 problem body), and SSRF/URL validation failures map to
``invalid_webhook_url`` instead of escaping as unhandled 500s.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.v1.endpoints.webhooks import WebhookHTTPException, require_admin
from app.db.session import get_db
from app.main import app
from app.models.webhook import Webhook, WebhookDeliveryStatus

client = TestClient(app)

VALID_PAYLOAD = {
    "name": "outage-webhook",
    "url": "https://example.com/webhook",
    "events": ["sla.violation"],
}


@pytest.fixture
def admin_override():
    def _fake_admin():
        return SimpleNamespace(email="codes-admin@example.com", id="user_admin", role="admin")

    app.dependency_overrides[require_admin] = _fake_admin
    yield
    app.dependency_overrides.pop(require_admin, None)


def _override_db(mock_db):
    def _get_db():
        yield mock_db

    app.dependency_overrides[get_db] = _get_db


class TestErrorCodesAreRegistered:
    def test_every_webhook_code_is_in_the_registry(self):
        doc = open("docs/ERROR_CODES.md").read()
        for code in (
            "webhook_not_found",
            "webhook_deleted_conflict",
            "webhook_limit_reached",
            "invalid_webhook_url",
            "delivery_not_found",
            "delivery_not_retryable",
            "delivery_in_progress",
            "delivery_not_replayable",
        ):
            assert f"`{code}`" in doc, f"{code} missing from docs/ERROR_CODES.md"

    def test_webhook_exception_carries_error_code(self):
        exc = WebhookHTTPException(404, "Webhook not found.", error_code="webhook_not_found")
        assert exc.error_code == "webhook_not_found"
        assert exc.status_code == 404


class TestNotFoundCarriesCode:
    def test_missing_webhook_returns_webhook_not_found(self, admin_override):
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None
        _override_db(mock_db)
        try:
            resp = client.get(f"/api/v1/webhooks/{uuid4()}")
            assert resp.status_code == 404
            body = resp.json()
            assert body["error_code"] == "webhook_not_found"
        finally:
            app.dependency_overrides.pop(get_db, None)

    def test_missing_delivery_returns_delivery_not_found(self, admin_override):
        # The webhook exists (first query) but the delivery does not (second);
        # a fully-empty session would correctly yield ``webhook_not_found``
        # because the parent lookup runs first.
        webhook = Webhook(
            id=uuid4(),
            name="hook",
            url="https://example.com/hook",
            is_active=True,
            events='["sla.violation"]',
            max_retries=3,
            secret_version=1,
        )
        mock_db = MagicMock()

        def _query(entity):
            query = MagicMock()
            if entity is Webhook:
                query.filter.return_value.first.return_value = webhook
            else:
                query.filter.return_value.first.return_value = None
            return query

        mock_db.query.side_effect = _query
        _override_db(mock_db)
        try:
            resp = client.post(f"/api/v1/webhooks/{webhook.id}/deliveries/{uuid4()}/retry")
            assert resp.status_code == 404
            assert resp.json()["error_code"] == "delivery_not_found"
        finally:
            app.dependency_overrides.pop(get_db, None)


class TestTombstoneConflictCarriesCode:
    def test_deleted_webhook_patch_returns_webhook_deleted_conflict(self, admin_override):
        tombstone = Webhook(
            id=uuid4(),
            name="gone",
            url="https://example.com/hook",
            is_active=False,
            events='["sla.violation"]',
            max_retries=3,
            secret_version=1,
            deleted_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
        )
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = tombstone
        _override_db(mock_db)
        try:
            resp = client.patch(f"/api/v1/webhooks/{tombstone.id}", json={"name": "new-name"})
            assert resp.status_code == 409
            body = resp.json()
            assert body["error_code"] == "webhook_deleted_conflict"
            # Existing tests also branch on the human-readable detail.
            assert "deleted" in body["detail"]
        finally:
            app.dependency_overrides.pop(get_db, None)


class TestRegistrationCapCarriesCode:
    def test_cap_rejection_returns_webhook_limit_reached(self, admin_override, monkeypatch):
        from app.core.config import settings

        monkeypatch.setattr(settings, "MAX_WEBHOOKS_PER_ACCOUNT", 2)
        mock_db = MagicMock()
        mock_db.query.return_value.scalar.return_value = 2
        _override_db(mock_db)
        try:
            with patch("app.api.v1.endpoints.webhooks.validate_webhook_url", return_value=["93.184.216.34"]):
                resp = client.post("/api/v1/webhooks", json=VALID_PAYLOAD)
            assert resp.status_code == 409
            body = resp.json()
            assert body["error_code"] == "webhook_limit_reached"
            assert "MAX_WEBHOOKS_PER_ACCOUNT" in body["detail"]
        finally:
            app.dependency_overrides.pop(get_db, None)


class TestSSRFMapsTo400:
    def test_ssrf_failure_is_invalid_webhook_url_not_500(self, admin_override):
        from app.utils.network_validation import NetworkValidationError

        mock_db = MagicMock()
        mock_db.query.return_value.scalar.return_value = 0
        _override_db(mock_db)
        try:
            with patch(
                "app.api.v1.endpoints.webhooks.validate_webhook_url",
                side_effect=NetworkValidationError("Loopback addresses are not allowed."),
            ):
                resp = client.post("/api/v1/webhooks", json=VALID_PAYLOAD)
            assert resp.status_code == 400
            body = resp.json()
            assert body["error_code"] == "invalid_webhook_url"
            assert "Loopback" in body["detail"]
        finally:
            app.dependency_overrides.pop(get_db, None)

    def test_url_update_ssrf_failure_is_invalid_webhook_url(self, admin_override):
        from app.utils.network_validation import NetworkValidationError

        webhook = Webhook(
            id=uuid4(),
            name="existing",
            url="https://example.com/hook",
            is_active=True,
            events='["sla.violation"]',
            max_retries=3,
            secret_version=1,
        )
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = webhook
        _override_db(mock_db)
        try:
            with patch(
                "app.api.v1.endpoints.webhooks.validate_webhook_url",
                side_effect=NetworkValidationError("Private network addresses are not allowed."),
            ):
                resp = client.patch(f"/api/v1/webhooks/{webhook.id}", json={"url": "https://10.0.0.1/hook"})
            assert resp.status_code == 400
            assert resp.json()["error_code"] == "invalid_webhook_url"
        finally:
            app.dependency_overrides.pop(get_db, None)


class TestDeliveryStateGuards:
    @staticmethod
    def _delivery(status):
        return SimpleNamespace(
            id=uuid4(),
            webhook_id=uuid4(),
            status=status,
        )

    def test_retry_of_successful_delivery_is_not_retryable(self, admin_override):
        delivery = self._delivery(WebhookDeliveryStatus.SUCCESS)
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = delivery
        _override_db(mock_db)
        try:
            resp = client.post(f"/api/v1/webhooks/{uuid4()}/deliveries/{delivery.id}/retry")
            assert resp.status_code == 400
            assert resp.json()["error_code"] == "delivery_not_retryable"
        finally:
            app.dependency_overrides.pop(get_db, None)

    def test_retry_of_sending_delivery_is_in_progress(self, admin_override):
        delivery = self._delivery(WebhookDeliveryStatus.SENDING)
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = delivery
        _override_db(mock_db)
        try:
            resp = client.post(f"/api/v1/webhooks/{uuid4()}/deliveries/{delivery.id}/retry")
            assert resp.status_code == 409
            assert resp.json()["error_code"] == "delivery_in_progress"
        finally:
            app.dependency_overrides.pop(get_db, None)

    def test_replay_of_non_dead_letter_is_not_replayable(self, admin_override):
        delivery = self._delivery(WebhookDeliveryStatus.SUCCESS)
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = delivery
        _override_db(mock_db)
        try:
            resp = client.post(f"/api/v1/webhooks/{uuid4()}/deliveries/{delivery.id}/replay")
            assert resp.status_code == 400
            assert resp.json()["error_code"] == "delivery_not_replayable"
        finally:
            app.dependency_overrides.pop(get_db, None)


class TestLintPasses:
    def test_error_code_lint_exits_zero(self):
        import subprocess
        import sys

        result = subprocess.run([sys.executable, "scripts/lint_error_codes.py"], capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr
