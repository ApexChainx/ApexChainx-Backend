"""Tests for #502: ``Webhook.previous_secrets`` is pruned on rotation.

Every rotation used to append to the JSONB ``previous_secrets`` array with no
removal of out-of-grace entries on the rotation path, so the column grew for
the lifetime of the webhook.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from app.utils.secret_history import is_within_grace, parse_expiry, prune_expired_secrets

NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def _entry(expires_at: datetime | None) -> dict:
    return {
        "hashed_secret": "deadbeef",
        "created_at": (expires_at - timedelta(hours=1)).isoformat(),
        "expires_at": expires_at.isoformat() if expires_at else "",
    }


class TestParseExpiry:
    def test_parses_isoformat_with_offset(self):
        assert parse_expiry(_entry(NOW)) == NOW

    def test_treats_naive_timestamp_as_utc(self):
        assert parse_expiry({"expires_at": "2026-01-01T12:00:00"}) == NOW

    def test_unparseable_returns_none(self):
        assert parse_expiry({"expires_at": "not-a-date"}) is None

    def test_missing_key_returns_none(self):
        assert parse_expiry({"hashed_secret": "x"}) is None

    def test_non_dict_entry_returns_none(self):
        assert parse_expiry("nope") is None


class TestIsWithinGrace:
    def test_future_expiry_is_in_grace(self):
        assert is_within_grace(_entry(NOW + timedelta(seconds=1)), NOW) is True

    def test_equal_expiry_is_expired(self):
        assert is_within_grace(_entry(NOW), NOW) is False

    def test_past_expiry_is_expired(self):
        assert is_within_grace(_entry(NOW - timedelta(hours=1)), NOW) is False

    def test_malformed_entry_is_kept_and_logged(self):
        # Conservative: a secret we cannot prove is out of grace must keep working.
        assert is_within_grace({"hashed_secret": "x"}, NOW) is True


class TestPruneExpiredSecrets:
    def test_empty_input(self):
        assert prune_expired_secrets([], NOW) == ([], 0)
        assert prune_expired_secrets(None, NOW) == ([], 0)

    def test_keeps_only_in_grace_entries(self):
        entries = [
            _entry(NOW - timedelta(hours=48)),  # long expired
            _entry(NOW + timedelta(hours=2)),  # still in grace
            _entry(NOW - timedelta(seconds=1)),  # just expired
        ]
        kept, dropped = prune_expired_secrets(entries, NOW)
        assert dropped == 2
        assert len(kept) == 1
        assert kept[0]["expires_at"] == (NOW + timedelta(hours=2)).isoformat()

    def test_does_not_mutate_the_input_list(self):
        entries = [_entry(NOW - timedelta(hours=1)), _entry(NOW + timedelta(hours=1))]
        prune_expired_secrets(entries, NOW)
        assert len(entries) == 2

    def test_repeated_rotations_stay_bounded(self):
        # Simulate 50 rotations an hour apart with a 24h grace window: at most
        # 25 entries can still be in grace at any point.
        entries: list[dict] = []
        total_dropped = 0
        for hour in range(50):
            rotation_time = NOW - timedelta(hours=50 - hour)
            expiry = rotation_time + timedelta(hours=24)
            kept, dropped = prune_expired_secrets(entries, rotation_time)
            total_dropped += dropped
            entries = kept + [_entry(expiry)]
        assert len(entries) <= 25
        assert total_dropped > 0


class TestRotatePrunesHistory:
    """The rotate endpoint must apply the same rule to the row it writes."""

    def test_prune_then_append_matches_rotate_ordering(self):
        history = [_entry(NOW - timedelta(hours=100)), _entry(NOW + timedelta(hours=1))]
        rotation_at = NOW
        kept, dropped = prune_expired_secrets(history, rotation_at)

        new_entry = _entry(rotation_at + timedelta(hours=24))
        result = kept + [new_entry]

        assert dropped == 1
        assert len(result) == 2
        assert all(datetime.fromisoformat(e["expires_at"]) > rotation_at for e in result)


class _FakeQuery:
    def __init__(self, row):
        self._row = row

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self._row


class _FakeSession:
    def __init__(self, row):
        self._row = row
        self.commits = 0

    def query(self, model):
        return _FakeQuery(self._row)

    def commit(self):
        self.commits += 1


class _FakeWebhook:
    def __init__(self, previous_secrets):
        self.id = uuid4()
        self.name = "test-hook"
        self.url = "https://example.com/hook"
        self.secret = "current-secret"
        self.secret_version = 3
        self.last_secret_rotation_at = NOW - timedelta(days=1)
        self.previous_secrets = previous_secrets


class _RecordingAuditLog:
    def __init__(self):
        self.events = []

    def log(self, event, payload):
        self.events.append((event, payload))


class TestRotateEndpointDropsExpiredSecrets:
    def _rotate(self, previous_secrets):
        from app.api.v1.endpoints import webhooks as webhooks_module

        webhook = _FakeWebhook(previous_secrets)
        db = _FakeSession(webhook)
        audit = _RecordingAuditLog()

        with patch.object(webhooks_module, "audit_log", audit):
            response = webhooks_module.rotate_webhook_secret(
                webhook_id=webhook.id,
                current_user=SimpleNamespace(email="admin@example.com"),
                db=db,
            )

        return webhook, audit, response, db

    def test_expired_history_is_dropped_and_new_entry_appended(self):
        history = [
            _entry(NOW - timedelta(days=3)),
            _entry(NOW - timedelta(seconds=1)),
        ]
        webhook, _audit, response, db = self._rotate(history)

        assert db.commits == 1
        assert response.new_secret
        # exactly one entry survives: the newly rotated secret
        assert len(webhook.previous_secrets) == 1
        assert webhook.secret_version == 4

    def test_in_grace_history_is_retained(self):
        # An entry expiring shortly after now is still usable by consumers, so
        # rotation must not drop it.
        history = [_entry(datetime.now(UTC) + timedelta(minutes=5))]
        webhook, _audit, _response, _db = self._rotate(history)

        assert len(webhook.previous_secrets) == 2

    def test_audit_records_the_pruned_count(self):
        history = [_entry(NOW - timedelta(days=3))]
        _webhook, audit, _response, _db = self._rotate(history)

        event, payload = audit.events[0]
        assert event == "webhook_secret_rotated"
        assert payload["pruned_previous_secrets"] == 1

    def test_prune_is_idempotent_across_repeated_rotations(self):
        history: list[dict] = []
        for _ in range(5):
            webhook, _audit, _response, _db = self._rotate(history)
            history = webhook.previous_secrets

        now = datetime.now(UTC)
        assert len(history) == 5
        assert all(datetime.fromisoformat(e["expires_at"]) > now for e in history)
