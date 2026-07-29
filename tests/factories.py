import itertools
from datetime import datetime
from uuid import uuid4

from app.api.v1.endpoints.webhooks import WebhookCreate
from app.models.auth import LoginRequest, RegisterRequest
from app.models.enums import OutageStatus, Role, Severity
from app.models.outage import Location
from app.models.outage_dto import OutageCreate
from app.models.payment import PaymentTransaction
from app.models.sla import SLAResult

_seq = itertools.count(1)

def _next_id() -> str:
    return str(next(_seq))


def make_login_request(email: str | None = None, password: str = "Password123!") -> LoginRequest:
    return LoginRequest(
        email=email or f"user{_next_id()}@example.com",
        password=password,
    )


def make_register_request(
    email: str | None = None,
    full_name: str = "Test User",
    role: Role = Role.engineer,
    password: str = "Password123!",
) -> RegisterRequest:
    return RegisterRequest(
        email=email or f"user{_next_id()}@example.com",
        password=password,
        full_name=full_name,
        role=role,
    )


def make_outage_create(
    overrides: dict | None = None,
) -> OutageCreate:
    overrides = overrides or {}
    default_payload = {
        "id": f"outage-{_next_id()}",
        "site_name": "Example Site",
        "site_id": "site-123",
        "severity": Severity.high,
        "status": OutageStatus.open,
        "detected_at": datetime(2026, 1, 1, 0, 0),
        "description": "Example outage description",
        "affected_services": ["core-api"],
        "affected_subscribers": 42,
        "assigned_to": "oncall@example.com",
        "created_by": "tester@example.com",
        "location": Location(latitude=40.7128, longitude=-74.0060),
    }
    default_payload.update(overrides)
    return OutageCreate(**default_payload)


def make_payment_transaction(
    overrides: dict | None = None,
) -> PaymentTransaction:
    overrides = overrides or {}
    default_payload = {
        "id": f"payment-{_next_id()}",
        "transaction_hash": f"tx-{uuid4().hex}",
        "type": "reward",
        "amount": 150.0,
        "asset_code": "USDC",
        "from_address": "SYSTEM_POOL",
        "to_address": "OUTAGE_SETTLEMENT",
        "status": "confirmed",
        "outage_id": f"outage-{_next_id()}",
        "sla_result_id": 1,
        "created_at": datetime.utcnow(),
        "confirmed_at": datetime.utcnow(),
        "retry_count": 0,
        "last_retried_at": None,
    }
    default_payload.update(overrides)
    return PaymentTransaction(**default_payload)


def make_webhook_create(
    overrides: dict | None = None,
) -> WebhookCreate:
    overrides = overrides or {}
    default_payload = {
        "name": f"hook-{_next_id()}",
        "url": "https://example.com/webhook",
        "secret": "supersecret",
        "events": ["sla.violation"],
        "max_retries": 3,
        "is_active": True,
    }
    default_payload.update(overrides)
    return WebhookCreate(**default_payload)


def make_sla_result(
    overrides: dict | None = None,
) -> SLAResult:
    overrides = overrides or {}
    default_payload = {
        "id": 1,
        "outage_id": "outage-001",
        "status": "met",
        "mttr_minutes": 30,
        "threshold_minutes": 60,
        "amount": 100,
        "payment_type": "reward",
        "rating": "excellent",
    }
    default_payload.update(overrides)
    return SLAResult(**default_payload)
