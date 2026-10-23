"""#569: webhook 4xx responses carry registered error codes.

Webhook routes used to raise bare ``HTTPException``s with only a ``detail``
string. Every webhook 4xx now carries a registered code from
docs/ERROR_CODES.md as an RFC 7807 ``error_code`` extension member, while the
``detail`` text stays byte-for-byte identical for existing consumers.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.v1.endpoints.webhooks import require_admin
from app.api.v1.endpoints.webhook_errors import WebhookHTTPException
from app.db.session import get_db
from app.main import app
from app.models.webhook import Webhook, WebhookDelivery, WebhookDeliveryStatus, WebhookEvent

client = TestClient(app)

ADMIN_EMAIL = "webhook-error-codes-admin@example.com"


@pytest.fixture
def admin_override():
    def _fake_admin():
        return SimpleNamespace(email=ADMIN_EMAIL, id="user_admin", role="admin")

    app.dependency_overrides[require_admin] = _fake_admin
    yield
    app.dependency_overrides.pop(require_admin, None)


def _webhook():
    return Webhook(
        id=uuid4(),
        name="outage-webhook",
        url="https://example.com/webhook",
        is_active=True,
        events='["sla.violation"]',
        max_retries=3,
        secret_version=1,
    )


def _delivery(status: WebhookDeliveryStatus):
    from datetime import UTC, datetime

    return WebhookDelivery(
        id=uuid4(),
        webhook_id=uuid4(),
        event=WebhookEvent.SLA_VIOLATION,
        status=status,
        attempt_count=1,
        response_status_code=None,
        error_message=None,
        delivered_at=None,
        dead_lettered_at=None,
        signature_version=1,
        created_at=datetime.now(UTC),
    )


def _override_db(first_object):
    """Session whose first query resolves *first_object*."""
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = first_object

    def _get_db():
        yield mock_db

    app.dependency_overrides[get_db] = _get_db
    return mock_db


class TestErrorCodesOnWebhook4xx:
    def test_unknown_webhook_is_404_with_registered_code(self, admin_override):
        _override_db(None)
        try:
            resp = client.get(f"/api/v1/webhooks/{uuid4()}")
            assert resp.status_code == 404
            assert resp.json()["error_code"] == "webhook_not_found"
        finally:
            app.dependency_overrides.pop(get_db, None)

    def test_unknown_delivery_is_404_with_registered_code(self, admin_override):
        webhook = _webhook()
        # First query (webhook) resolves, second query (delivery) does not.
        mock_db = MagicMock()
        results = [webhook, None]
        mock_db.query.return_value.filter.return_value.first.side_effect = lambda: results.pop(0)

        def _get_db():
            yield mock_db

        app.dependency_overrides[get_db] = _get_db
        try:
            resp = client.post(f"/api/v1/webhooks/{webhook.id}/deliveries/{uuid4()}/retry")
            assert resp.status_code == 404
            body = resp.json()
            assert body["error_code"] == "delivery_not_found"
            # Detail text unchanged for existing consumers.
            assert body["detail"] == "Delivery not found."
        finally:
            app.dependency_overrides.pop(get_db, None)

    def test_retry_of_successful_delivery_is_400_with_code(self, admin_override):
        webhook = _webhook()
        delivery = _delivery(WebhookDeliveryStatus.SUCCESS)
        delivery.webhook_id = webhook.id
        _override_db(delivery)
        try:
            with patch("app.services.webhook_service.dispatch_delivery"):
                resp = client.post(f"/api/v1/webhooks/{webhook.id}/deliveries/{delivery.id}/retry")
            assert resp.status_code == 400
            body = resp.json()
            assert body["error_code"] == "validation_error"
            assert body["detail"] == "Delivery already succeeded; retry not needed."
        finally:
            app.dependency_overrides.pop(get_db, None)

    def test_replay_failure_is_400_with_code(self, admin_override):
        webhook = _webhook()
        delivery = _delivery(WebhookDeliveryStatus.FAILED)
        delivery.webhook_id = webhook.id
        _override_db(delivery)
        try:
            with (
                patch("app.services.webhook_service.replay_dead_letter_delivery", return_value=False),
                patch("app.api.v1.endpoints.webhooks.audit_log"),
            ):
                resp = client.post(f"/api/v1/webhooks/{webhook.id}/deliveries/{delivery.id}/replay")
            assert resp.status_code == 400
            body = resp.json()
            assert body["error_code"] == "validation_error"
        finally:
            app.dependency_overrides.pop(get_db, None)

    def test_tombstone_mutation_is_409_with_code(self, admin_override):
        from datetime import UTC, datetime

        webhook = _webhook()
        webhook.deleted_at = datetime.now(UTC)
        webhook.is_active = False
        _override_db(webhook)
        try:
            resp = client.patch(f"/api/v1/webhooks/{webhook.id}", json={"name": "renamed"})
            assert resp.status_code == 409
            body = resp.json()
            assert body["error_code"] == "conflict"
            assert "deleted" in body["detail"]
        finally:
            app.dependency_overrides.pop(get_db, None)

    def test_registration_cap_is_409_with_code(self, admin_override):
        from app.core.config import settings

        mock_db = MagicMock()
        mock_db.query.return_value.scalar.return_value = settings.MAX_WEBHOOKS_PER_ACCOUNT or 1

        def _get_db():
            yield mock_db

        app.dependency_overrides[get_db] = _get_db
        try:
            with patch("app.api.v1.endpoints.webhooks.validate_webhook_url", return_value=["93.184.216.34"]):
                resp = client.post(
                    "/api/v1/webhooks",
                    json={"name": "w", "url": "https://example.com/webhook", "events": ["sla.violation"]},
                )
            assert resp.status_code == 409
            assert resp.json()["error_code"] == "conflict"
        finally:
            app.dependency_overrides.pop(get_db, None)


class TestResponseShapeUnchanged:
    def test_responses_still_use_the_problem_detail_envelope(self, admin_override):
        _override_db(None)
        try:
            resp = client.get(f"/api/v1/webhooks/{uuid4()}")
            body = resp.json()
            for key in ("title", "status", "detail", "correlation_id"):
                assert key in body
        finally:
            app.dependency_overrides.pop(get_db, None)


class TestCodesAreRegistered:
    def test_all_emitted_codes_are_in_the_error_code_doc(self):
        from pathlib import Path

        doc = (Path(__file__).resolve().parent.parent / "docs" / "ERROR_CODES.md").read_text()
        for code in ("webhook_not_found", "delivery_not_found", "conflict", "validation_error"):
            assert f"`{code}`" in doc

    def test_webhook_exception_carries_its_code(self):
        exc = WebhookHTTPException(status_code=404, detail="x", error_code="webhook_not_found")
        assert exc.error_code == "webhook_not_found"
        assert exc.status_code == 404
        assert exc.detail == "x"
