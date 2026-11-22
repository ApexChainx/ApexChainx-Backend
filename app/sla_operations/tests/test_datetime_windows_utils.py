"""Tests for SLA operations datetime window utilities.

These tests are deterministic and isolated from core application state.
They exercise parsing, bucketing, and boundary behavior of
``app.sla_operations.utils.datetime_windows``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.sla_operations.utils.datetime_windows import (
    TimeWindow,
    generate_buckets,
    parse_datetime,
    parse_window,
)


UTC = timezone.utc


# ---------------------------------------------------------------------------
# parse_datetime
# ---------------------------------------------------------------------------


def test_parse_datetime_accepts_iso_string_with_offset() -> None:
    result = parse_datetime("2024-01-15T10:30:00+00:00")
    assert result == datetime(2024, 1, 15, 10, 30, tzinfo=UTC)
    assert result.tzinfo is not None


def test_parse_datetime_accepts_iso_string_with_z_suffix() -> None:
    result = parse_datetime("2024-01-15T10:30:00Z")
    assert result == datetime(2024, 1, 15, 10, 30, tzinfo=UTC)


def test_parse_datetime_accepts_aware_datetime() -> None:
    value = datetime(2024, 1, 15, 10, 30, tzinfo=UTC)
    assert parse_datetime(value) == value


def test_parse_datetime_preserves_non_utc_offset() -> None:
    result = parse_datetime("2024-01-15T10:30:00+02:00")
    assert result.utcoffset() == timedelta(hours=2)
    assert result == datetime(2024, 1, 15, 8, 30, tzinfo=UTC)


def test_parse_datetime_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError):
        parse_datetime(datetime(2024, 1, 15, 10, 30))


def test_parse_datetime_rejects_naive_iso_string() -> None:
    with pytest.raises(ValueError):
        parse_datetime("2024-01-15T10:30:00")


def test_parse_datetime_rejects_invalid_string() -> None:
    with pytest.raises(ValueError):
        parse_datetime("not-a-datetime")


def test_parse_datetime_rejects_unsupported_type() -> None:
    with pytest.raises(ValueError):
        parse_datetime(12345)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# parse_window
# ---------------------------------------------------------------------------


def test_parse_window_returns_time_window() -> None:
    window = parse_window("2024-01-15T00:00:00Z", "2024-01-15T06:00:00Z")
    assert isinstance(window, TimeWindow)
    assert window.start == datetime(2024, 1, 15, 0, 0, tzinfo=UTC)
    assert window.end == datetime(2024, 1, 15, 6, 0, tzinfo=UTC)


def test_parse_window_accepts_datetime_objects() -> None:
    start = datetime(2024, 1, 15, 0, 0, tzinfo=UTC)
    end = datetime(2024, 1, 15, 6, 0, tzinfo=UTC)
    window = parse_window(start, end)
    assert window.start == start
    assert window.end == end


def test_parse_window_rejects_end_before_start() -> None:
    with pytest.raises(ValueError):
        parse_window("2024-01-15T06:00:00Z", "2024-01-15T00:00:00Z")


def test_parse_window_rejects_equal_start_and_end() -> None:
    with pytest.raises(ValueError):
        parse_window("2024-01-15T00:00:00Z", "2024-01-15T00:00:00Z")


def test_parse_window_rejects_naive_input() -> None:
    with pytest.raises(ValueError):
        parse_window(
            datetime(2024, 1, 15, 0, 0),
            datetime(2024, 1, 15, 6, 0),
        )


# ---------------------------------------------------------------------------
# generate_buckets
# ---------------------------------------------------------------------------


def test_generate_buckets_count_and_alignment() -> None:
    window = parse_window("2024-01-15T00:00:00Z", "2024-01-15T06:00:00Z")
    buckets = generate_buckets(window, bucket_size=timedelta(hours=1))

    assert len(buckets) == 6
    assert buckets[0].start == datetime(2024, 1, 15, 0, 0, tzinfo=UTC)
    assert buckets[0].end == datetime(2024, 1, 15, 1, 0, tzinfo=UTC)
    assert buckets[-1].start == datetime(2024, 1, 15, 5, 0, tzinfo=UTC)
    assert buckets[-1].end == datetime(2024, 1, 15, 6, 0, tzinfo=UTC)


def test_generate_buckets_are_contiguous() -> None:
    window = parse_window("2024-01-15T00:00:00Z", "2024-01-15T03:00:00Z")
    buckets = generate_buckets(window, bucket_size=timedelta(minutes=30))

    assert len(buckets) == 6
    for previous, current in zip(buckets, buckets[1:]):
        assert previous.end == current.start


def test_generate_buckets_partial_trailing_bucket() -> None:
    window = parse_window("2024-01-15T00:00:00Z", "2024-01-15T02:30:00Z")
    buckets = generate_buckets(window, bucket_size=timedelta(hours=1))

    assert len(buckets) == 3
    assert buckets[-1].start == datetime(2024, 1, 15, 2, 0, tzinfo=UTC)
    assert buckets[-1].end == datetime(2024, 1, 15, 2, 30, tzinfo=UTC)


def test_generate_buckets_single_bucket_when_window_smaller_than_size() -> None:
    window = parse_window("2024-01-15T00:00:00Z", "2024-01-15T00:10:00Z")
    buckets = generate_buckets(window, bucket_size=timedelta(hours=1))

    assert len(buckets) == 1
    assert buckets[0].start == window.start
    assert buckets[0].end == window.end


def test_generate_buckets_rejects_non_positive_bucket_size() -> None:
    window = parse_window("2024-01-15T00:00:00Z", "2024-01-15T06:00:00Z")
    with pytest.raises(ValueError):
        generate_buckets(window, bucket_size=timedelta(0))
    with pytest.raises(ValueError):
        generate_buckets(window, bucket_size=timedelta(seconds=-1))


# ---------------------------------------------------------------------------
# TimeWindow boundary behavior
# ---------------------------------------------------------------------------


def test_time_window_contains_is_inclusive_start_exclusive_end() -> None:
    window = parse_window("2024-01-15T00:00:00Z", "2024-01-15T06:00:00Z")

    assert window.contains(datetime(2024, 1, 15, 0, 0, tzinfo=UTC))
    assert window.contains(datetime(2024, 1, 15, 3, 0, tzinfo=UTC))
    assert not window.contains(datetime(2024, 1, 15, 6, 0, tzinfo=UTC))
    assert not window.contains(datetime(2024, 1, 14, 23, 59, tzinfo=UTC))


def test_time_window_contains_rejects_naive_datetime() -> None:
    window = parse_window("2024-01-15T00:00:00Z", "2024-01-15T06:00:00Z")
    with pytest.raises(ValueError):
        window.contains(datetime(2024, 1, 15, 3, 0))


def test_time_window_duration() -> None:
    window = parse_window("2024-01-15T00:00:00Z", "2024-01-15T06:00:00Z")
    assert window.duration == timedelta(hours=6)


def test_time_window_empty_window_rejected() -> None:
    with pytest.raises(ValueError):
        TimeWindow(
            start=datetime(2024, 1, 15, 0, 0, tzinfo=UTC),
            end=datetime(2024, 1, 15, 0, 0, tzinfo=UTC),
        )


def test_time_window_rejects_naive_bounds() -> None:
    with pytest.raises(ValueError):
        TimeWindow(
            start=datetime(2024, 1, 15, 0, 0),
            end=datetime(2024, 1, 15, 6, 0),
        )