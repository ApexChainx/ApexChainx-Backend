"""Atomic single-statement sequence bump for token families, replacing
a read-modify-write pattern that can lose concurrent increments.
"""
from sqlalchemy import text


def bump_family_sequence_atomic(session, family_id: str) -> int:
    result = session.execute(
        text("UPDATE token_families SET current = current + 1 WHERE id = :id RETURNING current"),
        {"id": family_id},
    )
    session.commit()
    return result.scalar_one()
