"""Tests for the SLA operations workspace aggregate router.

These tests validate that ``app.sla_operations.api.router`` exposes the
expected sub-routes and that the aggregate router can be mounted on a
standalone FastAPI application. All tests are deterministic and isolated
from core application state (no database, no global app instance).
"""

from __future__ import annotations

import importlib
from typing import Iterable, List

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.sla_operations.api import router as workspace_router_module


EXPECTED_PATHS: List[str] = [
    "/timeline/assemble",
    "/timeline/merge",
    "/webhook-monitoring/view",
    "/webhook-monitoring/health",
    "/bulk-validation/preview",
    "/health-indicators/aggregate",
    "/health-indicators/sample",
]


def _collect_paths(routes: Iterable) -> List[str]:
    """Return the set of route paths from a router/app, flattened."""
    paths: List[str] = []
    for route in routes:
        if isinstance(route, APIRoute):
            paths.append(route.path)
        else:
            # Nested routers (e.g. Mount) expose ``routes`` recursively.
            nested = getattr(route, "routes", None)
            if nested:
                paths.extend(_collect_paths(nested))
    return paths


def test_workspace_router_module_exposes_router() -> None:
    """The workspace router module must expose a module-level ``router``."""
    assert hasattr(workspace_router_module, "router")
    assert workspace_router_module.router is not None


def test_workspace_router_includes_expected_paths() -> None:
    """The aggregate router must include all expected sub-route paths."""
    paths = _collect_paths(workspace_router_module.router.routes)
    missing = [expected for expected in EXPECTED_PATHS if expected not in paths]
    assert not missing, f"Missing expected workspace routes: {missing}"


@pytest.mark.parametrize("expected_path", EXPECTED_PATHS)
def test_workspace_router_contains_individual_path(expected_path: str) -> None:
    """Each expected path is present individually (clearer failure output)."""
    paths = _collect_paths(workspace_router_module.router.routes)
    assert expected_path in paths


def test_workspace_router_paths_are_unique() -> None:
    """The aggregate router should not register duplicate paths."""
    paths = _collect_paths(workspace_router_module.router.routes)
    duplicates = {p for p in paths if paths.count(p) > 1}
    assert not duplicates, f"Duplicate workspace routes: {duplicates}"


def _build_test_app() -> FastAPI:
    app = FastAPI()
    app.include_router(workspace_router_module.router)
    return app


def test_workspace_router_mounts_on_fastapi_app() -> None:
    """The aggregate router can be mounted on a standalone FastAPI app."""
    app = _build_test_app()
    mounted_paths = _collect_paths(app.routes)
    for expected_path in EXPECTED_PATHS:
        assert expected_path in mounted_paths


@pytest.mark.parametrize("expected_path", EXPECTED_PATHS)
def test_workspace_router_paths_respond(expected_path: str) -> None:
    """Smoke-test each mounted path responds with a non-5xx status code.

    We accept 200 (success), 422 (validation error for missing payload),
    or 405 (method not allowed) as evidence the route is wired up. The
    key assertion is that we do not get a 404 (route missing) or a 5xx
    (unhandled server error).
    """
    app = _build_test_app()
    client = TestClient(app)

    # Try POST first (most workspace routes are POST), fall back to GET.
    response = client.post(expected_path, json={})
    if response.status_code == 405:
        response = client.get(expected_path)

    assert response.status_code != 404, (
        f"Route {expected_path} not found on mounted app"
    )
    assert response.status_code < 500, (
        f"Route {expected_path} returned server error "
        f"{response.status_code}: {response.text}"
    )
    assert response.status_code in (200, 201, 204, 400, 401, 403, 405, 422), (
        f"Unexpected status {response.status_code} for {expected_path}"
    )


def test_create_workspace_router_composes_subrouters() -> None:
    """``create_workspace_router`` composes subrouters with prefix and tags."""
    factory = getattr(workspace_router_module, "create_workspace_router", None)
    if factory is None:
        pytest.skip("create_workspace_router not provided by workspace module")

    prefix = "/custom-prefix"
    tags = ["custom-tag"]
    composed = factory(prefix=prefix, tags=tags)

    composed_paths = _collect_paths(composed.routes)
    assert composed_paths, "Composed router should contain at least one route"

    for path in composed_paths:
        assert path.startswith(prefix), (
            f"Composed route {path} does not start with prefix {prefix}"
        )

    for route in composed.routes:
        if isinstance(route, APIRoute):
            assert "custom-tag" in (route.tags or []), (
                f"Route {route.path} missing expected tag 'custom-tag'"
            )


def test_create_workspace_router_default_composition() -> None:
    """``create_workspace_router`` with defaults still yields expected paths."""
    factory = getattr(workspace_router_module, "create_workspace_router", None)
    if factory is None:
        pytest.skip("create_workspace_router not provided by workspace module")

    composed = factory()
    composed_paths = _collect_paths(composed.routes)

    for expected_path in EXPECTED_PATHS:
        assert expected_path in composed_paths, (
            f"Default composition missing expected path {expected_path}"
        )


def test_workspace_router_module_reimport_is_stable() -> None:
    """Re-importing the module yields the same router object (no side effects)."""
    reloaded = importlib.import_module("app.sla_operations.api.router")
    assert reloaded.router is workspace_router_module.router