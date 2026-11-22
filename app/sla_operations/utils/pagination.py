"""Pagination utilities for the SLA operations workspace.

This module provides small, dependency-light, pure helpers for paginating
collections and query results. It is intended to be reused by workspace
services and schemas (for example ``app.sla_operations.schemas.pagination``).

All helpers are deterministic and free of application state. Invalid inputs
raise :class:`ValueError` with a descriptive message so callers can surface
clear validation errors.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, Sequence, TypeVar

__all__ = [
    "DEFAULT_PAGE",
    "DEFAULT_PAGE_SIZE",
    "MIN_PAGE_SIZE",
    "MAX_PAGE_SIZE",
    "PageResult",
    "validate_page",
    "validate_page_size",
    "compute_offset",
    "compute_total_pages",
    "slice_collection",
    "build_page_meta",
]

T = TypeVar("T")

#: Default page number used when none is supplied.
DEFAULT_PAGE: int = 1

#: Default page size used when none is supplied.
DEFAULT_PAGE_SIZE: int = 20

#: Smallest allowed page size.
MIN_PAGE_SIZE: int = 1

#: Largest allowed page size.
MAX_PAGE_SIZE: int = 100


@dataclass(frozen=True)
class PageResult(Generic[T]):
    """Immutable result of slicing a collection into a single page.

    Attributes:
        items: The items contained in the requested page.
        total: Total number of items across all pages (never negative).
        page: The 1-based page number that was requested.
        page_size: The maximum number of items per page.
        total_pages: Total number of pages for the given ``total`` and
            ``page_size``. Zero when ``total`` is zero.
    """

    items: list[T]
    total: int
    page: int
    page_size: int
    total_pages: int


def validate_page(page: int, *, default: int = DEFAULT_PAGE) -> int:
    """Validate and normalize a 1-based page number.

    Args:
        page: The requested page number. Must be an integer ``>= 1``.
        default: Value returned when ``page`` is ``None``. Defaults to
            :data:`DEFAULT_PAGE`.

    Returns:
        The validated page number as an ``int``.

    Raises:
        ValueError: If ``page`` is ``None`` and no default is available, if
            ``page`` is not an integer, or if ``page < 1``.
    """
    if page is None:
        if default is None:
            raise ValueError("page must be provided")
        return default
    if isinstance(page, bool) or not isinstance(page, int):
        raise ValueError(f"page must be an integer, got {type(page).__name__}")
    if page < 1:
        raise ValueError(f"page must be >= 1, got {page}")
    return page


def validate_page_size(
    page_size: int,
    *,
    default: int = DEFAULT_PAGE_SIZE,
    min_size: int = MIN_PAGE_SIZE,
    max_size: int = MAX_PAGE_SIZE,
) -> int:
    """Validate and normalize a page size.

    Args:
        page_size: The requested number of items per page. Must be an integer
            within ``[min_size, max_size]``.
        default: Value returned when ``page_size`` is ``None``. Defaults to
            :data:`DEFAULT_PAGE_SIZE`.
        min_size: Inclusive lower bound. Defaults to :data:`MIN_PAGE_SIZE`.
        max_size: Inclusive upper bound. Defaults to :data:`MAX_PAGE_SIZE`.

    Returns:
        The validated page size as an ``int``.

    Raises:
        ValueError: If ``page_size`` is ``None`` and no default is available,
            if ``page_size`` is not an integer, or if it falls outside the
            allowed bounds.
    """
    if page_size is None:
        if default is None:
            raise ValueError("page_size must be provided")
        return default
    if isinstance(page_size, bool) or not isinstance(page_size, int):
        raise ValueError(
            f"page_size must be an integer, got {type(page_size).__name__}"
        )
    if page_size < min_size or page_size > max_size:
        raise ValueError(
            f"page_size must be between {min_size} and {max_size}, got {page_size}"
        )
    return page_size


def compute_offset(page: int, page_size: int) -> int:
    """Compute the zero-based offset for a page.

    Args:
        page: The 1-based page number. Must be ``>= 1``.
        page_size: The number of items per page. Must be ``>= 1``.

    Returns:
        The offset, i.e. ``(page - 1) * page_size``.

    Raises:
        ValueError: If ``page < 1`` or ``page_size < 1``.
    """
    validated_page = validate_page(page)
    validated_size = validate_page_size(page_size)
    return (validated_page - 1) * validated_size


def compute_total_pages(total: int, page_size: int) -> int:
    """Compute the number of pages needed to hold ``total`` items.

    Args:
        total: Total number of items. Must be ``>= 0``.
        page_size: The number of items per page. Must be ``>= 1``.

    Returns:
        The total number of pages. Returns ``0`` when ``total`` is ``0``.

    Raises:
        ValueError: If ``total < 0`` or ``page_size < 1``.
    """
    if isinstance(total, bool) or not isinstance(total, int):
        raise ValueError(f"total must be an integer, got {type(total).__name__}")
    if total < 0:
        raise ValueError(f"total must be >= 0, got {total}")
    validated_size = validate_page_size(page_size)
    if total == 0:
        return 0
    return (total + validated_size - 1) // validated_size


def slice_collection(
    items: Sequence[T],
    page: int = DEFAULT_PAGE,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> PageResult[T]:
    """Slice a collection into a single page and return a typed result.

    Args:
        items: The full collection of items to paginate.
        page: The 1-based page number. Defaults to :data:`DEFAULT_PAGE`.
        page_size: The number of items per page. Defaults to
            :data:`DEFAULT_PAGE_SIZE`.

    Returns:
        A :class:`PageResult` containing the sliced items, the total item
        count, the requested page, the page size, and the total page count.
        When ``page`` is beyond the last page, ``items`` is empty.

    Raises:
        ValueError: If ``page < 1`` or ``page_size`` is out of bounds.
    """
    validated_page = validate_page(page)
    validated_size = validate_page_size(page_size)

    total = len(items)
    total_pages = compute_total_pages(total, validated_size)
    offset = (validated_page - 1) * validated_size
    page_items = list(items[offset : offset + validated_size])

    return PageResult(
        items=page_items,
        total=total,
        page=validated_page,
        page_size=validated_size,
        total_pages=total_pages,
    )


def build_page_meta(
    total: int,
    page: int = DEFAULT_PAGE,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> dict[str, int]:
    """Build pagination metadata compatible with ``PageMeta``.

    Produces a plain ``dict`` with the fields expected by
    ``app.sla_operations.schemas.pagination.PageMeta`` (``page``,
    ``page_size``, ``total``, ``total_pages``). Returning a ``dict`` keeps this
    utility independent of Pydantic while remaining directly usable as
    ``PageMeta(**build_page_meta(...))``.

    Args:
        total: Total number of items. Must be ``>= 0``.
        page: The 1-based page number. Defaults to :data:`DEFAULT_PAGE`.
        page_size: The number of items per page. Defaults to
            :data:`DEFAULT_PAGE_SIZE`.

    Returns:
        A dictionary with keys ``page``, ``page_size``, ``total``, and
        ``total_pages``.

    Raises:
        ValueError: If ``total < 0``, ``page < 1``, or ``page_size`` is out of
            bounds.
    """
    validated_page = validate_page(page)
    validated_size = validate_page_size(page_size)
    total_pages = compute_total_pages(total, validated_size)
    return {
        "page": validated_page,
        "page_size": validated_size,
        "total": total,
        "total_pages": total_pages,
    }