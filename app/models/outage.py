from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


class Location(BaseModel):
    latitude: float
    longitude: float


class SLAStatus(BaseModel):
    status: str  # "in_progress", "met", "violated"
    mttr_minutes: int | None = None
    threshold_minutes: int
    time_remaining_minutes: int | None = None


class Outage(BaseModel):
    id: str = Field(..., description="Unique outage ID")
    site_name: str
    site_id: str | None = None
    severity: str  # critical, high, medium, low
    status: str  # active, resolved, investigating
    detected_at: datetime
    resolved_at: datetime | None = None
    description: str
    affected_services: list[str]
    affected_subscribers: int | None = None
    assigned_to: str | None = None
    created_by: str | None = None
    location: Location | None = None
    sla_status: SLAStatus | None = None

    @field_validator("detected_at")
    @classmethod
    def validate_detected_at_timezone(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValidationError("detected_at must be timezone-aware")
        # Normalize to UTC
        if v.tzinfo != UTC:
            v = v.astimezone(UTC)
        return v

    @field_validator("resolved_at")
    @classmethod
    def validate_resolved_at_timezone(cls, v: datetime | None) -> datetime | None:
        if v is None:
            return None
        if v.tzinfo is None:
            raise ValidationError("resolved_at must be timezone-aware")
        # Normalize to UTC
        if v.tzinfo != UTC:
            v = v.astimezone(UTC)
        return v


class PaginatedOutages(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "items": [
                    {
                        "id": "outage-001",
                        "site_name": "Site A",
                        "site_id": "site_1",
                        "severity": "high",
                        "status": "resolved",
                        "detected_at": "2023-10-01T12:00:00Z",
                        "resolved_at": "2023-10-01T12:45:00Z",
                        "description": "Fiber cut",
                        "affected_services": ["4G"],
                        "mttr_minutes": 45,
                        "assigned_to": None,
                        "created_by": "user1",
                        "location": {"latitude": 40.7128, "longitude": -74.0060},
                        "sla_status": "met"
                    }
                ],
                "total": 1,
                "page": 1,
                "page_size": 20
            }
        }
    )

    items: list[Outage]
    total: int
    page: int
    page_size: int


class ResolveOutageRequest(BaseModel):
    mttr_minutes: int
