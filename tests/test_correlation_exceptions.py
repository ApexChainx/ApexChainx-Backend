import logging

import pytest
from fastapi.testclient import TestClient

from app.core.exceptions import ApexException, ApexNotFoundError, ApexTransientError
from app.core.logging_config import CorrelationIdFilter
from app.main import app
from app.utils.correlation_ctx import set_correlation_id

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


# ---------------------------------------------------------------------------
# Issue #243 – acceptance criteria 3
# Verify correlation ID propagation from request to response on SUCCESS paths
# ---------------------------------------------------------------------------


def test_correlation_id_in_successful_response():
    """X-Correlation-ID header is present on a successful (2xx) response."""
    response = client.get("/health/liveness")
    assert response.status_code == 200
    assert "X-Correlation-ID" in response.headers
    assert response.headers["X-Correlation-ID"] != ""


def test_correlation_id_propagated_from_request_header_on_success():
    """A caller-supplied X-Correlation-ID is echoed back unchanged on success."""
    custom_cid = "test-success-correlation-id-0001"
    response = client.get(
        "/health/liveness",
        headers={"X-Correlation-ID": custom_cid},
    )
    assert response.status_code == 200
    assert response.headers.get("X-Correlation-ID") == custom_cid


def test_correlation_id_generated_when_absent_on_success():
    """A correlation ID is auto-generated and returned even when the caller
    does not supply one, and it conforms to the UUID format."""
    import re

    uuid_re = re.compile(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
        re.IGNORECASE,
    )
    response = client.get("/health/liveness")
    assert response.status_code == 200
    cid = response.headers.get("X-Correlation-ID", "")
    assert uuid_re.match(cid), f"Correlation ID {cid!r} is not a valid UUID"


# ---------------------------------------------------------------------------
# Issue #243 – acceptance criteria 1
# Verify that the CorrelationIdFilter stamps correlation_id on every LogRecord
# ---------------------------------------------------------------------------


def test_correlation_id_filter_stamps_log_records():
    """CorrelationIdFilter injects correlation_id onto every LogRecord so that
    both the JSON and plain formatters can include it without raising KeyError.
    """
    set_correlation_id("filter-test-cid-9999")

    record = logging.LogRecord(
        name="test_logger",
        level=logging.INFO,
        pathname=__file__,
        lineno=0,
        msg="test message",
        args=(),
        exc_info=None,
    )

    f = CorrelationIdFilter()
    result = f.filter(record)

    assert result is True  # filter should allow the record through
    assert record.correlation_id == "filter-test-cid-9999"


def test_correlation_id_filter_defaults_to_empty_string_when_no_context():
    """CorrelationIdFilter sets correlation_id to '' when no context is active,
    so the format string %(correlation_id)s does not raise KeyError.
    """
    from app.utils.correlation_ctx import correlation_id_var

    # Reset the context variable so there is no active correlation ID
    token = correlation_id_var.set(None)
    try:
        record = logging.LogRecord(
            name="test_logger",
            level=logging.INFO,
            pathname=__file__,
            lineno=0,
            msg="startup log line",
            args=(),
            exc_info=None,
        )
        f = CorrelationIdFilter()
        f.filter(record)
        assert record.correlation_id == ""
    finally:
        correlation_id_var.reset(token)


def test_json_formatter_includes_correlation_id_field():
    """_JsonFormatter always emits a correlation_id key in the JSON output."""
    import json

    from app.core.logging_config import CorrelationIdFilter, _JsonFormatter
    from app.utils.correlation_ctx import correlation_id_var

    cid = "json-fmt-test-cid-1234"
    token = correlation_id_var.set(cid)
    try:
        record = logging.LogRecord(
            name="test_logger",
            level=logging.INFO,
            pathname=__file__,
            lineno=0,
            msg="formatted message",
            args=(),
            exc_info=None,
        )
        # Apply the filter first (as dictConfig would do at runtime)
        CorrelationIdFilter().filter(record)

        formatter = _JsonFormatter()
        output = json.loads(formatter.format(record))

        assert "correlation_id" in output
        assert output["correlation_id"] == cid
    finally:
        correlation_id_var.reset(token)
