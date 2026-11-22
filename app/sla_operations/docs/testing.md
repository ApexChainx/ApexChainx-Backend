# Testing the SLA Operations Workspace

This guide covers the fixtures and helpers available in `app/sla_operations/` and how to run tests scoped to the workspace. It is intended for contributors implementing isolated modules under `app/sla_operations/` without touching core routed behavior.

> **Scope reminder:** All primary work for this workspace lives under `app/sla_operations/**`. Do not modify core endpoints, routers, services, repositories, ORM models, or Alembic migrations. The fixtures described here are **plain sample-data helpers** — they are *not* pytest fixtures and are not registered globally.

---

## Workspace layout (relevant paths)

```
app/sla_operations/
├── docs/
│   └── testing.md              # this file
├── fixtures/
│   ├── __init__.py
│   ├── bulk_import_fixtures.py
│   ├── operational_samples.py
│   ├── outage_fixtures.py
│   ├── settlement_fixtures.py
│   ├── sla_metrics_fixtures.py
│   ├── timeline_fixtures.py
│   └── webhook_fixtures.py
└── ...                         # workspace modules
```

Repository-level test infrastructure lives at:

```
tests/
├── conftest.py                 # shared pytest fixtures (DB session, client, etc.)
└── factories.py                # model/object factories
```

---

## Available fixtures

Each module in `app/sla_operations/fixtures/` exposes plain Python helpers (functions and/or constants) that return sample data. They are **not** pytest fixtures — import them directly in your tests or scripts.

### `bulk_import_fixtures`

Sample payloads for bulk-import workflows (e.g., lists of records to ingest, malformed rows for validation testing).

```python
from app.sla_operations.fixtures import bulk_import_fixtures

payload = bulk_import_fixtures.sample_bulk_import_payload()
```

Use these to exercise import parsing/validation logic without hitting the database.

### `operational_samples`

General-purpose operational sample data (representative records spanning common workspace workflows).

```python
from app.sla_operations.fixtures import operational_samples

samples = operational_samples.sample_operational_records()
```

### `outage_fixtures`

Sample outage lifecycle data (outage records in various states) for testing outage-related workspace logic.

```python
from app.sla_operations.fixtures import outage_fixtures

outages = outage_fixtures.sample_outages()
```

### `settlement_fixtures`

Sample settlement/bridging data (Stellar/Soroban settlement payloads) for testing settlement-related workspace modules.

```python
from app.sla_operations.fixtures import settlement_fixtures

settlements = settlement_fixtures.sample_settlements()
```

### `sla_metrics_fixtures`

Sample SLA metric inputs/outputs for testing SLA computation and analytics helpers.

```python
from app.sla_operations.fixtures import sla_metrics_fixtures

metrics = sla_metrics_fixtures.sample_sla_metrics()
```

### `timeline_fixtures`

Sample timeline/event sequences for testing timeline construction and ordering logic.

```python
from app.sla_operations.fixtures import timeline_fixtures

events = timeline_fixtures.sample_timeline_events()
```

### `webhook_fixtures`

Sample webhook payloads and delivery records for testing webhook-related workspace logic.

```python
from app.sla_operations.fixtures import webhook_fixtures

payloads = webhook_fixtures.sample_webhook_payloads()
```

> **Note:** Helper names above are illustrative. Inspect each module directly to confirm the exact callable/constant names before use.

---

## Running workspace-focused tests

There is currently **no dedicated test suite** for `app/sla_operations/`. To run tests that touch workspace modules, scope pytest with a keyword filter:

```bash
# Run any tests whose name/path matches the workspace
pytest tests/ -k sla_operations

# Verbose, stop on first failure
pytest tests/ -k sla_operations -x -v
```

If you add workspace-scoped tests under `tests/` (see below), you can also target them by path:

```bash
pytest tests/sla_operations/ -v
```

---

## Reusing shared test infrastructure

The repository provides shared infrastructure you should reuse rather than duplicate:

- **`tests/conftest.py`** — provides shared pytest fixtures such as the database session and test client. Import/consume these fixtures by name in your test functions; do not redefine them.
- **`tests/factories.py`** — provides factories for constructing model/object instances. Prefer factories over hand-built dicts when you need persisted or ORM-backed objects.

Example test that combines a shared fixture with a workspace sample helper:

```python
# tests/sla_operations/test_example.py
from app.sla_operations.fixtures import outage_fixtures


def test_outage_sample_shape(db_session):
    outages = outage_fixtures.sample_outages()
    assert outages, "expected at least one sample outage"
    # `db_session` comes from tests/conftest.py
```

---

## Adding tests within the workspace scope

When adding tests for workspace modules:

1. **Place tests under `tests/sla_operations/`** (create the directory if needed) so they are clearly scoped and easy to run with `pytest tests/sla_operations/`.
2. **Reuse `tests/conftest.py` fixtures** (`db_session`, client, etc.) and **`tests/factories.py`** — do not duplicate shared setup.
3. **Import sample data from `app/sla_operations/fixtures/`** rather than inlining large literals.
4. **Do not modify core tests** or core runtime components to make workspace tests pass. If you believe a core change is required, document it explicitly in your PR rather than making it silently.
5. **Keep tests deterministic** — avoid network calls, real Stellar/Soroban interactions, and real webhook delivery. Use the sample fixtures to simulate these boundaries.

### Boundary and integration notes

- **Database:** Workspace logic that persists data should be tested against the shared `db_session` fixture. Keep transactions isolated per test.
- **Settlement / Stellar / Soroban:** Do not hit live networks in tests. Use `settlement_fixtures` sample payloads to exercise parsing and transformation logic.
- **Webhooks:** Do not perform real deliveries. Use `webhook_fixtures` payloads to test construction and (where applicable) mocked delivery.
- **SLA computation:** Use `sla_metrics_fixtures` inputs to verify deterministic outputs.

---

## Quick reference

| Task | Command / Path |
| --- | --- |
| Run workspace tests | `pytest tests/ -k sla_operations` |
| Run scoped test dir | `pytest tests/sla_operations/ -v` |
| Shared fixtures | `tests/conftest.py` |
| Factories | `tests/factories.py` |
| Sample data | `app/sla_operations/fixtures/*` |

Remember: the fixtures under `app/sla_operations/fixtures/` are **plain sample-data helpers**, not globally registered pytest fixtures. Import them explicitly in the tests that need them.