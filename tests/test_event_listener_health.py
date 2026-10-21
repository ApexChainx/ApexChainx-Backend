"""Tests for the EventListener health check (issue #746).

Covers:

* :meth:`app.services.event_listener.EventListener.health` snapshot contents.
* ``GET /api/v1/events/health`` wiring through the application router.
* Response schema exposure in the generated OpenAPI document.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.v1.endpoints.event_listener import get_event_listener
from app.main import app
from app.services.event_listener import EventListener, event_listener


@pytest.fixture()
def isolated_listener() -> EventListener:
    """Return a listener that does not share state with other tests."""
    return EventListener()


@pytest.fixture()
def client(isolated_listener: EventListener):
    """TestClient with the events router bound to an isolated listener."""
    app.dependency_overrides[get_event_listener] = lambda: isolated_listener
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_event_listener, None)


class TestHealthSnapshot:
    """The service-level health snapshot."""

    def test_empty_listener_is_healthy(self, isolated_listener: EventListener) -> None:
        assert isolated_listener.health() == {
            "status": "ok",
            "total_events": 0,
            "topic_count": 0,
        }

    def test_counts_events_and_distinct_topics(self, isolated_listener: EventListener) -> None:
        isolated_listener.process_event({"topic": "outage.created", "payload": {}})
        isolated_listener.process_event({"topic": "outage.created", "payload": {}})
        isolated_listener.process_event({"topic": "sla.breached", "payload": {}})

        snapshot = isolated_listener.health()

        assert snapshot["status"] == "ok"
        assert snapshot["total_events"] == 3
        assert snapshot["topic_count"] == 2

    def test_events_without_topic_are_counted(self, isolated_listener: EventListener) -> None:
        isolated_listener.process_event({"payload": {}})

        snapshot = isolated_listener.health()

        assert snapshot["total_events"] == 1
        assert snapshot["topic_count"] == 1

    def test_snapshot_does_not_mutate_history(self, isolated_listener: EventListener) -> None:
        isolated_listener.process_event({"topic": "outage.created", "payload": {}})

        isolated_listener.health()

        assert isolated_listener.list_events()["total"] == 1


class TestHealthEndpoint:
    """The HTTP surface served by the application router."""

    def test_returns_200_and_snapshot(self, client: TestClient) -> None:
        response = client.get("/api/v1/events/health")

        assert response.status_code == 200
        assert response.json() == {"status": "ok", "total_events": 0, "topic_count": 0}

    def test_reflects_listener_state(self, client: TestClient, isolated_listener: EventListener) -> None:
        isolated_listener.process_event({"topic": "outage.created", "payload": {}})
        isolated_listener.process_event({"topic": "payment.completed", "payload": {}})

        body = client.get("/api/v1/events/health").json()

        assert body["total_events"] == 2
        assert body["topic_count"] == 2

    def test_is_reachable_without_authentication(self, client: TestClient) -> None:
        response = client.get("/api/v1/events/health")

        assert response.status_code == 200

    def test_router_is_mounted_under_api_v1(self, client: TestClient) -> None:
        assert client.get("/api/v1/events/health").status_code == 200
        assert client.get("/events/health").status_code == 404

    def test_rejects_non_get_methods(self, client: TestClient) -> None:
        assert client.post("/api/v1/events/health").status_code == 405


class TestHealthOpenAPIDocumentation:
    """The health endpoint must be documented in the OpenAPI schema."""

    def test_path_and_operation_are_documented(self) -> None:
        paths = app.openapi()["paths"]

        assert "/api/v1/events/health" in paths
        operation = paths["/api/v1/events/health"]["get"]

        assert operation["tags"] == ["events"]
        assert operation["summary"] == "EventListener health check"
        assert operation["description"]

    def test_response_schema_is_documented(self) -> None:
        schema = app.openapi()["components"]["schemas"]["EventListenerHealth"]

        assert set(schema["required"]) == {"status", "total_events", "topic_count"}
        assert schema["properties"]["status"]["const"] == "ok"


def test_module_singleton_is_used_by_default() -> None:
    """Without an override the router serves the module-level singleton."""
    event_listener.process_event({"topic": "singleton.check", "payload": {}})

    body = TestClient(app).get("/api/v1/events/health").json()
    assert body["total_events"] >= 1

    # Keep the shared singleton clean for other tests.
    event_listener._events.clear()
