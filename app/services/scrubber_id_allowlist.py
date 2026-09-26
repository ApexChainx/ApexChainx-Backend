"""Allowlists the codebase's own long identifier formats so the
32+ character catch-all redaction doesn't mangle UUIDs and prefixed ids.
"""
import re

KNOWN_ID_PATTERNS = [
    re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"),
    re.compile(r"^(fam|job|user|atk|rtk)_[A-Za-z0-9]+$"),
]


def is_known_identifier(value: str) -> bool:
    return any(pattern.match(value) for pattern in KNOWN_ID_PATTERNS)
