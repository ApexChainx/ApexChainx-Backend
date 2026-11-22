"""Tests for the SLA operations report generation service.

These tests exercise the workspace-local report generation service at
``app.sla_operations.services.report_generation``. They are deterministic and
isolated from core application state (no database, no network, no globals).
"""

from __future__ import annotations

import pytest

from app.sla_operations.services.report_generation import (
    Report,
    ReportSection,
    ReportGenerationService,
)


@pytest.fixture()
def service() -> ReportGenerationService:
    """Provide a fresh report generation service for each test."""
    return ReportGenerationService()


def _make_section(title: str, body: str = "", order: int = 0) -> ReportSection:
    """Build a ReportSection helper for tests."""
    return ReportSection(title=title, body=body, order=order)


def test_assemble_multi_section_preserves_order_and_content(
    service: ReportGenerationService,
) -> None:
    """Multiple sections are assembled in order with content preserved."""
    sections = [
        _make_section("Summary", "All systems nominal.", order=0),
        _make_section("Incidents", "Two incidents resolved.", order=1),
        _make_section("SLA Metrics", "Uptime 99.95%.", order=2),
    ]

    report = service.generate(sections)

    assert isinstance(report, Report)
    assert len(report.sections) == 3
    assert [s.title for s in report.sections] == [
        "Summary",
        "Incidents",
        "SLA Metrics",
    ]
    assert [s.body for s in report.sections] == [
        "All systems nominal.",
        "Two incidents resolved.",
        "Uptime 99.95%.",
    ]
    assert [s.order for s in report.sections] == [0, 1, 2]


def test_assemble_sorts_sections_by_order(
    service: ReportGenerationService,
) -> None:
    """Sections are ordered by their ``order`` attribute regardless of input."""
    sections = [
        _make_section("Third", "c", order=2),
        _make_section("First", "a", order=0),
        _make_section("Second", "b", order=1),
    ]

    report = service.generate(sections)

    assert [s.title for s in report.sections] == ["First", "Second", "Third"]


def test_empty_input_yields_valid_report_with_zero_sections(
    service: ReportGenerationService,
) -> None:
    """An empty iterable produces a valid Report with no sections."""
    report = service.generate([])

    assert isinstance(report, Report)
    assert report.sections == []
    assert len(report.sections) == 0


def test_empty_input_is_not_shared_between_reports(
    service: ReportGenerationService,
) -> None:
    """Empty reports do not share mutable section state."""
    first = service.generate([])
    second = service.generate([])

    first.sections.append(_make_section("Injected", "x", order=0))

    assert second.sections == []
    assert len(first.sections) == 1


def test_invalid_section_type_raises_type_error(
    service: ReportGenerationService,
) -> None:
    """Non-ReportSection entries raise TypeError."""
    with pytest.raises(TypeError):
        service.generate(["not a section"])  # type: ignore[list-item]


def test_invalid_section_type_in_mixed_input_raises_type_error(
    service: ReportGenerationService,
) -> None:
    """A single invalid entry among valid ones raises TypeError."""
    sections = [
        _make_section("Valid", "ok", order=0),
        {"title": "dict", "body": "nope"},  # type: ignore[list-item]
    ]

    with pytest.raises(TypeError):
        service.generate(sections)  # type: ignore[arg-type]


def test_blank_title_raises_value_error(
    service: ReportGenerationService,
) -> None:
    """A section with a blank title raises ValueError."""
    with pytest.raises(ValueError):
        service.generate([_make_section("   ", "body", order=0)])


def test_empty_title_raises_value_error(
    service: ReportGenerationService,
) -> None:
    """A section with an empty title raises ValueError."""
    with pytest.raises(ValueError):
        service.generate([_make_section("", "body", order=0)])