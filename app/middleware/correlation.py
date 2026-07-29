import hashlib
import time
from typing import Callable
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.utils.correlation_ctx import get_or_generate_correlation_id, set_correlation_id
from app.utils.logging import get_structured_logger

logger = get_structured_logger("access")


def _hash_value(value: str | None) -> str | None:
    if value is None:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


class CorrelationMiddleware(BaseHTTPMiddleware):
    """Middleware to add correlation IDs to requests and enable request tracing.

    Emits structured access logs with:
    - trace_id (correlation ID)
    - method (HTTP method)
    - route_template (URL path template)
    - status (HTTP response status)
    - duration_ms (request duration)
    - query_hash (SHA-256 prefix of query string)
    - user_id_hash (SHA-256 prefix of user identifier, if authenticated)
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        correlation_id = request.headers.get("X-Correlation-ID") or get_or_generate_correlation_id()
        set_correlation_id(correlation_id)
        request.state.correlation_id = correlation_id

        start_time = time.time()

        query_hash = _hash_value(str(request.query_params)) if request.query_params else None

        route_template: str | None = None
        user_id_hash: str | None = None

        try:
            response = await call_next(request)

            duration_ms = (time.time() - start_time) * 1000

            response.headers["X-Correlation-ID"] = correlation_id

            if request.scope.get("route"):
                route_template = getattr(request.scope["route"], "path", None)

            try:
                if hasattr(request.state, "user") and request.state.user:
                    uid = str(getattr(request.state.user, "id", ""))
                    if uid:
                        user_id_hash = _hash_value(uid)
            except Exception:
                pass

            logger.info(
                "Request completed",
                trace_id=correlation_id,
                method=request.method,
                route_template=route_template,
                status=response.status_code,
                duration_ms=round(duration_ms, 2),
                query_hash=query_hash,
                user_id_hash=user_id_hash,
            )

            return response

        except Exception as exc:
            duration_ms = (time.time() - start_time) * 1000

            logger.error(
                "Request failed",
                trace_id=correlation_id,
                method=request.method,
                route_template=route_template,
                error=str(exc),
                duration_ms=round(duration_ms, 2),
                query_hash=query_hash,
                user_id_hash=user_id_hash,
            )

            raise
