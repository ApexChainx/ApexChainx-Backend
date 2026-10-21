"""Content-type enforcement middleware."""

import logging

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.utils.correlation_ctx import get_or_generate_correlation_id

logger = logging.getLogger(__name__)

ALLOWED_CONTENT_TYPES = frozenset(
    {
        "application/json",
        "multipart/form-data",
    }
)

BODY_METHODS = frozenset({"POST", "PUT", "PATCH"})


def _has_body(content_length: str | None, transfer_encoding: str) -> bool:
    has_content_length = False
    if content_length is not None:
        try:
            has_content_length = int(content_length) > 0
        except (ValueError, TypeError):
            pass
    return has_content_length or bool(transfer_encoding)


class ContentTypeMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        if request.method in BODY_METHODS:
            content_length = request.headers.get("content-length")
            transfer_encoding = request.headers.get("transfer-encoding", "")
            has_body = _has_body(content_length, transfer_encoding)

            if has_body:
                content_type = (request.headers.get("content-type") or "").split(";")[0].strip().lower()

                is_allowed = any(
                    content_type == allowed or content_type.startswith(allowed) for allowed in ALLOWED_CONTENT_TYPES
                )

                if not is_allowed:
                    correlation_id = get_or_generate_correlation_id()
                    return JSONResponse(
                        status_code=415,
                        content={
                            "type": "about:blank",
                            "title": "Unsupported Media Type",
                            "status": 415,
                            "detail": "Unsupported media type. Only application/json and multipart/form-data are accepted.",
                            "correlation_id": correlation_id,
                        },
                        media_type="application/problem+json",
                        headers={"X-Correlation-ID": correlation_id},
                    )

        return await call_next(request)
