from app.models import SLAResult

from .config import SLA_CONFIG, get_config_for_severity


class SLACalculator:
    @staticmethod
    def calculate(outage_id: str, severity: str, mttr_minutes: int, policy_version: str = "1.0", threshold_source: str = "config") -> SLAResult:
        severity = severity.lower()

        if severity not in SLA_CONFIG:
            raise ValueError(f"Unknown severity level: {severity}")

        # Use policy version to get configuration (for historical recompute)
        try:
            config = get_config_for_severity(severity)
        except ValueError:
            # Fallback to default config if version-specific config not found
            config = SLA_CONFIG[severity]
        
        threshold = config.threshold_minutes

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
                decision_trace=f"MTTR {mttr_minutes} > threshold {threshold} (overtime {overtime} minutes)",
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
            decision_trace=f"MTTR {mttr_minutes} <= threshold {threshold}, performance ratio {performance_ratio}%, rating {rating}",
        )
