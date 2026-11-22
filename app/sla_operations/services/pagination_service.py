"""Paginate and slice in-memory collections with consistent metadata.

This service is pure and database-independent: it works on lists, tuples, ranges,
generators, or any other iterable of records (ORM rows, dicts, Pydantic models), and
it reuses the workspace schemas in ``app.sla_operations.schemas.pagination``.

Assumptions (also covered by the unit tests):

* Pages are 1-based. ``page_size`` is clamped to ``1..MAX_PAGE_SIZE``.
* ``None`` and non-collection inputs (including ``str``/``bytes``, which are almost
  always a caller bug) are treated as an empty dataset instead of raising.
* ``normalize_pagination`` is the lenient entry point: ``None``, non-integers, and
  ``bool`` fall back to defaults, and out-of-range numbers are clamped. Callers that
  already hold a validated ``PaginationParams`` can pass it straight to ``paginate``.
* Items are returned in input order and are never mutated or deep-copied. Only the
  returned list is new, so two calls with the same input give identical output.
* A page past the end is not an error: it returns no items, ``has_more=False``, and
  the real ``total`` so clients can detect and recover from it.
* Cursors are opaque offset tokens. A malformed cursor raises ``InvalidCursorError``
  (a ``ValueError``, which the workspace error mapping reports as a 422); an offset
  past the end of the data returns an empty last page.

Complexity: for a ``Sequence`` (list, tuple, range) ``paginate`` is O(1) to find the
total and O(page_size) to slice. For any other iterable the data has to be consumed
once, so it is O(n) time and O(n) memory.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Iterable, Sequence
from typing import TypeVar

from app.sla_operations.schemas.pagination import (
    DEFAULT_PAGE,
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    CursorPage,
    CursorParams,
    PageMeta,
    PaginatedResult,
    PaginationParams,
)

T = TypeVar("T")

_CURSOR_PREFIX = "offset:"


class InvalidCursorError(ValueError):
    """Raised when a cursor was not produced by this service or was tampered with."""


def _as_int(value: object) -> int | None:
    """Return ``value`` if it is a real integer; ``bool`` is rejected on purpose."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def normalize_pagination(page: object = None, page_size: object = None) -> PaginationParams:
    """Build safe ``PaginationParams`` from untrusted values without raising.

    ``page`` below 1 becomes 1; ``page_size`` is clamped to ``1..MAX_PAGE_SIZE``.
    Missing or non-integer values fall back to ``DEFAULT_PAGE``/``DEFAULT_PAGE_SIZE``.
    """
    safe_page = _as_int(page)
    safe_size = _as_int(page_size)
    return PaginationParams(
        page=DEFAULT_PAGE if safe_page is None else max(safe_page, 1),
        page_size=DEFAULT_PAGE_SIZE if safe_size is None else min(max(safe_size, 1), MAX_PAGE_SIZE),
    )


def total_pages(total: int, page_size: int = DEFAULT_PAGE_SIZE) -> int:
    """Return how many pages ``total`` items need (0 when there are no items)."""
    if total <= 0:
        return 0
    size = min(max(_as_int(page_size) or DEFAULT_PAGE_SIZE, 1), MAX_PAGE_SIZE)
    return -(-total // size)  # ceiling division without floats


def _as_sequence(items: Iterable[T] | None) -> Sequence[T]:
    """Return ``items`` as a sequence, treating missing or invalid input as empty."""
    if items is None or isinstance(items, (str, bytes, bytearray)):
        return ()
    if isinstance(items, Sequence):
        return items
    try:
        return list(items)
    except TypeError:
        return ()


def paginate(items: Iterable[T] | None, params: PaginationParams | None = None) -> PaginatedResult[T]:
    """Return one page of ``items`` plus metadata that always passes ``PageMeta`` validation."""
    params = params or PaginationParams()
    data = _as_sequence(items)
    total = len(data)
    page_items = list(data[params.offset : params.offset + params.page_size])
    meta = PageMeta(
        page=params.page,
        page_size=params.page_size,
        total=total,
        returned=len(page_items),
        has_more=params.page * params.page_size < total,
    )
    return PaginatedResult(items=page_items, meta=meta)


def slice_page(items: Iterable[T] | None, page: object = None, page_size: object = None) -> list[T]:
    """Return only the items of one page, using lenient ``normalize_pagination`` rules."""
    return paginate(items, normalize_pagination(page, page_size)).items


def encode_cursor(offset: int) -> str:
    """Encode a non-negative offset as an opaque, URL-safe cursor."""
    if _as_int(offset) is None or offset < 0:
        raise ValueError("offset must be a non-negative integer")
    return base64.urlsafe_b64encode(f"{_CURSOR_PREFIX}{offset}".encode("ascii")).decode("ascii")


def decode_cursor(cursor: str) -> int:
    """Decode a cursor made by ``encode_cursor``; raise ``InvalidCursorError`` otherwise."""
    if not isinstance(cursor, str) or not cursor:
        raise InvalidCursorError("cursor must be a non-empty string")
    try:
        text = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("ascii")
    except (binascii.Error, UnicodeError) as exc:
        raise InvalidCursorError("cursor is not valid") from exc
    digits = text[len(_CURSOR_PREFIX) :]
    if not text.startswith(_CURSOR_PREFIX) or not digits.isascii() or not digits.isdigit():
        raise InvalidCursorError("cursor is not valid")
    return int(digits)


def paginate_cursor(items: Iterable[T] | None, params: CursorParams | None = None) -> CursorPage[T]:
    """Return a cursor page; ``next_cursor`` is set exactly when more items remain."""
    params = params or CursorParams()
    data = _as_sequence(items)
    start = 0 if params.cursor is None else decode_cursor(params.cursor)
    end = start + params.limit
    page_items = list(data[start:end])
    has_more = end < len(data)
    return CursorPage(items=page_items, next_cursor=encode_cursor(end) if has_more else None, has_more=has_more)


__all__ = [
    "InvalidCursorError",
    "decode_cursor",
    "encode_cursor",
    "normalize_pagination",
    "paginate",
    "paginate_cursor",
    "slice_page",
    "total_pages",
]
