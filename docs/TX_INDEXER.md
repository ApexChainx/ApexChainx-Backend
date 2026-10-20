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

### Input validation

Both sides of the indexer treat input as untrusted: query filters on the read
API and every field of a Horizon record. Validation is **allow-list based**
(`app/services/tx_indexer_input.py`), so a value either canonicalizes to the
form the database expects or the request is rejected.

| Field | Accepted form | Normalization |
|-------|---------------|---------------|
| `transaction_hash` | 64 hexadecimal characters | lower-cased |
| `account` | letters, digits, `_`, `*`, `:`; max 69 | — |
| `asset_code` | 1-12 alphanumerics | upper-cased |
| `status`, `type` | `lower_snake_case` tokens, max 32 | lower-cased |
| `created_after`, `created_before` | ISO-8601 | naive timestamps become UTC; offsets convert to UTC |

Additional rules:

* surrounding whitespace is stripped; a value that is empty after stripping is
  rejected rather than treated as "no filter";
* NUL and any other control/format character is rejected;
* quotes, semicolons and comment markers cannot survive the allow-list, so an
  injection attempt is a `422` instead of a silently empty result set;
* validation runs **before** the repository is called — a malformed filter
  never reaches the database.

On the ingest path the same allow-list applies to `transaction_hash`,
`asset_code`, operation type and account fields, and amounts are bounded to a
signed 64-bit stroop value. A record that fails is logged as
`tx_indexer_skipped_record` and dropped; it never aborts the batch or writes a
row the ledger cannot represent.

### Error responses

| Status | `error_code` | When |
|--------|--------------|------|
| 401 | `unauthorized` | Missing or invalid bearer token |
| 403 | `forbidden` | Caller's role cannot read the index |
| 404 | `not_found` | Hash is not indexed |
| 422 | `unprocessable_entity` | A filter does not match its allow-list, or `created_after` > `created_before` |

Errors use the RFC 7807 envelope described in
[docs/RFC7807.md](RFC7807.md).

## Complexity budget

The indexer's modules are kept deliberately flat (`app/services/tx_indexer.py`,
`app/services/tx_indexer_input.py`, `app/repositories/tx_indexer_repository.py`,
`app/api/v1/endpoints/indexer.py`). Rather than refactor once and let nesting
creep back, `tests/test_tx_indexer_complexity.py` measures every function with
the standard McCabe count and fails above **8**:

```
complexity = 1 + decision points (if/elif, for, while, except,
             conditional expression, assert, match cases, extra boolean
             operands, comprehensions)
```

Single-purpose helpers keep each unit small:

| Was | Now |
|-----|-----|
| `IndexedTransaction.from_horizon_record` (11) | one parser per field: `_parse_transaction_hash`, `_parse_amount`, `_parse_accounts`, `_parse_asset_code`, `_parse_operation_type` |
| `HorizonClient.fetch_payments` (9) | `_get_page` (transport + error mapping), `_send`, `_page_params`, `_decode_page` |
| `TxIndexerRepository.build_query` (8) | `_apply_equalities`, `_apply_account`, `_apply_bounds`, driven by the `EQUALITY_COLUMNS` / `DATE_BOUNDS` tables |

The guard is part of the normal test run — no extra tooling, no new
dependency — and fails with the offending function names and scores.

## Tests

| File | Covers |
|------|--------|
| `tests/test_tx_indexer_ingest.py` | Record normalization, stroop conversion, skip behaviour, cursor advance |
| `tests/test_tx_indexer_openapi.py` | OpenAPI document: paths, parameters, descriptions, response codes, examples |
| `tests/test_tx_indexer_input_sanitization.py` | Allow-list sanitizers and their HTTP/ingest failure modes |
| `tests/test_tx_indexer_complexity.py` | Cyclomatic-complexity budget for every indexer module |

No network access is needed: the Horizon transport is injectable and the sink is
a fake.
