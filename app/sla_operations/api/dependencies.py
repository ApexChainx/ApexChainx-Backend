"""Dependency providers for services registered by the SLA operations workspace."""

from collections.abc import Callable, Mapping
from typing import TypeVar

from fastapi import HTTPException, Request, status

ServiceT = TypeVar("ServiceT")


def get_service(service_name: str) -> Callable[[Request], ServiceT]:
    """Create a FastAPI dependency that resolves a service from app state.

    The application may register workspace services in
    ``app.state.sla_operations_services`` as a name-to-instance mapping.
    """
    if not service_name.strip():
        raise ValueError("service_name must not be empty")

    def dependency(request: Request) -> ServiceT:
        services = getattr(request.app.state, "sla_operations_services", None)
        if not isinstance(services, Mapping) or service_name not in services:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"SLA operations service '{service_name}' is not configured",
            )
        return services[service_name]

    return dependency
