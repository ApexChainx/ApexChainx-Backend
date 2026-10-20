"""TxIndexer — incremental ingest of Stellar transactions into the ledger of record (#710).

The indexer walks the Stellar Horizon payment stream one bounded page at a time
and writes every operation it sees into ``payment_transactions``, the table the
payments API already reads. That table carries a unique ``transaction_hash``, so
re-playing a page is idempotent: the sink skips hashes it has already stored
instead of raising.

One row per on-chain transaction
--------------------------------
Horizon streams *operations*, and a single transaction can contain several
payment operations (a batch payout, for example). The ledger of record is keyed
by transaction, so the first payment operation seen for a transaction wins and
the remaining operations of that same transaction are collapsed into it. The
alternative — one row per operation — would need a new table and a migration;
this keeps the existing payment history consistent.

Design notes
------------
* ``IndexedTransaction.from_horizon_record`` is deliberately strict about the
  fields the storage layer depends on. A record without a ``transaction_hash``
  or a parseable amount is logged and skipped; it never reaches the sink,
  because an un-keyable or mis-scaled row corrupts the ledger.
* Amounts are converted from Horizon's decimal string to **stroops**
  (1 unit = 10^7 stroops) so the stored value stays an exact integer.
* The Horizon client is injectable and the transport is a plain
  ``httpx.Client``, so tests exercise the whole ingest path without network
  access.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

import httpx

from app.services.tx_indexer_input import (
    TxIndexerValidationError,
    sanitize_account,
    sanitize_asset_code,
    sanitize_operation_type,
    sanitize_transaction_hash,
)

logger = logging.getLogger(__name__)

#: Horizon refuses ``limit`` values above 200 for the payments endpoint.
HORIZON_MAX_PAGE_SIZE = 200

#: Stellar amounts are decimal strings with 7 decimal places.
STROOPS_PER_UNIT = Decimal(10) ** 7

#: Native payments carry no ``asset_code``; this is the code used for XLM.
NATIVE_ASSET_CODE = "XLM"

#: Indexed rows are confirmed by definition — they come from a closed ledger.
INDEXED_STATUS = "confirmed"

#: ``payment_transactions.amount`` is a signed 64-bit column. A Horizon value
#: that would overflow it is rejected at normalization time instead of raising a
#: database error that aborts the whole batch.
MAX_AMOUNT_STROOPS = 2**63 - 1


class TxIndexerError(RuntimeError):
    """Base class for every failure raised by the indexer."""


class TxIndexerRecordError(TxIndexerError):
    """A Horizon record cannot be normalized into an indexed transaction."""


class TxIndexerTransportError(TxIndexerError):
    """Horizon could not be reached, or answered with an error status."""


def _sanitize_record_field(
    value: Any,
    *,
    sanitizer: Callable[[str | None], str | None],
    error: str,
) -> str:
    """Run one allow-list sanitizer over a Horizon field.

    Horizon values are untrusted input: they are the remote half of the ingest
    path. A value that fails the allow-list is a record error, so the batch
    skips it instead of writing something the ledger cannot represent.
    """
    if value is None or str(value).strip() == "":
        raise TxIndexerRecordError(error)
    try:
        sanitized = sanitizer(str(value))
    except TxIndexerValidationError as exc:
        raise TxIndexerRecordError(f"{error}: {exc.reason}") from exc
    if not sanitized:
        raise TxIndexerRecordError(error)
    return sanitized


def _parse_created_at(value: Any) -> datetime:
    """Parse Horizon's ISO-8601 ``created_at`` into an aware UTC datetime."""
    if not value:
        raise TxIndexerRecordError("record has no created_at")
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise TxIndexerRecordError(f"invalid created_at: {value!r}") from exc
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


@dataclass(frozen=True)
class IndexedTransaction:
    """One on-chain payment operation normalized for the ledger of record."""

    id: str
    transaction_hash: str
    type: str
    amount: int
    asset_code: str
    from_address: str
    to_address: str
    created_at: datetime
    status: str = INDEXED_STATUS

    @classmethod
    def from_horizon_record(cls, record: Mapping[str, Any]) -> IndexedTransaction:
        """Normalize one Horizon payment record.

        Raises:
            TxIndexerRecordError: the record is missing a field the ledger of
                record relies on, or a field cannot be parsed.
        """
        transaction_hash = _sanitize_record_field(
            record.get("transaction_hash"),
            sanitizer=sanitize_transaction_hash,
            error="record has no transaction_hash",
        )

        try:
            amount = int(Decimal(str(record.get("amount"))) * STROOPS_PER_UNIT)
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise TxIndexerRecordError(f"invalid amount: {record.get('amount')!r}") from exc
        if amount < 0:
            raise TxIndexerRecordError(f"negative amount: {record.get('amount')!r}")
        if amount > MAX_AMOUNT_STROOPS:
            raise TxIndexerRecordError(f"amount out of range: {record.get('amount')!r}")

        source = record.get("from") or record.get("source_account") or record.get("funder")
        destination = record.get("to") or record.get("account")
        if not source or not destination:
            raise TxIndexerRecordError("record has no from/to account pair")

        from_address = _sanitize_record_field(source, sanitizer=sanitize_account, error="invalid source account")
        to_address = _sanitize_record_field(
            destination, sanitizer=sanitize_account, error="invalid destination account"
        )
        asset_code = _sanitize_record_field(
            record.get("asset_code") or NATIVE_ASSET_CODE,
            sanitizer=sanitize_asset_code,
            error="invalid asset_code",
        )
        operation_type = _sanitize_record_field(
            record.get("type") or "payment",
            sanitizer=sanitize_operation_type,
            error="invalid operation type",
        )

        return cls(
            id=f"idx_{transaction_hash}",
            transaction_hash=transaction_hash,
            type=operation_type,
            amount=amount,
            asset_code=asset_code,
            from_address=from_address,
            to_address=to_address,
            created_at=_parse_created_at(record.get("created_at")),
        )


class TxSink(Protocol):
    """The write side of the indexer (implemented by the repository)."""

    def upsert_many(self, transactions: Sequence[IndexedTransaction]) -> int:
        """Store *transactions* idempotently and return the number of new rows."""


@dataclass(frozen=True)
class IngestResult:
    """Outcome of a single ingest pass."""

    fetched: int
    stored: int
    skipped: int
    cursor: str | None


class HorizonClient:
    """Minimal Horizon payments reader.

    Only the one endpoint the indexer needs is implemented: ``GET /payments``
    ordered ascending, with cursor pagination.
    """

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 10.0,
        client: httpx.Client | None = None,
    ) -> None:
        if not base_url:
            raise ValueError("base_url must not be empty")
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._client = client

    def fetch_payments(
        self,
        cursor: str | None,
        limit: int,
    ) -> tuple[list[Mapping[str, Any]], str | None]:
        """Fetch up to *limit* payments, returning them plus the next cursor."""
        params: dict[str, Any] = {"order": "asc", "limit": limit}
        if cursor:
            params["cursor"] = cursor

        try:
            if self._client is not None:
                response = self._client.get("/payments", params=params)
            else:
                with httpx.Client(base_url=self._base_url, timeout=self._timeout) as client:
                    response = client.get("/payments", params=params)
        except httpx.HTTPError as exc:  # transport failures
            raise TxIndexerTransportError(f"Horizon request failed: {exc}") from exc

        if response.status_code >= 400:
            raise TxIndexerTransportError(f"Horizon returned HTTP {response.status_code}")

        payload = response.json()
        records = payload.get("_embedded", {}).get("records", []) or []
        next_cursor = records[-1].get("paging_token") if records else None
        return [record for record in records if isinstance(record, Mapping)], next_cursor


class TxIndexer:
    """Fetch a page from Horizon, normalize it, and hand it to the sink."""

    def __init__(self, client: HorizonClient, sink: TxSink, *, batch_size: int = HORIZON_MAX_PAGE_SIZE) -> None:
        if not 1 <= batch_size <= HORIZON_MAX_PAGE_SIZE:
            raise ValueError(f"batch_size must be between 1 and {HORIZON_MAX_PAGE_SIZE}")
        self._client = client
        self._sink = sink
        self._batch_size = batch_size

    @property
    def batch_size(self) -> int:
        return self._batch_size

    def run_once(self, cursor: str | None = None) -> IngestResult:
        """Run a single ingest pass starting after *cursor*."""
        records, next_cursor = self._client.fetch_payments(cursor, self._batch_size)
        transactions, skipped = self._normalize(records)
        stored = self._sink.upsert_many(transactions) if transactions else 0

        logger.info(
            "tx_indexer_pass",
            extra={
                "event": "tx_indexer_pass",
                "cursor": cursor,
                "next_cursor": next_cursor,
                "fetched": len(records),
                "stored": stored,
                "skipped": skipped,
            },
        )
        return IngestResult(
            fetched=len(records),
            stored=stored,
            skipped=skipped,
            cursor=next_cursor or cursor,
        )

    def _normalize(self, records: Iterable[Mapping[str, Any]]) -> tuple[list[IndexedTransaction], int]:
        """Normalize a page, collapsing operations that share a transaction.

        Returns the deduplicated transactions and the number of records that
        could not be normalized.
        """
        by_hash: dict[str, IndexedTransaction] = {}
        skipped = 0
        for record in records:
            try:
                transaction = IndexedTransaction.from_horizon_record(record)
            except TxIndexerRecordError as exc:
                skipped += 1
                logger.warning(
                    "tx_indexer_skipped_record",
                    extra={"event": "tx_indexer_skipped_record", "error": str(exc)},
                )
                continue
            by_hash.setdefault(transaction.transaction_hash, transaction)
        return list(by_hash.values()), skipped
