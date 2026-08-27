"""Tests for payload size middleware."""

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app.core.config import settings
from app.middleware.payload_size import PayloadSizeMiddleware


async def _echo(request):
    body = await request.body()
    return JSONResponse({"received": len(body)})


@pytest.fixture
def client():
    app = Starlette(routes=[Route("/echo", _echo, methods=["POST", "GET"])])
    app.add_middleware(PayloadSizeMiddleware)
    return TestClient(app)


@pytest.mark.parametrize(
    "bad_value",
    ["abc", "-1", "-999999", "1.5", "", " "],
)
def test_malformed_content_length_never_500s(client, bad_value):
    resp = client.post(
        "/echo",
        headers={"Content-Length": bad_value},
        content=b"{}",
    )
    assert resp.status_code != 500, f"Got 500 for Content-Length: {bad_value!r}"


def test_malformed_content_length_still_enforced_via_streaming_read(client, monkeypatch):
    monkeypatch.setattr(settings, "MAX_REQUEST_BODY_SIZE_BYTES", 10)
    resp = client.post(
        "/echo",
        headers={"Content-Length": "not-a-number"},
        content=b"x" * 100,
    )
    assert resp.status_code == 413


def test_negative_content_length_does_not_bypass_streaming_enforcement(client, monkeypatch):
    monkeypatch.setattr(settings, "MAX_REQUEST_BODY_SIZE_BYTES", 10)
    resp = client.post(
        "/echo",
        headers={"Content-Length": "-1"},
        content=b"x" * 100,
    )
    assert resp.status_code == 413


def test_valid_oversized_content_length_413s_at_header_check(client, monkeypatch):
    monkeypatch.setattr(settings, "MAX_REQUEST_BODY_SIZE_BYTES", 10)
    resp = client.post(
        "/echo",
        headers={"Content-Length": "1000"},
        content=b"x" * 5,
    )
    assert resp.status_code == 413


def test_valid_small_content_length_passes(client, monkeypatch):
    monkeypatch.setattr(settings, "MAX_REQUEST_BODY_SIZE_BYTES", 1000)
    resp = client.post(
        "/echo",
        headers={"Content-Length": "5"},
        content=b"hello",
    )
    assert resp.status_code == 200


def test_get_request_skips_size_checking_entirely(client):
    resp = client.get("/echo", headers={"Content-Length": "abc"})
    assert resp.status_code != 500
