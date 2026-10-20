# TxIndexer

`TxIndexer` keeps the `payment_transactions` table in step with the Stellar
chain. It reads the Horizon payment stream one bounded page at a time, maps
each record onto the ledger row shape, and inserts it idempotently.

| Piece | Location |
| --- | --- |
| Fetch, retry policy, parsing, `run_once` | `app/services/tx_indexer.py` |
| Idempotent insert | `app/repositories/tx_indexer_repository.py` |
| Configuration | `app/core/config.py`, `.env.example` §15 |

## Ingest contract

```
Horizon /payments --fetch (retry)--> FetchedPage --parse--> IndexedTransaction[] --upsert--> payment_transactions
```

* **One page per pass.** `run_once(cursor)` fetches at most
  `TX_INDEXER_BATCH_SIZE` records and returns
  `IndexResult(fetched, inserted, next_cursor)`. The caller owns the cursor and
  is expected to persist `next_cursor` only after `run_once` returns: the
  ingest is at-least-once, not at-most-once.
* **Idempotent.** The insert is a single PostgreSQL
  `INSERT ... ON CONFLICT (transaction_hash) DO NOTHING`, so a re-delivered
  batch (retry, replay, overlapping runs) inserts zero rows rather than
  raising or duplicating a payment. `IndexResult.inserted` reports the rows
  that were actually new.
* **Strict parsing.** Amounts are converted from Horizon's decimal strings to
  exact stroops (7 decimal places) and timestamps are normalized to
  timezone-aware UTC. A record without a `transaction_hash` cannot take part
  in conflict resolution, so it fails the batch (`TxIndexerError`) instead of
  being stored.
* Indexed rows are prefixed `txidx_` and carry `status="confirmed"`, which
  keeps them distinguishable from settlement rows minted by
  `PaymentRepository` (`pay_...`).

## Retry with backoff (#713)

Horizon answers `429`/`5xx` and drops connections during ordinary operation. A
single blip used to abort the pass, leaving the cursor stalled and every later
payment unindexed. `RetryPolicy` now wraps the fetch:

| Attempt | Delay (defaults, before jitter) |
| --- | --- |
| 1 | immediate |
| 2 | 0.5 s |
| 3 | 1.0 s |
| 4 | 2.0 s |
| 5+ | 4.0 s, 8.0 s, capped at `TX_INDEXER_BACKOFF_MAX_SECONDS` |

* **What is retried:** `httpx` timeout/transport errors and HTTP
  `408, 425, 429, 500, 502, 503, 504`. Everything else — notably a `400` from a
  malformed cursor — fails on the first attempt because it would fail
  identically on every retry.
* **Jitter:** each delay is scaled by a uniform factor in
  `[1 - TX_INDEXER_BACKOFF_JITTER_RATIO, 1 + TX_INDEXER_BACKOFF_JITTER_RATIO]`
  and clamped to the max delay, so a fleet of workers does not retry in
  lockstep against a recovering Horizon.
* **Bounded:** retries are capped by attempt count (`TX_INDEXER_MAX_ATTEMPTS`),
  not elapsed time. Exhaustion raises `TxIndexerRetryExhausted` chained from
  the last transport error; the caller decides whether to log, alert or
  re-schedule.
* Every failed attempt logs the structured event `tx_indexer_retry` with the
  attempt number, delay and error; exhaustion logs
  `tx_indexer_retry_exhausted`.

### Configuration

| Setting | Default | Meaning |
| --- | --- | --- |
| `TX_INDEXER_BATCH_SIZE` | `50` | Payments per page (Horizon caps at 200). |
| `TX_INDEXER_MAX_ATTEMPTS` | `4` | Total attempts per fetch, first try included. |
| `TX_INDEXER_BACKOFF_BASE_SECONDS` | `0.5` | Delay before the first retry. |
| `TX_INDEXER_BACKOFF_MAX_SECONDS` | `8.0` | Ceiling for a single backoff. |
| `TX_INDEXER_BACKOFF_JITTER_RATIO` | `0.5` | ± fraction applied to each delay. |
| `TX_INDEXER_TIMEOUT_SECONDS` | `10.0` | Horizon HTTP timeout. |

Out-of-range values are rejected at startup by `validate_critical_settings`,
so a typo cannot silently turn the indexer into a tight retry loop.

## Operating notes

* Cursor storage is the runner's responsibility. Persist `next_cursor` after a
  successful `run_once`; on `TxIndexerRetryExhausted`, leave the cursor where it
  is and let the next scheduled pass re-fetch the same page — the upsert makes
  the overlap free.
* The retry budget bounds one page, not the whole stream; a sustained Horizon
  outage surfaces as repeated `tx_indexer_retry_exhausted` events rather than a
  hung worker.
* `HorizonPaymentSource` reads `STELLAR_HORIZON_URL` from the environment (see
  `docs/STELLAR_INTEGRATION.md`) and is inert unless the indexer is scheduled.

## Health probe (#714)

`GET /api/v1/indexer/health` reports whether the stream is actually being
indexed. The ingest loop runs in a worker process and the probe is served by
the API process, so the worker publishes a heartbeat to Redis
(`tx_indexer:state`, TTL `TX_INDEXER_STATE_TTL_SECONDS`) after every pass, and
the probe reads it back alongside a `SELECT 1` against the ledger database.

```json
{
  "status": "ok",
  "reason": null,
  "observed_at": "2026-10-20T12:00:00+00:00",
  "last_attempt_at": "2026-10-20T11:59:50+00:00",
  "last_success_at": "2026-10-20T11:59:50+00:00",
  "lag_seconds": 10.0,
  "max_lag_seconds": 120,
  "consecutive_failures": 0,
  "last_error": null,
  "last_inserted": 42,
  "components": {
    "database": {"status": "ok", "details": {}},
    "state_store": {"status": "ok", "details": {}}
  }
}
```

| Status | HTTP | When |
| --- | --- | --- |
| `ok` | 200 | A successful pass within `TX_INDEXER_HEALTH_MAX_LAG_SECONDS`. |
| `degraded` | 200 | No successful pass recorded, the last pass failed, or the heartbeat is stale (`reason` names which). |
| `down` | 503 | The ledger database is unreachable, so no write can land. |

Precedence is deliberate: a database outage outranks indexer state, `degraded`
never returns 503 (a fresh deployment or one failed pass must not drain the API
from the load balancer), and a Redis failure is fail-open — an unreadable
heartbeat reports `no_successful_run`, never `down`. The response is
unauthenticated, like `/health/readiness`; `last_error` is truncated to 240
characters.

## Tests

`tests/test_tx_indexer_retry.py` covers the backoff curve, jitter bounds,
retryable-vs-final classification, exhaustion semantics, the retry path through
`TxIndexer.run_once`, record parsing, and the `ON CONFLICT` dedupe (Postgres
marked). Sleep and RNG are injected, so the suite never waits.

`tests/test_tx_indexer_health.py` covers state transitions and JSON
round-tripping, the fail-open Redis behaviour, every status/reason branch of
the probe, the heartbeat written by `TxIndexer.run_once`, and the endpoint's
200/503 mapping against a stubbed store and database probe.
