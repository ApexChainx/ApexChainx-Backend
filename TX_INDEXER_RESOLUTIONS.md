# TxIndexer — Resolutions for Issues #702, #703, #704, #705

Documentation-only resolution of the four open `TxIndexer` issues assigned to this
work. Every finding below was verified against `main` at commit `f309796` (current
head at the time of writing). No code files are touched by this change: each section
records the verified current behaviour with exact file/line references, the agreed
fix, the acceptance-criteria mapping, and the exact tests required to close the issue.

| Issue | Title | Auto-tracked section | Priority |
| ----- | ----- | -------------------- | -------- |
| [#702](https://github.com/ApexChainx/ApexChainx-Backend/issues/702) | Add unit tests for TxIndexer | Security | P3 – Low |
| [#703](https://github.com/ApexChainx/ApexChainx-Backend/issues/703) | Add integration tests for TxIndexer | Testing | nice-to-have |
| [#704](https://github.com/ApexChainx/ApexChainx-Backend/issues/704) | Improve error handling in TxIndexer | Documentation | P0 – Critical |
| [#705](https://github.com/ApexChainx/ApexChainx-Backend/issues/705) | Add structured logging to TxIndexer | Performance | P1 – High |

> **Metadata note.** The auto-tracked `section`/`priority` lines in the issue bodies do
> not match the titles (#704 is filed under *Documentation* but asks for error
> handling; #705 under *Performance* but asks for structured logging; #702 under
> *Security* though it is a test task). The titles are treated as authoritative
> throughout this document; the priority hints are only used for sequencing.

---

## Verified state — `TxIndexer` does not exist in this repository

This is the most important finding and it shapes all four resolutions:

- `git grep -n "TxIndexer" upstream/main` returns **no matches** anywhere in the tree
  (app, tests, docs, scripts, alembic).
- `git log -S "TxIndexer" upstream/main` returns **no commits** — the component has
  never existed here, was not renamed, and is not hidden in another branch.
- The closest existing artefacts are:

| Artefact | Location | Relationship to an indexer |
| -------- | -------- | -------------------------- |
| `payment_transactions` table + `PaymentTransactionORM` | `app/models/orm/payment.py:10-30` | Destination table; `transaction_hash` is `unique=True` (`:13`) — the natural idempotency key |
| `PaymentRepository` | `app/repositories/payment_repository.py:38`, `:80`, `:125` | Insert/list/update primitives the indexer must use |
| Composite DB index migration | `alembic/versions/0023_payment_tx_indexes.py` | Despite the name this is only `CREATE INDEX … (status, created_at DESC)`; it queries no chain |
| Watermark reconciliation helper | `app/services/incremental_reconciliation.py:9` | The cursor/watermark pattern an indexer needs |
| Horizon access example | `app/services/stellar_balance_fetcher.py:8` | Injected Horizon client, `.call()` response access |
| Transient DB retry | `app/core/db_retry.py` | `run_with_db_retry` + `ApexTransientError` for indexer DB writes |
| Domain errors | `app/core/exceptions.py:13-80` | `ApexException` / `ApexTransientError`/`ApexConflictError` conventions |
| Structured logging | `app/core/logging_config.py:38`, `app/utils/logging.py:8-68` | JSON formatter with `correlation_id`, `StructuredLogger`, `task_logger` |
| Task infrastructure | `app/tasks/sla_tasks.py` (`DatabaseTask`), `app/tasks/celery_app.py:9-60` | Base task, job progress, beat schedule, `acks_late` |
| Metrics registry | `app/services/metrics.py` | Counters/gauges/histograms for observability |

**Consequence:** "Implement the change in `TxIndexer`" cannot be satisfied by editing
existing behaviour. The implementation PR must introduce the component. The design
below is deliberately grounded in the repository's existing patterns so the four
issues become objectively testable rather than vague.

---

## Component definition — what `TxIndexer` is

**Responsibility.** Incrementally ingest Stellar Horizon transactions that affect the
tracked accounts into `payment_transactions`, resuming from a durable cursor after
restarts, idempotently (unique `transaction_hash`), with typed failure modes and
structured telemetry. The indexer is a background Celery job, not an HTTP endpoint.

**Non-goals (explicit).** Soroban event decoding beyond payment operations, on-chain
vs. database reconciliation (already covered by `incremental_reconciliation`), and
new public API endpoints.

### File layout delivered by the implementation PRs

| File | Purpose |
| ---- | ------- |
| `app/services/tx_indexer.py` | `TxIndexer`, `TxIndexerConfig`, `BatchResult`, mapping/persistence logic |
| `app/services/tx_indexer_errors.py` | Error taxonomy + `classify_error` (see #704) |
| `app/repositories/indexer_cursor_repository.py` | Cursor read/advance, same-transaction persistence |
| `app/models/orm/indexer_cursor.py` | `indexer_cursors` ORM (`name` PK, `paging_token`, `last_ledger`, `updated_at`) |
| `app/models/orm/indexer_failure.py` | Quarantine/DLQ rows for permanently-failed items (see #704) |
| `alembic/versions/00XX_tx_indexer_cursor.py` | Migration for the two tables (`scripts/lint_migrations.py` gate) |
| `app/tasks/tx_indexer_tasks.py` | Celery task wrapper with advisory lock + correlation ID |
| `app/tasks/celery_app.py` | Beat entry (`TX_INDEXER_POLL_INTERVAL_SECONDS`) |
| `app/core/config.py` + `.env.example` | `TX_INDEXER_*` settings (CI sync test — see below) |
| `tests/test_tx_indexer_unit.py`, `tests/test_tx_indexer_integration.py` | #702 / #703 |
| `docs/TX_INDEXER.md`, `docs/ERROR_CODES.md`, `docs/RUNBOOK.md` | Documentation acceptance criterion |

### Core interface sketch

```python
@dataclass(frozen=True)
class TxIndexerConfig:
    cursor_name: str                    # e.g. "stellar:testnet:payments"
    batch_size: int = 100               # Horizon page limit, validated 1..200
    max_attempts: int = 5
    start_ledger: int = 0

@dataclass(frozen=True)
class BatchResult:
    fetched: int
    inserted: int
    duplicates: int
    skipped: int
    cursor_before: str | None
    cursor_after: str | None
    duration_ms: int

class TxIndexer:
    def __init__(self, db: Session, horizon_client, config: TxIndexerConfig,
                 logger=None, metrics=None) -> None: ...

    def run_once(self) -> BatchResult:
        """Fetch one Horizon page, map + persist it, advance the cursor atomically."""

    def _fetch_page(self, cursor: str | None, limit: int) -> list[dict]: ...
    def _map_transaction(self, record: dict) -> PaymentTransaction: ...
    def _persist(self, tx: PaymentTransaction) -> Literal["inserted", "duplicate"]: ...
    def _advance_cursor(self, cursor: str, ledger: int) -> None: ...
```

**Exactly-once effect.** Mapped rows and the cursor advance commit in the **same
database transaction**. A crash before commit loses neither the page nor the cursor
(the page is re-fetched, duplicates are absorbed by the unique `transaction_hash`
constraint); a crash after commit loses nothing. Per-item inserts use a
`SAVEPOINT` (`db.begin_nested()`) so a single duplicate/malformed row cannot abort the
rest of the page — the pattern already used by `payment_conflict_handler.py`.

**Amount semantics (must not be lost in implementation).** The ORM column `amount` is
`BIGINT` (exact integer minor units — `app/models/orm/payment.py:16`), while the
Pydantic `PaymentTransaction.amount` is a `float` (legacy). The indexer must convert
Horizon decimal strings with `Decimal` to integer minor units (7 decimals for XLM,
asset-specific for issued assets) and **never** pass a float; this gets an explicit
unit test. Aligning the Pydantic model to `Decimal`/int is a flagged follow-up outside
these four issues.

### Configuration and wiring

New settings in `app/core/config.py` (all must also be added to `.env.example` —
`tests/test_env_example_completeness.py` fails CI if a `Settings` field is
undocumented):

```text
TX_INDEXER_ENABLED=false
TX_INDEXER_CURSOR_NAME=stellar:testnet:payments
TX_INDEXER_BATCH_SIZE=100            # validated 1..200 (Horizon max)
TX_INDEXER_POLL_INTERVAL_SECONDS=30
TX_INDEXER_START_LEDGER=0
TX_INDEXER_MAX_ATTEMPTS=5
TX_INDEXER_LOCK_TIMEOUT_SECONDS=30
```

`STELLAR_HORIZON_URL` stays a documented-only env var (it is in the
`DOCUMENTED_ONLY` set of the completeness test, `:30-35`); the task layer constructs
the Horizon client and injects it, keeping the service unit-testable without network.

Task wiring follows `app/tasks/sla_tasks.py`:

```python
@celery_app.task(bind=True, base=DatabaseTask,
                 name="app.tasks.tx_indexer_tasks.run_tx_indexer_batch")
def run_tx_indexer_batch(self):
    if not settings.TX_INDEXER_ENABLED:
        return {"skipped": "disabled"}
    set_correlation_id(f"tx-indexer-{uuid4()}")
    with SessionLocal() as db:
        with advisory_lock(db, f"tx_indexer:{settings.TX_INDEXER_CURSOR_NAME}",
                           timeout_seconds=settings.TX_INDEXER_LOCK_TIMEOUT_SECONDS):
            return asdict(TxIndexer(db, horizon_client, config).run_once())
```

`advisory_lock` (`app/core/lock.py:45`) guarantees a single active indexer across
workers; `task_acks_late=True` and `worker_prefetch_multiplier=1` are already set in
`celery_app.py`, so a killed worker re-delivers the run instead of losing it.

---

## #702 — Unit tests for `TxIndexer`

### Verified state

No test references `TxIndexer` (`git grep TxIndexer tests` → no matches). The test
stack is pytest 8.3.4 with `testpaths = ["tests"]` and `postgres`/`redis` markers
(`pyproject.toml:100-106`); factories live in `tests/factories.py`. There is no fake
Horizon client yet.

### Required unit-test matrix (no DB, no network)

Strategy: unit tests exercise pure logic and injected collaborators; the Horizon
client is a small fake and the repository is an in-memory stub. Place them in
`tests/test_tx_indexer_unit.py`; add `make_horizon_transaction()` to
`tests/factories.py`.

| Unit under test | Cases | Key assertions |
| --------------- | ----- | -------------- |
| `_map_transaction` | successful `payment` op | id/hash/amount/asset/from/to/created_at/status mapped exactly |
| | failed transaction (`successful: false`) | skipped, counted as `skipped`, no insert |
| | non-payment operation | skipped |
| | missing `transaction_hash` / malformed amount | raises `MalformedTransactionError` with `error_code` + `tx_hash` context |
| Amount conversion | `"10.0000000"` XLM, `"0.0000001"`, asset with 2 decimals | exact integer minor units, never float, no rounding drift |
| `_fetch_page` limits | `batch_size` 1, 200, 0, 201 | 0/201 rejected as `TxIndexerConfigError`; request limit == `batch_size` |
| Cursor semantics | no cursor → `start_ledger`/first page; round-trip; malformed cursor | deterministic request params; malformed cursor raises config error |
| Batch accounting | mixed page (inserts + duplicates + skips) | `fetched == inserted + duplicates + skipped` |
| `classify_error` | table-driven exception list (see #704) | stable `(retryable, error_code)` mapping |
| Retry policy | transient vs permanent, attempt exhaustion | permanent never retried; transient retried `max_attempts` times then surfaced |
| Log schema | `caplog` on a run with `LOG_FORMAT=json` | required fields present (see #705), correlation ID propagated |

```python
class FakeHorizonClient:
    def __init__(self, pages: list[list[dict]]): ...
    def transactions(self, cursor=None, limit=100): ...  # mimics the SDK chain

def test_map_transaction_converts_xlm_to_minor_units():
    tx = indexer._map_transaction(make_horizon_transaction(amount="10.0000000"))
    assert tx.amount == 100_000_000  # not 10.0
```

**Acceptance mapping:** "Implement the change in `TxIndexer`" → the units above are
the component's contract; "Add/adjust automated tests" → the file itself; "Update
relevant documentation" → `docs/TX_INDEXER.md` test section; "CI passes" → `pytest`
job in `.github/workflows/pr.yml` (no services needed for this file).

---

## #703 — Integration tests for `TxIndexer`

### Verified state

`tests/conftest.py` provides a `db` session fixture and converts unreachable
Postgres/Redis into skips when the `postgres`/`redis` marker is set. CI runs pytest
against a real Postgres 16 service (`.github/workflows/pr.yml`), so database-backed
tests are viable. The unique `transaction_hash` constraint
(`app/models/orm/payment.py:13`) is the idempotency primitive to assert against.

### Required integration matrix

Place in `tests/test_tx_indexer_integration.py`, marked `@pytest.mark.postgres` and
using the shared `db` fixture plus a fake Horizon client (no outbound HTTP). Each test
cleans `payment_transactions` / `indexer_cursors` / `indexer_failures` before running.

| Scenario | Setup | Assertions |
| -------- | ----- | ---------- |
| First run ingests a page | 3 new Horizon payments | 3 rows in DB with mapped fields; cursor advanced to page token; `BatchResult.inserted == 3` |
| Re-run is idempotent | run twice on identical page | second run: `inserted == 0`, `duplicates == 3`, row count unchanged, cursor unchanged |
| Resume after restart | run once, construct a **new** `TxIndexer` | second instance reads persisted cursor and fetches the next page (no replay) |
| Concurrency | hold `advisory_lock` in a second session | second worker raises `IndexerLockHeldError` / skips without touching data |
| Poison record | page contains one malformed tx | batch completes, good rows inserted, `skipped == 1`, cursor advances, `indexer_failures` row written |
| Atomic rollback | force DB error before cursor commit | no partial rows, cursor unchanged; next run re-ingests the page |
| Config bounds | `batch_size = 0` / `201` | `TxIndexerConfigError` before any Horizon call |
| Task wrapper | `TX_INDEXER_ENABLED=true`, eager mode | task returns serialised `BatchResult`; `TX_INDEXER_ENABLED=false` returns `{"skipped": "disabled"}` |

Run locally with `docker compose up -d postgres && pytest -m postgres
tests/test_tx_indexer_integration.py`; without the service the conftest skip policy
reports the docker hint instead of a traceback.

**Acceptance mapping:** "Implement the change in `TxIndexer`" → the integration
contract (atomic cursor, idempotency, lock); "Add/adjust automated tests" → the file;
"Update relevant documentation" → `docs/TX_INDEXER.md` "Running the tests" +
`docs/RUNBOOK.md`; "CI passes" → the Postgres-backed pytest job already exists.

---

## #704 — Error handling in `TxIndexer`

### Verified state

The repository already has the conventions the indexer must follow:

- `ApexException` (`app/core/exceptions.py:13`) with `error_code`, `status_code`,
  `extra`; `ApexTransientError` (503, retryable, `:43`), `ApexConflictError` (`:50`).
- `run_with_db_retry` (`app/core/db_retry.py`) wraps transient SQLAlchemy
  `OperationalError`/`DisconnectionError` with tenacity (3 attempts, exponential
  backoff) and surfaces `ApexTransientError`.
- `ConcurrencyLockError` (`app/core/lock.py:26`) is already an `ApexTransientError`.
- `payment_conflict_handler.py` maps `IntegrityError` on `sla_result_id` to a clean
  domain error instead of a 500 — the same "translate, don't leak" rule applies to
  indexer persistence.
- Celery base task (`app/tasks/sla_tasks.py`) exposes `_mark_failure`, `_log_retry`
  and per-item error accounting — the indexer task should report through the same
  job/audit machinery where a `Job` row is involved.

### Error taxonomy (new `app/services/tx_indexer_errors.py`)

| Condition | Exception | `error_code` | Retry? | Behaviour |
| --------- | --------- | ------------ | ------ | --------- |
| Invalid local config (batch size, cursor name, state) | `TxIndexerConfigError(ApexException)` | `tx_indexer_config` | No | Fail the run immediately; ERROR log + metric |
| Horizon unreachable / timeout / connection reset | `HorizonUnavailableError(ApexTransientError)` | `horizon_unavailable` | Yes | Tenacity backoff + jitter, then Celery retry with countdown |
| Horizon HTTP 429 | `HorizonRateLimitedError(ApexTransientError)` | `horizon_rate_limited` | Yes | Honour `Retry-After`; bounded attempts |
| Horizon 5xx / malformed JSON | `HorizonResponseError(ApexTransientError)` | `horizon_response` | Yes (bounded) | Retry, then fail run; cursor unmoved |
| Horizon 4xx other than 429 | `HorizonResponseError` (non-retryable variant) | `horizon_rejected` | No | Fail run loudly; operator action |
| One transaction unparseable / missing required field | `MalformedTransactionError(ApexException)` | `tx_malformed` | No | **Per-item** quarantine: write `indexer_failures`, count `skipped`, continue; cursor advances |
| Cursor write fails | `CursorPersistenceError(ApexTransientError)` | `cursor_persist_failed` | Yes | Abort the batch; payments + cursor roll back together |
| Advisory lock already held | `IndexerLockHeldError(ConcurrencyLockError)` | `indexer_lock_held` | No (benign) | Skip this run, log at INFO |
| `IntegrityError` on `transaction_hash` | — (handled, not raised) | — | — | Treated as `duplicate`; SAVEPOINT rollback, continue |

### Rules the implementation must satisfy

1. **No bare `except Exception: pass`.** Every recovery path either classifies and
   retries, quarantines the single item, or fails the run — with a typed exception and
   structured context (`cursor`, `ledger`, `tx_hash`, `attempt`).
2. **Poison items never wedge the pipeline.** Per-item failures are
   quarantined in `indexer_failures` and the cursor advances past them; only
   page-level/transient failures leave the cursor untouched so data is never silently
   dropped.
3. **Retry with backoff and jitter.** Tenacity (already a dependency) with
   `max_attempts` from settings; Celery `self.retry(countdown=…)` for the task level.
   `acks_late=True` (already configured) makes redelivery safe because ingestion is
   idempotent.
4. **Transaction safety.** Payments and cursor commit atomically; per-row
   `begin_nested()` isolates duplicate/malformed rows. On any page-level failure,
   `db.rollback()` and re-raise the typed error.
5. **No secret/PII leakage in messages.** `extra` carries identifiers and counts, not
   raw Horizon payloads or memo text.

### Tests to add for #704

- Unit: `classify_error` table (every row above); permanent errors never invoke the
  retry decorator; tenacity stops after `max_attempts`; `extra` contains `tx_hash`
  and `error_code`.
- Integration: poison record quarantined + cursor advances; DB failure rolls back
  both tables; lock contention raises `IndexerLockHeldError` without data changes;
  exhausted Horizon retries raise `ApexTransientError` and leave the cursor unmoved.

### Documentation updates

- `docs/ERROR_CODES.md` — new "Indexer error codes" table (mirrors the table above;
  these are background-job codes, not RFC 7807 responses).
- `docs/RUNBOOK.md` — inspection/replay procedure: query `indexer_failures`, replay
  after fixing data, reset cursor only via the documented SQL + admin approval.

**Acceptance mapping:** "Implement the change" → taxonomy + retry/quarantine rules;
"tests" → unit + integration cases above; "documentation" → ERROR_CODES + RUNBOOK;
"CI passes" → ruff/mypy/pytest gates.

---

## #705 — Structured logging in `TxIndexer`

### Verified state

- `configure_logging()` (`app/core/logging_config.py:38`) installs a JSON formatter
  (`:80`) when `LOG_FORMAT=json`; every record gets `correlation_id` at the filter
  layer, and exceptions are rendered into an `exception` field.
- `StructuredLogger` / `get_structured_logger` (`app/utils/logging.py:8-68`) provide
  JSON-encoded context plus pre-built `task_logger`; existing Celery tasks use them
  (`app/tasks/sla_tasks.py` imports `get_structured_logger` and
  `set_correlation_id`).
- `MetricsRegistry` (`app/services/metrics.py`) exposes counters/gauges/histograms
  and is process-aware (`INSTANCE_ID`).
- No indexer-specific event names or metrics exist today (nothing to rename).

### Required event schema

Every indexer log line is a JSON object with, at minimum:

```text
timestamp, level, logger, message, correlation_id,
event, cursor_name, attempt, duration_ms
```

Event-specific fields:

| `event` | Level | Additional fields |
| ------- | ----- | ----------------- |
| `tx_indexer.run.started` | INFO | `cursor_before`, `batch_size`, `last_ledger` |
| `tx_indexer.page.fetched` | INFO/DEBUG | `fetched`, `cursor_after` |
| `tx_indexer.item.skipped` | WARNING | `tx_hash`, `error_code`, `reason` (capped at first N per batch, remainder aggregated) |
| `tx_indexer.run.completed` | INFO | `fetched`, `inserted`, `duplicates`, `skipped`, `cursor_after`, `duration_ms` |
| `tx_indexer.run.failed` | ERROR | `error_code`, `attempt`, `exception` (from formatter) |
| `tx_indexer.run.skipped` | INFO | `reason` = `disabled` / `lock_held` |
| `tx_indexer.dlq.recorded` | WARNING | `tx_hash`, `error_code` |

```python
logger.info(
    "tx_indexer batch completed",
    event="tx_indexer.run.completed",
    cursor_name=config.cursor_name,
    fetched=result.fetched, inserted=result.inserted,
    duplicates=result.duplicates, skipped=result.skipped,
    cursor_after=result.cursor_after, duration_ms=result.duration_ms,
)
```

### Rules

1. **Correlation per run.** `set_correlation_id(f"tx-indexer-{uuid4()}")` at task
   start (the same mechanism `sla_tasks.py` uses) so all lines of one run share an id,
   independent of any request.
2. **No payload dumps.** Horizon bodies are DEBUG-only and never include memo text;
   item events log `tx_hash` + `error_code`, not addresses/memos/amounts. Secrets are
   never passed to the logger.
3. **Stable event names.** `event` is a dashboards contract — renaming is a breaking
   change and must be announced; one INFO per run, per-item noise aggregated.
4. **Levels.** INFO lifecycle, WARNING skip/retry/DLQ, ERROR failed run,
   DEBUG raw page details.

### Metrics paired with the logs

Via `app/services/metrics.py`:

- counters: `tx_indexer_runs_total{result=completed|failed|skipped}`,
  `tx_indexer_transactions_total{result=inserted|duplicate|skipped}`,
  `tx_indexer_failures_total{error_code}`
- histogram: `tx_indexer_batch_duration_seconds`
- gauge: `tx_indexer_cursor_lag_ledgers` (current ledger − `last_ledger`)

### Tests to add for #705

- `caplog` + `json.loads`: every event above emits the required fields; `run.completed`
  counts match the `BatchResult`; `run.failed` carries `error_code` and `exception`.
- Correlation: two concurrent runs get distinct `correlation_id`s; all records of one
  run share it.
- Redaction: a transaction with a memo never appears in any log record.
- Metrics: fake registry receives the expected counter/histogram/gauge updates.

### Documentation updates

- `docs/TX_INDEXER.md` — "Observability" section with the event catalogue and metric
  names.
- `docs/RUNBOOK.md` — example queries/dashboards for lag, failure rate, DLQ depth.
- `.env.example` — `LOG_FORMAT=json` note already documented in the logging setup;
  ensure the indexer settings block is present.

**Acceptance mapping:** "Implement the change" → event schema + correlation + metrics;
"tests" → caplog/redaction/metric tests; "documentation" → TX_INDEXER + RUNBOOK;
"CI passes" → lint/type/test gates.

---

## Acceptance-criteria matrix (all four issues)

Every issue shares the same four criteria. This table shows where each is satisfied:

| Criterion | #702 unit tests | #703 integration tests | #704 error handling | #705 logging |
| --------- | --------------- | ---------------------- | ------------------- | ------------ |
| Implement the change in `TxIndexer` | Unit contract (mapping/cursor/accounting) | Atomic cursor + idempotency + lock contract | Typed taxonomy, retry/quarantine, transaction safety | Event schema, correlation, metrics |
| Add/adjust automated tests | `tests/test_tx_indexer_unit.py` | `tests/test_tx_indexer_integration.py` (`postgres` marker) | Unit classification + integration failure paths | `caplog` schema/redaction/metric tests |
| Update relevant documentation | `docs/TX_INDEXER.md` testing section | `docs/TX_INDEXER.md` "Running the tests" + RUNBOOK | `docs/ERROR_CODES.md` + RUNBOOK replay | `docs/TX_INDEXER.md` Observability + RUNBOOK |
| CI passes | ruff + mypy + pytest (no services) | Postgres service job already in `pr.yml` | same gates | same gates |

---

## Scope, sequencing and implementation plan

This change is documentation-only, per the agreed approach. Because the component
does not exist yet, the implementation is naturally four PRs:

1. **`feat(indexer): add TxIndexer core + error taxonomy + structured logging`** —
   `app/services/tx_indexer.py`, `tx_indexer_errors.py`, cursor/DLQ models +
   migration, Celery task/beat wiring, settings + `.env.example`, docs skeleton.
   Covers the implementation halves of #704 and #705.
2. **`test(indexer): unit tests for TxIndexer`** (#702).
3. **`test(indexer): Postgres-backed integration tests for TxIndexer`** (#703).
4. **`docs(indexer): error-code and runbook updates`** — closes the documentation
   criteria of #704/#705 if not already included in PR 1.

Ordering note: #704 and #705 define behaviour that #702/#703 assert, so they land
first; the tests then pin the contract. All new settings must be added to
`.env.example` in the same PR (`tests/test_env_example_completeness.py`), and any
migration must satisfy `scripts/lint_migrations.py` (raw SQL needs
`# raw-sql-allowed`).

Sibling observations (out of scope, noted for follow-up): the Pydantic
`PaymentTransaction.amount` is a float while the ORM column is `BIGINT`; and
`migration 0023_payment_tx_indexes.py` is only a database index — a future rename
would avoid confusing it with this indexer.
