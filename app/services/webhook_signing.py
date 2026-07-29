"""Webhook signing and verification utilities with version management (BE-087).

This module provides signature generation and verification with explicit versioning support.
Versioning enables safe evolution of signing algorithms without breaking existing consumers.

## Signature Versions

### Version 1 (Current - SHA256 HMAC)
- Algorithm: HMAC-SHA256
- Format: `sha256={hex_digest}`
- Payload: Raw JSON string
- Header: `X-Webhook-Signature: sha256={hex_digest}`
- Version Header: `X-Webhook-Signature-Version: 1`

Future versions (e.g., EdDSA, RSA-PSS) can be added while maintaining backward compatibility.

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

### Receiver Recommendations
1. Store and compare timestamps for idempotency (database unique constraint on webhook_id + timestamp)
2. Optional: Reject timestamps outside a configurable grace period (e.g., > 1 hour old)
3. Use timestamp + delivery ID for audit logging and reconciliation
"""

import hmac
import hashlib
from datetime import datetime, timezone
from typing import Any, List, Optional, Tuple


# Current signature algorithm version
CURRENT_SIGNATURE_VERSION = 1


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


def sign_payload(secret: str, payload: str, version: int = CURRENT_SIGNATURE_VERSION) -> Tuple[str, int]:
    """Generate signature with version support.

    Args:
        secret: Secret key
        payload: JSON payload string
        version: Signature algorithm version (defaults to current)

    Returns:
        Tuple of (signature_hex, version)

    Raises:
        ValueError: If version is not supported
    """
    if version == 1:
        return sign_payload_v1(secret, payload), version
    else:
        raise ValueError(f"Unsupported signature version: {version}")


def verify_signature(
    secret: str,
    payload: str,
    signature: str,
    version: int = CURRENT_SIGNATURE_VERSION,
) -> bool:
    """Verify signature with version support.

    Args:
        secret: Secret key used during signing
        payload: Original JSON payload
        signature: Hex-encoded signature (without algorithm prefix like 'sha256=')
        version: Signature algorithm version that was used

    Returns:
        True if signature is valid, False otherwise
    """
    if version == 1:
        return verify_signature_v1(secret, payload, signature)
    else:
        # Unknown version - fail securely
        return False


def verify_signature_with_grace(
    secret: str,
    payload: str,
    signature: str,
    version: int = CURRENT_SIGNATURE_VERSION,
    previous_secrets: Optional[List[dict[str, Any]]] = None,
) -> bool:
    """Verify signature against current secret and valid previous secrets.

    Tries the current secret first. If that fails, tries each previous
    secret that has not yet expired. This enables zero-downtime secret rotation.

    Args:
        secret: Current secret key
        payload: Original JSON payload
        signature: Hex-encoded signature
        version: Signature algorithm version
        previous_secrets: List of dicts with hashed_secret and expires_at

    Returns:
        True if any valid secret produces a matching signature
    """
    if verify_signature(secret, payload, signature, version):
        return True

    if not previous_secrets:
        return False

    now = datetime.now(timezone.utc)
    for entry in previous_secrets:
        expires_at = datetime.fromisoformat(entry["expires_at"])
        if expires_at < now:
            continue
        if verify_signature(entry["hashed_secret"], payload, signature, version):
            return True

    return False
