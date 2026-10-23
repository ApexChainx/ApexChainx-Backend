"""Tests for RateLimiter per-scope policies, Prometheus metrics and OpenAPI docs."""

import asyncio
import time

import pytest
from redis.exceptions import RedisError

from app.core import rate_limiter as rl_mod
from app.core.config import settings
from app.core.rate_limiter import (
    RATE_LIMITED_RESPONSE,
    UNKNOWN_SCOPE,
    RateLimitPolicy,
    RedisRateLimiter,
    SimpleRateLimiter,
    rate_limit_scope,
    resolve_policy,
    with_rate_limit_responses,
)
from app.services.metrics import metrics


@pytest.fixture(autouse=True)
def redis_enabled(monkeypatch):
    monkeypatch.setattr(settings, "USE_REDIS_RATE_LIMITER", True)
    monkeypatch.setattr(settings, "CELERY_TASK_ALWAYS_EAGER", False)
    monkeypatch.setattr(settings, "CELERY_BROKER_URL", "redis://localhost:6379/0")


@pytest.fixture(autouse=True)
def isolate_shared_state():
    saved = {key: list(hits) for key, hits in SimpleRateLimiter._shared.items()}
    SimpleRateLimiter._shared.clear()
    yield
    SimpleRateLimiter._shared.clear()
    SimpleRateLimiter._shared.update(saved)


@pytest.fixture
def policies(monkeypatch):
    overrides: dict[str, RateLimitPolicy] = {}
    monkeypatch.setattr(rl_mod, "RATE_LIMIT_POLICIES", overrides)
    return overrides


class ScriptedClient:
    def __init__(self, verdicts=()):
        self.verdicts = list(verdicts)
        self.calls = []

    def eval(self, script, numkeys, key, now, window, limit, member):
        self.calls.append((key, window, limit))
        return self.verdicts.pop(0) if self.verdicts else 1


class AsyncScriptedClient(ScriptedClient):
    async def eval(self, *args):
        return ScriptedClient.eval(self, *args)


class FailingClient:
    def eval(self, *args, **kwargs):
        raise RedisError("down")


def counter(name, **tags):
    return metrics.get_metrics_summary()["counters"].get(metrics._make_key(name, tags), 0.0)


def gauge(name, **tags):
    return metrics.get_metrics_summary()["gauges"].get(metrics._make_key(name, tags))


# Per-scope rate-limit policies ---------------------------------------------


class TestPolicies:
    def test_scope_is_extracted_from_known_prefixes(self):
        assert rate_limit_scope("login_ip_1.2.3.4") == "login_ip"
        assert rate_limit_scope("profile_update_42") == "profile_update"
        assert rate_limit_scope("impersonate_7") == "impersonate"
        assert rate_limit_scope("something_else") == UNKNOWN_SCOPE

    def test_default_policy_comes_from_settings(self, policies):
        policy = resolve_policy("login_ip_1")
        assert policy.limit == settings.AUTH_RATE_LIMIT_REQUESTS
        assert policy.window_seconds == settings.AUTH_RATE_LIMIT_WINDOW_SECONDS

    def test_scope_override_and_explicit_policy(self, policies):
        policies["impersonate"] = RateLimitPolicy(limit=2, window_seconds=60)
        assert resolve_policy("impersonate_1") == RateLimitPolicy(2, 60)
        explicit = RateLimitPolicy(limit=1, window_seconds=5)
        assert resolve_policy("impersonate_1", explicit) is explicit

    def test_memory_limiter_enforces_scope_policy(self, policies):
        policies["impersonate"] = RateLimitPolicy(limit=2, window_seconds=60)
        limiter = SimpleRateLimiter()
        assert limiter.is_allowed("impersonate_1") is True
        assert limiter.is_allowed("impersonate_1") is True
        assert limiter.is_allowed("impersonate_1") is False
        # Other scopes keep the global limit.
        for _ in range(settings.AUTH_RATE_LIMIT_REQUESTS):
            assert limiter.is_allowed("login_ip_1") is True

    def test_memory_limiter_honours_explicit_policy(self, policies):
        limiter = SimpleRateLimiter()
        tight = RateLimitPolicy(limit=1, window_seconds=60)
        assert limiter.is_allowed("login_ip_9", policy=tight) is True
        assert limiter.is_allowed("login_ip_9", policy=tight) is False

    def test_cull_uses_per_scope_window(self, policies):
        policies["impersonate"] = RateLimitPolicy(limit=5, window_seconds=10)
        limiter = SimpleRateLimiter()
        now = time.time()
        limiter.requests["impersonate_1"] = [now - 20]
        limiter.requests["login_ip_1"] = [now - 20]
        assert limiter.cull_expired(now=now) == 1
        assert "impersonate_1" not in limiter.requests
        assert "login_ip_1" in limiter.requests

    def test_redis_receives_policy_limit_and_window(self, policies):
        policies["refresh_ip"] = RateLimitPolicy(limit=3, window_seconds=45)
        limiter = RedisRateLimiter()
        limiter.client = ScriptedClient()
        limiter.is_allowed("refresh_ip_1")
        assert limiter.client.calls == [("auth_rate_limiter:refresh_ip_1", 45, 3)]

    def test_ledger_receives_policy(self, policies, monkeypatch):
        seen = []
        monkeypatch.setattr(rl_mod, "record_rate_limit_attempt", lambda db, key, limit, window: seen.append((limit, window)) or True)
        policies["register_ip"] = RateLimitPolicy(limit=4, window_seconds=30)
        monkeypatch.setattr(settings, "USE_REDIS_RATE_LIMITER", False)
        SimpleRateLimiter().is_allowed("register_ip_1", db=object())
        assert seen == [(4, 30)]

    def test_db_path_uses_redis_after_ledger(self, monkeypatch):
        monkeypatch.setattr(rl_mod, "record_rate_limit_attempt", lambda *a: True)
        limiter = RedisRateLimiter()
        limiter.client = ScriptedClient([0])
        assert limiter.is_allowed("login_ip_1", db=object()) is False


# Prometheus metrics ---------------------------------------------------------


class TestMetrics:
    def test_memory_decisions_are_counted_by_scope_and_outcome(self, policies):
        policies["impersonate"] = RateLimitPolicy(limit=1, window_seconds=60)
        allowed = counter("rate_limiter_requests_total", backend="memory", scope="impersonate", outcome="allowed")
        rejected = counter("rate_limiter_requests_total", backend="memory", scope="impersonate", outcome="rejected")

        limiter = SimpleRateLimiter()
        limiter.is_allowed("impersonate_1")
        limiter.is_allowed("impersonate_1")

        assert counter("rate_limiter_requests_total", backend="memory", scope="impersonate", outcome="allowed") == allowed + 1
        assert counter("rate_limiter_requests_total", backend="memory", scope="impersonate", outcome="rejected") == rejected + 1

    def test_redis_decisions_are_counted(self):
        before = counter("rate_limiter_requests_total", backend="redis", scope="login_ip", outcome="rejected")
        limiter = RedisRateLimiter()
        limiter.client = ScriptedClient([0])
        limiter.is_allowed("login_ip_5.5.5.5")
        assert counter("rate_limiter_requests_total", backend="redis", scope="login_ip", outcome="rejected") == before + 1

    def test_async_redis_decisions_are_counted(self):
        before = counter("rate_limiter_requests_total", backend="redis", scope="refresh_ip", outcome="allowed")
        limiter = RedisRateLimiter()
        limiter.async_client = AsyncScriptedClient([1])
        assert asyncio.run(limiter.is_allowed_async("refresh_ip_1")) is True
        assert counter("rate_limiter_requests_total", backend="redis", scope="refresh_ip", outcome="allowed") == before + 1

    def test_ledger_decisions_are_counted(self, monkeypatch):
        monkeypatch.setattr(rl_mod, "record_rate_limit_attempt", lambda *a: False)
        before = counter("rate_limiter_requests_total", backend="ledger", scope="register_ip", outcome="rejected")
        RedisRateLimiter().is_allowed("register_ip_1", db=object())
        assert counter("rate_limiter_requests_total", backend="ledger", scope="register_ip", outcome="rejected") == before + 1

    def test_redis_failure_counts_fallback_and_opens_circuit(self):
        before = counter("rate_limiter_fallback_total", reason="redis_error")
        limiter = RedisRateLimiter()
        limiter.client = FailingClient()
        assert limiter.is_allowed("login_ip_1") is True
        assert counter("rate_limiter_fallback_total", reason="redis_error") == before + 1
        assert gauge("rate_limiter_circuit_open") == 1

    def test_client_identifier_never_becomes_a_label(self):
        limiter = SimpleRateLimiter()
        limiter.is_allowed("login_ip_203.0.113.77")
        assert not any("203.0.113.77" in key for key in metrics.get_metrics_summary()["counters"])

    def test_cull_reports_culled_keys_and_size(self):
        limiter = SimpleRateLimiter()
        limiter.requests["login_ip_old"] = [0.0]
        before = counter("rate_limiter_keys_culled_total")
        limiter.cull_expired()
        assert counter("rate_limiter_keys_culled_total") == before + 1
        assert gauge("rate_limiter_memory_keys") == len(limiter.requests)

    def test_metrics_reach_prometheus_endpoint(self):
        from app.api.v1.endpoints.metrics import get_prometheus_metrics

        SimpleRateLimiter().is_allowed("login_ip_1")
        response = get_prometheus_metrics(current_user=None)
        body = response.body.decode() if hasattr(response, "body") else str(response)
        assert "rate_limiter_requests_total" in body


# OpenAPI / Swagger documentation --------------------------------------------


class TestOpenAPI:
    def test_merge_keeps_endpoint_description_and_adds_schema(self):
        merged = with_rate_limit_responses({200: {"description": "ok"}, 429: {"description": "custom"}})
        assert merged[200] == {"description": "ok"}
        assert merged[429]["description"] == "custom"
        assert merged[429]["content"] == RATE_LIMITED_RESPONSE[429]["content"]

    def test_merge_adds_default_429(self):
        assert with_rate_limit_responses()[429] == RATE_LIMITED_RESPONSE[429]

    @pytest.mark.parametrize(
        ("path", "method"),
        [
            ("/api/v1/auth/register", "post"),
            ("/api/v1/auth/login", "post"),
            ("/api/v1/auth/refresh", "post"),
            ("/api/v1/auth/me/profile", "patch"),
            ("/api/v1/auth/impersonate", "post"),
        ],
    )
    def test_rate_limited_endpoints_document_429(self, path, method):
        from app.main import app

        app.openapi_schema = None
        operation = app.openapi()["paths"][path][method]
        response = operation["responses"]["429"]
        assert response["description"]
        example = response["content"]["application/problem+json"]["example"]
        assert example["error_code"] == "rate_limited"
        assert example["status"] == 429
