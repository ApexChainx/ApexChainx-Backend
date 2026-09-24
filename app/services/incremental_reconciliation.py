"""Windowed, incremental reconciliation instead of a full-table Python
scan on every scheduled reconciliation run.
"""
from datetime import datetime
from typing import Any, Callable, List


def reconcile_since_watermark(watermark: datetime, fetch_rows_since: Callable[[datetime], List[Any]],
                               process_row: Callable[[Any], None]) -> datetime:
    """Process only rows newer than the watermark, returning the new watermark."""
    rows = fetch_rows_since(watermark)
    new_watermark = watermark
    for row in rows:
        process_row(row)
        if row.created_at > new_watermark:
            new_watermark = row.created_at
    return new_watermark
