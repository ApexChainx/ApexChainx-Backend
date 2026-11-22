"""SLA operations workspace utilities.

This package exposes lightweight, dependency-free helpers used by workspace
services and schemas for filtering, sorting, severity normalization, and text
sanitization of in-memory operational records.

Public helpers are re-exported here for convenient access::

    from app.sla_operations.utils import normalize_severity, sanitize_text

The modules in this package intentionally avoid importing core application
state (database sessions, ORM models, settings, etc.) so they remain pure and
easily testable.
"""

from __future__ import annotations

from .filtering import (
    FilterOperator,
    FilterPredicate,
    apply_filters,
    matches_filter,
    matches_filters,
)
from .sanitization import (
    collapse_whitespace,
    sanitize_text,
    strip_control_characters,
    truncate_text,
)
from .severity import (
    SEVERITY_RANK,
    is_valid_severity,
    normalize_severity,
    severity_rank,
    sort_by_severity,
)
from .sorting import (
    SortDirection,
    SortKey,
    apply_sorting,
    sort_records,
)

__all__ = [
    # filtering
    "FilterOperator",
    "FilterPredicate",
    "apply_filters",
    "matches_filter",
    "matches_filters",
    # sorting
    "SortDirection",
    "SortKey",
    "apply_sorting",
    "sort_records",
    # severity
    "SEVERITY_RANK",
    "is_valid_severity",
    "normalize_severity",
    "severity_rank",
    "sort_by_severity",
    # sanitization
    "collapse_whitespace",
    "sanitize_text",
    "strip_control_characters",
    "truncate_text",
]