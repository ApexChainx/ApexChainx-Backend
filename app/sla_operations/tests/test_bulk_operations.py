"""Tests for the bulk-operations workspace schemas (#850)."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.sla_operations.schemas.bulk_operations import (
    BulkIssueSeverity,
    BulkOperationMode,
    BulkOperationOutcome,
    BulkOperationSummary,
    BulkOperationType,
    BulkPreviewItem,
    BulkPreviewResponse,
    BulkRecordAction,
    BulkValidationIssue,
)


def issue_payload(**overrides):
    payload = {
        "code": "invalid_severity",
        "message": "Severity is not recognised",
        "severity": BulkIssueSeverity.ERROR,
        "row_index": 0,
    }
    payload.update(overrides)
    return payload


def preview_payload(**overrides):
    payload = {
        "row_index": 0,
        "record_id": "outage-001",
        "operation": BulkOperationType.OUTAGE_IMPORT,
        "action": BulkRecordAction.CREATE,
        "summary": "Create outage record",
        "would_mutate": True,
    }
    payload.update(overrides)
    return payload


def summary_payload(**overrides):
    payload = {
        "operation": BulkOperationType.OUTAGE_IMPORT,
        "mode": BulkOperationMode.PREVIEW,
        "outcome": BulkOperationOutcome.COMPLETED,
        "total_records": 1,
        "processed_records": 0,
        "succeeded_records": 0,
        "failed_records": 0,
        "skipped_records": 0,
    }
    payload.update(overrides)
    return payload


def test_validation_issue_accepts_representative_payload():
    item = BulkValidationIssue.model_validate(issue_payload(field="severity", record_id="outage-001"))

    assert item.severity is BulkIssueSeverity.ERROR
    assert item.field == "severity"
    assert item.record_id == "outage-001"


@pytest.mark.parametrize(
    "overrides",
    [
        {"code": ""},
        {"code": "Bad Code"},
        {"code": "1invalid"},
        {"message": ""},
        {"severity": "blocker"},
        {"row_index": -1},
        {"row_index": 1000},
        {"field": ""},
    ],
)
def test_validation_issue_rejects_invalid_fields(overrides):
    with pytest.raises(ValidationError):
        BulkValidationIssue.model_validate(issue_payload(**overrides))


def test_validation_issue_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        BulkValidationIssue.model_validate(issue_payload(extra_field="x"))


def test_preview_item_accepts_skip_without_issues():
    item = BulkPreviewItem.model_validate(preview_payload(action=BulkRecordAction.SKIP, would_mutate=False))

    assert item.issues == []
    assert item.would_mutate is False


def test_rejected_preview_item_requires_an_error_issue():
    warning = issue_payload(code="duplicate_site", severity=BulkIssueSeverity.WARNING)
    error = issue_payload(severity=BulkIssueSeverity.ERROR)

    with pytest.raises(ValidationError, match="error issue"):
        BulkPreviewItem.model_validate(
            preview_payload(action=BulkRecordAction.REJECT, would_mutate=False, issues=[warning])
        )
    accepted = BulkPreviewItem.model_validate(
        preview_payload(action=BulkRecordAction.REJECT, would_mutate=False, issues=[error])
    )
    assert accepted.action is BulkRecordAction.REJECT


def test_preview_item_caps_issue_list():
    issues = [issue_payload() for _ in range(51)]

    with pytest.raises(ValidationError):
        BulkPreviewItem.model_validate(preview_payload(issues=issues))


def test_summary_accepts_full_batch_at_boundary():
    summary = BulkOperationSummary.model_validate(
        summary_payload(
            mode=BulkOperationMode.APPLY,
            total_records=1000,
            processed_records=1000,
            succeeded_records=998,
            failed_records=0,
            skipped_records=2,
            operation_id=uuid4(),
            duration_ms=0,
        )
    )

    assert summary.total_records == 1000
    assert summary.succeeded_records + summary.skipped_records == 1000


@pytest.mark.parametrize(
    "overrides",
    [
        {"total_records": 1001},
        {"processed_records": 2, "succeeded_records": 1},
        {"outcome": BulkOperationOutcome.COMPLETED, "failed_records": 1, "processed_records": 1},
        {"outcome": BulkOperationOutcome.FAILED, "succeeded_records": 1, "processed_records": 1},
        {"duration_ms": -1},
    ],
)
def test_summary_rejects_inconsistent_counts(overrides):
    with pytest.raises(ValidationError):
        BulkOperationSummary.model_validate(summary_payload(**overrides))


def test_preview_response_validates_items_against_summary():
    response = BulkPreviewResponse.model_validate(
        {"items": [preview_payload()], "summary": summary_payload()}
    )

    assert response.summary.mode is BulkOperationMode.PREVIEW
    assert len(response.items) == 1


@pytest.mark.parametrize(
    "items,summary",
    [
        ([preview_payload()], summary_payload(mode=BulkOperationMode.APPLY)),
        ([], summary_payload()),
        ([preview_payload(), preview_payload(row_index=1)], summary_payload()),
        (
            [preview_payload(row_index=0), preview_payload(row_index=0)],
            summary_payload(total_records=2),
        ),
        (
            [preview_payload(operation=BulkOperationType.PAYMENT_RETRY)],
            summary_payload(),
        ),
    ],
)
def test_preview_response_rejects_mismatched_payloads(items, summary):
    with pytest.raises(ValidationError):
        BulkPreviewResponse.model_validate({"items": items, "summary": summary})


def test_enum_values_are_machine_readable():
    assert BulkOperationType.SLA_RECOMPUTE.value == "sla_recompute"
    assert BulkOperationMode.PREVIEW.value == "preview"
    assert BulkOperationOutcome.PARTIAL.value == "partial"
    assert BulkRecordAction.UPDATE.value == "update"
    assert BulkIssueSeverity.WARNING.value == "warning"
