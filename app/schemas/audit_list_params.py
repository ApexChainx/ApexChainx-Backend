"""Shared list-pagination query bounds (#564).

List endpoints used to carry ad-hoc, inconsistent caps — webhook lists capped
at ``le=100``, deliveries at ``le=200``, and several others uncapped — so
pagination behavior depended on which resource you were paging. Every list
endpoint now declares its page-size constraint from the constants in this
module, and values above :data:`MAX_PAGE_SIZE` are rejected with 422 by the
query-param validation itself.

``AuditListParams`` keeps its historical name (it was born for the audit
cursor list) but the constants are the shared source of truth.
"""
from typing import Optional

from pydantic import BaseModel, Field

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


class AuditListParams(BaseModel):
    cursor: Optional[str] = None
    limit: int = Field(default=DEFAULT_PAGE_SIZE, le=MAX_PAGE_SIZE, gt=0)
