from collections.abc import Callable

from fastapi import HTTPException, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import settings
from app.utils.logging import get_structured_logger

logger = get_structured_logger("payload_size_middleware")


class PayloadSizeMiddleware(BaseHTTPMiddleware):
    """Middleware to enforce payload size limits on incoming requests."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Skip size checking for non-body requests (GET, HEAD, etc.)
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return await call_next(request)

        # Check Content-Length header if present
        content_length = request.headers.get("Content-Length")
        if content_length:
            try:
                content_length_int = int(content_length)
                if content_length_int > settings.MAX_REQUEST_BODY_SIZE_BYTES:
                    logger.warning(
                        "Request body too large",
                        content_length=content_length_int,
                        max_allowed=settings.MAX_REQUEST_BODY_SIZE_BYTES,
                        path=request.url.path,
                        method=request.method
                    )
                    raise HTTPException(
                        status_code=413,
                        detail=f"Request body too large. Maximum allowed size is {settings.MAX_REQUEST_BODY_SIZE_BYTES} bytes."
                    )
            except ValueError:
                # Invalid Content-Length header, let it pass through
                pass

        # For safety, we'll also check the actual body size as we read it
        # This is a backup in case Content-Length is missing or incorrect
        original_receive = request._receive

        total_body_size = 0

        async def size_limited_receive():
            nonlocal total_body_size
            message = await original_receive()
            body = message.get("body", b"")
            total_body_size += len(body)
            if total_body_size > settings.MAX_REQUEST_BODY_SIZE_BYTES:
                logger.warning(
                    "Request body size exceeded during read",
                    body_size=total_body_size,
                    max_allowed=settings.MAX_REQUEST_BODY_SIZE_BYTES,
                    path=request.url.path,
                    method=request.method
                )
                raise HTTPException(
                    status_code=413,
                    detail=f"Request body too large. Maximum allowed size is {settings.MAX_REQUEST_BODY_SIZE_BYTES} bytes."
                )
            return message

        request._receive = size_limited_receive

        return await call_next(request)
