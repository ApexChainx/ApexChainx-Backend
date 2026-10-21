"""TxIndexer — incremental ingest of Stellar transactions into the ledger of record.

The indexer walks the Stellar payment stream one bounded page at a time and
upserts every record into ``payment_transactions`` (idempotent on
``transaction_hash``). Issue #713 adds retry with exponential backoff and
jitter around the Horizon fetch: Horizon answers 429/5xx and drops connections
during ordinary operation, and a single blip used to abort the whole pass, so
the cursor stalled and every later batch stayed unindexed.

Design notes
------------
* ``RetryPolicy`` is a tiny, dependency-free reimplementation of what
  ``tenacity`` gives us in ``app/core/db_retry.py``. It is used here instead of
  ``tenacity`` because the fetch needs per-attempt logging with the ingest
  context (cursor, batch size) and a jitter profile driven by settings, and
  because the sleep and RNG are injected so the backoff curve can be asserted
  without wall-clock waits.
* Only *transient* failures are retried: connection/timeout errors, plus
  408/425/429/500/502/503/504 responses. A 400 (malformed cursor) is retried
  zero times — retrying it just burns the budget.
* Retries are bounded by the total number of attempts, not by elapsed time, so
  one pass cannot outlive its caller's expectations (batch size is small and
  the delays cap at ``TX_INDEXER_BACKOFF_MAX_SECONDS``).
* Parsing is deliberately strict: a record without a ``transaction_hash`` would
  break the upsert's conflict target, so it fails the batch instead of writing
  a row that can never be deduplicated.
* Tracing (#715) follows the project-wide OTel setup in ``app/core/tracing.py``:
  each pass is one ``tx_indexer.run_once`` span with ``fetch_page`` and
  ``persist`` children, retries are span events, failures set the span status
  to ERROR with the exception recorded, and the W3C ``traceparent`` of the
  active span is forwarded to Horizon so the upstream call joins the same
  trace. The tracer is injectable, so tests assert spans without touching the
  process-global provider.
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

logger = logging.getLogger(__name__)

#: HTTP statuses worth another attempt. Everything else (notably 4xx client
#: errors) is deterministic and would fail identically on every retry.
RETRYABLE_STATUS_CODES = frozenset({408, 425, 429, 500, 502, 503, 504})

HORIZON_DEFAULT_URL = "https://horizon-testnet.stellar.org"

#: Stellar amounts are decimal strings with 7 decimal places; the ledger column
#: is an exact integer, so every amount is stored in stroops.
STROOPS_PER_UNIT = Decimal(10) ** 7


class TxIndexerError(RuntimeError):
    """A batch could not be fetched, parsed or stored."""


class TxIndexerRetryExhausted(TxIndexerError):
    """The fetch still failed after every configured attempt."""


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
    """Bounded exponential backoff with jitter.

    The delay before retry ``n`` (1-based) is
    ``min(base_delay * 2 ** (n - 1), max_delay)`` scaled by a uniform factor in
    ``[1 - jitter_ratio, 1 + jitter_ratio]``. The jitter is what keeps a fleet
    of indexers from hammering a recovering Horizon in lockstep.
    """

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
        """Run *fn*, retrying transient failures with backoff.

        *on_retry* is invoked with (attempt, delay, error) before each sleep so
        callers can attach span events without this class depending on OTel.
        """
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
    """One Horizon payment normalized to the ``payment_transactions`` shape."""

    transaction_hash: str
    type: str
    amount: int
    asset_code: str
    from_address: str
    to_address: str
    created_at: datetime

    @property
    def id(self) -> str:
        # Deterministic and prefixed so an indexed row is never confused with a
        # settlement row minted by PaymentRepository ("pay_...").
        return f"txidx_{self.transaction_hash[:24]}"


def _to_stroops(raw: Any) -> int:
    """Convert a Horizon decimal amount string to an exact stroop count."""
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise TxIndexerError(f"Invalid payment amount {raw!r}") from exc
    if not value.is_finite() or value < 0:
        raise TxIndexerError(f"Invalid payment amount {raw!r}")
    return int(value * STROOPS_PER_UNIT)


def _parse_created_at(raw: Any) -> datetime:
    if not raw:
        return datetime.now(UTC)
    text = str(raw).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise TxIndexerError(f"Invalid created_at {raw!r}") from exc
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def parse_horizon_record(record: Mapping[str, Any]) -> IndexedTransaction:
    """Normalize a Horizon payment record.

    Raises ``TxIndexerError`` for records that cannot be stored faithfully
    rather than writing a half-populated row.
    """
    transaction_hash = str(record.get("transaction_hash") or "").strip()
    if not transaction_hash:
        raise TxIndexerError("Horizon record is missing transaction_hash")

    return IndexedTransaction(
        transaction_hash=transaction_hash,
        type=str(record.get("type") or "payment"),
        amount=_to_stroops(record.get("amount", "0")),
        asset_code=str(record.get("asset_code") or "XLM"),
        from_address=str(record.get("from") or ""),
        to_address=str(record.get("to") or ""),
        created_at=_parse_created_at(record.get("created_at")),
    )


@dataclass(frozen=True)
class FetchedPage:
    """A page of raw Horizon records plus the cursor to resume from."""

    records: Sequence[Mapping[str, Any]]
    next_cursor: str | None = None


@dataclass(frozen=True)
class IndexResult:
    """What one ``TxIndexer.run_once`` pass did."""

    fetched: int
    inserted: int
    next_cursor: str | None


class PaymentSource(Protocol):
    """Upstream transaction stream (Horizon in production, a fake in tests)."""

    def fetch_payments(self, cursor: str | None, limit: int) -> FetchedPage: ...

    def close(self) -> None: ...


class TransactionSink(Protocol):
    """Durable destination for normalized transactions."""

    def upsert_many(self, transactions: Sequence[IndexedTransaction]) -> int: ...


class StateRecorder(Protocol):
    """Publishes the outcome of a pass for the health probe (#714)."""

    def record_success(self, *, cursor: str | None, inserted: int) -> None: ...

    def record_failure(self, *, error: str) -> None: ...


class HorizonPaymentSource:
    """Thin Horizon ``/payments`` client.

    Kept intentionally small: the retry policy, parsing and persistence live in
    the indexer so this class has no policy of its own.
    """

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self._base_url = (base_url or os.getenv("STELLAR_HORIZON_URL") or HORIZON_DEFAULT_URL).rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=timeout or settings.TX_INDEXER_TIMEOUT_SECONDS)

    def fetch_payments(self, cursor: str | None, limit: int) -> FetchedPage:
        params: dict[str, str] = {"order": "asc", "limit": str(limit)}
        if cursor:
            params["cursor"] = cursor
        # Forward the active trace context (when there is one) so Horizon-side
        # instrumentation and our span join the same distributed trace.
        traceparent = get_current_traceparent()
        headers = {"traceparent": traceparent} if traceparent else None
        response = self._client.get(f"{self._base_url}/payments", params=params, headers=headers)
        # 4xx/5xx become HTTPStatusError; RetryPolicy decides whether it is
        # transient (429/5xx) or final (400 from a malformed cursor).
        response.raise_for_status()
        payload = response.json()
        records = payload.get("_embedded", {}).get("records", [])
        next_cursor = records[-1].get("paging_token") if records else cursor
        return FetchedPage(records=records, next_cursor=str(next_cursor) if next_cursor else None)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()


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
            max_attempts=settings.TX_INDEXER_MAX_ATTEMPTS,
            base_delay=settings.TX_INDEXER_BACKOFF_BASE_SECONDS,
            max_delay=settings.TX_INDEXER_BACKOFF_MAX_SECONDS,
            jitter_ratio=settings.TX_INDEXER_BACKOFF_JITTER_RATIO,
        )
        self._batch_size = batch_size or settings.TX_INDEXER_BATCH_SIZE

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
                page = self._policy.call(
                    lambda: self._source.fetch_payments(cursor=cursor, limit=self._batch_size),
                    description=f"horizon payments fetch (cursor={cursor or 'start'})",
                    on_retry=_record_retry,
                )
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
                transactions = [parse_horizon_record(record) for record in page.records]
                with self._tracer.start_as_current_span("tx_indexer.persist") as persist_span:
                    persist_span.set_attribute("tx_indexer.fetched", len(transactions))
                    inserted = self._sink.upsert_many(transactions)
                    persist_span.set_attribute("tx_indexer.inserted", inserted)
            except Exception as exc:
                # Every failure path (retries exhausted, unparseable record,
                # write error) is visible to the health probe and to the trace.
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, str(exc)))
                self._record_failure(str(exc))
                raise

            span.set_attribute("tx_indexer.fetched", len(transactions))
            span.set_attribute("tx_indexer.inserted", inserted)

            logger.info(
                "tx_indexer_batch_indexed",
                extra={
                    "event": "tx_indexer_batch_indexed",
                    "fetched": len(transactions),
                    "inserted": inserted,
                    "cursor": cursor,
                    "next_cursor": page.next_cursor,
                },
            )
            self._record_success(cursor=page.next_cursor, inserted=inserted)
            return IndexResult(
                fetched=len(transactions),
                inserted=inserted,
                next_cursor=page.next_cursor,
            )

    def _record_success(self, *, cursor: str | None, inserted: int) -> None:
        if self._state_recorder is not None:
            self._state_recorder.record_success(cursor=cursor, inserted=inserted)

    def _record_failure(self, error: str) -> None:
        if self._state_recorder is not None:
            self._state_recorder.record_failure(error=error)
