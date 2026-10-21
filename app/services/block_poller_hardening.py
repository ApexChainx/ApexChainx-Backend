"""Fault-tolerant block polling with rate limiting and input sanitization.

This module implements a reusable ``BlockPoller`` that fetches blocks from an
upstream source, validates and sanitizes untrusted payloads, enforces a
sliding-window request budget, and retries transient failures with exponential
backoff and jitter.

Related issues: #725 (rate limiting), #727 (input sanitization),
#728 (reduce cyclomatic complexity), and #729 (retry with backoff).
"""

from __future__ import annotations

import json
import logging
import math
import random
import re
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

from app.core.config import settings
from app.services.metrics import increment_counter, set_gauge

logger = logging.getLogger(__name__)

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+/=\-]*$")
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SEQUENCE_FIELDS = ("sequence", "ledger", "ledger_sequence")
_IDENTIFIER_FIELDS = ("hash", "id", "tx_hash", "transaction_hash", "paging_token")
_MAX_SEQUENCE = 2**63 - 1
_MAX_BACKOFF_EXPONENT = 32


class BlockPollerError(Exception):
    """Base error for block polling failures."""


class BlockPollerValidationError(BlockPollerError, ValueError):
    """Raised when an upstream block payload fails validation."""


class BlockPollerFetchError(BlockPollerError):
    """Raised when a block cannot be fetched after all retry attempts."""


class PollStatus(str, Enum):
    PROCESSED = "processed"
    RATE_LIMITED = "rate_limited"


@dataclass(frozen=True)
class PollResult:
    """Outcome of a single poll cycle."""

    status: PollStatus
    sequence: int | None = None
    attempts: int = 0
    block: dict[str, Any] | None = None


class SlidingWindowRateLimiter:
    """Thread-safe sliding-window limiter used to protect the upstream source."""

    def __init__(
        self,
        max_requests: int,
        window_seconds: float,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_requests <= 0:
            raise ValueError("max_requests must be > 0")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be > 0")

        self._max_requests = max_requests
        self._window_seconds = window_seconds
        self._clock = clock
        self._timestamps: deque[float] = deque()
        self._lock = threading.Lock()

    @property
    def max_requests(self) -> int:
        return self._max_requests

    @property
    def window_seconds(self) -> float:
        return self._window_seconds

    def _discard_expired(self, now: float) -> None:
        cutoff = now - self._window_seconds
        while self._timestamps and self._timestamps[0] <= cutoff:
            self._timestamps.popleft()

    def try_acquire(self) -> bool:
        """Reserve one request slot, returning False when the limit is reached."""
        with self._lock:
            now = self._clock()
            self._discard_expired(now)
            if len(self._timestamps) >= self._max_requests:
                return False
            self._timestamps.append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._timestamps.clear()


def compute_retry_delay(
    attempt_index: int,
    *,
    base_delay: float,
    max_delay: float,
    jitter_ratio: float = 0.0,
    rng: random.Random | None = None,
) -> float:
    """Return an exponentially increasing delay with optional proportional jitter.

    ``attempt_index`` is zero-based: 0 for the first retry, 1 for the second, etc.
    When ``jitter_ratio`` is greater than zero the delay is drawn from
    ``[(1 - jitter_ratio) * delay, delay]`` so retries from multiple workers do
    not stampede the upstream source.
    """
    if attempt_index < 0:
        raise ValueError("attempt_index must be >= 0")
    if base_delay <= 0 or max_delay <= 0:
        return 0.0

    exponent = min(attempt_index, _MAX_BACKOFF_EXPONENT)
    delay = min(max_delay, base_delay * (2**exponent))

    if jitter_ratio <= 0:
        return delay

    jitter = delay * min(jitter_ratio, 1.0)
    lower_bound = delay - jitter
    generator = rng or random
    return max(0.0, min(max_delay, lower_bound + generator.uniform(0.0, jitter)))


def _clean_text(value: str, *, field: str, max_length: int) -> str:
    if "\x00" in value:
        raise BlockPollerValidationError(f"{field} must not contain null bytes.")
    cleaned = _CONTROL_CHARS_RE.sub("", value)
    if len(cleaned) > max_length:
        raise BlockPollerValidationError(f"{field} exceeds the maximum length of {max_length} characters.")
    return cleaned


def _sanitize_key(key: Any, *, field: str) -> str:
    if not isinstance(key, str):
        raise BlockPollerValidationError(f"{field} keys must be strings.")
    cleaned = _clean_text(
        key,
        field=f"{field} key",
        max_length=settings.BLOCK_POLLER_MAX_IDENTIFIER_LENGTH,
    )
    if not cleaned.strip():
        raise BlockPollerValidationError(f"{field} keys must not be empty.")
    return cleaned


def _sanitize_mapping(value: dict[Any, Any], *, depth: int, field: str) -> dict[str, Any]:
    if len(value) > settings.BLOCK_POLLER_MAX_COLLECTION_ITEMS:
        raise BlockPollerValidationError(f"{field} contains too many keys.")

    sanitized: dict[str, Any] = {}
    for raw_key, raw_item in value.items():
        key = _sanitize_key(raw_key, field=field)
        if key in sanitized:
            raise BlockPollerValidationError(f"{field} contains duplicate keys after sanitization: {key!r}.")
        sanitized[key] = _sanitize_value(raw_item, depth=depth + 1, field=f"{field}.{key}")
    return sanitized


def _sanitize_sequence_value(value: Any, *, depth: int, field: str) -> list[Any]:
    if len(value) > settings.BLOCK_POLLER_MAX_COLLECTION_ITEMS:
        raise BlockPollerValidationError(f"{field} contains too many items.")
    return [_sanitize_value(item, depth=depth + 1, field=f"{field}[{index}]") for index, item in enumerate(value)]


def _sanitize_value(value: Any, *, depth: int, field: str) -> Any:
    if depth > settings.BLOCK_POLLER_MAX_DEPTH:
        raise BlockPollerValidationError(f"{field} exceeds the maximum nesting depth.")

    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value

    if isinstance(value, float):
        if not math.isfinite(value):
            raise BlockPollerValidationError(f"{field} must be a finite number.")
        return value

    if isinstance(value, str):
        return _clean_text(
            value,
            field=field,
            max_length=settings.BLOCK_POLLER_MAX_STRING_LENGTH,
        )

    if isinstance(value, (list, tuple)):
        return _sanitize_sequence_value(value, depth=depth, field=field)

    if isinstance(value, dict):
        return _sanitize_mapping(value, depth=depth, field=field)

    raise BlockPollerValidationError(f"{field} contains an unsupported value of type {type(value).__name__}.")


def _validate_payload_size(payload: Any) -> None:
    try:
        encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise BlockPollerValidationError("Block payload must be JSON serializable.") from exc

    if len(encoded) > settings.BLOCK_POLLER_MAX_PAYLOAD_BYTES:
        raise BlockPollerValidationError(
            f"Block payload exceeds the maximum size of {settings.BLOCK_POLLER_MAX_PAYLOAD_BYTES} bytes."
        )


def _extract_sequence(block: dict[str, Any]) -> int:
    raw_value = next((block[key] for key in _SEQUENCE_FIELDS if key in block), None)
    if raw_value is None:
        raise BlockPollerValidationError("Block payload must include a sequence or ledger number.")

    if isinstance(raw_value, bool):
        raise BlockPollerValidationError("Block sequence must be an integer.")

    if isinstance(raw_value, str):
        candidate = raw_value.strip()
        if not candidate.isdigit():
            raise BlockPollerValidationError("Block sequence must be a non-negative integer.")
        sequence = int(candidate)
    elif isinstance(raw_value, int):
        sequence = raw_value
    else:
        raise BlockPollerValidationError("Block sequence must be an integer.")

    if not 0 <= sequence <= _MAX_SEQUENCE:
        raise BlockPollerValidationError(f"Block sequence must be between 0 and {_MAX_SEQUENCE}.")
    return sequence


def _sanitize_identifier(value: Any, *, field: str) -> str:
    if not isinstance(value, str):
        raise BlockPollerValidationError(f"{field} must be a string.")

    cleaned = _clean_text(
        value,
        field=field,
        max_length=settings.BLOCK_POLLER_MAX_IDENTIFIER_LENGTH,
    ).strip()
    if not cleaned or not _IDENTIFIER_RE.fullmatch(cleaned):
        raise BlockPollerValidationError(f"{field} contains invalid characters.")
    return cleaned


def _sanitize_identifiers(record: dict[str, Any], *, prefix: str) -> dict[str, Any]:
    for key in _IDENTIFIER_FIELDS:
        if key in record:
            record[key] = _sanitize_identifier(
                record[key],
                field=f"{prefix}.{key}",
            )
    return record


def _sanitize_transaction(transaction: dict[str, Any], *, index: int) -> dict[str, Any]:
    return _sanitize_identifiers(transaction, prefix=f"transactions[{index}]")


def _sanitize_transactions(block: dict[str, Any]) -> None:
    transactions = block.get("transactions")
    if transactions is None:
        return

    if not isinstance(transactions, list):
        raise BlockPollerValidationError("Block transactions must be a list.")
    if len(transactions) > settings.BLOCK_POLLER_MAX_TRANSACTIONS:
        raise BlockPollerValidationError(
            f"Block contains more than {settings.BLOCK_POLLER_MAX_TRANSACTIONS} transactions."
        )

    for index, transaction in enumerate(transactions):
        if not isinstance(transaction, dict):
            raise BlockPollerValidationError(f"transactions[{index}] must be an object.")
        transactions[index] = _sanitize_transaction(transaction, index=index)


def sanitize_block_payload(payload: Any) -> dict[str, Any]:
    """Validate and sanitize an untrusted block payload.

    The returned payload is a deep-copied structure with control characters
    removed, length and size limits enforced, and the canonical ``sequence``
    field populated. Invalid payloads raise :class:`BlockPollerValidationError`.
    """
    if not isinstance(payload, dict):
        raise BlockPollerValidationError("Block payload must be an object.")

    _validate_payload_size(payload)
    block = _sanitize_value(payload, depth=0, field="block")
    block["sequence"] = _extract_sequence(block)
    _sanitize_identifiers(block, prefix="block")
    _sanitize_transactions(block)
    return block


class BlockPoller:
    """Poll sequential blocks while honouring rate limits and transient failures."""

    def __init__(
        self,
        fetch_block: Callable[[int], Any],
        process_block: Callable[[dict[str, Any]], None],
        *,
        start_sequence: int = 1,
        poll_interval_seconds: float | None = None,
        max_retries: int | None = None,
        retry_base_delay_seconds: float | None = None,
        retry_max_delay_seconds: float | None = None,
        retry_jitter_ratio: float | None = None,
        rate_limiter: SlidingWindowRateLimiter | None = None,
        sleep: Callable[[float], None] = time.sleep,
        rng: random.Random | None = None,
    ) -> None:
        if not callable(fetch_block) or not callable(process_block):
            raise TypeError("fetch_block and process_block must be callable")
        if start_sequence < 0:
            raise ValueError("start_sequence must be >= 0")

        self._fetch_block = fetch_block
        self._process_block = process_block
        self._next_sequence = start_sequence
        self._poll_interval_seconds = _positive_option(
            poll_interval_seconds if poll_interval_seconds is not None else settings.BLOCK_POLLER_POLL_INTERVAL_SECONDS,
            "poll_interval_seconds",
        )
        self._max_retries = _non_negative_option(
            max_retries if max_retries is not None else settings.BLOCK_POLLER_MAX_RETRIES,
            "max_retries",
        )
        self._retry_base_delay_seconds = _non_negative_option(
            retry_base_delay_seconds
            if retry_base_delay_seconds is not None
            else settings.BLOCK_POLLER_RETRY_BASE_DELAY_SECONDS,
            "retry_base_delay_seconds",
        )
        self._retry_max_delay_seconds = _positive_option(
            retry_max_delay_seconds
            if retry_max_delay_seconds is not None
            else settings.BLOCK_POLLER_RETRY_MAX_DELAY_SECONDS,
            "retry_max_delay_seconds",
        )
        self._retry_jitter_ratio = _jitter_option(
            retry_jitter_ratio if retry_jitter_ratio is not None else settings.BLOCK_POLLER_RETRY_JITTER_RATIO
        )
        self._rate_limiter = rate_limiter or SlidingWindowRateLimiter(
            settings.BLOCK_POLLER_RATE_LIMIT_REQUESTS,
            settings.BLOCK_POLLER_RATE_LIMIT_WINDOW_SECONDS,
        )
        self._sleep = sleep
        self._rng = rng
        self._poll_lock = threading.Lock()
        self._stop_event: threading.Event | None = None
        self._thread: threading.Thread | None = None

    @property
    def next_sequence(self) -> int:
        return self._next_sequence

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def poll_once(self) -> PollResult:
        """Run a single poll cycle."""
        with self._poll_lock:
            return self._poll_once_locked()

    def _poll_once_locked(self) -> PollResult:
        if not self._rate_limiter.try_acquire():
            increment_counter("block_poller_rate_limited_total")
            logger.warning("Block poller rate limit reached; skipping fetch for sequence %d.", self._next_sequence)
            return PollResult(status=PollStatus.RATE_LIMITED)

        block, attempts = self._fetch_with_retry(self._next_sequence)
        self._process_with_metrics(block)

        sequence = block["sequence"]
        self._next_sequence = sequence + 1
        set_gauge("block_poller_next_sequence", float(self._next_sequence))
        increment_counter("block_poller_blocks_processed_total")
        return PollResult(status=PollStatus.PROCESSED, sequence=sequence, attempts=attempts, block=block)

    def _process_with_metrics(self, block: dict[str, Any]) -> None:
        try:
            self._process_block(block)
        except Exception:
            increment_counter("block_poller_processing_failures_total")
            raise

    def _retry_delay(self, attempt_index: int) -> float:
        return compute_retry_delay(
            attempt_index,
            base_delay=self._retry_base_delay_seconds,
            max_delay=self._retry_max_delay_seconds,
            jitter_ratio=self._retry_jitter_ratio,
            rng=self._rng,
        )

    def _fetch_with_retry(self, sequence: int) -> tuple[dict[str, Any], int]:
        attempts = 0
        while True:
            attempts += 1
            try:
                block = sanitize_block_payload(self._fetch_block(sequence))
                return block, attempts
            except BlockPollerValidationError:
                increment_counter("block_poller_validation_failures_total")
                raise
            except Exception as exc:
                if attempts > self._max_retries:
                    increment_counter("block_poller_fetch_failures_total")
                    raise BlockPollerFetchError(f"Failed to fetch block {sequence} after {attempts} attempts.") from exc

                delay = self._retry_delay(attempts - 1)
                increment_counter("block_poller_retries_total")
                logger.warning(
                    "Block %d fetch failed (attempt %d/%d): %s. Retrying in %.2fs.",
                    sequence,
                    attempts,
                    self._max_retries + 1,
                    exc,
                    delay,
                )
                self._sleep(delay)

    def run_forever(self, stop_event: threading.Event | None = None) -> None:
        """Poll until ``stop_event`` is set or :meth:`stop` is called."""
        stop = stop_event or threading.Event()
        while not stop.is_set():
            try:
                self.poll_once()
            except BlockPollerError as exc:
                logger.error("Block poller cycle failed: %s", exc)
            except Exception:
                logger.exception("Unexpected block poller failure.")
            stop.wait(self._poll_interval_seconds)

    def start(self) -> None:
        """Start the poller on a background daemon thread."""
        if self.is_running:
            return
        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=self.run_forever,
            args=(self._stop_event,),
            name="block-poller",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Signal the background thread to stop and wait for it to exit."""
        if self._stop_event is not None:
            self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout)


def _positive_option(value: float, name: str) -> float:
    if value <= 0:
        raise ValueError(f"{name} must be > 0")
    return value


def _non_negative_option(value: float, name: str) -> float:
    if value < 0:
        raise ValueError(f"{name} must be >= 0")
    return value


def _jitter_option(value: float) -> float:
    if not 0 <= value <= 1:
        raise ValueError("retry_jitter_ratio must be between 0 and 1")
    return value
