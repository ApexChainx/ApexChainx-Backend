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
        kpi: Dashboard KPI object
        trends: List of trend point objects
        aggregation: Optional performance aggregation object
        format: Export format ('json' or 'csv')

    Returns:
        Exported data in specified format
    """
    format = format.lower()
    trend_points = trends or []
    kpi_data = kpi if isinstance(kpi, dict) else kpi.model_dump(mode="json")

    summary = {
        "kpi": kpi_data,
        "trends": [trend.model_dump(mode="json") for trend in trend_points],
        "trend_count": len(trend_points),
    }

    if aggregation:
        summary["aggregation"] = aggregation.model_dump(mode="json")

    if format == "json":
        return summary

    if format != "csv":
        raise ValueError("Unsupported export format. Use 'json' or 'csv'.")

    # CSV export includes only KPI metrics; trends and aggregation are available via JSON.
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(kpi_data.keys()))
    writer.writeheader()
    writer.writerow(kpi_data)
    return buffer.getvalue()
