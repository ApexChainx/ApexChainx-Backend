"""Batch-inserts parsed outage records, replacing a bulk import path
that built objects in memory but never added/committed them.
"""
from typing import Any, Dict, List


def persist_outage_batch(parsed_records: List[Dict[str, Any]], session, source_ref_field: str = "source_ref") -> int:
    """Insert records via the session, skipping ones with a duplicate source ref."""
    persisted = 0
    for record in parsed_records:
        existing = session.query(type(record)).filter_by(
            **{source_ref_field: record.get(source_ref_field)}
        ).first()
        if existing:
            continue
        session.add(record)
        persisted += 1
    session.commit()
    return persisted
