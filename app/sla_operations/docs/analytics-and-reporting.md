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

## Analytics query flow

`schemas/analytics_queries.py` is the entry contract. Callers submit an
`AnalyticsQueryParams` model and receive an `AnalyticsAggregateResponse`; the
workspace does not read the clock or database while validating.

### Request expectations

- `period_start` and `period_end` must both be timezone-aware, and the end must
  be strictly after the start. The span may not exceed `MAX_QUERY_RANGE_DAYS`
  (366 days).
- `granularity` is one of `hour`, `day`, `week`, `month`.
- `metrics` must contain 1 to `MAX_METRICS_PER_QUERY` (10) unique values from
  `outage_count`, `violation_count`, `availability_percentage`, `mttr_minutes`,
  `penalty_amount`, and `reward_amount`. Monetary metrics are expressed in
  **major currency units**, never minor units.
- `group_by` is optional (`severity`, `site_id`, or `status`). Ungrouped buckets
  carry `dimension_value = null`.
- `site_ids` holds at most `MAX_SITE_IDS_PER_QUERY` (100) unique non-blank
  identifiers; `severities` holds unique values from `critical`, `high`,
  `medium`, and `low`.
- `order_by` is `bucket_start` or `sample_count`; `sort_direction` is `asc` or
  `desc`. `limit` is 1 to `MAX_BUCKETS_PER_PAGE` (1000) and `offset` is
  non-negative.
- `include_partial` opts in to buckets whose period has not closed yet. Unknown
  fields are rejected (`extra="forbid"`).

Example request payload:

```json
{
  "period_start": "2026-01-01T00:00:00Z",
  "period_end": "2026-01-08T00:00:00Z",
  "granularity": "day",
  "metrics": ["outage_count", "mttr_minutes"],
  "group_by": "severity",
  "order_by": "bucket_start",
  "sort_direction": "asc",
  "limit": 50,
  "offset": 0,
  "include_partial": false
}
```

### Bucket and response expectations

Each `AnalyticsBucket` has aware `bucket_start`/`bucket_end` with end after
start, an optional `dimension_value`, a `metric_values` map whose values are
non-negative numbers, and a non-negative `sample_count` of source records.

`AnalyticsAggregateResponse` enforces the pagination contract:

- `returned_buckets` equals `len(buckets)` and never exceeds `total_buckets`.
- A page never has more buckets than `query.limit`.
- The sum of returned `sample_count` values never exceeds `total_records`.
- `totals` contains every requested metric, and buckets contain only requested
  metrics.
- `has_more` is exactly `query.offset + returned_buckets < total_buckets`.
- `generated_at` is timezone-aware; callers should generate it at the boundary
  so the aggregation itself stays deterministic.
