"""Stellar Horizon block poller for payment reconciliation (#722-#726).

The poller tails Horizon ledgers, collects the transaction hashes that closed
successfully, and confirms any matching ``payment_transactions`` rows that are
still pending. It is intentionally small and dependency-injected so the
Horizon client, Redis cache and database session can all be replaced in tests.

Design notes
------------
* **Cursor first**: the Horizon paging token is only advanced after the ledger
  transactions have been fetched *and* the database reconciliation has
  committed. A crash mid-poll therefore replays the same ledgers instead of
  skipping payments.
* **Redis cache (#724)**: cursors, per-ledger transaction hashes and the last
  poll summary live in Redis. The per-ledger cache prevents a replayed poll
  from paying Horizon twice for the same ledger; the poller still works if
  Redis is unavailable (the cursor falls back to process memory).
* **Batched writes (#723)**: all hashes collected in one poll are reconciled
  with a single ``UPDATE ... WHERE transaction_hash IN (...)`` per configured
  batch inside one transaction. There is no per-payment SELECT/UPDATE/commit
  loop, and the migration adds a partial index for pending hashes.
* **Metrics (#722)**: poll/ledger/transaction/payment/error counters and a
  last-ledger gauge are exported through the existing Prometheus registry.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Callable, Iterable, Protocol, Sequence

import httpx
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.orm.payment import PaymentTransactionORM
from app.services.metrics import increment_counter, record_timer, set_gauge

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Metric names (#722)
# --------------------------------------------------------------------------- #
BLOCK_POLLER_POLL_TOTAL = "block_poller_poll_total"
BLOCK_POLLER_INITIALIZED_TOTAL = "block_poller_initialized_total"
BLOCK_POLLER_LEDGERS_TOTAL = "block_poller_ledgers_processed_total"
BLOCK_POLLER_TRANSACTIONS_TOTAL = "block_poller_transactions_scanned_total"
BLOCK_POLLER_PAYMENTS_CONFIRMED_TOTAL = "block_poller_payments_confirmed_total"
BLOCK_POLLER_ERRORS_TOTAL = "block_poller_errors_total"
BLOCK_POLLER_CACHE_HITS_TOTAL = "block_poller_cache_hits_total"
BLOCK_POLLER_CACHE_MISSES_TOTAL = "block_poller_cache_misses_total"
BLOCK_POLLER_LAST_LEDGER = "block_poller_last_ledger_sequence"
BLOCK_POLLER_POLL_DURATION_MS = "block_poller_poll_duration_ms"

MAX_LEDGERS_PER_PAGE = 200
MAX_TRANSACTION_PAGES = 10
MAX_TRANSACTIONS_PER_PAGE = 200

_NETWORK_HORIZON_URLS = {
    "testnet": "https://horizon-testnet.stellar.org",
    "mainnet": "https://horizon.stellar.org",
    "futurenet": "https://horizon-futurenet.stellar.org",
    "standalone": "http://localhost:8000",
}


class HorizonError(RuntimeError):
    """Raised when Horizon returns an unusable response."""


@dataclass(frozen=True)
class HorizonLedger:
    sequence: int
    hash: str
    paging_token: str
    closed_at: str | None = None


@dataclass(frozen=True)
class HorizonTransaction:
    hash: str
    successful: bool
    ledger: int | None = None


@dataclass
class PollResult:
    initialized: bool = False
    ledgers_processed: int = 0
    transactions_scanned: int = 0
    payments_confirmed: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    cursor: str | None = None
    last_ledger_sequence: int | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class HorizonClient(Protocol):
    def latest_ledger(self) -> HorizonLedger | None: ...

    def ledgers_after(self, cursor: str, limit: int) -> list[HorizonLedger]: ...

    def transactions_for_ledger(
        self, sequence: int, limit: int = MAX_TRANSACTIONS_PER_PAGE
    ) -> list[HorizonTransaction]: ...


def resolve_horizon_url() -> str:
    """Return the configured Horizon URL or the network default."""
    configured = (getattr(settings, "STELLAR_HORIZON_URL", "") or "").strip()
    if configured:
        return configured.rstrip("/")
    return _NETWORK_HORIZON_URLS.get(settings.STELLAR_NETWORK, _NETWORK_HORIZON_URLS["testnet"])


class StellarHorizonClient:
    """Thin synchronous client for the Horizon endpoints the poller needs."""

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.base_url = (base_url or resolve_horizon_url()).rstrip("/")
        self._owns_client = http_client is None
        self._client = http_client or httpx.Client(
            timeout=timeout if timeout is not None else settings.BLOCK_POLLER_HORIZON_TIMEOUT_SECONDS
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> StellarHorizonClient:
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self._client.get(f"{self.base_url}{path}", params=params)
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError as exc:  # pragma: no cover - defensive
            raise HorizonError(f"Horizon returned non-JSON for {path}") from exc
        if not isinstance(payload, dict):
            raise HorizonError(f"Horizon returned an unexpected payload for {path}")
        return payload

    def latest_ledger(self) -> HorizonLedger | None:
        payload = self._get("/ledgers", {"order": "desc", "limit": 1})
        records = _records(payload)
        return _parse_ledger(records[0]) if records else None

    def ledgers_after(self, cursor: str, limit: int) -> list[HorizonLedger]:
        page_size = max(1, min(int(limit), MAX_LEDGERS_PER_PAGE))
        payload = self._get("/ledgers", {"cursor": cursor, "order": "asc", "limit": page_size})
        return [_parse_ledger(record) for record in _records(payload)]

    def transactions_for_ledger(
        self, sequence: int, limit: int = MAX_TRANSACTIONS_PER_PAGE
    ) -> list[HorizonTransaction]:
        page_size = max(1, min(int(limit), MAX_TRANSACTIONS_PER_PAGE))
        cursor: str | None = None
        transactions: list[HorizonTransaction] = []

        for _ in range(MAX_TRANSACTION_PAGES):
            params: dict[str, Any] = {"order": "asc", "limit": page_size}
            if cursor:
                params["cursor"] = cursor
            payload = self._get(f"/ledgers/{sequence}/transactions", params)
            records = _records(payload)
            if not records:
                break
            transactions.extend(_parse_transaction(record) for record in records)
            if len(records) < page_size:
                break
            cursor = records[-1].get("paging_token")
            if not cursor:
                break

        return transactions


def _records(payload: dict[str, Any]) -> list[dict[str, Any]]:
    embedded = payload.get("_embedded")
    if not isinstance(embedded, dict):
        return []
    records = embedded.get("records")
    if not isinstance(records, list):
        return []
    return [record for record in records if isinstance(record, dict)]


def _parse_ledger(record: dict[str, Any]) -> HorizonLedger:
    try:
        sequence = int(record["sequence"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HorizonError("Horizon ledger record is missing a valid sequence") from exc
    return HorizonLedger(
        sequence=sequence,
        hash=str(record.get("hash") or ""),
        paging_token=str(record.get("paging_token") or sequence),
        closed_at=str(record["closed_at"]) if record.get("closed_at") else None,
    )


def _parse_transaction(record: dict[str, Any]) -> HorizonTransaction:
    tx_hash = record.get("hash")
    if not tx_hash:
        raise HorizonError("Horizon transaction record is missing a hash")
    ledger = record.get("ledger")
    return HorizonTransaction(
        hash=str(tx_hash),
        successful=bool(record.get("successful", False)),
        ledger=int(ledger) if ledger is not None else None,
    )


class BlockPollerCache:
    """Redis-backed cursor, ledger and status cache (#724).

    Every method degrades to a miss when Redis is unavailable: polling is more
    important than caching, and a cache outage must not stop reconciliation.
    """

    def __init__(self, redis_client: Redis, ttl: int | None = None) -> None:
        self._redis = redis_client
        self._ttl = ttl if ttl is not None else settings.BLOCK_POLLER_CACHE_TTL_SECONDS

    @property
    def cursor_key(self) -> str:
        return settings.BLOCK_POLLER_CURSOR_KEY

    def _ledger_key(self, sequence: int) -> str:
        return f"{settings.BLOCK_POLLER_LEDGER_CACHE_PREFIX}:{sequence}"

    @property
    def last_poll_key(self) -> str:
        return settings.BLOCK_POLLER_LAST_POLL_KEY

    def get_cursor(self) -> str | None:
        try:
            raw = self._redis.get(self.cursor_key)
        except (RedisError, OSError) as exc:
            logger.warning("BlockPoller cursor cache unavailable: %s", exc)
            return None
        if raw is None:
            return None
        if isinstance(raw, bytes):
            return raw.decode("utf-8", errors="replace")
        return str(raw)

    def set_cursor(self, cursor: str) -> None:
        try:
            self._redis.set(self.cursor_key, cursor)
        except (RedisError, OSError) as exc:
            logger.warning("BlockPoller could not persist cursor %s: %s", cursor, exc)

    def get_ledger_transactions(self, sequence: int) -> list[str] | None:
        try:
            raw = self._redis.get(self._ledger_key(sequence))
        except (RedisError, OSError) as exc:
            logger.warning("BlockPoller ledger cache unavailable: %s", exc)
            return None
        if raw is None:
            return None
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
            logger.warning("Corrupt BlockPoller ledger cache entry for ledger %s; treating as miss", sequence)
            return None
        if not isinstance(payload, list):
            return None
        return [str(item) for item in payload]

    def set_ledger_transactions(self, sequence: int, transaction_hashes: Sequence[str]) -> None:
        try:
            self._redis.setex(self._ledger_key(sequence), self._ttl, json.dumps(list(transaction_hashes)))
        except (RedisError, OSError) as exc:
            logger.warning("BlockPoller could not cache ledger %s: %s", sequence, exc)

    def get_last_poll(self) -> dict[str, Any] | None:
        try:
            raw = self._redis.get(self.last_poll_key)
        except (RedisError, OSError) as exc:
            logger.warning("BlockPoller status cache unavailable: %s", exc)
            return None
        if raw is None:
            return None
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
            logger.warning("Corrupt BlockPoller status cache entry; treating as miss")
            return None
        return payload if isinstance(payload, dict) else None

    def set_last_poll(self, payload: dict[str, Any]) -> None:
        try:
            self._redis.setex(self.last_poll_key, self._ttl, json.dumps(payload, default=str))
        except (RedisError, OSError) as exc:
            logger.warning("BlockPoller could not persist poll status: %s", exc)


def _chunked(values: Sequence[str], size: int) -> Iterable[Sequence[str]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


class BlockPoller:
    """Reconciles pending Stellar payments against newly closed ledgers."""

    def __init__(
        self,
        db_factory: Callable[[], Session],
        horizon_client: HorizonClient,
        cache: BlockPollerCache,
        *,
        ledger_batch_size: int | None = None,
        db_batch_size: int | None = None,
    ) -> None:
        self._db_factory = db_factory
        self._horizon = horizon_client
        self._cache = cache
        self._ledger_batch_size = ledger_batch_size or settings.BLOCK_POLLER_BATCH_SIZE
        self._db_batch_size = db_batch_size or settings.BLOCK_POLLER_DB_BATCH_SIZE
        self._cursor: str | None = None

    def close(self) -> None:
        """Release the Horizon HTTP client, when the implementation owns one."""
        close = getattr(self._horizon, "close", None)
        if callable(close):
            close()

    def _load_cursor(self) -> str | None:
        cached = self._cache.get_cursor()
        if cached is not None:
            self._cursor = cached
        return self._cursor

    def _save_cursor(self, cursor: str) -> None:
        self._cursor = cursor
        self._cache.set_cursor(cursor)

    def _successful_hashes(self, ledger: HorizonLedger, result: PollResult) -> list[str]:
        cached = self._cache.get_ledger_transactions(ledger.sequence)
        if cached is not None:
            result.cache_hits += 1
            increment_counter(BLOCK_POLLER_CACHE_HITS_TOTAL)
            return cached

        result.cache_misses += 1
        increment_counter(BLOCK_POLLER_CACHE_MISSES_TOTAL)
        transactions = self._horizon.transactions_for_ledger(ledger.sequence)
        result.transactions_scanned += len(transactions)
        hashes = [tx.hash for tx in transactions if tx.successful and tx.hash]
        self._cache.set_ledger_transactions(ledger.sequence, hashes)
        return hashes

    def _reconcile_payments(self, transaction_hashes: Sequence[str]) -> int:
        """Confirm pending payments in batches with one transaction per poll.

        A single ``UPDATE ... WHERE transaction_hash IN (...)`` per batch means
        the number of round-trips grows with ``len(hashes) / batch_size``, not
        with the number of pending payments. The partial index added in
        migration 0033 keeps the lookup on pending rows.
        """
        unique_hashes = list(dict.fromkeys(transaction_hashes))
        if not unique_hashes:
            return 0

        confirmed = 0
        now = datetime.now(UTC)
        db = self._db_factory()
        try:
            for batch in _chunked(unique_hashes, max(1, self._db_batch_size)):
                result = db.execute(
                    update(PaymentTransactionORM)
                    .where(PaymentTransactionORM.transaction_hash.in_(batch))
                    .where(PaymentTransactionORM.status == "pending")
                    .values(status="confirmed", confirmed_at=now)
                )
                confirmed += max(result.rowcount or 0, 0)
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
        return confirmed

    def poll_once(self) -> PollResult:
        """Run one poll cycle and return a summary of the work performed."""
        started = time.monotonic()
        result = PollResult()
        increment_counter(BLOCK_POLLER_POLL_TOTAL)

        try:
            cursor = self._load_cursor()
            if cursor is None:
                latest = self._horizon.latest_ledger()
                if latest is None:
                    return result
                # Seed with the latest ledger and process it immediately so a
                # payment confirmed in the most recent ledger is not missed.
                ledgers = [latest]
                result.initialized = True
                increment_counter(BLOCK_POLLER_INITIALIZED_TOTAL)
            else:
                ledgers = self._horizon.ledgers_after(cursor, self._ledger_batch_size)

            if not ledgers:
                return result

            hashes: list[str] = []
            for ledger in ledgers:
                hashes.extend(self._successful_hashes(ledger, result))

            result.payments_confirmed = self._reconcile_payments(hashes)
            result.ledgers_processed = len(ledgers)
            result.last_ledger_sequence = ledgers[-1].sequence
            result.cursor = ledgers[-1].paging_token

            self._save_cursor(result.cursor)
            set_gauge(BLOCK_POLLER_LAST_LEDGER, float(result.last_ledger_sequence))
            increment_counter(BLOCK_POLLER_LEDGERS_TOTAL, result.ledgers_processed)
            increment_counter(BLOCK_POLLER_TRANSACTIONS_TOTAL, result.transactions_scanned)
            increment_counter(BLOCK_POLLER_PAYMENTS_CONFIRMED_TOTAL, result.payments_confirmed)

            self._cache.set_last_poll(
                {
                    **result.as_dict(),
                    "polled_at": datetime.now(UTC).isoformat(),
                }
            )
            return result
        except Exception as exc:  # noqa: BLE001 - a poll cycle must never crash the worker
            result.error = str(exc)
            increment_counter(BLOCK_POLLER_ERRORS_TOTAL, tags={"reason": type(exc).__name__})
            logger.exception("BlockPoller poll cycle failed: %s", exc)
            # Publish the failure so /block-poller/status can surface it, but
            # keep the last committed cursor instead of reporting a null one.
            self._cache.set_last_poll(
                {
                    **result.as_dict(),
                    "cursor": result.cursor or self._cursor,
                    "polled_at": datetime.now(UTC).isoformat(),
                }
            )
            return result
        finally:
            record_timer(BLOCK_POLLER_POLL_DURATION_MS, (time.monotonic() - started) * 1000)


def build_block_poller_cache() -> BlockPollerCache:
    return BlockPollerCache(Redis.from_url(settings.CELERY_BROKER_URL, decode_responses=True))


def build_block_poller(
    db_factory: Callable[[], Session] | None = None,
    horizon_client: HorizonClient | None = None,
    cache: BlockPollerCache | None = None,
) -> BlockPoller:
    """Construct a production-configured poller."""
    if db_factory is None:
        from app.db.session import SessionLocal

        db_factory = SessionLocal
    return BlockPoller(
        db_factory=db_factory,
        horizon_client=horizon_client or StellarHorizonClient(),
        cache=cache or build_block_poller_cache(),
    )
