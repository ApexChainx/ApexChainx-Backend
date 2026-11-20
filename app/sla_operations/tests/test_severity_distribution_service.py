from dataclasses import dataclass

import pytest

from app.sla_operations.services.severity_distribution import (
    calculate_severity_distribution,
    calculate_status_distribution,
)


@dataclass(frozen=True)
class Outage:
    severity: str
    status: str


def test_calculate_severity_distribution_counts_all_categories() -> None:
    outages = [
        Outage(severity="critical", status="open"),
        Outage(severity="high", status="resolved"),
        Outage(severity="critical", status="resolved"),
    ]

    assert calculate_severity_distribution(iter(outages)) == {
        "critical": 2,
        "high": 1,
        "medium": 0,
        "low": 0,
    }


def test_calculate_status_distribution_counts_all_categories() -> None:
    outages = [
        Outage(severity="low", status="open"),
        Outage(severity="high", status="open"),
        Outage(severity="critical", status="resolved"),
    ]

    assert calculate_status_distribution(outages) == {"open": 2, "resolved": 1}


def test_distributions_return_zero_counts_for_empty_input() -> None:
    assert calculate_severity_distribution([]) == {
        "critical": 0,
        "high": 0,
        "medium": 0,
        "low": 0,
    }
    assert calculate_status_distribution([]) == {"open": 0, "resolved": 0}


@pytest.mark.parametrize(
    ("outages", "calculate", "attribute", "unsupported"),
    [
        ([Outage(severity="urgent", status="open")], calculate_severity_distribution, "severity", "urgent"),
        ([Outage(severity="low", status="closed")], calculate_status_distribution, "status", "closed"),
    ],
)
def test_distributions_reject_unsupported_categories(
    outages: list[Outage],
    calculate,
    attribute: str,
    unsupported: str,
) -> None:
    with pytest.raises(ValueError, match=f"Unsupported outage {attribute}: '{unsupported}'"):
        calculate(outages)
