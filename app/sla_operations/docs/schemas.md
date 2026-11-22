# SLA Operations Workspace Schemas

This document describes the Pydantic schema modules that live under
`app/sla_operations/schemas/`. These schemas define the request/response
contracts used by the isolated SLA operations workspace. They are
**workspace-local** and are **not wired into any core FastAPI routes** under
`app/api/v1/endpoints`. They exist to support contributor-built modules,
internal tooling, and future integration work without destabilizing routed
behavior.

> **Boundary note:** Nothing in this directory is imported by core routers,
> services, repositories, or ORM models. Changes here do not affect existing
> API behavior or persistence schemas. See [`README.md`](../README.md) for the
> workspace overview and [`services.md`](./services.md) for the service layer
> that consumes these schemas.

## Directory layout

```
app/sla_operations/
├── docs/
│   ├── README.md          # workspace overview (see ../README.md)
│   ├── schemas.md         # this file
│   └── services.md        # service layer documentation
└── schemas/
    ├── __init__.py
    ├── outage_insights.py
    ├── settlement_reports.py
    ├── sla_summary.py
    ├── webhook_monitoring.py
    ├── pagination.py
    └── exports.py
```

All schema modules use **Pydantic v2** (`BaseModel`, `Field`,
`ConfigDict`). They are pure data containers: no I/O, no database access, and
no secrets are ever logged or embedded in defaults.

## Validation conventions

The workspace schemas follow these shared conventions:

- **Pydantic v2 models** subclass `pydantic.BaseModel` and use
  `model_config = ConfigDict(...)` for configuration (e.g.
  `extra="forbid"`, `from_attributes=True`).
- **Typed fields** use standard library / Pydantic types
  (`datetime`, `UUID`, `Decimal`, `Literal`, `Enum`). Timestamps are
  timezone-aware UTC (`datetime` with `tzinfo=timezone.utc`).
- **Constraints** are expressed with `Field(...)` (`ge`, `le`, `min_length`,
  `max_length`, `pattern`) rather than custom validators where possible.
- **Enums** are defined as `str, Enum` subclasses so they serialize cleanly to
  JSON and remain forward-compatible with string values.
- **Optional fields** default to `None`; required fields have no default.
- **No secrets**: fields that could carry credentials (e.g. webhook signing
  secrets) are never included in response models; only redacted/masked
  identifiers are exposed.
- **Serialization**: models are dumped with `model_dump(mode="json")` for
  JSON payloads; `Decimal` values are serialized as strings to avoid float
  precision loss.

## Modules

### `outage_insights.py`

**Purpose:** Represent aggregated outage insight records used by analytics
and reporting tooling in the workspace.

**Key models / fields:**

| Model | Field | Type | Notes |
| --- | --- | --- | --- |
| `OutageInsight` | `outage_id` | `UUID` | Outage identifier |
| | `started_at` | `datetime` | UTC, outage start |
| | `resolved_at` | `datetime \| None` | UTC, `None` if ongoing |
| | `duration_seconds` | `int` | `ge=0` |
| | `severity` | `OutageSeverity` | enum: `low`, `medium`, `high`, `critical` |
| | `affected_services` | `list[str]` | service identifiers |
| | `root_cause` | `str \| None` | free-text, `max_length=2000` |
| `OutageInsightList` | `items` | `list[OutageInsight]` | |
| | `total` | `int` | `ge=0` |

**Example payload:**

```json
{
  "outage_id": "3f1c2a9e-8b7d-4c1e-9a2f-1d2e3f4a5b6c",
  "started_at": "2024-05-01T12:00:00Z",
  "resolved_at": "2024-05-01T13:30:00Z",
  "duration_seconds": 5400,
  "severity": "high",
  "affected_services": ["stellar-bridge", "webhook-dispatcher"],
  "root_cause": "Upstream RPC timeout"
}
```

### `settlement_reports.py`

**Purpose:** Describe Stellar/Soroban settlement bridge reports surfaced in
the workspace. These are read-only summaries; the workspace does not submit
transactions.

**Key models / fields:**

| Model | Field | Type | Notes |
| --- | --- | --- | --- |
| `SettlementReport` | `report_id` | `UUID` | |
| | `period_start` | `datetime` | UTC |
| | `period_end` | `datetime` | UTC, must be `>= period_start` |
| | `settled_count` | `int` | `ge=0` |
| | `failed_count` | `int` | `ge=0` |
| | `total_amount` | `Decimal` | `ge=0`, serialized as string |
| | `asset_code` | `str` | e.g. `"USDC"`, `min_length=1` |
| | `status` | `SettlementStatus` | enum: `pending`, `completed`, `partial`, `failed` |
| `SettlementReportList` | `items` | `list[SettlementReport]` | |
| | `total` | `int` | `ge=0` |

**Example payload:**

```json
{
  "report_id": "9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d",
  "period_start": "2024-05-01T00:00:00Z",
  "period_end": "2024-05-02T00:00:00Z",
  "settled_count": 128,
  "failed_count": 3,
  "total_amount": "15230.50",
  "asset_code": "USDC",
  "status": "partial"
}
```

### `sla_summary.py`

**Purpose:** Summarize SLA computation results for a tenant or service over a
reporting window.

**Key models / fields:**

| Model | Field | Type | Notes |
| --- | --- | --- | --- |
| `SLASummary` | `tenant_id` | `UUID` | |
| | `window_start` | `datetime` | UTC |
| | `window_end` | `datetime` | UTC |
| | `uptime_percentage` | `float` | `ge=0`, `le=100` |
| | `target_percentage` | `float` | `ge=0`, `le=100` |
| | `breached` | `bool` | `uptime_percentage < target_percentage` |
| | `total_outages` | `int` | `ge=0` |
| | `total_downtime_seconds` | `int` | `ge=0` |
| `SLASummaryList` | `items` | `list[SLASummary]` | |
| | `total` | `int` | `ge=0` |

**Example payload:**

```json
{
  "tenant_id": "1b2c3d4e-5f6a-4b7c-8d9e-0f1a2b3c4d5e",
  "window_start": "2024-05-01T00:00:00Z",
  "window_end": "2024-06-01T00:00:00Z",
  "uptime_percentage": 99.87,
  "target_percentage": 99.9,
  "breached": true,
  "total_outages": 2,
  "total_downtime_seconds": 3360
}
```

### `webhook_monitoring.py`

**Purpose:** Describe webhook delivery monitoring records for the workspace
dashboard. Signing secrets are never represented in these models.

**Key models / fields:**

| Model | Field | Type | Notes |
| --- | --- | --- | --- |
| `WebhookDelivery` | `delivery_id` | `UUID` | |
| | `endpoint_id` | `UUID` | |
| | `event_type` | `str` | e.g. `"outage.resolved"` |
| | `status` | `DeliveryStatus` | enum: `pending`, `delivered`, `failed`, `retrying` |
| | `attempts` | `int` | `ge=0` |
| | `last_attempt_at` | `datetime \| None` | UTC |
| | `response_code` | `int \| None` | `ge=100`, `le=599` |
| `WebhookMonitoringSummary` | `total_deliveries` | `int` | `ge=0` |
| | `delivered` | `int` | `ge=0` |
| | `failed` | `int` | `ge=0` |
| | `retrying` | `int` | `ge=0` |

**Example payload:**

```json
{
  "delivery_id": "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d",
  "endpoint_id": "f0e1d2c3-b4a5-4968-8776-655443322110",
  "event_type": "outage.resolved",
  "status": "retrying",
  "attempts": 2,
  "last_attempt_at": "2024-05-01T13:31:00Z",
  "response_code": 503
}
```

### `pagination.py`

**Purpose:** Provide reusable pagination request/response envelopes shared by
the list models above.

**Key models / fields:**

| Model | Field | Type | Notes |
| --- | --- | --- | --- |
| `PaginationParams` | `page` | `int` | `ge=1`, default `1` |
| | `page_size` | `int` | `ge=1`, `le=100`, default `20` |
| `PageMeta` | `page` | `int` | `ge=1` |
| | `page_size` | `int` | `ge=1` |
| | `total` | `int` | `ge=0` |
| | `total_pages` | `int` | `ge=0` |

**Example payload:**

```json
{
  "page": 1,
  "page_size": 20,
  "total": 128,
  "total_pages": 7
}
```

### `exports.py`

**Purpose:** Describe export job requests and status records for workspace
report exports (CSV/JSON). Exports are workspace-local and do not trigger core
runtime jobs.

**Key models / fields:**

| Model | Field | Type | Notes |
| --- | --- | --- | --- |
| `ExportRequest` | `export_type` | `ExportType` | enum: `outage_insights`, `settlement_reports`, `sla_summary`, `webhook_monitoring` |
| | `format` | `ExportFormat` | enum: `csv`, `json` |
| | `period_start` | `datetime` | UTC |
| | `period_end` | `datetime` | UTC |
| `ExportStatus` | `export_id` | `UUID` | |
| | `status` | `ExportState` | enum: `queued`, `processing`, `completed`, `failed` |
| | `created_at` | `datetime` | UTC |
| | `download_url` | `str \| None` | present only when `completed` |

**Example payload:**

```json
{
  "export_type": "sla_summary",
  "format": "csv",
  "period_start": "2024-05-01T00:00:00Z",
  "period_end": "2024-06-01T00:00:00Z"
}
```

## Integration notes

- **Not wired into core routes.** These schemas are consumed only by
  workspace modules under `app/sla_operations/`. No core endpoint, router,
  service, repository, or ORM model imports them.
- **No persistence coupling.** The schemas are transport/validation
  containers; they do not map to database tables and require no Alembic
  migration.
- **Consumers.** The workspace service layer (see
  [`services.md`](./services.md)) is the primary consumer. If a future
  integration is proposed, it must be documented as a minimal, explicit
  change and reviewed against the core runtime boundaries.
- **Security.** No secrets, tokens, or signing keys are represented in any
  schema. Response models expose only redacted identifiers.

## Related documentation

- [`README.md`](../README.md) — workspace overview and contribution workflow.
- [`services.md`](./services.md) — service layer that consumes these schemas.