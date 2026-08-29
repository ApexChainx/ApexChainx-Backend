from __future__ import annotations

import hashlib
import json
import secrets
from copy import deepcopy
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.orm.sla_config_history import SLAConfigHistoryORM
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

# ── Policy version tracking (#37, #272) ────────────────────────────────
#
# The sla_config_history table is the source of truth for versions and
# publish tokens. These in-memory dicts are a per-process cache: they are
# consulted only when no DB session is available (e.g. hot SLA-calc path)
# and are refreshed from the latest history row on every DB-backed publish.
# Keeping the ledger in the DB means versions never repeat after a process
# restart and optimistic-concurrency tokens are shared across workers.

# In-memory version cache — incremented on each successful publish.
_policy_versions: dict[str, int] = {sev: 1 for sev in SLA_CONFIG}

# In-memory token cache for optimistic concurrency control.
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


def _latest_history_row(db: Session, severity: str) -> SLAConfigHistoryORM | None:
    """Return the most recent persisted history row for a severity."""
    return (
        db.query(SLAConfigHistoryORM)
        .filter(SLAConfigHistoryORM.severity == severity)
        .order_by(SLAConfigHistoryORM.policy_version.desc())
        .first()
    )


def get_policy_version(severity: str, db: Session | None = None) -> int:
    """Return the current policy version for a severity level.

    When a DB session is provided, the persisted history is authoritative
    (survives restarts and multi-worker divergence, #272); otherwise the
    in-process cache is used.
    """
    normalized = severity.lower()
    if normalized not in SLA_CONFIG:
        raise ValueError(f"Unknown severity level: {severity}")
    if db is not None:
        latest = _latest_history_row(db, normalized)
        if latest is not None:
            return latest.policy_version
    return _policy_versions[normalized]


def get_current_token(severity: str, db: Session | None = None) -> str:
    """Return the current publish token (used for optimistic concurrency).

    Prefers the persisted history row so a token fetched after a restart
    matches what the DB-backed publish will compare against (#272).
    """
    normalized = severity.lower()
    if normalized not in SLA_CONFIG:
        raise ValueError(f"Unknown severity level: {severity}")
    if db is not None:
        latest = _latest_history_row(db, normalized)
        if latest is not None:
            return latest.publish_token
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


def update_config_for_severity(severity: str, payload: SLAConfigUpdateRequest, db: Session | None = None) -> SLASeverityConfig:
    """Update config for a severity without an expected token (#273).

    Delegates to publish_config_for_severity with no token check, so a
    token-less update now gets the same version bump, history entry, and
    content-hash consistency as a normal publish, instead of silently
    mutating SLA_CONFIG in place.
    """
    policy, _token, _history = publish_config_for_severity(severity, payload, expected_token=None, db=db)
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
    db: Session | None = None,
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

    if db is not None:
        # Lock the latest history row so two workers cannot both bump the
        # same version. When no row exists yet (first publish), the unique
        # (severity, policy_version) index is the backstop and a duplicate
        # insert is converted to a ConcurrencyError below.
        latest = (
            db.query(SLAConfigHistoryORM)
            .filter(SLAConfigHistoryORM.severity == normalized)
            .order_by(SLAConfigHistoryORM.policy_version.desc())
            .with_for_update()
            .first()
        )
        current_version = latest.policy_version if latest else _policy_versions[normalized]
        current_token = latest.publish_token if latest else _publish_tokens[normalized]
    else:
        current_version = _policy_versions[normalized]
        current_token = _publish_tokens[normalized]

    # Optimistic concurrency: reject if token doesn't match
    if expected_token is not None and expected_token != current_token:
        raise ConcurrencyError(
            f"Config for '{severity}' was modified by another request. " f"Re-fetch the current config and retry."
        )

    # Bump version and write config atomically
    new_version = current_version + 1
    new_config = payload.model_dump()

    # Generate new token for next publish
    new_token = _generate_publish_token()

    if db is not None:
        db.add(
            SLAConfigHistoryORM(
                severity=normalized,
                policy_version=new_version,
                threshold_minutes=new_config["threshold_minutes"],
                penalty_per_minute=new_config["penalty_per_minute"],
                reward_base=new_config["reward_base"],
                content_hash=_compute_content_hash(normalized, new_config, new_version),
                publish_token=new_token,
                published_at=datetime.now(UTC),
                published_by=published_by,
            )
        )
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise ConcurrencyError(
                f"Config for '{severity}' was modified by another request. "
                "Re-fetch the current config and retry."
            ) from exc

    # Commit succeeded (or no DB) — refresh the in-process cache so the
    # hot read path agrees with the persisted ledger.
    _policy_versions[normalized] = new_version
    SLA_CONFIG[normalized] = new_config
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
