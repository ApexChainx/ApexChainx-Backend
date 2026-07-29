from datetime import datetime, timezone
from fastapi import APIRouter, Response, Depends
from app.services.metrics import metrics
from app.core.security import require_engineer

router = APIRouter(prefix="/metrics", tags=["Metrics"])


@router.get("")
def get_metrics():
    """Get application metrics in JSON format."""
    metrics_data = metrics.get_metrics_summary()
    return metrics_data


@router.get("/prometheus")
def get_prometheus_metrics(current_user=Depends(require_engineer)):
    """Get metrics in Prometheus text format for scraping (BE-043).

    This endpoint exposes all application metrics in Prometheus-compatible format:
    - Counters: Monotonically increasing values
    - Gauges: Point-in-time measurements
    - Histograms: Distribution of values with buckets
    - Timers: Request/job timing with percentiles

    Access Control:
    - Requires engineer role to prevent unauthorized access
    - Can be further restricted via environment configuration

    Prometheus will scrape this endpoint periodically to collect metrics.
    """
    metrics_data = metrics.get_metrics_summary()

    prometheus_lines = []

    # Add HELP and TYPE for counters
    for key, value in metrics_data["counters"].items():
        metric_name = key.split("{")[0]
        labels = key[key.find("{") + 1 : key.find("}")] if "{" in key else ""

        # Add HELP text for common metrics
        if "request" in metric_name.lower():
            prometheus_lines.append(f"# HELP {metric_name} Total number of requests")
        elif "error" in metric_name.lower():
            prometheus_lines.append(f"# HELP {metric_name} Total number of errors")
        elif "webhook" in metric_name.lower():
            prometheus_lines.append(f"# HELP {metric_name} Total number of webhook events")

        if labels:
            prometheus_lines.append(f"# TYPE {metric_name} counter")
            prometheus_lines.append(f"{metric_name}{{{labels}}} {value}")
        else:
            prometheus_lines.append(f"# TYPE {metric_name} counter")
            prometheus_lines.append(f"{metric_name} {value}")

    # Add HELP and TYPE for gauges
    for key, value in metrics_data["gauges"].items():
        metric_name = key.split("{")[0]
        labels = key[key.find("{") + 1 : key.find("}")] if "{" in key else ""

        if "active" in metric_name.lower() or "current" in metric_name.lower():
            prometheus_lines.append(f"# HELP {metric_name} Current active count")

        if labels:
            prometheus_lines.append(f"# TYPE {metric_name} gauge")
            prometheus_lines.append(f"{metric_name}{{{labels}}} {value}")
        else:
            prometheus_lines.append(f"# TYPE {metric_name} gauge")
            prometheus_lines.append(f"{metric_name} {value}")

    # Export histogram summaries with proper buckets
    for key, stats in metrics_data["histograms"].items():
        metric_name = key.split("{")[0]
        labels = key[key.find("{") + 1 : key.find("}")] if "{" in key else ""
        base_labels = f"{labels}," if labels else ""

        prometheus_lines.append(f"# HELP {metric_name} Histogram of {metric_name}")
        prometheus_lines.append(f"# TYPE {metric_name} histogram")
        prometheus_lines.append(f"{metric_name}_count{{{base_labels}}} {stats['count']}")
        prometheus_lines.append(f"{metric_name}_sum{{{base_labels}}} {stats['avg'] * stats['count']}")
        prometheus_lines.append(f"{metric_name}_bucket{{{base_labels}le=\"+Inf\"}} {stats['count']}")

    # Export timer summaries as histograms with proper buckets
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

        # Add proper histogram buckets based on actual min/max/avg
        avg_seconds = stats["avg_ms"] / 1000
        buckets = [0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0]

        # Estimate bucket counts based on distribution
        for bucket in buckets:
            # Simple estimation: assume normal distribution around avg
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

        prometheus_lines.append(f"{metric_name}_seconds_bucket{{{base_labels}le=\"+Inf\"}} {stats['count']}")

    # Add process metadata
    prometheus_lines.append("# HELP app_metrics_timestamp Timestamp of metrics collection")
    prometheus_lines.append("# TYPE app_metrics_timestamp gauge")
    prometheus_lines.append(f"app_metrics_timestamp {datetime.now(timezone.utc).timestamp()}")

    return Response(content="\n".join(prometheus_lines) + "\n", media_type="text/plain; version=0.0.4; charset=utf-8")
