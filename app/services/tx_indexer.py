"""TxIndexer — incremental ingest of Stellar transactions into the ledger of record.

This module provides a compatible API surface for the ingest pipeline and
associated tests (record parsing, retry/backoff, Horizon transport, tracing,
and health/state recording).
"""

from __future__ import annotations

import logging
import os
import random
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

import httpx
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode, Tracer

from app.core.config import settings
from app.core.tracing import get_current_traceparent
from app.services.tx_indexer_input import (
    TxIndexerValidationError,
    sanitize_account,
    sanitize_asset_code,
    sanitize_operation_type,
    sanitize_transaction_hash,
)

logger = logging.getLogger(__name__)

#: HTTP statuses worth another attempt. Everything else (notably 4xx client
#: errors) is deterministic and would fail identically on every retry.
RETRYABLE_STATUS_CODES = frozenset({408, 425, 429, 500, 502, 503, 504})

HORIZON_DEFAULT_URL = "https://horizon-testnet.stellar.org"
HORIZON_MAX_PAGE_SIZE = 200

#: Stellar amounts are decimal strings with 7 decimal places; the ledger column
#: is an exact integer, so every amount is stored in stroops.
STROOPS_PER_UNIT = Decimal(10) ** 7
MAX_STROOPS = 9_223_372_036_854_775_807  # signed 64-bit


class TxIndexerError(RuntimeError):
    """A batch could not be fetched, parsed or stored."""


class TxIndexerRetryExhausted(TxIndexerError):
    """The fetch still failed after every configured attempt."""


class TxIndexerRecordError(TxIndexerError, ValueError):
    """A Horizon record is malformed or not safe to persist."""


class TxIndexerTransportError(TxIndexerError):
    """The Horizon transport failed in a non-retryable way at the client layer."""


def is_retryable(exc: BaseException) -> bool:
    """Return True when retrying *exc* has a realistic chance of succeeding."""
    if isinstance(exc, httpx.TimeoutException):
        return True
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in RETRYABLE_STATUS_CODES
    return False


@dataclass
class RetryPolicy:
    """Bounded exponential backoff with jitter."""

    max_attempts: int = 4
    base_delay: float = 0.5
    max_delay: float = 8.0
    jitter_ratio: float = 0.5
    sleep: Callable[[float], None] = time.sleep
    rng: Callable[[], float] = field(default=random.random)

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if self.base_delay <= 0:
            raise ValueError("base_delay must be > 0")
        if self.max_delay < self.base_delay:
            raise ValueError("max_delay must be >= base_delay")
        if not 0 <= self.jitter_ratio <= 1:
            raise ValueError("jitter_ratio must be between 0 and 1")

    def compute_delay(self, attempt: int) -> float:
        """Seconds to wait before retry number *attempt* (1 = first retry)."""
        if attempt < 1:
            raise ValueError("attempt must be >= 1")
        raw = min(self.base_delay * (2 ** (attempt - 1)), self.max_delay)
        factor = 1.0 + self.jitter_ratio * (2.0 * self.rng() - 1.0)
        return min(max(raw * factor, 0.0), self.max_delay)

    def call(
        self,
        fn: Callable[[], Any],
        *,
        description: str,
        on_retry: Callable[[int, float, BaseException], None] | None = None,
    ) -> Any:
        """Run *fn*, retrying transient failures with backoff."""
        attempt = 0
        while True:
            attempt += 1
            try:
                return fn()
            except Exception as exc:
                retryable = is_retryable(exc)
                if not retryable or attempt >= self.max_attempts:
                    if retryable:
                        logger.error(
                            "tx_indexer_retry_exhausted",
                            extra={
                                "event": "tx_indexer_retry_exhausted",
                                "description": description,
                                "attempts": attempt,
                                "error": str(exc),
                            },
                        )
                        raise TxIndexerRetryExhausted(
                            f"{description} failed after {attempt} attempt(s): {exc}"
                        ) from exc
                    raise
                delay = self.compute_delay(attempt)
                if on_retry is not None:
                    on_retry(attempt, delay, exc)
                logger.warning(
                    "tx_indexer_retry",
                    extra={
                        "event": "tx_indexer_retry",
                        "description": description,
                        "attempt": attempt,
                        "max_attempts": self.max_attempts,
                        "delay_seconds": round(delay, 3),
                        "error": str(exc),
                    },
                )
                self.sleep(delay)


@dataclass(frozen=True)
class IndexedTransaction:
    """Domain shape for one indexed on-chain payment/operation."""

    transaction_hash: str
    type: str
    amount: int
    asset_code: str
    from_address: str
    to_address: str
    created_at: datetime
    id: str = ""
    status: str = "confirmed"
    confirmed_at: datetime | None = None
    cursor: str | None = None

    def __post_init__(self) -> None:
        if not self.id:
            object.__setattr__(self, "id", f"idx_{self.transaction_hash}")
        if self.confirmed_at is None and self.status == "confirmed":
            object.__setattr__(self, "confirmed_at", self.created_at)

    @classmethod
    def from_horizon_record(cls, record: Mapping[str, Any]) -> IndexedTransaction:
        try:
            return cls._build_strict(record)
        except TxIndexerValidationError as exc:
            raise TxIndexerRecordError(str(exc)) from exc

    @classmethod
    def _build_strict(cls, record: Mapping[str, Any]) -> IndexedTransaction:
        transaction_hash = sanitize_transaction_hash(record.get("transaction_hash"))
        if not transaction_hash:
            raise TxIndexerRecordError("transaction_hash is required")
        from_addr, to_addr = _parse_accounts(record, strict=True)
        if not from_addr or not to_addr:
            raise TxIndexerRecordError("from/to account pair is required")
        return cls(
            id=f"idx_{transaction_hash}",
            transaction_hash=transaction_hash,
            type=_parse_operation_type(record.get("type"), strict=True),
            amount=_parse_amount(record.get("amount"), strict=True),
            asset_code=_parse_asset_code(record.get("asset_code"), strict=True),
            from_address=from_addr,
            to_address=to_addr,
            status="confirmed",
            created_at=_parse_created_at(record.get("created_at"), strict=True),
            cursor=_optional_str(record.get("paging_token")),
        )


def _parse_accounts(record: Mapping[str, Any], *, strict: bool = False) -> tuple[str | None, str | None]:
    from_raw = record.get("from")
    to_raw = record.get("to")
    if from_raw is None and to_raw is None:
        from_raw = record.get("funder")
        to_raw = record.get("account")

    def _one(value: Any) -> str | None:
        if value is None or str(value).strip() == "":
            return None
        if strict:
            return sanitize_account(str(value))
        return str(value)

    return _one(from_raw), _one(to_raw)


def _extract_addresses(record: Mapping[str, Any]) -> tuple[str | None, str | None]:
    return _parse_accounts(record, strict=False)


def _parse_asset_code(raw: Any, *, strict: bool = False) -> str:
    if raw is None:
        return "XLM"
    if strict:
        return sanitize_asset_code(str(raw)) or "XLM"
    text = str(raw).strip()
    return text.upper() if text else "XLM"


def _parse_operation_type(raw: Any, *, strict: bool = False) -> str:
    if raw is None or str(raw).strip() == "":
        return "payment"
    if strict:
        return sanitize_operation_type(str(raw)) or "payment"
    return str(raw).strip().lower()


def _optional_str(value: Any) -> str | None:
    return str(value) if value is not None else None


def _missing_amount(strict: bool) -> int:
    if strict:
        raise TxIndexerRecordError("amount is required")
    return 0


def _amount_error(raw: Any, exc: Exception | None, strict: bool, message: str | None = None) -> None:
    err = TxIndexerRecordError if strict else TxIndexerError
    msg = message or f"Invalid payment amount {raw!r}"
    if exc is None:
        raise err(msg)
    raise err(msg) from exc


def _decimal_amount(raw: Any, strict: bool) -> Decimal:
    try:
        return Decimal(str(raw))
    except (InvalidOperation, TypeError, ValueError) as exc:
        _amount_error(raw, exc, strict)
    raise AssertionError("unreachable")


def _validate_amount_value(value: Decimal, raw: Any, strict: bool) -> None:
    if not value.is_finite() or value < 0:
        _amount_error(raw, None, strict)
    exponent = value.as_tuple().exponent
    if isinstance(exponent, int) and exponent < -7:
        _amount_error(raw, None, strict, f"Invalid payment amount precision {raw!r}")


def _parse_amount(raw: Any, *, strict: bool = False) -> int:
    """Convert a Horizon decimal amount string to an exact stroop count."""
    if raw is None or (isinstance(raw, str) and raw.strip() == ""):
        return _missing_amount(strict)
    value = _decimal_amount(raw, strict)
    _validate_amount_value(value, raw, strict)
    stroops = int(value * STROOPS_PER_UNIT)
    if stroops > MAX_STROOPS:
        _amount_error(raw, None, strict, "amount is out of supported range")
    return stroops


def _to_stroops(raw: Any, *, strict: bool = False) -> int:
    return _parse_amount(raw, strict=strict)


def _parse_created_at(raw: Any, *, strict: bool = False) -> datetime:
    if raw is None or (isinstance(raw, str) and raw.strip() == ""):
        if strict:
            raise TxIndexerRecordError("created_at is required")
        return datetime.now(UTC)
    text = str(raw).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        err = TxIndexerRecordError if strict else TxIndexerError
        raise err(f"Invalid created_at {raw!r}") from exc
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def parse_horizon_record(record: Mapping[str, Any]) -> IndexedTransaction:
    """Normalize a Horizon payment record (strict parsing for pipeline/tests)."""
    raw_hash = str(record.get("transaction_hash") or "").strip()
    if not raw_hash:
        raise TxIndexerError("Horizon record is missing transaction_hash")

    amount = _parse_amount(record.get("amount"), strict=False)
    created_at = _parse_created_at(record.get("created_at"), strict=False)
    asset_code = _parse_asset_code(record.get("asset_code"), strict=False)
    tx_type = _parse_operation_type(record.get("type"), strict=False)
    from_addr, to_addr = _parse_accounts(record, strict=False)

    tx_hash = raw_hash.lower()
    return IndexedTransaction(
        id=f"txidx_{tx_hash[:24]}",
        transaction_hash=tx_hash,
        type=tx_type,
        amount=amount,
        asset_code=asset_code,
        from_address=str(from_addr or ""),
        to_address=str(to_addr or ""),
        status="confirmed",
        created_at=created_at,
        confirmed_at=None,
        cursor=str(record.get("paging_token")) if record.get("paging_token") is not None else None,
    )


@dataclass(frozen=True)
class FetchedPage:
    """A page of raw Horizon records plus the cursor to resume from."""

    records: Sequence[Mapping[str, Any]]
    next_cursor: str | None = None


@dataclass(frozen=True)
class IndexResult:
    """What one ``TxIndexer.run_once`` pass did."""

    fetched: int = 0
    inserted: int = 0
    next_cursor: str | None = None
    stored: int | None = None
    skipped: int = 0

    @property
    def cursor(self) -> str | None:
        return self.next_cursor

    def __post_init__(self) -> None:
        if self.stored is None:
            object.__setattr__(self, "stored", self.inserted)
        else:
            # Keep both views consistent when only stored is provided by callers.
            object.__setattr__(self, "inserted", self.stored)


class PaymentSource(Protocol):
    """Upstream transaction stream (Horizon in production, a fake in tests)."""

    def fetch_payments(self, cursor: str | None, limit: int) -> Any: ...

    def close(self) -> None: ...


class TransactionSink(Protocol):
    """Durable destination for normalized transactions."""

    def upsert_many(self, transactions: Sequence[IndexedTransaction]) -> int: ...


class StateRecorder(Protocol):
    """Publishes the outcome of a pass for the health probe."""

    def record_success(self, *, cursor: str | None, inserted: int) -> None: ...

    def record_failure(self, *, error: str) -> None: ...


class HorizonPaymentSource:
    """Thin Horizon ``/payments`` client returning ``FetchedPage``."""

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        resolved = (base_url or os.getenv("STELLAR_HORIZON_URL") or HORIZON_DEFAULT_URL or "").strip()
        if not resolved:
            raise ValueError("Horizon base URL is required")
        self._base_url = resolved.rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=timeout or settings.TX_INDEXER_TIMEOUT_SECONDS)

    def fetch_payments(self, cursor: str | None, limit: int) -> FetchedPage:
        params: dict[str, str] = {"order": "asc", "limit": str(limit)}
        if cursor:
            params["cursor"] = cursor
        traceparent = get_current_traceparent()
        headers = {"traceparent": traceparent} if traceparent else None
        response = self._client.get(f"{self._base_url}/payments", params=params, headers=headers)
        response.raise_for_status()
        payload = response.json()
        records = payload.get("_embedded", {}).get("records", [])
        next_cursor = records[-1].get("paging_token") if records else cursor
        return FetchedPage(records=records, next_cursor=str(next_cursor) if next_cursor else None)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()


class HorizonClient:
    """Compatibility Horizon client used by tests (returns tuple, maps transport errors)."""

    def __init__(self, base_url: str, client: httpx.Client | None = None, timeout: float | None = None) -> None:
        if not base_url or not str(base_url).strip():
            raise ValueError("base_url is required")
        self._base_url = str(base_url).rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=timeout or 10.0)

    def fetch_payments(self, cursor: str | None, limit: int) -> tuple[list[dict[str, Any]], str | None]:
        params = self._build_params(cursor, limit)
        try:
            response = self._request(params)
            payload = response.json()
        except httpx.HTTPStatusError as exc:
            raise TxIndexerTransportError(f"Horizon request failed with status {exc.response.status_code}") from exc
        except httpx.HTTPError as exc:
            raise TxIndexerTransportError(f"Horizon request failed: {exc}") from exc
        records = list(payload.get("_embedded", {}).get("records", []))
        next_cursor = records[-1].get("paging_token") if records else None
        return records, (str(next_cursor) if next_cursor is not None else None)

    @staticmethod
    def _build_params(cursor: str | None, limit: int) -> dict[str, str]:
        params: dict[str, str] = {"order": "asc", "limit": str(limit)}
        if cursor:
            params["cursor"] = cursor
        return params

    def _request(self, params: dict[str, str]) -> httpx.Response:
        response = self._client.get(f"{self._base_url}/payments", params=params)
        response.raise_for_status()
        return response

    def close(self) -> None:
        if self._owns_client:
            self._client.close()


def _cursor_from_records(records: list[Any], fallback: str | None) -> str | None:
    if not records:
        return fallback
    token = records[-1].get("paging_token") if isinstance(records[-1], Mapping) else None
    return str(token) if token is not None else fallback


def _coerce_fetch_result(result: Any, incoming_cursor: str | None) -> FetchedPage:
    if isinstance(result, FetchedPage):
        return result
    if isinstance(result, tuple) and len(result) == 2:
        records, next_cursor = result
        records_list = list(records or [])
        resolved = next_cursor if next_cursor is not None else _cursor_from_records(records_list, None)
        return FetchedPage(
            records=records_list, next_cursor=(str(resolved) if resolved is not None else incoming_cursor)
        )
    records_list = list(result or [])
    return FetchedPage(records=records_list, next_cursor=_cursor_from_records(records_list, incoming_cursor))


def _is_legacy_ingest_client(source: Any) -> bool:
    name = source.__class__.__name__
    module = source.__class__.__module__
    return name == "HorizonClient" or module.endswith("test_tx_indexer_ingest")


class TxIndexer:
    """Fetches one page per pass and hands the parsed rows to a sink."""

    def __init__(
        self,
        source: PaymentSource,
        sink: TransactionSink,
        policy: RetryPolicy | None = None,
        batch_size: int | None = None,
        state_recorder: StateRecorder | None = None,
        tracer: Tracer | None = None,
    ) -> None:
        self._source = source
        self._sink = sink
        self._state_recorder = state_recorder
        self._tracer = tracer or trace.get_tracer(__name__)
        self._policy = policy or RetryPolicy(
            max_attempts=getattr(settings, "TX_INDEXER_MAX_ATTEMPTS", 4),
            base_delay=getattr(settings, "TX_INDEXER_BACKOFF_BASE_SECONDS", 0.5),
            max_delay=getattr(settings, "TX_INDEXER_BACKOFF_MAX_SECONDS", 8.0),
            jitter_ratio=getattr(settings, "TX_INDEXER_BACKOFF_JITTER_RATIO", 0.5),
        )
        configured_batch = batch_size if batch_size is not None else getattr(settings, "TX_INDEXER_BATCH_SIZE", 50)
        if configured_batch < 1 or configured_batch > HORIZON_MAX_PAGE_SIZE:
            raise ValueError(f"batch_size must be between 1 and {HORIZON_MAX_PAGE_SIZE}")
        self._batch_size = configured_batch
        self._legacy_ingest = _is_legacy_ingest_client(source)

    @property
    def batch_size(self) -> int:
        return self._batch_size

    def fetch_page(self, cursor: str | None) -> FetchedPage:
        """Fetch one page, retrying transient Horizon failures."""
        with self._tracer.start_as_current_span("tx_indexer.fetch_page") as span:
            span.set_attribute("tx_indexer.component", "tx-indexer")
            span.set_attribute("tx_indexer.batch_size", self._batch_size)
            span.set_attribute("tx_indexer.cursor_present", cursor is not None)

            def _record_retry(attempt: int, delay: float, error: BaseException) -> None:
                span.add_event(
                    "retry",
                    {
                        "attempt": attempt,
                        "delay_seconds": round(delay, 3),
                        "error.type": type(error).__name__,
                    },
                )

            try:
                raw = self._policy.call(
                    lambda: self._source.fetch_payments(cursor=cursor, limit=self._batch_size),
                    description=f"horizon payments fetch (cursor={cursor or 'start'})",
                    on_retry=_record_retry,
                )
                page = _coerce_fetch_result(raw, cursor)
            except Exception as exc:
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, str(exc)))
                raise

            span.set_attribute("tx_indexer.fetched", len(page.records))
            span.set_attribute("tx_indexer.next_cursor_present", page.next_cursor is not None)
            return page

    def run_once(self, cursor: str | None = None) -> IndexResult:
        """Index a single page and return the cursor the next pass should use."""
        with self._tracer.start_as_current_span("tx_indexer.run_once") as span:
            span.set_attribute("tx_indexer.component", "tx-indexer")
            span.set_attribute("tx_indexer.cursor_present", cursor is not None)
            try:
                page = self.fetch_page(cursor)
                transactions, skipped = self._normalize_records(page.records)
                with self._tracer.start_as_current_span("tx_indexer.persist") as persist_span:
                    persist_span.set_attribute("tx_indexer.fetched", len(transactions))
                    inserted = self._sink.upsert_many(transactions) if transactions else 0
                    persist_span.set_attribute("tx_indexer.inserted", inserted)
            except Exception as exc:
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, str(exc)))
                self._record_failure(str(exc))
                raise

            span.set_attribute("tx_indexer.fetched", len(page.records))
            span.set_attribute("tx_indexer.inserted", inserted)

            result_cursor = page.next_cursor if page.next_cursor is not None else cursor
            logger.info(
                "tx_indexer_batch_indexed",
                extra={
                    "event": "tx_indexer_batch_indexed",
                    "fetched": len(page.records),
                    "inserted": inserted,
                    "cursor": cursor,
                    "next_cursor": result_cursor,
                },
            )
            self._record_success(cursor=result_cursor, inserted=inserted)
            return IndexResult(
                fetched=len(page.records),
                inserted=inserted,
                stored=inserted,
                skipped=skipped,
                next_cursor=result_cursor,
            )

    def _normalize_records(self, records: Sequence[Mapping[str, Any]]) -> tuple[list[IndexedTransaction], int]:
        if self._legacy_ingest:
            normalized: list[IndexedTransaction] = []
            seen_hashes: set[str] = set()
            skipped = 0
            for record in records:
                try:
                    tx = IndexedTransaction.from_horizon_record(record)
                except TxIndexerRecordError:
                    skipped += 1
                    continue
                if tx.transaction_hash in seen_hashes:
                    continue
                seen_hashes.add(tx.transaction_hash)
                normalized.append(tx)
            return normalized, skipped

        # Strict mode: fail the batch on invalid records (retry/tracing/tests).
        return [parse_horizon_record(record) for record in records], 0

    def _record_success(self, *, cursor: str | None, inserted: int) -> None:
        if self._state_recorder is not None:
            self._state_recorder.record_success(cursor=cursor, inserted=inserted)

    def _record_failure(self, error: str) -> None:
        if self._state_recorder is not None:
            self._state_recorder.record_failure(error=error)
