"""Self-contained data quality checks for SLA operations datasets.

This module provides deterministic, dependency-free validation of
operational datasets (e.g. outage records, SLA snapshots, settlement
rows) against a declarative set of :class:`FieldSpec` definitions.

Design notes / assumptions
--------------------------
* The service is intentionally self-contained: it does **not** import any
  core ``app`` modules, ORM models, or runtime internals. It only relies on
  the Python standard library so it can be reused by tooling, notebooks and
  tests in isolation.
* Records are validated in the exact order they are supplied. Findings are
  emitted in that same order, and within a single record findings are
  emitted in the order of ``field_specs``. This makes output deterministic
  and easy to diff/snapshot in tests.
* ``record_index`` is the zero-based position of the record in the input
  iterable. It is *not* a database primary key.
* Blank strings (``""`` or whitespace-only) are treated as missing when
  ``treat_blank_as_missing`` is ``True`` (the default). ``None`` is always
  treated as missing for required fields.
* Type checks use :func:`isinstance` against ``expected_type``. ``None``
  values are skipped for optional fields (they are simply "absent").
  ``bool`` is a subclass of ``int`` in Python; callers who need to reject
  booleans for integer fields should pass an explicit ``allowed_values``
  or a custom ``expected_type`` tuple.
* ``allowed_values`` is checked with membership (``in``); it is only
  applied when the value is present and (if a type is declared) of the
  expected type, so a single value does not produce both a type-mismatch
  and an out-of-range finding.

Complexity
----------
For ``n`` records and ``m`` field specs the runtime is ``O(n * m)`` with
``O(1)`` auxiliary space per record (findings are accumulated in the
result). Empty input yields an empty, clean result without raising.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Optional, Sequence, Tuple, Type, Union

__all__ = [
    "DataQualityFinding",
    "DataQualityResult",
    "FieldSpec",
    "run_data_quality_checks",
    "ISSUE_MISSING_REQUIRED",
    "ISSUE_TYPE_MISMATCH",
    "ISSUE_INCONSISTENT",
]

# Canonical issue type identifiers. Kept as module constants so callers and
# tests can reference them without relying on string literals.
ISSUE_MISSING_REQUIRED = "missing_required"
ISSUE_TYPE_MISMATCH = "type_mismatch"
ISSUE_INCONSISTENT = "inconsistent"

# ``expected_type`` accepts a single type or a tuple of types (mirroring the
# second argument of :func:`isinstance`).
ExpectedType = Union[Type[Any], Tuple[Type[Any], ...]]


@dataclass(frozen=True)
class DataQualityFinding:
    """A single data quality problem detected in one record field.

    Attributes:
        field: Name of the field the finding relates to.
        record_index: Zero-based index of the offending record in the input.
        issue_type: One of ``missing_required``, ``type_mismatch`` or
            ``inconsistent``.
        message: Human-readable description of the problem.
    """

    field: str
    record_index: int
    issue_type: str
    message: str


@dataclass
class DataQualityResult:
    """Aggregate result of a data quality run.

    Attributes:
        findings: All findings, in deterministic (record, field) order.
        records_checked: Number of records that were inspected.
    """

    findings: list[DataQualityFinding] = field(default_factory=list)
    records_checked: int = 0

    @property
    def is_clean(self) -> bool:
        """``True`` when no findings were produced."""
        return not self.findings

    def findings_for_field(self, field_name: str) -> list[DataQualityFinding]:
        """Return findings restricted to ``field_name`` (order preserved)."""
        return [f for f in self.findings if f.field == field_name]

    def findings_for_record(self, record_index: int) -> list[DataQualityFinding]:
        """Return findings restricted to ``record_index`` (order preserved)."""
        return [f for f in self.findings if f.record_index == record_index]


@dataclass(frozen=True)
class FieldSpec:
    """Declarative validation rules for a single record field.

    Attributes:
        name: Field name to look up in each record mapping.
        required: When ``True`` the field must be present and non-missing.
        expected_type: A type or tuple of types validated with
            :func:`isinstance`. ``None`` disables the type check.
        allowed_values: Optional set of permitted values. Membership is
            checked only for present, correctly-typed values.
    """

    name: str
    required: bool = True
    expected_type: Optional[ExpectedType] = None
    allowed_values: Optional[frozenset] = None


def _is_blank(value: Any) -> bool:
    """Return ``True`` for empty or whitespace-only strings."""
    return isinstance(value, str) and value.strip() == ""


def _is_missing(value: Any, treat_blank_as_missing: bool) -> bool:
    """Return ``True`` when ``value`` should be considered absent."""
    if value is None:
        return True
    if treat_blank_as_missing and _is_blank(value):
        return True
    return False


def _type_name(expected_type: ExpectedType) -> str:
    """Render ``expected_type`` for use in finding messages."""
    if isinstance(expected_type, tuple):
        return " or ".join(t.__name__ for t in expected_type)
    return expected_type.__name__


def _check_record(
    record: Mapping[str, Any],
    record_index: int,
    field_specs: Sequence[FieldSpec],
    treat_blank_as_missing: bool,
) -> list[DataQualityFinding]:
    """Validate a single record and return its findings in spec order."""
    findings: list[DataQualityFinding] = []

    for spec in field_specs:
        present = spec.name in record
        value = record.get(spec.name)
        missing = (not present) or _is_missing(value, treat_blank_as_missing)

        if missing:
            if spec.required:
                reason = "is absent" if not present else "is empty"
                findings.append(
                    DataQualityFinding(
                        field=spec.name,
                        record_index=record_index,
                        issue_type=ISSUE_MISSING_REQUIRED,
                        message=f"Required field '{spec.name}' {reason}.",
                    )
                )
            # Optional missing fields are fine; nothing else to validate.
            continue

        if spec.expected_type is not None and not isinstance(value, spec.expected_type):
            findings.append(
                DataQualityFinding(
                    field=spec.name,
                    record_index=record_index,
                    issue_type=ISSUE_TYPE_MISMATCH,
                    message=(
                        f"Field '{spec.name}' expected "
                        f"{_type_name(spec.expected_type)} but got "
                        f"{type(value).__name__}."
                    ),
                )
            )
            # Skip value-range checks for wrongly-typed values to avoid
            # cascading, misleading findings.
            continue

        if spec.allowed_values is not None and value not in spec.allowed_values:
            findings.append(
                DataQualityFinding(
                    field=spec.name,
                    record_index=record_index,
                    issue_type=ISSUE_INCONSISTENT,
                    message=(
                        f"Field '{spec.name}' value {value!r} is not in the "
                        f"allowed set."
                    ),
                )
            )

    return findings


def run_data_quality_checks(
    records: Iterable[Mapping[str, Any]],
    field_specs: Sequence[FieldSpec],
    *,
    treat_blank_as_missing: bool = True,
) -> DataQualityResult:
    """Validate ``records`` against ``field_specs`` and collect findings.

    Args:
        records: Iterable of record mappings. Iterated exactly once, in
            order; the input is never mutated.
        field_specs: Ordered sequence of :class:`FieldSpec` rules. Findings
            within a record follow this order.
        treat_blank_as_missing: When ``True`` (default), empty or
            whitespace-only strings are treated as missing values.

    Returns:
        A :class:`DataQualityResult` with findings in deterministic
        ``(record_index, field_specs order)`` order and the number of
        records inspected. Empty input returns a clean result with
        ``records_checked == 0``.

    Complexity:
        ``O(n * m)`` time for ``n`` records and ``m`` field specs.
    """
    result = DataQualityResult()

    if not field_specs:
        # Still count records so callers can report dataset size, but there
        # is nothing to validate.
        count = 0
        for _ in records:
            count += 1
        result.records_checked = count
        return result

    for index, record in enumerate(records):
        result.records_checked += 1
        if not isinstance(record, Mapping):
            # Defensive: a non-mapping record cannot be validated field by
            # field. Emit a single finding rather than raising.
            result.findings.append(
                DataQualityFinding(
                    field="<record>",
                    record_index=index,
                    issue_type=ISSUE_INCONSISTENT,
                    message=(
                        f"Record is not a mapping (got "
                        f"{type(record).__name__})."
                    ),
                )
            )
            continue
        result.findings.extend(
            _check_record(record, index, field_specs, treat_blank_as_missing)
        )

    return result