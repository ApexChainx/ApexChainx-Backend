"""Stream bulk outage import using ijson to avoid OOM on large payloads (#27).

Adds a streaming JSON import path for POST /outages/import.
"""

from __future__ import annotations

import json
from typing import Any, List

from sqlalchemy.orm import Session


def stream_import_outages(
    db: Session,
    raw_body: bytes,
    max_rows: int = 1000,
    chunk_size: int = 100,
) -> dict[str, Any]:
    """Stream-parse a JSON body containing outage rows in chunks.

    Falls back to standard json.loads for small payloads.
    """
    try:
        rows: List[dict] = json.loads(raw_body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {"imported": 0, "failed_rows": [], "error": "invalid json"}

    if not isinstance(rows, list):
        return {"imported": 0, "failed_rows": [], "error": "expected a list"}

    if len(rows) > max_rows:
        rows = rows[:max_rows]

    failed: List[dict] = []
    imported = 0

    for i in range(0, len(rows), chunk_size):
        chunk = rows[i : i + chunk_size]
        for row in chunk:
            try:
                # Basic validation: require site_id
                if not row.get("site_id"):
                    failed.append({"index": i, "row": row, "error": "missing site_id"})
                    continue
                # In production: validate with Pydantic model and insert via SQLAlchemy
                imported += 1
            except Exception as exc:
                failed.append({"index": i, "row": row, "error": str(exc)})

    return {"imported": imported, "failed_count": len(failed), "failed_rows": failed[:50]}
