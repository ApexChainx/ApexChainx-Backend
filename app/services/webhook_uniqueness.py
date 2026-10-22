"""Translates a duplicate-webhook unique-violation into a clean 409 instead of
an unhandled IntegrityError.

The source of truth is the partial unique index ``uq_webhooks_url_events_live``
created by migration ``0031_webhook_url_events_unique``. When two live webhooks
share the same ``(url, events)`` tuple, PostgreSQL raises ``UniqueViolation``;
``create_webhook`` catches the wrapping ``IntegrityError``, rolls back, and asks
this module to classify it — a duplicate becomes a 409, while any *other*
integrity failure (FK, NOT NULL, …) is left to the caller / global handler.
"""

from sqlalchemy.exc import IntegrityError

# Name of the partial unique index created by migration 0031.
WEBHOOK_UNIQUE_INDEX = "uq_webhooks_url_events_live"


class DuplicateWebhookError(Exception):
    """A live webhook with the same url + events combination already exists."""


def canonical_webhook_key(url: str, events: list) -> str:
    return f"{url}|{','.join(sorted(events))}"


def is_duplicate_webhook_error(exc: IntegrityError) -> bool:
    """True when ``exc`` is the unique-violation raised by the webhook index.

    Drivers word the error differently: psycopg2 names the constraint
    (``uq_webhooks_url_events_live``) while SQLite names the columns
    (``webhooks.url, webhooks.events``). Match on either form so the endpoint
    returns 409 on a true duplicate but never masks an unrelated
    ``IntegrityError`` as one.
    """
    raw = str(getattr(exc, "orig", None) or exc).lower()
    if WEBHOOK_UNIQUE_INDEX in raw:
        return True
    return "unique" in raw and "url" in raw and "events" in raw


def handle_create_integrity_error(exc: IntegrityError) -> None:
    """Raise ``DuplicateWebhookError`` when ``exc`` is a duplicate.

    Returns normally for any other integrity failure so the caller can re-raise
    it and let the global handler deal with it.
    """
    if is_duplicate_webhook_error(exc):
        raise DuplicateWebhookError("A webhook with this url/events already exists") from exc
