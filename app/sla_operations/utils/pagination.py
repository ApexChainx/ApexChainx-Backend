"""Pagination utilities for the SLA operations workspace.

This module provides a small, dependency-free pagination helper used by the
SLA operations workspace. It is intentionally isolated from the core
application so that contributor work here cannot destabilize routed behavior.

The constants and field names mirror ``app/sla_operations/schemas/pagination.py``
so that request/response schemas and utility results stay consistent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, Sequence, TypeVar

__all__ = [
    "DEFAULT_PAGE",
    "DEFAULT_PAGE_SIZE",
    "MAX_PAGE_SIZE",
    "PaginationError",
    "PaginationResult",
    "paginate",
]

# Defaults aligned with ``app/sla_operations/schemas/pagination.py``.
DEFAULT_PAGE: int = 1
DEFAULT_PAGE_SIZE: int = 20
MAX_PAGE_SIZE: int = 100

T = TypeVar("T")


class PaginationError(ValueError):
    """Raised when pagination parameters are invalid.

    Subclasses :class:`ValueError` so callers may catch either the specific
    error or the broader built-in exception.
    """


@dataclass(frozen=True)
class PaginationResult(Generic[T]):
    """Immutable result of a pagination operation.

    Attributes:
        items: The slice of items for the requested page.
        total: Total number of items across all pages.
        page: The 1-based page number that was requested.
        page_size: The maximum number of items per page.
        total_pages: Total number of pages (``0`` when there are no items).
        has_next: Whether a page after ``page`` exists.
        has_prev: Whether a page before ``page`` exists.
    """

    items: list[T]
    total: int
    page: int
    page_size: int
    total_pages: int
    has_next: bool
    has_prev: bool


def _compute_total_pages(total: int, page_size: int) -> int:
    """Return the number of pages needed to hold ``total`` items.

    Returns ``0`` when ``total`` is ``0``.
    """
    if total <= 0:
        return 0
    return (total + page_size - 1) // page_size


def paginate(
    items: Sequence[T],
    page: int = DEFAULT_PAGE,
    page_size: int = DEFAULT_PAGE_SIZE,
    max_page_size: int = MAX_PAGE_SIZE,
) -> PaginationResult[T]:
    """Paginate ``items`` for the requested ``page`` and ``page_size``.

    Args:
        items: The full sequence of items to paginate. It is not mutated.
        page: 1-based page number. Must be ``>= 1``.
        page_size: Number of items per page. Must satisfy
            ``1 <= page_size <= max_page_size``.
        max_page_size: Upper bound for ``page_size``.

    Returns:
        A :class:`PaginationResult` describing the requested page.

    Raises:
        PaginationError: If ``page`` or ``page_size`` are out of bounds, or if
            ``max_page_size`` is not a positive integer.
    """
    if not isinstance(max_page_size, int) or isinstance(max_page_size, bool):
        raise PaginationError("max_page_size must be an integer")
    if max_page_size < 1:
        raise PaginationError("max_page_size must be >= 1")

    if not isinstance(page, int) or isinstance(page, bool):
        raise PaginationError("page must be an integer")
    if page < 1:
        raise PaginationError(f"page must be >= 1, got {page}")

    if not isinstance(page_size, int) or isinstance(page_size, bool):
        raise PaginationError("page_size must be an integer")
    if page_size < 1:
        raise PaginationError(f"page_size must be >= 1, got {page_size}")
    if page_size > max_page_size:
        raise PaginationError(
            f"page_size must be <= {max_page_size}, got {page_size}"
        )

    # Materialize a shallow copy so the caller's sequence is never mutated and
    # so slicing works uniformly for any ``Sequence`` implementation.
    materialized: list[T] = list(items)
    total = len(materialized)
    total_pages = _compute_total_pages(total, page_size)

    start = (page - 1) * page_size
    end = start + page_size
    page_items = materialized[start:end]

    has_prev = page > 1
    has_next = page < total_pages

    return PaginationResult(
        items=page_items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
        has_next=has_next,
        has_prev=has_prev,
    )