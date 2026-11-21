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

## Report aggregation flows

The reporting services are pure transforms over caller-supplied records. They
never open a session, read application state, or mutate their inputs, so the same
records always produce the same report.

### SLA metrics rollup

`SLAMetricsAggregationService.aggregate()` (aliased as
`SlaMetricsAggregationService`, with `aggregate_sla_metrics()` as the
function-style entry point) accepts mappings or attribute objects with:

- `status` — non-blank string; `violated`, `breached`, and `violation` count as
  violations (case-insensitive, trimmed).
- `mttr_minutes` — finite number that must not be negative.
- `amount` — finite number, treated as a major-unit payout.

The result `SLAMetricsAggregation` exposes `total_outages`, `violation_rate`,
`avg_mttr`, and `payout_sum`. Rates are rounded to four decimals; averages and
payout sums to two. An empty input yields all zeroes. Missing fields, booleans
in numeric positions, negative MTTR, non-finite numbers, `None`, strings, and
mappings passed as the record collection are rejected with clear errors.
`to_dict()` and `model_dump()` provide serialization for downstream consumers.

### MTTR analysis

`analyze_outage_mttr()` in `services/outage_mttr_analysis.py` accepts
`OutageMTTRRecord` instances or mappings. MTTR can be supplied directly as
`mttr_minutes` or derived from `detected_at`/`resolved_at`; naive timestamps are
treated as UTC. The `granularity` argument accepts `day`, `week`, or `month`
and falls back to `day`; invalid samples are excluded and counted in
`exclusion_reasons` rather than silently corrupting the summary.

Outputs include `MTTRSummary` (count, mean, median, min, max, p90, p95),
band counts for `<=15`, `<=30`, `<=60`, `<=120`, `>120`, a per-severity
breakdown, an ordered trend series, and unresolved/excluded record counts. All
numeric outputs honor the `precision` argument (default 2).

### Severity distribution

`compute_severity_distribution()` in
`services/severity_distribution_report.py` normalizes labels by trimming and
lower-casing. Blank or non-string severity/status values go to `unknown`.
Canonical severities (`critical`, `high`, `medium`, `low`, `unknown`) and
statuses (`open`, `resolved`, `unknown`) are always present with zero values;
extra observed labels are appended in sorted order. Ratios are counts over the
total processed rows (or over a severity bucket for per-severity status
ratios), rounded to four decimals, and are `0.0` for an empty dataset.

### KPI views

`services/sla_metrics_aggregation_kpis.py` derives presentation-ready KPIs from
the same aggregation inputs. Keep new KPI helpers in this module rather than
adding presentation logic to the core aggregation service.

## Export flow

Export requests and artifact metadata live in `schemas/exports.py`; the
deterministic rendering pipeline lives in `services/export_preparation.py`.

### Request validation

`ExportRequest` accepts a `resource` (`outages`, `timeline_events`,
`sla_results`, `disputes`, `payments`, `annotations`), a `format` (`csv`, `json`,
`xlsx`, `pdf`), and optional filters, field projection, date bounds, a metadata
flag, and a `requested_by` identifier.

- `filters` may contain at most `MAX_FILTER_KEYS` (50) keys and no blank keys.
  Keys are service-defined exact-match filters.
- `fields` may contain at most `MAX_FIELDS` (100) entries. An empty list is
  invalid; omit the field to export all available columns.
- `date_from` and `date_to` must be timezone-aware, are normalized to UTC, and
  `date_from` may not be after `date_to`.
- `requested_by` is an audit identifier, not an authorization credential. Do not
  place secrets in filters or fields.

### Preparing a dataset

`prepare_export_dataset()` (aliased as `prepare_export`) accepts mappings,
Pydantic-style objects with `model_dump()`, or plain attribute objects.
Behavior worth relying on:

- Column order is explicit when `columns` is supplied, otherwise it follows
  first appearance across records. Blank, excluded, and duplicate keys are
  skipped.
- `sort_by` produces a stable total ordering across booleans, finite numbers,
  and normalized strings; `descending` reverses it. `max_rows` is applied
  **after** sorting and must be non-negative.
- `None` becomes an empty string, enums unwrap to their values, dates and
  datetimes use ISO-8601, nested containers become compact sorted-key JSON,
  bytes decode as UTF-8 with replacement, and non-finite floats/`Decimal`
  values become strings.
- `ExportDataset` exposes `headers`, `row_count`, `to_records()`, `to_csv()`
  (LF line endings), `to_json()`, and `to_dict()`.

### Artifact metadata and job status

`ExportArtifact` carries a filename, content type, byte size, a 64-character
hex SHA-256 digest of the artifact bytes, an opaque `storage_key`, and aware
`generated_at`/`expires_at` timestamps normalized to UTC. Expiry may not precede
generation. `storage_key` must never be a signed download URL or credential.

`ExportResponse` tracks `pending`, `processing`, `completed`, `failed`, or
`expired` jobs. A `completed` job requires an artifact; a `failed` job requires
an error message of at most `MAX_ERROR_LENGTH` (1000) characters;
`completed_at` may not precede `requested_at`.

## Integration notes for contributors

1. Add new analytics or report helpers under `services/` with matching schemas
   in `schemas/` and focused tests in `tests/`.
2. Keep helpers deterministic: no wall-clock reads, database sessions, or
   application state inside the workspace service layer. Pass timestamps and
   records in from the caller.
3. Prefer additive changes. Do not alter core endpoints, ORM models, or
   migrations for a workspace-only feature.
4. If an HTTP surface is needed, define a workspace subrouter and compose it
   with `api/router.py::create_workspace_router()`; core router inclusion is a
   maintainer decision.
5. Reuse `fixtures/` samples for representative inputs and cover edge cases
   (empty inputs, unknown labels, reversed ranges, maximum bounds).
6. Run the repository's lint/format/type/test checks before opening a PR, even
   though this document does not prescribe tool versions.
