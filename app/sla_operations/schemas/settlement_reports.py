"""Typed settlement, reward, and penalty report schemas.

Rows carry a direction plus an unsigned magnitude so rewards and penalties keep
an unambiguous representation across reporting and export boundaries.
"""

from datetime import UTC, datetime
from enum import Enum
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator


class SettlementReportSchema(BaseModel):
    """Base model that accepts both mapping payloads and attribute-based objects."""

    model_config = ConfigDict(from_attributes=True)


class SettlementDirection(str, Enum):
    REWARD = "reward"
    PENALTY = "penalty"


class SettlementState(str, Enum):
    PENDING = "pending"
    SETTLED = "settled"
    CONFIRMED = "confirmed"
    FAILED = "failed"
    DISPUTED = "disputed"


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("datetimes must be timezone-aware")
    return value.astimezone(UTC)


UTCDateTime = Annotated[datetime, AfterValidator(_as_utc)]


def _validate_order(start: datetime, end: datetime, start_name: str, end_name: str) -> None:
    if end <= start:
        raise ValueError(f"{end_name} must be after {start_name}")


class SettlementReportRow(SettlementReportSchema):
    """One SLA settlement line item."""

    row_id: str = Field(min_length=1)
    outage_id: str = Field(min_length=1)
    sla_result_id: int | None = Field(default=None, ge=1)
    direction: SettlementDirection
    amount: float = Field(gt=0.0, description="Unsigned magnitude; direction determines debit or credit")
    asset_code: str = Field(min_length=1, max_length=12, pattern=r"^[A-Z0-9]{1,12}$")
    state: SettlementState
    transaction_hash: str | None = Field(default=None, min_length=1)
    settled_at: UTCDateTime | None = Field(default=None, description="Completion time for settled or confirmed rows")
    policy_version: str | None = None

    @model_validator(mode="after")
    def validate_settlement_time(self) -> "SettlementReportRow":
        settled_states = {SettlementState.SETTLED, SettlementState.CONFIRMED}
        if self.state in settled_states and self.settled_at is None:
            raise ValueError("settled_at is required for settled or confirmed rows")
        return self


class RewardReportRow(SettlementReportRow):
    """Settlement row guaranteed to represent a reward."""

    direction: Literal[SettlementDirection.REWARD] = SettlementDirection.REWARD


class PenaltyReportRow(SettlementReportRow):
    """Settlement row guaranteed to represent a penalty."""

    direction: Literal[SettlementDirection.PENALTY] = SettlementDirection.PENALTY


class SettlementSummaryEnvelope(SettlementReportSchema):
    """Aggregated settlement report returned to dashboards and auditors."""

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "report_id": "settlement-2026-03",
                "window_start": "2026-03-01T00:00:00Z",
                "window_end": "2026-04-01T00:00:00Z",
                "generated_at": "2026-04-01T01:00:00Z",
                "asset_code": "USDC",
                "total_rewards": 150.0,
                "total_penalties": 25.5,
                "net_payout": 124.5,
                "reward_count": 1,
                "penalty_count": 1,
                "rows": [],
            }
        },
    )

    report_id: str = Field(min_length=1)
    window_start: UTCDateTime
    window_end: UTCDateTime
    generated_at: UTCDateTime
    asset_code: str = Field(min_length=1, max_length=12, pattern=r"^[A-Z0-9]{1,12}$")
    total_rewards: float = Field(ge=0.0)
    total_penalties: float = Field(ge=0.0)
    net_payout: float = Field(description="total_rewards minus total_penalties")
    reward_count: int = Field(ge=0)
    penalty_count: int = Field(ge=0)
    rows: list[SettlementReportRow] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_totals(self) -> "SettlementSummaryEnvelope":
        _validate_order(self.window_start, self.window_end, "window_start", "window_end")
        if abs(self.net_payout - (self.total_rewards - self.total_penalties)) > 0.01:
            raise ValueError("net_payout must equal total_rewards minus total_penalties")
        return self


class SettlementReportFilter(SettlementReportSchema):
    """Validated query parameters for settlement report listings."""

    window_start: UTCDateTime
    window_end: UTCDateTime
    directions: list[SettlementDirection] = Field(default_factory=lambda: list(SettlementDirection))
    states: list[SettlementState] = Field(default_factory=list)
    asset_code: str | None = Field(default=None, min_length=1, max_length=12, pattern=r"^[A-Z0-9]{1,12}$")
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=200)

    @model_validator(mode="after")
    def validate_window(self) -> "SettlementReportFilter":
        _validate_order(self.window_start, self.window_end, "window_start", "window_end")
        return self


SettlementRow = SettlementReportRow
RewardRow = RewardReportRow
PenaltyRow = PenaltyReportRow
SettlementReportSummary = SettlementSummaryEnvelope
