# Analytics and Reporting in the SLA Operations Workspace

This guide describes how analytics queries, report aggregation, and export
artifacts fit together inside `app/sla_operations/`. It is written for
contributors adding isolated workspace modules: it documents the data shapes and
invariants that already exist so new helpers can compose with them without
touching the core FastAPI application.

## Scope and boundaries

- **Primary implementation area:** `app/sla_operations/**`.
- **Read-only context:** core routers, services, repositories, ORM models, and
  Alembic migrations under `app/` are out of scope. Workspace services are pure
  and database-independent; they operate on dictionaries, Pydantic models, or
  plain objects supplied by the caller.
- **No runtime wiring by default:** `app/sla_operations/api/router.py` exposes
  `create_workspace_router()` and a module-level `router` that is deliberately
  not included by the core application. Integration is a maintainer decision.
- **Safety expectations:** validate inputs, keep timestamps timezone-aware,
  never log credentials, and treat export storage keys as opaque identifiers
  rather than signed URLs.

## Module map

| Concern | Module | Responsibility |
| --- | --- | --- |
| Query contracts | `schemas/analytics_queries.py` | Bound and validate analytics requests, buckets, and paginated responses |
| SLA rollups | `services/sla_metrics_aggregation.py` | Aggregate outage counts, violation rate, average MTTR, and payouts |
| KPI rollups | `services/sla_metrics_aggregation_kpis.py` | Derived KPI views on top of the same aggregation inputs |
| MTTR analysis | `services/outage_mttr_analysis.py` | MTTR bands, distribution statistics, and ordered trend buckets |
| Severity mix | `services/severity_distribution_report.py` | Outage counts and ratios by severity/status |
| Export contracts | `schemas/exports.py` | Export requests, artifact metadata, and job status responses |
| Export rendering | `services/export_preparation.py` | Deterministic column/row preparation for CSV and JSON |
| Workspace API | `api/router.py`, `api/responses.py`, `api/request_parsing.py` | Compose subrouters and parse/typed response helpers |
| Sample data | `fixtures/` | Reusable outage, settlement, timeline, and webhook samples |
| Verification | `tests/` | Focused tests for workspace-owned schemas and services |

Every path in this document is relative to `app/sla_operations/` unless stated
otherwise.
