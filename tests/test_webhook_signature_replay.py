"""Webhook signatures must bind the delivery time, not just the bytes (#538).

Version 1 signs the payload alone, so a captured delivery replays byte-for-byte
and a consumer cannot tell it from a fresh one — it double-processes events like
`sla.violation` or an outage notification. Version 2 signs
`{X-Webhook-Timestamp}.{payload}`, which makes a replay at a different time fail
verification, and adds the skew window that turns "old" into "rejected".
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.services.webhook_service import _build_headers
from app.services.webhook_signing import (
    DEFAULT_MAX_SKEW_SECONDS,
    is_timestamp_fresh,
    sign_payload,
    sign_payload_v1,
    sign_payload_v2,
    signing_input_v2,
    verify_delivery_signature,
    verify_signature,
    verify_signature_v2,
)

SECRET = "whsec_replay_test"
PAYLOAD = '{"event":"sla.violation","delivery":"d-1"}'


def _now_iso(offset_seconds: float = 0.0) -> str:
    return (datetime.now(UTC) + timedelta(seconds=offset_seconds)).isoformat()


class _FakeWebhook:
    """Minimal stand-in for the ORM row `_build_headers` needs."""

    def __init__(self, secret: str | None = SECRET) -> None:
        self.secret = secret
        self.id = 1


class TestSigningInput:
    def test_v2_signs_timestamp_and_payload(self) -> None:
        ts = _now_iso()

        assert signing_input_v2(ts, PAYLOAD) == f"{ts}.{PAYLOAD}"

    def test_v2_is_not_the_v1_digest(self) -> None:
        ts = _now_iso()

        assert sign_payload_v2(SECRET, PAYLOAD, ts) != sign_payload_v1(SECRET, PAYLOAD)

    def test_v1_still_signs_payload_only(self) -> None:
        sig, version, timestamp = sign_payload(SECRET, PAYLOAD, version=1)

        assert sig == sign_payload_v1(SECRET, PAYLOAD)
        assert version == 1
        assert timestamp == ""

    def test_unsupported_version_raises(self) -> None:
        with pytest.raises(ValueError):
            sign_payload(SECRET, PAYLOAD, version=99)


class TestReplayIsDetectable:
    def test_replayed_bytes_under_a_new_timestamp_fail(self) -> None:
        """The core of #538: re-stamping a captured delivery breaks the signature."""
        captured_ts = _now_iso()
        signature = sign_payload_v2(SECRET, PAYLOAD, captured_ts)

        later_ts = _now_iso(offset_seconds=60)
        assert verify_signature_v2(SECRET, PAYLOAD, signature, later_ts) is False

    def test_edited_payload_fails(self) -> None:
        ts = _now_iso()
        signature = sign_payload_v2(SECRET, PAYLOAD, ts)

        assert verify_signature_v2(SECRET, PAYLOAD, signature, PAYLOAD.replace("d-1", "d-2")) is False

    def test_untouched_delivery_verifies(self) -> None:
        ts = _now_iso()
        signature = sign_payload_v2(SECRET, PAYLOAD, ts)

        assert verify_signature_v2(SECRET, PAYLOAD, signature, ts) is True

    def test_wrong_secret_fails(self) -> None:
        ts = _now_iso()
        signature = sign_payload_v2(SECRET, PAYLOAD, ts)

        assert verify_signature(SECRET + "x", PAYLOAD, signature, version=2, timestamp=ts) is False

    def test_v2_without_a_timestamp_fails_closed(self) -> None:
        ts = _now_iso()
        signature = sign_payload_v2(SECRET, PAYLOAD, ts)

        assert verify_signature(SECRET, PAYLOAD, signature, version=2, timestamp=None) is False

    def test_unknown_version_fails_closed(self) -> None:
        ts = _now_iso()
        signature = sign_payload_v2(SECRET, PAYLOAD, ts)

        assert verify_signature(SECRET, PAYLOAD, signature, version=42, timestamp=ts) is False


class TestSkewWindow:
    def test_fresh_timestamp_is_accepted(self) -> None:
        assert is_timestamp_fresh(_now_iso()) is True

    def test_timestamp_inside_the_window_is_accepted(self) -> None:
        assert is_timestamp_fresh(_now_iso(offset_seconds=DEFAULT_MAX_SKEW_SECONDS - 5)) is True

    def test_stale_timestamp_is_rejected(self) -> None:
        assert is_timestamp_fresh(_now_iso(offset_seconds=DEFAULT_MAX_SKEW_SECONDS + 60)) is False

    def test_slightly_future_timestamp_is_accepted(self) -> None:
        """Sender clock ahead of the receiver's must not reject a fresh delivery."""
        assert is_timestamp_fresh(_now_iso(offset_seconds=30)) is True

    def test_wildly_future_timestamp_is_rejected(self) -> None:
        assert is_timestamp_fresh(_now_iso(offset_seconds=86_400)) is False

    def test_unparseable_timestamp_is_rejected(self) -> None:
        assert is_timestamp_fresh("not-a-timestamp") is False
        assert is_timestamp_fresh("") is False

    def test_custom_window_is_honoured(self) -> None:
        ts = _now_iso(offset_seconds=120)

        assert is_timestamp_fresh(ts, max_skew_seconds=30) is False
        assert is_timestamp_fresh(ts, max_skew_seconds=300) is True


class TestVerifyDeliverySignature:
    def test_fresh_v2_delivery_is_accepted(self) -> None:
        ts = _now_iso()
        signature = sign_payload_v2(SECRET, PAYLOAD, ts)

        assert verify_delivery_signature(SECRET, PAYLOAD, signature, 2, ts) is True

    def test_stale_v2_delivery_is_rejected(self) -> None:
        ts = _now_iso(offset_seconds=DEFAULT_MAX_SKEW_SECONDS + 60)
        signature = sign_payload_v2(SECRET, PAYLOAD, ts)

        assert verify_delivery_signature(SECRET, PAYLOAD, signature, 2, ts) is False

    def test_replay_within_the_window_still_verifies(self) -> None:
        """Documented limit: freshness bounds the replay window, it cannot end it.

        Deduplicating on the payload timestamp or delivery id is still required.
        """
        ts = _now_iso()
        signature = sign_payload_v2(SECRET, PAYLOAD, ts)

        assert verify_delivery_signature(SECRET, PAYLOAD, signature, 2, ts, now=datetime.now(UTC) + timedelta(seconds=60)) is True

    def test_legacy_v1_delivery_still_verifies(self) -> None:
        signature = sign_payload_v1(SECRET, PAYLOAD)

        assert verify_delivery_signature(SECRET, PAYLOAD, signature, 1, None) is True


class TestDeliveryHeaders:
    def test_headers_carry_a_signature_over_the_emitted_timestamp(self) -> None:
        headers = _build_headers(_FakeWebhook(), PAYLOAD)
        timestamp = headers["X-Webhook-Timestamp"]
        signature = headers["X-Webhook-Signature"].removeprefix("sha256=")

        assert headers["X-Webhook-Signature-Version"] == "2"
        assert verify_signature_v2(SECRET, PAYLOAD, signature, timestamp) is True

    def test_signed_input_matches_the_header_byte_for_byte(self) -> None:
        """A consumer that recomputes over its own clock must not be handed a
        timestamp that was not the one signed."""
        headers = _build_headers(_FakeWebhook(), PAYLOAD)
        signature = headers["X-Webhook-Signature"].removeprefix("sha256=")
        tampered = _now_iso(offset_seconds=5)

        assert verify_signature_v2(SECRET, PAYLOAD, signature, tampered) is False

    def test_no_secret_means_no_signature_headers(self) -> None:
        headers = _build_headers(_FakeWebhook(secret=None), PAYLOAD)

        assert "X-Webhook-Signature" not in headers
        assert "X-Webhook-Timestamp" in headers

    def test_legacy_version_still_signs_payload_only(self) -> None:
        headers = _build_headers(_FakeWebhook(), PAYLOAD, signature_version=1)
        signature = headers["X-Webhook-Signature"].removeprefix("sha256=")

        assert headers["X-Webhook-Signature-Version"] == "1"
        assert signature == sign_payload_v1(SECRET, PAYLOAD)
        assert verify_delivery_signature(SECRET, PAYLOAD, signature, 1, headers["X-Webhook-Timestamp"]) is True
