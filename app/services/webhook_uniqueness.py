"""Translates a duplicate-webhook race into a clean 409 instead of an
unhandled IntegrityError, pending a DB unique constraint on
(url, canonical(events)).
"""
from sqlalchemy.exc import IntegrityError


class DuplicateWebhookError(Exception):
    pass


def canonical_webhook_key(url: str, events: list) -> str:
    return f"{url}|{','.join(sorted(events))}"


def handle_create_integrity_error(exc: IntegrityError) -> None:
    raise DuplicateWebhookError("A webhook with this url/events already exists") from exc
