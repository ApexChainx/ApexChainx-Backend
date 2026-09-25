"""Tests for #499: X-API-Version request negotiation.

The middleware used to only echo ``X-API-Version`` on responses, so a client
pinning an out-of-range version silently received the current behaviour.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import (
    API_VERSION_HEADER,
    Settings,
    parse_api_version,
    validate_critical_settings,
)
from app.middleware.api_version import ERROR_CODE, ApiVersionMiddleware


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.add_middleware(ApiVersionMiddleware)

    @app.get("/probe")
    async def probe():
        return {"ok": True}

    return TestClient(app, raise_server_exceptions=False)


class TestParseApiVersion:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("1.0.0", (1, 0, 0)),
            ("1", (1, 0, 0)),
            ("1.0", (1, 0, 0)),
            ("v1.2.3", (1, 2, 3)),
            (" 2.0.0 ", (2, 0, 0)),
        ],
    )
    def test_valid_versions(self, raw, expected):
        assert parse_api_version(raw) == expected

    @pytest.mark.parametrize(
        "raw", ["", "   ", "banana", "1.0.0.1", "1..0", "1.0.x", "-1", "1.0.0-beta", "1.0.²"]
    )
    def test_invalid_versions(self, raw):
        assert parse_api_version(raw) is None


class TestNegotiation:
    def test_no_header_is_served_as_latest(self, client):
        resp = client.get("/probe")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

    def test_blank_header_is_served_as_latest(self, client):
        resp = client.get("/probe", headers={API_VERSION_HEADER: "   "})
        assert resp.status_code == 200

    def test_supported_version_passes(self, client):
        resp = client.get("/probe", headers={API_VERSION_HEADER: "1.0.0"})
        assert resp.status_code == 200
        assert resp.headers[API_VERSION_HEADER] == "1.0.0"

    def test_bare_major_of_supported_version_passes(self, client):
        resp = client.get("/probe", headers={API_VERSION_HEADER: "1"})
        assert resp.status_code == 200

    def test_future_version_is_rejected_with_426(self, client):
        resp = client.get("/probe", headers={API_VERSION_HEADER: "2.0.0"})
        assert resp.status_code == 426
        body = resp.json()
        assert body["error_code"] == ERROR_CODE
        assert body["requested_version"] == "2.0.0"
        assert body["current_version"] == "1.0.0"
        assert body["supported_versions"] == ["1.0.0", "1.0.0"]
        assert resp.headers[API_VERSION_HEADER] == "1.0.0"
        assert resp.headers["X-Supported-API-Versions"] == "1.0.0,1.0.0"

    def test_retired_version_is_rejected_with_426(self, client):
        resp = client.get("/probe", headers={API_VERSION_HEADER: "0.9.0"})
        assert resp.status_code == 426
        assert resp.json()["error_code"] == ERROR_CODE

    def test_malformed_version_is_rejected_with_400(self, client):
        resp = client.get("/probe", headers={API_VERSION_HEADER: "banana"})
        assert resp.status_code == 400
        assert resp.json()["error_code"] == ERROR_CODE

    def test_rejection_is_rfc7807_problem_document(self, client):
        resp = client.get("/probe", headers={API_VERSION_HEADER: "2.0.0"})
        assert resp.headers["content-type"].startswith("application/problem+json")
        assert resp.json()["type"].endswith("/426")
        assert resp.json()["instance"] == "/probe"


class TestSupportedRangeConfiguration:
    def _settings(self, **overrides) -> Settings:
        base = dict(
            PROJECT_NAME="ApexChainx API",
            VERSION="1.0.0",
            DEBUG=False,
            API_V1_PREFIX="/api/v1",
            DATABASE_URL="postgresql://postgres:password@localhost:5432/apexchainx",
            ALLOWED_ORIGINS=["http://localhost:3000"],
            CELERY_BROKER_URL="redis://localhost:6379/0",
            CELERY_RESULT_BACKEND="redis://localhost:6379/0",
            STELLAR_NETWORK="testnet",
            CONTRACT_EXECUTION_MODE="local_adapter",
            ENVIRONMENT="local",
            SECRET_KEY="apexchainx-dev-secret",
        )
        base.update(overrides)
        return Settings.model_construct(**base)

    def test_served_version_inside_range_is_accepted(self):
        validate_critical_settings(
            self._settings(API_VERSION_MIN_SUPPORTED="1.0.0", API_VERSION_MAX_SUPPORTED="2.0.0")
        )

    def test_served_version_outside_range_is_rejected(self):
        config = self._settings(
            API_VERSION_MIN_SUPPORTED="2.0.0", API_VERSION_MAX_SUPPORTED="3.0.0"
        )
        with pytest.raises(ValueError) as exc:
            validate_critical_settings(config)
        assert "API_VERSION_MIN_SUPPORTED" in str(exc.value)

    def test_inverted_range_is_rejected(self):
        config = self._settings(
            API_VERSION_MIN_SUPPORTED="2.0.0", API_VERSION_MAX_SUPPORTED="1.0.0"
        )
        with pytest.raises(ValueError) as exc:
            validate_critical_settings(config)
        assert "must not be greater than" in str(exc.value)

    def test_unparseable_range_is_rejected(self):
        config = self._settings(API_VERSION_MAX_SUPPORTED="latest")
        with pytest.raises(ValueError) as exc:
            validate_critical_settings(config)
        assert "dotted numeric version" in str(exc.value)
