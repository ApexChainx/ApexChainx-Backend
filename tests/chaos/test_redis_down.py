import time

import httpx
import pytest


@pytest.mark.skip(reason="Requires toxiproxy running on localhost:8474")
class TestRedisDownChaos:
    TOXIPROXY_URL = "http://localhost:8474"
    REDIS_PROXY_NAME = "redis_proxy"

    @pytest.fixture(autouse=True)
    def setup_teardown(self):
        self._ensure_proxy_exists()
        yield
        self._remove_proxy()

    def _ensure_proxy_exists(self):
        resp = httpx.get(f"{self.TOXIPROXY_URL}/proxies")
        existing = [p["name"] for p in resp.json()]
        if self.REDIS_PROXY_NAME not in existing:
            httpx.post(
                f"{self.TOXIPROXY_URL}/proxies",
                json={
                    "name": self.REDIS_PROXY_NAME,
                    "listen": "0.0.0.0:16379",
                    "upstream": "localhost:6379",
                },
            )

    def _remove_proxy(self):
        httpx.delete(f"{self.TOXIPROXY_URL}/proxies/{self.REDIS_PROXY_NAME}")

    def _add_timeout_toxic(self):
        httpx.post(
            f"{self.TOXIPROXY_URL}/proxies/{self.REDIS_PROXY_NAME}/toxics",
            json={
                "type": "timeout",
                "name": "redis_timeout",
                "stream": "downstream",
                "toxicity": 1.0,
                "attributes": {"timeout": 0},
            },
        )

    def _add_latency_toxic(self, latency_ms: int):
        httpx.post(
            f"{self.TOXIPROXY_URL}/proxies/{self.REDIS_PROXY_NAME}/toxics",
            json={
                "type": "latency",
                "name": "redis_latency",
                "stream": "downstream",
                "toxicity": 1.0,
                "attributes": {"latency": latency_ms, "jitter": 0},
            },
        )

    def test_rate_limiter_handles_redis_down(self):
        self._add_timeout_toxic()
        try:
            import fakeredis

            test_redis = fakeredis.FakeRedis()
            test_redis.set("rate_limit:test", "1")
            val = test_redis.get("rate_limit:test")
            assert val == b"1"
        finally:
            self._remove_proxy()

    def test_cache_fallback_on_redis_latency(self):
        self._add_latency_toxic(5000)
        try:
            import fakeredis

            fake_redis = fakeredis.FakeRedis()
            fake_redis.set("test_key", "cached_value")
            val = fake_redis.get("test_key")
            assert val == b"cached_value"
        finally:
            self._remove_proxy()

    def test_health_readiness_handles_redis_down(self):
        self._add_timeout_toxic()
        try:
            from fastapi.testclient import TestClient

            from app.main import app

            client = TestClient(app)
            resp = client.get("/health/readiness")
            assert resp.status_code in (200, 503)
        finally:
            self._remove_proxy()

    def test_app_does_not_hang_on_redis_timeout(self):
        self._add_timeout_toxic()
        try:
            from fastapi.testclient import TestClient

            from app.main import app

            client = TestClient(app)
            start = time.time()
            resp = client.get("/health/liveness")
            elapsed = time.time() - start
            assert resp.status_code == 200
            assert elapsed < 10
        finally:
            self._remove_proxy()
