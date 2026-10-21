"""Identifier validation for the block poller (issue #732).

The poller accepts values that arrive from outside the process -- a cursor, a
transaction hash, a status filter -- and uses them to select rows.  Every one
of those values is validated against an allowlist here *before* it reaches
the database, so a malformed or hostile value can never end up inside a
statement.

The queries themselves only ever use bound parameters; the validation exists
because the same values are also used to build the URL of the network call,
where a parameter placeholder is not available.
"""

from __future__ import annotations

import re

# Stellar transaction hashes are 64 lowercase hexadecimal characters.
TRANSACTION_HASH_PATTERN = re.compile(r"\A[0-9a-f]{64}\Z")

# Payment statuses the poller understands.  Anything else is rejected rather
# than used as a filter value.
ALLOWED_PAYMENT_STATUSES = frozenset({"pending", "confirmed", "failed"})

# The poller only ever looks at payments that have not been settled yet.
POLLABLE_PAYMENT_STATUSES = frozenset({"pending"})

MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 20


class UnsafeIdentifierError(ValueError):
    """An identifier did not match its allowlist and was rejected."""


def validate_transaction_hash(value: str) -> str:
    """Return ``value`` when it is a well-formed Stellar transaction hash.

    Raises:
        UnsafeIdentifierError: for anything that is not 64 lowercase
            hexadecimal characters -- including empty strings, surrounding
            whitespace and SQL metacharacters.
    """
    if not isinstance(value, str) or not TRANSACTION_HASH_PATTERN.match(value):
        raise UnsafeIdentifierError("transaction hash must be 64 lowercase hexadecimal characters")
    return value


def validate_payment_status(value: str) -> str:
    """Return ``value`` when it is a payment status the poller understands.

    Raises:
        UnsafeIdentifierError: for any status outside the allowlist.
    """
    if not isinstance(value, str) or value not in ALLOWED_PAYMENT_STATUSES:
        raise UnsafeIdentifierError(f"unsupported payment status: {value!r}")
    return value


def validate_page_size(
    value: int,
    *,
    maximum: int = MAX_PAGE_SIZE,
    default: int = DEFAULT_PAGE_SIZE,
) -> int:
    """Clamp a requested page size into ``1..maximum``.

    Raises:
        UnsafeIdentifierError: when the value is not an integer at all.
    """
    try:
        size = int(value)
    except (TypeError, ValueError) as exc:
        raise UnsafeIdentifierError("page size must be an integer") from exc
    if size < 1:
        return default
    return min(size, maximum)
