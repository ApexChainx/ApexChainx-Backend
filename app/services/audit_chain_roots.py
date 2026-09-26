"""Incremental chain roots so /audit/verify can confirm linkage in
near-constant time instead of re-hashing the full log every call.
"""
import hashlib
from typing import Iterable, Tuple


def fold_root(previous_root: str, entry_hash: str) -> str:
    return hashlib.sha256(f"{previous_root}:{entry_hash}".encode("utf-8")).hexdigest()


def compute_incremental_root(entry_hashes: Iterable[str], genesis_root: str = "0" * 64) -> str:
    root = genesis_root
    for entry_hash in entry_hashes:
        root = fold_root(root, entry_hash)
    return root


def verify_tail(last_verified_root: str, tail_entry_hashes: Iterable[str], claimed_root: str) -> Tuple[bool, str]:
    computed = compute_incremental_root(tail_entry_hashes, genesis_root=last_verified_root)
    return computed == claimed_root, computed
