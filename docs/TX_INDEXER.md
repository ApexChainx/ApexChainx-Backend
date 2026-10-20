# TxIndexer

TxIndexer keeps a queryable copy of Stellar payments in the backend's ledger of
record, so partners and operators can list a transaction without calling
Horizon themselves.

Issues: #710 (OpenAPI/Swagger documentation).

## Where it lives

| Concern | Module |
|---------|--------|
| Ingest: Horizon fetch → normalize → sink | `app/services/tx_indexer.py` |
| Persistence and read queries | `app/repositories/tx_indexer_repository.py` |
| Read API | `app/api/v1/endpoints/indexer.py` |
| Scheduled entrypoint | `app/tasks/tx_indexer_tasks.py` |

The index is **`payment_transactions`**, the table the payments API already
reads. It is unique on `transaction_hash`, so re-delivering a Horizon page is
idempotent: the insert is a single `ON CONFLICT DO NOTHING` statement keyed on
that column.

## Ingest flow

1. `HorizonClient.fetch_payments(cursor, limit)` reads
   `GET {TX_INDEXER_HORIZON_URL}/payments?order=asc&limit=…` and returns the
   page plus the `paging_token` of its last record.
2. `IndexedTransaction.from_horizon_record` normalizes each record:

   | Horizon field | Stored as | Notes |
   |---------------|-----------|-------|
   | `transaction_hash` | `transaction_hash` | Required; the unique key |
   | `amount` (decimal string) | `amount` (integer) | Converted to stroops (×10⁷) |
   | `asset_code` | `asset_code` | `XLM` for native payments |
   | `from` / `source_account` / `funder` | `from_address` | First present wins |
   | `to` / `account` | `to_address` | First present wins |
   | `created_at` | `created_at` | Ledger close time, UTC |
   | `type` | `type` | Horizon operation type |

3. A record that cannot be normalized (no hash, unparseable or negative
   amount, no account pair) is logged as `tx_indexer_skipped_record` and
   skipped. It never reaches the sink.
4. `TxIndexerRepository.upsert_many` inserts the page; already-indexed hashes
   insert zero rows.
5. The pass returns an `IngestResult` whose `cursor` is the position to resume
   from.

### One row per transaction

Horizon streams *operations*, and one transaction can contain several payment
operations. The index is keyed by transaction, so the first payment operation
seen for a transaction wins and the rest are collapsed into it. One row per
operation would need a dedicated table and a migration; collapsing keeps the
index consistent with the payments API.

## Configuration

| Variable | Default | Meaning |
|----------|---------|---------|
| `TX_INDEXER_HORIZON_URL` | `https://horizon-testnet.stellar.org` | Horizon base URL. Must not be empty |
| `TX_INDEXER_BATCH_SIZE` | `200` | Payments per pass, 1–200 (Horizon's page cap) |

Both are validated by `validate_critical_settings()` at startup, and both are
listed in `.env.example`.

## Running an ingest pass

```python
from app.tasks.tx_indexer_tasks import index_transactions

result = index_transactions.delay(cursor=None).get()
# {"fetched": 200, "stored": 197, "skipped": 0, "cursor": "1234567890"}
```

The task is intentionally **not** on the Celery beat schedule: a deployment
runs it from its own scheduler and persists the returned `cursor`. Passing the
previous cursor replays nothing; passing `None` re-walks the stream from the
beginning (safe, because the write is idempotent).

## Read API

Both routes require an engineer or admin bearer token.

| Route | Purpose |
|-------|---------|
| `GET /api/v1/indexer/transactions` | Filtered, offset-paginated list, newest first |
| `GET /api/v1/indexer/transactions/{transaction_hash}` | One transaction, 404 when not indexed |

Filters: `transaction_hash`, `account` (matches sender or recipient),
`asset_code`, `status`, `type`, `created_after`, `created_before`. Paging:
`limit` (1–200, default 50) and `offset` (≥ 0). A `created_after` later than
`created_before` returns `422`.

Request and response fields are described in the generated OpenAPI document
(`/openapi.json`, Swagger UI at `/docs`) and in
[docs/API.md](API.md#transaction-indexer-txindexer).

### Error responses

| Status | `error_code` | When |
|--------|--------------|------|
| 401 | `unauthorized` | Missing or invalid bearer token |
| 403 | `forbidden` | Caller's role cannot read the index |
| 404 | `not_found` | Hash is not indexed |
| 422 | `unprocessable_entity` | Query validation failed, or `created_after` > `created_before` |

Errors use the RFC 7807 envelope described in
[docs/RFC7807.md](RFC7807.md).

## Tests

| File | Covers |
|------|--------|
| `tests/test_tx_indexer_ingest.py` | Record normalization, stroop conversion, skip behaviour, cursor advance |
| `tests/test_tx_indexer_openapi.py` | OpenAPI document: paths, parameters, descriptions, response codes, examples |

No network access is needed: the Horizon transport is injectable and the sink is
a fake.
