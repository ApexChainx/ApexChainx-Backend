from unittest.mock import patch
from fastapi.testclient import TestClient

from app.main import app


def test_health_liveness_endpoint():
    client = TestClient(app)
    response = client.get("/health/liveness")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "timestamp" in body


@patch("app.main.build_readiness_report")
def test_health_readiness_endpoint_ok_status(mock_build_report):
    """Test readiness endpoint returns 200 when all components are ok."""
    mock_build_report.return_value = {
        "status": "ok",
        "components": {
            "database": {"status": "ok", "latency_ms": 5.2},
            "postgres_pool": {"status": "ok", "pool_size": 10, "checked_out": 2},
            "redis": {"status": "ok", "latency_ms": 1.8},
            "webhook_dlq": {"status": "ok", "dead_letter_count": 0},
            "audit_database": {"status": "ok", "latency_ms": 3.1},
            "revocation_store": {"status": "ok", "latency_ms": 2.3},
        },
    }

    client = TestClient(app)
    response = client.get("/health/readiness")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "components" in body
    assert "timestamp" in body


@patch("app.main.build_readiness_report")
def test_health_readiness_endpoint_warn_status(mock_build_report):
    """Test readiness endpoint returns 200 when some components are warn."""
    mock_build_report.return_value = {
        "status": "warn",
        "components": {
            "database": {"status": "ok", "latency_ms": 5.2},
            "postgres_pool": {"status": "warn", "pool_size": 10, "checked_out": 9},
            "redis": {"status": "ok", "latency_ms": 1.8},
            "webhook_dlq": {"status": "ok", "dead_letter_count": 0},
            "audit_database": {"status": "warn", "error": "Connection timeout"},
            "revocation_store": {"status": "ok", "latency_ms": 2.3},
        },
    }

    client = TestClient(app)
    response = client.get("/health/readiness")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "warn"
    assert "components" in body
    assert "timestamp" in body


@patch("app.main.build_readiness_report")
def test_health_readiness_endpoint_down_status(mock_build_report):
    """Test readiness endpoint returns 503 when any component is down."""
    mock_build_report.return_value = {
        "status": "down",
        "components": {
            "database": {"status": "down", "error": "Connection refused"},
            "postgres_pool": {"status": "ok", "pool_size": 10, "checked_out": 2},
            "redis": {"status": "ok", "latency_ms": 1.8},
            "webhook_dlq": {"status": "ok", "dead_letter_count": 0},
            "audit_database": {"status": "ok", "latency_ms": 3.1},
            "revocation_store": {"status": "ok", "latency_ms": 2.3},
        },
    }

    client = TestClient(app)
    response = client.get("/health/readiness")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "down"
    assert "components" in body
    assert "timestamp" in body


def test_legacy_health_endpoint_is_deprecated_and_redirects():
    """Legacy /health now returns 308 redirect to /health/liveness with Deprecation header (BE-041)."""
    client = TestClient(app)
    response = client.get("/health", follow_redirects=False)

    assert response.status_code == 308
    assert response.headers.get("location") == "/health/liveness"
    assert response.headers.get("deprecation") == "true"
