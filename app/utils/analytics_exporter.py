"""Analytics export utilities for dashboard and reporting use cases."""

import csv
import io
from typing import Any

from app.models.sla import SLADashboardKPI, SLAPerformanceAggregation, SLATrendPoint


def export_dashboard_kpi(kpi: SLADashboardKPI, format: str = "json") -> Any:
    """Export dashboard KPI data in JSON or CSV format.

    Args:
        kpi: Dashboard KPI object
        format: Export format ('json' or 'csv')

    Returns:
        Exported data in specified format
    """
    format = format.lower()
    data = kpi.model_dump(mode="json")

    if format == "json":
        return data

    if format != "csv":
        raise ValueError("Unsupported export format. Use 'json' or 'csv'.")

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=data.keys())
    writer.writeheader()
    writer.writerow(data)
    return buffer.getvalue()


def export_trends(trends: list[SLATrendPoint], format: str = "json") -> Any:
    """Export trends data in JSON or CSV format.

    Args:
        trends: List of trend point objects
        format: Export format ('json' or 'csv')

    Returns:
        Exported data in specified format
    """
    format = format.lower()
    data = [trend.model_dump(mode="json") for trend in trends]

    if format == "json":
        return data

    if format != "csv":
        raise ValueError("Unsupported export format. Use 'json' or 'csv'.")

    if not data:
        # Handle empty dataset safely
        return "date,total_outages,violations,rewards,penalties\n"

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=data[0].keys())
    writer.writeheader()
    for row in data:
        writer.writerow(row)
    return buffer.getvalue()


def export_performance_aggregation(aggregation: SLAPerformanceAggregation, format: str = "json") -> Any:
    """Export performance aggregation data in JSON or CSV format.

    Args:
        aggregation: Performance aggregation object
        format: Export format ('json' or 'csv')

    Returns:
        Exported data in specified format
    """
    format = format.lower()
    data = aggregation.model_dump(mode="json")

    if format == "json":
        return data

    if format != "csv":
        raise ValueError("Unsupported export format. Use 'json' or 'csv'.")

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=data.keys())
    writer.writeheader()
    writer.writerow(data)
    return buffer.getvalue()


def export_analytics_summary(
    kpi: SLADashboardKPI | dict[str, Any],
    trends: list[SLATrendPoint] | None = None,
    aggregation: SLAPerformanceAggregation | None = None,
    format: str = "json",
) -> Any:
    """Export comprehensive analytics summary combining KPI, trends, and optional aggregation.

    Args:
        kpi: Dashboard KPI object — or a pre-built summary dict with
            ``kpi``/``trends``/``trend_count`` keys (as produced by a previous
            ``export_analytics_summary(..., format="json")`` call).
        trends: List of trend point objects (ignored when ``kpi`` is a dict)
        aggregation: Optional performance aggregation object (ignored when
            ``kpi`` is a dict)
        format: Export format ('json' or 'csv')

    Returns:
        Exported data in specified format
    """
    format = format.lower()

    if isinstance(kpi, dict):
        # Already-serialized summary (round-trip path); re-use as-is.
        summary = dict(kpi)
        kpi_model = summary.get("kpi")
        if not isinstance(kpi_model, SLADashboardKPI):
            kpi_model = SLADashboardKPI.model_validate(kpi_model)
    else:
        summary = {
            "kpi": kpi.model_dump(mode="json"),
            "trends": [trend.model_dump(mode="json") for trend in trends or []],
            "trend_count": len(trends or []),
        }
        if aggregation:
            summary["aggregation"] = aggregation.model_dump(mode="json")
        kpi_model = kpi

    if format == "json":
        return summary

    if format != "csv":
        raise ValueError("Unsupported export format. Use 'json' or 'csv'.")

    # CSV export includes only KPI metrics; trends and aggregation are available via JSON.
    return export_dashboard_kpi(kpi_model, format="csv")
