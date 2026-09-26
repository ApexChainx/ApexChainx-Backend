"""Per-chunk hash commitments so audit chain verification survives
retention pruning of older JSONL archive chunks.
"""
import hashlib
from typing import Iterable


def compute_chunk_root(entry_hashes: Iterable[str]) -> str:
    """Fold a chunk's entry hashes into a single commitment root."""
    hasher = hashlib.sha256()
    for entry_hash in entry_hashes:
        hasher.update(entry_hash.encode("utf-8"))
    return hasher.hexdigest()


def verify_chunk_root(entry_hashes: Iterable[str], expected_root: str) -> bool:
    """Confirm a chunk's entries still fold to its recorded root."""
    return compute_chunk_root(entry_hashes) == expected_root
