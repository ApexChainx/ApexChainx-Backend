"""Computes SLA snapshot checksums over deterministic content only,
excluding volatile fields like created_at/generated_at.
"""
import hashlib
import json
from typing import Any, Dict

VOLATILE_FIELDS = {"created_at", "generated_at", "id"}


def compute_snapshot_checksum(payload: Dict[str, Any]) -> str:
    deterministic = {k: v for k, v in payload.items() if k not in VOLATILE_FIELDS}
    canonical = json.dumps(deterministic, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
