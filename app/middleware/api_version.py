"""API version header handling and request version negotiation (#499).

The middleware previously only *echoed* ``X-API-Version`` on responses, so a
client pinning an older documented version silently received the current
behaviour and version drift was unobservable.  Requests now negotiate:

* no header            -> served as "latest" (backwards compatible),
* supported version    -> served normally and echoed back,
* unparseable version  -> 400 ``api_version_unsupported``,
* out-of-range version -> 426 ``api_version_unsupported`` with the supported
  range advertised in ``X-Supported-API-Versions``.

Errors are RFC 7807 problem documents, matching the rest of the error surface
(see ``docs/ERROR_CODES.md``).
"""

import os
from collections.abc import Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import API_VERSION_HEADER, parse_api_version, settings

ERROR_CODE = "api_version_unsupported"


def supported_versions() -> list[str]:
    """Return the advertised supported versions, oldest first."""
    return [settings.API_VERSION_MIN_SUPPORTED, settings.API_VERSION_MAX_SUPPORTED]


def _version_headers() -> dict[str, str]:
    return {
        API_VERSION_HEADER: settings.VERSION,
        "X-Supported-API-Versions": ",".join(supported_versions()),
    }


def _unsupported(
    requested: str, status_code: int, detail: str, request: Request
) -> JSONResponse:
    headers = _version_headers()
    body = {
        "type": f"https://developer.apexchainx.io/errors/{status_code}",
        "title": "Unsupported API Version" if status_code == 426 else "Invalid API Version",
        "status": status_code,
        "detail": detail,
        "instance": str(request.url.path),
        "error_code": ERROR_CODE,
        "requested_version": requested,
        "supported_versions": supported_versions(),
        "current_version": settings.VERSION,
    }
    return JSONResponse(
        status_code=status_code,
        content=body,
        media_type="application/problem+json",
        headers=headers,
    )


def reject_unsupported_version(request: Request) -> JSONResponse | None:
    """Return an error response when the pinned version cannot be served."""
    requested = request.headers.get(API_VERSION_HEADER)
    if requested is None or not requested.strip():
        # No pin: serve the latest behaviour.
        return None

    requested_version = parse_api_version(requested)
    if requested_version is None:
        return _unsupported(
            requested,
            400,
            f"Malformed {API_VERSION_HEADER} header; expected a dotted numeric "
            f"version such as {settings.VERSION}.",
            request,
        )

    minimum = parse_api_version(settings.API_VERSION_MIN_SUPPORTED)
    maximum = parse_api_version(settings.API_VERSION_MAX_SUPPORTED)
    if minimum is not None and requested_version < minimum:
        return _unsupported(
            requested,
            426,
            f"API version {requested} is no longer supported; this deployment "
            f"serves {settings.API_VERSION_MIN_SUPPORTED} through "
            f"{settings.API_VERSION_MAX_SUPPORTED}.",
            request,
        )
    if maximum is not None and requested_version > maximum:
        return _unsupported(
            requested,
            426,
            f"API version {requested} is not available on this deployment; this "
            f"deployment serves {settings.API_VERSION_MIN_SUPPORTED} through "
            f"{settings.API_VERSION_MAX_SUPPORTED}.",
            request,
        )
    return None


class ApiVersionMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        rejection = reject_unsupported_version(request)
        if rejection is not None:
            return rejection

        response = await call_next(request)
        response.headers[API_VERSION_HEADER] = settings.VERSION
        response.headers["X-API-Commit"] = os.environ.get("GIT_COMMIT_SHA", "unknown")
        response.headers["X-Supported-API-Versions"] = ",".join(supported_versions())
        return response
