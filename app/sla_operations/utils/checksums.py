"""Deterministic checksum and hashing utilities for the SLA operations workspace.

This module provides small, dependency-light helpers for computing stable
checksums over JSON-serializable data and raw bytes/strings. It is intended
for use by workspace services and schemas that need report/export integrity
metadata (e.g. content hashes) without depending on core application state.

All helpers are pure functions with deterministic behavior. Input contents are
never logged.

Edge behavior summary:
    * Empty input (``b""`` or ``""``) yields the well-known SHA-256 digest of
      empty bytes: ``e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855``.
    * Non-serializable JSON input raises :class:`TypeError`.
    * Non-``bytes``/``str`` input to :func:`sha256_hex` or
      :func:`checksum_bytes` raises :class:`TypeError`.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

__all__ = [
    "canonicalize_json",
    "sha256_hex",
    "checksum_json",
    "checksum_bytes",
]

#: SHA-256 digest of empty bytes, exposed for convenience/testing.
EMPTY_SHA256_HEX = (
    "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
)


def canonicalize_json(data: Any) -> bytes:
    """Serialize ``data`` to a canonical, deterministic JSON byte string.

    The encoding is stable across runs and Python processes:

    * object keys are sorted (``sort_keys=True``),
    * separators are compact (``(",", ":")``) with no extra whitespace,
    * non-ASCII characters are preserved as UTF-8 (``ensure_ascii=False``),
    * the result is encoded as UTF-8.

    Parameters:
        data: Any JSON-serializable Python object (dict, list, str, int,
            float, bool, ``None``, or nested combinations thereof).

    Returns:
        The canonical UTF-8 encoded JSON representation of ``data``.

    Raises:
        TypeError: If ``data`` (or a nested value) is not JSON-serializable,
            e.g. a custom object, ``set``, ``bytes``, or a non-finite float
            when ``allow_nan`` is disabled by the encoder. Note that
            ``float('nan')``/``inf`` are serialized by the stdlib encoder by
            default; callers requiring strict JSON should validate upstream.
    """
    try:
        serialized = json.dumps(
            data,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
    except (TypeError, ValueError) as exc:
        # Re-raise as TypeError for a consistent, documented contract.
        raise TypeError(
            f"Value is not JSON-serializable: {type(data).__name__}"
        ) from exc
    return serialized.encode("utf-8")


def sha256_hex(data: bytes | str) -> str:
    """Compute the lowercase hex SHA-256 digest of ``data``.

    Parameters:
        data: Raw bytes, or a ``str`` which is UTF-8 encoded before hashing.

    Returns:
        A 64-character lowercase hexadecimal SHA-256 digest string.

    Raises:
        TypeError: If ``data`` is neither ``bytes`` nor ``str``.

    Edge behavior:
        Empty input (``b""`` or ``""``) returns the well-known SHA-256 digest
        of empty bytes (:data:`EMPTY_SHA256_HEX`).
    """
    if isinstance(data, str):
        payload = data.encode("utf-8")
    elif isinstance(data, (bytes, bytearray, memoryview)):
        payload = bytes(data)
    else:
        raise TypeError(
            "sha256_hex expects bytes or str, got "
            f"{type(data).__name__}"
        )
    return hashlib.sha256(payload).hexdigest()


def checksum_json(data: Any) -> str:
    """Compute the SHA-256 hex checksum of the canonical JSON form of ``data``.

    The input is first canonicalized with :func:`canonicalize_json`, so the
    resulting checksum is independent of object key ordering and insignificant
    whitespace.

    Parameters:
        data: Any JSON-serializable Python object.

    Returns:
        A 64-character lowercase hexadecimal SHA-256 digest string.

    Raises:
        TypeError: If ``data`` is not JSON-serializable.
    """
    return sha256_hex(canonicalize_json(data))


def checksum_bytes(data: bytes) -> str:
    """Compute the SHA-256 hex checksum of raw ``data`` bytes.

    Parameters:
        data: Raw bytes to hash.

    Returns:
        A 64-character lowercase hexadecimal SHA-256 digest string.

    Raises:
        TypeError: If ``data`` is not a bytes-like object.

    Edge behavior:
        Empty input (``b""``) returns the well-known SHA-256 digest of empty
        bytes (:data:`EMPTY_SHA256_HEX`).
    """
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError(
            "checksum_bytes expects a bytes-like object, got "
            f"{type(data).__name__}"
        )
    return hashlib.sha256(bytes(data)).hexdigest()