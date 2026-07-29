from fastapi.testclient import TestClient

from app.main import app


def test_health_liveness_endpoint():
    client = TestClient(app)
    response = client.get("/health/liveness")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "timestamp" in body


def test_health_readiness_endpoint():
    client = TestClient(app)
    response = client.get("/health/readiness")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] in {"ok", "degraded"}
    assert body["dependencies"]["database"] in {"ok", "down"}
    assert body["dependencies"]["celery"] in {"ok", "down"}
    assert "timestamp" in body


def test_legacy_health_endpoint_is_liveness_alias():
    client = TestClient(app)
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
