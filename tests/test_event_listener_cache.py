"""Tests for the event-listener subscriber cache (issue #740).

The cache exists so that a burst of events does not re-read the whole
``webhooks`` table for every emission.  Two properties matter and are pinned
here: a degraded Redis must never break the fan-out, and the cached entry must
never outlive the registration it describes.
"""

import json

from redis.exceptions import ConnectionError as RedisConnectionError

from app.services.event_listener_cache import EventListenerCache


class FakeRedis:
    """A minimal Redis stand-in that keeps everything in memory."""

    def __init__(self):
        self.store = {}
        self.ttls = {}

    def get(self, key):
        return self.store.get(key)

    def setex(self, key, ttl, value):
        self.store[key] = value.encode("utf-8") if isinstance(value, str) else value
        self.ttls[key] = ttl

    def delete(self, key):
        return 1 if self.store.pop(key, None) is not None else 0

    def scan_iter(self, match=None):
        prefix = (match or "*").rstrip("*")
        return [key for key in list(self.store) if key.startswith(prefix)]


class BrokenRedis:
    """A Redis client whose every command fails."""

    def __init__(self):
        self.calls = 0

    def get(self, key):
        self.calls += 1
        raise RedisConnectionError("redis is down")

    def setex(self, key, ttl, value):
        self.calls += 1
        raise RedisConnectionError("redis is down")

    def delete(self, key):
        self.calls += 1
        raise RedisConnectionError("redis is down")


def test_a_miss_returns_none():
    cache = EventListenerCache(FakeRedis())

    assert cache.get_subscriber_ids("sla.violation") is None


def test_subscriber_ids_round_trip():
    cache = EventListenerCache(FakeRedis())

    cache.set_subscriber_ids("sla.violation", ["a", "b"])

    assert cache.get_subscriber_ids("sla.violation") == ["a", "b"]


def test_ids_are_stored_as_strings():
    cache = EventListenerCache(FakeRedis())

    cache.set_subscriber_ids("sla.violation", [1, 2])

    assert cache.get_subscriber_ids("sla.violation") == ["1", "2"]


def test_a_corrupt_entry_is_treated_as_a_miss():
    redis = FakeRedis()
    redis.setex("event_listener:subscribers:sla.violation", 15, "not json")
    cache = EventListenerCache(redis)

    assert cache.get_subscriber_ids("sla.violation") is None


def test_an_entry_that_is_not_a_list_is_treated_as_a_miss():
    redis = FakeRedis()
    redis.setex("event_listener:subscribers:sla.violation", 15, json.dumps({"a": 1}))
    cache = EventListenerCache(redis)

    assert cache.get_subscriber_ids("sla.violation") is None


def test_an_unreachable_redis_is_a_miss_not_a_failure():
    cache = EventListenerCache(BrokenRedis())

    assert cache.get_subscriber_ids("sla.violation") is None


def test_a_repeated_redis_failure_stops_the_cache_from_retrying():
    redis = BrokenRedis()
    cache = EventListenerCache(redis, failure_threshold=2, failure_cooldown_seconds=60)

    cache.get_subscriber_ids("sla.violation")
    cache.get_subscriber_ids("sla.violation")
    calls_after_the_threshold = redis.calls

    # The cache is disabled for the cooldown, so it stops paying for a
    # connection attempt on every single event.
    cache.get_subscriber_ids("sla.violation")

    assert redis.calls == calls_after_the_threshold


def test_invalidate_removes_a_single_event():
    redis = FakeRedis()
    cache = EventListenerCache(redis)
    cache.set_subscriber_ids("sla.violation", ["a"])
    cache.set_subscriber_ids("sla.resolved", ["b"])

    cache.invalidate("sla.violation")

    assert cache.get_subscriber_ids("sla.violation") is None
    assert cache.get_subscriber_ids("sla.resolved") == ["b"]


def test_invalidate_all_removes_every_cached_event():
    redis = FakeRedis()
    cache = EventListenerCache(redis)
    cache.set_subscriber_ids("sla.violation", ["a"])
    cache.set_subscriber_ids("sla.resolved", ["b"])

    removed = cache.invalidate_all()

    assert removed == 2
    assert cache.get_subscriber_ids("sla.violation") is None
    assert cache.get_subscriber_ids("sla.resolved") is None
