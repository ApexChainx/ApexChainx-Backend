# SLA Operations Workspace

This package is the dedicated, project-specific contribution area for the ApexChainx Backend.

## Purpose
`app/sla_operations/` is reserved for contributor-built backend modules that extend SLA operations capabilities (outage/SLA analytics, reporting utilities, validation helpers, operational tooling, and integration-safe service components) while keeping the core runtime and stabilization-sensitive codebase isolated for maintainer-led fixes.

## Why this location fits the project
- The backend is a FastAPI application organized by domain packages under `app/` (`api`, `services`, `repositories`, `models`, `schemas`, `tasks`, etc.).
- ApexChainx centers on deterministic SLA calculation, outage lifecycle management, and settlement/audit operations, making `sla_operations` a natural bounded context for additive contributions.
- A dedicated package allows contributors to deliver cohesive modules without scattering changes across core endpoints and services.

## Boundaries
- **Do primary implementation work here:** `app/sla_operations/**`
- **Avoid unrelated edits** to core runtime paths (`app/main.py`, existing routers/endpoints, core services/repositories/models, migrations) unless an assigned issue explicitly requires and documents a minimal integration change.
- Any required wiring outside this package (e.g. router inclusion, dependency registration) must be narrowly scoped and justified in the issue/PR.

## Suggested structure (as needed per issue)
- `api/` – isolated routers/schemas for contributor modules (integration only when explicitly requested)
- `services/` – domain/service logic and orchestrators
- `schemas/` – Pydantic request/response models for module APIs/utilities
- `repositories/` – data-access helpers specific to contributed modules (avoid altering core repositories)
- `utils/` – pure helpers, validators, formatters
- `tasks/` – isolated task definitions/helpers (Celery/worker integration only when explicitly requested)
- `tests/` or colocated `test_*.py` – tests for contributed modules
- `docs/` – module-level documentation

## API scaffolding
`api/router.py` exposes an aggregate router factory for composing workspace-owned
subrouters. The module-level `router` is intentionally not wired into the core
application; maintainers can compose subrouters and integrate it later.
`api/dependencies.py` resolves services registered on
`app.state.sla_operations_services`, while `api/responses.py` and
`api/request_parsing.py` provide typed response and request-parameter helpers.

## Contributor workflow
1. Select an assigned issue scoped to `app/sla_operations/`.
2. Create a branch: `feat/issue-<number>-short-description` or `fix/issue-<number>-short-description`.
3. Implement only the requested work inside this workspace.
4. Add/update tests relevant to your changes.
5. Run checks before opening a PR (as applicable):
   - `ruff check app tests`
   - `ruff format --check app tests`
   - `mypy app`
   - `pytest`
6. Open a PR referencing the issue and clearly note any approved exception that touches files outside this workspace.

## Quality expectations
- Follow existing FastAPI, Pydantic, SQLAlchemy, and service/repository patterns.
- Keep modules deterministic, typed, and testable.
- Do not introduce breaking changes to existing routed API behavior.
- Preserve security practices (no secrets in code/logs, validate inputs, respect auditability).

## SLA metrics aggregation

`services/sla_metrics_aggregation.py` provides a pure, database-independent
aggregation service. It accepts dictionaries or objects with `status`,
`mttr_minutes`, and `amount` fields and returns total outages, violation rate,
average MTTR, and payout sum.

## Documentation

- `docs/analytics-and-reporting.md` – analytics query contracts, report
  aggregation flows, export pipeline, data expectations, and integration
  boundaries for workspace contributors.
