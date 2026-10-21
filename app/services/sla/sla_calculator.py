import hashlib
import time

from app.models import SLAResult

from ..metrics import record_histogram
from .config import SLA_CONFIG, get_config_for_severity, get_policy_version


def _compute_hash(outage_id: str, started_at: str, resolved_at: str, policy_version: str) -> str:
    """Compute deterministic SHA-256 hash of recompute inputs for idempotency (#35)."""
    raw = f"{outage_id}||{started_at}||{resolved_at}||{policy_version}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class SLACalculator:
    @staticmethod
    def calculate(
        outage_id: str,
        severity: str,
        mttr_minutes: int,
        policy_version: str = "1.0",
        threshold_source: str = "config",
        started_at: str = "",
        resolved_at: str = "",
    ) -> SLAResult:
        _start = time.perf_counter()
        severity = severity.lower()

        if severity not in SLA_CONFIG:
            raise ValueError(f"Unknown severity level: {severity}")

        # (#274) There is no persisted per-version config history yet, so a
        # requested policy_version older/newer than the live version cannot
        # be honored. Detect the mismatch explicitly instead of silently
        # computing with the current config as if it matched.
        version_fallback_note = ""
        try:
            config = get_config_for_severity(severity)
            live_version = str(get_policy_version(severity))
            if policy_version and policy_version != live_version:
                version_fallback_note = (
                    f" [WARNING: requested policy_version={policy_version} but no history "
                    f"exists; computed with live config version={live_version}]"
                )
        except ValueError:
            # Fallback to default config if version-specific config not found
            config = SLA_CONFIG[severity]

        threshold = config.threshold_minutes

        # Compute idempotency hash when all inputs are available (#35)
        compute_hash = None
        if started_at and resolved_at:
            compute_hash = _compute_hash(outage_id, started_at, resolved_at, policy_version)

        # Case 1: SLA violated → penalty
        # Deterministic boundary handling: use >= for violation check to handle exact threshold edges
        if mttr_minutes > threshold:
            overtime = mttr_minutes - threshold
            penalty = overtime * config.penalty_per_minute

            return SLAResult(
                outage_id=outage_id,
                status="violated",
                mttr_minutes=mttr_minutes,
                threshold_minutes=threshold,
                amount=-penalty,
                payment_type="penalty",
                rating="poor",
                policy_version=policy_version,
                threshold_source=threshold_source,
                reason_code="mttr_exceeded",
                decision_trace=f"MTTR {mttr_minutes} > threshold {threshold} (overtime {overtime} minutes)"
                + version_fallback_note,
                compute_hash=compute_hash,
            )

        # Case 2: SLA met → reward
        # Deterministic boundary handling: use <= for met check to handle exact threshold edges
        performance_ratio = 0 if threshold == 0 else (mttr_minutes * 100) // threshold

        if performance_ratio < 50:
            multiplier = 200
            rating = "exceptional"
            reason_code = "met_exceptional"
        elif performance_ratio < 75:
            multiplier = 150
            rating = "excellent"
            reason_code = "met_excellent"
        else:
            multiplier = 100
            rating = "good"
            reason_code = "met_good"

        reward = (config.reward_base * multiplier) // 100

        _latency = (time.perf_counter() - _start) * 1000
        record_histogram("sla_calc_latency_milliseconds", _latency, tags={"severity": severity})

        return SLAResult(
            outage_id=outage_id,
            status="met",
            mttr_minutes=mttr_minutes,
            threshold_minutes=threshold,
            amount=reward,
            payment_type="reward",
            rating=rating,
            policy_version=policy_version,
            threshold_source=threshold_source,
            reason_code=reason_code,
            decision_trace=f"MTTR {mttr_minutes} <= threshold {threshold}, performance ratio {performance_ratio}%, rating {rating}"
            + version_fallback_note,
            compute_hash=compute_hash,
        )
