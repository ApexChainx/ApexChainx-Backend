"""Exact DNS-label hostname allowlist matching, replacing a
startswith/endswith check that a subdomain or suffix can bypass.
"""


def hostname_matches_allowlist(hostname: str, allowlist_entry: str) -> bool:
    host_labels = hostname.rstrip(".").lower().split(".")
    allow_labels = allowlist_entry.rstrip(".").lower().split(".")

    if len(host_labels) < len(allow_labels):
        return False

    return host_labels[-len(allow_labels):] == allow_labels
