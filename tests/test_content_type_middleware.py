"""Tests for content-type enforcement middleware."""

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app.middleware.content_type import ContentTypeMiddleware


async def _echo(request):
    body = await request.body()
    return JSONResponse({"received": len(body)})


@pytest.fixture
def client():
    app = Starlette(routes=[Route("/echo", _echo, methods=["POST", "GET"])])
    app.add_middleware(ContentTypeMiddleware)
    return TestClient(app)


def test_malformed_content_length_never_500s(client):
    for bad_value in ["abc", "-1", "-999999", "99999999999999999999999999999999", "1.5", "", " "]:
        resp = client.post(
            "/echo",
            headers={"Content-Length": bad_value, "Content-Type": "application/json"},
            content=b"{}",
        )
        assert resp.status_code != 500, f"Got 500 for Content-Length: {bad_value!r}"


def test_negative_content_length_treated_as_no_body(client):
    resp = client.post(
        "/echo",
        headers={"Content-Length": "-1", "Content-Type": "text/plain"},
        content=b"{}",
    )
    assert resp.status_code != 415


def test_valid_body_with_invalid_content_type_returns_415(client):
    resp = client.post(
        "/echo",
        headers={"Content-Length": "5", "Content-Type": "text/plain"},
        content=b"hello",
    )
    assert resp.status_code == 415


def test_valid_body_with_valid_content_type_passes(client):
    resp = client.post(
        "/echo",
        headers={"Content-Length": "5", "Content-Type": "application/json"},
        content=b"hello",
    )
    assert resp.status_code == 200


def test_get_request_skips_content_type_check(client):
    resp = client.get("/echo", headers={"Content-Length": "abc", "Content-Type": "text/plain"})
    assert resp.status_code != 415
