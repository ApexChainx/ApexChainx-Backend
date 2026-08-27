from starlette.requests import Request
from starlette.responses import JSONResponse

from app.core.config import settings
from app.utils.logging import get_structured_logger

logger = get_structured_logger("payload_size_middleware")


class _PayloadTooLarge(Exception):
    pass


class PayloadSizeMiddleware:
    """ASGI-native middleware to enforce payload size limits on incoming requests."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)

        if request.method in ("GET", "HEAD", "OPTIONS"):
            await self.app(scope, receive, send)
            return

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
                        method=request.method,
                    )
                    raise _PayloadTooLarge
            except ValueError:
                pass

        original_receive = receive
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
                    method=request.method,
                )
                raise _PayloadTooLarge
            return message

        try:
            await self.app(scope, size_limited_receive, send)
        except _PayloadTooLarge:
            response = JSONResponse(
                status_code=413,
                content={
                    "type": "about:blank",
                    "title": "Payload Too Large",
                    "status": 413,
                    "detail": f"Request body too large. Maximum allowed size is {settings.MAX_REQUEST_BODY_SIZE_BYTES} bytes.",
                },
                media_type="application/problem+json",
            )
            await response(scope, receive, send)
