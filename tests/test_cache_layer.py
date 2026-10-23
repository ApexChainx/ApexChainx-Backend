"""Tests for CacheLayer Prometheus metrics (#770), query optimizations (#771),
cache invalidation propagation (#1372), and cache warmup (#1415)."""

import json
from unittest.mock import Mock

import pytest

from app.services.wallet_cache import WalletCache
from app.services.sla_cache import SLACache
from app.utils.cache import TTLCache
from app.services.metrics import metrics


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reset_metrics() -> None:
    """Reset the in-process metrics registry between tests."""
    metrics._counters.clear()
    metrics._gauges.clear()
    metrics._histograms.clear()
    metrics._timers.clear()


# ---------------------------------------------------------------------------
# #770 – Prometheus metrics on WalletCache
# ---------------------------------------------------------------------------

class TestWalletCachePrometheusMetrics:
    """Verify WalletCache emits the expected Prometheus counters and timers."""

    def setup_method(self) -> None:
        _reset_metrics()
        self.redis = Mock()
        self.cache = WalletCache(self.redis, ttl=60)

    def test_get_records_hit_counter_on_cache_hit(self) -> None:
        self.redis.get.return_value = json.dumps({"address": "GABCD"})
        result = self.cache.get("GABCD")
        assert result == {"address": "GABCD"}
        assert metrics._counters["cache_wallet_hit_total{operation=\"get\",result=\"hit\"}"] == 1.0
        assert metrics._counters["cache_wallet_get_total{operation=\"get\",result=\"hit\"}"] == 1.0

    def test_get_records_miss_counter_on_cache_miss(self) -> None:
        self.redis.get.return_value = None
        result = self.cache.get("GABCD")
        assert result is None
        assert metrics._counters["cache_wallet_miss_total{operation=\"get\",result=\"miss\"}"] == 1.0
        assert metrics._counters["cache_wallet_get_total{operation=\"get\",result=\"miss\"}"] == 1.0

    def test_get_records_error_on_redis_failure(self) -> None:
        self.redis.get.side_effect = RuntimeError("connection refused")
        result = self.cache.get("GABCD")
        assert result is None
        assert metrics._counters["cache_wallet_error_total{operation=\"get\",error=\"redis_error\"}"] == 1.0
        assert metrics._counters["cache_wallet_get_total{operation=\"get\",result=\"error\"}"] == 1.0

    def test_set_records_set_counter(self) -> None:
        self.cache.set("GABCD", {"address": "GABCD"})
        assert metrics._counters["cache_wallet_set_total{operation=\"set\"}"] == 1.0

    def test_set_records_error_on_redis_failure(self) -> None:
        self.redis.setex.side_effect = RuntimeError("connection refused")
        self.cache.set("GABCD", {"address": "GABCD"})
        assert metrics._counters["cache_wallet_error_total{operation=\"set\",error=\"redis_error\"}"] == 1.0

    def test_invalidate_records_invalidate_counter(self) -> None:
        self.cache.invalidate("GABCD")
        assert metrics._counters["cache_wallet_invalidate_total{operation=\"invalidate\"}"] == 1.0

    def test_invalidate_records_error_on_redis_failure(self) -> None:
        self.redis.delete.side_effect = RuntimeError("connection refused")
        self.cache.invalidate("GABCD")
        assert metrics._counters["cache_wallet_error_total{operation=\"invalidate\",error=\"redis_error\"}"] == 1.0

    def test_warmup_populates_cache_and_counts(self) -> None:
        rows = [("GABCD", {"address": "GABCD"}), ("GBCDE", {"address": "GBCDE"})]
        count = self.cache.warmup(rows)
        assert count == 2
        assert metrics._counters["cache_wallet_warmup_total{operation=\"warmup\",result=\"ok\"}"] == 2.0
        assert self.redis.setex.call_count == 2

    def test_warmup_counts_errors(self) -> None:
        self.redis.setex.side_effect = RuntimeError("fail")
        count = self.cache.warmup([("GABCD", {})])
        assert count == 0
        assert metrics._counters["cache_wallet_warmup_total{operation=\"warmup\",result=\"error\"}"] == 1.0

    def test_stats_returns_ttl_and_class(self) -> None:
        stats = self.cache.get_stats()
        assert stats["ttl"] == 60
        assert stats["class"] == "WalletCache"


# ---------------------------------------------------------------------------
# #770 – Prometheus metrics on SLACache
# ---------------------------------------------------------------------------

class TestSLACachePrometheusMetrics:
    """Verify SLACache emits the expected Prometheus counters and timers."""

    def setup_method(self) -> None:
        _reset_metrics()
        self.redis = Mock()
        self.cache = SLACache(self.redis, ttl=60)

    def test_get_records_hit_on_cache_hit(self) -> None:
        self.redis.get.return_value = json.dumps({"result": "ok"})
        result = self.cache.get("device-1", "2024-01")
        assert result == {"result": "ok"}
        assert metrics._counters["cache_sla_hit_total{operation=\"get\",result=\"hit\"}"] == 1.0

    def test_get_records_miss_on_cache_miss(self) -> None:
        self.redis.get.return_value = None
        result = self.cache.get("device-1", "2024-01")
        assert result is None
        assert metrics._counters["cache_sla_miss_total{operation=\"get\",result=\"miss\"}"] == 1.0

    def test_get_or_compute_hits_computes_and_caches_on_miss(self) -> None:
        self.redis.get.return_value = None
        computed = {"result": "computed"}
        result = self.cache.get_or_compute("device-1", "2024-01", lambda: computed)
        assert result == computed
        assert self.redis.setex.called
        assert metrics._counters["cache_sla_compute_total{operation=\"compute\",result=\"miss_triggered\"}"] == 1.0

    def test_get_or_compute_returns_cached_without_computing_on_hit(self) -> None:
        self.redis.get.return_value = json.dumps({"result": "cached"})
        compute_called = []
        result = self.cache.get_or_compute(
            "device-1", "2024-01", lambda: compute_called.append(True) or {"x": 1}
        )
        assert result == {"result": "cached"}
        assert not compute_called

    def test_invalidate_by_device_deletes_matching_keys(self) -> None:
        self.redis.scan.side_effect = [(0, [b"sla:device-1:2024-01", b"sla:device-1:2024-02"])]
        self.redis.delete.return_value = 2
        deleted = self.cache.invalidate_by_device("device-1")
        assert deleted == 2
        assert metrics._counters["cache_sla_invalidate_device_total{operation=\"invalidate_device\",deleted=\"2\"}"] == 1.0 or metrics._counters["cache_sla_invalidate_device_total{deleted=\"2\",operation=\"invalidate_device\"}"] == 1.0

    def test_warmup_populates_and_counts(self) -> None:
        rows = [("device-1", "2024-01", {"v": 1}), ("device-2", "2024-01", {"v": 2})]
        count = self.cache.warmup(rows)
        assert count == 2
        assert metrics._counters["cache_sla_warmup_total{operation=\"warmup\",result=\"ok\"}"] == 2.0

    def test_stats_returns_ttl_and_class(self) -> None:
        stats = self.cache.get_stats()
        assert stats["ttl"] == 60
        assert stats["class"] == "SLACache"


# ---------------------------------------------------------------------------
# #770 – Prometheus metrics on TTLCache (in-process)
# ---------------------------------------------------------------------------

class TestTTLCachePrometheusMetrics:
    """Verify TTLCache emits the expected Prometheus counters and gauges."""

    def setup_method(self) -> None:
        _reset_metrics()
        self.cache = TTLCache(ttl_seconds=30)

    def test_get_records_hit(self) -> None:
        self.cache.set("k1", "v1")
        result = self.cache.get("k1")
        assert result == "v1"
        assert metrics._counters["cache_inprocess_hit_total{operation=\"get\",result=\"hit\"}"] == 1.0
        assert metrics._gauges["cache_inprocess_entries"] == 1.0

    def test_get_records_miss_on_unknown_key(self) -> None:
        result = self.cache.get("nope")
        assert result is None
        assert metrics._counters["cache_inprocess_miss_total{operation=\"get\",result=\"miss\"}"] == 1.0

    def test_get_records_expired_on_ttl_expiry(self) -> None:
        # Use a negative TTL to force immediate expiry
        expired = TTLCache(ttl_seconds=-1)
        expired.set("k1", "v1")
        result = expired.get("k1")
        assert result is None
        assert metrics._counters["cache_inprocess_expired_total{operation=\"get\",result=\"expired\"}"] == 1.0
        assert metrics._counters["cache_inprocess_miss_total{operation=\"get\",result=\"expired\"}"] == 1.0

    def test_set_records_set_counter(self) -> None:
        self.cache.set("k1", "v1")
        assert metrics._counters["cache_inprocess_set_total{operation=\"set\"}"] == 1.0
        assert metrics._gauges["cache_inprocess_entries"] == 1.0

    def test_invalidate_records_invalidate_counter(self) -> None:
        self.cache.set("k1", "v1")
        self.cache.invalidate("k1")
        assert metrics._counters["cache_inprocess_invalidate_total{operation=\"invalidate\"}"] == 1.0
        assert metrics._gauges["cache_inprocess_entries"] == 0.0

    def test_invalidate_prefix_records_counter_with_removed_count(self) -> None:
        self.cache.set("pref_a", 1)
        self.cache.set("pref_b", 2)
        self.cache.set("other", 3)
        self.cache.invalidate_prefix("pref_")
        assert metrics._counters["cache_inprocess_invalidate_prefix_total{operation=\"invalidate_prefix\",removed=\"2\"}"] == 1.0
        assert metrics._gauges["cache_inprocess_entries"] == 1.0

    def test_warmup_populates_and_counts(self) -> None:
        count = self.cache.warmup({"a": 1, "b": 2, "c": 3})
        assert count == 3
        assert metrics._counters["cache_inprocess_warmup_total{operation=\"warmup\",result=\"ok\",count=\"3\"}"] == 1.0 or metrics._counters["cache_inprocess_warmup_total{count=\"3\",operation=\"warmup\",result=\"ok\"}"] == 1.0
        assert metrics._gauges["cache_inprocess_entries"] == 3.0

    def test_stats_returns_ttl_entries_and_class(self) -> None:
        self.cache.set("x", 1)
        stats = self.cache.get_stats()
        assert stats["ttl"] == 30
        assert stats["entries"] == 1
        assert stats["class"] == "TTLCache"


# ---------------------------------------------------------------------------
# #771 – WalletRepository batch query optimization
# ---------------------------------------------------------------------------

class TestWalletRepositoryBatchQuery:
    """Verify the batch get_by_user_id_or_public_key reduces round-trips (#771)."""

    def test_batch_query_returns_both_none_when_no_match(self) -> None:
        from app.repositories.wallet_repository import WalletRepository

        mock_db = Mock()
        mock_db.query.return_value.filter.return_value.all.return_value = []
        repo = WalletRepository(mock_db)

        by_user, by_key = repo.get_by_user_id_or_public_key("user-1", "GABCD")
        assert by_user is None
        assert by_key is None

    def test_batch_query_returns_matching_user(self) -> None:
        from app.repositories.wallet_repository import WalletRepository
        from app.models.orm.wallet import WalletORM

        mock_db = Mock()
        wallet = WalletORM(user_id="user-1", public_key="GABCD")
        mock_db.query.return_value.filter.return_value.all.return_value = [wallet]
        repo = WalletRepository(mock_db)

        by_user, by_key = repo.get_by_user_id_or_public_key("user-1", "GBCDE")
        assert by_user is wallet
        assert by_key is None

    def test_batch_query_returns_matching_address(self) -> None:
        from app.repositories.wallet_repository import WalletRepository
        from app.models.orm.wallet import WalletORM

        mock_db = Mock()
        wallet = WalletORM(user_id="user-1", public_key="GABCD")
        mock_db.query.return_value.filter.return_value.all.return_value = [wallet]
        repo = WalletRepository(mock_db)

        by_user, by_key = repo.get_by_user_id_or_public_key("user-99", "GABCD")
        assert by_user is None
        assert by_key is wallet

    def test_batch_query_returns_both_when_different_rows(self) -> None:
        from app.repositories.wallet_repository import WalletRepository
        from app.models.orm.wallet import WalletORM

        mock_db = Mock()
        wallet_user = WalletORM(user_id="user-1", public_key="GBCDE")
        wallet_key = WalletORM(user_id="user-2", public_key="GABCD")
        mock_db.query.return_value.filter.return_value.all.return_value = [wallet_user, wallet_key]
        repo = WalletRepository(mock_db)

        by_user, by_key = repo.get_by_user_id_or_public_key("user-1", "GABCD")
        assert by_user is wallet_user
        assert by_key is wallet_key


# ---------------------------------------------------------------------------
# #771 – WalletRegistry uses batch query in link_wallet
# ---------------------------------------------------------------------------

class TestWalletRegistryLinkUsesBatchQuery:
    """Verify link_wallet uses the batch query path (#771)."""

    def test_link_wallet_calls_batch_query(self) -> None:
        from app.services.wallet_registry import WalletRegistry
        from app.repositories.wallet_repository import WalletRepository
        from app.models.wallet import WalletLinkRequest

        mock_repo = Mock(spec=WalletRepository)
        mock_repo.get_by_user_id_or_public_key.return_value = (None, None)
        mock_repo.create.return_value = Mock(
            user_id="user-1",
            public_key="GABCD",
            created_at=None,
            last_updated=None,
            funded=False,
            active=True,
            trustline_ready=False,
            cached_at=None,
        )

        # Patch the repository constructor used inside WalletRegistry
        import app.services.wallet_registry as wr_module
        original = wr_module.WalletRepository
        wr_module.WalletRepository = lambda db: mock_repo

        try:
            payload = WalletLinkRequest(user_id="user-1", public_key="GABCD")
            result = WalletRegistry.link_wallet(Mock(), payload)
            assert mock_repo.get_by_user_id_or_public_key.called
            assert not mock_repo.get_by_user_id.called
            assert not mock_repo.get_by_public_key.called
        finally:
            wr_module.WalletRepository = original


# ---------------------------------------------------------------------------
# #1372 – Cache invalidation propagation
# ---------------------------------------------------------------------------

class TestCacheInvalidationPropagation:
    """Verify device-level invalidation propagates to all matching entries (#1372)."""

    def test_sla_invalidate_by_device_deletes_all_device_keys(self) -> None:
        redis = Mock()
        redis.scan.side_effect = [
            (0, [b"sla:dev-1:2024-01", b"sla:dev-1:2024-02", b"sla:dev-1:2024-03"]),
        ]
        redis.delete.return_value = 3
        cache = SLACache(redis)
        deleted = cache.invalidate_by_device("dev-1")
        assert deleted == 3

    def test_sla_invalidate_by_device_handles_paginated_scan(self) -> None:
        redis = Mock()
        redis.scan.side_effect = [
            (123, [b"sla:dev-1:2024-01"]),
            (0, [b"sla:dev-1:2024-02"]),
        ]
        redis.delete.return_value = 1
        cache = SLACache(redis)
        deleted = cache.invalidate_by_device("dev-1")
        assert deleted == 2


# ---------------------------------------------------------------------------
# #1415 – Cache warmup
# ---------------------------------------------------------------------------

class TestCacheWarmup:
    """Verify bulk warmup paths work correctly (#1415)."""

    def test_ttl_warmup_populates_all_keys(self) -> None:
        cache = TTLCache(ttl_seconds=60)
        count = cache.warmup({"a": 1, "b": 2})
        assert count == 2
        assert cache.get("a") == 1
        assert cache.get("b") == 2

    def test_wallet_cache_warmup_batches_redis_calls(self) -> None:
        redis = Mock()
        cache = WalletCache(redis, ttl=120)
        rows = [("G1", {"a": 1}), ("G2", {"b": 2})]
        count = cache.warmup(rows, ttl_override=300)
        assert count == 2
        assert redis.setex.call_count == 2
        # Verify TTL override was applied
        for call in redis.setex.call_args_list:
            assert call[0][1] == 300

    def test_sla_cache_warmup_batches_redis_calls(self) -> None:
        redis = Mock()
        cache = SLACache(redis, ttl=60)
        rows = [("d1", "p1", {"v": 1}), ("d2", "p2", {"v": 2})]
        count = cache.warmup(rows)
        assert count == 2
        assert redis.setex.call_count == 2
