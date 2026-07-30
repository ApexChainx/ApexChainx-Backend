from fastapi.testclient import TestClient

from app.main import app
from app.core.exceptions import ApexException, ApexNotFoundError, ApexTransientError


client = TestClient(app, raise_server_exceptions=False)


def test_correlation_id_in_404_error_response():
    response = client.get("/non-existent-endpoint-12345")
    assert response.status_code == 404
    assert "X-Correlation-ID" in response.headers
    corr_header = response.headers["X-Correlation-ID"]
    assert corr_header != ""

    body = response.json()
    assert body.get("correlation_id") == corr_header


def test_correlation_id_in_422_validation_error_response():
    response = client.post(
        "/api/v1/auth/login",
        json={"invalid_field": "test"},
    )
    assert response.status_code in (422, 400)
    assert "X-Correlation-ID" in response.headers
    corr_header = response.headers["X-Correlation-ID"]

    body = response.json()
    assert body.get("correlation_id") == corr_header


def test_correlation_id_propagates_passed_header_on_error():
    custom_cid = "test-correlation-id-9999"
    response = client.get(
        "/non-existent-endpoint-12345",
        headers={"X-Correlation-ID": custom_cid},
    )
    assert response.status_code == 404
    assert response.headers.get("X-Correlation-ID") == custom_cid

    body = response.json()
    assert body.get("correlation_id") == custom_cid


def test_correlation_id_in_forced_apex_exceptions():
    @app.get("/test/forced-apex-exception")
    def forced_apex_error():
        raise ApexException(detail="Forced domain error", status_code=400)

    @app.get("/test/forced-apex-not-found")
    def forced_not_found():
        raise ApexNotFoundError(detail="Forced item not found")

    @app.get("/test/forced-apex-transient")
    def forced_transient():
        raise ApexTransientError(detail="Forced transient error")

    @app.get("/test/forced-unhandled-exception")
    def forced_unhandled():
        raise RuntimeError("Forced unhandled exception")

    # Test forced ApexException
    res1 = client.get("/test/forced-apex-exception")
    assert res1.status_code == 400
    assert "X-Correlation-ID" in res1.headers
    assert res1.json().get("correlation_id") == res1.headers["X-Correlation-ID"]

    # Test forced ApexNotFoundError
    res2 = client.get("/test/forced-apex-not-found")
    assert res2.status_code == 404
    assert "X-Correlation-ID" in res2.headers
    assert res2.json().get("correlation_id") == res2.headers["X-Correlation-ID"]

    # Test forced ApexTransientError
    res3 = client.get("/test/forced-apex-transient")
    assert res3.status_code == 503
    assert "X-Correlation-ID" in res3.headers
    assert res3.json().get("correlation_id") == res3.headers["X-Correlation-ID"]

    # Test forced unhandled exception (500)
    res4 = client.get("/test/forced-unhandled-exception")
    assert res4.status_code == 500
    assert "X-Correlation-ID" in res4.headers
    assert res4.json().get("correlation_id") == res4.headers["X-Correlation-ID"]
