"""Verify the composite index on payment_transactions is used for reconciliation queries."""

import pytest


@pytest.mark.skip(reason="Requires a live database with EXPLAIN ANALYZE support")
def test_payment_reconciliation_index_scan():
    """Verify the composite index ix_payment_tx_status_created is used.

    Run against a real PostgreSQL instance:

        EXPLAIN ANALYZE
        SELECT * FROM payment_transactions
        WHERE status = 'pending'
        ORDER BY created_at DESC;
    """
