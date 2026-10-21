import asyncio

from fastapi import FastAPI, Response
from fastapi.testclient import TestClient
from starlette.responses import StreamingResponse
from starlette.types import Receive, Scope, Send

from app.middleware.etag import ETagMiddleware, MAX_ETAG_BODY_BYTES


def create_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(ETagMiddleware)

    @app.get("/small")
    async def small() -> Response:
        return Response(content=b"small body", media_type="text/plain")

    @app.get("/large")
    async def large() -> Response:
        return Response(content=b"x" * (MAX_ETAG_BODY_BYTES + 1), media_type="text/plain")

    stream_consumed = False

    async def stream_body():
        nonlocal stream_consumed
        stream_consumed = True
        yield b"streamed body"

    @app.get("/stream")
    async def stream() -> StreamingResponse:
        return StreamingResponse(stream_body(), media_type="text/plain")

    app.state.stream_consumed = lambda: stream_consumed
    return app


def test_small_response_gets_etag_and_supports_not_modified():
    with TestClient(create_app()) as client:
        response = client.get("/small")
        assert response.status_code == 200
        assert response.content == b"small body"
        etag = response.headers["etag"]

        not_modified = client.get("/small", headers={"If-None-Match": etag})

    assert not_modified.status_code == 304
    assert not_modified.headers["etag"] == etag


def test_large_response_gets_bounded_in_pass_etag():
    with TestClient(create_app()) as client:
        response = client.get("/large")

    assert response.status_code == 200
    assert len(response.content) == MAX_ETAG_BODY_BYTES + 1
    assert response.headers["etag"].startswith('W/"')


def test_streaming_response_is_not_consumed_by_etag_middleware():
    first_chunk_sent = asyncio.Event()
    second_chunk_requested = asyncio.Event()
    events: list[dict] = []

    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"first", "more_body": True})
        first_chunk_sent.set()
        await second_chunk_requested.wait()
        await send({"type": "http.response.body", "body": b"second", "more_body": False})

    async def send(message: dict) -> None:
        events.append(message)
        if message["type"] == "http.response.body" and message["body"] == b"first":
            second_chunk_requested.set()

    scope: Scope = {"type": "http", "method": "GET", "headers": [], "path": "/stream"}
    middleware = ETagMiddleware(downstream)

    asyncio.run(middleware(scope, lambda: {}, send))

    assert first_chunk_sent.is_set()
    assert [event.get("body") for event in events if event["type"] == "http.response.body"] == [b"first", b"second"]


def test_etag_and_if_none_match():
    client = TestClient(create_app())
    r1 = client.get("/small")
    assert r1.status_code == 200
    assert "etag" in r1.headers
    etag = r1.headers["etag"]
    r2 = client.get("/small", headers={"If-None-Match": etag})
    assert r2.status_code == 304
    assert r2.headers.get("etag") == etag
