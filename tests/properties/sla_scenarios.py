"""Hypothesis strategies for SLA contract-parity property-based tests (#36).

Generates 60+ scenarios per severity/policy covering:
- Boundary MTTR values (0, 1, threshold-1, threshold, threshold+1)
- Leap-day and zero-duration edge cases
- Policy version bumps
- All severity levels
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hypothesis import strategies as st
from hypothesis.strategies import SearchStrategy

# ── Severity and policy version generators ──────────────────────────────

SEVERITIES = ["critical", "high", "medium", "low"]

POLICY_VERSIONS = ["1.0", "2.0", "3.0"]


@st.composite
def severity_strategy(draw: st.DrawFn) -> str:  # type: ignore[type-arg]
    """Draw a valid severity level."""
    return draw(st.sampled_from(SEVERITIES))


@st.composite
def policy_version_strategy(draw: st.DrawFn) -> str:  # type: ignore[type-arg]
    """Draw a policy version string."""
    return draw(st.sampled_from(POLICY_VERSIONS))


# ── MTTR generators with boundary coverage ──────────────────────────────


def mttr_for_severity(severity: str) -> SearchStrategy[int]:
    """Generate MTTR values that cover boundaries for a given severity.

    Thresholds:
        critical: 15 min
        high:     30 min
        medium:   60 min
        low:      120 min
    """
    thresholds: dict[str, int] = {
        "critical": 15,
        "high": 30,
        "medium": 60,
        "low": 120,
    }
    t = thresholds.get(severity, 60)

    # Boundary-focused values plus random range
    boundary_values = [
        0,  # zero-duration
        1,  # minimal
        max(1, t // 2),  # well under threshold
        t - 1,  # just under threshold
        t,  # exactly at threshold (boundary)
        t + 1,  # just over threshold (boundary)
        t * 2,  # well over threshold
        t * 5,  # extreme over threshold
    ]

    return st.one_of(
        st.sampled_from(boundary_values),
        st.integers(min_value=0, max_value=t * 10),  # broad random range
    )


# ── Datetime generators for edge cases ──────────────────────────────────


@st.composite
def edge_datetime_pair(draw: st.DrawFn) -> tuple[datetime, datetime]:  # type: ignore[type-arg]
    """Generate (started_at, resolved_at) pairs covering edge cases.

    Includes:
    - Normal dates
    - Leap day (Feb 29)
    - Zero-duration (started_at == resolved_at)
    - Year boundary
    """
    edge_dates = [
        datetime(2024, 2, 29, 10, 0, 0, tzinfo=UTC),  # leap day
        datetime(2024, 12, 31, 23, 59, 0, tzinfo=UTC),  # year boundary
        datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),  # year start
        datetime(2024, 6, 15, 12, 0, 0, tzinfo=UTC),  # normal mid-year
    ]

    started = draw(st.sampled_from(edge_dates))

    # Duration: include zero and normal ranges
    duration_minutes = draw(
        st.one_of(
            st.just(0),  # zero-duration
            st.integers(min_value=1, max_value=60 * 24 * 7),  # up to 7 days
        )
    )
    resolved = started + timedelta(minutes=duration_minutes)

    return started, resolved


# ── Full SLA scenario generator ─────────────────────────────────────────


@st.composite
def sla_scenario(draw: st.DrawFn) -> dict:  # type: ignore[type-arg]
    """Generate a complete SLA calculation scenario.

    Returns a dict with:
        outage_id, severity, mttr_minutes, policy_version,
        started_at (iso), resolved_at (iso)
    """
    severity = draw(severity_strategy())
    mttr = draw(mttr_for_severity(severity))
    policy_version = draw(policy_version_strategy())
    started_at, resolved_at = draw(edge_datetime_pair())

    # Generate a realistic outage_id
    outage_id = draw(st.from_regex(r"OUT-[0-9a-f]{3,6}", fullmatch=True))

    return {
        "outage_id": outage_id,
        "severity": severity,
        "mttr_minutes": mttr,
        "policy_version": policy_version,
        "started_at": started_at.isoformat(),
        "resolved_at": resolved_at.isoformat(),
    }


# ── Determinism test strategy ───────────────────────────────────────────


@st.composite
def scenario_with_repeated_mttr(draw: st.DrawFn) -> tuple[dict, int]:  # type: ignore[type-arg]
    """Generate a scenario and a repeated MTTR value for idempotency tests."""
    scenario = draw(sla_scenario())
    # Use the same MTTR as the scenario for repeat test
    return scenario, scenario["mttr_minutes"]


# ── Policy-version test strategy ────────────────────────────────────────


@st.composite
def scenario_with_policy_bump(draw: st.DrawFn) -> tuple[dict, str]:  # type: ignore[type-arg]
    """Generate a scenario with original and bumped policy versions."""
    scenario = draw(sla_scenario())
    original_version = scenario["policy_version"]
    # Pick a different version for the bump
    other_versions = [v for v in POLICY_VERSIONS if v != original_version]
    bumped = draw(st.sampled_from(other_versions)) if other_versions else "99.0"
    return scenario, bumped
