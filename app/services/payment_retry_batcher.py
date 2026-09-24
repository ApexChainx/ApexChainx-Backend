"""Bounded, batched enqueueing for the failed-payment retry endpoint,
replacing an unbounded full-table load into memory.
"""
from typing import Any, Dict, List, Tuple

DEFAULT_BATCH_SIZE = 200


def build_retry_batch(failed_payment_ids: List[Any], cursor: int = 0,
                       batch_size: int = DEFAULT_BATCH_SIZE) -> Tuple[List[Any], bool, int]:
    """Slice a bounded batch of ids to enqueue, reporting has_more."""
    end = cursor + batch_size
    batch = failed_payment_ids[cursor:end]
    has_more = end < len(failed_payment_ids)
    return batch, has_more, end
