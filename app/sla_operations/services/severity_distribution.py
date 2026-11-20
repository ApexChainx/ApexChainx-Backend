"""Pure helpers for aggregating outage severity and status counts."""

from collections.abc import Iterable
from typing import Literal, Protocol


class OutageClassification(Protocol):
    """The outage attributes required by the distribution helpers."""

    severity: str
    status: str


_SEVERITIES = ("critical", "high", "medium", "low")
_STATUSES = ("open", "resolved")


def _distribution(
    outages: Iterable[OutageClassification],
    attribute: Literal["severity", "status"],
    categories: tuple[str, ...],
) -> dict[str, int]:
    counts = {category: 0 for category in categories}
    for outage in outages:
        value = getattr(outage, attribute)
        if not isinstance(value, str) or value not in counts:
            raise ValueError(f"Unsupported outage {attribute}: {value!r}")
        counts[value] += 1
    return counts


def calculate_severity_distribution(outages: Iterable[OutageClassification]) -> dict[str, int]:
    """Count outages for every supported severity, including categories with no matches."""
    return _distribution(outages, "severity", _SEVERITIES)


def calculate_status_distribution(outages: Iterable[OutageClassification]) -> dict[str, int]:
    """Count outages for every supported status, including categories with no matches."""
    return _distribution(outages, "status", _STATUSES)
