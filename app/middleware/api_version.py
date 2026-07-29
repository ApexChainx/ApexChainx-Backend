import os
from collections.abc import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import settings


class ApiVersionMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)
        response.headers["X-API-Version"] = settings.VERSION
        response.headers["X-API-Commit"] = os.environ.get("GIT_COMMIT_SHA", "unknown")
        return response
