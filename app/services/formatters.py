import json
from typing import Any


def canonical_json(value: Any) -> str:
    """Serialize JSON values into a stable, hash-friendly representation."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
