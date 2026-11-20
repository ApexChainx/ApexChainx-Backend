from datetime import date

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.sla_operations.api.dependencies import get_service
from app.sla_operations.api.request_parsing import parse_date_range, parse_pagination
from app.sla_operations.api.responses import success_response
from app.sla_operations.api.router import create_workspace_router, router


def test_workspace_router_composes_subrouters_without_core_wiring() -> None:
    child_router = APIRouter()

    @child_router.get("/status")
    def status_endpoint() -> dict[str, str]:
        return {"status": "ready"}

    app = FastAPI()
    app.include_router(create_workspace_router(child_router, prefix="/operations", tags=["sla-operations"]))

    response = TestClient(app).get("/operations/status")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
    assert not router.routes


def test_service_dependency_resolves_registered_workspace_service() -> None:
    service = object()
    app = FastAPI()
    app.state.sla_operations_services = {"reports": service}

    @app.get("/service")
    def service_endpoint(resolved: object = Depends(get_service("reports"))) -> dict[str, bool]:
        return {"resolved": resolved is service}

    response = TestClient(app).get("/service")

    assert response.status_code == 200
    assert response.json() == {"resolved": True}


def test_service_dependency_reports_missing_service() -> None:
    app = FastAPI()

    @app.get("/service")
    def service_endpoint(resolved: object = Depends(get_service("reports"))) -> dict[str, bool]:
        return {"resolved": resolved is not None}

    response = TestClient(app).get("/service")

    assert response.status_code == 503
    assert response.json()["detail"] == "SLA operations service 'reports' is not configured"


def test_response_envelope_and_request_parsing_helpers() -> None:
    response = success_response({"count": 2}, message="loaded")
    pagination = parse_pagination(page=3, page_size=20)
    date_range = parse_date_range(date(2026, 1, 1), date(2026, 1, 31))

    assert response.model_dump() == {"success": True, "message": "loaded", "data": {"count": 2}}
    assert (pagination.page, pagination.page_size, pagination.offset) == (3, 20, 40)
    assert date_range.start_date == date(2026, 1, 1)
    assert date_range.end_date == date(2026, 1, 31)

    with pytest.raises(ValidationError):
        parse_pagination(page=0)
    with pytest.raises(ValidationError):
        parse_date_range(date(2026, 2, 1), date(2026, 1, 1))
