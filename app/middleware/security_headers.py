
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.config import settings


class SecurityHeadersMiddleware:
    """Add a set of conservative security headers to all non-preflight responses.

    - Does not modify OPTIONS preflight responses.
    - Honors settings.SECURITY_HEADERS_ENABLED and settings.ENVIRONMENT.
    - Allows an opt-in permissive CSP for OpenAPI/Swagger paths.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Only process HTTP responses
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # If disabled, pass-through
        if not settings.SECURITY_HEADERS_ENABLED:
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message):
            # Only modify the http.response.start message
            if message["type"] == "http.response.start":
                # Do not interfere with CORS preflight responses
                request_method = scope.get("method", "").upper()
                if request_method != "OPTIONS":
                    headers = message.setdefault("headers", [])

                    def set_header(name: bytes, value: bytes) -> None:
                        # remove any existing header with same name (case-insensitive)
                        lname = name.lower()
                        nonlocal headers
                        headers = [h for h in headers if h[0].lower() != lname]
                        headers.append((name, value))

                    # Strict-Transport-Security: only in non-local environments
                    if settings.ENVIRONMENT.lower() != "local":
                        set_header(b"strict-transport-security", b"max-age=63072000; includeSubDomains")

                    set_header(b"x-content-type-options", b"nosniff")
                    set_header(b"x-frame-options", b"DENY")
                    set_header(b"referrer-policy", b"no-referrer")

                    # Default CSP - very restrictive
                    csp_value = b"default-src 'none'; frame-ancestors 'none'"

                    # Allow a permissive CSP for docs when configured
                    path = scope.get("path", "")
                    if settings.SECURITY_CSP_SWAGGER_PERMISSIVE and (
                        path.startswith("/docs") or path.startswith("/redoc") or path.startswith("/openapi")
                    ):
                        csp_value = b"default-src 'self' 'unsafe-inline' 'unsafe-eval' data: blob:"

                    set_header(b"content-security-policy", csp_value)
                    set_header(b"permissions-policy", b"interest-cohort=()")

                    # write headers back to message
                    message["headers"] = headers

            await send(message)

        await self.app(scope, receive, send_wrapper)
