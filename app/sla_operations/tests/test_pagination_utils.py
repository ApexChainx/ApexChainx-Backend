"""Tests for the SLA operations workspace pagination utility.

These tests are deterministic and isolated from core application state. They
exercise ``app.sla_operations.utils.pagination`` only.
"""

from __future__ import annotations

from typing import List

import pytest

from app.sla_operations.utils.pagination import (
    PaginationError,
    PaginationResult,
    paginate,
)


def _make_items(count: int) -> List[int]:
    """Return a deterministic list of ``count`` integers starting at 0."""
    return list(range(count))


class TestPaginateFirstPage:
    def test_first_page_returns_leading_slice(self) -> None:
        items = _make_items(25)
        result = paginate(items, page=1, page_size=10)

        assert isinstance(result, PaginationResult)
        assert result.items == list(range(10))
        assert result.page == 1
        assert result.page_size == 10
        assert result.total == 25
        assert result.total_pages == 3
        assert result.has_prev is False
        assert result.has_next is True

    def test_first_page_with_exact_page_size(self) -> None:
        items = _make_items(10)
        result = paginate(items, page=1, page_size=10)

        assert result.items == list(range(10))
        assert result.total == 10
        assert result.total_pages == 1
        assert result.has_prev is False
        assert result.has_next is False


class TestPaginateMiddlePage:
    def test_middle_page_returns_interior_slice(self) -> None:
        items = _make_items(25)
        result = paginate(items, page=2, page_size=10)

        assert result.items == list(range(10, 20))
        assert result.page == 2
        assert result.total == 25
        assert result.total_pages == 3
        assert result.has_prev is True
        assert result.has_next is True


class TestPaginateLastPage:
    def test_last_page_returns_partial_slice(self) -> None:
        items = _make_items(25)
        result = paginate(items, page=3, page_size=10)

        assert result.items == list(range(20, 25))
        assert result.page == 3
        assert result.total == 25
        assert result.total_pages == 3
        assert result.has_prev is True
        assert result.has_next is False

    def test_last_page_when_exact_multiple(self) -> None:
        items = _make_items(20)
        result = paginate(items, page=2, page_size=10)

        assert result.items == list(range(10, 20))
        assert result.total == 20
        assert result.total_pages == 2
        assert result.has_prev is True
        assert result.has_next is False


class TestPaginateOutOfRangePage:
    def test_page_beyond_last_returns_empty_items_with_metadata(self) -> None:
        items = _make_items(25)
        result = paginate(items, page=10, page_size=10)

        assert result.items == []
        assert result.page == 10
        assert result.page_size == 10
        assert result.total == 25
        assert result.total_pages == 3
        assert result.has_prev is True
        assert result.has_next is False

    def test_page_just_past_last_returns_empty_items(self) -> None:
        items = _make_items(25)
        result = paginate(items, page=4, page_size=10)

        assert result.items == []
        assert result.total == 25
        assert result.total_pages == 3
        assert result.has_prev is True
        assert result.has_next is False


class TestPaginateSlicing:
    def test_exact_page_slicing(self) -> None:
        items = _make_items(30)
        result = paginate(items, page=2, page_size=10)

        assert result.items == list(range(10, 20))

    def test_partial_page_slicing(self) -> None:
        items = _make_items(23)
        result = paginate(items, page=3, page_size=10)

        assert result.items == list(range(20, 23))

    def test_single_item_page_size(self) -> None:
        items = _make_items(5)
        result = paginate(items, page=3, page_size=1)

        assert result.items == [2]
        assert result.total == 5
        assert result.total_pages == 5
        assert result.has_prev is True
        assert result.has_next is True


class TestPaginateEmptyInput:
    def test_empty_input_metadata(self) -> None:
        result = paginate([], page=1, page_size=10)

        assert result.items == []
        assert result.page == 1
        assert result.page_size == 10
        assert result.total == 0
        assert result.total_pages == 0
        assert result.has_prev is False
        assert result.has_next is False

    def test_empty_input_out_of_range_page(self) -> None:
        result = paginate([], page=5, page_size=10)

        assert result.items == []
        assert result.total == 0
        assert result.total_pages == 0
        assert result.has_prev is False
        assert result.has_next is False


class TestPaginationFlagsAcrossPages:
    @pytest.mark.parametrize(
        "page, expected_has_prev, expected_has_next",
        [
            (1, False, True),
            (2, True, True),
            (3, True, False),
        ],
    )
    def test_flags_across_pages(
        self,
        page: int,
        expected_has_prev: bool,
        expected_has_next: bool,
    ) -> None:
        items = _make_items(25)
        result = paginate(items, page=page, page_size=10)

        assert result.has_prev is expected_has_prev
        assert result.has_next is expected_has_next

    def test_single_page_flags(self) -> None:
        items = _make_items(5)
        result = paginate(items, page=1, page_size=10)

        assert result.has_prev is False
        assert result.has_next is False


class TestPaginationValidation:
    @pytest.mark.parametrize("page", [0, -1, -100])
    def test_invalid_page_raises(self, page: int) -> None:
        with pytest.raises(PaginationError):
            paginate(_make_items(10), page=page, page_size=10)

    @pytest.mark.parametrize("page_size", [0, -1, -10])
    def test_invalid_page_size_raises(self, page_size: int) -> None:
        with pytest.raises(PaginationError):
            paginate(_make_items(10), page=1, page_size=page_size)

    def test_page_size_above_max_raises(self) -> None:
        with pytest.raises(PaginationError):
            paginate(_make_items(10), page=1, page_size=10_000)

    def test_page_size_at_max_is_allowed(self) -> None:
        items = _make_items(5)
        result = paginate(items, page=1, page_size=100)

        assert result.items == items
        assert result.page_size == 100


class TestPaginationDoesNotMutateInput:
    def test_input_sequence_not_mutated(self) -> None:
        items = _make_items(25)
        snapshot = list(items)

        paginate(items, page=2, page_size=10)

        assert items == snapshot

    def test_input_sequence_not_mutated_on_empty(self) -> None:
        items: List[int] = []
        paginate(items, page=1, page_size=10)

        assert items == []