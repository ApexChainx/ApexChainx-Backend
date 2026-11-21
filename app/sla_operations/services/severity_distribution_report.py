"""Outage counts and ratios by severity and status (#867).

This is a pure aggregation over plain mappings; it never touches a session or
core runtime internals. Severity and status labels are trimmed and lower-cased,
and blank or non-string values are counted in an ``unknown`` bucket. Canonical
categories are always present with zero values, while additional labels seen in
the data are appended in sorted order so the output shape is deterministic.

Ratios are counts divided by the total number of processed rows (or by the
severity bucket for per-severity status ratios), rounded to four decimals, and
are 0.0 for an empty dataset. Non-mapping rows are counted as unknown rather
than raising.

Complexity: O(n + k) time and O(k) space for n rows and k distinct categories.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from pydantic import BaseModel, Field

DEFAULT_SEVERITIES: tuple[str, ...] = ("critical", "high", "medium", "low", "unknown")
DEFAULT_STATUSES: tuple[str, ...] = ("open", "resolved", "unknown")
UNKNOWN = "unknown"
RATIO_DIGITS = 4


class StatusTotal(BaseModel):
    """Count of rows in a status bucket and its share of all rows."""

    count: int
    ratio: float


class SeverityBucket(BaseModel):
    """Count and share of a severity bucket, plus its internal status split."""

    count: int
    ratio: float
    statuses: dict[str, int] = Field(default_factory=dict)
    status_ratios: dict[str, float] = Field(default_factory=dict)


class SeverityDistributionReport(BaseModel):
    """Aggregate distribution for one outage dataset."""

    total: int
    unclassified: int
    by_severity: dict[str, SeverityBucket] = Field(default_factory=dict)
    by_status: dict[str, StatusTotal] = Field(default_factory=dict)


def _normalize(value: object) -> str:
    if isinstance(value, str):
        text = value.strip().lower()
        if text:
            return text
    return UNKNOWN


def _category_order(
    defaults: Iterable[str],
    configured: Iterable[str],
    discovered: Iterable[str],
) -> list[str]:
    ordered: list[str] = []
    for value in (*defaults, *configured, *sorted(discovered)):
        key = _normalize(value)
        if key not in ordered:
            ordered.append(key)
    return ordered


def _ratio(count: int, total: int) -> float:
    return round(count / total, RATIO_DIGITS) if total else 0.0


def _rows(outages: Iterable[object] | None) -> Iterable[object]:
    if outages is None:
        return ()
    try:
        return iter(outages)
    except TypeError:
        return ()


def compute_severity_distribution(
    outages: Iterable[object] | None,
    *,
    severities: Iterable[str] = DEFAULT_SEVERITIES,
    statuses: Iterable[str] = DEFAULT_STATUSES,
) -> SeverityDistributionReport:
    """Compute severity/status counts and ratios for the supplied outages.

    Args:
        outages: Iterable of outage-like mappings; non-mappings are counted as
            unknown on both dimensions. ``None`` is treated as empty.
        severities: Extra severity labels to include even when absent from data.
        statuses: Extra status labels to include even when absent from data.

    Returns:
        A report keyed by normalized category, with canonical categories first
        and any additional data-driven categories sorted afterwards.
    """
    severity_counts: dict[str, int] = {}
    status_counts: dict[str, int] = {}
    status_by_severity: dict[str, dict[str, int]] = {}
    total = 0

    for outage in _rows(outages):
        total += 1
        mapping = outage if isinstance(outage, Mapping) else {}
        severity = _normalize(mapping.get("severity"))
        status = _normalize(mapping.get("status"))
        severity_counts[severity] = severity_counts.get(severity, 0) + 1
        status_counts[status] = status_counts.get(status, 0) + 1
        inner = status_by_severity.setdefault(severity, {})
        inner[status] = inner.get(status, 0) + 1

    severity_order = _category_order(DEFAULT_SEVERITIES, severities, severity_counts)
    status_order = _category_order(DEFAULT_STATUSES, statuses, status_counts)

    by_severity: dict[str, SeverityBucket] = {}
    for severity in severity_order:
        count = severity_counts.get(severity, 0)
        inner = status_by_severity.get(severity, {})
        by_severity[severity] = SeverityBucket(
            count=count,
            ratio=_ratio(count, total),
            statuses={status: inner.get(status, 0) for status in status_order},
            status_ratios={
                status: _ratio(inner.get(status, 0), count) for status in status_order
            },
        )

    by_status = {
        status: StatusTotal(count=status_counts.get(status, 0), ratio=_ratio(status_counts.get(status, 0), total))
        for status in status_order
    }

    return SeverityDistributionReport(
        total=total,
        unclassified=severity_counts.get(UNKNOWN, 0),
        by_severity=by_severity,
        by_status=by_status,
    )
