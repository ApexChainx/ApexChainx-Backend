"""Query params for GET /audit, replacing an unbounded full-table response.

The limit bound is an alias of the shared pagination cap (#564) so the audit
list cannot drift from the caps enforced on every other list endpoint. The
endpoint consumes these bounds directly via ``Query`` (see
``app/api/v1/endpoints/audit.py``), mirroring ``AuditListParams``.
"""

from typing import Optional

from pydantic import BaseModel, Field

from app.schemas.pagination import MAX_PAGE_SIZE as _SHARED_MAX_PAGE_SIZE

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = _SHARED_MAX_PAGE_SIZE


class AuditListParams(BaseModel):
    cursor: Optional[str] = None
    limit: int = Field(default=DEFAULT_PAGE_SIZE, le=MAX_PAGE_SIZE, gt=0)
