"""Explicit per-route admin dependency for webhook routes, so the
guarantee doesn't rely solely on the router-level dependency surviving
a future refactor.
"""
from fastapi import Depends


def require_admin_explicit(require_admin_dependency):
    """Wrap require_admin so every route declares it in its own signature."""
    return Depends(require_admin_dependency)


WEBHOOK_ADMIN_MARKER = "webhooks:admin-required"
