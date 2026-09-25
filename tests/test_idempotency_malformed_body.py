"""Regression tests for #497: malformed bodies under an Idempotency-Key.

Before the fix, ``_compute_fingerprint`` called ``json.loads`` unguarded, so a
client retry that arrived with a corrupt body produced an unhandled
``JSONDecodeError`` (HTTP 500 with a stack trace) instead of a client error --
exactly when the idempotency guarantee was supposed to absorb the retry.
"""

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.middleware.idempotency import (
    IdempotencyMiddleware,
    MalformedRequestBody,
    _compute_fingerprint,
)


class FakeRedis:
    """Minimal in-memory stand-in for the Redis used by the middleware."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.setex_calls: list[str] = []

    def get(self, key: str):
        return self.store.get(key)

    def setex(self, key: str, ttl: int, value: str) -> None:
        self.setex_calls.append(key)
        self.store[key] = value


def _client(redis: FakeRedis) -> TestClient:
    app = FastAPI()
    app.add_middleware(IdempotencyMiddleware, redis_client=redis)

    @app.post("/api/v1/echo")
    async def echo(payload: dict):
        return {"received": payload}

    return TestClient(app, raise_server_exceptions=False)


class TestFingerprint:
    def test_valid_json_is_normalised_by_key_order(self):
        a = _compute_fingerprint("POST", "/x", b'{"a":1,"b":2}')
        b = _compute_fingerprint("POST", "/x", b'{"b":2,"a":1}')
        assert a == b

    def test_empty_body_fingerprints_as_empty_object(self):
        assert _compute_fingerprint("POST", "/x", b"") == _compute_fingerprint(
            "POST", "/x", b"{}"
        )

    def test_malformed_json_raises_domain_error(self):
        try:
            _compute_fingerprint("POST", "/x", b'{"a":')
        except MalformedRequestBody as exc:
            assert "not valid JSON" in str(exc)
        else:  # pragma: no cover - the call above must raise
            raise AssertionError("expected MalformedRequestBody")

    def test_invalid_utf8_raises_domain_error(self):
        try:
            _compute_fingerprint("POST", "/x", b"\xff\xfe\x00")
        except MalformedRequestBody:
            pass
        else:  # pragma: no cover - the call above must raise
            raise AssertionError("expected MalformedRequestBody")


class TestMalformedBodyRequest:
    def test_malformed_body_returns_400_not_500(self):
        redis = FakeRedis()
        client = _client(redis)
        resp = client.post(
            "/api/v1/echo",
            content=b'{"amount": ',
            headers={"Idempotency-Key": "key-1", "Content-Type": "application/json"},
        )
        assert resp.status_code == 400
        assert "not valid JSON" in resp.json()["detail"]

    def test_malformed_body_does_not_persist_anything_for_the_key(self):
        redis = FakeRedis()
        client = _client(redis)
        client.post(
            "/api/v1/echo",
            content=b"not json at all",
            headers={"Idempotency-Key": "key-1", "Content-Type": "application/json"},
        )
        assert redis.store == {}
        assert redis.setex_calls == []

    def test_corrected_retry_under_same_key_succeeds(self):
        redis = FakeRedis()
        client = _client(redis)
        headers = {"Idempotency-Key": "key-1", "Content-Type": "application/json"}

        bad = client.post("/api/v1/echo", content=b"{oops", headers=headers)
        assert bad.status_code == 400

        good = client.post("/api/v1/echo", json={"amount": 5}, headers=headers)
        assert good.status_code == 200
        assert good.json() == {"received": {"amount": 5}}

        replay = client.post("/api/v1/echo", json={"amount": 5}, headers=headers)
        assert replay.status_code == 200
        assert json.loads(replay.content) == good.json()

    def test_requests_without_idempotency_key_are_untouched(self):
        redis = FakeRedis()
        client = _client(redis)
        resp = client.post(
            "/api/v1/echo",
            content=b"{oops",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 422  # FastAPI validation, not a middleware 400
        assert redis.store == {}
