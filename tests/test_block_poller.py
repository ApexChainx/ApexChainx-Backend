"""Unit and integration coverage for the Horizon ledger poller (#718-#721).

Unit tests inject a fake ``BlockSource`` to pin cursor semantics, the retry
budget, handler-failure behaviour, and structured log fields. Integration tests
wire a real ``HorizonBlockSource`` to an ``httpx.MockTransport`` so the
Horizon query parameters, pagination, and HTTP error classification are
exercised end to end without touching the network.
"""

from __future__ import annotations

import json
import logging
from collections import deque
from typing import Any, Sequence

import httpx
import pytest

from app.services.block_poller import (
    Block,
    BlockFetchError,
    BlockHandlerError,
    BlockPoller,
    HorizonBlockSource,
)


class FakeSource:
    def __init__(self, pages: Sequence[Any]) -> None:
        self.pages = deque(pages)
        self.calls: list[tuple[str | None, int]] = []

    def fetch_blocks(self, *, cursor: str | None, limit: int) -> Sequence[Block]:
        self.calls.append((cursor, limit))
        if not self.pages:
            return []
        item = self.pages.popleft()
        if isinstance(item, BaseException):
            raise item
        return item


class AlwaysFailSource:
    def __init__(self, error: BaseException) -> None:
        self.error = error
        self.calls = 0

    def fetch_blocks(self, *, cursor: str | None, limit: int) -> Sequence[Block]:
        self.calls += 1
        raise self.error


class StopAfterWait:
    """Minimal stand-in for threading.Event that stops after one wait."""

    def __init__(self) -> None:
        self._set = False
        self.waits = 0

    def is_set(self) -> bool:
        return self._set

    def wait(self, timeout: float) -> bool:
        self.waits += 1
        self._set = True
        return True

    def set(self) -> None:
        self._set = True


def _block(height: int) -> Block:
    return Block(cursor=f"cursor-{height}", height=height, payload={"sequence": height})


def _structured_records(caplog: pytest.LogCaptureFixture) -> list[dict[str, Any]]:
    return [json.loads(record.message) for record in caplog.records if record.name == "block_poller"]


class TestBlockPollerUnit:
    def test_processes_blocks_in_order_and_advances_cursor(self):
        first, second = _block(10), _block(11)
        source = FakeSource([[first, second]])
        handled: list[int] = []

        poller = BlockPoller(source, lambda block: handled.append(block.height))
        result = poller.poll_once()

        assert handled == [10, 11]
        assert result.blocks_processed == 2
        assert result.latest_height == 11
        assert result.cursor == "cursor-11"
        assert poller.cursor == "cursor-11"
        assert source.calls == [(None, 50)]

    def test_next_poll_uses_the_previous_cursor(self):
        source = FakeSource([[_block(1)], [_block(2)]])
        poller = BlockPoller(source, batch_size=10)

        poller.poll_once()
        poller.poll_once()

        assert source.calls == [(None, 10), ("cursor-1", 10)]

    def test_empty_page_is_a_success_and_keeps_the_cursor(self):
        source = FakeSource([[_block(5)], []])
        poller = BlockPoller(source)

        poller.poll_once()
        result = poller.poll_once()

        assert result.blocks_processed == 0
        assert result.cursor == "cursor-5"
        assert poller.health()["status"] == "healthy"

    def test_handler_failure_does_not_skip_the_failed_block(self):
        first, second = _block(1), _block(2)
        gap_source = FakeSource([[first, second], [second]])

        def flaky_handler(block: Block) -> None:
            if block.height == 2 and gap_source.calls == [(None, 50)]:
                raise RuntimeError("database unavailable")

        poller = BlockPoller(gap_source, flaky_handler)

        with pytest.raises(BlockHandlerError) as exc_info:
            poller.poll_once()

        assert exc_info.value.block is second
        assert poller.cursor == "cursor-1"
        assert poller.health()["status"] == "degraded"

        result = poller.poll_once()
        assert result.cursor == "cursor-2"
        assert gap_source.calls[1] == ("cursor-1", 50)

    def test_retryable_fetch_errors_use_bounded_exponential_backoff(self):
        delays: list[float] = []
        source = FakeSource(
            [
                BlockFetchError("rate limited", retryable=True, status_code=429),
                BlockFetchError("unavailable", retryable=True, status_code=503),
                [_block(1)],
            ]
        )

        poller = BlockPoller(
            source,
            max_attempts=3,
            retry_base_delay_seconds=0.5,
            retry_max_delay_seconds=4.0,
            sleep=delays.append,
        )
        result = poller.poll_once()

        assert result.attempts == 3
        assert result.blocks_processed == 1
        assert delays == [0.5, 1.0]

    def test_retry_delay_is_capped(self):
        delays: list[float] = []
        source = FakeSource(
            [
                BlockFetchError("boom", retryable=True),
                BlockFetchError("boom", retryable=True),
                BlockFetchError("boom", retryable=True),
                [_block(1)],
            ]
        )

        poller = BlockPoller(
            source,
            max_attempts=4,
            retry_base_delay_seconds=5.0,
            retry_max_delay_seconds=6.0,
            sleep=delays.append,
        )
        poller.poll_once()

        assert delays == [5.0, 6.0, 6.0]

    def test_terminal_fetch_error_is_not_retried(self):
        source = AlwaysFailSource(BlockFetchError("not found", retryable=False, status_code=404))
        poller = BlockPoller(source, max_attempts=5, sleep=lambda _delay: None)

        with pytest.raises(BlockFetchError) as exc_info:
            poller.poll_once()

        assert exc_info.value.retryable is False
        assert source.calls == 1
        assert poller.cursor is None
        assert poller.health()["status"] == "degraded"

    def test_retry_budget_exhaustion_keeps_cursor_and_reports_failures(self):
        source = AlwaysFailSource(BlockFetchError("still down", retryable=True))
        poller = BlockPoller(source, max_attempts=2, sleep=lambda _delay: None)

        with pytest.raises(BlockFetchError, match="still down"):
            poller.poll_once()

        assert source.calls == 2
        assert poller.cursor is None
        health = poller.health()
        assert health["status"] == "degraded"
        assert health["last_error_type"] == "BlockFetchError"
        assert health["consecutive_failures"] == 1

        with pytest.raises(BlockFetchError, match="still down"):
            poller.poll_once()

        assert poller.health()["status"] == "unhealthy"
        assert poller.health()["consecutive_failures"] == 2

    def test_unexpected_source_exception_is_wrapped_and_retried(self):
        delays: list[float] = []
        source = FakeSource([RuntimeError("connection reset"), [_block(1)]])
        poller = BlockPoller(source, max_attempts=2, sleep=delays.append)

        result = poller.poll_once()

        assert result.attempts == 2
        assert delays == [0.5]

    def test_source_returning_non_blocks_is_rejected_without_retry(self):
        source = FakeSource([["not-a-block"]])
        poller = BlockPoller(source, max_attempts=3, sleep=lambda _delay: None)

        with pytest.raises(BlockFetchError, match="non-Block"):
            poller.poll_once()

        assert source.calls == [(None, 50)]

    def test_run_continues_after_fetch_failure_and_stops_cleanly(self):
        source = AlwaysFailSource(BlockFetchError("horizon down", retryable=False))
        stop_event = StopAfterWait()
        poller = BlockPoller(source, max_attempts=1, sleep=lambda _delay: None)

        poller.run(stop_event)  # type: ignore[arg-type]

        assert source.calls == 1
        assert stop_event.waits == 1
        assert poller.running is False

    def test_run_can_be_stopped_from_the_handler(self):
        source = FakeSource([[_block(1)]])
        poller = BlockPoller(source)

        poller._handler = lambda _block: poller.stop()  # type: ignore[assignment]
        poller.run()

        assert source.calls == [(None, 50)]

    @pytest.mark.parametrize(
        ("kwargs", "message"),
        [
            ({"poll_interval_seconds": 0}, "poll_interval_seconds"),
            ({"batch_size": 0}, "batch_size"),
            ({"batch_size": 201}, "batch_size"),
            ({"max_attempts": 0}, "max_attempts"),
            ({"retry_base_delay_seconds": -1}, "retry_base_delay_seconds"),
            (
                {"retry_base_delay_seconds": 2, "retry_max_delay_seconds": 1},
                "retry_max_delay_seconds",
            ),
        ],
    )
    def test_invalid_configuration_is_rejected(self, kwargs, message):
        with pytest.raises(ValueError, match=message):
            BlockPoller(FakeSource([]), **kwargs)

    def test_block_rejects_empty_cursor_and_negative_height(self):
        with pytest.raises(ValueError, match="cursor"):
            Block(cursor="", height=1)
        with pytest.raises(ValueError, match="height"):
            Block(cursor="c", height=-1)

    def test_health_starts_in_starting_state(self):
        health = BlockPoller(FakeSource([])).health()

        assert health["status"] == "starting"
        assert health["running"] is False
        assert health["cursor"] is None
        assert health["last_success_at"] is None


class TestBlockPollerStructuredLogging:
    def test_successful_cycle_logs_structured_context(self, caplog):
        caplog.set_level(logging.DEBUG, logger="block_poller")
        poller = BlockPoller(FakeSource([[_block(7), _block(8)]]))

        poller.poll_once()

        records = _structured_records(caplog)
        completed = [record for record in records if record.get("event") == "poll_completed"]
        assert len(completed) == 1
        event = completed[0]
        assert event["message"] == "block_poller.poll_completed"
        assert event["cursor"] == "cursor-8"
        assert event["blocks_processed"] == 2
        assert event["latest_height"] == 8
        assert event["attempts"] == 1
        assert event["duration_ms"] >= 0
        assert event["level"] == "INFO"
        assert event["logger"] == "block_poller"

    def test_retry_and_terminal_failure_are_logged(self, caplog):
        caplog.set_level(logging.DEBUG, logger="block_poller")
        source = AlwaysFailSource(BlockFetchError("horizon 503", retryable=True, status_code=503))
        poller = BlockPoller(source, max_attempts=2, retry_base_delay_seconds=0.25, sleep=lambda _delay: None)

        with pytest.raises(BlockFetchError):
            poller.poll_once()

        records = _structured_records(caplog)
        retry = next(record for record in records if record.get("event") == "fetch_retry")
        failure = next(record for record in records if record.get("event") == "fetch_failed")

        assert retry["message"] == "block_poller.fetch_retry"
        assert retry["attempt"] == 1
        assert retry["max_attempts"] == 2
        assert retry["delay_seconds"] == 0.25
        assert retry["status_code"] == 503
        assert retry["error_type"] == "BlockFetchError"
        assert retry["level"] == "WARNING"

        assert failure["attempts"] == 2
        assert failure["retryable"] is True
        assert failure["status_code"] == 503
        assert failure["message"] == "block_poller.fetch_failed"
        assert failure["level"] == "ERROR"

    def test_handler_failure_log_identifies_the_block(self, caplog):
        caplog.set_level(logging.DEBUG, logger="block_poller")

        def reject(_block: Block) -> None:
            raise ValueError("bad ledger payload")

        poller = BlockPoller(FakeSource([[_block(42)]]), reject)

        with pytest.raises(BlockHandlerError):
            poller.poll_once()

        records = _structured_records(caplog)
        event = next(record for record in records if record.get("event") == "handler_failed")
        assert event["cursor"] == "cursor-42"
        assert event["height"] == 42
        assert event["error"] == "bad ledger payload"
        assert event["error_type"] == "ValueError"
        assert event["consecutive_failures"] == 1


def _ledger(sequence: int) -> dict[str, Any]:
    return {
        "paging_token": f"ledger-{sequence}",
        "sequence": sequence,
        "hash": f"hash-{sequence}",
        "closed_at": "2026-10-20T00:00:00Z",
    }


def _page(*sequences: int) -> dict[str, Any]:
    return {"_embedded": {"records": [_ledger(sequence) for sequence in sequences]}}


class TestHorizonBlockSourceIntegration:
    def test_poller_walks_horizon_pages_using_the_paging_token(self):
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            assert request.url.path == "/ledgers"
            assert request.url.params["order"] == "asc"
            assert request.url.params["limit"] == "2"
            cursor = request.url.params.get("cursor")
            if cursor is None:
                return httpx.Response(200, json=_page(1, 2))
            if cursor == "ledger-2":
                return httpx.Response(200, json=_page(3, 4))
            return httpx.Response(200, json=_page())

        source = HorizonBlockSource(
            "https://horizon.test",
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        handled: list[int] = []
        poller = BlockPoller(source, lambda block: handled.append(block.height), batch_size=2)

        first = poller.poll_once()
        second = poller.poll_once()

        assert handled == [1, 2, 3, 4]
        assert first.cursor == "ledger-2"
        assert second.cursor == "ledger-4"
        assert [request.url.params.get("cursor") for request in requests] == [None, "ledger-2"]

    def test_transient_horizon_failure_is_retried(self):
        responses = [
            httpx.Response(503, json={"title": "service unavailable"}),
            httpx.Response(200, json=_page(9)),
        ]
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return responses.pop(0)

        source = HorizonBlockSource(
            "https://horizon.test",
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        delays: list[float] = []
        poller = BlockPoller(source, max_attempts=2, sleep=delays.append)

        result = poller.poll_once()

        assert result.attempts == 2
        assert result.blocks_processed == 1
        assert result.cursor == "ledger-9"
        assert delays == [0.5]
        assert len(requests) == 2

    def test_rate_limit_is_classified_as_retryable(self):
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(429)

        source = HorizonBlockSource(
            "https://horizon.test",
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        poller = BlockPoller(source, max_attempts=2, sleep=lambda _delay: None)

        with pytest.raises(BlockFetchError) as exc_info:
            poller.poll_once()

        assert exc_info.value.retryable is True
        assert exc_info.value.status_code == 429
        assert len(requests) == 2

    def test_terminal_http_error_is_not_retried(self):
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(404)

        source = HorizonBlockSource(
            "https://horizon.test",
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        poller = BlockPoller(source, max_attempts=4, sleep=lambda _delay: None)

        with pytest.raises(BlockFetchError) as exc_info:
            poller.poll_once()

        assert exc_info.value.retryable is False
        assert len(requests) == 1

    def test_malformed_horizon_response_is_terminal(self):
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json={"unexpected": "shape"})

        source = HorizonBlockSource(
            "https://horizon.test",
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        poller = BlockPoller(source, max_attempts=3, sleep=lambda _delay: None)

        with pytest.raises(BlockFetchError, match="_embedded"):
            poller.poll_once()

        assert len(requests) == 1
        assert poller.cursor is None

    def test_network_error_is_wrapped_as_retryable(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        source = HorizonBlockSource(
            "https://horizon.test",
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        poller = BlockPoller(source, max_attempts=2, sleep=lambda _delay: None)

        with pytest.raises(BlockFetchError, match="Horizon request failed") as exc_info:
            poller.poll_once()

        assert exc_info.value.retryable is True

    def test_source_sends_cursor_and_limit_and_maps_records(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.params["cursor"] == "ledger-10"
            assert request.url.params["limit"] == "1"
            return httpx.Response(200, json=_page(11))

        source = HorizonBlockSource(
            "https://horizon.test",
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )

        blocks = source.fetch_blocks(cursor="ledger-10", limit=1)

        assert len(blocks) == 1
        assert blocks[0].cursor == "ledger-11"
        assert blocks[0].height == 11
        assert blocks[0].payload["hash"] == "hash-11"

    @pytest.mark.parametrize("limit", [0, 201])
    def test_source_rejects_out_of_range_limits(self, limit):
        source = HorizonBlockSource(
            "https://horizon.test",
            client=httpx.Client(transport=httpx.MockTransport(lambda _request: httpx.Response(200))),
        )

        with pytest.raises(ValueError, match="limit"):
            source.fetch_blocks(limit=limit)

    @pytest.mark.parametrize("base_url", ["", "   ", "horizon.test"])
    def test_source_rejects_invalid_base_urls(self, base_url):
        with pytest.raises(ValueError, match="base_url"):
            HorizonBlockSource(base_url)
