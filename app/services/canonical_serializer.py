"""Explicit canonical serializer for audit hashing, replacing the
default=str fallback whose stability depends on ad-hoc __str__ output.
"""
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any


def canonical_default(value: Any) -> Any:
    """Deterministic serialization for the types the audit chain hashes."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        return str(value.normalize())
    raise TypeError(f"Object of type {type(value)!r} is not canonically serializable")
