"""Recursively scrubs nested audit details, so secrets under a nested
key aren't skipped by a top-level-only redaction pass.
"""
from typing import Any

MAX_DEPTH = 10


def scrub_recursive(value: Any, is_sensitive_key, redact_value, depth: int = 0) -> Any:
    if depth >= MAX_DEPTH:
        return value
    if isinstance(value, dict):
        return {
            key: (redact_value(v) if is_sensitive_key(key) else scrub_recursive(v, is_sensitive_key, redact_value, depth + 1))
            for key, v in value.items()
        }
    if isinstance(value, list):
        return [scrub_recursive(item, is_sensitive_key, redact_value, depth + 1) for item in value]
    return value
