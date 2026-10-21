# TxIndexer — Metrics, Query Optimization, Redis Caching and Rate Limiting Resolutions

Resolution notes for the four issues assigned to this contributor.

| Issue | Title | Status in `main` |
| --- | --- | --- |
| [#706](https://github.com/ApexChainx/ApexChainx-Backend/issues/706) | Add Prometheus metrics to TxIndexer | **Open** — metric set, instrumentation points, exposition wiring and tests specified below |
| [#707](https://github.com/ApexChainx/ApexChainx-Backend/issues/707) | Optimize database queries used by TxIndexer | **Open** — current `PaymentRepository` write path audited; batch-upsert, keyset-scan and migration design specified |
| [#708](https://github.com/ApexChainx/ApexChainx-Backend/issues/708) | Add Redis caching for TxIndexer | **Open** — cache surface, keys/TTLs, fail-open policy and invalidation specified |
| [#709](https://github.com/ApexChainx/ApexChainx-Backend/issues/709) | Add rate limiting to TxIndexer | **Open** — outbound RPC pacing and inbound endpoint limits specified |

One finding shapes all four sections: **`TxIndexer` does not exist in this repository today.** `grep -rli "txindexer\|indexer" --include="*.py" --include="*.md"` returns no
matches, and there is no ingestion worker, no indexer table and no indexer
route. The `payment_transactions` table and `PaymentRepository` are the closest
existing primitives. This document therefore records that finding, defines the
component contract the issues assume, and specifies each of the four changes
against the codebase's existing patterns (metrics registry, cache helpers, rate
limiter, Alembic conventions, test layout) so the implementation is mechanical.

---

## Component contract: what `TxIndexer` is

For the purposes of these four issues, `TxIndexer` is the service that tails the
configured Stellar network (Horizon payments endpoint or Soroban RPC events),
normalises each observed payment into `PaymentTransaction`, and persists it into
`payment_transactions` idempotently while maintaining a durable ingestion
watermark. It is distinct from `PaymentRepository` (persistence) and from the
API endpoints (read surface); the indexer is the writer that keeps the table in
sync with the chain.

Suggested layout (matching existing conventions):

| Path | Purpose |
| --- | --- |
| `app/services/tx_indexer.py` | `TxIndexer` service: fetch page → normalise → persist batch → advance watermark; exposes `run_once()` and status fields |
| `app/repositories/tx_indexer_repository.py` | Batched upsert and keyset-scan queries (see #707); or additive methods on `PaymentRepository` |
| `app/tasks/tx_indexer_tasks.py` | Celery beat entry point; single-runner lock via `app/core/lock.py` so overlapping ticks cannot double-ingest |
| `app/api/v1/endpoints/indexer.py` | `GET /api/v1/indexer/status` (engineer-only): watermark, lag, last batch size, error counters |
| `alembic/versions/00XX_tx_indexer_indexes.py` | `ledger_sequence` / `paging_token` columns + composite indexes (see #707) |
| `tests/test_tx_indexer_*.py` | Unit/integration coverage per section below |

Data flow:

```
Stellar network ──► RPC/Horizon page (cursor = paging_token)
                        │  rate-limited (#709), instrumented (#706)
                        ▼
                 normalise → PaymentTransaction (transaction_hash natural key)
                        │  batched upsert, keyset watermark (#707)
                        ▼
             payment_transactions  ◄── read-through cache (#708)
                        │
                        ▼
          GET /api/v1/payments/*  +  GET /api/v1/indexer/status
```

Idempotency and finality: `payment_transactions.transaction_hash` is already
`unique=True` (`app/models/orm/payment.py:10-12`), so re-scanning a ledger
window is safe if writes use `ON CONFLICT DO NOTHING`/`UPDATE`. Stellar ledgers
are final, but the indexer should still re-scan the last `TX_INDEXER_REORG_WINDOW`
ledgers (default 2) on start-up and after a gap, because an RPC page can be
truncated mid-batch.

**If maintainers intend these issues to apply to the existing payment pipeline
rather than a new service,** the same four sections map 1:1: instrument
`app/api/v1/endpoints/payments.py` + `PaymentRepository` (#706), replace the
per-row `create()` commits with the batch path and add the keyset scan (#707),
cache `get_payment` reads (#708), and rate-limit the write/reconcile endpoints
(#709). The specifications below note the mapping where it differs.

---

## #706 — Prometheus metrics

### Metric set

The repository already has an in-process Prometheus-style registry
(`app/services/metrics.py`) with helpers `increment_counter`, `set_gauge`,
`record_histogram`, `timer`, exposed at `GET /api/v1/metrics/prometheus`
(`app/api/v1/endpoints/metrics.py:18`). Add the following families at the
instrumentation points listed:

| Metric | Type | Labels | Instrumentation point |
| --- | --- | --- | --- |
| `tx_indexer_transactions_total` | counter | `result` = `indexed` \| `skipped` \| `failed`; `asset_code` | after each batch persist, per outcome |
| `tx_indexer_rpc_requests_total` | counter | `outcome` = `ok` \| `error` \| `rate_limited` | around each RPC page fetch / retry |
| `tx_indexer_errors_total` | counter | `reason` = `rpc_timeout` \| `rpc_error` \| `decode_error` \| `db_error` | every handled failure path |
| `tx_indexer_ingest_duration_seconds` | histogram | — | whole tick (fetch + normalise + persist) |
| `tx_indexer_rpc_latency_seconds` | histogram | `operation` = `fetch_payments` | per RPC call |
| `tx_indexer_last_indexed_ledger` | gauge | — | after each successful watermark advance |
| `tx_indexer_lag_ledgers` | gauge | — | `network_head_ledger - last_indexed_ledger` each tick |
| `tx_indexer_cache_total` | counter | `result` = `hit` \| `miss` \| `error` | cache wrapper (#708) |

Histogram buckets: reuse `_DEFAULT_LATENCY_BUCKETS`
(`app/services/metrics.py:23`) for RPC latency; use
`[0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]` for a full ingest tick.

### Cardinality rules (must hold)

- **Never** label with `transaction_hash`, wallet address, ledger sequence,
  paging token, or raw error strings — each is unbounded and would create a
  cardinality explosion in the 1,000-sample registry windows.
- Labels are limited to the enumerated values in the table; every other
  dimension belongs in logs, correlated by `correlation_id`
  (`app/utils/correlation_ctx.py`).
- `asset_code` is bounded by configuration (`PAYMENT_ASSET_CODE`, default
  `USDC`); if multiple assets are supported later, cap the label set and use
  `other`.

### Instrumentation sketch

```python
from app.services.metrics import increment_counter, record_histogram, set_gauge, timer

def run_once(self) -> None:
    with timer("tx_indexer_ingest_duration_seconds") as stop:
        try:
            page = self._fetch_page()  # #709 limiter wraps this call
            rows, skipped = self._normalise(page)
            inserted = self._persist(rows)  # #707 batched upsert
        except RpcError as exc:
            increment_counter("tx_indexer_errors_total", tags={"reason": exc.reason})
            raise
        finally:
            stop()

    increment_counter(
        "tx_indexer_transactions_total",
        value=inserted,
        tags={"result": "indexed", "asset_code": self.asset_code},
    )
    increment_counter(
        "tx_indexer_transactions_total",
        value=skipped,
        tags={"result": "skipped", "asset_code": self.asset_code},
    )
    set_gauge("tx_indexer_last_indexed_ledger", float(self.watermark.ledger))
    set_gauge("tx_indexer_lag_ledgers", float(self.network_head - self.watermark.ledger))
```

Exposition wiring: the Prometheus endpoint builds HELP text from a prefix map
(`app/api/v1/endpoints/metrics.py:35-50`). Add entries so the new families ship
with `# HELP`/`# TYPE` blocks:

```python
"tx_indexer_transactions_total": "Transactions persisted by the indexer, by result",
"tx_indexer_rpc_requests_total": "Stellar RPC page fetches made by the indexer, by outcome",
"tx_indexer_errors_total": "TxIndexer error events, by reason",
"tx_indexer_ingest_duration_seconds": "Wall time for one TxIndexer tick",
"tx_indexer_rpc_latency_seconds": "Latency of TxIndexer RPC calls",
"tx_indexer_last_indexed_ledger": "Highest ledger persisted by the indexer",
"tx_indexer_lag_ledgers": "Ledger distance between network head and indexer watermark",
"tx_indexer_cache_total": "TxIndexer cache outcomes, by result",
```

### Tests

`tests/test_tx_indexer_metrics.py`:

1. Successful tick increments `tx_indexer_transactions_total{result="indexed"}`
   by the persisted row count and sets both gauges.
2. A duplicate-only batch increments `{result="skipped"}` and does not move the
   watermark backwards.
3. Each failure path increments `tx_indexer_errors_total` with the right
   `reason`, and `tx_indexer_rpc_requests_total{outcome=...}` matches the call
   count.
4. Histograms have a non-zero `_count` after a tick and land in sensible
   buckets (assert on `get_metrics_summary()` keys).
5. `GET /api/v1/metrics/prometheus` (engineer auth) contains `tx_indexer_` and
   the `# TYPE` lines after a tick — this also guards the HELP map addition.
6. Cardinality guard: a test scans `get_metrics_summary()` labels and fails if
   any value matches a 64-hex transaction-hash pattern or a Stellar `G...`
   address.

### Docs

- `README.md` observability section: table of the families above, and the
  note that `/api/v1/metrics/prometheus` requires the engineer role.
- `docs/RUNBOOK.md`: alert thresholds — `tx_indexer_lag_ledgers > 25` for 5
  minutes (page), `rate(tx_indexer_errors_total[5m]) > 0.2` (warn), cache
  `error` rate > 5% (warn).

---

## #707 — database query optimization

### Current write path (the problem)

`PaymentRepository.create()` (`app/repositories/payment_repository.py:34-57`)
performs one `INSERT` + `commit()` + `refresh()` **per row**. For an indexer
batch of 500 payments that is 1,500 round trips and 500 commits; each commit
flushes the ORM identity map and can hold a pooled connection for the whole
batch. `list()` also uses `OFFSET` pagination (`:80-121`), which degrades
linearly on large tables. Neither path is acceptable for ingestion.

### Proposed queries

1. **Batched upsert** — one statement per chunk (default 500 rows) using the
   PostgreSQL dialect:

   ```python
   from sqlalchemy.dialects.postgresql import insert as pg_insert

   def bulk_upsert(self, rows: list[dict], chunk_size: int = 500) -> int:
       inserted = 0
       for start in range(0, len(rows), chunk_size):
           chunk = rows[start : start + chunk_size]
           stmt = (
               pg_insert(PaymentTransactionORM)
               .values(chunk)
               .on_conflict_do_nothing(index_elements=["transaction_hash"])
               .returning(PaymentTransactionORM.id)
           )
           inserted += len(self.db.execute(stmt).scalars().all())
           self.db.commit()
       return inserted
   ```

   `RETURNING id` distinguishes inserted from skipped rows without a follow-up
   `SELECT`. The unique index on `transaction_hash` already exists — no new
   uniqueness constraint is needed.

2. **Keyset watermark scan** — replace cursor-in-memory paging with a
   composite index and a tuple comparison:

   ```sql
   SELECT id, transaction_hash, ledger_sequence, paging_token
   FROM payment_transactions
   WHERE (ledger_sequence, paging_token) > (:ledger, :token)
   ORDER BY ledger_sequence, paging_token
   LIMIT :batch_size;
   ```

3. **Status reconciliation in one statement** — `UPDATE payment_transactions
   SET status = :status, confirmed_at = :ts WHERE id = ANY(:ids)` rather than
   per-row `update_status()` loads/refreshes.

4. **Read-path hygiene** — the hot lookup by hash should project only the
   needed columns and hit the unique index; list pagination stays as-is for the
   API but the indexer never uses `OFFSET`.

### Migration

New Alembic revision on the current head (the graph has parallel heads and has
needed a merge revision before — see
`alembic/versions/218a9af9d890_merge_multiple_heads.py`; verify with
`alembic heads` before writing it):

```python
"""tx indexer ledger scan columns and indexes

Revision ID: 00XX_tx_indexer_ledger_scan
"""
# raw-sql-allowed  (scripts/lint_migrations.py requires this marker for op.execute)

def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TABLE payment_transactions "
            "ADD COLUMN IF NOT EXISTS ledger_sequence BIGINT"
        )
        op.execute(
            "ALTER TABLE payment_transactions "
            "ADD COLUMN IF NOT EXISTS paging_token VARCHAR(128)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_payment_tx_ledger_scan "
            "ON payment_transactions (ledger_sequence, paging_token)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_payment_tx_hash_lookup "
            "ON payment_transactions (transaction_hash)"
        )
```

`CREATE INDEX CONCURRENTLY` must stay inside an autocommit block (same
convention as `alembic/versions/0023_payment_tx_indexes.py`) so deploys do not
lock the payments table. The existing composite index
`ix_payment_tx_status_created (status, created_at DESC)` from migration 0023
covers the status/date listing used by the API.

### Acceptance targets

Measure with `EXPLAIN (ANALYZE, BUFFERS)` against a seeded dataset (≥100k rows,
realistic asset/status distribution):

| Target | Acceptance |
| --- | --- |
| Hash lookup | `Index Scan` on `ix_payment_tx_hash_lookup`, no `Seq Scan` |
| Watermark scan | `Index Scan` on `ix_payment_tx_ledger_scan`, rows examined ≈ limit |
| Batch persist | 500 rows in one statement per chunk, ≤ 2 commits per tick |
| Tick latency (local) | p95 ingest-duration < 250 ms for a 500-row batch |
| Commit count | ≤ 2 per batch (persist + watermark) |

Record the measured baseline in `docs/PERFORMANCE.md` (the doc's table format
already tracks contract/API numbers) and add a pytest-benchmark entry in
`tests/benchmarks/test_benchmarks.py` (or a sibling
`tests/benchmarks/test_tx_indexer_queries.py`) mirroring the existing
`pytest-benchmark` fixture style; the nightly job already runs
`pytest tests/benchmarks` (`.github/workflows/pr.yml:168-170`), and
`scripts/check_performance_regression.py` tracks the 1.2× median rule.

### Tests

`tests/test_tx_indexer_repository.py`:

1. `bulk_upsert` inserts N rows with exactly one statement per chunk and
   returns N.
2. Re-running the same batch returns 0 inserted and leaves row count unchanged
   (idempotency on `transaction_hash`).
3. Mixed insert/duplicate batch returns only the inserted count.
4. Keyset scan returns pages in `(ledger_sequence, paging_token)` order with no
   overlap and no gaps across page boundaries.
5. `EXPLAIN` assertions (Postgres-only, skipped on SQLite): the watermark query
   plan contains `ix_payment_tx_ledger_scan`; the hash query plan contains
   `ix_payment_tx_hash_lookup`.
6. Reconciliation update touches only the given ids and sets
   `confirmed_at` consistently with `validate_transition` (no illegal
   transitions).

---

## #708 — Redis caching

### What to cache (and what not to)

| Key | Value | TTL | Invalidated by |
| --- | --- | --- | --- |
| `txindexer:tx:{network}:{hash}` | JSON of the `PaymentTransaction` read model | `TX_INDEXER_CACHE_TTL_SECONDS` (default 30) | upsert of that hash; status reconciliation |
| `txindexer:status` | status payload for `GET /api/v1/indexer/status` | 5 s | watermark advance / error tick |
| `txindexer:head:{network}` | last observed network head ledger | 10 s | each successful head fetch |

The **watermark is not cached as an authority** — it is persisted in the
database (single source of truth). Redis may mirror it for status rendering,
but ingestion always reads the durable value. This avoids losing progress on
Redis eviction and keeps the indexer correct when the cache is empty.

### Helper (mirrors the existing cache classes)

Follow the fail-open pattern documented in `app/services/user_cache.py` and
`app/services/wallet_cache.py` — a thin JSON wrapper where any `RedisError` is
logged and treated as a miss:

```python
"""Redis read-through cache for TxIndexer lookups (#708)."""

from __future__ import annotations

import json
import logging

from redis import Redis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)

DEFAULT_TTL_SECONDS = 30


class TxIndexerCache:
    def __init__(self, redis_client: Redis, ttl: int = DEFAULT_TTL_SECONDS) -> None:
        self._redis = redis_client
        self._ttl = ttl

    @staticmethod
    def _tx_key(network: str, tx_hash: str) -> str:
        return f"txindexer:tx:{network}:{tx_hash}"

    def get_tx(self, network: str, tx_hash: str) -> dict | None:
        try:
            raw = self._redis.get(self._tx_key(network, tx_hash))
        except RedisError as exc:
            logger.warning("TxIndexer cache read failed for %s: %s", tx_hash, exc)
            return None
        if not raw:
            return None
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            logger.warning("Corrupt JSON in TxIndexer cache for %s, treating as miss", tx_hash)
            return None

    def set_tx(self, network: str, tx_hash: str, data: dict) -> None:
        try:
            self._redis.setex(
                self._tx_key(network, tx_hash), self._ttl, json.dumps(data, default=str)
            )
        except RedisError as exc:
            logger.warning("TxIndexer cache write failed for %s: %s", tx_hash, exc)

    def invalidate_txs(self, network: str, tx_hashes: list[str]) -> None:
        if not tx_hashes:
            return
        try:
            pipe = self._redis.pipeline()
            for tx_hash in tx_hashes:
                pipe.delete(self._tx_key(network, tx_hash))
            pipe.execute()
        except RedisError as exc:
            logger.warning("TxIndexer cache invalidation failed: %s", exc)
```

Wiring rules:

- The cache is optional and flag-gated: `TX_INDEXER_CACHE_ENABLED` (default
  true), `TX_INDEXER_CACHE_TTL_SECONDS` (default 30) in
  `app/core/config.py` — **and mirrored in `.env.example`**, because
  `tests/test_env_example_completeness.py` fails on an undocumented setting.
- Client: pass an injected `Redis` instance (testable with `fakeredis`, as the
  rate-limiter tests do) created from the same broker URL used by the other
  caches (`settings.CELERY_BROKER_URL`); do not create a new connection per
  request.
- Stampede protection for hot `get_by_hash` reads: reuse the single-flight
  shape from `app/services/sla_singleflight.py` (acquire a short Redis lock,
  re-check the cache, only then hit the database).
- Every wrapper call increments `tx_indexer_cache_total{result=hit|miss|error}`
  (#706), so the cache's effectiveness is observable.
- Payloads are the Pydantic `PaymentTransaction` JSON (datetimes serialised with
  `default=str`, consistent with `user_cache.py`'s convention).

### Tests

`tests/test_tx_indexer_cache.py` (fakeredis, mirroring
`tests/test_rate_limiter_redis.py`):

1. Hit after `set_tx`; payload round-trips datetimes as ISO strings.
2. Miss on unknown key; miss on corrupt JSON (no exception, warning logged).
3. `RedisError` on get/set/invalidate is swallowed and logged; callers still see
   a database result (fail-open).
4. `invalidate_txs` deletes every key in one pipeline.
5. TTL is applied (`ttl()` within ±1 s of configured value).
6. Cache metric labels increment `hit`/`miss`/`error` correctly.

---

## #709 — rate limiting

The P0 label on this issue makes sense once the indexer is described: an
unpaced indexer can exhaust the Stellar RPC/Horizon budget and take the whole
backend's chain access down with it. Rate limiting therefore has two distinct
surfaces.

### A. Outbound RPC pacing (primary)

- Wrap every page fetch and retry in a **distributed token-bucket / sliding
  window** backed by Redis so multiple Celery workers and API replicas share
  one budget. Reuse the existing sorted-set Lua approach in
  `app/core/rate_limiter.py` (`RATE_LIMITER_LUA`) — it is already proven in
  production and handles key expiry — but parameterise the window/limit instead
  of hardcoding the auth settings. A small `OutboundRateLimiter` class (or
  optional `limit`/`window` arguments on `RedisRateLimiter`) avoids coupling
  indexer limits to `AUTH_RATE_LIMIT_*`.
- New settings: `TX_INDEXER_RPC_RATE_LIMIT` (default 20 requests),
  `TX_INDEXER_RPC_RATE_WINDOW_SECONDS` (default 1), and
  `TX_INDEXER_RPC_MAX_RETRIES` (default 3).
- On limit exhaustion: sleep the remainder of the window with jitter rather
  than dropping the page (the indexer is a background task and can wait). On
  upstream HTTP 429, honour `Retry-After` when present and back off
  exponentially (`base * 2**attempt`, capped) — do not hammer.
- Resilience: if Redis is unavailable, fall back to the in-process
  `SimpleRateLimiter` behaviour (as `RedisRateLimiter` already does) and trip
  the 30-second circuit (`_trip_circuit`), logging a warning. Availability of
  the indexer must not depend on Redis, but neither may it run unpaced when
  Redis is flaky — the in-process fallback still limits the worker.
- Instrument every call as `tx_indexer_rpc_requests_total{outcome=ok|error|rate_limited}`
  and time it in `tx_indexer_rpc_latency_seconds`.

### B. Inbound endpoints (secondary)

- `GET /api/v1/indexer/status` (engineer-only) and any manual
  `POST /api/v1/indexer/reindex` are limited per user via the existing
  `rate_limiter.is_allowed(f"indexer_status_{user.id}")` call shape used in
  `app/api/v1/endpoints/auth.py:333,574`.
- On rejection return `CodedHTTPException(status_code=429, ...)` — the error
  code map already declares `429: "rate_limited"` in
  `app/api/coded_errors.py:38` — plus a `Retry-After` header consistent with
  the limiter window.
- Do not add a per-IP limit on the engineer endpoints: they are already
  role-gated, and per-user identity is the relevant dimension.

### Tests

`tests/test_tx_indexer_rate_limit.py`:

1. Outbound limiter allows exactly `limit` calls per window, rejects the next,
   and allows again after the window rolls (fake clock, fakeredis).
2. `Retry-After` / backoff schedule: a 429 response sets the next attempt at
   `now + retry_after` (\(\pm\) jitter bounds); exponential backoff is capped.
3. Redis failure falls back to the in-process limiter and trips the circuit;
   a subsequent call inside 30 s does not retry Redis.
4. Limiter keys expire (`ttl()` ≥ window, ≤ window + margin).
5. Inbound: 429 after the per-user limit with the coded error body and
   `Retry-After`; a different user is unaffected.
6. Multi-worker semantics: two limiter instances sharing fakeredis enforce one
   combined budget (mirrors `tests/test_rate_limiter_multiworker.py`).

---

## Acceptance-criteria mapping

Every issue's checklist has the same four items. The table below shows where
each is satisfied by the work above.

| AC | #706 | #707 | #708 | #709 |
| --- | --- | --- | --- | --- |
| Implement in TxIndexer | metric families + instrumentation in `tx_indexer.py` | `bulk_upsert`, keyset scan, migration | `TxIndexerCache` + read-through wiring | outbound limiter + inbound endpoint gate |
| Add/adjust automated tests | `test_tx_indexer_metrics.py` | `test_tx_indexer_repository.py` | `test_tx_indexer_cache.py` | `test_tx_indexer_rate_limit.py` |
| Update relevant documentation | README observability + RUNBOOK alerts | `docs/PERFORMANCE.md` baseline + schema notes | `.env.example`, README cache section | `.env.example`, README config table, RUNBOOK |
| CI passes on the PR | unit job (`.github/workflows/pr.yml:40-116`); benchmarks run nightly (`:121-174`) | benchmark job + migration lint (`scripts/lint_migrations.py`) | unit job (fakeredis, no service needed) | unit job + multiworker tests |

Additional CI gates to satisfy when the code lands:

- `tests/test_env_example_completeness.py` — every new setting must appear in
  `.env.example`.
- `tests/check_doc_drift.ts` — any new route (`/api/v1/indexer/status`, manual
  reindex) must be mentioned in `README.md`.
- `scripts/lint_test_filenames.py` — new tests must follow `test_*.py`.
- `docs/openapi.snapshot.json` / `scripts/openapi_diff.py` — regenerate the
  snapshot if the new endpoints are added (per CONTRIBUTING).

## New settings summary

| Setting | Default | Used by |
| --- | --- | --- |
| `TX_INDEXER_ENABLED` | `false` (safe rollout) | task registration |
| `TX_INDEXER_BATCH_SIZE` | `500` | #707 |
| `TX_INDEXER_POLL_INTERVAL_SECONDS` | `5` | task schedule |
| `TX_INDEXER_CACHE_ENABLED` | `true` | #708 |
| `TX_INDEXER_CACHE_TTL_SECONDS` | `30` | #708 |
| `TX_INDEXER_RPC_RATE_LIMIT` | `20` | #709 |
| `TX_INDEXER_RPC_RATE_WINDOW_SECONDS` | `1` | #709 |
| `TX_INDEXER_RPC_MAX_RETRIES` | `3` | #709 |
| `TX_INDEXER_REORG_WINDOW` | `2` | re-scan depth |

## Rollout and backfill

1. Ship with `TX_INDEXER_ENABLED=false` and the migration applied
   (`alembic upgrade head`, index build concurrent).
2. Backfill from the contract deployment ledger using the batched upsert; the
   idempotent `transaction_hash` conflict handling makes restarts safe.
3. Persist the watermark only after a batch commits, so a crash re-processes at
   most one batch.
4. Enable the scheduler; watch `tx_indexer_lag_ledgers` and
   `tx_indexer_errors_total` on the runbook alerts from #706.
5. Tune `TX_INDEXER_RPC_RATE_LIMIT` against the RPC provider's quota; the
   limiter is the guardrail, not a substitute for a provider-side quota.

## Note on scope

This PR is documentation only, per the agreed approach. #706–#709 are specified
to the level of concrete modules, metrics, SQL/DDL, cache keys, limiter
semantics, tests and CI gates; the follow-up implementation PRs can proceed
mechanically. The key finding recorded here is that `TxIndexer` itself must be
introduced first — the four issues describe its non-functional requirements,
not edits to an existing module.
