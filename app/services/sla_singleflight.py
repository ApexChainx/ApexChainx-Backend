"""Single-flight guard for SLA recompute, using the existing
app/core/lock.py acquire semantics to prevent a thundering-herd
recompute storm on cache expiry.
"""
from typing import Any, Callable


async def compute_with_singleflight(cache_key: str, lock_acquire: Callable,
                                     compute_fn: Callable[[], Any],
                                     cache_get: Callable[[str], Any]) -> Any:
    async with lock_acquire(f"sla-recompute:{cache_key}"):
        cached = cache_get(cache_key)
        if cached is not None:
            return cached
        return await compute_fn()
