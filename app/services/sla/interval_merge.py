"""Merges overlapping outage intervals before summing downtime, so
duplicate/overlapping periods aren't double-counted in SLA windows.
"""
from datetime import datetime
from typing import List, Tuple

Interval = Tuple[datetime, datetime]


def merge_intervals(intervals: List[Interval]) -> List[Interval]:
    if not intervals:
        return []
    ordered = sorted(intervals, key=lambda interval: interval[0])
    merged = [ordered[0]]
    for start, end in ordered[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged
