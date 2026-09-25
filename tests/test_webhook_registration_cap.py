"""Tests for #517: webhook registration cap and fan-out warning.

Registration was unbounded, so one admin session could register enough webhooks
to multiply every emitted event by an arbitrary number of outbound HTTPS
requests, and each registration adds a Fernet-encrypted secret to rotate. This
covers the cap (409 with the cap in the message) and the fan-out warning.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.v1.endpoints.webhooks import require_admin
from app.core.config import settings
from app.db.session import get_db
from app.main import app
from app.models.webhook import Webhook

client = TestClient(app)

ADMIN_EMAIL = "webhook-cap-admin@example.com"
CREATE_URL = "/api/v1/webhooks"
VALID_PAYLOAD = {
    "name": "outage-webhook",
    "url": "https://example.com/webhook",
    "events": ["sla.violation"],
}


@pytest.fixture
def admin_override():
    def _fake_admin():
        return SimpleNamespace(email=ADMIN_EMAIL, id="user_admin", role="admin")

    app.dependency_overrides[require_admin] = _fake_admin
    yield
    app.dependency_overrides.pop(require_admin, None)


def _fake_session(registered=0, event_rows=(), webhook=None):
    """A session that answers the three queries the limits rely on.

    ``func.count(Webhook.id)`` -> the registered count, ``Webhook.events`` -> the
    raw stored rows, ``Webhook`` -> a single row for PATCH.
    """
    mock_db = MagicMock()

    def _query(entity):
        query = MagicMock()
        if entity is Webhook.events:
            query.all.return_value = [(row,) for row in event_rows]
        elif entity is Webhook:
            query.filter.return_value.first.return_value = webhook
        else:
            query.scalar.return_value = registered
        return query

    def _add(obj):
        # Emulate the flush that would assign the primary key and column
        # defaults, which never happens against a mocked session.
        if getattr(obj, "id", None) is None:
            obj.id = uuid4()
        if getattr(obj, "secret_version", None) is None:
            obj.secret_version = 1

    mock_db.query.side_effect = _query
    mock_db.add.side_effect = _add
    return mock_db


def _override_db(mock_db):
    def _get_db():
        yield mock_db

    app.dependency_overrides[get_db] = _get_db


def _existing_webhook(events):
    return Webhook(
        id=uuid4(),
        name="existing",
        url="https://example.com/hook",
        is_active=True,
        events=json.dumps(events),
        max_retries=3,
        secret_version=2,
    )


def _post():
    with patch("app.api.v1.endpoints.webhooks.validate_webhook_url", return_value=["93.184.216.34"]):
        return client.post(CREATE_URL, json=VALID_PAYLOAD)


class TestRegistrationCap:
    def test_create_beyond_cap_returns_409(self, admin_override, monkeypatch):
        monkeypatch.setattr(settings, "MAX_WEBHOOKS_PER_ACCOUNT", 2)
        _override_db(_fake_session(registered=2))
        try:
            resp = _post()
            assert resp.status_code == 409
        finally:
            app.dependency_overrides.pop(get_db, None)

    def test_conflict_message_carries_the_cap(self, admin_override, monkeypatch):
        monkeypatch.setattr(settings, "MAX_WEBHOOKS_PER_ACCOUNT", 2)
        _override_db(_fake_session(registered=7))
        try:
            resp = _post()
            assert resp.status_code == 409
            detail = resp.json()["detail"]
            assert "MAX_WEBHOOKS_PER_ACCOUNT" in detail
            assert "2" in detail
        finally:
            app.dependency_overrides.pop(get_db, None)

    def test_registration_below_cap_is_allowed(self, admin_override, monkeypatch):
        monkeypatch.setattr(settings, "MAX_WEBHOOKS_PER_ACCOUNT", 2)
        _override_db(_fake_session(registered=1))
        try:
            resp = _post()
            assert resp.status_code == 201
        finally:
            app.dependency_overrides.pop(get_db, None)

    def test_cap_is_not_off_by_one(self, admin_override, monkeypatch):
        # registered == cap - 1 must still be allowed: the cap is the largest
        # number of webhooks that may exist, not the largest number of creates.
        monkeypatch.setattr(settings, "MAX_WEBHOOKS_PER_ACCOUNT", 3)
        _override_db(_fake_session(registered=2))
        try:
            resp = _post()
            assert resp.status_code == 201
        finally:
            app.dependency_overrides.pop(get_db, None)

    def test_cap_zero_disables_the_check(self, admin_override, monkeypatch):
        monkeypatch.setattr(settings, "MAX_WEBHOOKS_PER_ACCOUNT", 0)
        _override_db(_fake_session(registered=5000))
        try:
            resp = _post()
            assert resp.status_code == 201
        finally:
            app.dependency_overrides.pop(get_db, None)

    def test_rejected_registration_does_not_resolve_dns(self, admin_override, monkeypatch):
        # The cap is checked before the SSRF lookup so a request that is going to
        # be refused does not cost a DNS resolution.
        monkeypatch.setattr(settings, "MAX_WEBHOOKS_PER_ACCOUNT", 1)
        _override_db(_fake_session(registered=1))
        try:
            with patch("app.api.v1.endpoints.webhooks.validate_webhook_url") as mock_validate:
                resp = _post()
            assert resp.status_code == 409
            mock_validate.assert_not_called()
        finally:
            app.dependency_overrides.pop(get_db, None)


class TestFanoutWarning:
    def test_fanout_over_threshold_warns_and_counts(self, admin_override, monkeypatch):
        monkeypatch.setattr(settings, "WEBHOOK_FANOUT_WARN_THRESHOLD", 3)
        _override_db(
            _fake_session(registered=1, event_rows=[json.dumps(["a", "b"]), json.dumps(["c", "d"])])
        )
        try:
            with (
                patch("app.api.v1.endpoints.webhooks.logger") as mock_logger,
                patch("app.api.v1.endpoints.webhooks.increment_counter") as mock_counter,
                patch("app.api.v1.endpoints.webhooks.set_gauge") as mock_gauge,
            ):
                resp = _post()
            assert resp.status_code == 201
            mock_logger.warning.assert_called_once()
            assert mock_counter.call_args[0][0] == "webhook.fanout.threshold_exceeded"
            mock_gauge.assert_called_once_with("webhook.fanout.subscriptions", 4.0)
        finally:
            app.dependency_overrides.pop(get_db, None)

    def test_fanout_at_threshold_does_not_warn(self, admin_override, monkeypatch):
        monkeypatch.setattr(settings, "WEBHOOK_FANOUT_WARN_THRESHOLD", 4)
        _override_db(
            _fake_session(registered=1, event_rows=[json.dumps(["a", "b"]), json.dumps(["c", "d"])])
        )
        try:
            with patch("app.api.v1.endpoints.webhooks.increment_counter") as mock_counter:
                resp = _post()
            assert resp.status_code == 201
            mock_counter.assert_not_called()
        finally:
            app.dependency_overrides.pop(get_db, None)

    def test_threshold_zero_disables_the_warning(self, admin_override, monkeypatch):
        monkeypatch.setattr(settings, "WEBHOOK_FANOUT_WARN_THRESHOLD", 0)
        _override_db(_fake_session(registered=1, event_rows=[json.dumps(["a"] * 500)]))
        try:
            with patch("app.api.v1.endpoints.webhooks.increment_counter") as mock_counter:
                resp = _post()
            assert resp.status_code == 201
            mock_counter.assert_not_called()
        finally:
            app.dependency_overrides.pop(get_db, None)

    def test_corrupt_events_row_is_skipped_not_fatal(self, admin_override, monkeypatch):
        # A row with unparseable events must not break counting, and must not
        # count towards the total either.
        monkeypatch.setattr(settings, "WEBHOOK_FANOUT_WARN_THRESHOLD", 2)
        _override_db(
            _fake_session(
                registered=2,
                event_rows=["{not json", json.dumps(["a", "b", "c"]), None],
            )
        )
        try:
            with (
                patch("app.api.v1.endpoints.webhooks.logger"),
                patch("app.api.v1.endpoints.webhooks.set_gauge") as mock_gauge,
            ):
                resp = _post()
            assert resp.status_code == 201
            mock_gauge.assert_called_once_with("webhook.fanout.subscriptions", 3.0)
        finally:
            app.dependency_overrides.pop(get_db, None)


class TestFanoutWarningOnSubscriptionUpdate:
    def test_subscribing_more_events_triggers_the_check(self, admin_override, monkeypatch):
        monkeypatch.setattr(settings, "WEBHOOK_FANOUT_WARN_THRESHOLD", 3)
        webhook = _existing_webhook(["sla.violation"])
        _override_db(_fake_session(registered=1, event_rows=[json.dumps(["a", "b", "c", "d"])], webhook=webhook))
        try:
            with patch("app.api.v1.endpoints.webhooks.increment_counter") as mock_counter:
                resp = client.patch(
                    f"{CREATE_URL}/{webhook.id}",
                    json={"events": ["sla.violation", "sla.resolved"]},
                )
            assert resp.status_code == 200
            mock_counter.assert_called_once()
        finally:
            app.dependency_overrides.pop(get_db, None)

    def test_unrelated_update_does_not_trigger_the_check(self, admin_override, monkeypatch):
        monkeypatch.setattr(settings, "WEBHOOK_FANOUT_WARN_THRESHOLD", 3)
        webhook = _existing_webhook(["sla.violation"])
        _override_db(_fake_session(registered=1, event_rows=[json.dumps(["a", "b", "c", "d"])], webhook=webhook))
        try:
            with patch("app.api.v1.endpoints.webhooks.increment_counter") as mock_counter:
                resp = client.patch(f"{CREATE_URL}/{webhook.id}", json={"name": "renamed"})
            assert resp.status_code == 200
            mock_counter.assert_not_called()
        finally:
            app.dependency_overrides.pop(get_db, None)
