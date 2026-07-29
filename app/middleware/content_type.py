from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse


ALLOWED_CONTENT_TYPES = frozenset(
    {
        "application/json",
        "multipart/form-data",
    }
)

BODY_METHODS = frozenset({"POST", "PUT", "PATCH"})


class ContentTypeMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        if request.method in BODY_METHODS:
            # Skip content-type enforcement when the request carries no body.
            # Requests without a body (no Content-Length or Content-Length: 0)
            # should pass through so that auth/authz middleware can reject them
            # with the appropriate 401/403 before content-type is relevant.
            content_length = request.headers.get("content-length")
            transfer_encoding = request.headers.get("transfer-encoding", "")
            has_body = (content_length is not None and int(content_length) > 0) or bool(transfer_encoding)

            if has_body:
                content_type = (request.headers.get("content-type") or "").split(";")[0].strip().lower()

                is_allowed = any(
                    content_type == allowed or content_type.startswith(allowed) for allowed in ALLOWED_CONTENT_TYPES
                )

                if not is_allowed:
                    return JSONResponse(
                        status_code=415,
                        content={
                            "detail": "Unsupported media type. Only application/json and multipart/form-data are accepted.",
                        },
                    )

        return await call_next(request)
