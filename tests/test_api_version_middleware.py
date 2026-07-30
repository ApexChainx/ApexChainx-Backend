import os

from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app


class TestApiVersionMiddleware:
    def test_x_api_version_header_present(self):
        with TestClient(app) as client:
            resp = client.get("/health/liveness")
            assert resp.headers.get("X-API-Version") == settings.VERSION


  # m

    def test_x_api_commit_header_default(self):
        with TestClient(app) as client:
            resp = client.get("/health/liveness")
            assert resp.headers.get("X-API-Commit") == "unknown"

    def test_x_api_commit_from_env(self):
        os.environ["GIT_COMMIT_SHA"] = "abc123def456"
        try:
            with TestClient(app) as client:
                resp = client.get("/health/liveness")
                assert resp.headers.get("X-API-Commit") == "abc123def456"
        finally:
            os.environ.pop("GIT_COMMIT_SHA", None)

    def test_headers_on_all_endpoints(self):
        with TestClient(app) as client:
            for path in ["/health/liveness", "/health/readiness", "/health", "/api/v1/webhooks"]:
                resp = client.get(path)
                assert resp.headers.get("X-API-Version") is not None
                assert resp.headers.get("X-API-Commit") is not None

       def test_headers_on_error_responses(self):
        with TestClient(app) as client:
            resp = client.get("/nonexistent")
            assert resp.status_code == 404
            assert resp.headers.get("X-API-Version") is not None
            assert resp.headers.get("X-API-Commit") is not None

    def test_headers_on_error_responses(self):
        with TestClient(app) as client:
            resp = client.get("/nonexistent")
            assert resp.status_code == 404
            assert resp.headers.get("X-API-Version") is not None
            assert resp.headers.get("X-API-Commit") is not None
