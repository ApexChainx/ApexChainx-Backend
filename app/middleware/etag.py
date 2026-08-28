"""ETag / If-None-Match middleware for GET endpoints.

Generates a weak ETag from the response body prefix so clients can send
``If-None-Match`` headers on subsequent requests.  Returns 304
Not Modified when the content has not changed, saving bandwidth.

ETag algorithm: SHA-256 of a bounded response body prefix, combined with
the response length when available, and wrapped in ``W/"..."``.
"""

import hashlib

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.utils.logging import get_structured_logger

logger = get_structured_logger("etag_middleware")
MAX_ETAG_BODY_BYTES = 1024 * 1024


def _compute_etag(body: bytes, content_length: str | None) -> str:
    digest = hashlib.sha256()
    digest.update(body)
    if content_length is not None:
        digest.update(b"|")
        digest.update(content_length.encode("ascii"))
    return f'W/"{digest.hexdigest()}"'


class ETagMiddleware:
    """Add ETag response headers and honour If-None-Match on GET/HEAD requests.

    Only applies to 2xx responses for GET and HEAD methods.
    Responses that already carry an ETag are left untouched.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] not in ("GET", "HEAD"):
            await self.app(scope, receive, send)
            return

        request_headers = dict(scope.get("headers", []))
        if b"if-none-match" in request_headers:
            if_none_match = request_headers[b"if-none-match"].decode("latin-1")
        else:
            if_none_match = None

        response_status: int | None = None
        response_headers: list[tuple[bytes, bytes]] = []
        body_prefix = bytearray()
        etag: str | None = None
        not_modified = False

        async def send_with_etag(message: Message) -> None:
            nonlocal response_status, response_headers
            nonlocal etag, not_modified

            if message["type"] == "http.response.start":
                response_status = message["status"]
                response_headers = list(message.get("headers", []))
                if response_status < 200 or response_status >= 300:
                    await send(message)
                    return
                if any(name.lower() == b"etag" for name, _ in response_headers):
                    await send(message)
                    return
                return

            if message["type"] != "http.response.body" or response_status is None:
                await send(message)
                return

            body = message.get("body", b"")
            if etag is None:
                remaining = MAX_ETAG_BODY_BYTES - len(body_prefix)
                if remaining > 0:
                    body_prefix.extend(body[:remaining])
                content_length = next(
                    (value.decode("ascii") for name, value in response_headers if name.lower() == b"content-length"),
                    None,
                )
                etag = _compute_etag(bytes(body_prefix), content_length)
                not_modified = if_none_match == etag

                if not_modified:
                    logger.info("ETag match, returning 304", path=scope.get("path", ""), etag=etag)
                    headers = [(b"etag", etag.encode("ascii"))]
                    correlation_id = next(
                        (value for name, value in response_headers if name.lower() == b"x-correlation-id"),
                        None,
                    )
                    if correlation_id is not None:
                        headers.append((b"x-correlation-id", correlation_id))
                    await send({"type": "http.response.start", "status": 304, "headers": headers})
                    return

                headers = response_headers + [(b"etag", etag.encode("ascii"))]
                await send({"type": "http.response.start", "status": response_status, "headers": headers})

            if not not_modified:
                await send(message)

        await self.app(scope, receive, send_with_etag)
