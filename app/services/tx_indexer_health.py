"""Shared health state and probe report for :mod:`app.services.tx_indexer` (#714).

The ingest loop runs in a worker process and the probe is served by the API
process, so the state has to live outside either process. It is one small JSON
document in Redis:

    tx_indexer:state -> {"last_attempt_at": ..., "last_success_at": ...,
                         "consecutive_failures": 0, "last_error": null,
                         "last_inserted": 42, "cursor": "..."}

Redis failures are **fail-open**: an unreadable heartbeat is reported as "no
run recorded" (degraded), never as "down". The database probe is the only
signal allowed to take the component out of rotation, mirroring the
main/audit-database policy in ``app/services/health_report.py``.

Status semantics
----------------
``ok``        a successful pass within ``TX_INDEXER_HEALTH_MAX_LAG_SECONDS``
``degraded``  the last pass failed, the heartbeat is stale, or no pass has been
              recorded yet (a new deployment, or Redis lost the key)
``down``      the ledger database is unreachable, so indexer writes cannot land
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from redis import Redis
from redis.exceptions import RedisError

from app.core.config import settings

logger = logging.getLogger(__name__)

STATE_KEY = "tx_indexer:state"
#: Errors are truncated before they reach an unauthenticated probe response.
MAX_ERROR_LENGTH = 240


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _parse_timestamp(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


@dataclass(frozen=True)
class IndexerState:
    """Snapshot of the last ingest attempt as published by the worker."""

    last_attempt_at: datetime | None = None
    last_success_at: datetime | None = None
    last_error: str | None = None
    consecutive_failures: int = 0
    last_inserted: int = 0
    cursor: str | None = None

    def record_success(self, *, cursor: str | None, inserted: int, at: datetime | None = None) -> IndexerState:
        """Return the state after a pass that indexed *inserted* rows."""
        moment = at or _utcnow()
        return replace(
            self,
            last_attempt_at=moment,
            last_success_at=moment,
            last_error=None,
            consecutive_failures=0,
            last_inserted=inserted,
            cursor=cursor,
        )

    def record_failure(self, *, error: str, at: datetime | None = None) -> IndexerState:
        """Return the state after a failed pass, preserving the last success."""
        return replace(
            self,
            last_attempt_at=at or _utcnow(),
            last_error=str(error)[:MAX_ERROR_LENGTH],
            consecutive_failures=self.consecutive_failures + 1,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "last_attempt_at": _iso(self.last_attempt_at),
            "last_success_at": _iso(self.last_success_at),
            "last_error": self.last_error,
            "consecutive_failures": self.consecutive_failures,
            "last_inserted": self.last_inserted,
            "cursor": self.cursor,
        }

    @classmethod
    def from_dict(cls, payload: object) -> IndexerState:
        if not isinstance(payload, dict):
            return cls()
        failures = payload.get("consecutive_failures", 0)
        inserted = payload.get("last_inserted", 0)
        return cls(
            last_attempt_at=_parse_timestamp(payload.get("last_attempt_at")),
            last_success_at=_parse_timestamp(payload.get("last_success_at")),
            last_error=payload.get("last_error") if isinstance(payload.get("last_error"), str) else None,
            consecutive_failures=failures if isinstance(failures, int) and failures >= 0 else 0,
            last_inserted=inserted if isinstance(inserted, int) and inserted >= 0 else 0,
            cursor=payload.get("cursor") if isinstance(payload.get("cursor"), str) else None,
        )


class TxIndexerStateStore:
    """Redis-backed heartbeat shared by the worker and the API process."""

    def __init__(
        self,
        redis_client: Redis | None = None,
        key: str = STATE_KEY,
        ttl_seconds: int | None = None,
    ) -> None:
        self._redis = redis_client
        self._key = key
        self._ttl = ttl_seconds or settings.TX_INDEXER_STATE_TTL_SECONDS

    @property
    def _client(self) -> Redis:
        if self._redis is None:
            self._redis = Redis.from_url(settings.REDIS_URL, decode_responses=True)
        return self._redis

    def load(self) -> IndexerState:
        try:
            raw = self._client.get(self._key)
        except RedisError as exc:
            logger.warning(
                "tx_indexer_state_read_failed", extra={"event": "tx_indexer_state_read_failed", "error": str(exc)}
            )
            return IndexerState()
        if not raw:
            return IndexerState()
        try:
            return IndexerState.from_dict(json.loads(raw))
        except (TypeError, ValueError):
            logger.warning("tx_indexer_state_corrupt", extra={"event": "tx_indexer_state_corrupt"})
            return IndexerState()

    def save(self, state: IndexerState) -> None:
        try:
            self._client.set(self._key, json.dumps(state.to_dict()), ex=self._ttl)
        except RedisError as exc:
            # Losing the heartbeat must not fail an ingest pass that already
            # committed its rows; the next pass republishes it.
            logger.warning(
                "tx_indexer_state_write_failed", extra={"event": "tx_indexer_state_write_failed", "error": str(exc)}
            )

    def record_success(self, *, cursor: str | None, inserted: int, at: datetime | None = None) -> None:
        self.save(self.load().record_success(cursor=cursor, inserted=inserted, at=at))

    def record_failure(self, *, error: str, at: datetime | None = None) -> None:
        self.save(self.load().record_failure(error=error, at=at))


def build_indexer_health(
    state: IndexerState,
    *,
    now: datetime | None = None,
    max_lag_seconds: int | None = None,
    database_ok: bool = True,
    database_error: str | None = None,
) -> dict[str, Any]:
    """Assemble the probe body shared by the endpoint and its tests."""
    moment = now or _utcnow()
    lag_seconds: float | None = None
    if state.last_success_at is not None:
        lag_seconds = round(max((moment - state.last_success_at).total_seconds(), 0.0), 2)

    threshold = max_lag_seconds if max_lag_seconds is not None else settings.TX_INDEXER_HEALTH_MAX_LAG_SECONDS
    components: dict[str, dict] = {
        "database": {
            "status": "ok" if database_ok else "down",
            "details": {"error": database_error} if database_error else {},
        },
        "state_store": {
            "status": "ok" if state.last_attempt_at is not None else "empty",
            "details": {},
        },
    }

    reason: str | None
    if not database_ok:
        status, reason = "down", "database_unreachable"
    elif state.last_success_at is None:
        status, reason = "degraded", "no_successful_run"
    elif state.consecutive_failures > 0:
        status, reason = "degraded", "last_run_failed"
    elif lag_seconds is not None and lag_seconds > threshold:
        status, reason = "degraded", "stale"
    else:
        status, reason = "ok", None

    return {
        "status": status,
        "reason": reason,
        "observed_at": _iso(moment),
        "last_attempt_at": _iso(state.last_attempt_at),
        "last_success_at": _iso(state.last_success_at),
        "lag_seconds": lag_seconds,
        "max_lag_seconds": threshold,
        "consecutive_failures": state.consecutive_failures,
        "last_error": state.last_error,
        "last_inserted": state.last_inserted,
        "components": components,
    }
