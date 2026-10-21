"""Tests for the block poller's identifier hardening (issue #732).

The poller looks up payments by transaction hash.  A hash that reaches the
database unvalidated is a value that came from outside the process, so the
tests here pin both halves of the contract: the value is rejected before it
is used, and the value that *is* accepted is bound as a parameter instead of
being pasted into the statement.
"""

import pytest

from app.services.block_poller import BlockPoller
from app.services.block_poller_security import (
    UnsafeIdentifierError,
    validate_page_size,
    validate_payment_status,
    validate_transaction_hash,
)

VALID_HASH = "a" * 64


class CapturingSession:
    """A session stand-in that records the filters it was given."""

    def __init__(self):
        self.filters = []

    def query(self, entity):
        return self

    def filter(self, *args, **kwargs):
        self.filters.extend(args)
        return self

    def first(self):
        return None


@pytest.mark.parametrize(
    "value",
    [
        VALID_HASH,
        "0123456789abcdef" * 4,
    ],
)
def test_a_well_formed_transaction_hash_is_accepted(value):
    assert validate_transaction_hash(value) == value


@pytest.mark.parametrize(
    "value",
    [
        "",
        "not-a-hash",
        # Uppercase is not the canonical form.
        "A" * 64,
        # Too short / too long.
        "a" * 63,
        "a" * 65,
        # SQL metacharacters must never survive the allowlist.
        "' OR '1'='1",
        "a' OR 1=1 --",
        'a"; DROP TABLE payment_transactions; --',
        "%",
        "_",
        "a" * 63 + " ",
    ],
)
def test_a_malformed_transaction_hash_is_rejected(value):
    with pytest.raises(UnsafeIdentifierError):
        validate_transaction_hash(value)


def test_a_non_string_transaction_hash_is_rejected():
    with pytest.raises(UnsafeIdentifierError):
        validate_transaction_hash(None)


@pytest.mark.parametrize("value", ["pending", "confirmed", "failed"])
def test_known_payment_statuses_are_accepted(value):
    assert validate_payment_status(value) == value


@pytest.mark.parametrize("value", ["", "PENDING", "deleted", "pending' OR '1'='1"])
def test_unknown_payment_statuses_are_rejected(value):
    with pytest.raises(UnsafeIdentifierError):
        validate_payment_status(value)


def test_the_page_size_is_clamped_into_the_allowed_range():
    assert validate_page_size(10) == 10
    assert validate_page_size(10_000) == 100
    assert validate_page_size(0) == 20
    assert validate_page_size(-5) == 20


def test_a_malformed_hash_never_reaches_the_database():
    session = CapturingSession()
    poller = BlockPoller(session)

    with pytest.raises(UnsafeIdentifierError):
        poller.payment_by_hash("' OR 1=1 --")

    assert session.filters == []


def test_an_accepted_hash_is_bound_as_a_parameter():
    session = CapturingSession()
    poller = BlockPoller(session)

    poller.payment_by_hash(VALID_HASH)

    assert len(session.filters) == 1
    clause = session.filters[0]
    # The value is not pasted into the statement ...
    assert VALID_HASH not in str(clause)
    # ... it is carried as a bound parameter.
    assert clause.right.value == VALID_HASH
