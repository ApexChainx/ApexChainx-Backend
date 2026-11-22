"""Unit tests for the in-memory pagination service."""

import pytest

from app.sla_operations.schemas.pagination import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    CursorParams,
    PageMeta,
    PaginationParams,
)
from app.sla_operations.services.pagination_service import (
    InvalidCursorError,
    decode_cursor,
    encode_cursor,
    normalize_pagination,
    paginate,
    paginate_cursor,
    slice_page,
    total_pages,
)

OUTAGES = [f"outage-{index}" for index in range(1, 26)]  # 25 records


def test_first_page_returns_slice_and_metadata():
    result = paginate(OUTAGES, PaginationParams(page=1, page_size=10))

    assert result.items == OUTAGES[:10]
    assert result.meta.model_dump() == {"page": 1, "page_size": 10, "total": 25, "returned": 10, "has_more": True}


def test_last_partial_page_has_no_more():
    result = paginate(OUTAGES, PaginationParams(page=3, page_size=10))

    assert result.items == OUTAGES[20:]
    assert (result.meta.returned, result.meta.has_more) == (5, False)


def test_exact_boundary_page_has_no_more():
    result = paginate(OUTAGES[:20], PaginationParams(page=2, page_size=10))

    assert result.items == OUTAGES[10:20]
    assert result.meta.has_more is False


def test_defaults_are_used_when_no_params_given():
    result = paginate(list(range(45)))

    assert result.meta.page == 1
    assert result.meta.page_size == DEFAULT_PAGE_SIZE
    assert result.items == list(range(DEFAULT_PAGE_SIZE))


def test_page_past_the_end_is_empty_and_keeps_real_total():
    result = paginate(OUTAGES, PaginationParams(page=99, page_size=10))

    assert result.items == []
    assert result.meta.model_dump() == {"page": 99, "page_size": 10, "total": 25, "returned": 0, "has_more": False}


@pytest.mark.parametrize("empty", [None, [], (), "", b"", 42, object()])
def test_empty_or_invalid_collections_return_an_empty_page(empty):
    result = paginate(empty, PaginationParams(page=1, page_size=5))

    assert result.items == []
    assert result.meta.model_dump() == {"page": 1, "page_size": 5, "total": 0, "returned": 0, "has_more": False}


@pytest.mark.parametrize("source", [tuple(range(7)), range(7), (n for n in range(7)), iter(range(7))])
def test_non_list_iterables_are_supported(source):
    result = paginate(source, PaginationParams(page=2, page_size=3))

    assert result.items == [3, 4, 5]
    assert (result.meta.total, result.meta.has_more) == (7, True)


def test_input_is_not_mutated_and_output_is_a_new_list():
    data = [{"id": 1}, {"id": 2}, {"id": 3}]
    snapshot = list(data)

    result = paginate(data, PaginationParams(page=1, page_size=2))

    assert data == snapshot
    assert result.items is not data
    assert result.items[0] is data[0]  # items are shared, not deep-copied


def test_results_are_deterministic():
    params = PaginationParams(page=2, page_size=4)

    assert paginate(OUTAGES, params) == paginate(OUTAGES, params)


def test_max_page_size_is_respected():
    big = list(range(MAX_PAGE_SIZE * 2))

    result = paginate(big, PaginationParams(page=1, page_size=MAX_PAGE_SIZE))

    assert len(result.items) == MAX_PAGE_SIZE
    assert result.meta.has_more is True


def test_metadata_is_always_consistent_for_many_shapes():
    for total in (0, 1, 9, 10, 11, 100):
        for page_size in (1, 3, 10, MAX_PAGE_SIZE):
            for page in (1, 2, 5, 50):
                result = paginate(list(range(total)), PaginationParams(page=page, page_size=page_size))
                # Re-validating proves the metadata satisfies the PageMeta invariants.
                PageMeta(**result.meta.model_dump())
                assert result.meta.returned == len(result.items)


@pytest.mark.parametrize(
    ("page", "page_size", "expected"),
    [
        (None, None, (1, DEFAULT_PAGE_SIZE)),
        (0, 0, (1, 1)),
        (-5, -5, (1, 1)),
        (3, 10, (3, 10)),
        (2, MAX_PAGE_SIZE + 500, (2, MAX_PAGE_SIZE)),
        ("2", "5", (1, DEFAULT_PAGE_SIZE)),
        (True, False, (1, DEFAULT_PAGE_SIZE)),
        (2.5, 7.5, (1, DEFAULT_PAGE_SIZE)),
    ],
)
def test_normalize_pagination_never_raises_and_clamps(page, page_size, expected):
    params = normalize_pagination(page, page_size)

    assert (params.page, params.page_size) == expected


def test_slice_page_returns_only_items():
    assert slice_page(OUTAGES, 2, 10) == OUTAGES[10:20]
    assert slice_page(None, 1, 10) == []
    assert slice_page(OUTAGES, 0, 0) == OUTAGES[:1]


@pytest.mark.parametrize(
    ("total", "page_size", "expected"),
    [(0, 10, 0), (-3, 10, 0), (1, 10, 1), (10, 10, 1), (11, 10, 2), (25, 10, 3), (5, 0, 1), (500, 1000, 5)],
)
def test_total_pages(total, page_size, expected):
    assert total_pages(total, page_size) == expected


def test_cursor_round_trip_and_opacity():
    cursor = encode_cursor(40)

    assert decode_cursor(cursor) == 40
    assert "40" not in cursor
    assert decode_cursor(encode_cursor(0)) == 0


@pytest.mark.parametrize("offset", [-1, True, 1.5, "3", None])
def test_encode_cursor_rejects_bad_offsets(offset):
    with pytest.raises(ValueError, match="non-negative integer"):
        encode_cursor(offset)


@pytest.mark.parametrize(
    "cursor",
    ["", "not base64!!", "bm90LWFuLW9mZnNldA==", encode_cursor(1)[:-3], "b2Zmc2V0Oi0x", "b2Zmc2V0OjEuNQ==", "ZsOpbGl4"],
)
def test_decode_cursor_rejects_invalid_values(cursor):
    with pytest.raises(InvalidCursorError):
        decode_cursor(cursor)


def test_decode_cursor_rejects_non_strings():
    with pytest.raises(InvalidCursorError):
        decode_cursor(None)  # type: ignore[arg-type]


def test_invalid_cursor_error_is_a_value_error():
    assert issubclass(InvalidCursorError, ValueError)


def test_cursor_pagination_walks_every_item_exactly_once():
    seen: list[str] = []
    cursor: str | None = None
    for _ in range(10):  # safety bound so a bug cannot loop forever
        page = paginate_cursor(OUTAGES, CursorParams(cursor=cursor, limit=10))
        seen.extend(page.items)
        if not page.has_more:
            assert page.next_cursor is None
            break
        cursor = page.next_cursor

    assert seen == OUTAGES


def test_cursor_first_page_and_defaults():
    page = paginate_cursor(OUTAGES)

    assert page.items == OUTAGES[:DEFAULT_PAGE_SIZE]
    assert page.has_more is True
    assert decode_cursor(page.next_cursor) == DEFAULT_PAGE_SIZE


def test_cursor_pagination_handles_empty_and_exhausted_data():
    assert paginate_cursor(None).model_dump() == {"items": [], "next_cursor": None, "has_more": False}
    assert paginate_cursor([]).has_more is False

    past_end = paginate_cursor(OUTAGES, CursorParams(cursor=encode_cursor(500), limit=10))
    assert (past_end.items, past_end.has_more, past_end.next_cursor) == ([], False, None)


def test_cursor_pagination_rejects_tampered_cursor():
    with pytest.raises(InvalidCursorError):
        paginate_cursor(OUTAGES, CursorParams(cursor="tampered-value", limit=5))
