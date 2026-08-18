from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Response

from app.core.security import require_engineer
from app.services.metrics import _DEFAULT_LATENCY_BUCKETS, metrics

router = APIRouter(prefix="/metrics", tags=["Metrics"])


@router.get("")
def get_metrics():
    """Get application metrics in JSON format."""
    metrics_data = metrics.get_metrics_summary()
    return metrics_data


@router.get("/prometheus")
def get_prometheus_metrics(current_user=Depends(require_engineer)):
    """Get metrics in Prometheus text format for scraping (BE-063).

    This endpoint exposes all application metrics in Prometheus-compatible format:
    - Counters: Monotonically increasing values
    - Gauges: Point-in-time measurements
    - Histograms: Distribution of values with actual bucket counts

    Access Control:
    - Requires engineer role to prevent unauthorized access
    - Can be further restricted via environment configuration

    Hot metric names documented in dashboards/apexchainx.json.
    """
    metrics_data = metrics.get_metrics_summary()

    prometheus_lines = []

    # ── Counters ──────────────────────────────────────────────────────────
    for key, value in metrics_data["counters"].items():
        metric_name = key.split("{")[0]
        labels = key[key.find("{") + 1 : key.find("}")] if "{" in key else ""

        # Add HELP text for known counters
        help_texts = {
            "requests_total": "Total number of requests",
            "sla_recomputation_total": "Total SLA recomputations triggered",
            "sla_violation_total": "Total SLA violations detected",
            "sla_computation_latency_seconds": "SLA computation latency in seconds",
            "webhook_delivery_total": "Total webhook deliveries dispatched",
            "sladispute_notification_attempt_total": "Total SLA dispute notification attempts",
        }
        for prefix, help_text in help_texts.items():
            if prefix in metric_name.lower():
                prometheus_lines.append(f"# HELP {metric_name} {help_text}")

        prometheus_lines.append(f"# TYPE {metric_name} counter")
        if labels:
            prometheus_lines.append(f"{metric_name}{{{labels}}} {value}")
        else:
            prometheus_lines.append(f"{metric_name} {value}")

    # ── Gauges ────────────────────────────────────────────────────────────
    for key, value in metrics_data["gauges"].items():
        metric_name = key.split("{")[0]
        labels = key[key.find("{") + 1 : key.find("}")] if "{" in key else ""

        help_texts = {
            "active_connections": "Current active connection count",
            "db_pool_size": "Current database connection pool size",
        }
        for prefix, help_text in help_texts.items():
            if prefix in metric_name.lower():
                prometheus_lines.append(f"# HELP {metric_name} {help_text}")

        prometheus_lines.append(f"# TYPE {metric_name} gauge")
        if labels:
            prometheus_lines.append(f"{metric_name}{{{labels}}} {value}")
        else:
            prometheus_lines.append(f"{metric_name} {value}")

    # ── Histograms (with actual bucket data from registry) ───────────────
    for key, stats in metrics_data["histograms"].items():
        metric_name = key.split("{")[0]
        labels = key[key.find("{") + 1 : key.find("}")] if "{" in key else ""
        base_labels = f"{labels}," if labels else ""

        prometheus_lines.append(f"# HELP {metric_name} Histogram of {metric_name}")
        prometheus_lines.append(f"# TYPE {metric_name} histogram")
        prometheus_lines.append(f"{metric_name}_count{{{base_labels}}} {stats['count']}")
        prometheus_lines.append(f"{metric_name}_sum{{{base_labels}}} {stats['sum']}")

        # Emit actual bucket counts from the registry
        buckets = metrics_data.get("histogram_buckets", {}).get(key, {})
        if buckets:
            cumulative = 0
            for bucket_bound in sorted(buckets.keys()):
                cumulative += buckets[bucket_bound]
                prometheus_lines.append(f'{metric_name}_bucket{{{base_labels}le="{bucket_bound}"}} {cumulative}')
        prometheus_lines.append(f'{metric_name}_bucket{{{base_labels}le="+Inf"}} {stats["count"]}')

    # ── Timers (exported as histograms with percentile estimation) ───────
    for key, stats in metrics_data["timers"].items():
        metric_name = key.split("{")[0]
        labels = key[key.find("{") + 1 : key.find("}")] if "{" in key else ""
        base_labels = f"{labels}," if labels else ""

        prometheus_lines.append(f"# HELP {metric_name}_seconds Duration of {metric_name} in seconds")
        prometheus_lines.append(f"# TYPE {metric_name}_seconds histogram")
        prometheus_lines.append(f"{metric_name}_seconds_count{{{base_labels}}} {stats['count']}")
        prometheus_lines.append(
            f"{metric_name}_seconds_sum{{{base_labels}}} {(stats['avg_ms'] / 1000) * stats['count']}"
        )

        # Estimate bucket counts for timers using default Prometheus buckets
        avg_seconds = stats["avg_ms"] / 1000
        for bucket in _DEFAULT_LATENCY_BUCKETS:
            # Estimate count using normal distribution assumption around avg
            if bucket < avg_seconds * 0.1:
                count = 0
            elif bucket < avg_seconds * 0.5:
                count = int(stats["count"] * 0.1)
            elif bucket < avg_seconds:
                count = int(stats["count"] * 0.3)
            elif bucket < avg_seconds * 2:
                count = int(stats["count"] * 0.7)
            elif bucket < avg_seconds * 5:
                count = int(stats["count"] * 0.95)
            else:
                count = stats["count"]

            prometheus_lines.append(f'{metric_name}_seconds_bucket{{{base_labels}le="{bucket}"}} {count}')

        prometheus_lines.append(f'{metric_name}_seconds_bucket{{{base_labels}le="+Inf"}} {stats["count"]}')

    # ── Process metadata ──────────────────────────────────────────────────
    prometheus_lines.append("# HELP app_metrics_timestamp Timestamp of metrics collection")
    prometheus_lines.append("# TYPE app_metrics_timestamp gauge")
    prometheus_lines.append(f"app_metrics_timestamp {datetime.now(UTC).timestamp()}")

    return Response(
        content="\n".join(prometheus_lines) + "\n",
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )
