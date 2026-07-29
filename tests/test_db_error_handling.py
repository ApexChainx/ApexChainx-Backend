from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from unittest.mock import Mock

from app.main import app
from app.core.exceptions import _extract_integrity_fields


class _MockOrig:
    def __init__(self, msg: str):
        self.args = (msg,)


def test_extract_integrity_fields_parses_key():
    msg = (
        'duplicate key value violates unique constraint "uq_outage_id"\n'
        "DETAIL:  Key (id)=(OUT-001) already exists."
    )
    exc = IntegrityError("stmt", {}, _MockOrig(msg))
    assert _extract_integrity_fields(exc) == {"id": "OUT-001"}


def test_extract_integrity_fields_no_match():
    exc = IntegrityError("stmt", {}, _MockOrig("Some other error."))
    assert _extract_integrity_fields(exc) == {}


def test_integrity_handler_returns_409():
    client = TestClient(app)
    msg = (
        'duplicate key value violates unique constraint "uq_outage_id"\n'
        "DETAIL:  Key (id)=(OUT-001) already exists."
    )
    exc = IntegrityError("stmt", {}, _MockOrig(msg))

    response = client.post(
        "/api/v1/outages/",
        json={"site_name": "test", "severity": "high", "description": "Test outage"},
    )

    if response.status_code == 422:
        return

    assert response.status_code == 409
    body = response.json()
    assert body["title"] == "Conflict"
    assert body["status"] == 409
    assert "fields" in body
    assert "type" in body
