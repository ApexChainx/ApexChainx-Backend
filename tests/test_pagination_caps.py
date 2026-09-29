"""Every list endpoint must enforce the same pagination cap — Issue #564.

Before #564 the list endpoints grew their own pagination knobs: webhook
deliveries capped at 200, webhooks/payments/outages/jobs at 100 with
differing defaults — and the SLA dispute list and the audit log had no cap
at all, so any authenticated caller could request a full-table dump.

``app/api/dependencies.py`` now owns the contract (``DEFAULT_PAGE_SIZE=20``,
``MAX_PAGE_SIZE=200``) and every list endpoint declares its ``page_size`` /
``limit`` through the shared Query objects. These tests pin the contract from
two sides:

- the OpenAPI schema: every pagination query parameter on every GET list
  endpoint must carry the same ``ge=1`` / ``le=MAX_PAGE_SIZE`` bounds, so a
  new endpoint (or a refactor) cannot quietly reintroduce an uncapped list;
- a live request: an out-of-range value is rejected with 422 by FastAPI
  before the handler runs, and the cap itself stays acceptable.

The audit log keeps its historical ``default=50`` — the issue requires the
same *caps*, not the same defaults.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from app.core.security import require_admin
from app.db.session import get_db
from app.main import app
from app.services.audit_log import audit_log

client = TestClient(app)

# Query-parameter names a list endpoint may paginate by.
PAGINATION_PARAMS = ("page_size", "limit")


def _pagination_parameters(spec: dict) -> list[tuple[str, str, dict]]:
    """Collect every query param named like a pagination knob from the spec."""
    found: list[tuple[str, str, dict]] = []
    for path, methods in spec["paths"].items():
        for method, op in methods.items():
            for param in op.get("parameters", []):
                if param.get("in") == "query" and param["name"] in PAGINATION_PARAMS:
                    found.append((method.upper(), path, param))
    return found


class TestSharedContract:
    def test_every_list_endpoint_declares_the_shared_bounds(self):
        """ge=1 / le=MAX_PAGE_SIZE on every pagination param, no exceptions."""
        spec = app.openapi()
        entries = _pagination_parameters(spec)

        # If this trips, a list endpoint lost its pagination parameter —
        # the uncapped full-table dump #564 closed is one refactor away.
        assert len(entries) >= 13, f"expected ≥13 pagination params, found {len(entries)}"

        offenders = []
        for method, path, param in entries:
            schema = param["schema"]
            if schema.get("minimum") != 1 or schema.get("maximum") != MAX_PAGE_SIZE:
                offenders.append(f"{method} {path} ({param['name']})")

        assert offenders == [], f"endpoints off the shared cap: {offenders}"

    def test_shared_defaults_are_what_the_dependencies_declare(self):
        """Non-audit lists use DEFAULT_PAGE_SIZE; audit keeps its legacy 50."""
        spec = app.openapi()
        entries = _pagination_parameters(spec)

        for method, path, param in entries:
            default = param["schema"].get("default")
            if path == "/api/v1/audit":
                assert default == 50  # historical default, cap is shared
            else:
                assert default == DEFAULT_PAGE_SIZE, (method, path)


# ── Live 422 behavior ────────────────────────────────────────────────────
#
# Out-of-range values must be rejected by request validation (422) before
# the handler runs. Two param styles are exercised: offset-style
# (``page_size`` on GET /webhooks) and limit-style (``limit`` on GET /audit).


def _webhook_row():
    return SimpleNamespace(
        id=SimpleNamespace(hex="0" * 32),
        name="cap-test",
        url="https://example.com/webhook",
        is_active=True,
        events='["sla.violation"]',
        max_retries=3,
        secret_version=1,
        last_secret_rotation_at=None,
        deleted_at=None,
        created_at=None,
        schema_version="1",
    )


def _mock_db():
    query = MagicMock()
    query.filter.return_value = query
    query.add_columns.return_value = query
    query.order_by.return_value = query
    query.offset.return_value = query
    query.limit.return_value = query
    query.all.return_value = []
    query.count.return_value = 0
    db = MagicMock()
    db.query.return_value = query
    return db


@pytest.fixture
def admin_override():
    """Fake admin + DB session, and a fake session factory for the audit service.

    ``AuditLogService.list`` opens its own session (``AuditSessionLocal`` /
    ``db_session_factory``) instead of taking the request-scoped one, so the
    audit-list tests must patch the singleton's factory, not just ``get_db``.
    """

    def _fake_admin():
        return SimpleNamespace(email="caps-admin@example.com", id="user_admin", role="admin")

    audit_session = MagicMock()
    audit_session.query.return_value.order_by.return_value.offset.return_value.limit.return_value.all.return_value = []
    audit_session.query.return_value.count.return_value = 0
    audit_session.__enter__ = lambda s: audit_session
    audit_session.__exit__ = lambda s, *exc: False

    original_factory = audit_log.db_session_factory
    audit_log.db_session_factory = MagicMock(return_value=audit_session)

    app.dependency_overrides[require_admin] = _fake_admin
    app.dependency_overrides[get_db] = lambda: (yield _mock_db())
    yield
    audit_log.db_session_factory = original_factory
    app.dependency_overrides.pop(require_admin, None)
    app.dependency_overrides.pop(get_db, None)


class TestOutOfRangeIs422:
    @pytest.mark.parametrize("page_size", [0, -1, MAX_PAGE_SIZE + 1, 10_000])
    def test_webhook_page_size_out_of_range(self, admin_override, page_size):
        resp = client.get(f"/api/v1/webhooks?page=1&page_size={page_size}")

        assert resp.status_code == 422

    def test_webhook_cap_itself_is_accepted(self, admin_override):
        resp = client.get(f"/api/v1/webhooks?page=1&page_size={MAX_PAGE_SIZE}")

        assert resp.status_code == 200

    @pytest.mark.parametrize("limit", [0, MAX_PAGE_SIZE + 1, 10_000])
    def test_audit_limit_out_of_range(self, admin_override, limit):
        # FastAPI resolves dependencies (auth -> DB) before returning 422,
        # so the auth/DB overrides are required even for rejected requests.
        resp = client.get(f"/api/v1/audit?limit={limit}")

        assert resp.status_code == 422

    def test_audit_cap_itself_is_accepted(self, admin_override):
        resp = client.get(f"/api/v1/audit?limit={MAX_PAGE_SIZE}")

        assert resp.status_code == 200
