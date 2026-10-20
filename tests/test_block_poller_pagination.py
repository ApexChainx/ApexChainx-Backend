"""Tests for the block poller's cursor pagination (issue #733).

The poller walks the payments that are still waiting for their confirmation.
Walking that backlog with OFFSET/LIMIT is wrong: a payment that is confirmed
halfway through shifts every later row, so a row is either looked at twice or
skipped entirely. The poller pages with a cursor instead.
"""

from datetime import UTC, datetime

import pytest

from app.services.block_poller import BlockPoller
from app.services.block_poller_pagination import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    clamp_page_size,
    decode_payment_cursor,
    encode_payment_cursor,
)


class FakePayment:
    """The bits of a payment row the poller reads."""

    def __init__(self, payment_id, created_at, transaction_hash="a" * 64):
        self.id = payment_id
        self.created_at = created_at
        self.transaction_hash = transaction_hash
        self.status = "pending"
        self.confirmed_at = None


class FakeQuery:
    """A query stand-in that records what it was asked to do."""

    def __init__(self, rows):
        self.rows = rows
        self.filters = []
        self.ordered = False
        self.limit_value = None

    def filter(self, *args, **kwargs):
        self.filters.extend(args)
        return self

    def order_by(self, *args, **kwargs):
        self.ordered = True
        return self

    def limit(self, value):
        self.limit_value = value
        return self

    def all(self):
        rows = self.rows
        if self.limit_value is not None:
            rows = rows[: self.limit_value]
        return list(rows)


class FakeSession:
    """A session stand-in that answers every query with the same rows."""

    def __init__(self, rows):
        self.rows = rows
        self.last_query = None
        self.committed = 0

    def query(self, entity):
        self.last_query = FakeQuery(self.rows)
        return self.last_query

    def commit(self):
        self.committed += 1

    def refresh(self, instance):
        return None


def _payments(count):
    base = datetime(2026, 1, 1, tzinfo=UTC)
    return [FakePayment(f"p-{index}", base.replace(second=index)) for index in range(count)]


def test_the_page_size_is_clamped_into_the_allowed_range():
    assert clamp_page_size(10) == 10
    assert clamp_page_size(10_000) == MAX_PAGE_SIZE
    assert clamp_page_size(0) == DEFAULT_PAGE_SIZE
    assert clamp_page_size(-5) == DEFAULT_PAGE_SIZE


def test_a_page_size_that_is_not_a_number_is_rejected():
    with pytest.raises(ValueError):
        clamp_page_size("not a number")


def test_a_cursor_round_trips():
    created_at = datetime(2026, 1, 1, 12, 30, tzinfo=UTC)

    cursor = encode_payment_cursor("p-1", created_at)

    assert decode_payment_cursor(cursor) == ("p-1", created_at)


def test_a_cursor_that_cannot_be_decoded_is_treated_as_no_cursor():
    assert decode_payment_cursor("not-a-cursor") is None
    assert decode_payment_cursor(None) is None


def test_a_page_reports_whether_there_is_another_one():
    session = FakeSession(_payments(5))

    page = BlockPoller(session).pending_page(limit=2)

    assert len(page.payments) == 2
    assert page.has_more is True
    assert page.next_cursor is not None


def test_the_last_page_has_no_cursor():
    session = FakeSession(_payments(2))

    page = BlockPoller(session).pending_page(limit=5)

    assert len(page.payments) == 2
    assert page.has_more is False
    assert page.next_cursor is None


def test_a_cursor_adds_the_keyset_filter_to_the_statement():
    session = FakeSession(_payments(5))
    cursor = encode_payment_cursor("p-1", datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC))

    BlockPoller(session).pending_page(cursor=cursor, limit=2)

    # One filter for "still pending", one for "after the cursor".
    assert len(session.last_query.filters) == 2


def test_without_a_cursor_the_statement_only_filters_the_pending_payments():
    session = FakeSession(_payments(5))

    BlockPoller(session).pending_page(limit=2)

    assert len(session.last_query.filters) == 1


def test_a_poll_marks_the_payments_the_network_confirmed():
    payments = _payments(2)
    session = FakeSession(payments)

    class FakeHorizon:
        def transaction(self, transaction_hash):
            return {"successful": True}

    report = BlockPoller(session, horizon_client=FakeHorizon()).poll_once(limit=10)

    assert report.checked == 2
    assert report.confirmed == 2
    assert all(payment.status == "confirmed" for payment in payments)
    assert all(payment.confirmed_at is not None for payment in payments)


def test_a_poll_skips_a_transaction_the_network_cannot_be_reached_for():
    payments = _payments(2)
    session = FakeSession(payments)

    class BrokenHorizon:
        def transaction(self, transaction_hash):
            raise RuntimeError("the network is down")

    report = BlockPoller(session, horizon_client=BrokenHorizon()).poll_once(limit=10)

    assert report.checked == 2
    assert report.skipped == 2
    assert report.confirmed == 0
    assert len(report.errors) == 2
    # A transaction that could not be checked is not marked as failed.
    assert all(payment.status == "pending" for payment in payments)
