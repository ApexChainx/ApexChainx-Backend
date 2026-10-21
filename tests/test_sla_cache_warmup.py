"""#566: SLA cache warm-up populates the cache before the first read wave.

After a restart the first wave of SLA reads recomputed everything (the
cold-cache stampede). The warm-up ranks devices by recent outage volume,
precomputes their results, and writes them through the same key/TTL path as
normal reads — so a warmed value is indistinguishable from a computed one.
"""

from datetime import UTC, datetime
from unittest.mock import patch

import fakeredis
import pytest

from app.services.sla_cache import SLACache
from app.services.sla_cache_warmup import warm_sla_cache


@pytest.fixture
def fake_cache():
    return SLACache(fakeredis.FakeRedis(decode_responses=True))


class TestWarmUpWritesThroughReadPath:
    def test_warm_up_uses_the_same_keys_as_reads(self, fake_cache):
        fake_cache.warm_up([("device-1", "2026-09", {"availability_percentage": 100.0})])
        # The read path must hit exactly what warm-up wrote.
        assert fake_cache.get("device-1", "2026-09") == {"availability_percentage": 100.0}

    def test_warmed_entries_expire_like_normal_entries(self, fake_cache):
        fake_cache.warm_up([("device-1", "2026-09", {"a": 1})])
        ttl = fake_cache._redis.ttl("sla:device-1:2026-09")
        assert 0 < ttl <= fake_cache._ttl


class TestWarmSLACache:
    def _db_with_outages(self, device_rows):
        """A minimal fake session answering the device-ranking query."""
        from unittest.mock import MagicMock

        db = MagicMock()

        def _query(*cols):
            query = MagicMock()
            query.filter.return_value.filter.return_value.all.return_value = [(d,) for d in device_rows]
            return query

        db.query.side_effect = _query
        return db

    def test_simulated_restart_then_warm_read(self, fake_cache):
        """The acceptance scenario: restart (empty cache), warm-up runs, the
        first read is served from the cache without recomputing."""
        db = self._db_with_outages(["device-a", "device-a", "device-b"])
        result_payload = {
            "device_id": "device-a",
            "period": "2026-09",
            "availability_percentage": 99.95,
        }
        with (
            patch("app.services.sla_cache_warmup._get_sla_cache", return_value=fake_cache),
            patch("app.services.sla_cache_warmup._compute_for_warmup", return_value=result_payload) as compute,
        ):
            warmed = warm_sla_cache(db, periods=["2026-09"])

        # One entry per device (outage-row counts only drive the ranking),
        # busiest first.
        assert [e["device_id"] for e in warmed] == ["device-a", "device-b"]
        assert compute.call_count == 2

        # After the warm-up, reads are warm — no recompute on the read path.
        assert fake_cache.get("device-a", "2026-09") == result_payload
        assert fake_cache.get("device-b", "2026-09") == result_payload

    def test_ranking_prefers_busiest_devices(self, fake_cache):
        db = self._db_with_outages(["noisy", "noisy", "noisy", "quiet"])
        with (
            patch("app.services.sla_cache_warmup._get_sla_cache", return_value=fake_cache),
            patch("app.services.sla_cache_warmup._compute_for_warmup", return_value={"ok": True}),
        ):
            warmed = warm_sla_cache(db, periods=["2026-09"], limit=1)

        assert [e["device_id"] for e in warmed] == ["noisy"]

    def test_limit_bounds_the_warm_set(self, fake_cache):
        db = self._db_with_outages(["d1", "d2", "d3"])
        with (
            patch("app.services.sla_cache_warmup._get_sla_cache", return_value=fake_cache),
            patch("app.services.sla_cache_warmup._compute_for_warmup", return_value={"ok": True}),
        ):
            warmed = warm_sla_cache(db, periods=["2026-09"], limit=2)

        assert len({e["device_id"] for e in warmed}) == 2

    def test_computation_failure_does_not_abort_warmup(self, fake_cache):
        db = self._db_with_outages(["bad", "good"])
        calls = []

        def _flaky(db, device_id, period):
            calls.append(device_id)
            if device_id == "bad":
                raise RuntimeError("boom")
            return {"device_id": device_id}

        with (
            patch("app.services.sla_cache_warmup._get_sla_cache", return_value=fake_cache),
            patch("app.services.sla_cache_warmup._compute_for_warmup", side_effect=_flaky),
        ):
            warmed = warm_sla_cache(db, periods=["2026-09"])

        assert [e["device_id"] for e in warmed] == ["good"]
        assert len(calls) == 2

    def test_no_cache_means_no_warmup_and_no_queries(self):
        from unittest.mock import MagicMock

        db = MagicMock()
        with patch("app.services.sla_cache_warmup._get_sla_cache", return_value=None):
            assert warm_sla_cache(db) == []
        db.query.assert_not_called()

    def test_default_periods_are_current_and_previous_month(self, fake_cache):
        db = self._db_with_outages(["device-1"])
        compute_calls = []

        def _record(db, device_id, period):
            compute_calls.append(period)
            return {"ok": True}

        with (
            patch("app.services.sla_cache_warmup._get_sla_cache", return_value=fake_cache),
            patch("app.services.sla_cache_warmup._compute_for_warmup", side_effect=_record),
        ):
            warm_sla_cache(db)

        now = datetime.now(UTC)
        assert f"{now.year:04d}-{now.month:02d}" in compute_calls
        assert len(set(compute_calls)) == 2  # current + previous month


class TestRealComputeIntegration:
    def test_compute_for_warmup_returns_serializable_dict(self):
        """The warm-up write path must produce JSON-serializable dicts so it
        can flow through the same set() call the read path uses."""
        from app.services.sla_cache_warmup import _compute_for_warmup
        from app.services.sla_service import SLACalculationResult

        payload = SLACalculationResult(
            device_id="d",
            period="2026-09",
            period_start="2026-09-01T00:00:00+00:00",
            period_end="2026-10-01T00:00:00+00:00",
            total_outages=0,
            violated_outages=0,
            avg_mttr_minutes=0.0,
            availability_percentage=100.0,
            is_violated=False,
            sla_thresholds={"availability": 99.9, "mttr": 60.0},
        ).model_dump()

        with patch("app.services.sla_service.compute_device_sla", return_value=payload):
            import json

            dumped = json.dumps(_compute_for_warmup(None, "d", "2026-09"))  # type: ignore[arg-type]
        assert json.loads(dumped)["device_id"] == "d"
