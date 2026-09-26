import json

from app.core.config import settings
from app.utils.correlation_ctx import get_or_generate_correlation_id
from app.utils.logging import get_structured_logger

logger = get_structured_logger("payload_size_middleware")


class _PayloadTooLarge(Exception):
    """Internal signal only — never propagated past this middleware."""


def _problem_413_body(correlation_id: str) -> bytes:
    limit = settings.MAX_REQUEST_BODY_SIZE_BYTES
    return json.dumps(
        {
            "type": "https://developer.apexchainx.io/errors/413",
            "title": "Payload Too Large",
            "status": 413,
            "detail": f"Request body too large. Maximum allowed size is {limit} bytes.",
            "correlation_id": correlation_id,
            "error_code": "payload_too_large",
        }
    ).encode("utf-8")


class PayloadSizeMiddleware:
    """ASGI-native middleware enforcing MAX_REQUEST_BODY_SIZE_BYTES.

    Sends the 413 problem+json response directly over the raw ASGI `send`
    channel rather than raising HTTPException. This middleware runs outside
    Starlette's ExceptionMiddleware (as does all app.add_middleware(...)
    middleware), so a raised HTTPException here is not reliably converted
    into the documented RFC 7807 response.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "")
        if method in ("GET", "HEAD", "OPTIONS"):
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        limit = settings.MAX_REQUEST_BODY_SIZE_BYTES
        headers = dict(scope.get("headers") or [])

        # Fast path: reject on Content-Length before touching the body at all.
        content_length = headers.get(b"content-length")
        if content_length:
            try:
                content_length_int = int(content_length)
            except ValueError:
                content_length_int = None
            if content_length_int is not None and content_length_int > limit:
                logger.warning(
                    "Request body too large",
                    content_length=content_length_int,
                    max_allowed=limit,
                    path=path,
                    method=method,
                )
                await self._send_413(send)
                return

        # Slow path: no (or lying) Content-Length — enforce as bytes stream in.
        total = 0

        async def size_limited_receive():
            nonlocal total
            message = await receive()
            total += len(message.get("body", b""))
            if total > limit:
                logger.warning(
                    "Request body size exceeded during read",
                    body_size=total,
                    max_allowed=limit,
                    path=path,
                    method=method,
                )
                raise _PayloadTooLarge()
            return message

        try:
            await self.app(scope, size_limited_receive, send)
        except _PayloadTooLarge:
            await self._send_413(send)

    @staticmethod
    async def _send_413(send):
        correlation_id = get_or_generate_correlation_id()
        body = _problem_413_body(correlation_id)
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/problem+json"),
                    (b"content-length", str(len(body)).encode()),
                    (b"X-Correlation-ID", correlation_id.encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})