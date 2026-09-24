"""Builds SLA cache keys including the policy version, so a config
change invalidates stale cached results instead of serving them until
TTL expiry.
"""


def build_sla_cache_key(sla_id: str, snapshot_marker: str, policy_version: int) -> str:
    return f"sla:{sla_id}:{snapshot_marker}:v{policy_version}"
