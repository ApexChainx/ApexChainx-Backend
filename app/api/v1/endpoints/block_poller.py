"""OpenAPI-documented status endpoint for the Stellar block poller (#726).

The poller itself runs in Celery, not in the request path, but operators need
a documented way to see whether it is advancing. This router exposes the
cached last-poll summary and is included in the generated Swagger spec.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.security import require_engineer
from app.services.block_poller import BlockPollerCache, build_block_poller_cache

router = APIRouter(prefix="/block-poller", tags=["block-poller"])


class BlockPollerStatus(BaseModel):
    """Last observed BlockPoller state.

    Values come from Redis so the endpoint stays cheap and can be scraped
    without touching Horizon or PostgreSQL.
    """

    enabled: bool = Field(
        ...,
        description="Whether BLOCK_POLLER_ENABLED is true for this deployment.",
        json_schema_extra={"example": True},
    )
    cursor: str | None = Field(
        default=None,
        description="Last committed Horizon paging token.",
        json_schema_extra={"example": "1234567890"},
    )
    last_poll_at: str | None = Field(
        default=None,
        description="UTC timestamp of the last completed poll cycle.",
        json_schema_extra={"example": "2026-10-20T15:30:00+00:00"},
    )
    last_ledger_sequence: int | None = Field(
        default=None,
        description="Highest Stellar ledger sequence processed by the last poll.",
        json_schema_extra={"example": 51234567},
    )
    ledgers_processed: int = Field(
        default=0, description="Ledgers processed in the last poll cycle.", json_schema_extra={"example": 3}
    )
    transactions_scanned: int = Field(
        default=0,
        description="Horizon transactions fetched in the last poll cycle.",
        json_schema_extra={"example": 42},
    )
    payments_confirmed: int = Field(
        default=0,
        description="Pending payments confirmed in the last poll cycle.",
        json_schema_extra={"example": 2},
    )
    cache_hits: int = Field(
        default=0,
        description="Ledgers served from the Redis transaction cache in the last poll.",
        json_schema_extra={"example": 1},
    )
    cache_misses: int = Field(
        default=0,
        description="Ledgers fetched from Horizon because the Redis cache missed.",
        json_schema_extra={"example": 2},
    )
    error: str | None = Field(
        default=None,
        description="Error from the last poll cycle, if it failed.",
        json_schema_extra={"example": None},
    )


def get_block_poller_cache() -> BlockPollerCache:
    return build_block_poller_cache()


def _parse_poll_time(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return None
    return value


def _as_int(value: object) -> int:
    try:
        return int(value)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return 0


@router.get(
    "/status",
    response_model=BlockPollerStatus,
    summary="Get BlockPoller status",
    description=(
        "Return the last BlockPoller poll summary from Redis.\n\n"
        "The poller tails Stellar Horizon ledgers on the Celery beat schedule "
        "and confirms pending payment transactions whose hash closed "
        "successfully. This endpoint does not contact Horizon or PostgreSQL; "
        "it reports the most recent checkpoint."
    ),
    responses={
        401: {"description": "Missing or invalid bearer token."},
        403: {"description": "Caller does not have the engineer role."},
    },
)
def get_block_poller_status(
    current_user=Depends(require_engineer),
    cache: BlockPollerCache = Depends(get_block_poller_cache),
) -> BlockPollerStatus:
    payload = cache.get_last_poll() or {}
    return BlockPollerStatus(
        enabled=settings.BLOCK_POLLER_ENABLED,
        cursor=payload.get("cursor"),
        last_poll_at=_parse_poll_time(payload.get("polled_at")),
        last_ledger_sequence=_as_int(payload.get("last_ledger_sequence")) or None,
        ledgers_processed=_as_int(payload.get("ledgers_processed")),
        transactions_scanned=_as_int(payload.get("transactions_scanned")),
        payments_confirmed=_as_int(payload.get("payments_confirmed")),
        cache_hits=_as_int(payload.get("cache_hits")),
        cache_misses=_as_int(payload.get("cache_misses")),
        error=payload.get("error"),
    )
