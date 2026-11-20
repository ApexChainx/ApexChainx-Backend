"""Validated parsing helpers for common SLA operations request parameters."""

from datetime import date

from pydantic import BaseModel, Field, model_validator


class PaginationParams(BaseModel):
    """Validated page-based pagination parameters."""

    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=100)

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


class DateRangeParams(BaseModel):
    """Optional inclusive date range with chronological validation."""

    start_date: date | None = None
    end_date: date | None = None

    @model_validator(mode="after")
    def validate_order(self) -> "DateRangeParams":
        if self.start_date is not None and self.end_date is not None and self.start_date > self.end_date:
            raise ValueError("start_date must be on or before end_date")
        return self


def parse_pagination(page: int = 1, page_size: int = 50) -> PaginationParams:
    """Validate pagination values and calculate the corresponding offset."""
    return PaginationParams(page=page, page_size=page_size)


def parse_date_range(
    start_date: date | None = None,
    end_date: date | None = None,
) -> DateRangeParams:
    """Validate an optional inclusive date range."""
    return DateRangeParams(start_date=start_date, end_date=end_date)
