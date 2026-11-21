"""Workspace-local contracts for bulk outage/SLA operations (#850).

These models describe preview rows, validation issues, and result summaries
without importing core ORM classes or routed request/response models.
"""

from enum import Enum
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_BULK_BATCH_SIZE = 1000
MAX_ISSUES_PER_ITEM = 50


class BulkOperationType(str, Enum):
    OUTAGE_IMPORT = "outage_import"
    SLA_RECOMPUTE = "sla_recompute"
    PAYMENT_RETRY = "payment_retry"
    WEBHOOK_REDISPATCH = "webhook_redispatch"


class BulkOperationMode(str, Enum):
    PREVIEW = "preview"
    APPLY = "apply"


class BulkOperationOutcome(str, Enum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class BulkRecordAction(str, Enum):
    CREATE = "create"
    UPDATE = "update"
    SKIP = "skip"
    REJECT = "reject"


class BulkIssueSeverity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


class BulkValidationIssue(BaseModel):
    """One machine-readable problem found while validating a bulk record."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(..., min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    message: str = Field(..., min_length=1, max_length=500)
    severity: BulkIssueSeverity
    row_index: int = Field(..., ge=0, lt=MAX_BULK_BATCH_SIZE)
    field: str | None = Field(default=None, min_length=1, max_length=128)
    record_id: str | None = Field(default=None, min_length=1, max_length=255)


class BulkPreviewItem(BaseModel):
    """Planned effect of one record in a bulk preview."""

    model_config = ConfigDict(extra="forbid")

    row_index: int = Field(..., ge=0, lt=MAX_BULK_BATCH_SIZE)
    record_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="Domain identifier (outage, payment, or webhook); null for server-assigned ids",
    )
    operation: BulkOperationType
    action: BulkRecordAction
    summary: str = Field(..., min_length=1, max_length=300)
    issues: list[BulkValidationIssue] = Field(default_factory=list, max_length=MAX_ISSUES_PER_ITEM)
    would_mutate: bool = Field(..., description="True when applying this item changes persisted state")

    @model_validator(mode="after")
    def rejected_items_have_an_error(self) -> Self:
        if self.action is BulkRecordAction.REJECT and not any(
            issue.severity is BulkIssueSeverity.ERROR for issue in self.issues
        ):
            raise ValueError("a rejected preview item must include at least one error issue")
        return self


class BulkOperationSummary(BaseModel):
    """Counts and outcome for a previewed or applied batch."""

    model_config = ConfigDict(extra="forbid")

    operation: BulkOperationType
    mode: BulkOperationMode
    outcome: BulkOperationOutcome
    operation_id: UUID | None = Field(default=None, description="Correlation id shared with batch audit logs")
    total_records: int = Field(..., ge=0, le=MAX_BULK_BATCH_SIZE)
    processed_records: int = Field(..., ge=0)
    succeeded_records: int = Field(..., ge=0)
    failed_records: int = Field(..., ge=0)
    skipped_records: int = Field(..., ge=0)
    issues: list[BulkValidationIssue] = Field(default_factory=list)
    duration_ms: int | None = Field(default=None, ge=0, description="Wall-clock duration of the batch")

    @model_validator(mode="after")
    def counts_are_consistent(self) -> Self:
        if self.processed_records > self.total_records:
            raise ValueError("processed_records cannot exceed total_records")
        accounted = self.succeeded_records + self.failed_records + self.skipped_records
        if accounted != self.processed_records:
            raise ValueError("succeeded_records + failed_records + skipped_records must equal processed_records")
        if self.outcome is BulkOperationOutcome.COMPLETED and self.failed_records:
            raise ValueError("a completed batch cannot have failed records")
        if self.outcome is BulkOperationOutcome.FAILED and self.succeeded_records:
            raise ValueError("a failed batch cannot have succeeded records")
        return self


class BulkPreviewResponse(BaseModel):
    """Preview rows paired with the summary they contribute to."""

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    items: list[BulkPreviewItem] = Field(..., max_length=MAX_BULK_BATCH_SIZE)
    summary: BulkOperationSummary

    @model_validator(mode="after")
    def items_match_summary(self) -> Self:
        if self.summary.mode is not BulkOperationMode.PREVIEW:
            raise ValueError("a preview response must have preview mode")
        if len(self.items) != self.summary.total_records:
            raise ValueError("items length must equal summary.total_records")
        if len({item.row_index for item in self.items}) != len(self.items):
            raise ValueError("preview item row_index values must be unique")
        if any(item.operation is not self.summary.operation for item in self.items):
            raise ValueError("preview items must use the summary operation")
        return self


__all__ = [
    "BulkIssueSeverity",
    "BulkOperationMode",
    "BulkOperationOutcome",
    "BulkOperationSummary",
    "BulkOperationType",
    "BulkPreviewItem",
    "BulkPreviewResponse",
    "BulkRecordAction",
    "BulkValidationIssue",
]
