"""Webhook secret lifecycle through both mutation paths — Issues #581, #582.

Two bugs, one root cause: the signing-secret lifecycle (version bump, hashed
history entry, grace window, audit event) lived inline in the rotate endpoint,
so any other path that touched ``webhook.secret`` silently skipped it.

#581: ``PATCH /webhooks/{id}`` overwrote the secret in place — no version
bump, no ``previous_secrets`` entry, no grace window, no audit event — so
signatures produced with the previous secret failed the moment the row was
written, even though docs/WEBHOOK_INTEGRATION.md documents PATCH-with-secret
as a supported rotation flow.

#582: rotation read the global ``WEBHOOK_SECRET_GRACE_HOURS`` setting and
ignored the per-webhook ``secret_grace_hours`` column added by migration
0018_webhook_secret_grace, so the documented per-webhook configuration had no
effect. The rotation path now honours the column, falling back to the global
setting when it is missing or out of range (1..MAX_WEBHOOK_SECRET_GRACE_HOURS).

The tests exercise the endpoint functions directly with fake sessions, so no
database is required (same pattern as tests/test_webhook_previous_secrets_pruning.py).
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.core.config import settings
from app.core.security import hash_token
from app.utils.secret_history import parse_expiry


NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)


def _rotate_call(webhook, new_secret="generated-by-endpoint"):
    """Invoke rotate_webhook_secret against a fake session, returning (webhook, audit, response).

    The module's ``datetime`` is patched to a fixed instant (NOW) so grace
    expiry timestamps are deterministic.
    """
    from app.api.v1.endpoints import webhooks as webhooks_module

    class _FakeQuery:
        def filter(self, *args, **kwargs):
            return self

        def first(self):
            return webhook

    class _FakeSession:
        def __init__(self):
            self.commits = 0

        def query(self, model):
            return _FakeQuery()

        def commit(self):
            self.commits += 1

    db = _FakeSession()
    audit_events = []

    with (
        patch.object(webhooks_module, "_get_live_webhook_or_409", return_value=webhook),
        patch.object(webhooks_module, "audit_log") as audit,
        patch.object(webhooks_module, "datetime") as fake_dt,
    ):
        fake_dt.now.return_value = NOW
        audit.log.side_effect = lambda event, payload: audit_events.append((event, payload))
        response = webhooks_module.rotate_webhook_secret(
            webhook_id=webhook.id,
            current_user=SimpleNamespace(email="admin@example.com"),
            db=db,
        )

    return webhook, audit_events, response


def _patch_call(webhook, payload):
    """Invoke update_webhook against fakes for the pieces PATCH touches.

    The module's ``datetime`` is patched to a fixed instant (NOW) so grace
    expiry timestamps are deterministic.
    """
    from app.api.v1.endpoints import webhooks as webhooks_module

    class _FakeSession:
        def __init__(self):
            self.commits = 0

        def commit(self):
            self.commits += 1

        def refresh(self, obj):
            pass

    db = _FakeSession()
    audit_events = []

    with (
        patch.object(webhooks_module, "_get_live_webhook_or_409", return_value=webhook),
        patch.object(webhooks_module, "audit_log") as audit,
        patch.object(webhooks_module, "datetime") as fake_dt,
        patch.object(webhooks_module.settings, "WEBHOOK_FANOUT_WARN_THRESHOLD", 0),
    ):
        fake_dt.now.return_value = NOW
        audit.log.side_effect = lambda event, payload: audit_events.append((event, payload))
        response = webhooks_module.update_webhook(
            webhook_id=webhook.id,
            payload=payload,
            current_user=SimpleNamespace(email="admin@example.com"),
            db=db,
        )

    return webhook, audit_events, response


def _webhook(secret="current-secret", secret_grace_hours=24, version=1, history=None):
    from app.models.webhook import Webhook

    return Webhook(
        id=uuid4(),
        name="grace-hook",
        url="https://example.com/hook",
        secret=secret,
        events='["sla.violation"]',
        secret_version=version,
        last_secret_rotation_at=None,
        previous_secrets=history or [],
        secret_grace_hours=secret_grace_hours,
        is_active=True,
    )


class TestPerWebhookGraceHoursHonoured:
    def test_rotation_uses_webhook_grace_not_global(self):
        webhook = _webhook(secret_grace_hours=7, history=[])

        webhook, audit, response = _rotate_call(webhook)

        entry = webhook.previous_secrets[-1]
        # datetime is patched to NOW in _rotate_call, so the expiry is
        # deterministic: NOW + 7 webhook-configured hours, not the 24h
        # global default.
        assert parse_expiry(entry) == NOW + timedelta(hours=7)
        assert response.message == "Secret rotated. Previous secret will remain valid for 7 hours."
        assert audit[-1][1]["grace_hours"] == 7
        assert audit[-1][1]["grace_source"] == "webhook"

    def test_out_of_range_webhook_grace_falls_back_to_global(self):
        webhook = _webhook(secret_grace_hours=0, history=[])

        webhook, audit, _response = _rotate_call(webhook)

        entry = webhook.previous_secrets[-1]
        assert parse_expiry(entry) == NOW + timedelta(hours=settings.WEBHOOK_SECRET_GRACE_HOURS)
        assert audit[-1][1]["grace_hours"] == settings.WEBHOOK_SECRET_GRACE_HOURS
        assert audit[-1][1]["grace_source"] == "global"

    def test_oversized_webhook_grace_falls_back_to_global(self):
        webhook = _webhook(secret_grace_hours=10_000, history=[])

        webhook, audit, _response = _rotate_call(webhook)

        assert audit[-1][1]["grace_source"] == "global"
        assert audit[-1][1]["grace_hours"] == settings.WEBHOOK_SECRET_GRACE_HOURS


class TestPatchSecretIsARotation:
    def test_patch_bumps_version_and_stores_hashed_previous(self):
        webhook = _webhook(secret="old-secret", version=1, secret_grace_hours=24)

        from app.api.v1.endpoints.webhooks import WebhookUpdate

        webhook, audit, _response = _patch_call(webhook, WebhookUpdate(secret="replacement-secret"))

        assert webhook.secret == "replacement-secret"
        assert webhook.secret_version == 2
        assert webhook.last_secret_rotation_at is not None
        assert len(webhook.previous_secrets) == 1
        assert webhook.previous_secrets[0]["hashed_secret"] == hash_token("old-secret")

    def test_patch_emits_the_rotation_audit_event(self):
        webhook = _webhook(secret_grace_hours=24)

        from app.api.v1.endpoints.webhooks import WebhookUpdate

        _webhook, audit, _response = _patch_call(webhook, WebhookUpdate(secret="next-secret"))

        event, payload = audit[-1]
        assert event == "webhook_secret_rotated"
        assert payload["rotation_source"] == "patch"
        assert payload["rotated_by"] == "admin@example.com"

    def test_patch_without_secret_does_not_rotate(self):
        webhook = _webhook(version=3)

        from app.api.v1.endpoints.webhooks import WebhookUpdate

        webhook, audit, _response = _patch_call(webhook, WebhookUpdate(name="renamed-hook"))

        assert webhook.secret_version == 3
        assert webhook.previous_secrets == []
        assert webhook.last_secret_rotation_at is None
        assert audit == []

    def test_patch_honours_the_webhook_grace_window(self):
        webhook = _webhook(secret_grace_hours=48)

        from app.api.v1.endpoints.webhooks import WebhookUpdate

        webhook, _audit, _response = _patch_call(webhook, WebhookUpdate(secret="next-secret"))

        assert parse_expiry(webhook.previous_secrets[-1]) == NOW + timedelta(hours=48)

    def test_patch_can_set_the_grace_window_itself(self):
        webhook = _webhook(secret_grace_hours=24)

        from app.api.v1.endpoints.webhooks import WebhookUpdate

        webhook, _audit, _response = _patch_call(
            webhook, WebhookUpdate(secret_grace_hours=72, secret="next-secret")
        )

        assert webhook.secret_grace_hours == 72
        # The rotation in the same request used the new window.
        assert parse_expiry(webhook.previous_secrets[-1]) == NOW + timedelta(hours=72)

    def test_patch_rejects_out_of_range_grace_windows(self):
        from app.api.v1.endpoints.webhooks import WebhookUpdate

        with pytest.raises(ValidationError):
            WebhookUpdate(secret_grace_hours=0)
        with pytest.raises(ValidationError):
            WebhookUpdate(secret_grace_hours=settings.MAX_WEBHOOK_SECRET_GRACE_HOURS + 1)
