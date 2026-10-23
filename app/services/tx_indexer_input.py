"""Input sanitization for the TxIndexer (#711).

Two things reach the indexer from the outside world: query parameters on the
read API, and records streamed from Horizon. Neither is trusted.

The rules here are deliberately **allow-list based**. Every filter is first
stripped, then rejected if it contains a control character, then checked
against the one character class that field can legitimately contain. That makes
"sanitization" mean "only a canonical value survives", not "escape the quotes
and hope the query is parameterized":

* a transaction hash is 64 hexadecimal characters — nothing else can be a
  Stellar hash;
* an asset code is 1-12 alphanumerics, normalized to upper case;
* statuses and operation types are lower-case snake_case tokens from the
  values Horizon and this service emit;
* an account is a bounded Stellar account id / muxed id / placeholder.

SQL injection is not prevented by this module. Every query the repository runs
is parameterized; the point here is that a value carrying quotes, comment
markers, semicolons or NUL bytes is a *bad request* (422) rather than an empty
result set, and that a caller cannot push an unbounded string into a database
filter. Both properties are tested in
``tests/test_tx_indexer_input_sanitization.py``.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

#: A Stellar transaction hash is a 32-byte value rendered as 64 hex characters.
HASH_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")

#: Stellar account ids are 56-character StrKeys; muxed ids are longer. The
#: pattern stays permissive about the prefix because seed/dev data uses
#: placeholders such as ``SYSTEM_POOL``.
ACCOUNT_PATTERN = re.compile(r"^[A-Za-z0-9*_:]{1,69}$")

#: Stellar asset codes are 1-12 alphanumerics (``USDC``, ``XLM``, ...).
ASSET_CODE_PATTERN = re.compile(r"^[A-Za-z0-9]{1,12}$")

#: Statuses and operation types are lower snake_case tokens.
TOKEN_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,31}$")

MAX_ACCOUNT_LENGTH = 69
MAX_HASH_LENGTH = 64
MAX_ASSET_CODE_LENGTH = 12
MAX_TOKEN_LENGTH = 32


class TxIndexerValidationError(ValueError):
    """A caller-supplied value is not an acceptable filter."""

    def __init__(self, field: str, message: str) -> None:
        self.field = field
        self.reason = message
        super().__init__(f"Invalid {field}: {message}")


def _reject_control_characters(value: str, field: str) -> None:
    """Reject NUL and any other control/format character."""
    for character in value:
        if unicodedata.category(character).startswith("C"):
            raise TxIndexerValidationError(field, "must not contain control characters")


def _sanitize(
    value: str | None,
    *,
    field: str,
    pattern: re.Pattern[str],
    max_length: int,
    normalize: Callable[[str], str] | None = None,
) -> str | None:
    """Strip, canonicalize, bound and allow-list one value.

    Returns ``None`` for a missing filter (absence is not an error), and the
    normalized value otherwise. ``normalize`` runs *before* the allow-list, so
    ``USDC``/``usdc`` and an upper-case hash are accepted and then emitted in
    their canonical form.
    """
    if value is None:
        return None

    candidate = value.strip()
    if not candidate:
        raise TxIndexerValidationError(field, "must not be empty")

    _reject_control_characters(candidate, field)

    if normalize is not None:
        candidate = normalize(candidate)

    if len(candidate) > max_length:
        raise TxIndexerValidationError(field, f"must be at most {max_length} characters")

    if not pattern.match(candidate):
        raise TxIndexerValidationError(
            field,
            "contains unsupported characters or is not in the expected format",
        )
    return candidate


def sanitize_transaction_hash(value: str | None) -> str | None:
    """Return a canonical lower-case hash, or reject the value."""
    return _sanitize(
        value,
        field="transaction_hash",
        pattern=HASH_PATTERN,
        max_length=MAX_HASH_LENGTH,
        normalize=str.lower,
    )


def sanitize_account(value: str | None) -> str | None:
    """Return a bounded Stellar account filter."""
    return _sanitize(
        value,
        field="account",
        pattern=ACCOUNT_PATTERN,
        max_length=MAX_ACCOUNT_LENGTH,
    )


def sanitize_asset_code(value: str | None) -> str | None:
    """Return an upper-case asset code, the form Horizon emits."""
    return _sanitize(
        value,
        field="asset_code",
        pattern=ASSET_CODE_PATTERN,
        max_length=MAX_ASSET_CODE_LENGTH,
        normalize=str.upper,
    )


def sanitize_status(value: str | None) -> str | None:
    """Return a lower-case status token."""
    return _sanitize(
        value,
        field="status",
        pattern=TOKEN_PATTERN,
        max_length=MAX_TOKEN_LENGTH,
        normalize=str.lower,
    )


def sanitize_operation_type(value: str | None) -> str | None:
    """Return a lower-case Horizon operation type."""
    return _sanitize(
        value,
        field="type",
        pattern=TOKEN_PATTERN,
        max_length=MAX_TOKEN_LENGTH,
        normalize=str.lower,
    )


def sanitize_bound(value: datetime | None, *, field: str) -> datetime | None:
    """Normalize a time bound to aware UTC.

    A naive timestamp is interpreted as UTC rather than left to the database's
    session timezone, so the same request means the same window on every
    deployment.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


@dataclass(frozen=True)
class TxIndexFilters:
    """Validated, canonical filters for the transaction index read API."""

    transaction_hash: str | None = None
    account: str | None = None
    asset_code: str | None = None
    status: str | None = None
    transaction_type: str | None = None
    created_after: datetime | None = None
    created_before: datetime | None = None

    @classmethod
    def from_query(
        cls,
        *,
        transaction_hash: str | None = None,
        account: str | None = None,
        asset_code: str | None = None,
        status: str | None = None,
        transaction_type: str | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
    ) -> TxIndexFilters:
        """Validate every raw query value at once.

        Raises:
            TxIndexerValidationError: the first value that is not acceptable.
        """
        return cls(
            transaction_hash=sanitize_transaction_hash(transaction_hash),
            account=sanitize_account(account),
            asset_code=sanitize_asset_code(asset_code),
            status=sanitize_status(status),
            transaction_type=sanitize_operation_type(transaction_type),
            created_after=sanitize_bound(created_after, field="created_after"),
            created_before=sanitize_bound(created_before, field="created_before"),
        )
