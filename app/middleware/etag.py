"""ETag / If-None-Match middleware for GET endpoints.

Generates an ETag from the response body so clients can send
``If-None-Match`` headers on subsequent requests.  Returns 304
Not Modified when the content has not changed, saving bandwidth.

ETag algorithm: SHA-256 of the response body, hex-encoded, wrapped in
double-quotes (strong validator per RFC 7232 §2.3).
"""

import hashlib
from collections.abc import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from app.utils.logging import get_structured_logger

logger = get_structured_logger("etag_middleware")


def _compute_etag(body: bytes) -> str:
    return f'"{hashlib.sha256(body).hexdigest()}"'


class ETagMiddleware(BaseHTTPMiddleware):
    """Add ETag response headers and honour If-None-Match on GET/HEAD requests.

    Only applies to 2xx responses for GET and HEAD methods.
    Responses that already carry an ETag are left untouched.
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)

        # Only process 2xx GET/HEAD responses
        if request.method not in ("GET", "HEAD"):
            return response
        if response.status_code < 200 or response.status_code >= 300:
            return response

        # If the downstream handler already set an ETag, respect it
        if "etag" in response.headers:
            return response

        # Read the body and compute ETag
        body = b""
        async for chunk in response.body_iterator:
            body += chunk
        etag = _compute_etag(body)

        response.headers["ETag"] = etag

        # Honour If-None-Match
        if_none_match = request.headers.get("If-None-Match")
        if if_none_match and if_none_match == etag:
            logger.info(
                "ETag match, returning 304",
                path=request.url.path,
                etag=etag,
            )
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
