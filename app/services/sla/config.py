from copy import deepcopy

from app.models.sla import SLAConfigUpdateRequest, SLASeverityConfig

SLA_CONFIG = {
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


def get_all_config() -> dict[str, SLASeverityConfig]:
    return {severity: SLASeverityConfig(**deepcopy(values)) for severity, values in SLA_CONFIG.items()}


def get_config_for_severity(severity: str) -> SLASeverityConfig:
    normalized = severity.lower()
    if normalized not in SLA_CONFIG:
        raise ValueError(f"Unknown severity level: {severity}")
    return SLASeverityConfig(**deepcopy(SLA_CONFIG[normalized]))


def update_config_for_severity(severity: str, payload: SLAConfigUpdateRequest) -> SLASeverityConfig:
    normalized = severity.lower()
    if normalized not in SLA_CONFIG:
        raise ValueError(f"Unknown severity level: {severity}")

    SLA_CONFIG[normalized] = payload.model_dump()
    return SLASeverityConfig(**deepcopy(SLA_CONFIG[normalized]))
