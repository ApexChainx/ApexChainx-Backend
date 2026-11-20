"""Aggregate router for API modules owned by the SLA operations workspace."""

from collections.abc import Sequence

from fastapi import APIRouter


def create_workspace_router(
    *subrouters: APIRouter,
    prefix: str = "",
    tags: Sequence[str] | None = None,
) -> APIRouter:
    """Compose workspace subrouters without changing the core application router."""
    aggregate = APIRouter(prefix=prefix, tags=list(tags) if tags is not None else None)
    for subrouter in subrouters:
        aggregate.include_router(subrouter)
    return aggregate


router = create_workspace_router()
