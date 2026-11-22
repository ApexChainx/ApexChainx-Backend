from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable, Tuple


@dataclass(frozen=True)
class ReportSection:
    """A single titled section within a generated report."""

    title: str
    content: str


@dataclass(frozen=True)
class Report:
    """An assembled report containing ordered sections and a generation timestamp."""

    sections: Tuple[ReportSection, ...] = field(default_factory=tuple)
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def _validate_section(section: object) -> ReportSection:
    """Validate a single section, raising on invalid input."""
    if not isinstance(section, ReportSection):
        raise TypeError(
            f"Expected ReportSection, got {type(section).__name__}"
        )
    if not isinstance(section.title, str) or not section.title.strip():
        raise ValueError("ReportSection title must be a non-blank string")
    return section


def build_report(sections: Iterable[ReportSection]) -> Report:
    """Assemble a Report from an iterable of ReportSection instances.

    Preserves input order. Empty input yields a valid Report with an empty
    sections tuple rather than raising.

    Raises:
        TypeError: If any item is not a ReportSection.
        ValueError: If any section has a blank title.
    """
    validated: list[ReportSection] = []
    for section in sections:
        validated.append(_validate_section(section))
    return Report(sections=tuple(validated))