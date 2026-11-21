# SLA Operations Workspace — API Modules

This document describes the **workspace-local** API helper modules that live under
`app/sla_operations/api/`. These modules provide the building blocks for exposing
SLA operations functionality as HTTP endpoints, but they are intentionally
**isolated from the core application wiring**.

> **Important boundary note**
>
> The helpers described here are **workspace-local**. They are **NOT** registered
> in `app/api/v1/router.py`, and they do **not** alter any existing routed
> behavior. Nothing in this document prescribes changes to core runtime
> components. Any future integration is described only as *optional context* so
> contributors understand the seams — not as a required or recommended change.

---

## Scope and layout

All modules referenced here live under `app/sla_operations/api/`:

| Module | Purpose |
| --- | --- |
| `dependencies.py` | FastAPI dependency that resolves the workspace service container from `app.state`. |
| `error_mapping.py` | Maps workspace exceptions to HTTP status codes / error payloads. |
| `openapi_metadata.py` | Shared OpenAPI tags, summaries, and response metadata. |
| `request_parsing.py` | Helpers for validating and normalizing incoming request data. |
| `responses.py` | `ResponseEnvelope` model and `success_response` helper. |
| `router.py` | `create_workspace_router` factory and a module-level `router`. |
| `timeline_router.py` | Router for SLA timeline operations. |

These modules are **additive** and self-contained. They import from the standard
library, FastAPI/Pydantic, and workspace-local code only.

---

## `dependencies.py`

**Purpose:** Provide a FastAPI dependency that resolves the workspace's service
container from the application state, so route handlers can access services
without importing concrete implementations directly.

**Key function:**

- `get_service(request: Request) -> SlaOperationsServices`
  - Reads `request.app.state.sla_operations_services`.
  - Raises a clear error if the container has not been attached to the app
    state (i.e., the workspace was never wired up).

**Example usage:**

```python
from fastapi import APIRouter, Depends

from app.sla_operations.api.dependencies import get_service
from app.sla_operations.services import SlaOperationsServices

router = APIRouter()


@router.get("/status")
def read_status(services: SlaOperationsServices = Depends(get_service)) -> dict:
    return {"ready": services.is_ready()}
```

**Integration note:** The dependency expects the host application to attach a
service container to `app.state.sla_operations_services`. This document does not
require or prescribe that attachment — it is simply the contract the dependency
relies on. Tests can satisfy it by setting the attribute on a test app instance.

---

## `error_mapping.py`

**Purpose:** Centralize the translation of workspace-specific exceptions into
HTTP responses, keeping route handlers free of repetitive `try/except` blocks.

**Key functions:**

- `map_exception(exc: Exception) -> tuple[int, dict]`
  - Returns an HTTP status code and a JSON-serializable error body for a known
    workspace exception.
- `register_error_handlers(app: FastAPI) -> None`
  - Optional helper that registers the mapping as exception handlers on a given
    app instance (used only when a contributor chooses to mount the workspace
    router on a standalone app, e.g., in tests).

**Example usage:**

```python
from app.sla_operations.api.error_mapping import map_exception
from app.sla_operations.exceptions import TimelineNotFoundError

try:
    ...
except TimelineNotFoundError as exc:
    status_code, body = map_exception(exc)
    # status_code == 404, body == {"error": "timeline_not_found", ...}
```

**Integration note:** For read-only context on how the core application maps
coded errors and registers exception handlers, see
`app/api/coded_errors.py` and `app/api/exception_handlers.py`. Those modules are
referenced **only** as context; this workspace does not modify them.

---

## `openapi_metadata.py`

**Purpose:** Hold shared OpenAPI metadata (tags, summaries, descriptions, and
common response declarations) so routers stay declarative and consistent.

**Key constants / helpers:**

- `WORKSPACE_TAG` — the OpenAPI tag applied to workspace routes.
- `COMMON_RESPONSES` — a dict of reusable response declarations (e.g., 400/404/500).
- `build_router_metadata(...)` — helper that assembles `tags`, `responses`, and
  `summary` kwargs for `APIRouter` / route decorators.

**Example usage:**

```python
from fastapi import APIRouter

from app.sla_operations.api.openapi_metadata import COMMON_RESPONSES, WORKSPACE_TAG

router = APIRouter(tags=[WORKSPACE_TAG], responses=COMMON_RESPONSES)
```

---

## `request_parsing.py`

**Purpose:** Provide reusable parsing/validation helpers for request payloads and
query parameters, keeping validation logic consistent and testable outside of
route handlers.

**Key functions:**

- `parse_timeline_query(params: Mapping[str, Any]) -> TimelineQuery`
  - Validates and normalizes timeline query parameters into a typed object.
- `parse_pagination(params: Mapping[str, Any]) -> Pagination`
  - Extracts and validates `limit` / `offset` with sane defaults and bounds.

**Example usage:**

```python
from app.sla_operations.api.request_parsing import parse_pagination


@router.get("/timelines")
def list_timelines(params: dict = Depends(...)) -> ...:
    pagination = parse_pagination(params)
    ...
```

**Security note:** These helpers perform input validation only. They never log
raw request bodies or secrets.

---

## `responses.py`

**Purpose:** Define a consistent response envelope for workspace endpoints.

**Key definitions:**

- `ResponseEnvelope` — a Pydantic model wrapping `data`, optional `meta`, and
  optional `error` fields.
- `success_response(data, meta=None) -> ResponseEnvelope`
  - Builds a success envelope with `error=None`.

**Example usage:**

```python
from app.sla_operations.api.responses import ResponseEnvelope, success_response


@router.get("/timelines/{timeline_id}", response_model=ResponseEnvelope)
def get_timeline(timeline_id: str) -> ResponseEnvelope:
    timeline = ...
    return success_response(timeline)
```

---

## `router.py`

**Purpose:** Assemble the workspace router from its sub-routers and expose both a
factory and a module-level instance.

**Key definitions:**

- `create_workspace_router() -> APIRouter`
  - Builds a fresh `APIRouter` with workspace metadata and includes the
    timeline sub-router. Useful for tests and for hosts that want an isolated
    instance.
- `router` — a module-level `APIRouter` created via `create_workspace_router()`,
  provided for convenience.

**Example usage:**

```python
from fastapi import FastAPI

from app.sla_operations.api.router import create_workspace_router

app = FastAPI()
app.include_router(create_workspace_router(), prefix="/sla-operations")
```

**Integration note (optional, not prescribed):** A host application *could*
mount this router by including it in its own router composition. This document
does **not** require or recommend modifying `app/api/v1/router.py` or any core
router. The workspace remains fully functional and testable in isolation.

---

## `timeline_router.py`

**Purpose:** Define the HTTP routes for SLA timeline operations (listing and
retrieving timelines), delegating business logic to the workspace services
resolved via `get_service`.

**Key definitions:**

- `router` — an `APIRouter` with timeline routes, tagged via
  `openapi_metadata.WORKSPACE_TAG` and using `COMMON_RESPONSES`.
- Route handlers depend on `get_service` and return `ResponseEnvelope` payloads
  built with `success_response`.

**Example usage:**

```python
from app.sla_operations.api.timeline_router import router as timeline_router

app.include_router(timeline_router, prefix="/sla-operations")
```

---

## Boundary and integration summary

- **Workspace-local only.** All modules above live under
  `app/sla_operations/api/` and are not imported by core routed domains.
- **Not registered in core.** Nothing here is included in
  `app/api/v1/router.py`. Existing API behavior is unchanged.
- **Read-only context.** `app/api/coded_errors.py` and
  `app/api/exception_handlers.py` are referenced only to explain how the core
  application handles errors; the workspace neither imports nor modifies them.
- **Optional mounting.** A host may choose to include the workspace router, but
  doing so is a host decision and is not required by this workspace.

---

## Testing the workspace API modules

Because the modules are isolated, they can be exercised with a minimal test app:

```python
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.sla_operations.api.router import create_workspace_router


def make_client(services) -> TestClient:
    app = FastAPI()
    app.state.sla_operations_services = services
    app.include_router(create_workspace_router(), prefix="/sla-operations")
    return TestClient(app)
```

This pattern keeps tests deterministic and avoids touching core application
wiring.