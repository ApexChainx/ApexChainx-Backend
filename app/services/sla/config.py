from __future__ import annotations

import hashlib
import json
import secrets
from copy import deepcopy
from datetime import UTC, datetime

from app.models.sla import SLAConfigHistoryEntry, SLAConfigUpdateRequest, SLAPolicyContent, SLASeverityConfig

SLA_CONFIG: dict[str, dict[str, int]] = {
    "critical": {
        "threshold_minutes": 15,
        "penalty_per_minute": 100,
        "reward_base": 750,
    },
    "high": {
        "threshold_minutes": 30,
        "penalty_per_minute": 50,
        "reward_base": 750,
    },
    "medium": {
        "threshold_minutes": 60,
        "penalty_per_minute": 25,
        "reward_base": 750,
    },
    "low": {
        "threshold_minutes": 120,
        "penalty_per_minute": 10,
        "reward_base": 600,
    },
}

# ── Policy version tracking (#37) ──────────────────────────────────────

# In-memory version counters — incremented on each successful publish.
# In production these are read from sla_config_history table on startup.
_policy_versions: dict[str, int] = {sev: 1 for sev in SLA_CONFIG}

# In-memory lock tokens for optimistic concurrency control.
# Each publish generates a new token; concurrent PUTs with stale tokens get 409.
_publish_tokens: dict[str, str] = {sev: "" for sev in SLA_CONFIG}


def _compute_content_hash(severity: str, config: dict[str, int], version: int) -> str:
    """Compute content hash of a config payload for integrity verification."""
    raw = json.dumps(
        {
            "severity": severity,
            "policy_version": version,
            "threshold_minutes": config["threshold_minutes"],
            "penalty_per_minute": config["penalty_per_minute"],
            "reward_base": config["reward_base"],
        },
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _generate_publish_token() -> str:
    """Generate a new random publish token for optimistic concurrency."""
    return secrets.token_hex(16)


def get_policy_version(severity: str) -> int:
    """Return current policy version for a severity level."""
    normalized = severity.lower()
    if normalized not in _policy_versions:
        raise ValueError(f"Unknown severity level: {severity}")
    return _policy_versions[normalized]


def get_current_token(severity: str) -> str:
    """Return the current publish token (used for optimistic concurrency)."""
    normalized = severity.lower()
    if normalized not in _publish_tokens:
        raise ValueError(f"Unknown severity level: {severity}")
    return _publish_tokens[normalized]


def get_all_config() -> dict[str, SLASeverityConfig]:
    return {severity: SLASeverityConfig(**deepcopy(values)) for severity, values in SLA_CONFIG.items()}


def get_all_config_with_hashes() -> dict[str, SLAPolicyContent]:
    """Return all configs with content hashes for integrity verification (#37)."""
    return {
        severity: SLAPolicyContent(
            severity=severity,
            policy_version=_policy_versions[severity],
            threshold_minutes=values["threshold_minutes"],
            penalty_per_minute=values["penalty_per_minute"],
            reward_base=values["reward_base"],
            content_hash=_compute_content_hash(severity, values, _policy_versions[severity]),
        )
        for severity, values in SLA_CONFIG.items()
    }


def get_config_for_severity(severity: str) -> SLASeverityConfig:
    normalized = severity.lower()
    if normalized not in SLA_CONFIG:
        raise ValueError(f"Unknown severity level: {severity}")
    return SLASeverityConfig(**deepcopy(SLA_CONFIG[normalized]))


def get_config_with_hash(severity: str) -> SLAPolicyContent:
    """Return config with content hash and policy version (#37)."""
    normalized = severity.lower()
    if normalized not in SLA_CONFIG:
        raise ValueError(f"Unknown severity level: {severity}")
    config = SLA_CONFIG[normalized]
    version = _policy_versions[normalized]
    return SLAPolicyContent(
        severity=normalized,
        policy_version=version,
        threshold_minutes=config["threshold_minutes"],
        penalty_per_minute=config["penalty_per_minute"],
        reward_base=config["reward_base"],
        content_hash=_compute_content_hash(normalized, config, version),
    )


def update_config_for_severity(severity: str, payload: SLAConfigUpdateRequest) -> SLASeverityConfig:
    """Update config for a severity without an expected token (#273).

    Delegates to publish_config_for_severity with no token check, so a
    token-less update now gets the same version bump, history entry, and
    content-hash consistency as a normal publish, instead of silently
    mutating SLA_CONFIG in place.
    """
    policy, _token, _history = publish_config_for_severity(severity, payload, expected_token=None)
    return SLASeverityConfig(
        threshold_minutes=policy.threshold_minutes,
        penalty_per_minute=policy.penalty_per_minute,
        reward_base=policy.reward_base,
    )


def publish_config_for_severity(
    severity: str,
    payload: SLAConfigUpdateRequest,
    expected_token: str | None = None,
    published_by: str | None = None,
) -> tuple[SLAPolicyContent, str, SLAConfigHistoryEntry]:
    """Atomically publish a new policy version with optimistic concurrency (#37).

    Args:
        severity: Severity level to update.
        payload: New config values.
        expected_token: If provided, the publish only succeeds if this matches
                        the current token. Mismatch → 409.
        published_by: Identity of the publisher for audit trail.

    Returns:
        Tuple of (new_policy_content, new_publish_token, history_entry).

    Raises:
        ValueError: On unknown severity.
        ConcurrencyError: On token mismatch (caller should return 409).
    """
    normalized = severity.lower()
    if normalized not in SLA_CONFIG:
        raise ValueError(f"Unknown severity level: {severity}")

    # Optimistic concurrency: reject if token doesn't match
    if expected_token is not None and expected_token != _publish_tokens[normalized]:
        raise ConcurrencyError(
            f"Config for '{severity}' was modified by another request. " f"Re-fetch the current config and retry."
        )

    # Bump version and write config atomically
    _policy_versions[normalized] += 1
    new_version = _policy_versions[normalized]
    new_config = payload.model_dump()
    SLA_CONFIG[normalized] = new_config

    # Generate new token for next publish
    new_token = _generate_publish_token()
    _publish_tokens[normalized] = new_token

    # Compute content hash
    content_hash = _compute_content_hash(normalized, new_config, new_version)

    # Build history entry
    now = datetime.now(UTC)
    history = SLAConfigHistoryEntry(
        severity=normalized,
        policy_version=new_version,
        threshold_minutes=new_config["threshold_minutes"],
        penalty_per_minute=new_config["penalty_per_minute"],
        reward_base=new_config["reward_base"],
        content_hash=content_hash,
        published_at=now.isoformat(),
        published_by=published_by,
    )

    policy = SLAPolicyContent(
        severity=normalized,
        policy_version=new_version,
        threshold_minutes=new_config["threshold_minutes"],
        penalty_per_minute=new_config["penalty_per_minute"],
        reward_base=new_config["reward_base"],
        content_hash=content_hash,
    )

    return policy, new_token, history


class ConcurrencyError(Exception):
    """Raised when an optimistic concurrency check fails (→ 409)."""
