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

## Query path hardening (#716)

The read side (`GET /api/v1/indexer/transactions`) accepts eleven optional
filters. Everything a caller sends is untrusted, so the path is built in two
layers:

1. **`app/services/tx_indexer_query.py` — validate, then bind.**
   - `transaction_hash`: 64 hex characters, lowercased. Anything else (including
     `'; DROP TABLE ...`) is a `422`.
   - `account`: a syntactically valid Stellar public key, normalized through
     `app.utils.wallet_address.normalize`; it is compared against
     `from_address` **or** `to_address`.
   - `asset_code`: 1-12 alphanumerics, uppercased.
   - `status`: one of `pending`, `confirmed`, `failed` (from `PaymentStatus`).
   - `type`: lowercase label grammar `^[a-z][a-z0-9_]{0,31}$`.
   - `outage_id`: `^[A-Za-z0-9_-]{1,64}$`.
   - `min_amount` / `max_amount`: coerced to exact `BIGINT` stroops, range
     checked, and `min <= max` enforced. Floats, booleans and `2**63` are
     rejected.
   - `created_after` / `created_before`: real `datetime`s with the ordering
     enforced.
   - `sort_by`: resolved through the `{created_at, confirmed_at, amount,
     status}` whitelist; `sort_dir` through `{asc, desc}`. The repository looks
     the **whitelisted name** up in its column map, so a caller can never name a
     column (or inject SQL through the order-by).
   - `limit`: 1-200.
2. **Repository binds every value.** `TxIndexerRepository.search` filters with
   SQLAlchemy comparison operators (`==`, `>=`, `<=`, `OR`), i.e. every value
   becomes a statement parameter. There is no `text()` or f-string SQL in the
   query path, and the ORDER BY clause is a column object from the map above.

The endpoint maps `TxIndexerQueryError` to a coded `422` with the reason, so a
bad filter is a clear client error rather than a 500 — and a rejected filter
never reaches the database.

`escape_like()` is exported for any future prefix search: `%`, `_` and `\` are
escaped so user text cannot turn a bounded lookup into a full scan.

## Distributed tracing (#715)

Spans use the project-wide OTel setup (`app/core/tracing.py`), which is
initialised once in `app/main.py` and exported over OTLP when
`OTEL_EXPORTER_OTLP_ENDPOINT` is set. One ingest pass is one trace:

| Span | Parent | Key attributes |
| --- | --- | --- |
| `tx_indexer.run_once` | (root of the pass) | `tx_indexer.component`, `tx_indexer.cursor_present`, `tx_indexer.fetched`, `tx_indexer.inserted` |
| `tx_indexer.fetch_page` | `run_once` | `tx_indexer.batch_size`, `tx_indexer.cursor_present`, `tx_indexer.next_cursor_present` |
| `tx_indexer.persist` | `run_once` | `tx_indexer.fetched`, `tx_indexer.inserted` |

* **Retries are span events**, not spans: each backoff adds a `retry` event on
  `fetch_page` with `attempt`, `delay_seconds` and `error.type`, so a flappy
  Horizon shows up as a single span with a burst of events instead of a span
  forest.
* **Failures** record the exception (`exception` event) and set the span status
  to `ERROR`; the pass and the failing child both carry it, so a trace search
  for `ERROR` finds the ingest, not just the leaf.
* **Context propagation:** the active W3C `traceparent` is sent on the Horizon
  request, so an instrumented upstream joins the same trace. No active span
  means no header — the client stays a plain HTTP call.
* The tracer is injectable (`TxIndexer(..., tracer=...)`); production uses the
  module tracer and tests assert span shape against an in-memory exporter
  without touching the process-global provider.

> Note: `get_current_traceparent()` previously emitted an extra `0` in front of
> the flags field (`...-001`), which is not a valid W3C `traceparent`, so
> receivers dropped the context. This PR fixes the header to the 2-hex flags
> form; it also repairs propagation for webhook deliveries, which use the same
> helper.

## Tests

`tests/test_tx_indexer_retry.py` covers the backoff curve, jitter bounds,
retryable-vs-final classification, exhaustion semantics, the retry path through
`TxIndexer.run_once`, record parsing, and the `ON CONFLICT` dedupe (Postgres
marked). Sleep and RNG are injected, so the suite never waits.

`tests/test_tx_indexer_health.py` covers state transitions and JSON
round-tripping, the fail-open Redis behaviour, every status/reason branch of
the probe, the heartbeat written by `TxIndexer.run_once`, and the endpoint's
200/503 mapping against a stubbed store and database probe.

`tests/test_tx_indexer_sql_safety.py` covers the validators (including
injection payloads in every field), inverted ranges, the sort whitelist, the
`LIKE` escaping helper, filtered search against real rows, a compiled-statement
assertion that the hash value is a bind parameter and absent from the SQL text,
and the endpoint's `422` mapping — each followed by a `SELECT count(*)` proving
the table survived.

`tests/test_tx_indexer_tracing.py` asserts the span tree (names, shared trace
id, parent span id), pass attributes, retry events, `ERROR` status and recorded
exceptions on fetch/sink failures, `traceparent` forwarding (and absence without
an active span), and that a non-recording tracer changes nothing.
