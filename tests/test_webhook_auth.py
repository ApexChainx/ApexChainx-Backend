"""Tests for webhook delivery/retry/replay endpoint authentication — Issue #265.

Every webhook sub-resource (deliveries list, retry, dead-letter list/replay,
replay-by-context) must require an admin session, and replay/retry actions
must write audit entries that identify the acting admin.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.v1.endpoints.webhooks import require_admin
from app.db.session import get_db
from app.main import app
from app.models.webhook import WebhookDeliveryStatus, WebhookEvent

client = TestClient(app)

ADMIN_EMAIL = "webhook-admin@example.com"


@pytest.fixture
def admin_override():
    """Substitute a fixed admin identity for require_admin in this test."""

    def _fake_admin():
        return SimpleNamespace(email=ADMIN_EMAIL, id="user_admin", role="admin")

    app.dependency_overrides[require_admin] = _fake_admin
    yield
    app.dependency_overrides.pop(require_admin, None)


class TestWebhookSubresourcesRequireAuth:
    """The previously unauthenticated endpoints must return 401 without a session."""

    def test_list_deliveries_requires_auth(self):
        resp = client.get(f"/api/v1/webhooks/{uuid4()}/deliveries")
        assert resp.status_code == 401

    def test_retry_delivery_requires_auth(self):
        resp = client.post(f"/api/v1/webhooks/{uuid4()}/deliveries/{uuid4()}/retry")
        assert resp.status_code == 401

    def test_list_dead_letter_deliveries_requires_auth(self):
        resp = client.get(f"/api/v1/webhooks/{uuid4()}/dead-letter-deliveries")
        assert resp.status_code == 401

    def test_replay_dead_letter_delivery_requires_auth(self):
        resp = client.post(f"/api/v1/webhooks/{uuid4()}/deliveries/{uuid4()}/replay")
        assert resp.status_code == 401

    def test_replay_by_context_requires_auth(self):
        resp = client.post(
            "/api/v1/webhooks/replay-by-context",
            params={"event": "sla.violation"},
            json={},
        )
        assert resp.status_code == 401


def _fake_delivery(status: WebhookDeliveryStatus):
    """A fully-shaped delivery object with the fields _serialize_delivery reads."""
    now = datetime.now(UTC)
    return SimpleNamespace(
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
        created_at=now,
    )


def _override_db_with_delivery(delivery):
    """Yield a mocked session whose webhook/delivery lookups return *delivery*."""
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = delivery

    def _override_db():
        yield mock_db

    app.dependency_overrides[get_db] = _override_db


class TestRetryAuditsActor:
    def test_retry_delivery_audits_acting_admin(self, admin_override):
        webhook_id = uuid4()
        delivery_id = uuid4()
        delivery = _fake_delivery(WebhookDeliveryStatus.FAILED)
        delivery.id = delivery_id
        delivery.webhook_id = webhook_id
        _override_db_with_delivery(delivery)
        try:
            with patch("app.api.v1.endpoints.webhooks.audit_log") as mock_audit, patch(
                "app.services.webhook_service.dispatch_delivery"
            ) as mock_dispatch:
                resp = client.post(f"/api/v1/webhooks/{webhook_id}/deliveries/{delivery_id}/retry")
                assert resp.status_code == 200
                mock_dispatch.assert_called_once()
                mock_audit.log.assert_called_once()
                event_type, details = mock_audit.log.call_args[0]
                assert event_type == "webhook_delivery_retried"
                assert details["actor"] == ADMIN_EMAIL
                assert details["delivery_id"] == str(delivery_id)
        finally:
            app.dependency_overrides.pop(get_db, None)


class TestReplayAuditsActor:
    def test_replay_dead_letter_audits_acting_admin(self, admin_override):
        webhook_id = uuid4()
        delivery_id = uuid4()
        delivery = _fake_delivery(WebhookDeliveryStatus.DEAD_LETTER)
        delivery.id = delivery_id
        delivery.webhook_id = webhook_id
        _override_db_with_delivery(delivery)
        try:
            with patch("app.api.v1.endpoints.webhooks.audit_log") as mock_audit, patch(
                "app.services.webhook_service.replay_dead_letter_delivery", return_value=True
            ):
                resp = client.post(f"/api/v1/webhooks/{webhook_id}/deliveries/{delivery_id}/replay")
                assert resp.status_code == 200
                mock_audit.log.assert_called_once()
                event_type, details = mock_audit.log.call_args[0]
                assert event_type == "webhook_dead_letter_replayed"
                assert details["actor"] == ADMIN_EMAIL
        finally:
            app.dependency_overrides.pop(get_db, None)

    def test_replay_by_context_audits_acting_admin(self, admin_override):
        with patch("app.api.v1.endpoints.webhooks.audit_log") as mock_audit, patch(
            "app.services.webhook_service.replay_deliveries_by_event_context", return_value=3
        ):
            resp = client.post(
                "/api/v1/webhooks/replay-by-context",
                params={"event": "sla.violation"},
                json={"device_id": "dev-1", "limit": 10},
            )
            assert resp.status_code == 200
            assert resp.json()["replayed_count"] == 3
            mock_audit.log.assert_called_once()
            event_type, details = mock_audit.log.call_args[0]
            assert event_type == "webhook_replay_by_context"
            assert details["actor"] == ADMIN_EMAIL
            assert details["replayed_count"] == 3
