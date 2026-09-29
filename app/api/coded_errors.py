"""Machine-readable error codes for endpoints that raise bare ``HTTPException`` (#569).

The webhook routes already raise ``WebhookHTTPException`` with registered codes
(docs/ERROR_CODES.md); every other endpoint module still raised plain
``HTTPException`` whose only identity was a human-readable ``detail`` string, so
partners had to string-match to branch on errors.

``CodedHTTPException`` closes that gap: it is a ``StarletteHTTPException``
carrying an ``error_code`` attribute, which the RFC 7807 handler in
``app.api.exception_handlers`` surfaces as an ``error_code`` extension member on
the problem response. Call-sites that need a domain-specific code
(``wallet_not_found``, ``invalid_credentials``, ...) pass ``error_code=``; every
other call-site gets a stable generic code for its status via
``DEFAULT_ERROR_CODES``. The ``detail`` texts are unchanged, so existing
consumers keep working.
"""

from __future__ import annotations

from starlette.exceptions import HTTPException as StarletteHTTPException

# Status → default registered code (docs/ERROR_CODES.md, "Standard HTTP Error
# Codes"). A raise without an explicit ``error_code`` maps through this table so
# a 404 is always ``not_found`` and a 409 always ``conflict`` in the response
# body, no matter which endpoint raised it. Statuses outside the table fall back
# to the RFC 7807 handler's codeless body.
DEFAULT_ERROR_CODES: dict[int, str] = {
    400: "validation_error",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    410: "gone",
    413: "payload_too_large",
    415: "unsupported_media_type",
    422: "unprocessable_entity",
    429: "rate_limited",
    500: "internal_error",
    501: "not_implemented",
    502: "bad_gateway",
    503: "service_unavailable",
}


class CodedHTTPException(StarletteHTTPException):
    """An HTTPException that carries a registered error code (#569)."""

    def __init__(
        self,
        status_code: int,
        detail: str | None = None,
        headers: dict[str, str] | None = None,
        error_code: str | None = None,
    ) -> None:
        super().__init__(status_code=status_code, detail=detail, headers=headers)
        self.error_code = error_code or DEFAULT_ERROR_CODES.get(status_code)
