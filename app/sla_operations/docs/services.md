# SLA Operations Workspace — Services

This document describes the service modules that live under
`app/sla_operations/services/`. These modules implement the isolated SLA
operations workspace: they are **self-contained**, **additive**, and are **not
wired into any core routed domain** (`app/api/v1/endpoints`, `app/services`,
`app/repositories`, `app/models`). Nothing here changes existing API behavior or
persistence schemas.

> **Workspace isolation.** Everything under `app/sla_operations/` is a
> contributor sandbox. Service modules here must not import from or mutate core
> runtime components. If a future integration is needed, it must be documented
> explicitly and kept minimal — see the boundary notes below.

Related docs:

- [`../README.md`](../README.md) — workspace overview, layout, and conventions.
- [`./schemas.md`](./schemas.md) — Pydantic schemas used as service inputs/outputs.

---

## Layout

```
app/sla_operations/
├── README.md
├── docs/
│   ├── services.md      # this file
│   └── schemas.md
└── services/
    ├── __init__.py
    ├── report_generation.py
    └── ...              # additional workspace service modules
```

Each module in `services/` exposes plain Python functions (no FastAPI routers,
no SQLAlchemy sessions, no global state). Inputs and outputs are Pydantic models
or plain Python types defined in `app/sla_operations/schemas.py` (see
[`schemas.md`](./schemas.md)).

---

## Service modules

### `report_generation.py`

**Responsibility.** Assemble a multi-section SLA operations report from
already-computed inputs. This module is a pure composition layer: it does not
query the database, call external services, or perform SLA math. It takes
prepared section data and produces a single, ordered report object.

**Inputs.**

- A collection of section inputs (e.g. summary metrics, breach details,
  settlement status, audit excerpts), each conforming to the workspace schemas
  documented in [`schemas.md`](./schemas.md).
- Optional report metadata (title, generated-at timestamp, scope identifiers).

**Outputs.**

- A single assembled report object containing an ordered list of sections plus
  report-level metadata.
- Sections are emitted in a deterministic order so downstream consumers and
  tests can rely on stable output.

**Multi-section assembly.** The service iterates over the provided section
inputs, normalizes each into the common section shape, and appends them to the
report in a fixed order. Assembly is additive: missing optional sections are
simply omitted rather than causing failure, and each section retains its own
heading and payload.

**Empty-input behavior.** When no section inputs are supplied, the service
returns a valid, well-formed report with an empty section list (and any
report-level metadata that was provided). It does **not** raise and does **not**
fabricate placeholder sections. Callers that require at least one section
should validate that themselves before calling.

**Usage example.**

```python
from app.sla_operations.services.report_generation import generate_report

report = generate_report(
    sections=[summary_section, breach_section, settlement_section],
    title="Weekly SLA Operations Report",
)

for section in report.sections:
    print(section.heading)

# Empty input is safe and returns an empty-section report.
empty_report = generate_report(sections=[])
assert empty_report.sections == []
```

---

## Adding a new service module

1. Create `app/sla_operations/services/<name>.py`.
2. Define inputs/outputs as Pydantic models in `app/sla_operations/schemas.py`
   and document them in [`schemas.md`](./schemas.md).
3. Keep the module pure: no routers, no DB sessions, no imports from core
   `app/services`, `app/repositories`, or `app/models`.
4. Add a section to this file describing responsibility, inputs/outputs, and a
   short usage example.
5. Add focused unit tests under the workspace test scope.

---

## Boundary and integration notes

- **No core runtime coupling.** Workspace services must not import core
  endpoints, routers, services, repositories, or ORM models. They operate only
  on workspace schemas and plain Python data.
- **No persistence.** These services do not open sessions or write to the
  database; persistence remains the responsibility of core repositories.
- **No secrets in logs.** Do not log credentials, tokens, or full request
  payloads from these modules.
- **Additive only.** Changes here must not alter existing API behavior or
  persistence schemas. Any required integration with core code must be called
  out explicitly in the PR and kept minimal.
- **Determinism.** Outputs (especially section ordering in
  `report_generation.py`) must be deterministic to keep tests reliable.
</｜｜DSML｜｜ parameter>
</｜｜DSML｜｜ invoke>
</｜｜DSML｜｜ calls>