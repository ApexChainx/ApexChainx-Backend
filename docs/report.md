# BlockPoller

`BlockPoller` tails Stellar Horizon ledgers and confirms pending
`payment_transactions` rows whose transaction hash closed successfully.

It addresses four tracked issues:

| Issue | Scope |
| --- | --- |
| #722 | Prometheus metrics for poll cycles, ledgers, transactions, confirmations, cache and errors |
| #723 | Batched reconciliation query plus a partial index for pending transaction hashes |
| #724 | Redis cache for the cursor, per-ledger transaction hashes and the last poll summary |
| #726 | OpenAPI/Swagger documentation for the operator status endpoint |

## Flow

1. Celery beat dispatches `app.tasks.block_poller_tasks.poll_stellar_blocks`
   every `BLOCK_POLLER_INTERVAL_SECONDS`.
2. The task exits immediately unless `BLOCK_POLLER_ENABLED=true`.
3. `BlockPoller.poll_once()` loads the Horizon paging token from Redis. On a
   cold start it reads the latest ledger, processes it immediately, and seeds
   the cursor there instead of replaying the whole chain.
4. Ledgers after the cursor are fetched with `GET /ledgers`.
5. For each ledger, successful transaction hashes are fetched from
   `GET /ledgers/{sequence}/transactions`. The hash list is cached in Redis so
   a replayed poll does not call Horizon twice for the same ledger.
6. All hashes from the poll are reconciled in one transaction using a batched
   `UPDATE ... WHERE transaction_hash IN (...) AND status = 'pending'`.
7. Only after the database commit does the poller advance the cursor and
   publish the last-poll summary to Redis.

A crash before step 7 replays the same ledgers. Reconciliation is idempotent,
so replaying cannot double-confirm a payment.

## Configuration

All settings live in `app/core/config.py` and are documented in `.env.example`.

| Setting | Default | Purpose |
| --- | --- | --- |
| `STELLAR_HORIZON_URL` | network default | Horizon endpoint. Empty uses `testnet`, `mainnet`, `futurenet` or `standalone` defaults. |
| `BLOCK_POLLER_ENABLED` | `false` | Enables the Celery task. |
| `BLOCK_POLLER_INTERVAL_SECONDS` | `15` | Beat interval. |
| `BLOCK_POLLER_BATCH_SIZE` | `20` | Ledgers fetched per poll. |
| `BLOCK_POLLER_DB_BATCH_SIZE` | `500` | Hashes per reconciliation `UPDATE`. |
| `BLOCK_POLLER_CACHE_TTL_SECONDS` | `300` | TTL for ledger transaction caches and last-poll status. |
| `BLOCK_POLLER_HORIZON_TIMEOUT_SECONDS` | `10` | Horizon HTTP timeout. |
| `BLOCK_POLLER_CURSOR_KEY` | `block_poller:cursor` | Redis cursor key. |
| `BLOCK_POLLER_LEDGER_CACHE_PREFIX` | `block_poller:ledger` | Redis per-ledger cache prefix. |
| `BLOCK_POLLER_LAST_POLL_KEY` | `block_poller:last_poll` | Redis last-poll summary key. |

The poller reuses `CELERY_BROKER_URL` as its Redis connection string, matching
the other Redis-backed services in this codebase.

## Metrics

Exposed through `GET /api/v1/metrics` (JSON) and
`GET /api/v1/metrics/prometheus` (text):

- `block_poller_poll_total`
- `block_poller_initialized_total`
- `block_poller_ledgers_processed_total`
- `block_poller_transactions_scanned_total`
- `block_poller_payments_confirmed_total`
- `block_poller_errors_total{reason}`
- `block_poller_cache_hits_total`
- `block_poller_cache_misses_total`
- `block_poller_last_ledger_sequence` (gauge)
- `block_poller_poll_duration_ms` (timer/histogram)

## Status endpoint

`GET /api/v1/block-poller/status` requires the `engineer` role and returns the
cached last-poll summary. It does not call Horizon or PostgreSQL. The response
model is published in the generated OpenAPI schema and Swagger UI.

Example:

```json
{
  "enabled": true,
  "cursor": "51234567",
  "last_poll_at": "2026-10-20T15:30:00+00:00",
  "last_ledger_sequence": 51234567,
  "ledgers_processed": 3,
  "transactions_scanned": 42,
  "payments_confirmed": 2,
  "cache_hits": 1,
  "cache_misses": 2,
  "error": null
}
```

## Operational notes

- **Redis outage**: the poller keeps working using an in-process cursor. New
  polls still reconcile, but a worker restart loses the cursor and triggers a
  cold start from the latest ledger.
- **Horizon outage**: the poll cycle logs a warning, increments
  `block_poller_errors_total`, and returns without advancing the cursor. The
  next beat tick retries the same ledgers.
- **Database failure**: the transaction rolls back and the cursor is not
  advanced, so the next poll retries.
- **Reorgs**: only successful transactions confirm payments. A later ledger
  that no longer contains a hash is not treated as a failure; use the payment
  reconciliation endpoint for manual status changes.
- **Index**: migration `0033_block_poller_pending_hash_index` adds the partial
  pending-hash index used by the batched update.
