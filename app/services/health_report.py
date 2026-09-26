"""Expanded health/readiness probe for issue #32."""

from __future__ import annotations

import time
from dataclasses import dataclass

from redis import Redis
from sqlalchemy import text
from sqlalchemy.engine import Engine


@dataclass
class ComponentStatus:
    component: str
    status: str  # ok, warn, down
    details: dict


def _pool_status(engine: Engine) -> ComponentStatus:
    try:
        pool = engine.pool
        size = pool.size()
        checked_out = pool.checkedout()
        overflow = pool.overflow()
        checked_in = pool.checkedin()
        warn = checked_out > size * 0.9
        return ComponentStatus(
            component="postgres_pool",
            status="warn" if warn else "ok",
            details={
                "pool_size": size,
                "checked_out": checked_out,
                "overflow": overflow,
                "checkedin": checked_in,
            },
        )
    except Exception as exc:
        return ComponentStatus("postgres_pool", "down", {"error": str(exc)})


def _db_ping(engine: Engine) -> ComponentStatus:
    start = time.monotonic()
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        elapsed_ms = round((time.monotonic() - start) * 1000, 2)
        return ComponentStatus("database", "ok", {"latency_ms": elapsed_ms})
    except Exception as exc:
        return ComponentStatus("database", "down", {"error": str(exc)})


def _redis_ping(redis_url: str, component: str = "redis") -> ComponentStatus:
    start = time.monotonic()
    client = None
    try:
        client = Redis.from_url(redis_url)
        client.ping()
        elapsed_ms = round((time.monotonic() - start) * 1000, 2)
        return ComponentStatus(component, "ok", {"latency_ms": elapsed_ms})
    except Exception as exc:
        return ComponentStatus(component, "down", {"error": str(exc)})
    finally:
        if client is not None:
            client.close()


def _dlq_depth(engine: Engine, warn_threshold: int = 1000) -> ComponentStatus:
    try:
        with engine.connect() as conn:
            # Bounded query: stop counting once we pass the warn threshold so a
            # large DLQ does not add full-table-scan load on every probe.
            result = conn.execute(
                text(
                    "SELECT COUNT(*) FROM ("
                    "SELECT 1 FROM webhook_deliveries WHERE status = 'DEAD_LETTER' LIMIT :cap"
                    ") t"
                ),
                {"cap": warn_threshold + 1},
            )
            count = result.scalar() or 0
        warn = count > warn_threshold
        return ComponentStatus(
            "webhook_dlq",
            status="warn" if warn else "ok",
            details={"dead_letter_count": count, "warn_threshold": warn_threshold},
        )
    except Exception as exc:
        return ComponentStatus("webhook_dlq", "down", {"error": str(exc)})


def _audit_db_ping(engine: Engine) -> ComponentStatus:
    """Probe the audit database. Failures degrade (warn) rather than deregister the service."""
    start = time.monotonic()
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        elapsed_ms = round((time.monotonic() - start) * 1000, 2)
        return ComponentStatus("audit_database", "ok", {"latency_ms": elapsed_ms})
    except Exception as exc:
        return ComponentStatus("audit_database", "warn", {"error": str(exc)})


def _revocation_store_ping(redis_url: str) -> ComponentStatus:
    """Probe the token revocation store. Failures degrade (warn) rather than deregister the service."""
    status = _redis_ping(redis_url, component="revocation_store")
    if status.status == "down":
        return ComponentStatus(status.component, "warn", status.details)
    return status


def build_readiness_report(engine: Engine, audit_engine: Engine, redis_url: str, dlq_warn_threshold: int = 1000) -> dict:
    """Build a structured readiness report for /health/readiness.

    Policy: main database/redis failures are "down"; audit-database and
    revocation-store failures are "warn" so partial availability during an
    incident does not deregister the service from the load balancer.
    """
    components = [
        _db_ping(engine),
        _pool_status(engine),
        _redis_ping(redis_url),
        _dlq_depth(engine, dlq_warn_threshold),
        _audit_db_ping(audit_engine),
        _revocation_store_ping(redis_url),
    ]

    overall = "ok"
    for c in components:
        if c.status == "down":
            overall = "down"
            break
        if c.status == "warn":
            overall = "warn"

    return {
        "status": overall,
        "components": {c.component: {"status": c.status, **c.details} for c in components},
    }
