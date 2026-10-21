"""Celery entrypoint for the TxIndexer (#710).

One task run is one bounded ingest pass: fetch a page from Horizon, normalize
it, upsert it into the ledger of record, and return the cursor to resume from.
The cursor is deliberately an input *and* an output instead of hidden worker
state — an operator can replay a pass, and a scheduler (or a cron entry) can
carry the cursor forward without the indexer owning a second store.
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.config import settings
from app.db.session import SessionLocal
from app.repositories.tx_indexer_repository import TxIndexerRepository
from app.services.tx_indexer import HorizonClient, TxIndexer
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.tx_indexer_tasks.index_transactions")
def index_transactions(cursor: str | None = None) -> dict[str, Any]:
    """Run a single TxIndexer ingest pass starting after *cursor*."""
    db = SessionLocal()
    try:
        indexer = TxIndexer(
            HorizonClient(settings.TX_INDEXER_HORIZON_URL),
            TxIndexerRepository(db),
            batch_size=settings.TX_INDEXER_BATCH_SIZE,
        )
        result = indexer.run_once(cursor)
    finally:
        db.close()

    logger.info(
        "tx_indexer_task",
        extra={
            "event": "tx_indexer_task",
            "cursor": cursor,
            "next_cursor": result.cursor,
            "fetched": result.fetched,
            "stored": result.stored,
            "skipped": result.skipped,
        },
    )
    return {
        "fetched": result.fetched,
        "stored": result.stored,
        "skipped": result.skipped,
        "cursor": result.cursor,
    }
