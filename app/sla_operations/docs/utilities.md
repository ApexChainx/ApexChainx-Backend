# SLA Operations Workspace Utilities

This document describes the utility/service modules that live under
`app/sla_operations/services/`. These modules provide isolated, reusable
building blocks for the SLA operations contributor workspace.

> **Scope note:** Everything documented here is **workspace-local**. These
> utilities are **not wired into core routers, services, repositories, or ORM
> models**, and core code must not depend on or modify them. See the
> [Boundaries](#boundaries) section for details.

## Summary

| Module | Path | Purpose |
| --- | --- | --- |
| Bulk import validation | [`bulk_import_validation.py`](../services/bulk_import_validation.py) | Validate bulk-imported SLA records before persistence. |
| Compliance evaluation | [`compliance_evaluation.py`](../services/compliance_evaluation.py) | Evaluate SLA compliance against targets. |
| Dispute summary | [`dispute_summary.py`](../services/dispute_summary.py) | Summarize SLA dispute records. |
| Export preparation | [`export_preparation.py`](../services/export_preparation.py) | Prepare SLA data for export. |
| Health scoring | [`health_scoring.py`](../services/health_scoring.py) | Compute health scores for SLA/outage data. |
| Outage MTTR analysis | [`outage_mttr_analysis.py`](../services/outage_mttr_analysis.py) | Analyze mean time to repair (MTTR) for outages. |
| Reconciliation checks | [`reconciliation_checks.py`](../services/reconciliation_checks.py) | Run reconciliation checks across SLA records. |
| Settlement breakdown | [`settlement_breakdown.py`](../services/settlement_breakdown.py) | Break down settlement amounts. |
| Severity distribution | [`severity_distribution.py`](../services/severity_distribution.py) | Compute severity distributions. |
| Severity distribution report | [`severity_distribution_report.py`](../services/severity_distribution_report.py) | Render severity distribution reports. |
| SLA metrics aggregation | [`sla_metrics_aggregation.py`](../services/sla_metrics_aggregation.py) | Aggregate SLA metrics. |
| SLA metrics aggregation KPIs | [`sla_metrics_aggregation_kpis.py`](../services/sla_metrics_aggregation_kpis.py) | Compute KPI-level SLA aggregations. |
| Timeline assembly | [`timeline_assembly.py`](../services/timeline_assembly.py) | Assemble outage/SLA timelines. |
| Webhook delivery insights | [`webhook_delivery_insights.py`](../services/webhook_delivery_insights.py) | Derive insights from webhook delivery records. |

All paths above are relative to this document
(`app/sla_operations/docs/utilities.md`).

## Conventions

- **Pure functions** take inputs and return outputs without mutating external
  state. They are safe to call from tests and scripts.
- **Side-effecting functions** perform I/O (database reads/writes, network
  calls, file writes). These are called out explicitly per module.
- Inputs are expected to be plain Python structures (dicts, lists, dataclasses,
  or Pydantic models) unless noted otherwise. Utilities should not assume a
  specific ORM session is present.
- Utilities must not log secrets or sensitive payloads.

---

## `bulk_import_validation.py`

**Purpose:** Validate batches of SLA records prior to import so that malformed
rows are rejected with clear, aggregated errors.

**Key public functions:**

```python
def validate_bulk_import(records: list[dict]) -> dict:
    """Return {"valid": [...], "invalid": [{"index": int, "errors": [...]}]}."""

def validate_record(record: dict) -> list[str]:
    """Return a list of validation error messages for a single record."""
```

**Input/output expectations:**
- `records` is a list of dicts representing candidate SLA records.
- Returns a dict separating valid records from invalid ones, each invalid entry
  carrying its original index and error messages.

**Purity:** Pure. No I/O; safe to call repeatedly.

**Usage example:**

```python
from app.sla_operations.services.bulk_import_validation import validate_bulk_import

result = validate_bulk_import([
    {"sla_id": "SLA-1", "target_seconds": 3600},
    {"sla_id": "", "target_seconds": -1},
])
print(result["invalid"])  # [{"index": 1, "errors": [...]}]
```

---

## `compliance_evaluation.py`

**Purpose:** Evaluate whether SLA records meet their configured targets and
produce compliance outcomes.

**Key public functions:**

```python
def evaluate_compliance(records: list[dict], targets: dict) -> list[dict]:
    """Return per-record compliance results with a boolean `compliant` flag."""

def is_compliant(actual_seconds: float, target_seconds: float) -> bool:
    """Return True when actual meets the target."""
```

**Input/output expectations:**
- `records` contains observed metrics; `targets` maps SLA identifiers to target
  values.
- Returns a list of result dicts including the record identifier, actual value,
  target value, and compliance flag.

**Purity:** Pure.

**Usage example:**

```python
from app.sla_operations.services.compliance_evaluation import evaluate_compliance

results = evaluate_compliance(
    records=[{"sla_id": "SLA-1", "actual_seconds": 1800}],
    targets={"SLA-1": 3600},
)
```

---

## `dispute_summary.py`

**Purpose:** Summarize dispute records for reporting and dashboards.

**Key public functions:**

```python
def summarize_disputes(disputes: list[dict]) -> dict:
    """Return counts grouped by status and reason."""
```

**Input/output expectations:**
- `disputes` is a list of dicts with at least `status` and `reason` keys.
- Returns a dict with `total`, `by_status`, and `by_reason` mappings.

**Purity:** Pure.

**Usage example:**

```python
from app.sla_operations.services.dispute_summary import summarize_disputes

summary = summarize_disputes([
    {"status": "open", "reason": "latency"},
    {"status": "closed", "reason": "latency"},
])
```

---

## `export_preparation.py`

**Purpose:** Transform SLA data into a flat, export-friendly structure (e.g.
for CSV/JSON export).

**Key public functions:**

```python
def prepare_export(records: list[dict], columns: list[str] | None = None) -> list[dict]:
    """Return records projected onto `columns` (all keys if None)."""

def flatten_record(record: dict) -> dict:
    """Return a single-level dict with nested keys joined by dots."""
```

**Input/output expectations:**
- `records` is a list of dicts; `columns` optionally restricts output fields.
- Returns a list of flat dicts suitable for serialization.

**Purity:** Pure.

**Usage example:**

```python
from app.sla_operations.services.export_preparation import prepare_export

rows = prepare_export(
    records=[{"sla_id": "SLA-1", "meta": {"region": "us"}}],
    columns=["sla_id", "meta.region"],
)
```

---

## `health_scoring.py`

**Purpose:** Compute a normalized health score for SLA/outage data to support
at-a-glance status reporting.

**Key public functions:**

```python
def compute_health_score(metrics: dict) -> float:
    """Return a score in [0.0, 1.0] derived from the provided metrics."""

def score_records(records: list[dict]) -> list[dict]:
    """Return records annotated with a `health_score` field."""
```

**Input/output expectations:**
- `metrics` is a dict of numeric inputs (e.g. compliance rate, MTTR).
- Returns a float clamped to `[0.0, 1.0]`.

**Purity:** Pure.

**Usage example:**

```python
from app.sla_operations.services.health_scoring import compute_health_score

score = compute_health_score({"compliance_rate": 0.95, "mttr_seconds": 1200})
```

---

## `outage_mttr_analysis.py`

**Purpose:** Analyze mean time to repair (MTTR) across outages.

**Key public functions:**

```python
def compute_mttr(outages: list[dict]) -> float:
    """Return the mean repair duration in seconds."""

def mttr_by_group(outages: list[dict], group_by: str) -> dict:
    """Return MTTR grouped by the given key."""
```

**Input/output expectations:**
- Each outage dict is expected to include start/end timestamps (or a duration).
- Returns a float (seconds) or a mapping of group → MTTR.

**Purity:** Pure.

**Usage example:**

```python
from app.sla_operations.services.outage_mttr_analysis import compute_mttr

mttr = compute_mttr([
    {"duration_seconds": 600},
    {"duration_seconds": 1200},
])
```

---

## `reconciliation_checks.py`

**Purpose:** Run consistency checks across SLA records to surface mismatches
(e.g. totals that do not add up).

**Key public functions:**

```python
def run_reconciliation(records: list[dict]) -> dict:
    """Return {"ok": bool, "issues": [...]} describing detected mismatches."""

def check_totals(records: list[dict]) -> list[str]:
    """Return a list of human-readable issue descriptions."""
```

**Input/output expectations:**
- `records` is a list of dicts with numeric fields to reconcile.
- Returns a summary dict with an overall `ok` flag and a list of issues.

**Purity:** Pure.

**Usage example:**

```python
from app.sla_operations.services.reconciliation_checks import run_reconciliation

report = run_reconciliation([{"expected": 10, "actual": 9}])
print(report["ok"])  # False
```

---

## `settlement_breakdown.py`

**Purpose:** Break down settlement amounts into their component parts for
reporting.

**Key public functions:**

```python
def breakdown_settlement(settlement: dict) -> dict:
    """Return a dict of component amounts and their total."""

def breakdown_many(settlements: list[dict]) -> list[dict]:
    """Return per-settlement breakdowns."""
```

**Input/output expectations:**
- `settlement` contains component amounts (e.g. base, penalty, bonus).
- Returns a dict with each component and a computed `total`.

**Purity:** Pure.

**Usage example:**

```python
from app.sla_operations.services.settlement_breakdown import breakdown_settlement

parts = breakdown_settlement({"base": 100, "penalty": -10, "bonus": 5})
```

---

## `severity_distribution.py`

**Purpose:** Compute the distribution of records across severity levels.

**Key public functions:**

```python
def severity_distribution(records: list[dict], key: str = "severity") -> dict:
    """Return a mapping of severity → count."""

def severity_percentages(records: list[dict], key: str = "severity") -> dict:
    """Return a mapping of severity → percentage of total."""
```

**Input/output expectations:**
- `records` is a list of dicts; `key` selects the severity field.
- Returns counts or percentages as a dict.

**Purity:** Pure.

**Usage example:**

```python
from app.sla_operations.services.severity_distribution import severity_distribution

dist = severity_distribution([
    {"severity": "high"},
    {"severity": "low"},
])
```

---

## `severity_distribution_report.py`

**Purpose:** Render a human-readable report from a severity distribution.

**Key public functions:**

```python
def build_report(distribution: dict) -> str:
    """Return a formatted text report for the given distribution."""

def build_report_rows(distribution: dict) -> list[dict]:
    """Return structured rows suitable for tabular rendering."""
```

**Input/output expectations:**
- `distribution` is a mapping of severity → count (as produced by
  `severity_distribution`).
- Returns a string report or a list of row dicts.

**Purity:** Pure.

**Usage example:**

```python
from app.sla_operations.services.severity_distribution import severity_distribution
from app.sla_operations.services.severity_distribution_report import build_report

dist = severity_distribution([{"severity": "high"}])
print(build_report(dist))
```

---

## `sla_metrics_aggregation.py`

**Purpose:** Aggregate raw SLA metrics into summary statistics.

**Key public functions:**

```python
def aggregate_metrics(records: list[dict], fields: list[str]) -> dict:
    """Return count/min/max/mean/sum for each requested numeric field."""

def aggregate_by_group(records: list[dict], group_by: str, fields: list[str]) -> dict:
    """Return per-group aggregations keyed by the group value."""
```

**Input/output expectations:**
- `records` is a list of dicts; `fields` names numeric fields to aggregate.
- Returns a dict of statistics per field (or per group).

**Purity:** Pure.

**Usage example:**

```python
from app.sla_operations.services.sla_metrics_aggregation import aggregate_metrics

stats = aggregate_metrics(
    records=[{"latency": 100}, {"latency": 200}],
    fields=["latency"],
)
```

---

## `sla_metrics_aggregation_kpis.py`

**Purpose:** Compute KPI-level aggregations (e.g. compliance rate, average
MTTR) on top of aggregated metrics.

**Key public functions:**

```python
def compute_kpis(records: list[dict]) -> dict:
    """Return a dict of named KPI values."""

def kpi_summary(records: list[dict]) -> list[dict]:
    """Return KPIs as a list of {"name", "value"} dicts for display."""
```

**Input/output expectations:**
- `records` is a list of dicts containing the raw metrics needed for KPIs.
- Returns named KPI values or a display-ready list.

**Purity:** Pure.

**Usage example:**

```python
from app.sla_operations.services.sla_metrics_aggregation_kpis import compute_kpis

kpis = compute_kpis([{"compliant": True}, {"compliant": False}])
```

---

## `timeline_assembly.py`

**Purpose:** Assemble chronological timelines from outage and SLA events.

**Key public functions:**

```python
def assemble_timeline(events: list[dict]) -> list[dict]:
    """Return events sorted chronologically by timestamp."""

def merge_timelines(*timelines: list[dict]) -> list[dict]:
    """Return a single merged, sorted timeline."""
```

**Input/output expectations:**
- Each event dict includes a timestamp field (e.g. `timestamp` or `at`).
- Returns a new sorted list; input lists are not mutated.

**Purity:** Pure.

**Usage example:**

```python
from app.sla_operations.services.timeline_assembly import assemble_timeline

timeline = assemble_timeline([
    {"timestamp": "2024-01-01T00:00:00Z", "event": "start"},
    {"timestamp": "2024-01-01T01:00:00Z", "event": "end"},
])
```

---

## `webhook_delivery_insights.py`

**Purpose:** Derive insights (success rates, failure reasons, retry counts)
from webhook delivery records.

**Key public functions:**

```python
def delivery_insights(deliveries: list[dict]) -> dict:
    """Return success rate, failure counts, and top failure reasons."""

def failure_reasons(deliveries: list[dict]) -> dict:
    """Return a mapping of failure reason → count."""
```

**Input/output expectations:**
- `deliveries` is a list of dicts with at least `status` and optional `reason`.
- Returns a summary dict of derived metrics.

**Purity:** Pure. This module analyzes already-fetched delivery records; it does
**not** send webhooks or perform network I/O.

**Usage example:**

```python
from app.sla_operations.services.webhook_delivery_insights import delivery_insights

insights = delivery_insights([
    {"status": "success"},
    {"status": "failed", "reason": "timeout"},
])
```

---

## Boundaries

- **Workspace-local only.** All modules under `app/sla_operations/services/`
  are isolated utilities for the SLA operations contributor workspace. They are
  **not** imported by core routers (`app/api/v1/endpoints`), services
  (`app/services`), repositories (`app/repositories`), or ORM models
  (`app/models`).
- **Do not modify from core code.** Core code must not depend on these modules,
  and changes to core runtime components must not be made to accommodate them.
  Any integration would require a separate, explicitly scoped issue.
- **No persistence assumptions.** Utilities operate on plain Python structures
  and do not assume a database session, ORM model, or migration. Persistence
  concerns remain in the core layers.
- **No secrets in logs.** Utilities must not log secrets or sensitive payloads.
- **Additive changes.** Contributions here should be additive and must not
  alter existing API behavior or persistence schemas.

## Related documentation

- Workspace overview: [`../README.md`](../README.md) (if present)
- Repository root: [`../../../README.md`](../../../README.md)