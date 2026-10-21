"""Validation for TxIndexer read queries (#716).

Every value that reaches the indexer's query path comes from the network. This
module is the single place that decides whether a value is safe to bind: sort
columns are resolved through a whitelist (never interpolated into SQL), amounts
are coerced to bounded integers, and hashes, accounts, asset codes, statuses and
timestamps are pattern-checked.

The repository still binds everything as statement parameters — validation is
defence in depth, not the only barrier — but rejecting hostile input here means
a malformed filter is a `422` with a reason instead of a database error or a
silently widened scan.
"""

from __future__ import annotations

import re
from datetime import datetime

from app.models.payment import PaymentStatus
from app.utils.wallet_address import WalletAddressError, normalize as normalize_wallet

#: Stellar transaction hashes are 32 bytes, hex-encoded.
TRANSACTION_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
TRANSACTION_HASH_LENGTH = 64

#: Asset codes are 1-12 alphanumerics (Stellar asset-code grammar).
ASSET_CODE_PATTERN = re.compile(r"^[A-Z0-9]{1,12}$")

#: Payment "type" is an internal label such as ``payment``/``reward``.
TRANSACTION_TYPE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,31}$")

#: Outage ids are generated as ``outage_<hex>``.
OUTAGE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

VALID_STATUSES = frozenset(status.value for status in PaymentStatus)
VALID_SORT_FIELDS = frozenset({"created_at", "confirmed_at", "amount", "status"})
VALID_SORT_DIRECTIONS = frozenset({"asc", "desc"})

MIN_LIMIT = 1
MAX_LIMIT = 200
DEFAULT_LIMIT = 50

#: PostgreSQL BIGINT bounds; the amount column is an exact integer.
BIGINT_MIN = -(2**63)
BIGINT_MAX = 2**63 - 1


class TxIndexerQueryError(ValueError):
    """A filter value is not safe or not meaningful for the indexer query."""


def _text(raw: object, field: str, max_length: int) -> str:
    if not isinstance(raw, str):
        raise TxIndexerQueryError(f"{field} must be a string")
    value = raw.strip()
    if not value:
        raise TxIndexerQueryError(f"{field} must not be empty")
    if len(value) > max_length:
        raise TxIndexerQueryError(f"{field} must be at most {max_length} characters")
    return value


def validate_transaction_hash(raw: object | None) -> str | None:
    """Return the lowercase hash, or None when the filter is absent."""
    if raw is None:
        return None
    value = _text(raw, "transaction_hash", TRANSACTION_HASH_LENGTH).lower()
    if not TRANSACTION_HASH_PATTERN.fullmatch(value):
        raise TxIndexerQueryError("transaction_hash must be a 64-character hex string")
    return value


def validate_account(raw: object | None, *, field: str = "account") -> str | None:
    """Validate a Stellar account id so it can be compared to either party."""
    if raw is None:
        return None
    value = _text(raw, field, 56)
    try:
        return str(normalize_wallet(value))
    except WalletAddressError as exc:
        raise TxIndexerQueryError(f"{field} is not a valid Stellar account: {exc.reason}") from exc


def validate_asset_code(raw: object | None) -> str | None:
    if raw is None:
        return None
    value = _text(raw, "asset_code", 12).upper()
    if not ASSET_CODE_PATTERN.fullmatch(value):
        raise TxIndexerQueryError("asset_code must be 1-12 alphanumeric characters")
    return value


def validate_status(raw: object | None) -> str | None:
    if raw is None:
        return None
    value = _text(raw, "status", 16).lower()
    if value not in VALID_STATUSES:
        raise TxIndexerQueryError(f"status must be one of: {', '.join(sorted(VALID_STATUSES))}")
    return value


def validate_transaction_type(raw: object | None) -> str | None:
    if raw is None:
        return None
    value = _text(raw, "type", 32).lower()
    if not TRANSACTION_TYPE_PATTERN.fullmatch(value):
        raise TxIndexerQueryError("type must be lowercase letters, digits or underscores")
    return value


def validate_outage_id(raw: object | None) -> str | None:
    if raw is None:
        return None
    value = _text(raw, "outage_id", 64)
    if not OUTAGE_ID_PATTERN.fullmatch(value):
        raise TxIndexerQueryError("outage_id contains unsupported characters")
    return value


def validate_amount(raw: object | None, *, field: str) -> int | None:
    """Coerce an amount filter to an exact BIGINT, rejecting anything else."""
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, (int, str)):
        raise TxIndexerQueryError(f"{field} must be an integer number of stroops")
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise TxIndexerQueryError(f"{field} must be an integer number of stroops") from exc
    if not BIGINT_MIN <= value <= BIGINT_MAX:
        raise TxIndexerQueryError(f"{field} is outside the supported amount range")
    return value


def validate_timestamp(raw: object | None, *, field: str) -> datetime | None:
    if raw is None:
        return None
    if not isinstance(raw, datetime):
        raise TxIndexerQueryError(f"{field} must be an ISO-8601 timestamp")
    return raw


def validate_amount_range(min_amount: object | None, max_amount: object | None) -> tuple[int | None, int | None]:
    low = validate_amount(min_amount, field="min_amount")
    high = validate_amount(max_amount, field="max_amount")
    if low is not None and high is not None and low > high:
        raise TxIndexerQueryError("min_amount must not be greater than max_amount")
    return low, high


def validate_timestamp_range(
    created_after: object | None,
    created_before: object | None,
) -> tuple[datetime | None, datetime | None]:
    start = validate_timestamp(created_after, field="created_after")
    end = validate_timestamp(created_before, field="created_before")
    if start is not None and end is not None and start > end:
        raise TxIndexerQueryError("created_after must not be later than created_before")
    return start, end


def validate_sort(sort_by: object | None, sort_dir: object | None) -> tuple[str, str]:
    """Resolve a sort key through the whitelist; unknown keys are rejected.

    The returned names are *identifiers from the whitelist*, never the raw input,
    so the repository can look them up in its column map without interpolating
    user text into SQL.
    """
    field = "created_at" if sort_by is None else _text(sort_by, "sort_by", 32).lower()
    direction = "desc" if sort_dir is None else _text(sort_dir, "sort_dir", 4).lower()
    if field not in VALID_SORT_FIELDS:
        raise TxIndexerQueryError(f"sort_by must be one of: {', '.join(sorted(VALID_SORT_FIELDS))}")
    if direction not in VALID_SORT_DIRECTIONS:
        raise TxIndexerQueryError(f"sort_dir must be one of: {', '.join(sorted(VALID_SORT_DIRECTIONS))}")
    return field, direction


def validate_limit(raw: object | None) -> int:
    if raw is None:
        return DEFAULT_LIMIT
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise TxIndexerQueryError("limit must be an integer")
    if not MIN_LIMIT <= raw <= MAX_LIMIT:
        raise TxIndexerQueryError(f"limit must be between {MIN_LIMIT} and {MAX_LIMIT}")
    return raw


def escape_like(value: str) -> str:
    """Escape LIKE metacharacters so user text is matched literally.

    Not used for the equality filters, but exported for any future prefix search:
    an unescaped ``%`` or ``_`` turns a bounded lookup into a full scan, and a
    trailing backslash would otherwise escape the closing wildcard.
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
