from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.sla_operations.schemas.settlement_reports import (
    PenaltyReportRow,
    RewardReportRow,
    SettlementDirection,
    SettlementReportFilter,
    SettlementReportRow,
    SettlementState,
    SettlementSummaryEnvelope,
)

START = datetime(2026, 3, 1, tzinfo=UTC)


def test_reward_and_penalty_rows_validate_directions_and_timestamps() -> None:
    reward = RewardReportRow(
        row_id="row-1",
        outage_id="outage-1",
        amount=150.0,
        asset_code="USDC",
        state="confirmed",
        transaction_hash="tx-abc123",
        settled_at=START + timedelta(hours=1),
    )
    penalty = PenaltyReportRow(
        row_id="row-2",
        outage_id="outage-2",
        amount=25.5,
        asset_code="XLM",
        state="pending",
    )

    assert reward.direction is SettlementDirection.REWARD
    assert penalty.direction is SettlementDirection.PENALTY
    assert reward.state is SettlementState.CONFIRMED
    assert reward.settled_at == START + timedelta(hours=1)


def test_summary_envelope_validates_totals_and_rows() -> None:
    envelope = SettlementSummaryEnvelope(
        report_id="settlement-2026-03",
        window_start=START,
        window_end=START + timedelta(days=31),
        generated_at=START + timedelta(days=32),
        asset_code="USDC",
        total_rewards=150.0,
        total_penalties=25.5,
        net_payout=124.5,
        reward_count=1,
        penalty_count=1,
        rows=[
            SettlementReportRow(
                row_id="row-1",
                outage_id="outage-1",
                direction="reward",
                amount=150.0,
                asset_code="USDC",
                state="settled",
                settled_at=START + timedelta(hours=1),
            ),
            SettlementReportRow(
                row_id="row-2",
                outage_id="outage-2",
                direction="penalty",
                amount=25.5,
                asset_code="USDC",
                state="failed",
            ),
        ],
    )

    assert envelope.net_payout == 124.5
    assert envelope.rows[0].direction is SettlementDirection.REWARD
    assert envelope.total_penalties == 25.5


def test_filter_accepts_range_boundaries() -> None:
    query = SettlementReportFilter(
        window_start=START,
        window_end=START + timedelta(seconds=1),
        directions=["reward"],
        states=["failed"],
        page_size=200,
    )

    assert query.directions == [SettlementDirection.REWARD]
    assert query.page_size == 200


def test_rows_reject_invalid_amounts_assets_and_states() -> None:
    with pytest.raises(ValidationError):
        RewardReportRow(
            row_id="row-1",
            outage_id="outage-1",
            amount=0.0,
            asset_code="USDC",
            state="pending",
        )
    with pytest.raises(ValidationError):
        RewardReportRow(
            row_id="row-1",
            outage_id="outage-1",
            amount=1.0,
            asset_code="usdc",
            state="pending",
        )
    with pytest.raises(ValidationError):
        RewardReportRow(
            row_id="row-1",
            outage_id="outage-1",
            amount=1.0,
            asset_code="USDC",
            state="unknown",
        )
    with pytest.raises(ValidationError):
        RewardReportRow(
            row_id="row-1",
            outage_id="outage-1",
            amount=1.0,
            asset_code="USDC",
            state="settled",
        )
    with pytest.raises(ValidationError):
        RewardReportRow(
            row_id="row-1",
            outage_id="outage-1",
            direction="penalty",
            amount=1.0,
            asset_code="USDC",
            state="pending",
        )


def test_envelope_and_filter_reject_inconsistent_values() -> None:
    with pytest.raises(ValidationError):
        SettlementSummaryEnvelope(
            report_id="settlement-1",
            window_start=START,
            window_end=START + timedelta(days=1),
            generated_at=START,
            asset_code="USDC",
            total_rewards=100.0,
            total_penalties=40.0,
            net_payout=100.0,
            reward_count=1,
            penalty_count=1,
        )
    with pytest.raises(ValidationError):
        SettlementReportFilter(
            window_start=START + timedelta(days=1),
            window_end=START,
        )
    with pytest.raises(ValidationError):
        SettlementReportFilter(
            window_start=START,
            window_end=START + timedelta(days=1),
            page=0,
        )
    with pytest.raises(ValidationError):
        SettlementReportFilter(
            window_start=START,
            window_end=START + timedelta(days=1),
            page_size=201,
        )
