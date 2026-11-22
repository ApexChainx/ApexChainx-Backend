from app.sla_operations.utils.pagination import (
    PageMeta,
    clamp_page,
    clamp_page_size,
    compute_total_pages,
    paginate,
    paginate_query,
    slice_page,
)


class TestClampPage:
    def test_normal_page(self):
        assert clamp_page(1) == 1
        assert clamp_page(5) == 5

    def test_zero_page_defaults_to_one(self):
        assert clamp_page(0) == 1

    def test_negative_page_defaults_to_one(self):
        assert clamp_page(-10) == 1

    def test_none_page_defaults_to_one(self):
        assert clamp_page(None) == 1

    def test_custom_default(self):
        assert clamp_page(0, default=3) == 3
        assert clamp_page(-1, default=2) == 2


class TestClampPageSize:
    def test_normal_page_size(self):
        assert clamp_page_size(10) == 10
        assert clamp_page_size(50) == 50

    def test_zero_page_size_defaults(self):
        assert clamp_page_size(0) == 20

    def test_negative_page_size_defaults(self):
        assert clamp_page_size(-5) == 20

    def test_none_page_size_defaults(self):
        assert clamp_page_size(None) == 20

    def test_oversized_page_size_capped(self):
        assert clamp_page_size(1000) == 100
        assert clamp_page_size(101, max_size=100) == 100

    def test_custom_default_and_max(self):
        assert clamp_page_size(0, default=5) == 5
        assert clamp_page_size(500, max_size=50) == 50


class TestComputeTotalPages:
    def test_exact_division(self):
        assert compute_total_pages(100, 10) == 10

    def test_partial_last_page(self):
        assert compute_total_pages(101, 10) == 11

    def test_zero_total(self):
        assert compute_total_pages(0, 10) == 0

    def test_negative_total(self):
        assert compute_total_pages(-5, 10) == 0

    def test_zero_page_size(self):
        assert compute_total_pages(10, 0) == 0

    def test_negative_page_size(self):
        assert compute_total_pages(10, -1) == 0

    def test_single_item(self):
        assert compute_total_pages(1, 10) == 1


class TestSlicePage:
    def test_first_page(self):
        items = list(range(25))
        assert slice_page(items, 1, 10) == list(range(10))

    def test_middle_page(self):
        items = list(range(25))
        assert slice_page(items, 2, 10) == list(range(10, 20))

    def test_last_partial_page(self):
        items = list(range(25))
        assert slice_page(items, 3, 10) == list(range(20, 25))

    def test_page_beyond_end(self):
        items = list(range(25))
        assert slice_page(items, 4, 10) == []

    def test_empty_collection(self):
        assert slice_page([], 1, 10) == []

    def test_zero_page_treated_as_first(self):
        items = list(range(5))
        assert slice_page(items, 0, 10) == list(range(5))

    def test_negative_page_treated_as_first(self):
        items = list(range(5))
        assert slice_page(items, -3, 10) == list(range(5))

    def test_oversized_page_size(self):
        items = list(range(5))
        assert slice_page(items, 1, 1000) == list(range(5))

    def test_zero_page_size(self):
        items = list(range(5))
        assert slice_page(items, 1, 0) == []

    def test_boundary_total_exact(self):
        items = list(range(20))
        assert slice_page(items, 2, 10) == list(range(10, 20))
        assert slice_page(items, 3, 10) == []


class TestPaginate:
    def test_normal_page(self):
        items = list(range(25))
        result = paginate(items, page=1, page_size=10)
        assert result.items == list(range(10))
        assert result.meta.page == 1
        assert result.meta.page_size == 10
        assert result.meta.total == 25
        assert result.meta.total_pages == 3
        assert result.meta.has_next is True
        assert result.meta.has_previous is False

    def test_last_partial_page(self):
        items = list(range(25))
        result = paginate(items, page=3, page_size=10)
        assert result.items == list(range(20, 25))
        assert result.meta.page == 3
        assert result.meta.total_pages == 3
        assert result.meta.has_next is False
        assert result.meta.has_previous is True

    def test_empty_collection(self):
        result = paginate([], page=1, page_size=10)
        assert result.items == []
        assert result.meta.total == 0
        assert result.meta.total_pages == 0
        assert result.meta.has_next is False
        assert result.meta.has_previous is False

    def test_zero_page(self):
        items = list(range(5))
        result = paginate(items, page=0, page_size=10)
        assert result.meta.page == 1
        assert result.items == list(range(5))

    def test_negative_page(self):
        items = list(range(5))
        result = paginate(items, page=-2, page_size=10)
        assert result.meta.page == 1
        assert result.items == list(range(5))

    def test_oversized_page_size(self):
        items = list(range(5))
        result = paginate(items, page=1, page_size=1000)
        assert result.meta.page_size == 100
        assert result.items == list(range(5))

    def test_boundary_total_exact(self):
        items = list(range(20))
        result = paginate(items, page=2, page_size=10)
        assert result.items == list(range(10, 20))
        assert result.meta.total_pages == 2
        assert result.meta.has_next is False

    def test_page_beyond_end(self):
        items = list(range(5))
        result = paginate(items, page=10, page_size=10)
        assert result.items == []
        assert result.meta.page == 10
        assert result.meta.has_next is False
        assert result.meta.has_previous is True


class TestPaginateQuery:
    def test_normal_page(self):
        items = list(range(25))
        result = paginate_query(items, page=1, page_size=10)
        assert result.items == list(range(10))
        assert result.meta.total == 25

    def test_empty(self):
        result = paginate_query([], page=1, page_size=10)
        assert result.items == []
        assert result.meta.total == 0


class TestPageMeta:
    def test_metadata_consistency(self):
        meta = PageMeta.build(page=2, page_size=10, total=25)
        assert meta.page == 2
        assert meta.page_size == 10
        assert meta.total == 25
        assert meta.total_pages == 3
        assert meta.has_next is True
        assert meta.has_previous is True

    def test_metadata_first_page(self):
        meta = PageMeta.build(page=1, page_size=10, total=25)
        assert meta.has_previous is False
        assert meta.has_next is True

    def test_metadata_empty(self):
        meta = PageMeta.build(page=1, page_size=10, total=0)
        assert meta.total_pages == 0
        assert meta.has_next is False
        assert meta.has_previous is False

    def test_metadata_matches_paginate(self):
        items = list(range(25))
        result = paginate(items, page=2, page_size=10)
        expected = PageMeta.build(page=2, page_size=10, total=25)
        assert result.meta == expected
</parameter>