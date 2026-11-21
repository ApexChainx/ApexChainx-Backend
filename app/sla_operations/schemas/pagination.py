"""Reusable pagination, sorting, and filtering schemas for SLA workspace services."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DEFAULT_PAGE = 1
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100
MAX_FILTER_VALUES = 50
DEFAULT_SORT_FIELD = "created_at"

T = TypeVar("T")


class SortDirection(str, Enum):
    asc = "asc"
    desc = "desc"


class PaginationParams(BaseModel):
    """Offset pagination inputs; the SQL offset is derived from the page."""

    page: int = Field(default=DEFAULT_PAGE, ge=1, description="1-based page number")
    page_size: int = Field(
        default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE, description="Maximum items returned per page"
    )

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


class CursorParams(BaseModel):
    """Cursor pagination inputs for services that cannot cheaply count rows."""

    cursor: str | None = Field(default=None, min_length=1, description="Opaque cursor from a previous page")
    limit: int = Field(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE, description="Maximum items returned")


class SortParams(BaseModel):
    """Sorting inputs; services must whitelist ``sort_by`` before querying."""

    sort_by: str = Field(
        default=DEFAULT_SORT_FIELD,
        min_length=1,
        pattern=r"^[A-Za-z_][A-Za-z0-9_]*$",
        description="Field name in snake_case; whitelisted by each service",
    )
    sort_dir: SortDirection = Field(default=SortDirection.desc)


class DateRangeFilter(BaseModel):
    """Inclusive time bounds shared by workspace list filters."""

    date_from: datetime | None = None
    date_to: datetime | None = None

    @field_validator("date_from", "date_to")
    @classmethod
    def normalize_bound(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("date filters must be timezone-aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_range(self) -> DateRangeFilter:
        if self.date_from is not None and self.date_to is not None and self.date_from > self.date_to:
            raise ValueError("date_from cannot be after date_to")
        return self


class FilterParams(DateRangeFilter):
    """Generic equality/text filters layered on a date range."""

    search: str | None = Field(
        default=None, max_length=200, description="Free-text term; searched fields are service-specific"
    )
    status: str | None = Field(default=None, min_length=1, max_length=64)
    record_ids: list[str] = Field(
        default_factory=list, max_length=MAX_FILTER_VALUES, description="Exact record identifiers to include"
    )

    @field_validator("search")
    @classmethod
    def search_must_not_be_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("search cannot be blank")
        return value

    @field_validator("record_ids")
    @classmethod
    def record_ids_must_not_be_blank(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("record_ids cannot contain blank values")
        return values


class ListQueryParams(PaginationParams, SortParams, FilterParams):
    """Combined request model for paginated, sorted, and filtered lists."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "page": 1,
                "page_size": 20,
                "sort_by": "detected_at",
                "sort_dir": "desc",
                "status": "open",
                "search": "site-a",
            }
        }
    )


class PageMeta(BaseModel):
    """Response metadata that must be internally consistent with its page."""

    page: int = Field(..., ge=1)
    page_size: int = Field(..., ge=1, le=MAX_PAGE_SIZE)
    total: int = Field(..., ge=0)
    returned: int = Field(..., ge=0)
    has_more: bool

    @model_validator(mode="after")
    def validate_consistency(self) -> PageMeta:
        expected_returned = min(self.page_size, max(self.total - self.offset, 0))
        if self.returned != expected_returned:
            raise ValueError("returned must match total, page, and page_size")
        if self.has_more != (self.page * self.page_size < self.total):
            raise ValueError("has_more is inconsistent with page, page_size, and total")
        return self


class PaginatedResult(BaseModel, Generic[T]):
    """Generic page envelope for workspace service responses."""

    model_config = ConfigDict(from_attributes=True)

    items: list[T]
    meta: PageMeta


class CursorPage(BaseModel, Generic[T]):
    """Generic cursor page; ``next_cursor`` is required when more rows exist."""

    items: list[T]
    next_cursor: str | None = None
    has_more: bool = False

    @model_validator(mode="after")
    def validate_cursor(self) -> CursorPage:
        if self.has_more and not self.next_cursor:
            raise ValueError("next_cursor is required when has_more is true")
        return self
