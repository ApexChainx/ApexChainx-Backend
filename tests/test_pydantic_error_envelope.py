from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.core.exceptions import _build_rfc7807


def test_rfc7807_structure():
    body = _build_rfc7807(
        title="Validation Error",
        status=422,
        detail="Invalid input",
        instance="/api/v1/outages",
        errors=[{"field": "severity", "message": "Field required", "code": "missing"}],
    )
    assert body["type"].startswith("https://")
    assert body["title"] == "Validation Error"
    assert body["status"] == 422
    assert body["detail"] == "Invalid input"
    assert body["instance"] == "/api/v1/outages"
    assert len(body["errors"]) == 1


def test_validation_handler_on_bad_request():
    client = TestClient(app)
    response = client.post(
        "/api/v1/auth/register",
        json={"email": "bad-email", "password": "123"},
    )

    assert response.status_code == 422
    body = response.json()
    assert body["title"] == "Validation Error"
    assert body["status"] == 422
    assert "errors" in body
    assert "type" in body
