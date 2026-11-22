# SLA Operations Workspace

Welcome to the SLA operations contributor workspace. This document describes the purpose, structure, and boundaries of the isolated `app/sla_operations/` package so that contributors can implement modules safely and consistently without destabilizing the core service.

## Purpose

The SLA operations workspace is a self-contained contributor area for building SLA-related operational tooling: report generation, analytics helpers, and supporting schemas. It exists to keep new, in-progress operational features isolated from the core routed domains until they are ready to be integrated.

The broader ApexChainx-Backend service is a FastAPI + SQLAlchemy + PostgreSQL application that manages outage lifecycles, SLA computation/analytics, audit logging, Stellar/Soroban settlement bridging, and webhook delivery. Core routed domains live under:

- `app/api/v1/endpoints` — HTTP routers and endpoint handlers
- `app/services` — core business logic
- `app/repositories` — data access layer
- `app/models` — SQLAlchemy ORM models

The SLA operations workspace is **not** part of that routed surface. It is a sandbox for isolated modules that may later be promoted into the core domains through a separate, reviewed change.

## Directory layout

```
app/sla_operations/
├── api/          # Workspace-local request/response entry points (not mounted by default)
├── schemas/      # Pydantic models scoped to workspace modules
├── services/     # Business logic for SLA operational features
├── tests/        # Unit tests for workspace modules
└── docs/         # Contributor documentation (this file and siblings)
```

### `api/`

Workspace-local entry points and adapters. These are **not** registered with the application router by default. If a module needs to be exposed, that integration is a separate, explicitly reviewed change to the core API layer — it must not be done implicitly from within this workspace.

### `schemas/`

Pydantic models used by workspace services. Keep schemas scoped to the modules that consume them. See [schemas.md](./schemas.md) for conventions on defining and validating workspace schemas.

### `services/`

Business logic for SLA operational features. For example, report generation lives at:

- `app/sla_operations/services/report_generation.py`

Services in this workspace should be pure and testable: accept validated inputs, return typed outputs, and avoid reaching into core repositories or ORM models unless a documented integration is explicitly required.

See [services.md](./services.md) for guidance on structuring workspace services.

### `tests/`

Unit tests for workspace modules. Tests should live alongside the module they cover, mirroring the module name. For example, the report generation service is tested at:

- `app/sla_operations/tests/test_report_generation_service.py`

Run the workspace tests with the repository's standard test runner, scoped to this package, e.g.:

```bash
pytest app/sla_operations/tests
```

### `docs/`

Contributor documentation for the workspace. Keep it additive and aligned with the actual workspace structure.

## How modules relate

A typical workspace module flows through three layers:

1. **Schemas** (`app/sla_operations/schemas/`) define validated input and output shapes.
2. **Services** (`app/sla_operations/services/`) implement the operational logic using those schemas.
3. **API adapters** (`app/sla_operations/api/`) optionally expose services to callers — but only when explicitly wired up.

Tests in `app/sla_operations/tests/` exercise the service layer directly, keeping behavior deterministic and independent of the HTTP layer.

## Isolation boundaries

This workspace is intentionally isolated. Contributors working here **must not**:

- Modify existing core endpoints or routers under `app/api/v1/endpoints`.
- Modify core services under `app/services`.
- Modify core repositories under `app/repositories`.
- Modify ORM models under `app/models`.
- Add or alter Alembic migrations.

If a workspace module requires integration with core runtime components, that integration must be documented as a minimal, explicitly reviewed change in a separate issue/PR — it is not expected as part of routine workspace contributions.

Additional boundaries:

- Do not introduce breaking changes to existing API behavior or persistence schemas.
- Do not log secrets or sensitive values; follow the repository's input-validation and security practices.
- Keep changes scoped to `app/sla_operations/**` unless a documented integration is explicitly required.

## Contributor workflow

1. Review this document and the sibling [schemas.md](./schemas.md) and [services.md](./services.md).
2. Create a dedicated branch: `feat/issue-<number>-short-description` or `fix/issue-<number>-short-description`.
3. Implement the change only within `app/sla_operations/**`.
4. Add or update tests under `app/sla_operations/tests/` and update documentation as needed.
5. Run linting, workspace tests, and applicable type checks.
6. Open a pull request referencing the issue and confirm no unnecessary changes were made outside the designated area.

## Related documentation

- [schemas.md](./schemas.md) — schema conventions for workspace modules
- [services.md](./services.md) — service structure and testing guidance