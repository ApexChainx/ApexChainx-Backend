"""Unit tests for the reusable pagination and filtering schemas."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.sla_operations.schemas.pagination import (
    MAX_PAGE_SIZE,
    CursorPage,
    CursorParams,
    DateRangeFilter,
    FilterParams,
    ListQueryParams,
    PageMeta,
    PaginatedResult,
    PaginationParams,
    SortDirection,
    SortParams,
)


def test_pagination_defaults_and_derived_offset():
    params = PaginationParams()
    assert (params.page, params.page_size, params.offset) == (1, 20, 0)
    assert PaginationParams(page=3, page_size=20).offset == 40
    assert "offset" not in params.model_dump()


@pytest.mark.parametrize("payload", [{"page": 0}, {"page_size": 0}, {"page_size": MAX_PAGE_SIZE + 1}])
def test_pagination_rejects_out_of_range_values(payload):
    with pytest.raises(ValidationError):
        PaginationParams(**payload)


def test_cursor_params_require_non_blank_cursor_and_valid_limit():
    assert CursorParams().cursor is None
    assert CursorParams(cursor="next-page", limit=5).limit == 5
    with pytest.raises(ValidationError):
        CursorParams(cursor="")
    with pytest.raises(ValidationError):
        CursorParams(limit=MAX_PAGE_SIZE + 1)


def test_sort_params_validate_field_shape_and_direction():
    sort = SortParams()
    assert (sort.sort_by, sort.sort_dir) == ("created_at", SortDirection.desc)
    assert SortParams(sort_by="detected_at", sort_dir="asc").sort_dir is SortDirection.asc
    with pytest.raises(ValidationError):
        SortParams(sort_by="1bad;drop")
    with pytest.raises(ValidationError):
        SortParams(sort_dir="sideways")


def test_date_range_filter_normalizes_timezones_and_checks_order():
    filt = DateRangeFilter(date_from=datetime(2026, 1, 1, tzinfo=UTC), date_to="2026-01-02T02:00:00+02:00")
    assert filt.date_to == datetime(2026, 1, 2, tzinfo=UTC)
    with pytest.raises(ValidationError, match="date_from cannot be after date_to"):
        DateRangeFilter(date_from="2026-01-03T00:00:00Z", date_to="2026-01-02T00:00:00Z")
    with pytest.raises(ValidationError, match="timezone-aware"):
        DateRangeFilter(date_from="2026-01-01T00:00:00")


def test_filter_params_reject_blank_search_and_record_ids():
    filt = FilterParams(search="fiber", status="open", record_ids=["outage-1"])
    assert (filt.search, filt.status, filt.record_ids) == ("fiber", "open", ["outage-1"])
    with pytest.raises(ValidationError, match="search cannot be blank"):
        FilterParams(search="   ")
    with pytest.raises(ValidationError, match="record_ids cannot contain blank values"):
        FilterParams(record_ids=[""])
    with pytest.raises(ValidationError):
        FilterParams(record_ids=[f"id-{index}" for index in range(51)])


def test_list_query_params_combine_all_inputs():
    query = ListQueryParams(page=2, page_size=10, sort_by="severity", sort_dir="asc", search="site-a")
    assert query.offset == 10
    assert query.sort_dir is SortDirection.asc
    assert query.search == "site-a"
    with pytest.raises(ValidationError):
        ListQueryParams(page=0, sort_dir="asc")


@pytest.mark.parametrize(
    "payload",
    [
        {"page": 1, "page_size": 20, "total": 25, "returned": 20, "has_more": True},
        {"page": 2, "page_size": 20, "total": 25, "returned": 5, "has_more": False},
        {"page": 1, "page_size": 20, "total": 0, "returned": 0, "has_more": False},
    ],
)
def test_page_meta_accepts_consistent_payloads(payload):
    assert PageMeta(**payload).model_dump() == payload


@pytest.mark.parametrize(
    "payload",
    [
        {"page": 1, "page_size": 20, "total": 5, "returned": 4, "has_more": False},
        {"page": 1, "page_size": 20, "total": 25, "returned": 20, "has_more": False},
        {"page": 0, "page_size": 20, "total": 0, "returned": 0, "has_more": False},
        {"page": 1, "page_size": 20, "total": -1, "returned": 0, "has_more": False},
    ],
)
def test_page_meta_rejects_inconsistent_payloads(payload):
    with pytest.raises(ValidationError):
        PageMeta(**payload)


def test_paginated_result_is_generic_and_supports_attributes():
    meta = PageMeta(page=1, page_size=20, total=2, returned=2, has_more=False)
    result = PaginatedResult[int](items=[1, 2], meta=meta)
    assert result.items == [1, 2]
    from_attributes = PaginatedResult[int].model_validate(SimpleNamespace(items=[3], meta=meta))
    assert from_attributes.items == [3]


def test_cursor_page_requires_cursor_when_more_rows_exist():
    assert CursorPage[int](items=[1], has_more=False).next_cursor is None
    page = CursorPage[str](items=["a"], next_cursor="opaque", has_more=True)
    assert page.next_cursor == "opaque"
    with pytest.raises(ValidationError, match="next_cursor is required"):
        CursorPage[int](items=[1], has_more=True)
