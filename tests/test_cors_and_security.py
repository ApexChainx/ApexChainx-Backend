import pytest
from fastapi.testclient import TestClient

from app.core import config
from app.main import app


def test_options_preflight_only_configured_methods_and_headers():
    client = TestClient(app)
    # Use one of the default allowed origins so the pre-existing CORSMiddleware
    # (initialized at app import time with settings.ALLOWED_ORIGINS) accepts it.
    headers = {
        "Origin": "http://localhost:3000",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "Authorization, X-Correlation-ID",
    }
    response = client.options("/api/v1/somewhere", headers=headers)
    assert response.status_code in (200, 204)

    allow_methods = response.headers.get("access-control-allow-methods", "")
    # default methods should be advertised, and no wildcard
    for m in config.settings.CORS_ALLOWED_METHODS:
        assert m in allow_methods
    assert "*" not in allow_methods

    allow_headers = response.headers.get("access-control-allow-headers", "")
    for h in config.settings.CORS_ALLOWED_HEADERS:
        assert h in allow_headers


def test_credentials_continue_to_work_with_explicit_origin():
    client = TestClient(app)
    # Use one of the default allowed origins since CORSMiddleware was initialized at import time
    response = client.get("/health/liveness", headers={"Origin": "http://localhost:3000"})
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-credentials") == "true"
    assert response.headers.get("access-control-allow-origin") == "http://localhost:3000"


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
