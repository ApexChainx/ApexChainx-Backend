import json
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator
from sqlalchemy import String, cast, func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.v1.endpoints.webhook_errors import (
    DELIVERY_NOT_FOUND,
    VALIDATION_ERROR,
    WEBHOOK_CONFLICT,
    WEBHOOK_NOT_FOUND,
    WebhookHTTPException,
)
from app.core.config import settings
from app.core.security import hash_token, require_admin
from app.db.session import get_db
from app.models.webhook import Webhook, WebhookDelivery, WebhookDeliveryStatus, WebhookEvent

from app.services.audit_log import audit_log
from app.services.formatters import canonical_json
from app.services.metrics import increment_counter, set_gauge
from app.services.webhook_service import WEBHOOK_SCHEMA_VERSION
from app.services.webhook_uniqueness import DuplicateWebhookError, handle_create_integrity_error
from app.utils.logging import get_structured_logger
from app.utils.network_validation import validate_webhook_url
from app.utils.secret_history import prune_expired_secrets

router = APIRouter(
    prefix="/webhooks",
    tags=["Webhooks"],
    # Every webhook route — including delivery inspection, retry, dead-letter
    # replay and replay-by-context — requires an admin session. The router-level
    # dependency is the durable gate so newly added sub-resources cannot be
    # added without authentication by omission.
    dependencies=[Depends(require_admin)],
)

logger = get_structured_logger("webhooks_api")


# --------------------------------------------------------------------------- #
# Schemas                                                                      #
# --------------------------------------------------------------------------- #


class WebhookCreate(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name": "outage-webhook",
                "url": "https://example.com/webhook",
                "secret": "supersecret",
                "events": ["sla.violation"],
                "max_retries": 3,
                "is_active": True,
            }
        }
    )

    name: str
    url: HttpUrl
    secret: str | None = None
    events: list[WebhookEvent]
    # Bounded (#552): an unbounded value was accepted and stored, promising
    # retries the dispatcher can never make — the attempt count is also capped
    # by the number of entries in WEBHOOK_RETRY_BASE_DELAYS. 0 disables retries.
    max_retries: int = Field(default=3, ge=0, le=settings.MAX_WEBHOOK_MAX_RETRIES)
    is_active: bool = True

    @field_validator("name")
    @classmethod
    def validate_name_length(cls, v: str) -> str:
        if len(v) > settings.MAX_WEBHOOK_NAME_LENGTH:
            raise ValueError(f"name too long. Maximum length is {settings.MAX_WEBHOOK_NAME_LENGTH} characters.")
        return v

    @field_validator("url")
    @classmethod
    def validate_url_length(cls, v: HttpUrl) -> HttpUrl:
        url_str = str(v)
        if len(url_str) > settings.MAX_WEBHOOK_URL_LENGTH:
            raise ValueError(f"url too long. Maximum length is {settings.MAX_WEBHOOK_URL_LENGTH} characters.")
        validate_webhook_url(url_str)
        return v

    @field_validator("events")
    @classmethod
    def validate_events_count(cls, v: list[WebhookEvent]) -> list[WebhookEvent]:
        if not v:
            raise ValueError("At least one event must be specified.")
        if len(v) > settings.MAX_WEBHOOK_EVENTS_COUNT:
            raise ValueError(f"too many events. Maximum allowed is {settings.MAX_WEBHOOK_EVENTS_COUNT}.")
        return v


class WebhookUpdate(BaseModel):
    name: str | None = None
    url: HttpUrl | None = None
    secret: str | None = None
    events: list[WebhookEvent] | None = None
    # Same bound as create (#552); None means "leave unchanged".
    max_retries: int | None = Field(default=None, ge=0, le=settings.MAX_WEBHOOK_MAX_RETRIES)
    is_active: bool | None = None
    # #582: the per-webhook grace window used by secret rotations. None means
    # "leave unchanged"; the bound matches _apply_secret_rotation's fallback
    # rule so a value that would be ignored cannot be stored in the first place.
    secret_grace_hours: int | None = Field(
        default=None, ge=1, le=settings.MAX_WEBHOOK_SECRET_GRACE_HOURS
    )

    @field_validator("name")
    @classmethod
    def validate_name_length(cls, v: str) -> str:
        if v is not None and len(v) > settings.MAX_WEBHOOK_NAME_LENGTH:
            raise ValueError(f"name too long. Maximum length is {settings.MAX_WEBHOOK_NAME_LENGTH} characters.")
        return v

    @field_validator("url")
    @classmethod
    def validate_url_length(cls, v: HttpUrl) -> HttpUrl:
        if v is not None:
            url_str = str(v)
            if len(url_str) > settings.MAX_WEBHOOK_URL_LENGTH:
                raise ValueError(f"url too long. Maximum length is {settings.MAX_WEBHOOK_URL_LENGTH} characters.")
            validate_webhook_url(url_str)
        return v

    @field_validator("events")
    @classmethod
    def validate_events_count(cls, v: list[WebhookEvent]) -> list[WebhookEvent]:
        if v is not None:
            if not v:
                raise ValueError("At least one event must be specified.")
            if len(v) > settings.MAX_WEBHOOK_EVENTS_COUNT:
                raise ValueError(f"too many events. Maximum allowed is {settings.MAX_WEBHOOK_EVENTS_COUNT}.")
        return v


class WebhookResponse(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "123e4567-e89b-12d3-a456-426614174000",
                "name": "outage-webhook",
                "url": "https://example.com/webhook",
                "is_active": True,
                "events": ["sla.violation"],
                "max_retries": 3,
                "schema_version": "1",
            }
        },
        from_attributes=True,
    )

    id: UUID
    name: str
    url: str
    is_active: bool
    events: list[str]
    max_retries: int
    schema_version: str = WEBHOOK_SCHEMA_VERSION  # BE-082: explicit schema version
    # BE-034: Secret lifecycle metadata (without exposing the secret)
    secret_version: int = 1
    last_secret_rotation_at: str | None = None
    # #518: non-null only for soft-deleted webhooks, which stay retrievable so
    # their delivery history remains auditable.
    deleted_at: str | None = None


class WebhookDeliveryResponse(BaseModel):
    id: UUID
    webhook_id: UUID
    event: WebhookEvent
    status: WebhookDeliveryStatus
    attempt_count: int
    response_status_code: int | None
    error_message: str | None
    delivered_at: str | None
    dead_lettered_at: str | None  # BE-086: Include dead-letter timestamp
    signature_version: int  # BE-087: Explicit signature algorithm version
    created_at: str

    model_config = {"from_attributes": True}


class PaginatedWebhookDeliveries(BaseModel):
    items: list[WebhookDeliveryResponse]
    total: int
    offset: int
    limit: int
    returned: int
    has_more: bool


class PaginatedWebhookList(BaseModel):
    """Envelope for `GET /webhooks` (#554).

    The endpoint used to answer with a bare array, so a caller paging through
    webhooks could not tell "end of list" from "this page happens to be short":
    it had to request one more page to find out. The items array is still
    present, under `items`, alongside the metadata needed to page correctly.
    """

    items: list[WebhookResponse]
    total: int
    page: int
    page_size: int
    returned: int
    has_more: bool


class WebhookSecretRotateResponse(BaseModel):
    webhook_id: UUID
    new_secret: str
    message: str


class WebhookReplayRequest(BaseModel):
    device_id: str | None = None
    outage_id: str | None = None
    limit: int = 50


class WebhookReplayResponse(BaseModel):
    replayed_count: int
    message: str


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #


def _get_webhook_or_404(db: Session, webhook_id: UUID) -> Webhook:
    webhook = db.query(Webhook).filter(Webhook.id == webhook_id).first()
    if not webhook:
        raise WebhookHTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Webhook not found.",
            error_code=WEBHOOK_NOT_FOUND,
        )
    return webhook


def _get_live_webhook_or_409(db: Session, webhook_id: UUID) -> Webhook:
    """Resolve a webhook that is expected to still be modifiable.

    Soft-deleted webhooks keep their row and delivery history (#518), so a
    tombstone is a 409 rather than a 404: the resource exists, it is simply no
    longer mutable, and saying "not found" would hide that.
    """
    webhook = _get_webhook_or_404(db, webhook_id)
    if webhook.is_deleted:
        raise WebhookHTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Webhook has been deleted and can no longer be modified.",
            error_code=WEBHOOK_CONFLICT,
        )
    return webhook


def _serialize_webhook(webhook: Webhook) -> WebhookResponse:
    try:
        events = json.loads(webhook.events)
    except (json.JSONDecodeError, TypeError):
        events = []
    return WebhookResponse(
        id=webhook.id,
        name=webhook.name,
        url=webhook.url,
        is_active=webhook.is_active,
        events=events,
        max_retries=webhook.max_retries,
        secret_version=webhook.secret_version,
        last_secret_rotation_at=webhook.last_secret_rotation_at.isoformat()
        if webhook.last_secret_rotation_at
        else None,
        deleted_at=webhook.deleted_at.isoformat() if webhook.deleted_at else None,
    )


def _serialize_delivery(delivery: WebhookDelivery) -> WebhookDeliveryResponse:
    return WebhookDeliveryResponse(
        id=delivery.id,
        webhook_id=delivery.webhook_id,
        event=delivery.event,
        status=delivery.status,
        attempt_count=delivery.attempt_count,
        response_status_code=delivery.response_status_code,
        error_message=delivery.error_message,
        delivered_at=delivery.delivered_at.isoformat() if delivery.delivered_at else None,
        dead_lettered_at=delivery.dead_lettered_at.isoformat() if delivery.dead_lettered_at else None,
        signature_version=delivery.signature_version,
        created_at=delivery.created_at.isoformat(),
    )


# --------------------------------------------------------------------------- #
# Registration limits (#517)                                                   #
# --------------------------------------------------------------------------- #


def _count_registered_webhooks(db: Session) -> int:
    """Number of webhooks currently registered.

    The `webhooks` table has no owner column: registration is admin-scoped and
    every row is a subscription the platform pays outbound requests for, so the
    per-customer cap in #517 is applied to the total.
    """
    return db.query(func.count(Webhook.id)).scalar() or 0


def _count_event_subscriptions(db: Session) -> int:
    """Total (webhook, event) pairs across all webhooks.

    This is the platform's webhook fan-out: one `sla.violation` emission costs
    one HTTPS request per subscribed webhook. The events column is text-encoded
    JSON rather than JSONB, so it is decoded here rather than in SQL to keep the
    query portable across the SQLite test database.
    """
    total = 0
    for (raw_events,) in db.query(Webhook.events).all():
        if not raw_events:
            continue
        try:
            parsed = json.loads(raw_events)
        except (TypeError, ValueError):
            logger.warning("Webhook events column is not valid JSON; excluded from fan-out count")
            continue
        if isinstance(parsed, list):
            total += len(parsed)
    return total


def _enforce_webhook_registration_cap(db: Session) -> None:
    """Reject registration once the configured cap is reached.

    Unlimited registrations mean an admin session can multiply every emitted
    event by an unbounded number of outbound requests, and each registered
    secret is an additional Fernet ciphertext to rotate. A cap of 0 disables
    the check for operators that manage this themselves.
    """
    cap = settings.MAX_WEBHOOKS_PER_ACCOUNT
    if cap <= 0:
        return
    registered = _count_registered_webhooks(db)
    if registered >= cap:
        raise WebhookHTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Webhook limit reached: {registered} webhooks are already registered and "
                f"MAX_WEBHOOKS_PER_ACCOUNT is {cap}. Delete an unused webhook before creating another."
            ),
            error_code=WEBHOOK_CONFLICT,
        )


def _warn_on_excessive_fanout(db: Session) -> None:
    """Log and count a warning when total fan-out crosses the threshold.

    Not a hard limit: per-dispatch concurrency is already capped by
    WEBHOOK_MAX_CONCURRENT_DISPATCHES, so exceeding this is a signal that the
    delivery queue is being asked to do more work than intended rather than
    something to reject.
    """
    threshold = settings.WEBHOOK_FANOUT_WARN_THRESHOLD
    if threshold <= 0:
        return
    fanout = _count_event_subscriptions(db)
    if fanout <= threshold:
        return
    logger.warning(
        "Webhook fan-out threshold exceeded",
        fanout_subscriptions=fanout,
        threshold=threshold,
    )
    increment_counter("webhook.fanout.threshold_exceeded", tags={"threshold": str(threshold)})
    set_gauge("webhook.fanout.subscriptions", float(fanout))


# --------------------------------------------------------------------------- #
# Endpoints                                                                    #
# --------------------------------------------------------------------------- #


@router.post("", response_model=WebhookResponse, status_code=status.HTTP_201_CREATED)
def create_webhook(payload: WebhookCreate, current_user=Depends(require_admin), db: Session = Depends(get_db)):
    # Checked before the SSRF lookup so a registration that is going to be
    # rejected does not cost a DNS resolution.
    _enforce_webhook_registration_cap(db)
    url = str(payload.url)
    resolved_ips = validate_webhook_url(url)
    webhook = Webhook(
        name=payload.name,
        url=url,
        secret=payload.secret,
        events=canonical_json([e.value for e in payload.events]),
        max_retries=payload.max_retries,
        is_active=payload.is_active,
        resolved_ips=canonical_json(resolved_ips),
    )
    db.add(webhook)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        # The partial unique index on (url, events) WHERE deleted_at IS NULL
        # fires when the same url+events tuple is registered twice without an
        # intervening soft-delete.  Translate to 409 so the caller gets a
        # machine-readable signal instead of an unhandled 500 (issue #562).
        try:
            handle_create_integrity_error(exc)
        except DuplicateWebhookError:
            raise WebhookHTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A webhook with this url and events combination already exists.",
                error_code=WEBHOOK_CONFLICT,
            ) from exc
        # Any other IntegrityError (e.g. FK violation) is not a duplicate —
        # re-raise and let the global handler deal with it.
        raise
    db.refresh(webhook)
    _warn_on_excessive_fanout(db)
    return _serialize_webhook(webhook)


@router.get("", response_model=PaginatedWebhookList)
def list_webhooks(
    is_active: bool | None = Query(None),
    name: str | None = Query(None, description="Filter by name (case-insensitive substring match)"),  # BE-083
    include_deleted: bool = Query(
        False,
        description=(
            "Include soft-deleted webhooks (#518). Their delivery history is retained for audit, "
            "so operators need a way to find the tombstones."
        ),
    ),
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),  # BE-083
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),  # BE-083
    current_user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    query = db.query(Webhook)
    if not include_deleted:
        query = query.filter(Webhook.deleted_at.is_(None))
    if is_active is not None:
        query = query.filter(Webhook.is_active == is_active)
    if name:
        query = query.filter(Webhook.name.ilike(f"%{name}%"))
    offset = (page - 1) * page_size

    # Single statement for total + page, matching the deliveries endpoint (#296):
    # avoids the separate COUNT(*) scan that would otherwise run before every
    # page request. The explicit ORDER BY is new too - without it Postgres
    # returned rows in arbitrary order, so pages could repeat or skip webhooks
    # and `has_more` could not be trusted.
    paged = (
        query.add_columns(func.count().over().label("total_count"))
        .order_by(Webhook.created_at.desc(), Webhook.id)
        .offset(offset)
        .limit(page_size)
        .all()
    )
    total = paged[0].total_count if paged else query.order_by(None).count()
    items = [_serialize_webhook(row[0]) for row in paged]
    return PaginatedWebhookList(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        returned=len(items),
        has_more=offset + len(items) < total,
    )


@router.get("/{webhook_id}", response_model=WebhookResponse)
def get_webhook(webhook_id: UUID, current_user=Depends(require_admin), db: Session = Depends(get_db)):
    webhook = _get_webhook_or_404(db, webhook_id)
    return _serialize_webhook(webhook)


@router.patch("/{webhook_id}", response_model=WebhookResponse)
def update_webhook(
    webhook_id: UUID, payload: WebhookUpdate, current_user=Depends(require_admin), db: Session = Depends(get_db)
):
    webhook = _get_live_webhook_or_409(db, webhook_id)

    if payload.name is not None:
        webhook.name = payload.name
    if payload.url is not None:
        url = str(payload.url)
        resolved_ips = validate_webhook_url(url)
        webhook.url = url
        webhook.resolved_ips = canonical_json(resolved_ips)
    if payload.secret is not None:
        # #581: PATCH used to overwrite the signing secret in place — no
        # version bump, no history entry, no grace window, no audit event —
        # so signatures produced with the previous secret failed the moment
        # the row was written. Changing the secret here is a rotation and
        # goes through the same lifecycle as POST /rotate-secret.
        _apply_secret_rotation(
            webhook,
            new_secret=payload.secret,
            now=datetime.now(UTC),
            actor=getattr(current_user, "email", "unknown"),
            source="patch",
        )
    if payload.events is not None:
        webhook.events = canonical_json([e.value for e in payload.events])
    if payload.max_retries is not None:
        webhook.max_retries = payload.max_retries
    if payload.is_active is not None:
        webhook.is_active = payload.is_active
    if payload.secret_grace_hours is not None:
        # #582: make the per-webhook grace window settable so the rotation
        # path's honouring of it (and the 1..MAX bound in the schema) is
        # reachable from the API rather than only from the DB.
        webhook.secret_grace_hours = payload.secret_grace_hours

    db.commit()
    db.refresh(webhook)
    if payload.events is not None:
        # Subscribing an existing webhook to more events grows fan-out just as
        # registering a new one does, so the #517 warning applies here too.
        _warn_on_excessive_fanout(db)
    return _serialize_webhook(webhook)


@router.delete("/{webhook_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_webhook(webhook_id: UUID, current_user=Depends(require_admin), db: Session = Depends(get_db)):
    """Soft-delete a webhook, retaining its delivery history (#518).

    The row used to be deleted outright, and `deliveries` cascades, so the record
    of what was delivered and what the consumer answered was destroyed with the
    registration. The row is now kept as a tombstone: `deleted_at` is stamped and
    `is_active` cleared so the dispatcher stops selecting it, while the delivery
    rows stay queryable for audit.

    Idempotent: deleting an already-deleted webhook is still a 204, because the
    caller's intent (this webhook should not receive events) already holds.
    """
    webhook = _get_webhook_or_404(db, webhook_id)
    if not webhook.is_deleted:
        webhook.deleted_at = datetime.now(UTC)
        webhook.is_active = False
        audit_log.log(
            "webhook_deleted",
            {
                "webhook_id": str(webhook.id),
                "webhook_name": webhook.name,
                "url": webhook.url,
                "deleted_by": getattr(current_user, "email", "unknown"),
            },
        )
        db.commit()


@router.get("/{webhook_id}/deliveries", response_model=PaginatedWebhookDeliveries)
def list_webhook_deliveries(
    webhook_id: UUID,
    status: WebhookDeliveryStatus | None = Query(None, description="Filter by delivery status."),
    event: WebhookEvent | None = Query(None, description="Filter by delivery event type."),
    search: str | None = Query(None, description="Search delivery id, error message, or response status code."),
    created_after: datetime | None = Query(None, description="Return deliveries created after this timestamp."),
    created_before: datetime | None = Query(None, description="Return deliveries created before this timestamp."),
    delivered_after: datetime | None = Query(None, description="Return deliveries delivered after this timestamp."),
    delivered_before: datetime | None = Query(None, description="Return deliveries delivered before this timestamp."),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0, description="Number of records to skip"),  # BE-083
    db: Session = Depends(get_db),
):
    _get_webhook_or_404(db, webhook_id)
    query = db.query(WebhookDelivery).filter(WebhookDelivery.webhook_id == webhook_id)

    if status is not None:
        query = query.filter(WebhookDelivery.status == status)
    if event is not None:
        query = query.filter(WebhookDelivery.event == event)
    if created_after is not None:
        query = query.filter(WebhookDelivery.created_at >= created_after)
    if created_before is not None:
        query = query.filter(WebhookDelivery.created_at <= created_before)
    if delivered_after is not None:
        query = query.filter(WebhookDelivery.delivered_at >= delivered_after)
    if delivered_before is not None:
        query = query.filter(WebhookDelivery.delivered_at <= delivered_before)
    if search:
        search_term = f"%{search}%"
        query = query.filter(
            or_(
                cast(WebhookDelivery.id, String).ilike(search_term),
                WebhookDelivery.error_message.ilike(search_term),
                cast(WebhookDelivery.response_status_code, String).ilike(search_term),
            )
        )

    # Single statement for total + page (issue #296): avoids the separate
    # COUNT(*) scan that used to run before every page query. Cursor-based
    # paging (via app/utils/cursor.py) and the composite index are a
    # follow-up that needs a schema migration, tracked in #296.
    paged = (
        query.add_columns(func.count().over().label("total_count"))
        .order_by(WebhookDelivery.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    total = paged[0].total_count if paged else query.order_by(None).count()
    items = [_serialize_delivery(row[0]) for row in paged]
    return PaginatedWebhookDeliveries(
        items=items,
        total=total,
        offset=offset,
        limit=limit,
        returned=len(items),
        has_more=offset + len(items) < total,
    )


def _apply_secret_rotation(webhook: Webhook, new_secret: str, now: datetime, actor: str, *, source: str) -> int:
    """Move `webhook` from its current secret to `new_secret` with the full lifecycle (#581, #582).

    Both paths that change a webhook's signing secret — the dedicated rotate
    endpoint (BE-084) and PATCH with a `secret` field (#581) — must behave
    identically, otherwise one of them becomes a silent downgrade:

    - the outgoing secret is stored (hashed) in ``previous_secrets`` for the
      webhook's own grace window (#582): ``secret_grace_hours`` when valid,
      falling back to ``WEBHOOK_SECRET_GRACE_HOURS`` when missing/invalid —
      the column is nullable in older schema states and could be set to 0 or
      negative by an operator to disable the overlap;
    - ``secret_version`` is bumped so consumers can detect the change;
    - ``last_secret_rotation_at`` is stamped;
    - out-of-grace history is pruned first (#502) and the actor + source are
      recorded in the audit trail.

    Returns the effective grace window in hours (the per-webhook value when it
    was used, otherwise the global fallback) so callers can report it.

    The caller is responsible for resolving the webhook (live, not deleted)
    and committing; this helper assigns ``webhook.secret`` itself.
    """
    old_secret_version = webhook.secret_version
    old_rotation_time = webhook.last_secret_rotation_at

    # #582: honour the per-webhook grace window instead of the global default.
    # Values outside 1..MAX_WEBHOOK_SECRET_GRACE_HOURS fall back to the global
    # setting: a grace of 0 or less would drop the old secret immediately
    # (breaking in-flight consumers with no warning), and an unbounded value
    # would keep a (hashed) secret alive forever.
    configured_grace = getattr(webhook, "secret_grace_hours", None)
    if isinstance(configured_grace, int) and 1 <= configured_grace <= settings.MAX_WEBHOOK_SECRET_GRACE_HOURS:
        grace_hours = configured_grace
        grace_source = "webhook"
    else:
        grace_hours = settings.WEBHOOK_SECRET_GRACE_HOURS
        grace_source = "global"
        if configured_grace is not None:
            logger.warning(
                "Webhook secret_grace_hours out of range; falling back to global grace",
                webhook_id=str(webhook.id),
                configured_grace_hours=configured_grace,
                fallback_grace_hours=grace_hours,
            )

    # #502: prune previous_secrets that fell out of their grace window before
    # appending the new one. The daily housekeeping task also prunes, but a
    # webhook that rotates rarely would otherwise carry every historical entry
    # on every read for as long as it exists.
    kept_previous, pruned_previous = prune_expired_secrets(webhook.previous_secrets, now)
    if pruned_previous:
        webhook.previous_secrets = kept_previous

    # Store old secret in previous_secrets with expiry
    if webhook.secret:
        expires_at = now + timedelta(hours=grace_hours)
        previous_entry = {
            "hashed_secret": hash_token(webhook.secret),
            "created_at": now.isoformat(),
            "expires_at": expires_at.isoformat(),
        }
        if not webhook.previous_secrets:
            webhook.previous_secrets = []
        webhook.previous_secrets.append(previous_entry)

    webhook.secret = new_secret
    webhook.secret_version = old_secret_version + 1
    webhook.last_secret_rotation_at = now

    # Emit audit log with actor context and timestamp. `source` distinguishes
    # the dedicated rotate endpoint from a PATCH that carries a secret (#581):
    # both are rotations and both must be auditable as one.
    audit_log.log(
        "webhook_secret_rotated",
        {
            "webhook_id": str(webhook.id),
            "webhook_name": webhook.name,
            "old_secret_version": old_secret_version,
            "new_secret_version": webhook.secret_version,
            "previous_rotation_at": old_rotation_time.isoformat() if old_rotation_time else None,
            "grace_hours": grace_hours,
            "grace_source": grace_source,
            "rotated_by": actor,
            "rotation_source": source,
            # #502: how much history the rotation had to drop to stay bounded
            "pruned_previous_secrets": pruned_previous,
        },
    )

    return grace_hours


@router.post("/{webhook_id}/rotate-secret", response_model=WebhookSecretRotateResponse)  # BE-084
def rotate_webhook_secret(webhook_id: UUID, current_user=Depends(require_admin), db: Session = Depends(get_db)):
    """Rotate the webhook signing secret with a grace period overlap window.

    The previous secret is stored (hashed) and remains valid for the webhook's
    ``secret_grace_hours`` (falling back to WEBHOOK_SECRET_GRACE_HOURS when the
    column is unset or out of range), enabling zero-downtime rotation for
    consumers (#582).

    PATCH with a ``secret`` field goes through the same lifecycle (#581).

    #502: previous_secrets entries that are past their grace window are pruned
    on every rotation, so the JSONB history stays bounded instead of growing for
    the lifetime of the webhook.

    BE-034: Emits durable audit information with timestamp and actor context.
    """
    webhook = _get_live_webhook_or_409(db, webhook_id)

    now = datetime.now(UTC)
    new_secret = secrets.token_hex(32)

    # Capture the plaintext before it is replaced; _apply_secret_rotation
    # hashes the outgoing secret into the history.
    grace_hours = _apply_secret_rotation(
        webhook,
        new_secret=new_secret,
        now=now,
        actor=getattr(current_user, "email", "unknown"),
        source="rotate_endpoint",
    )

    db.commit()

    return WebhookSecretRotateResponse(
        webhook_id=webhook.id,
        new_secret=new_secret,
        message=(
            f"Secret rotated. Previous secret will remain valid for {grace_hours} hours."
        ),
    )


@router.post("/{webhook_id}/deliveries/{delivery_id}/retry", response_model=WebhookDeliveryResponse)
def retry_delivery(
    webhook_id: UUID,
    delivery_id: UUID,
    current_user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    _get_webhook_or_404(db, webhook_id)
    delivery = (
        db.query(WebhookDelivery)
        .filter(
            WebhookDelivery.id == delivery_id,
            WebhookDelivery.webhook_id == webhook_id,
        )
        .first()
    )
    if not delivery:
        raise WebhookHTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Delivery not found.",
            error_code=DELIVERY_NOT_FOUND,
        )
    if delivery.status == WebhookDeliveryStatus.SUCCESS:
        raise WebhookHTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Delivery already succeeded; retry not needed.",
            error_code=VALIDATION_ERROR,
        )
    if delivery.status == WebhookDeliveryStatus.SENDING:
        raise WebhookHTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Delivery is already being sent.",
            error_code=WEBHOOK_CONFLICT,
        )

    from app.services.webhook_service import dispatch_delivery

    dispatch_delivery(db, delivery.id)
    db.refresh(delivery)
    audit_log.log(
        "webhook_delivery_retried",
        {
            "webhook_id": str(webhook_id),
            "delivery_id": str(delivery_id),
            "actor": getattr(current_user, "email", "unknown"),
        },
    )
    return _serialize_delivery(delivery)


# BE-086: Dead-letter handling endpoints


@router.get("/{webhook_id}/dead-letter-deliveries", response_model=list[WebhookDeliveryResponse])
def list_dead_letter_deliveries(
    webhook_id: UUID,
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """List dead-lettered deliveries for a webhook."""
    _get_webhook_or_404(db, webhook_id)
    from app.services.webhook_service import get_dead_letter_deliveries

    deliveries = get_dead_letter_deliveries(db, webhook_id=webhook_id, limit=limit)
    return [_serialize_delivery(d) for d in deliveries]


@router.post("/{webhook_id}/deliveries/{delivery_id}/replay", response_model=WebhookDeliveryResponse)
def replay_dead_letter_delivery(
    webhook_id: UUID,
    delivery_id: UUID,
    current_user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Replay a dead-lettered delivery."""
    _get_webhook_or_404(db, webhook_id)
    delivery = (
        db.query(WebhookDelivery)
        .filter(
            WebhookDelivery.id == delivery_id,
            WebhookDelivery.webhook_id == webhook_id,
        )
        .first()
    )
    if not delivery:
        raise WebhookHTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Delivery not found.",
            error_code=DELIVERY_NOT_FOUND,
        )

    from app.services.webhook_service import replay_dead_letter_delivery

    success = replay_dead_letter_delivery(db, delivery_id)
    if not success:
        raise WebhookHTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to replay delivery. It may not be in dead-letter status.",
            error_code=VALIDATION_ERROR,
        )

    db.refresh(delivery)
    audit_log.log(
        "webhook_dead_letter_replayed",
        {
            "webhook_id": str(webhook_id),
            "delivery_id": str(delivery_id),
            "actor": getattr(current_user, "email", "unknown"),
        },
    )
    return _serialize_delivery(delivery)


# BE-085: Webhook replay by event or outage filters


@router.post("/replay-by-context", response_model=WebhookReplayResponse)
def replay_deliveries_by_context(
    event: WebhookEvent,
    payload: WebhookReplayRequest,
    current_user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Replay deliveries by event and context (device or outage)."""
    from app.services.webhook_service import replay_deliveries_by_event_context

    replayed_count = replay_deliveries_by_event_context(
        db, event=event, device_id=payload.device_id, outage_id=payload.outage_id, limit=payload.limit
    )

    audit_log.log(
        "webhook_replay_by_context",
        {
            "event": event.value,
            "device_id": payload.device_id,
            "outage_id": payload.outage_id,
            "limit": payload.limit,
            "replayed_count": replayed_count,
            "actor": getattr(current_user, "email", "unknown"),
        },
    )

    return WebhookReplayResponse(
        replayed_count=replayed_count, message=f"Replayed {replayed_count} deliveries for event {event.value}"
    )
