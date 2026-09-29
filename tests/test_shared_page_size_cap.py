"""All list endpoints enforce the same page-size cap — Issue #564.

List endpoints used to carry ad-hoc, inconsistent caps: outages (``le=100``),
payments (``le=100``), webhooks (``le=100``), webhook deliveries
(``le=200``), jobs (``le=100``), and the SLA dispute list imported the shared
constant while everything else rolled its own. The cap now lives in a single
module (``app/schemas/audit_list_params.py`` — ``MAX_PAGE_SIZE = 200``) and
every list endpoint declares it, so a ``page_size``/``limit`` above 200 is
rejected with 422 by query-param validation itself, uniformly.

Two layers are pinned here:

1. **Source level** — every ``le=`` in the endpoints tree reads the shared
   constant, so a future endpoint can't quietly reintroduce an ad-hoc cap.
2. **HTTP level** — the enforcement is real, not decorative: at the cap the
   webhook list answers 200, above it FastAPI rejects with 422 before the
   handler runs (mocked the same way ``test_webhook_list_pagination.py`` does,
   since the cap check happens in query-param validation before any handler
   code).
"""

import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.api.v1.endpoints.webhooks import require_admin
from app.db.session import get_db
from app.main import app
from app.schemas.audit_list_params import MAX_PAGE_SIZE

client = TestClient(app)

ENDPOINTS_DIR = Path(__file__).resolve().parent.parent / "app" / "api" / "v1" / "endpoints"


# ---------------------------------------------------------------------------
# Source-level: every list cap must come from the shared constant
# ---------------------------------------------------------------------------


PAGINATION_PARAMS = {"page_size", "limit"}


def _le_constraints_with_context():
    """Yield (file, line, value) for every literal `le=` on a pagination param.

    Only `le=` keywords attached to endpoint parameters *named* ``page_size``
    or ``limit`` count — the shared cap governs list pagination, not
    unrelated range params (e.g. the SLA analytics ``days`` windows, which
    have their own domain bounds).
    """

    def _query_call_param_names(func_node) -> set[str]:
        """Names of parameters whose default is a Query(...) call.

        FastAPI caps are written as ``param: int = Query(...)``: defaults live
        in ``args.defaults`` (positional) and ``kw_defaults`` (keyword-only),
        parallel to ``args.args`` / ``args.kwonlyargs``.
        """
        names = set()
        args = func_node.args
        positional = args.args[-len(args.defaults):] if args.defaults else []
        for a, default in zip(positional, args.defaults):
            if isinstance(default, ast.Call):
                names.add(a.arg)
        for a, default in zip(args.kwonlyargs, args.kw_defaults):
            if default is not None and isinstance(default, ast.Call):
                names.add(a.arg)
        return names

    found = []
    for path in sorted(ENDPOINTS_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for func_node in ast.walk(tree):
            if not isinstance(func_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            pag_params = _query_call_param_names(func_node) & PAGINATION_PARAMS
            if not pag_params:
                continue
            for node in ast.walk(func_node):
                if not isinstance(node, ast.Call) or node.func is None:
                    continue
                callee = getattr(node.func, "id", getattr(node.func, "attr", "?"))
                if callee != "Query":
                    continue
                for kw in node.keywords:
                    if kw.arg != "le":
                        continue
                    if isinstance(kw.value, ast.Name):
                        # A referenced constant (e.g. MAX_PAGE_SIZE) — the
                        # compliant form; capture the name for diagnostics.
                        found.append((path.name, kw.value.lineno, f"{kw.value.id}()"))
                    elif isinstance(kw.value, ast.Constant):
                        # An inlined literal — the ad-hoc form we forbid.
                        found.append((path.name, kw.value.lineno, kw.value.value))
    return found


def test_every_list_cap_uses_the_shared_constant():
    offenders = [
        (file, line, value)
        for file, line, value in _le_constraints_with_context()
        if value != "MAX_PAGE_SIZE()"
    ]

    assert offenders == [], (
        "Endpoints must take their list cap from "
        f"app.schemas.audit_list_params.MAX_PAGE_SIZE ({MAX_PAGE_SIZE}), "
        f"not an ad-hoc number: {offenders}"
    )


def test_the_shared_cap_is_actually_declared_somewhere():
    """Guards against deleting the caps entirely to satisfy the check above."""
    le_values = [value for _, _, value in _le_constraints_with_context()]
    assert le_values, "No `le=` constraint found in any endpoint — pagination caps were removed?"
    assert "MAX_PAGE_SIZE()" in le_values


# ---------------------------------------------------------------------------
# HTTP-level: the shared cap is enforced, uniformly
# ---------------------------------------------------------------------------


def _webhook():
    return SimpleNamespace(
        id="wh-1",
        name="outage-webhook",
        url="https://example.com/webhook",
        is_active=True,
        events='["sla.violation"]',
        max_retries=3,
        secret_version=1,
        deleted_at=None,
        last_secret_rotation_at=None,
    )


def _mock_db(rows, total: int):
    """A session whose query chain is self-returning, as SQLAlchemy's is."""
    query = MagicMock()
    query.filter.return_value = query
    query.add_columns.return_value = query
    query.order_by.return_value = query
    query.offset.return_value = query
    query.limit.return_value = query
    query.all.return_value = rows
    query.count.return_value = total

    db = MagicMock()
    db.query.return_value = query
    return db, query


def _paged_rows(items, total: int):
    """Rows as `add_columns(func.count().over())` returns them."""

    class _Row:
        __slots__ = ("_values", "total_count")

        def __init__(self, item, count: int):
            self._values = (item, count)
            self.total_count = count

        def __getitem__(self, index):
            return self._values[index]

    return [_Row(item, total) for item in items]


def _install(rows, total: int):
    db, query = _mock_db(rows, total)

    def _get_db_override():
        yield db

    app.dependency_overrides[get_db] = _get_db_override
    return query


@pytest.fixture
def admin_override():
    def _fake_admin():
        return SimpleNamespace(email="webhook-admin@example.com", id="user_admin", role="admin")

    app.dependency_overrides[require_admin] = _fake_admin
    yield
    app.dependency_overrides.pop(require_admin, None)
    app.dependency_overrides.pop(get_db, None)


def test_webhook_list_accepts_the_shared_cap(admin_override):
    _install(_paged_rows([], 0), 0)

    resp = client.get(f"/api/v1/webhooks?page_size={MAX_PAGE_SIZE}")

    assert resp.status_code == 200
    assert resp.json()["page_size"] == MAX_PAGE_SIZE


def test_webhook_list_rejects_above_the_shared_cap(admin_override):
    _install(_paged_rows([], 0), 0)

    resp = client.get(f"/api/v1/webhooks?page_size={MAX_PAGE_SIZE + 1}")

    assert resp.status_code == 422


def test_webhook_deliveries_reject_above_the_shared_cap(admin_override):
    """Deliveries used to cap at le=200-by-luck while webhooks sat at 100."""
    _install(_paged_rows([], 0), 0)

    resp = client.get(f"/api/v1/webhooks/{'x' * 8}/deliveries?limit={MAX_PAGE_SIZE + 1}")

    assert resp.status_code == 422
