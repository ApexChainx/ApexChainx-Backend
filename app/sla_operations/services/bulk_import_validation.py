"""Deterministic validation for bulk outage import rows (#866).

The validator operates on plain mappings, so it can run before persistence or
inside a worker without a database session. Rows are checked in input order and
every issue is returned as a structured finding instead of being raised.

Assumptions:
* ``row`` is a zero-based position in the supplied dataset; ``row=-1`` denotes
  a dataset-level finding.
* Severity/status values are matched case-insensitively after trimming.
* Timestamps must be timezone-aware; naive datetimes are rejected.
* Unknown fields are warnings so newer import payloads remain forward-compatible.
* An empty dataset is invalid and yields one ``empty_dataset`` finding.

Complexity: O(n * f) for n rows and f fields, with findings in input order.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

DEFAULT_SEVERITIES: tuple[str, ...] = ("critical", "high", "medium", "low")
DEFAULT_STATUSES: tuple[str, ...] = ("open", "resolved")
REQUIRED_FIELDS: tuple[str, ...] = (
    "id",
    "site_name",
    "severity",
    "status",
    "detected_at",
    "description",
    "affected_services",
)
OPTIONAL_FIELDS: tuple[str, ...] = (
    "site_id",
    "affected_subscribers",
    "assigned_to",
    "created_by",
    "resolved_at",
)
KNOWN_FIELDS: frozenset[str] = frozenset(REQUIRED_FIELDS + OPTIONAL_FIELDS)


class FindingCode(str, Enum):
    empty_dataset = "empty_dataset"
    invalid_dataset = "invalid_dataset"
    missing_field = "missing_field"
    invalid_type = "invalid_type"
    invalid_value = "invalid_value"
    invalid_timestamp = "invalid_timestamp"
    invalid_resolution_window = "invalid_resolution_window"
    missing_resolution_timestamp = "missing_resolution_timestamp"
    unexpected_field = "unexpected_field"
    duplicate_id = "duplicate_id"


class FindingLevel(str, Enum):
    error = "error"
    warning = "warning"


class ImportFinding(BaseModel):
    """One validation observation; warnings do not make a row invalid."""

    row: int
    code: FindingCode
    message: str
    level: FindingLevel = FindingLevel.error
    field: str | None = None
    id: str | None = None


class RowValidation(BaseModel):
    """Per-row result, preserving the caller's row order."""

    row: int
    valid: bool
    id: str | None = None
    findings: list[ImportFinding] = Field(default_factory=list)


class BulkImportValidationReport(BaseModel):
    """Aggregate result for one bulk import dataset."""

    total_rows: int
    valid_rows: int
    invalid_rows: int
    valid: bool
    rows: list[RowValidation] = Field(default_factory=list)
    accepted_ids: list[str] = Field(default_factory=list)
    findings: list[ImportFinding] = Field(default_factory=list)


def _finding(
    findings: list[ImportFinding],
    row: int,
    code: FindingCode,
    message: str,
    *,
    field: str | None = None,
    row_id: str | None = None,
    level: FindingLevel = FindingLevel.error,
) -> None:
    findings.append(
        ImportFinding(row=row, code=code, message=message, level=level, field=field, id=row_id)
    )


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _parse_timestamp(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None and value.utcoffset() is not None else None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith(("Z", "z")):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None


def _validate_row(
    row: object,
    index: int,
    seen_ids: set[str],
    *,
    allowed_severities: frozenset[str],
    allowed_statuses: frozenset[str],
) -> tuple[RowValidation, str | None]:
    findings: list[ImportFinding] = []
    if not isinstance(row, Mapping):
        _finding(findings, index, FindingCode.invalid_type, f"expected a mapping, got {type(row).__name__}")
        return RowValidation(row=index, valid=False, findings=findings), None

    row_id = _text(row.get("id"))
    if row_id is None:
        _finding(findings, index, FindingCode.missing_field, "id is required", field="id")
    elif row_id in seen_ids:
        _finding(
            findings,
            index,
            FindingCode.duplicate_id,
            f"duplicate outage id: {row_id}",
            field="id",
            row_id=row_id,
        )
    else:
        seen_ids.add(row_id)

    for field in ("site_name", "description"):
        if _text(row.get(field)) is None:
            _finding(findings, index, FindingCode.missing_field, f"{field} is required", field=field, row_id=row_id)

    severity = _text(row.get("severity"))
    if severity is None:
        _finding(findings, index, FindingCode.missing_field, "severity is required", field="severity", row_id=row_id)
    elif severity.lower() not in allowed_severities:
        _finding(
            findings,
            index,
            FindingCode.invalid_value,
            f"unsupported severity: {severity}",
            field="severity",
            row_id=row_id,
        )

    status = _text(row.get("status"))
    if status is None:
        _finding(findings, index, FindingCode.missing_field, "status is required", field="status", row_id=row_id)
    elif status.lower() not in allowed_statuses:
        _finding(
            findings,
            index,
            FindingCode.invalid_value,
            f"unsupported status: {status}",
            field="status",
            row_id=row_id,
        )

    detected_value = row.get("detected_at")
    detected_at = _parse_timestamp(detected_value)
    if detected_at is None:
        if detected_value is None:
            _finding(findings, index, FindingCode.missing_field, "detected_at is required", field="detected_at", row_id=row_id)
        else:
            _finding(
                findings,
                index,
                FindingCode.invalid_timestamp,
                "detected_at must be a timezone-aware ISO-8601 timestamp",
                field="detected_at",
                row_id=row_id,
            )

    services_value = row.get("affected_services")
    if services_value is None:
        _finding(
            findings,
            index,
            FindingCode.missing_field,
            "affected_services is required",
            field="affected_services",
            row_id=row_id,
        )
    elif isinstance(services_value, (str, bytes)) or not isinstance(services_value, (list, tuple)):
        _finding(
            findings,
            index,
            FindingCode.invalid_type,
            "affected_services must be a list of service names",
            field="affected_services",
            row_id=row_id,
        )
    elif not services_value:
        _finding(
            findings,
            index,
            FindingCode.invalid_value,
            "affected_services must contain at least one service",
            field="affected_services",
            row_id=row_id,
        )
    else:
        invalid_positions = [position for position, item in enumerate(services_value) if _text(item) is None]
        if invalid_positions:
            positions = ", ".join(str(position) for position in invalid_positions)
            _finding(
                findings,
                index,
                FindingCode.invalid_value,
                f"affected_services has invalid entries at indexes: {positions}",
                field="affected_services",
                row_id=row_id,
            )

    subscribers = row.get("affected_subscribers")
    if subscribers is not None and (
        isinstance(subscribers, bool) or not isinstance(subscribers, int) or subscribers < 0
    ):
        _finding(
            findings,
            index,
            FindingCode.invalid_value,
            "affected_subscribers must be a non-negative integer",
            field="affected_subscribers",
            row_id=row_id,
        )

    resolved_value = row.get("resolved_at")
    resolved_at = None
    if resolved_value is not None:
        resolved_at = _parse_timestamp(resolved_value)
        if resolved_at is None:
            _finding(
                findings,
                index,
                FindingCode.invalid_timestamp,
                "resolved_at must be a timezone-aware ISO-8601 timestamp",
                field="resolved_at",
                row_id=row_id,
            )
        elif detected_at is not None and resolved_at < detected_at:
            _finding(
                findings,
                index,
                FindingCode.invalid_resolution_window,
                "resolved_at precedes detected_at",
                field="resolved_at",
                row_id=row_id,
                level=FindingLevel.warning,
            )
    if status is not None and status.lower() == "resolved" and resolved_at is None:
        _finding(
            findings,
            index,
            FindingCode.missing_resolution_timestamp,
            "resolved rows normally include resolved_at",
            field="resolved_at",
            row_id=row_id,
            level=FindingLevel.warning,
        )

    for field in sorted(key for key in row if isinstance(key, str) and key not in KNOWN_FIELDS):
        _finding(
            findings,
            index,
            FindingCode.unexpected_field,
            f"unexpected field: {field}",
            field=field,
            row_id=row_id,
            level=FindingLevel.warning,
        )

    valid = not any(finding.level is FindingLevel.error for finding in findings)
    return RowValidation(row=index, valid=valid, id=row_id, findings=findings), row_id


def validate_bulk_import_rows(
    rows: Iterable[object] | None,
    *,
    allowed_severities: Iterable[str] = DEFAULT_SEVERITIES,
    allowed_statuses: Iterable[str] = DEFAULT_STATUSES,
) -> BulkImportValidationReport:
    """Validate an iterable of outage import rows without raising on row data.

    Args:
        rows: Import rows, usually mappings. ``None`` is treated as empty.
        allowed_severities: Accepted severity labels, case-insensitive.
        allowed_statuses: Accepted status labels, case-insensitive.

    Returns:
        A report with per-row results, all findings, accepted ids, and an
        ``valid`` flag that is true only when at least one row exists and every
        row is error-free. Warnings never make a row invalid.
    """
    severities = frozenset(
        normalized.lower()
        for normalized in (_text(item) for item in allowed_severities)
        if normalized is not None
    )
    statuses = frozenset(
        normalized.lower()
        for normalized in (_text(item) for item in allowed_statuses)
        if normalized is not None
    )

    findings: list[ImportFinding] = []
    validated: list[RowValidation] = []
    accepted_ids: list[str] = []
    seen_ids: set[str] = set()
    iterator: Iterable[object]

    if rows is None:
        iterator = ()
    else:
        try:
            iterator = iter(rows)
        except TypeError:
            _finding(
                findings,
                -1,
                FindingCode.invalid_dataset,
                f"rows must be iterable, got {type(rows).__name__}",
            )
            iterator = ()

    for index, row in enumerate(iterator):
        result, row_id = _validate_row(
            row,
            index,
            seen_ids,
            allowed_severities=severities,
            allowed_statuses=statuses,
        )
        validated.append(result)
        findings.extend(result.findings)
        if result.valid and row_id is not None:
            accepted_ids.append(row_id)

    if not validated and not any(finding.code is FindingCode.invalid_dataset for finding in findings):
        _finding(findings, -1, FindingCode.empty_dataset, "no rows supplied")

    valid_rows = sum(1 for result in validated if result.valid)
    return BulkImportValidationReport(
        total_rows=len(validated),
        valid_rows=valid_rows,
        invalid_rows=len(validated) - valid_rows,
        valid=bool(validated) and valid_rows == len(validated),
        rows=validated,
        accepted_ids=accepted_ids,
        findings=findings,
    )
