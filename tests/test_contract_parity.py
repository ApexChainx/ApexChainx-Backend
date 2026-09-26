"""Contract-parity property-based tests for SLA calculation (#36).

Uses pytest-hypothesis to generate 60+ scenarios per severity/policy and
asserts bit-exact equality between local adapter output (SLACalculator)
and the contract adapter (SLAContractAdapter + translate_contract_result).

Covers:
- Penalty vs reward boundary cases
- MTTR equal to threshold (boundary)
- Policy version bumps
- Leap day and zero-duration edge cases
- Determinism: same inputs always produce same outputs
"""

from __future__ import annotations

import pytest
from hypothesis import HealthCheck, given, settings

from app.services.contracts import SLAContractAdapter, translate_contract_result
from app.services.sla import SLACalculator
from tests.properties.sla_scenarios import (
    SEVERITIES,
    scenario_with_policy_bump,
    scenario_with_repeated_mttr,
    sla_scenario,
)

# ── Config ──────────────────────────────────────────────────────────────

# Target 60 scenarios per severity × 4 severities = 240 total
# But hypothesis generates many more by default, so we cap with settings
SCENARIOS_PER_TEST = 60


# ── Property: local ↔ contract adapter bit-exact equality ───────────────


@given(scenario=sla_scenario())
@settings(
    max_examples=SCENARIOS_PER_TEST,
    deadline=None,  # No hard deadline; target < 5 min total
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_local_and_contract_adapter_produce_equal_results(scenario: dict):
    """Property: local calculator output matches contract adapter after translation.

    For any valid outage parameters, the local SLACalculator should produce
    the same result as the SLAContractAdapter (which wraps SLACalculator and
    translates through Soroban-style encoding).

    This ensures no drift between local and on-chain settlement paths.
    """
    # Local path: direct calculator
    local = SLACalculator.calculate(
        outage_id=scenario["outage_id"],
        severity=scenario["severity"],
        mttr_minutes=scenario["mttr_minutes"],
        policy_version=scenario["policy_version"],
        started_at=scenario["started_at"],
        resolved_at=scenario["resolved_at"],
    )

    # Contract path: adapter → translation
    raw_contract = SLAContractAdapter.calculate_sla(
        outage_id=scenario["outage_id"],
        severity=scenario["severity"],
        mttr_minutes=scenario["mttr_minutes"],
        policy_version=scenario["policy_version"],
        started_at=scenario["started_at"],
        resolved_at=scenario["resolved_at"],
    )
    contract = translate_contract_result(raw_contract)

    # Assert bit-exact equality on all fields that survive translation
    assert (
        local.outage_id == contract.outage_id
    ), f"outage_id mismatch: local={local.outage_id} vs contract={contract.outage_id}"
    assert (
        local.status == contract.status
    ), f"status mismatch for {scenario['outage_id']}: local={local.status} vs contract={contract.status}"
    assert local.mttr_minutes == contract.mttr_minutes
    assert local.threshold_minutes == contract.threshold_minutes
    assert local.amount == contract.amount
    assert local.payment_type == contract.payment_type
    assert local.rating == contract.rating


# ── Property: idempotency / determinism ─────────────────────────────────


@given(scenario_and_mttr=scenario_with_repeated_mttr())
@settings(
    max_examples=SCENARIOS_PER_TEST // 2,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_sla_calculation_is_deterministic(scenario_and_mttr: tuple[dict, int]):
    """Property: same inputs always produce the same SLA result.

    Given identical (outage_id, severity, mttr, policy_version, timestamps),
    the calculator must always return the same result — critical for
    idempotent recompute and audit trail consistency.
    """
    scenario, _ = scenario_and_mttr

    result1 = SLACalculator.calculate(
        outage_id=scenario["outage_id"],
        severity=scenario["severity"],
        mttr_minutes=scenario["mttr_minutes"],
        policy_version=scenario["policy_version"],
        started_at=scenario["started_at"],
        resolved_at=scenario["resolved_at"],
    )

    result2 = SLACalculator.calculate(
        outage_id=scenario["outage_id"],
        severity=scenario["severity"],
        mttr_minutes=scenario["mttr_minutes"],
        policy_version=scenario["policy_version"],
        started_at=scenario["started_at"],
        resolved_at=scenario["resolved_at"],
    )

    assert (
        result1.model_dump() == result2.model_dump()
    ), f"Non-deterministic SLA calculation:\n  Result 1: {result1.model_dump_json()}\n  Result 2: {result2.model_dump_json()}"


# ── Property: MTTR boundary at threshold ────────────────────────────────


@pytest.mark.parametrize("severity", SEVERITIES)
def test_mttr_boundary_penalty_vs_reward(severity: str):
    """Boundary test: MTTR exactly at threshold → met (reward), not violated.

    The contract states: if MTTR > threshold → penalty, MTTR ≤ threshold → reward.
    Therefore MTTR == threshold must produce 'met' status.
    """
    from app.services.sla.config import SLA_CONFIG

    config = SLA_CONFIG[severity]
    threshold = config["threshold_minutes"]

    # At threshold → should be met (reward)
    result = SLACalculator.calculate(
        outage_id="BOUNDARY-TEST",
        severity=severity,
        mttr_minutes=threshold,
        policy_version="1.0",
        started_at="2024-01-01T00:00:00+00:00",
        resolved_at=f"2024-01-01T00:{threshold:02d}:00+00:00",
    )
    assert (
        result.status == "met"
    ), f"MTTR == threshold ({threshold}) should be 'met' for {severity}, got '{result.status}'"
    assert result.payment_type == "reward"


@pytest.mark.parametrize("severity", SEVERITIES)
def test_mttr_just_over_threshold_penalty(severity: str):
    """Boundary test: MTTR just over threshold → violated (penalty)."""
    from app.services.sla.config import SLA_CONFIG

    config = SLA_CONFIG[severity]
    threshold = config["threshold_minutes"]

    # Just over threshold → should be violated (penalty)
    result = SLACalculator.calculate(
        outage_id="BOUNDARY-TEST-2",
        severity=severity,
        mttr_minutes=threshold + 1,
        policy_version="1.0",
        started_at="2024-01-01T00:00:00+00:00",
        resolved_at=f"2024-01-01T00:{threshold + 1:02d}:00+00:00",
    )
    assert (
        result.status == "violated"
    ), f"MTTR > threshold ({threshold + 1}) should be 'violated' for {severity}, got '{result.status}'"
    assert result.payment_type == "penalty"


# ── Property: policy version bump produces different compute_hash ───────


@given(scenario_and_bump=scenario_with_policy_bump())
@settings(
    max_examples=SCENARIOS_PER_TEST // 3,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_policy_version_bump_changes_compute_hash(scenario_and_bump: tuple[dict, str]):
    """Property: changing policy_version yields a different compute_hash.

    This ensures that recompute with an updated policy correctly produces
    a new row rather than colliding with the old one.
    """
    scenario, bumped_version = scenario_and_bump

    result_original = SLACalculator.calculate(
        outage_id=scenario["outage_id"],
        severity=scenario["severity"],
        mttr_minutes=scenario["mttr_minutes"],
        policy_version=scenario["policy_version"],
        started_at=scenario["started_at"],
        resolved_at=scenario["resolved_at"],
    )

    result_bumped = SLACalculator.calculate(
        outage_id=scenario["outage_id"],
        severity=scenario["severity"],
        mttr_minutes=scenario["mttr_minutes"],
        policy_version=bumped_version,
        started_at=scenario["started_at"],
        resolved_at=scenario["resolved_at"],
    )

    # Both should have compute_hashes
    assert result_original.compute_hash is not None
    assert result_bumped.compute_hash is not None

    # Different policy versions → different hashes
    assert result_original.compute_hash != result_bumped.compute_hash, (
        f"Policy version bump from {scenario['policy_version']} to {bumped_version} "
        f"should produce different compute_hash"
    )


# ── Property: zero-duration outage ──────────────────────────────────────


@pytest.mark.parametrize("severity", SEVERITIES)
def test_zero_duration_outage_met(severity: str):
    """Zero-duration outage (MTTR=0) should always be met with exceptional rating."""
    result = SLACalculator.calculate(
        outage_id="ZERO-DURATION",
        severity=severity,
        mttr_minutes=0,
        policy_version="1.0",
        started_at="2024-02-29T12:00:00+00:00",  # leap day
        resolved_at="2024-02-29T12:00:00+00:00",  # same time
    )
    assert result.status == "met"
    assert result.rating == "exceptional"
    assert result.payment_type == "reward"


# ── Property: compute_hash is deterministic ─────────────────────────────


@given(scenario=sla_scenario())
@settings(
    max_examples=SCENARIOS_PER_TEST // 3,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_compute_hash_is_deterministic(scenario: dict):
    """Property: compute_hash is identical for identical inputs."""
    r1 = SLACalculator.calculate(
        outage_id=scenario["outage_id"],
        severity=scenario["severity"],
        mttr_minutes=scenario["mttr_minutes"],
        policy_version=scenario["policy_version"],
        started_at=scenario["started_at"],
        resolved_at=scenario["resolved_at"],
    )

    r2 = SLACalculator.calculate(
        outage_id=scenario["outage_id"],
        severity=scenario["severity"],
        mttr_minutes=scenario["mttr_minutes"],
        policy_version=scenario["policy_version"],
        started_at=scenario["started_at"],
        resolved_at=scenario["resolved_at"],
    )

    assert r1.compute_hash == r2.compute_hash
