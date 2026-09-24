"""Re-validates webhook destinations immediately before dispatch,
closing the TOCTOU window between configuration-time validation and a
later DNS-rebound delivery attempt.
"""
from typing import Callable, List


class DeliveryRevalidationFailed(Exception):
    pass


def revalidate_before_dispatch(hostname: str, resolve_fn: Callable[[str], List[str]],
                                is_public_ip_fn: Callable[[str], bool]) -> List[str]:
    fresh_ips = resolve_fn(hostname)
    if not all(is_public_ip_fn(ip) for ip in fresh_ips):
        raise DeliveryRevalidationFailed(f"Non-public IP resolved for {hostname} at dispatch time")
    return fresh_ips
