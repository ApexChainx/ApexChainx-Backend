import asyncio
import json
from unittest.mock import Mock

from sqlalchemy.exc import IntegrityError

from app.core.exceptions import _extract_integrity_fields, integrity_error_handler


class _MockOrig:
    def __init__(self, msg: str):
        self.args = (msg,)


def _mock_request() -> Mock:
    request = Mock()
    request.url.path = "/api/v1/outages/"
    return request


def test_extract_integrity_fields_parses_key():
    msg = (
        'duplicate key value violates unique constraint "uq_outage_id"\n' "DETAIL:  Key (id)=(OUT-001) already exists."
    )
    exc = IntegrityError("stmt", {}, _MockOrig(msg))
    assert _extract_integrity_fields(exc) == {"id": "OUT-001"}


def test_extract_integrity_fields_no_match():
    exc = IntegrityError("stmt", {}, _MockOrig("Some other error."))
    assert _extract_integrity_fields(exc) == {}


def test_integrity_handler_returns_409():
    msg = (
        'duplicate key value violates unique constraint "uq_outage_id"\n' "DETAIL:  Key (id)=(OUT-001) already exists."
    )
    exc = IntegrityError("stmt", {}, _MockOrig(msg))

    response = asyncio.run(integrity_error_handler(_mock_request(), exc))

    assert response.status_code == 409
    body = json.loads(response.body)
    assert body["title"] == "Conflict"
    assert body["status"] == 409
    assert "fields" in body
    assert "type" in body
