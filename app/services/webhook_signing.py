"""Webhook signing and verification utilities with version management (BE-087).

This module provides signature generation and verification with explicit versioning support.
Versioning enables safe evolution of signing algorithms without breaking existing consumers.

## Signature Versions

### Version 2 (Current - timestamped HMAC)
- Algorithm: HMAC-SHA256
- Format: `sha256={hex_digest}`
- Signed input: `{X-Webhook-Timestamp}.{payload}`
- Header: `X-Webhook-Signature: sha256={hex_digest}`
- Version Header: `X-Webhook-Signature-Version: 2`
- Replay protection: the timestamp is inside the signed input, so a captured
  delivery cannot be re-signed for a different time, and a receiver can reject
  anything outside its skew window (default 300s, see `DEFAULT_MAX_SKEW_SECONDS`).

### Version 1 (Legacy - payload-only HMAC)
- Algorithm: HMAC-SHA256
- Format: `sha256={hex_digest}`
- Payload: Raw JSON string
- Header: `X-Webhook-Signature: sha256={hex_digest}`
- Version Header: `X-Webhook-Signature-Version: 1`

Still accepted so existing consumers keep verifying, but it offers **no replay
protection**: the signature covers the payload only, so the same bytes replay
indistinguishably from a fresh delivery (#538). Prefer version 2.

Future versions (e.g., EdDSA, RSA-PSS) can be added while maintaining backward
compatibility.

## Timestamp Validation Semantics

### Receiver-Facing Contract
Webhooks include an explicit timestamp in the payload (`timestamp` field) for:
1. **Idempotency**: Detect and deduplicate retried deliveries
2. **Freshness validation**: Optional receiver-side time window validation
3. **Audit trails**: Track when events occurred vs. when they were delivered

### Timestamp Format
- ISO 8601 format: `2026-04-29T14:30:45.123456`
- Timezone: UTC
- Field location: Top-level `timestamp` in JSON payload
- Immutable: Same timestamp across all retry attempts and signature versions

### `X-Webhook-Timestamp` vs. the payload `timestamp`
Two different clocks, deliberately:
- the payload `timestamp` is **when the event happened** and never changes;
- the `X-Webhook-Timestamp` header is **when this attempt was signed**, and it is
  the value covered by the version 2 signature. Each retry attempt re-stamps and
  re-signs it, so a retried delivery is not rejected as stale by a receiver's
  skew check; deduplicate on the payload timestamp or delivery id instead.

### Receiver Recommendations
1. Store and compare timestamps for idempotency (database unique constraint on webhook_id + timestamp)
2. Reject timestamps outside a configurable grace period (e.g., > 1 hour old)
3. Use timestamp + delivery ID for audit logging and reconciliation
4. Verify version 2 signatures over `{X-Webhook-Timestamp}.{payload}`, and reject
   anything whose timestamp is outside your skew window
"""

import hashlib
import hmac
from datetime import UTC, datetime

# Current signature algorithm version
CURRENT_SIGNATURE_VERSION = 2

# Version 1 signs the payload only: no timestamp, therefore no replay protection.
LEGACY_SIGNATURE_VERSION = 1

# Version 2 signs "{timestamp}.{payload}".
TIMESTAMPED_SIGNATURE_VERSION = 2

# Separator between the signed timestamp and the payload in version 2.
TIMESTAMP_SEPARATOR = "."

# Default freshness window a receiver should apply to `X-Webhook-Timestamp`.
DEFAULT_MAX_SKEW_SECONDS = 300


def sign_payload_v1(secret: str, payload: str) -> str:
    """Generate HMAC-SHA256 signature for payload.

    Args:
        secret: Secret key (will be encoded to UTF-8)
        payload: JSON payload string (will be encoded to UTF-8)

    Returns:
        Hex-encoded digest string
    """
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def verify_signature_v1(secret: str, payload: str, signature: str) -> bool:
    """Verify HMAC-SHA256 signature.

    Args:
        secret: Secret key used during signing
        payload: Original JSON payload
        signature: Hex-encoded signature to verify (without 'sha256=' prefix)

    Returns:
        True if signature is valid, False otherwise
    """
    expected_signature = sign_payload_v1(secret, payload)
    return hmac.compare_digest(expected_signature, signature)


def signing_input_v2(timestamp: str, payload: str) -> str:
    """Return the exact string version 2 signs: ``{timestamp}.{payload}``.

    Published so a receiver can reimplement the check in any language.
    """
    return f"{timestamp}{TIMESTAMP_SEPARATOR}{payload}"


def sign_payload_v2(secret: str, payload: str, timestamp: str) -> str:
    """Generate HMAC-SHA256 signature over the timestamp and the payload (#538).

    Because the timestamp is part of the signed input, a captured delivery cannot
    be presented as fresh at a later time: the receiver recomputes over the
    timestamp it received, and a mismatch is a signature failure.

    Args:
        secret: Secret key (encoded to UTF-8)
        payload: JSON payload string (encoded to UTF-8)
        timestamp: ISO-8601 UTC timestamp, as sent in `X-Webhook-Timestamp`

    Returns:
        Hex-encoded digest string
    """
    message = signing_input_v2(timestamp, payload)
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


def verify_signature_v2(secret: str, payload: str, signature: str, timestamp: str) -> bool:
    """Verify a version 2 (timestamped) signature.

    Returns:
        True if signature is valid, False otherwise
    """
    expected_signature = sign_payload_v2(secret, payload, timestamp)
    return hmac.compare_digest(expected_signature, signature)


def is_timestamp_fresh(
    timestamp: str,
    max_skew_seconds: int = DEFAULT_MAX_SKEW_SECONDS,
    now: datetime | None = None,
) -> bool:
    """Report whether `X-Webhook-Timestamp` is inside the allowed skew window.

    Accepts timestamps slightly in the future, since the sender's clock can be
    ahead of the receiver's. Unparseable or naive-but-UTC-less values are treated
    as stale rather than waved through.
    """
    try:
        sent_at = datetime.fromisoformat(timestamp)
    except (TypeError, ValueError):
        return False
    if sent_at.tzinfo is None:
        sent_at = sent_at.replace(tzinfo=UTC)
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    return abs((current - sent_at).total_seconds()) <= max_skew_seconds


def verify_delivery_signature(
    secret: str,
    payload: str,
    signature: str,
    version: int,
    timestamp: str | None,
    max_skew_seconds: int = DEFAULT_MAX_SKEW_SECONDS,
    now: datetime | None = None,
) -> bool:
    """Receiver-side check: signature valid **and** timestamp fresh (#538).

    Version 1 deliveries carry no replay protection, so they verify on signature
    alone and the caller is expected to deduplicate on the payload timestamp or
    delivery id. Version 2 requires both.
    """
    if version == LEGACY_SIGNATURE_VERSION:
        return verify_signature_v1(secret, payload, signature)
    if not timestamp or not is_timestamp_fresh(timestamp, max_skew_seconds, now):
        return False
    return verify_signature_v2(secret, payload, signature, timestamp)


def sign_payload(secret: str, payload: str, version: int = CURRENT_SIGNATURE_VERSION, timestamp: str | None = None) -> tuple[str, int, str]:
    """Generate signature with version support.

    Args:
        secret: Secret key
        payload: JSON payload string
        version: Signature algorithm version (defaults to current)
        timestamp: ISO-8601 UTC timestamp for version 2. Defaults to now, and the
            value used is returned so the caller can send it in
            `X-Webhook-Timestamp` — the signed input and the header must match
            byte for byte. Ignored for version 1.

    Returns:
        Tuple of (signature_hex, version, timestamp)

    Raises:
        ValueError: If version is not supported
    """
    if version == LEGACY_SIGNATURE_VERSION:
        return sign_payload_v1(secret, payload), version, timestamp or ""
    if version == TIMESTAMPED_SIGNATURE_VERSION:
        ts = timestamp or datetime.now(UTC).isoformat()
        return sign_payload_v2(secret, payload, ts), version, ts
    raise ValueError(f"Unsupported signature version: {version}")


def verify_signature(
    secret: str,
    payload: str,
    signature: str,
    version: int = CURRENT_SIGNATURE_VERSION,
    timestamp: str | None = None,
) -> bool:
    """Verify signature with version support.

    Args:
        secret: Secret key used during signing
        payload: Original JSON payload
        signature: Hex-encoded signature (without algorithm prefix like 'sha256=')
        version: Signature algorithm version that was used
        timestamp: The `X-Webhook-Timestamp` value the delivery carried. Required
            for version 2 — a timestamped signature cannot be verified without
            it, so a missing value fails closed instead of being ignored.

    Returns:
        True if signature is valid, False otherwise
    """
    if version == LEGACY_SIGNATURE_VERSION:
        return verify_signature_v1(secret, payload, signature)
    if version == TIMESTAMPED_SIGNATURE_VERSION:
        if not timestamp:
            return False
        return verify_signature_v2(secret, payload, signature, timestamp)
    # Unknown version - fail securely
    return False


# NOTE (#297): `verify_signature_with_grace` was removed. It verified against
# `hashed_secret` (a one-way SHA-256 digest stored by rotate_webhook_secret) as
# if it were the raw HMAC key, which can never match a signature produced with
# the real old secret — the "grace period" it implemented was non-functional
# and unused by any call site. Until previous secrets are stored in a form
# that can actually re-verify old signatures (e.g. encrypted, not hashed),
# the backend does not implement a verification-side grace period: only
# `verify_signature` against the current secret is supported.
