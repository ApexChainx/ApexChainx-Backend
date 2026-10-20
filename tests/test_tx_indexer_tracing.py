"""Distributed tracing for TxIndexer (#715).

Spans are collected with an in-memory exporter attached to a *local*
``TracerProvider`` that is injected into the indexer: the process-global provider
was already set by ``app.main``, so these tests never touch it.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from app.core.tracing import get_current_traceparent
from app.services.tx_indexer import (
    FetchedPage,
    HorizonPaymentSource,
    RetryPolicy,
    TxIndexer,
)

TRACEPARENT_RE = re.compile(r"^00-[0-9a-f]{32}-[0-9a-f]{16}-0[01]$")


def _record(hash_suffix: str = "a") -> dict[str, Any]:
    return {
        "transaction_hash": f"hash-{hash_suffix}",
        "type": "payment",
        "amount": "2.5",
        "asset_code": "USDC",
        "from": "GFROM",
        "to": "GTO",
        "created_at": "2026-10-01T12:00:00Z",
    }


class Source:
    def __init__(self, failures: list[BaseException] | None = None) -> None:
        self.failures = failures or []
        self.calls = 0

    def fetch_payments(self, cursor: str | None, limit: int) -> FetchedPage:
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        return FetchedPage(records=[_record()], next_cursor="next")

    def close(self) -> None:
        pass


class Sink:
    def __init__(self, inserted: int | None = None, error: Exception | None = None) -> None:
        self.inserted = inserted
        self.error = error

    def upsert_many(self, transactions) -> int:
        if self.error is not None:
            raise self.error
        return len(transactions) if self.inserted is None else self.inserted


@pytest.fixture
def tracing() -> tuple[Any, InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider.get_tracer("test.tx_indexer"), exporter


def _by_name(spans: list[ReadableSpan], name: str) -> list[ReadableSpan]:
    return [span for span in spans if span.name == name]


def _attributes(span: ReadableSpan) -> dict[str, Any]:
    return dict(span.attributes or {})


class TestSpanShape:
    def test_successful_pass_emits_parent_and_children(self, tracing):
        tracer, exporter = tracing
        indexer = TxIndexer(Source(), Sink(), batch_size=10, tracer=tracer)

        indexer.run_once(cursor="c-1")

        names = [span.name for span in exporter.get_finished_spans()]
        assert names.count("tx_indexer.run_once") == 1
        assert names.count("tx_indexer.fetch_page") == 1
        assert names.count("tx_indexer.persist") == 1

    def test_children_share_the_pass_trace_and_point_at_the_parent(self, tracing):
        tracer, exporter = tracing
        indexer = TxIndexer(Source(), Sink(), tracer=tracer)

        indexer.run_once()

        spans = {span.name: span for span in exporter.get_finished_spans()}
        root = spans["tx_indexer.run_once"]
        for child_name in ("tx_indexer.fetch_page", "tx_indexer.persist"):
            child = spans[child_name]
            assert child.context is not None and root.context is not None
            assert child.context.trace_id == root.context.trace_id
            assert child.parent is not None
            assert child.parent.span_id == root.context.span_id

    def test_pass_attributes_describe_the_batch(self, tracing):
        tracer, exporter = tracing
        indexer = TxIndexer(Source(), Sink(inserted=5), batch_size=33, tracer=tracer)

        indexer.run_once(cursor="c-9")

        spans = {span.name: span for span in exporter.get_finished_spans()}
        root = _attributes(spans["tx_indexer.run_once"])
        assert root["tx_indexer.component"] == "tx-indexer"
        assert root["tx_indexer.cursor_present"] is True
        assert root["tx_indexer.fetched"] == 1
        assert root["tx_indexer.inserted"] == 5

        fetch = _attributes(spans["tx_indexer.fetch_page"])
        assert fetch["tx_indexer.batch_size"] == 33
        assert fetch["tx_indexer.cursor_present"] is True
        assert fetch["tx_indexer.next_cursor_present"] is True

    def test_no_cursor_is_reported_as_absent(self, tracing):
        tracer, exporter = tracing
        indexer = TxIndexer(Source(), Sink(), tracer=tracer)

        indexer.run_once()

        spans = {span.name: span for span in exporter.get_finished_spans()}
        assert _attributes(spans["tx_indexer.run_once"])["tx_indexer.cursor_present"] is False


class TestFailureRecording:
    def test_fetch_failure_marks_the_spans_as_error(self, tracing):
        tracer, exporter = tracing
        indexer = TxIndexer(
            Source(failures=[httpx.ConnectError("boom", request=httpx.Request("GET", "http://h"))]),
            Sink(),
            policy=RetryPolicy(max_attempts=1, sleep=lambda _: None),
            tracer=tracer,
        )

        with pytest.raises(Exception):
            indexer.run_once()

        for name in ("tx_indexer.run_once", "tx_indexer.fetch_page"):
            span = {s.name: s for s in exporter.get_finished_spans()}[name]
            assert span.status.status_code is StatusCode.ERROR

    def test_sink_failure_marks_persist_and_pass_as_error(self, tracing):
        tracer, exporter = tracing
        indexer = TxIndexer(Source(), Sink(error=RuntimeError("write failed")), tracer=tracer)

        with pytest.raises(RuntimeError):
            indexer.run_once()

        spans = {s.name: s for s in exporter.get_finished_spans()}
        assert spans["tx_indexer.persist"].status.status_code is StatusCode.ERROR
        assert spans["tx_indexer.run_once"].status.status_code is StatusCode.ERROR
        # The fetch succeeded, so it keeps its non-error status.
        assert spans["tx_indexer.fetch_page"].status.status_code is not StatusCode.ERROR

    def test_exception_is_recorded_as_an_event(self, tracing):
        tracer, exporter = tracing
        indexer = TxIndexer(Source(), Sink(error=RuntimeError("write failed")), tracer=tracer)

        with pytest.raises(RuntimeError):
            indexer.run_once()

        span = {s.name: s for s in exporter.get_finished_spans()}["tx_indexer.persist"]
        assert any(event.name == "exception" for event in span.events)


class TestRetryEvents:
    def test_each_retry_becomes_an_event_on_the_fetch_span(self, tracing):
        tracer, exporter = tracing
        transient = [httpx.ConnectError("boom", request=httpx.Request("GET", "http://h")) for _ in range(2)]
        indexer = TxIndexer(
            Source(failures=transient),
            Sink(),
            policy=RetryPolicy(max_attempts=3, base_delay=0.1, jitter_ratio=0.0, sleep=lambda _: None, rng=lambda: 0.5),
            tracer=tracer,
        )

        indexer.run_once()

        fetch = _by_name(exporter.get_finished_spans(), "tx_indexer.fetch_page")[0]
        retries = [event for event in fetch.events if event.name == "retry"]
        assert len(retries) == 2
        assert retries[0].attributes is not None and retries[0].attributes["attempt"] == 1
        assert retries[1].attributes is not None and retries[1].attributes["attempt"] == 2
        assert retries[0].attributes["error.type"] == "ConnectError"
        # The retried fetch still succeeds, so neither span is an error.
        assert fetch.status.status_code is not StatusCode.ERROR


class TestTraceparentPropagation:
    def _source(self, captured: list[httpx.Request]) -> HorizonPaymentSource:
        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(
                200,
                json={"_embedded": {"records": [_record()]}, "next_cursor": "next"},
            )

        return HorizonPaymentSource(
            base_url="https://horizon.test",
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )

    def test_active_trace_context_is_forwarded_to_horizon(self, tracing):
        tracer, exporter = tracing
        captured: list[httpx.Request] = []
        source = self._source(captured)
        indexer = TxIndexer(source, Sink(), tracer=tracer)

        indexer.run_once()

        assert len(captured) == 1
        traceparent = captured[0].headers.get("traceparent")
        assert traceparent is not None
        assert TRACEPARENT_RE.match(traceparent)
        root = _by_name(exporter.get_finished_spans(), "tx_indexer.run_once")[0]
        assert root.context is not None
        assert f"{root.context.trace_id:032x}" in traceparent

    def test_no_header_is_sent_without_an_active_span(self):
        captured: list[httpx.Request] = []
        source = self._source(captured)

        source.fetch_payments(cursor=None, limit=5)

        assert "traceparent" not in captured[0].headers

    def test_get_current_traceparent_matches_the_w3c_format(self, tracing):
        tracer, _ = tracing

        with tracer.start_as_current_span("manual"):
            traceparent = get_current_traceparent()

        assert traceparent is not None
        assert TRACEPARENT_RE.match(traceparent)


class TestDisabledTracing:
    def test_indexer_works_without_an_active_tracer_provider(self):
        """A non-recording tracer must not change behaviour or attributes."""
        indexer = TxIndexer(Source(), Sink(), tracer=TracerProvider().get_tracer("disabled"))

        result = indexer.run_once()

        assert result.fetched == 1
        assert result.next_cursor == "next"
        assert result.inserted == 1


def test_record_fixture_timestamp_is_parseable():
    """Guard the fixture itself: a bad timestamp would silently mask span failures."""
    parsed = datetime.fromisoformat(_record()["created_at"].replace("Z", "+00:00"))

    assert parsed == datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
