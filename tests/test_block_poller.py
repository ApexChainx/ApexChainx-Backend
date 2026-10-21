"""Tests for BlockPoller rate limiting, sanitization, refactoring, and retries."""

import random
import re
import threading

import pytest

from app.core.config import settings, validate_critical_settings
from app.services.block_poller import (
    BlockPoller,
    BlockPollerFetchError,
    BlockPollerValidationError,
    PollStatus,
    SlidingWindowRateLimiter,
    compute_retry_delay,
    sanitize_block_payload,
)


class TestInputSanitization:
    def test_accepts_and_normalizes_valid_block(self):
        block = sanitize_block_payload(
            {
                "sequence": " 42 ",
                "hash": "  abc123  ",
                "transactions": [{"hash": "tx-1", "memo": "hello"}],
            }
        )

        assert block["sequence"] == 42
        assert block["hash"] == "abc123"
        assert block["transactions"][0]["hash"] == "tx-1"

    def test_uses_ledger_field_when_sequence_missing(self):
        assert sanitize_block_payload({"ledger": 7})["sequence"] == 7

    def test_strips_control_characters_from_strings(self):
        block = sanitize_block_payload({"sequence": 1, "note": "hello\x01world"})
        assert block["note"] == "helloworld"

    @pytest.mark.parametrize(
        "payload",
        [
            None,
            [],
            "not-a-block",
            {"hash": "missing-sequence"},
            {"sequence": True},
            {"sequence": -1},
            {"sequence": 1.5},
            {"sequence": "not-a-number"},
            {"sequence": 2**63},
        ],
    )
    def test_rejects_invalid_payloads(self, payload):
        with pytest.raises(BlockPollerValidationError):
            sanitize_block_payload(payload)

    def test_rejects_null_bytes(self):
        with pytest.raises(BlockPollerValidationError, match="null bytes"):
            sanitize_block_payload({"sequence": 1, "hash": "abc\x00"})

    def test_rejects_invalid_identifier_characters(self):
        with pytest.raises(BlockPollerValidationError, match="invalid characters"):
            sanitize_block_payload({"sequence": 1, "transactions": [{"hash": "bad hash"}]})

    def test_rejects_non_mapping_transactions(self):
        with pytest.raises(BlockPollerValidationError, match="list"):
            sanitize_block_payload({"sequence": 1, "transactions": "nope"})

    def test_rejects_transaction_count_over_limit(self, monkeypatch):
        monkeypatch.setattr(settings, "BLOCK_POLLER_MAX_TRANSACTIONS", 1)
        with pytest.raises(BlockPollerValidationError, match="more than 1"):
            sanitize_block_payload({"sequence": 1, "transactions": [{"hash": "a"}, {"hash": "b"}]})

    def test_rejects_oversized_payload(self, monkeypatch):
        monkeypatch.setattr(settings, "BLOCK_POLLER_MAX_PAYLOAD_BYTES", 10)
        with pytest.raises(BlockPollerValidationError, match="maximum size"):
            sanitize_block_payload({"sequence": 1, "data": "x" * 100})

    def test_rejects_oversized_string(self, monkeypatch):
        monkeypatch.setattr(settings, "BLOCK_POLLER_MAX_STRING_LENGTH", 3)
        with pytest.raises(BlockPollerValidationError, match="maximum length"):
            sanitize_block_payload({"sequence": 1, "note": "too long"})

    def test_rejects_non_finite_floats(self):
        with pytest.raises(BlockPollerValidationError, match="finite"):
            sanitize_block_payload({"sequence": 1, "value": float("nan")})

    def test_rejects_unsupported_types(self):
        with pytest.raises(BlockPollerValidationError, match="JSON serializable"):
            sanitize_block_payload({"sequence": 1, "value": object()})

    def test_rejects_non_string_keys(self):
        with pytest.raises(BlockPollerValidationError, match="keys must be strings"):
            sanitize_block_payload({"sequence": 1, 3: "value"})

    def test_rejects_keys_that_collide_after_sanitization(self):
        with pytest.raises(BlockPollerValidationError, match="duplicate keys"):
            sanitize_block_payload({"sequence": 1, "a": 1, "a\x01": 2})

    def test_rejects_excessive_nesting(self, monkeypatch):
        monkeypatch.setattr(settings, "BLOCK_POLLER_MAX_DEPTH", 1)
        with pytest.raises(BlockPollerValidationError, match="nesting depth"):
            sanitize_block_payload({"sequence": 1, "nested": {"deeper": {"value": 1}}})


class TestRetryWithBackoff:
    def test_compute_retry_delay_is_exponential_and_capped(self):
        assert compute_retry_delay(0, base_delay=1, max_delay=30) == 1
        assert compute_retry_delay(1, base_delay=1, max_delay=30) == 2
        assert compute_retry_delay(2, base_delay=1, max_delay=30) == 4
        assert compute_retry_delay(10, base_delay=1, max_delay=30) == 30

    def test_compute_retry_delay_applies_bounded_jitter(self):
        generator = random.Random(1234)
        delays = [
            compute_retry_delay(3, base_delay=1, max_delay=30, jitter_ratio=0.5, rng=generator) for _ in range(50)
        ]
        assert all(4 <= delay <= 8 for delay in delays)

    def test_compute_retry_delay_rejects_negative_attempt(self):
        with pytest.raises(ValueError):
            compute_retry_delay(-1, base_delay=1, max_delay=30)

    def test_poller_retries_transient_failures_then_succeeds(self):
        attempts: list[int] = []
        sleeps: list[float] = []

        def fetch(sequence):
            attempts.append(sequence)
            if len(attempts) < 3:
                raise ConnectionError("upstream unavailable")
            return {"sequence": sequence}

        poller = BlockPoller(
            fetch,
            lambda block: None,
            max_retries=3,
            retry_base_delay_seconds=1,
            retry_max_delay_seconds=8,
            retry_jitter_ratio=0,
            sleep=sleeps.append,
        )

        result = poller.poll_once()

        assert result.status == PollStatus.PROCESSED
        assert result.attempts == 3
        assert sleeps == [1, 2]
        assert poller.next_sequence == 2

    def test_poller_raises_after_exhausting_retries(self):
        sleeps: list[float] = []

        def fetch(sequence):
            raise TimeoutError("still down")

        poller = BlockPoller(
            fetch,
            lambda block: None,
            max_retries=1,
            retry_base_delay_seconds=0.5,
            retry_max_delay_seconds=4,
            retry_jitter_ratio=0,
            sleep=sleeps.append,
        )

        with pytest.raises(BlockPollerFetchError, match="after 2 attempts"):
            poller.poll_once()
        assert sleeps == [0.5]

    def test_validation_errors_are_not_retried(self):
        calls: list[int] = []

        def fetch(sequence):
            calls.append(sequence)
            return {"hash": "missing-sequence"}

        poller = BlockPoller(fetch, lambda block: None, max_retries=5, sleep=lambda delay: pytest.fail("slept"))

        with pytest.raises(BlockPollerValidationError):
            poller.poll_once()
        assert calls == [1]


class TestRateLimiting:
    def test_sliding_window_allows_limit_then_rejects(self):
        now = [100.0]
        limiter = SlidingWindowRateLimiter(2, 1.0, clock=lambda: now[0])

        assert limiter.try_acquire() is True
        assert limiter.try_acquire() is True
        assert limiter.try_acquire() is False

        now[0] += 1.1
        assert limiter.try_acquire() is True

    def test_poller_skips_fetch_when_rate_limited(self):
        now = [100.0]
        fetched: list[int] = []

        def fetch(sequence):
            fetched.append(sequence)
            return {"sequence": sequence}

        limiter = SlidingWindowRateLimiter(2, 1.0, clock=lambda: now[0])
        poller = BlockPoller(fetch, lambda block: None, rate_limiter=limiter)

        assert poller.poll_once().status == PollStatus.PROCESSED
        assert poller.poll_once().status == PollStatus.PROCESSED
        assert poller.poll_once().status == PollStatus.RATE_LIMITED
        assert fetched == [1, 2]

        now[0] += 1.1
        assert poller.poll_once().status == PollStatus.PROCESSED
        assert fetched == [1, 2, 3]


class TestRefactoredPollingFlow:
    def test_processes_sanitized_block_and_advances_cursor(self):
        received = []
        poller = BlockPoller(
            lambda sequence: {"sequence": str(sequence), "hash": " block-hash "},
            received.append,
        )

        result = poller.poll_once()

        assert result.block == {"sequence": 1, "hash": "block-hash"}
        assert received == [{"sequence": 1, "hash": "block-hash"}]
        assert poller.next_sequence == 2

    def test_processing_failure_does_not_advance_cursor(self):
        def process(block):
            raise RuntimeError("processor failed")

        poller = BlockPoller(lambda sequence: {"sequence": sequence}, process)

        with pytest.raises(RuntimeError):
            poller.poll_once()
        assert poller.next_sequence == 1

    def test_run_forever_stops_when_event_is_set(self):
        stop = threading.Event()
        processed = []

        def process(block):
            processed.append(block["sequence"])
            stop.set()

        poller = BlockPoller(
            lambda sequence: {"sequence": sequence},
            process,
            poll_interval_seconds=10,
        )

        poller.run_forever(stop)

        assert processed == [1]

    @pytest.mark.parametrize(
        ("kwargs", "message"),
        [
            ({"start_sequence": -1}, "start_sequence"),
            ({"poll_interval_seconds": 0}, "poll_interval_seconds"),
            ({"max_retries": -1}, "max_retries"),
            ({"retry_base_delay_seconds": -1}, "retry_base_delay_seconds"),
            ({"retry_max_delay_seconds": 0}, "retry_max_delay_seconds"),
            ({"retry_jitter_ratio": 1.5}, "retry_jitter_ratio"),
        ],
    )
    def test_rejects_invalid_configuration(self, kwargs, message):
        with pytest.raises(ValueError, match=message):
            BlockPoller(lambda sequence: {"sequence": sequence}, lambda block: None, **kwargs)


class TestBlockPollerConfigValidation:
    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("BLOCK_POLLER_POLL_INTERVAL_SECONDS", 0),
            ("BLOCK_POLLER_MAX_RETRIES", -1),
            ("BLOCK_POLLER_RETRY_BASE_DELAY_SECONDS", -1),
            ("BLOCK_POLLER_RETRY_MAX_DELAY_SECONDS", 0),
            ("BLOCK_POLLER_RETRY_JITTER_RATIO", 1.5),
            ("BLOCK_POLLER_RATE_LIMIT_REQUESTS", 0),
            ("BLOCK_POLLER_RATE_LIMIT_WINDOW_SECONDS", 0),
            ("BLOCK_POLLER_MAX_PAYLOAD_BYTES", 0),
            ("BLOCK_POLLER_MAX_TRANSACTIONS", 0),
            ("BLOCK_POLLER_MAX_IDENTIFIER_LENGTH", 0),
            ("BLOCK_POLLER_MAX_STRING_LENGTH", 0),
            ("BLOCK_POLLER_MAX_COLLECTION_ITEMS", 0),
            ("BLOCK_POLLER_MAX_DEPTH", 0),
        ],
    )
    def test_invalid_block_poller_settings_fail_fast(self, field, value):
        config = settings.model_copy(update={field: value})

        with pytest.raises(ValueError, match=re.escape(field)):
            validate_critical_settings(config)
