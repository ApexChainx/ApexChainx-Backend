"""Shared pagination limits for every list endpoint (issue #564).

List endpoints grew their own pagination knobs over time: webhook deliveries
capped at 200, webhooks at 100, payments/outages/jobs at 100 with differing
defaults — and the SLA dispute list and the audit log were not capped at all,
so an authenticated client could request a full-table dump.

One module now owns the contract so the caps cannot drift apart again:

- ``DEFAULT_PAGE_SIZE`` — page size / limit used when the caller omits it.
- ``MAX_PAGE_SIZE`` — hard cap; FastAPI answers 422 for anything above it.

``AuditListParams`` and the ``PaginatedPayments`` envelope are aliases of
these constants, so changing the cap here changes it everywhere.
"""

from __future__ import annotations

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 200
