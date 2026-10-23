"""Property-based tests for the SLA calculator invariants (#56).

Uses Hypothesis to generate 100+ scenarios and assert the core SLA
invariants hold for *any* valid input:

- availability is always within [0, 100] percent
- MTTR is always >= 0 minutes
- results are deterministic at a fixed seed (pinned via @seed)
- violated  <=>  mttr > threshold  (penalty, negative amount)
- met       <=>  mttr <= threshold (reward, non-negative amount)
- adding downtime never *increases* availability (monotonicity)

Counter-examples are cached automatically by Hypothesis in the example
database (`.hypothesis/examples`): a failing example is replayed on every
subsequent run until the regression is fixed, so discoveries survive
across runs. High-value boundary cases are additionally pinned with
@example() so they are always exercised.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from hypothesis import example, given, seed, settings
from hypothesis import strategies as st

from app.services.sla.sla_calculator import SLACalculator
from app.services.sla_service import SLAOrchestrator
from tests.properties.sla_scenarios import SEVERITIES, sla_scenario

# ── Config ──────────────────────────────────────────────────────────────

# Reproducible runs: every property below is pinned to this seed so a CI
# failure can be replayed locally with the exact same generated examples.
FIXED_SEED = 12345

# 100+ scenarios per property, as required by the issue acceptance criteria.
SCENARIOS = 150

# Bound outage count and durations so generation stays fast (< 30s total).
MAX_OUTAGES = 12
MAX_OUTAGE_DURATION_MINUTES = 60 * 24 * 7  # up to 7 days


# ── Strategies ──────────────────────────────────────────────────────────


@st.composite
def resolved_outage(draw: st.DrawFn) -> SimpleNamespace:  # type: ignore[type-arg]
    """A fully-resolved outage: both started_at and resolved_at are present.

    Unresolved outages are intentionally excluded from the property suite:
    their MTTR/availability math uses wall-clock time (datetime.now), which
    is not reproducible — everything here stays deterministic.
    """
    started_at = draw(
        st.datetimes(
            timezones=st.just(UTC),
            min_value=datetime(2024, 1, 1, tzinfo=UTC),
            max_value=datetime(2026, 1, 1, tzinfo=UTC),
        )
    )
    duration_minutes = draw(st.integers(min_value=0, max_value=MAX_OUTAGE_DURATION_MINUTES))
    resolved_at = started_at + timedelta(minutes=duration_minutes)
    return SimpleNamespace(started_at=started_at, resolved_at=resolved_at)


@st.composite
def outage_list(draw: st.DrawFn) -> list[SimpleNamespace]:  # type: ignore[type-arg]
    """A list of 0..MAX_OUTAGES fully-resolved outages."""
    count = draw(st.integers(min_value=0, max_value=MAX_OUTAGES))
    return [draw(resolved_outage()) for _ in range(count)]


PERIOD_DAYS = st.integers(min_value=1, max_value=31)


# ── Availability invariants (SLAOrchestrator.calculate_availability) ─────


@settings(max_examples=SCENARIOS, deadline=None)
@seed(FIXED_SEED)
@given(outages=outage_list(), period_days=PERIOD_DAYS)
@example(outages=[], period_days=30)
def test_availability_always_within_unit_interval(outages: list[SimpleNamespace], period_days: int) -> None:
    """Invariant: availability is a percentage, always in [0, 100]."""
    orch = SLAOrchestrator(db=None)
    availability = orch.calculate_availability(outages, period_days)
    assert 0.0 <= availability <= 100.0


@settings(max_examples=SCENARIOS, deadline=None)
@seed(FIXED_SEED)
@given(period_days=PERIOD_DAYS)
def test_availability_is_100_percent_without_outages(period_days: int) -> None:
    """Invariant: an outage-free period is 100% available."""
    orch = SLAOrchestrator(db=None)
    assert orch.calculate_availability([], period_days) == 100.0


@settings(max_examples=SCENARIOS, deadline=None)
@seed(FIXED_SEED)
@given(outages=outage_list(), period_days=PERIOD_DAYS)
def test_availability_never_increases_when_downtime_is_added(outages: list[SimpleNamespace], period_days: int) -> None:
    """Invariant: extra downtime can only lower (never raise) availability."""
    orch = SLAOrchestrator(db=None)
    baseline = orch.calculate_availability(outages, period_days)
    extra = _extra_outage(outages)
    extended = orch.calculate_availability(outages + [extra], period_days)
    assert extended <= baseline


def _extra_outage(outages: list[SimpleNamespace]) -> SimpleNamespace:
    """Build a fresh resolved outage that is not already in the list."""
    if outages:
        base = outages[0]
    else:
        base = SimpleNamespace(
            started_at=datetime(2025, 1, 1, tzinfo=UTC),
            resolved_at=datetime(2025, 1, 1, tzinfo=UTC),
        )
    return SimpleNamespace(
        started_at=base.started_at,
        resolved_at=base.resolved_at + timedelta(minutes=MAX_OUTAGE_DURATION_MINUTES),
    )


# ── MTTR invariants (SLAOrchestrator.calculate_mttr) ────────────────────


@settings(max_examples=SCENARIOS, deadline=None)
@seed(FIXED_SEED)
@given(outages=outage_list())
@example(outages=[])
def test_mttr_is_never_negative(outages: list[SimpleNamespace]) -> None:
    """Invariant: mean time to resolution is always >= 0 minutes."""
    orch = SLAOrchestrator(db=None)
    assert orch.calculate_mttr(outages) >= 0.0


def test_mttr_is_zero_without_outages() -> None:
    """Invariant: no outages means no MTTR to measure."""
    orch = SLAOrchestrator(db=None)
    assert orch.calculate_mttr([]) == 0.0


# ── Determinism at fixed seed ───────────────────────────────────────────


@settings(max_examples=SCENARIOS, deadline=None)
@seed(FIXED_SEED)
@given(outages=outage_list(), period_days=PERIOD_DAYS)
def test_availability_and_mttr_are_deterministic(outages: list[SimpleNamespace], period_days: int) -> None:
    """Invariant: identical inputs produce identical outputs."""
    orch = SLAOrchestrator(db=None)
    assert orch.calculate_availability(outages, period_days) == orch.calculate_availability(outages, period_days)
    assert orch.calculate_mttr(outages) == orch.calculate_mttr(outages)


# ── Per-outage calculator invariants (SLACalculator.calculate) ───────────


@settings(max_examples=SCENARIOS, deadline=None)
@seed(FIXED_SEED)
@given(scenario=sla_scenario())
@example(
    {
        "outage_id": "OUT-000001",
        "severity": "critical",
        "mttr_minutes": 15,  # exactly at threshold
        "policy_version": "1.0",
        "started_at": "2025-01-01T00:00:00+00:00",
        "resolved_at": "2025-01-01T00:15:00+00:00",
    }
)
def test_calculator_penalty_vs_reward_invariants(scenario: dict) -> None:
    """Invariant: violated <=> mttr > threshold; amounts keep sign + type."""
    result = SLACalculator.calculate(
        outage_id=scenario["outage_id"],
        severity=scenario["severity"],
        mttr_minutes=scenario["mttr_minutes"],
        policy_version=scenario["policy_version"],
        started_at=scenario["started_at"],
        resolved_at=scenario["resolved_at"],
    )
    assert result.mttr_minutes == scenario["mttr_minutes"]
    assert result.threshold_minutes > 0

    if result.status == "violated":
        assert result.amount < 0
        assert result.payment_type == "penalty"
        assert result.rating == "poor"
        assert result.mttr_minutes > result.threshold_minutes
    else:
        assert result.amount >= 0
        assert result.payment_type == "reward"
        assert result.rating in {"exceptional", "excellent", "good"}
        assert result.mttr_minutes <= result.threshold_minutes


@settings(max_examples=SCENARIOS, deadline=None)
@seed(FIXED_SEED)
@given(scenario=sla_scenario())
def test_calculator_is_deterministic_at_fixed_seed(scenario: dict) -> None:
    """Invariant: recomputing with identical inputs (and seed) yields identical results."""
    first = SLACalculator.calculate(
        outage_id=scenario["outage_id"],
        severity=scenario["severity"],
        mttr_minutes=scenario["mttr_minutes"],
        policy_version=scenario["policy_version"],
        started_at=scenario["started_at"],
        resolved_at=scenario["resolved_at"],
    )
    second = SLACalculator.calculate(
        outage_id=scenario["outage_id"],
        severity=scenario["severity"],
        mttr_minutes=scenario["mttr_minutes"],
        policy_version=scenario["policy_version"],
        started_at=scenario["started_at"],
        resolved_at=scenario["resolved_at"],
    )
    assert first == second
    assert first.compute_hash is not None
    assert first.compute_hash == second.compute_hash


@settings(max_examples=SCENARIOS, deadline=None)
@seed(FIXED_SEED)
@given(
    severity=st.one_of(st.sampled_from(SEVERITIES), st.text(min_size=1, max_size=50)),
    mttr_minutes=st.integers(min_value=0, max_value=10_000),
)
def test_calculator_rejects_unknown_severity(severity: str, mttr_minutes: int) -> None:
    """Invariant: only configured severities are accepted; unknown ones raise."""
    if severity in SEVERITIES:
        # smoke: valid severities never raise and always produce a decision
        result = SLACalculator.calculate("OUT-1", severity, mttr_minutes)
        assert result.status in {"met", "violated"}
    else:
        with pytest.raises(ValueError):
            SLACalculator.calculate("OUT-1", severity, mttr_minutes)
