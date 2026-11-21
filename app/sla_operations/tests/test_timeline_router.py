from datetime import datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.sla_operations.api.timeline_router import (
    TimelineAssemblyRequest,
    TimelineAssemblyResponse,
    TimelineMergeRequest,
    assemble_timeline,
    assemble_timeline_endpoint,
    merge_timeline_batches,
    merge_timeline_batches_endpoint,
    router,
)


def _event(event_id, minute, *, domain="sla", kind="outage", summary=None):
    return {
        "event_id": event_id,
        "occurred_at": datetime(2026, 1, 1, 0, minute),
        "domain": domain,
        "kind": kind,
        "summary": summary or f"event {event_id}",
        "metadata": {"source": "workspace"},
    }


def test_router_is_workspace_local_and_exposes_assembly_routes():
    assert router.prefix == "/timeline"
    assert {route.path for route in router.routes} == {"/timeline/assemble", "/timeline/merge"}


def test_assemble_sorts_filters_deduplicates_and_caps_events():
    events = [
        _event("e-2", 2),
        _event("e-1", 1),
        _event("e-1", 3, summary="duplicate"),
        _event("w-1", 4, domain="webhook"),
    ]

    response = assemble_timeline(
        TimelineAssemblyRequest(events=events, domains=["SLA"], kinds=["OUTAGE"], max_events=2)
    )

    assert response.total_input == 4
    assert response.total_matched == 2
    assert response.returned == 2
    assert response.truncated is False
    assert [item.event_id for item in response.items] == ["e-1", "e-2"]
    assert [item.sequence for item in response.items] == [1, 2]
    assert response.items[0].summary == "event e-1"


def test_descending_order_truncates_and_reports_window():
    events = [_event("e-1", 1), _event("e-2", 2), _event("e-3", 3)]

    response = assemble_timeline({"events": events, "order": "desc", "max_events": 2})

    assert [item.event_id for item in response.items] == ["e-3", "e-2"]
    assert response.truncated is True
    assert response.window_start == response.items[0].occurred_at
    assert response.window_end == response.items[-1].occurred_at


def test_naive_timestamps_become_utc_and_empty_input_is_safe():
    response = assemble_timeline({"events": [_event("e-1", 1)]})

    assert response.items[0].occurred_at.utcoffset().total_seconds() == 0

    empty = assemble_timeline({})
    assert empty.items == []
    assert empty.total_input == 0
    assert empty.window_start is None
    assert empty.window_end is None
    assert empty.truncated is False


def test_merge_flattens_batches_and_uses_shared_options():
    request = TimelineMergeRequest(
        timelines=[
            [_event("e-1", 1), _event("e-2", 2)],
            [_event("e-2", 3), _event("e-3", 4)],
        ],
        max_events=1,
    )

    response = merge_timeline_batches(request)

    assert response.total_input == 4
    assert response.total_matched == 3
    assert [item.event_id for item in response.items] == ["e-1"]
    assert response.truncated is True


def test_handler_functions_return_typed_responses():
    assembled = assemble_timeline_endpoint(TimelineAssemblyRequest(events=[_event("e-1", 1)]))
    merged = merge_timeline_batches_endpoint(TimelineMergeRequest(timelines=[[_event("e-1", 1)]]))

    assert isinstance(assembled, TimelineAssemblyResponse)
    assert isinstance(merged, TimelineAssemblyResponse)
    assert assembled.model_dump()["items"][0]["event_id"] == "e-1"


def test_testclient_validates_requests_and_returns_explicit_shape():
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    response = client.post(
        "/timeline/assemble",
        json={
            "events": [
                {
                    "event_id": "e-1",
                    "occurred_at": "2026-01-01T00:00:00Z",
                    "domain": "sla",
                    "kind": "outage",
                }
            ]
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"items", "total_input", "total_matched", "returned", "truncated", "window_start", "window_end"}
    assert body["items"][0]["sequence"] == 1
    assert body["items"][0]["metadata"] == {}

    invalid_event = _event("e-1", 1)
    invalid_event["unexpected"] = "field"
    assert client.post("/timeline/assemble", json={"events": [invalid_event]}).status_code == 422
    assert client.post("/timeline/assemble", json={"events": [], "max_events": 0}).status_code == 422

    empty = client.post("/timeline/assemble", json={"events": []})
    assert empty.status_code == 200
    assert empty.json()["items"] == []
