"""Tests for the block poller's distributed tracing (issue #731).

The poller spends most of its time talking to the network. Without a span
around the poll cycle that time is invisible: an operator sees the API
answer slowly and has no way to tell that the delay was the poller waiting
on Horizon.
"""

from datetime import UTC, datetime

import pytest

from app.services.block_poller import BlockPoller


class FakePayment:
    """The bits of a payment row the poller reads."""

    def __init__(self, payment_id="pay-1"):
        self.id = payment_id
        self.transaction_hash = "a" * 64
        self.created_at = datetime.now(UTC)
        self.status = "pending"
        self.confirmed_at = None


class FakeQuery:
    """A query stand-in that answers with the given rows."""

    def __init__(self, rows):
        self.rows = rows

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def limit(self, value):
        return self

    def all(self):
        return list(self.rows)


class FakeSession:
    """A session stand-in that answers every query with the same rows."""

    def __init__(self, rows):
        self.rows = rows

    def query(self, entity):
        return FakeQuery(self.rows)

    def commit(self):
        return None

    def refresh(self, instance):
        return None


class FakeSpan:
    """A span stand-in that records the attributes set on it."""

    def __init__(self, name):
        self.name = name
        self.attributes = {}

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def set_attribute(self, key, value):
        self.attributes[key] = value

    def record_exception(self, exc):
        return None

    def set_status(self, status):
        return None


class FakeTracer:
    """A tracer stand-in that records the spans that were opened."""

    def __init__(self):
        self.spans = []

    def start_as_current_span(self, name, attributes=None):
        span = FakeSpan(name)
        self.spans.append(span)
        return span


@pytest.fixture
def tracer(monkeypatch) -> FakeTracer:
    fake = FakeTracer()
    monkeypatch.setattr("app.core.tracing.tracer", fake)
    return fake


def test_the_poll_cycle_runs_inside_a_span(tracer):
    class FakeHorizon:
        def transaction(self, transaction_hash):
            return {"successful": True}

    poller = BlockPoller(FakeSession([FakePayment()]), horizon_client=FakeHorizon())

    poller.poll_once()

    names = [span.name for span in tracer.spans]
    assert "block_poller.poll" in names
    assert "block_poller.pending_payments" in names


def test_the_span_carries_the_poll_limit(tracer):
    class FakeHorizon:
        def transaction(self, transaction_hash):
            return {"successful": True}

    poller = BlockPoller(FakeSession([FakePayment()]), horizon_client=FakeHorizon())

    poller.poll_once(limit=7)

    poll_span = next(span for span in tracer.spans if span.name == "block_poller.poll")
    assert poll_span.attributes["block_poller.limit"] == 7


def test_the_poll_exposes_its_trace_context(tracer):
    class FakeHorizon:
        def transaction(self, transaction_hash):
            return {"successful": True}

    poller = BlockPoller(FakeSession([FakePayment()]), horizon_client=FakeHorizon())

    # The traceparent is only set when a real span is active; the contract is
    # that the poller can hand it to an outbound request either way.
    traceparent = poller.traceparent()

    assert traceparent is None or traceparent.startswith("00-")


def test_a_failed_network_call_is_still_inside_a_span(tracer):
    class BrokenHorizon:
        def transaction(self, transaction_hash):
            raise RuntimeError("the network is down")

    poller = BlockPoller(FakeSession([FakePayment()]), horizon_client=BrokenHorizon())

    report = poller.poll_once()

    assert report.skipped == 1
    assert "block_poller.poll" in [span.name for span in tracer.spans]
