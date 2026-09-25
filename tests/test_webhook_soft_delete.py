"""Tests for #518: deleting a webhook retains its delivery history.

``DELETE /api/v1/webhooks/{id}`` used to remove the row, and the ``deliveries``
relationship cascades, so the record of what was delivered and what the consumer
answered was destroyed along with the registration. Delete is now a soft delete.
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
from app.models.webhook import Webhook, WebhookDeliveryStatus

client = TestClient(app)

ADMIN_EMAIL = "webhook-soft-delete-admin@example.com"


@pytest.fixture
def admin_override():
    def _fake_admin():
        return SimpleNamespace(email=ADMIN_EMAIL, id="user_admin", role="admin")

    app.dependency_overrides[require_admin] = _fake_admin
    yield
    app.dependency_overrides.pop(require_admin, None)


def _webhook(deleted_at=None, is_active=True):
    return Webhook(
        id=uuid4(),
        name="outage-webhook",
        url="https://example.com/webhook",
        is_active=is_active,
        events='["sla.violation"]',
        max_retries=3,
        secret_version=1,
        deleted_at=deleted_at,
    )


def _session(webhook):
    """Session that resolves *webhook* by id and returns an empty delivery page."""
    mock_db = MagicMock()

    def _query(entity):
        query = MagicMock()
        if entity is Webhook:
            query.filter.return_value.first.return_value = webhook
        else:
            # list_webhook_deliveries pages with a window-count column.
            paged = query.filter.return_value.add_columns.return_value
            paged.order_by.return_value.offset.return_value.limit.return_value.all.return_value = []
            paged.order_by.return_value.count.return_value = 0
        return query

    mock_db.query.side_effect = _query
    return mock_db


def _override(mock_db):
    def _get_db():
        yield mock_db

    app.dependency_overrides[get_db] = _get_db


class TestDeleteRetainsRow:
    def test_delete_never_calls_db_delete(self, admin_override):
        webhook = _webhook()
        mock_db = _session(webhook)
        _override(mock_db)
        try:
            resp = client.delete(f"/api/v1/webhooks/{webhook.id}")
            assert resp.status_code == 204
            mock_db.delete.assert_not_called()
        finally:
            app.dependency_overrides.pop(get_db, None)

    def test_delete_stamps_deleted_at_and_clears_is_active(self, admin_override):
        webhook = _webhook()
        mock_db = _session(webhook)
        _override(mock_db)
        try:
            with patch("app.api.v1.endpoints.webhooks.audit_log"):
                resp = client.delete(f"/api/v1/webhooks/{webhook.id}")
            assert resp.status_code == 204
            assert webhook.is_deleted is True
            assert isinstance(webhook.deleted_at, datetime)
            assert webhook.deleted_at.tzinfo is not None
            assert webhook.is_active is False
            mock_db.commit.assert_called_once()
        finally:
            app.dependency_overrides.pop(get_db, None)

    def test_deliveries_are_not_deleted_with_the_registration(self, admin_override):
        # A real `db.delete(webhook)` would cascade through the `deliveries`
        # relationship and take the history with it. Nothing is deleted, so the
        # delivery rows stay queryable against the same session afterwards.
        webhook = _webhook()
        mock_db = _session(webhook)
        _override(mock_db)
        try:
            with patch("app.api.v1.endpoints.webhooks.audit_log"):
                assert client.delete(f"/api/v1/webhooks/{webhook.id}").status_code == 204
            assert mock_db.delete.call_count == 0
            assert mock_db.execute.call_count == 0
        finally:
            app.dependency_overrides.pop(get_db, None)

    def test_delete_is_idempotent(self, admin_override):
        already = _webhook(deleted_at=datetime.now(UTC), is_active=False)
        mock_db = _session(already)
        _override(mock_db)
        try:
            with patch("app.api.v1.endpoints.webhooks.audit_log") as mock_audit:
                resp = client.delete(f"/api/v1/webhooks/{already.id}")
            assert resp.status_code == 204
            # The original tombstone timestamp is preserved, not restamped.
            mock_audit.log.assert_not_called()
            mock_db.commit.assert_not_called()
        finally:
            app.dependency_overrides.pop(get_db, None)

    def test_delete_audits_the_actor(self, admin_override):
        webhook = _webhook()
        _override(_session(webhook))
        try:
            with patch("app.api.v1.endpoints.webhooks.audit_log") as mock_audit:
                client.delete(f"/api/v1/webhooks/{webhook.id}")
            event_type, details = mock_audit.log.call_args[0]
            assert event_type == "webhook_deleted"
            assert details["webhook_id"] == str(webhook.id)
            assert details["deleted_by"] == ADMIN_EMAIL
        finally:
            app.dependency_overrides.pop(get_db, None)

    def test_delete_of_unknown_webhook_is_404(self, admin_override):
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None
        _override(mock_db)
        try:
            assert client.delete(f"/api/v1/webhooks/{uuid4()}").status_code == 404
        finally:
            app.dependency_overrides.pop(get_db, None)


class TestTombstonesStayReadable:
    def test_get_returns_the_tombstone(self, admin_override):
        deleted_at = datetime.now(UTC)
        webhook = _webhook(deleted_at=deleted_at, is_active=False)
        _override(_session(webhook))
        try:
            resp = client.get(f"/api/v1/webhooks/{webhook.id}")
            assert resp.status_code == 200
            body = resp.json()
            assert body["deleted_at"] == deleted_at.isoformat()
            assert body["is_active"] is False
        finally:
            app.dependency_overrides.pop(get_db, None)

    def test_live_webhook_reports_no_deleted_at(self, admin_override):
        webhook = _webhook()
        _override(_session(webhook))
        try:
            resp = client.get(f"/api/v1/webhooks/{webhook.id}")
            assert resp.status_code == 200
            assert resp.json()["deleted_at"] is None
        finally:
            app.dependency_overrides.pop(get_db, None)

    def test_deliveries_of_deleted_webhook_remain_readable(self, admin_override):
        # The delivery-history endpoint still resolves a tombstone, so an
        # operator can answer "did we notify them?" after the endpoint is gone.
        webhook = _webhook(deleted_at=datetime.now(UTC), is_active=False)
        _override(_session(webhook))
        try:
            resp = client.get(f"/api/v1/webhooks/{webhook.id}/deliveries")
            assert resp.status_code == 200
        finally:
            app.dependency_overrides.pop(get_db, None)


class TestListHidesTombstones:
    def _list_session(self):
        mock_db = MagicMock()
        mock_db.query.return_value.offset.return_value.limit.return_value.all.return_value = []
        return mock_db

    def _applied_filters(self, mock_db) -> str:
        return " ".join(str(call) for call in mock_db.query.return_value.filter.call_args_list)

    def test_list_excludes_deleted_by_default(self, admin_override):
        mock_db = self._list_session()
        _override(mock_db)
        try:
            assert client.get("/api/v1/webhooks").status_code == 200
            assert "deleted_at" in self._applied_filters(mock_db)
        finally:
            app.dependency_overrides.pop(get_db, None)

    def test_include_deleted_reveals_tombstones(self, admin_override):
        mock_db = self._list_session()
        _override(mock_db)
        try:
            assert client.get("/api/v1/webhooks", params={"include_deleted": "true"}).status_code == 200
            assert "deleted_at" not in self._applied_filters(mock_db)
        finally:
            app.dependency_overrides.pop(get_db, None)


class TestTombstonesAreNotMutable:
    @pytest.mark.parametrize(
        "method,path,body",
        [
            ("patch", "", {"name": "renamed"}),
            ("post", "/rotate-secret", {}),
        ],
    )
    def test_mutations_are_refused_with_409(self, admin_override, method, path, body):
        webhook = _webhook(deleted_at=datetime.now(UTC), is_active=False)
        _override(_session(webhook))
        try:
            resp = getattr(client, method)(f"/api/v1/webhooks/{webhook.id}{path}", json=body)
            assert resp.status_code == 409
            assert "deleted" in resp.json()["detail"]
        finally:
            app.dependency_overrides.pop(get_db, None)

    def test_live_webhook_is_still_patchable(self, admin_override):
        webhook = _webhook()
        _override(_session(webhook))
        try:
            resp = client.patch(f"/api/v1/webhooks/{webhook.id}", json={"name": "renamed"})
            assert resp.status_code == 200
            assert webhook.name == "renamed"
        finally:
            app.dependency_overrides.pop(get_db, None)


class TestDispatcherIgnoresTombstones:
    def test_dispatch_is_skipped_for_deleted_webhook(self):
        from app.services.webhook_service import dispatch_delivery

        webhook = _webhook(deleted_at=datetime.now(UTC), is_active=False)
        delivery = SimpleNamespace(
            id=uuid4(),
            webhook=webhook,
            status=WebhookDeliveryStatus.RETRYING,
            attempt_count=1,
        )
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = delivery
        with patch("app.services.webhook_service._attempt_delivery") as mock_attempt:
            dispatch_delivery(mock_db, delivery.id)
        # No outbound request, and the delivery is left as-is for audit.
        mock_attempt.assert_not_called()
        assert delivery.attempt_count == 1
        mock_db.commit.assert_not_called()

    def test_dispatch_is_skipped_for_inactive_webhook(self):
        from app.services.webhook_service import dispatch_delivery

        webhook = _webhook(is_active=False)
        delivery = SimpleNamespace(
            id=uuid4(),
            webhook=webhook,
            status=WebhookDeliveryStatus.RETRYING,
            attempt_count=1,
        )
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = delivery
        with patch("app.services.webhook_service._attempt_delivery") as mock_attempt:
            dispatch_delivery(mock_db, delivery.id)
        mock_attempt.assert_not_called()
