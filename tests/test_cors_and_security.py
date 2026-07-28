from fastapi.testclient import TestClient
import pytest

from app.main import app
from app.core import config


def test_options_preflight_only_configured_methods_and_headers():
    client = TestClient(app)
    # ensure explicit origin is allowed for the test
    original_origins = config.settings.ALLOWED_ORIGINS
    original_methods = config.settings.CORS_ALLOWED_METHODS
    original_headers = config.settings.CORS_ALLOWED_HEADERS
    try:
        config.settings.ALLOWED_ORIGINS = ["http://example.com"]
        config.settings.CORS_ALLOWED_METHODS = ["GET", "POST", "OPTIONS"]
        config.settings.CORS_ALLOWED_HEADERS = ["Authorization", "X-Correlation-ID"]

        headers = {
            "Origin": "http://example.com",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Authorization, X-Correlation-ID",
        }
        response = client.options("/api/v1/somewhere", headers=headers)
        assert response.status_code in (200, 204)

        allow_methods = response.headers.get("access-control-allow-methods", "")
        # configured methods should be advertised, and no wildcard
        for m in config.settings.CORS_ALLOWED_METHODS:
            assert m in allow_methods
        assert "*" not in allow_methods

        allow_headers = response.headers.get("access-control-allow-headers", "")
        for h in config.settings.CORS_ALLOWED_HEADERS:
            assert h in allow_headers

    finally:
        config.settings.ALLOWED_ORIGINS = original_origins
        config.settings.CORS_ALLOWED_METHODS = original_methods
        config.settings.CORS_ALLOWED_HEADERS = original_headers


def test_credentials_continue_to_work_with_explicit_origin():
    client = TestClient(app)
    original_origins = config.settings.ALLOWED_ORIGINS
    try:
        config.settings.ALLOWED_ORIGINS = ["http://example.com"]
        response = client.get("/health/liveness", headers={"Origin": "http://example.com"})
        assert response.status_code == 200
        assert response.headers.get("access-control-allow-credentials") == "true"
        assert response.headers.get("access-control-allow-origin") == "http://example.com"
    finally:
        config.settings.ALLOWED_ORIGINS = original_origins


def test_wildcard_origins_rejected_on_startup_validation():
    # create a synthetic Settings instance with wildcard origin and ensure validation fails
    bad = config.Settings(ALLOWED_ORIGINS=["*"])
    with pytest.raises(ValueError):
        config.validate_critical_settings(bad)


def test_security_headers_and_hsts_behavior():
    client = TestClient(app)
    # preserve originals
    orig_env = config.settings.ENVIRONMENT
    orig_enabled = config.settings.SECURITY_HEADERS_ENABLED
    try:
        # HSTS should be present when not local
        config.settings.ENVIRONMENT = "production"
        config.settings.SECURITY_HEADERS_ENABLED = True
        resp = client.get("/health/liveness")
        assert resp.status_code == 200
        # common headers
        assert resp.headers.get("x-content-type-options") == "nosniff"
        assert resp.headers.get("x-frame-options") == "DENY"
        assert resp.headers.get("referrer-policy") == "no-referrer"
        assert "content-security-policy" in resp.headers
        assert resp.headers.get("permissions-policy") == "interest-cohort=()"
        # HSTS present in non-local env
        assert "strict-transport-security" in resp.headers

        # HSTS omitted in local
        config.settings.ENVIRONMENT = "local"
        resp2 = client.get("/health/liveness")
        assert resp2.status_code == 200
        assert "strict-transport-security" not in resp2.headers

        # Middleware can be disabled
        config.settings.SECURITY_HEADERS_ENABLED = False
        resp3 = client.get("/health/liveness")
        assert resp3.status_code == 200
        assert "x-content-type-options" not in resp3.headers

    finally:
        config.settings.ENVIRONMENT = orig_env
        config.settings.SECURITY_HEADERS_ENABLED = orig_enabled
