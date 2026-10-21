"""Resumable ledger polling for the Stellar Horizon API (#718-#721).

``BlockPoller`` walks Horizon ledger records one page at a time, hands each
ledger to a caller-supplied handler, and only advances its cursor after the
handler succeeds. If a handler fails, the cursor stays on the last processed
ledger so the next poll re-reads the failed one instead of silently skipping
on-chain activity.

The module is intentionally transport-agnostic: ``HorizonBlockSource`` is the
production source, while tests inject a fake implementation of the
``BlockSource`` protocol. Fetch failures are classified as retryable (network
errors, HTTP 408/429/5xx) or terminal (other 4xx, malformed responses) and
retried with bounded exponential backoff.

Every poll/retry/failure emits a structured JSON log record through
``app.utils.logging``. Records include the event name, cursor, ledger height,
attempt number, delay, and error type, but never the raw ledger payload.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Callable, Mapping, Protocol, Sequence

import httpx

from app.utils.logging import StructuredLogger, get_structured_logger

DEFAULT_HORIZON_URL = "https://horizon-testnet.stellar.org"
HORIZON_MAX_LEDGERS_PER_PAGE = 200


class BlockPollerError(Exception):
    """Base class for errors raised by the block poller."""


class BlockFetchError(BlockPollerError):
    """Raised when a block source cannot return a page of ledgers.

    ``retryable`` lets the poller distinguish a transient Horizon/network
    failure from a terminal response such as a 404 or malformed JSON.
    """

    def __init__(self, message: str, *, retryable: bool = True, status_code: int | None = None) -> None:
        self.retryable = retryable
        self.status_code = status_code
        super().__init__(message)


class BlockHandlerError(BlockPollerError):
    """Raised when the consumer cannot process a fetched ledger."""

    def __init__(self, message: str, *, block: Block) -> None:
        self.block = block
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class Block:
    """A single ledger/block, addressed by a source-specific cursor."""

    cursor: str
    height: int
    payload: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.cursor:
            raise ValueError("block cursor must not be empty")
        if self.height < 0:
            raise ValueError("block height must be non-negative")


@dataclass(frozen=True, slots=True)
class PollCycleResult:
    """Summary of one successful ``BlockPoller.poll_once`` call."""

    blocks_processed: int
    cursor: str | None
    latest_height: int | None
    attempts: int
    started_at: datetime
    completed_at: datetime

    @property
    def duration_ms(self) -> float:
        return (self.completed_at - self.started_at).total_seconds() * 1000


class BlockSource(Protocol):
    """Minimal interface the poller needs from a ledger provider."""

    def fetch_blocks(self, *, cursor: str | None, limit: int) -> Sequence[Block]:
        """Return the next page of blocks after ``cursor`` (ascending order)."""


class HorizonBlockSource:
    """Reads ledgers from the Horizon ``/ledgers`` endpoint.

    ``client`` may be supplied by tests (for example with an
    ``httpx.MockTransport``). When omitted, the source owns its client and
    closes it via :meth:`close` / the context-manager protocol.
    """

    def __init__(
        self,
        base_url: str = DEFAULT_HORIZON_URL,
        *,
        client: httpx.Client | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        if not base_url or not base_url.strip():
            raise ValueError("base_url must not be empty")
        if "://" not in base_url:
            raise ValueError("base_url must include an http(s) scheme")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0")

        self._base_url = base_url.rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=timeout_seconds)

    def fetch_blocks(self, *, cursor: str | None = None, limit: int = 50) -> Sequence[Block]:
        if not 1 <= limit <= HORIZON_MAX_LEDGERS_PER_PAGE:
            raise ValueError(f"limit must be between 1 and {HORIZON_MAX_LEDGERS_PER_PAGE}")

        params: dict[str, Any] = {"order": "asc", "limit": limit}
        if cursor is not None:
            params["cursor"] = cursor

        try:
            response = self._client.get(f"{self._base_url}/ledgers", params=params)
        except httpx.HTTPError as exc:
            raise BlockFetchError(f"Horizon request failed: {exc}", retryable=True) from exc

        if response.status_code == 408 or response.status_code == 429 or response.status_code >= 500:
            raise BlockFetchError(
                f"Horizon returned retryable HTTP {response.status_code}",
                retryable=True,
                status_code=response.status_code,
            )
        if response.status_code >= 400:
            raise BlockFetchError(
                f"Horizon returned terminal HTTP {response.status_code}",
                retryable=False,
                status_code=response.status_code,
            )

        try:
            body = response.json()
        except ValueError as exc:
            raise BlockFetchError("Horizon returned invalid JSON", retryable=False) from exc

        if not isinstance(body, dict):
            raise BlockFetchError("Horizon response must be a JSON object", retryable=False)
        embedded = body.get("_embedded")
        if not isinstance(embedded, dict):
            raise BlockFetchError("Horizon response is missing _embedded", retryable=False)
        records = embedded.get("records")
        if not isinstance(records, list):
            raise BlockFetchError("Horizon response is missing _embedded.records", retryable=False)

        blocks: list[Block] = []
        for index, record in enumerate(records):
            if not isinstance(record, dict):
                raise BlockFetchError(f"Horizon ledger record {index} is not an object", retryable=False)

            block_cursor = record.get("paging_token")
            if not isinstance(block_cursor, str) or not block_cursor:
                raise BlockFetchError(f"Horizon ledger record {index} has no paging_token", retryable=False)

            raw_height = record.get("sequence")
            if isinstance(raw_height, bool):
                raise BlockFetchError(f"Horizon ledger record {index} has an invalid sequence", retryable=False)
            try:
                height = int(raw_height)
            except (TypeError, ValueError) as exc:
                raise BlockFetchError(
                    f"Horizon ledger record {index} has an invalid sequence", retryable=False
                ) from exc

            blocks.append(Block(cursor=block_cursor, height=height, payload=record))
        return blocks

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> HorizonBlockSource:
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()


class BlockPoller:
    """Polls a ``BlockSource`` and dispatches each block to a handler."""

    def __init__(
        self,
        source: BlockSource,
        handler: Callable[[Block], None] | None = None,
        *,
        poll_interval_seconds: float = 5.0,
        batch_size: int = 50,
        start_cursor: str | None = None,
        max_attempts: int = 3,
        retry_base_delay_seconds: float = 0.5,
        retry_max_delay_seconds: float = 8.0,
        sleep: Callable[[float], None] = time.sleep,
        logger: StructuredLogger | None = None,
    ) -> None:
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be > 0")
        if not 1 <= batch_size <= HORIZON_MAX_LEDGERS_PER_PAGE:
            raise ValueError(f"batch_size must be between 1 and {HORIZON_MAX_LEDGERS_PER_PAGE}")
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if retry_base_delay_seconds < 0:
            raise ValueError("retry_base_delay_seconds must be >= 0")
        if retry_max_delay_seconds < retry_base_delay_seconds:
            raise ValueError("retry_max_delay_seconds must be >= retry_base_delay_seconds")

        self._source = source
        self._handler = handler
        self._poll_interval_seconds = poll_interval_seconds
        self._batch_size = batch_size
        self._cursor = start_cursor
        self._max_attempts = max_attempts
        self._retry_base_delay_seconds = retry_base_delay_seconds
        self._retry_max_delay_seconds = retry_max_delay_seconds
        self._sleep = sleep
        self._logger = logger or get_structured_logger("block_poller")

        self._stop_event = threading.Event()
        self._active_stop_event: threading.Event | None = None
        self._running = False
        self._last_poll_at: datetime | None = None
        self._last_success_at: datetime | None = None
        self._last_error: str | None = None
        self._last_error_type: str | None = None
        self._consecutive_failures = 0

    @property
    def cursor(self) -> str | None:
        return self._cursor

    @property
    def running(self) -> bool:
        return self._running

    def health(self) -> dict[str, Any]:
        """Return an operator-friendly snapshot of the poller's current state."""
        if self._last_error is not None:
            status = "unhealthy" if self._consecutive_failures >= self._max_attempts else "degraded"
        elif self._last_success_at is not None:
            status = "healthy"
        else:
            status = "starting"

        return {
            "status": status,
            "running": self._running,
            "cursor": self._cursor,
            "last_poll_at": self._last_poll_at.isoformat() if self._last_poll_at else None,
            "last_success_at": self._last_success_at.isoformat() if self._last_success_at else None,
            "last_error": self._last_error,
            "last_error_type": self._last_error_type,
            "consecutive_failures": self._consecutive_failures,
        }

    def stop(self) -> None:
        """Ask ``run`` to stop after the current poll cycle."""
        self._stop_event.set()
        if self._active_stop_event is not None:
            self._active_stop_event.set()

    def run(self, stop_event: threading.Event | None = None) -> None:
        """Poll continuously until ``stop_event`` (or :meth:`stop`) is set."""
        event = stop_event or self._stop_event
        self._active_stop_event = event
        self._running = True
        self._logger.info(
            "block_poller.started",
            event="started",
            cursor=self._cursor,
            poll_interval_seconds=self._poll_interval_seconds,
            batch_size=self._batch_size,
        )
        try:
            while not event.is_set():
                try:
                    self.poll_once()
                except BlockPollerError:
                    # The failure is recorded + logged by poll_once; keep the
                    # loop alive so a transient Horizon outage does not kill
                    # the worker. A persistent failure remains visible through
                    # ``health()`` and the structured error logs.
                    pass
                if not event.is_set():
                    event.wait(self._poll_interval_seconds)
        finally:
            self._running = False
            self._active_stop_event = None
            self._logger.info("block_poller.stopped", event="stopped", cursor=self._cursor)

    def poll_once(self) -> PollCycleResult:
        """Fetch and process one page of blocks.

        Returns a summary on success. Raises :class:`BlockFetchError` after the
        retry budget is exhausted, or :class:`BlockHandlerError` when the
        handler rejects a block; in both cases the cursor only reflects blocks
        that were fully processed.
        """
        started_at = datetime.now(UTC)

        try:
            blocks, attempts = self._fetch_with_retry()
        except BlockPollerError as exc:
            self._record_failure(exc)
            raise

        blocks_processed = 0
        latest_height: int | None = None

        for block in blocks:
            if self._handler is not None:
                try:
                    self._handler(block)
                except Exception as exc:  # noqa: BLE001 - handler failures are surfaced, not swallowed
                    error = BlockHandlerError(
                        f"block handler failed at height {block.height}: {exc}",
                        block=block,
                    )
                    self._record_failure(error)
                    self._logger.error(
                        "block_poller.handler_failed",
                        event="handler_failed",
                        cursor=block.cursor,
                        height=block.height,
                        error=str(exc),
                        error_type=type(exc).__name__,
                        consecutive_failures=self._consecutive_failures,
                    )
                    raise error from exc

            # Advance only after the handler succeeded: a crash/retry re-reads
            # this ledger rather than skipping it.
            self._cursor = block.cursor
            latest_height = block.height
            blocks_processed += 1
            self._logger.debug(
                "block_poller.block_processed",
                event="block_processed",
                cursor=block.cursor,
                height=block.height,
            )

        completed_at = datetime.now(UTC)
        self._last_poll_at = completed_at
        self._last_success_at = completed_at
        self._consecutive_failures = 0
        self._last_error = None
        self._last_error_type = None

        result = PollCycleResult(
            blocks_processed=blocks_processed,
            cursor=self._cursor,
            latest_height=latest_height,
            attempts=attempts,
            started_at=started_at,
            completed_at=completed_at,
        )
        self._logger.info(
            "block_poller.poll_completed",
            event="poll_completed",
            cursor=self._cursor,
            blocks_processed=blocks_processed,
            latest_height=latest_height,
            attempts=attempts,
            duration_ms=result.duration_ms,
        )
        return result

    def _fetch_with_retry(self) -> tuple[list[Block], int]:
        last_error: BlockPollerError | None = None

        for attempt in range(1, self._max_attempts + 1):
            try:
                raw_blocks = self._source.fetch_blocks(cursor=self._cursor, limit=self._batch_size)
                return self._validate_blocks(raw_blocks), attempt
            except BlockFetchError as exc:
                last_error = exc
            except Exception as exc:  # noqa: BLE001 - an injected source may raise anything
                last_error = BlockFetchError(f"unexpected block source error: {exc}", retryable=True)

            assert last_error is not None
            if not last_error.retryable or attempt >= self._max_attempts:
                self._logger.error(
                    "block_poller.fetch_failed",
                    event="fetch_failed",
                    cursor=self._cursor,
                    attempts=attempt,
                    max_attempts=self._max_attempts,
                    error=str(last_error),
                    error_type=type(last_error).__name__,
                    retryable=last_error.retryable,
                    status_code=last_error.status_code,
                )
                raise last_error

            delay = min(
                self._retry_base_delay_seconds * (2 ** (attempt - 1)),
                self._retry_max_delay_seconds,
            )
            self._logger.warning(
                "block_poller.fetch_retry",
                event="fetch_retry",
                cursor=self._cursor,
                attempt=attempt,
                max_attempts=self._max_attempts,
                delay_seconds=delay,
                error=str(last_error),
                error_type=type(last_error).__name__,
                status_code=last_error.status_code,
            )
            self._sleep(delay)

        # The loop either returns or raises on every iteration.
        assert last_error is not None
        raise last_error

    @staticmethod
    def _validate_blocks(raw_blocks: Sequence[Block]) -> list[Block]:
        if isinstance(raw_blocks, (str, bytes)) or not isinstance(raw_blocks, Sequence):
            raise BlockFetchError("block source must return a sequence of Block objects", retryable=False)
        blocks = list(raw_blocks)
        for block in blocks:
            if not isinstance(block, Block):
                raise BlockFetchError("block source returned a non-Block value", retryable=False)
        return blocks

    def _record_failure(self, error: Exception) -> None:
        self._last_poll_at = datetime.now(UTC)
        self._last_error = str(error)
        self._last_error_type = type(error).__name__
        self._consecutive_failures += 1
