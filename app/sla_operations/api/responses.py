"""Typed response envelope utilities for SLA operations endpoints."""

from typing import Generic, TypeVar

from pydantic import BaseModel, Field

DataT = TypeVar("DataT")


class ResponseEnvelope(BaseModel, Generic[DataT]):
    """Consistent success response shape for workspace endpoints."""

    success: bool = True
    message: str = Field(default="success", min_length=1)
    data: DataT


def success_response(data: DataT, message: str = "success") -> ResponseEnvelope[DataT]:
    """Build a validated success envelope around endpoint data."""
    return ResponseEnvelope[DataT](data=data, message=message)
