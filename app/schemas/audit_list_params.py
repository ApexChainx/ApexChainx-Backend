"""Cursor pagination query params for GET /audit, replacing an
unbounded full-table response.
"""
from typing import Optional

from pydantic import BaseModel, Field

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


class AuditListParams(BaseModel):
    cursor: Optional[str] = None
    limit: int = Field(default=DEFAULT_PAGE_SIZE, le=MAX_PAGE_SIZE, gt=0)
