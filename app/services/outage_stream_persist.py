"""Real per-row persistence for streamed outage imports, replacing the
placeholder that only incremented a counter without writing to the DB.
"""

from typing import Any


def persist_outage_rows(
    rows: list[dict[str, Any]], repository, chunk_offset: int = 0
) -> tuple[int, list[dict[str, Any]]]:
    """Validate and persist each row, returning (imported_count, failures)."""
    imported = 0
    failures: list[dict[str, Any]] = []

    for index, row in enumerate(rows):
        absolute_index = chunk_offset + index
        if not row.get("site_id"):
            failures.append({"index": absolute_index, "row": row, "error": "missing site_id"})
            continue
        repository.create_or_get_existing(row)
        imported += 1

    return imported, failures
