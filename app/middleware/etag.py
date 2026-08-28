"""ETag / If-None-Match middleware for GET endpoints.
...
"""

import hashlib
from collections.abc import Callable, Iterable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from app.utils.logging import get_structured_logger

logger = get_structured_logger("etag_middleware")


def _compute_etag(body: bytes) -> str:
    return f'"{hashlib.sha256(body).hexdigest()}"'


class ETagMiddleware(BaseHTTPMiddleware):
    """Add ETag response headers and honour If-None-Match on GET/HEAD requests.

    Only applies to 2xx responses for GET and HEAD methods, and skips any
    path starting with one of `exclude_path_prefixes` — this middleware
    buffers the full response body in memory to hash it, which is unsafe
    for large/streamed responses (e.g. CSV/JSON exports). Streaming-safe
    ETag support is tracked separately.
    """

    def __init__(self, app: ASGIApp, exclude_path_prefixes: Iterable[str] = ()) -> None:
        super().__init__(app)
        self._exclude_path_prefixes = tuple(exclude_path_prefixes)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)

        if request.method not in ("GET", "HEAD"):
            return response
        if response.status_code < 200 or response.status_code >= 300:
            return response
        if any(request.url.path.startswith(p) for p in self._exclude_path_prefixes):
            return response
        if "etag" in response.headers:
            return response

        body = b""
        async for chunk in response.body_iterator:
            body += chunk
        etag = _compute_etag(body)

        response.headers["ETag"] = etag

        if_none_match = request.headers.get("If-None-Match")
        if if_none_match:
            candidates = {tok.strip() for tok in if_none_match.split(",")}
            if "*" in candidates or etag in candidates:
                logger.info("ETag match, returning 304", path=request.url.path, etag=etag)
                return Response(
                    status_code=304,
                    headers={
                        "ETag": etag,
                        "X-Correlation-ID": response.headers.get("X-Correlation-ID", ""),
                    },
                )

        return Response(
            status_code=response.status_code,
            content=body,
            media_type=response.media_type,
            headers=dict(response.headers),
        )