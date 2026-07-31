import time
from urllib.parse import urlparse

import httpx
import pytest
from sqlalchemy import text

from app.core.config import settings


@pytest.mark.skip(reason="Requires toxiproxy running on localhost:8474")
class TestDatabaseLatencyChaos:
    TOXIPROXY_URL = "http://localhost:8474"
    DB_PROXY_NAME = "postgresql_proxy"

    @pytest.fixture(autouse=True)
    def setup_teardown(self):
        self._ensure_proxy_exists()
        yield
        self._remove_toxics()

    def _proxy_listen(self):
        parsed = urlparse(settings.DATABASE_URL)
        return f"{parsed.hostname}:{parsed.port}"

    def _ensure_proxy_exists(self):
        resp = httpx.get(f"{self.TOXIPROXY_URL}/proxies")
        existing = [p["name"] for p in resp.json()]
        if self.DB_PROXY_NAME not in existing:
            listen = "0.0.0.0:15432"
            upstream = self._proxy_listen()
            httpx.post(
                f"{self.TOXIPROXY_URL}/proxies",
                json={"name": self.DB_PROXY_NAME, "listen": listen, "upstream": upstream},
            )

    def _remove_toxics(self):
        httpx.delete(f"{self.TOXIPROXY_URL}/proxies/{self.DB_PROXY_NAME}/toxics")

    def _add_latency_toxic(self, latency_ms: int, jitter_ms: int = 0):
        httpx.post(
            f"{self.TOXIPROXY_URL}/proxies/{self.DB_PROXY_NAME}/toxics",
            json={
                "type": "latency",
                "name": "db_latency",
                "stream": "downstream",
                "toxicity": 1.0,
                "attributes": {"latency": latency_ms, "jitter": jitter_ms},
            },
        )

    def test_db_query_with_normal_latency(self):
        self._add_latency_toxic(50, 10)
        try:
            from app.db.session import engine

            start = time.time()
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
                conn.commit()
            elapsed = (time.time() - start) * 1000
            assert elapsed > 40
        finally:
            self._remove_toxics()

    def test_db_query_with_high_latency(self):
        self._add_latency_toxic(2000, 500)
        try:
            from tenacity import retry, stop_after_attempt, wait_fixed

            from app.db.session import engine

            @retry(stop=stop_after_attempt(3), wait=wait_fixed(1))
            def query():
                with engine.connect() as conn:
                    conn.execute(text("SELECT 1"))
                    conn.commit()

            start = time.time()
            query()
            elapsed = (time.time() - start) * 1000
            assert elapsed > 1800
        finally:
            self._remove_toxics()

    def test_db_query_with_timeout(self):
        self._add_latency_toxic(30000)
        try:
            from sqlalchemy import exc as sa_exc

            from app.db.session import engine

            start = time.time()
            timeout = 5
            try:
                with engine.connect() as conn:
                    conn.execute(text(f"SET statement_timeout = {timeout * 1000}"))
                    conn.execute(text("SELECT pg_sleep(10)"))
                    conn.commit()
            except sa_exc.OperationalError:
                elapsed = (time.time() - start) * 1000
                assert elapsed < 10000
                return
            elapsed = (time.time() - start) * 1000
            assert elapsed < 10000
        finally:
            self._remove_toxics()

    def test_health_endpoint_survives_db_latency(self):
        self._add_latency_toxic(300, 50)
        try:
            from fastapi.testclient import TestClient

            from app.main import app

            client = TestClient(app)
            resp = client.get("/health/liveness")
            assert resp.status_code == 200
        finally:
            self._remove_toxics()
