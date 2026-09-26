"""Collision-resistant relay memo digest, replacing truncated hashes
that can collide across distinct transfers within the dedupe window.
"""
import hashlib
from typing import Any, Dict


def build_memo_digest(transfer_attributes: Dict[str, Any], max_length: int = 28) -> str:
    """Hash full transfer attributes and truncate only the final digest."""
    canonical = "|".join(f"{k}={transfer_attributes[k]}" for k in sorted(transfer_attributes))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return digest[:max_length]
