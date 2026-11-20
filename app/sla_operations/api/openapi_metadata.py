"""OpenAPI metadata helpers for isolated SLA operations API modules."""

from typing import TypedDict

from fastapi import APIRouter


class OpenAPITag(TypedDict):
    name: str
    description: str


SLA_OPERATIONS_TAGS: tuple[OpenAPITag, ...] = (
    {
        "name": "SLA Operations",
        "description": "Workspace APIs for SLA analytics and outage operations.",
    },
    {
        "name": "SLA Metrics",
        "description": "Read-only SLA performance metrics and reporting helpers.",
    },
    {
        "name": "Outage Operations",
        "description": "Outage lifecycle and operational reporting helpers.",
    },
)

# Modules may import this router and add their own routes without wiring it into
# the application's core router.
router = APIRouter(tags=["SLA Operations"])


def get_openapi_tags() -> list[OpenAPITag]:
    """Return fresh FastAPI ``openapi_tags`` entries for the workspace."""
    return [OpenAPITag(name=tag["name"], description=tag["description"]) for tag in SLA_OPERATIONS_TAGS]


def build_openapi_metadata(
    *,
    summary: str,
    description: str,
    tags: list[str] | None = None,
) -> dict[str, str | list[str]]:
    """Build deterministic operation metadata suitable for FastAPI route options."""
    metadata: dict[str, str | list[str]] = {
        "summary": summary,
        "description": description,
    }
    if tags is not None:
        metadata["tags"] = list(tags)
    return metadata
