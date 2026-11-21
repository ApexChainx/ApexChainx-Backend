from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ReconciliationDomain(str, Enum):
    """Domain pair covered by a reconciliation check."""

    PAYMENT_SLA = "payment_sla"


class ReconciliationCheckStatus(str, Enum):
    """Outcome of comparing expected and actual reconciliation records."""

    MATCHED = "matched"
    MISMATCHED = "mismatched"
    MISSING_EXPECTED = "missing_expected"
    MISSING_ACTUAL = "missing_actual"


class PaymentSlaReconciliationCheck(BaseModel):
    """Single reconciliation check between a payment record and SLA outcome.

    Amount fields use integer minor units to avoid floating-point ambiguity in
    financial comparisons.
    """

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        json_schema_extra={
            "example": {
                "check_id": "recon-outage-001",
                "domain": "payment_sla",
                "status": "mismatched",
                "outage_id": "outage-001",
                "sla_result_id": 101,
                "payment_id": "payment-001",
                "expected_amount_minor": 1500,
                "actual_amount_minor": 1200,
                "currency": "USD",
                "expected_payment_type": "reward",
                "actual_payment_type": "reward",
                "expected_payment_status": "confirmed",
                "actual_payment_status": "pending",
                "reason_codes": ["amount_mismatch", "status_mismatch"],
                "notes": "Settlement amount and confirmation state diverge from SLA result.",
                "checked_at": "2026-11-20T13:20:00Z",
            }
        },
    )

    check_id: str = Field(..., min_length=1, max_length=128)
    domain: ReconciliationDomain = ReconciliationDomain.PAYMENT_SLA
    status: ReconciliationCheckStatus
    outage_id: str | None = Field(default=None, min_length=1, max_length=128)
    sla_result_id: int | None = Field(default=None, ge=1)
    payment_id: str | None = Field(default=None, min_length=1, max_length=128)
    expected_amount_minor: int | None = Field(
        default=None,
        description="Expected settlement amount in minor units derived from SLA computation.",
    )
    actual_amount_minor: int | None = Field(
        default=None,
        description="Observed settlement amount in minor units from the payment record.",
    )
    currency: str | None = Field(default=None, min_length=3, max_length=8)
    expected_payment_type: str | None = Field(default=None, min_length=1, max_length=32)
    actual_payment_type: str | None = Field(default=None, min_length=1, max_length=32)
    expected_payment_status: str | None = Field(default=None, min_length=1, max_length=32)
    actual_payment_status: str | None = Field(default=None, min_length=1, max_length=32)
    reason_codes: list[str] = Field(default_factory=list)
    notes: str | None = Field(default=None, max_length=2000)
    checked_at: datetime | None = None

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str | None) -> str | None:
        if value is None:
            return value
        normalized = value.strip().upper()
        if not normalized:
            raise ValueError("currency must not be blank")
        return normalized

    @field_validator("reason_codes")
    @classmethod
    def validate_reason_codes(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for code in value:
            item = code.strip()
            if not item:
                raise ValueError("reason_codes must not contain blank values")
            cleaned.append(item)
        return cleaned

    @model_validator(mode="after")
    def validate_check_consistency(self) -> "PaymentSlaReconciliationCheck":
        if not any([self.outage_id, self.sla_result_id, self.payment_id]):
            raise ValueError("at least one identifier (outage_id, sla_result_id, payment_id) is required")
        if self.status == ReconciliationCheckStatus.MATCHED and self.reason_codes:
            raise ValueError("matched checks must not include reason_codes")
        if self.status != ReconciliationCheckStatus.MATCHED and not self.reason_codes:
            raise ValueError("non-matched checks must include at least one reason_code")
        if self.status == ReconciliationCheckStatus.MISSING_EXPECTED and self.expected_amount_minor is not None:
            raise ValueError("missing_expected checks must not define expected_amount_minor")
        if self.status == ReconciliationCheckStatus.MISSING_ACTUAL and self.actual_amount_minor is not None:
            raise ValueError("missing_actual checks must not define actual_amount_minor")
        return self


class ReconciliationMismatchReport(BaseModel):
    """Aggregated mismatch report for a set of reconciliation checks."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        json_schema_extra={
            "example": {
                "report_id": "recon-batch-20261120-01",
                "generated_at": "2026-11-20T13:25:00Z",
                "total_checks": 2,
                "matched_count": 1,
                "mismatch_count": 1,
                "checks": [
                    {
                        "check_id": "recon-outage-001",
                        "status": "mismatched",
                        "outage_id": "outage-001",
                        "sla_result_id": 101,
                        "payment_id": "payment-001",
                        "reason_codes": ["amount_mismatch"],
                    },
                    {
                        "check_id": "recon-outage-002",
                        "status": "matched",
                        "outage_id": "outage-002",
                        "sla_result_id": 102,
                        "payment_id": "payment-002",
                    },
                ],
                "mismatches": [
                    {
                        "check_id": "recon-outage-001",
                        "status": "mismatched",
                        "outage_id": "outage-001",
                        "sla_result_id": 101,
                        "payment_id": "payment-001",
                        "reason_codes": ["amount_mismatch"],
                    }
                ],
            }
        },
    )

    report_id: str = Field(..., min_length=1, max_length=128)
    checks: list[PaymentSlaReconciliationCheck] = Field(default_factory=list)
    total_checks: int = Field(..., ge=0)
    matched_count: int = Field(..., ge=0)
    mismatch_count: int = Field(..., ge=0)
    mismatches: list[PaymentSlaReconciliationCheck] = Field(default_factory=list)
    generated_at: datetime | None = None

    @model_validator(mode="after")
    def validate_report_consistency(self) -> "ReconciliationMismatchReport":
        matched = sum(1 for check in self.checks if check.status == ReconciliationCheckStatus.MATCHED)
        mismatched = len(self.checks) - matched
        derived_mismatches = [check for check in self.checks if check.status != ReconciliationCheckStatus.MATCHED]

        if self.total_checks != len(self.checks):
            raise ValueError("total_checks must match number of checks")
        if self.matched_count != matched:
            raise ValueError("matched_count does not match checks")
        if self.mismatch_count != mismatched:
            raise ValueError("mismatch_count does not match checks")
        if [check.check_id for check in self.mismatches] != [check.check_id for check in derived_mismatches]:
            raise ValueError("mismatches must contain all and only non-matched checks in order")
        return self
