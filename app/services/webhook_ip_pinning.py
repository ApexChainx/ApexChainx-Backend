"""Pins webhook dispatch connections to the validated resolved_ips
recorded at configuration time, instead of re-resolving DNS blindly.
"""
from typing import List


class ResolvedIpDriftError(Exception):
    pass


def assert_ip_pinned(dispatch_ip: str, resolved_ips: List[str]) -> None:
    """Reject delivery if the dispatch-time IP drifted from validation."""
    if dispatch_ip not in resolved_ips:
        raise ResolvedIpDriftError(
            f"Dispatch IP {dispatch_ip} not in validated set {resolved_ips}"
        )
