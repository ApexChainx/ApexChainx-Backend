from fastapi.testclient import TestClient
from app.main import app
from app.core.config import settings


def test_payload_too_large_integration(monkeypatch):
    # Make the limit small so the test is fast and deterministic
    monkeypatch.setattr(settings, "MAX_REQUEST_BODY_SIZE_BYTES", 16)
    client = TestClient(app)
    data = b"a" * 100
    resp = client.post("/health/liveness", data=data)
    assert resp.status_code == 413
    assert resp.headers.get("content-type") == "application/problem+json"
    body = resp.json()
    assert body.get("status") == 413
    assert body.get("error_code") == "payload_too_large"
    assert "correlation_id" in body
    # Ensure correlation header is present (middleware adds X-Correlation-ID)
    assert resp.headers.get("X-Correlation-ID") or resp.headers.get("x-correlation-id")
