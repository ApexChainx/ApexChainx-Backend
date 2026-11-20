"""Unit tests for operator annotation schemas."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.sla_operations.schemas.annotations import (
    MAX_BODY_LENGTH,
    MAX_TAG_LENGTH,
    Annotation,
    AnnotationCreate,
    AnnotationKind,
    AnnotationRecordType,
    AnnotationUpdate,
    AnnotationVisibility,
)


def _create_payload(**overrides):
    payload = {
        "record_type": "outage",
        "record_id": "outage-1",
        "author": "operator@example.com",
        "body": "Fiber cut confirmed by field team.",
        "created_at": "2026-01-01T10:00:00Z",
    }
    payload.update(overrides)
    return payload


def test_annotation_create_applies_defaults_and_normalizes_time():
    annotation = AnnotationCreate(**_create_payload(created_at="2026-01-01T12:00:00+02:00"))
    assert annotation.kind is AnnotationKind.note
    assert annotation.visibility is AnnotationVisibility.internal
    assert annotation.tags == []
    assert annotation.created_at == datetime(2026, 1, 1, 10, tzinfo=UTC)
    assert AnnotationCreate(**_create_payload()).created_at.tzinfo is UTC


@pytest.mark.parametrize(
    "overrides",
    [
        {"record_type": "unknown"},
        {"record_id": ""},
        {"body": "   "},
        {"body": "x" * (MAX_BODY_LENGTH + 1)},
        {"tags": [""]},
        {"tags": ["x" * (MAX_TAG_LENGTH + 1)]},
        {"created_at": "2026-01-01T00:00:00"},
        {"author": ""},
    ],
)
def test_annotation_create_rejects_invalid_payloads(overrides):
    with pytest.raises(ValidationError):
        AnnotationCreate(**_create_payload(**overrides))


def test_annotation_create_accepts_full_payload():
    annotation = AnnotationCreate(
        **_create_payload(
            kind="escalation",
            visibility="team",
            tags=["sla", "escalated"],
        )
    )
    assert annotation.kind is AnnotationKind.escalation
    assert annotation.visibility is AnnotationVisibility.team
    assert annotation.tags == ["sla", "escalated"]


def test_stored_annotation_validates_timestamp_order_and_reads_attributes():
    stored = Annotation(
        id="ann-1",
        **_create_payload(),
        updated_at="2026-01-02T00:00:00Z",
    )
    assert stored.record_type is AnnotationRecordType.outage
    assert stored.updated_at == datetime(2026, 1, 2, tzinfo=UTC)
    raw = SimpleNamespace(id="ann-2", updated_at=None, **stored.model_dump(exclude={"id", "updated_at"}))
    assert Annotation.model_validate(raw).id == "ann-2"
    with pytest.raises(ValidationError, match="updated_at cannot be before created_at"):
        Annotation(id="ann-3", **_create_payload(updated_at="2025-12-31T00:00:00Z"))
    with pytest.raises(ValidationError, match="timezone-aware"):
        Annotation(id="ann-4", **_create_payload(updated_at="2026-01-01T00:00:00"))


def test_annotation_update_requires_a_change_and_validates_values():
    assert AnnotationUpdate(body="Updated note.").body == "Updated note."
    assert AnnotationUpdate(tags=["reviewed"], visibility="public").tags == ["reviewed"]
    with pytest.raises(ValidationError, match="at least one annotation field"):
        AnnotationUpdate()
    with pytest.raises(ValidationError, match="body cannot be blank"):
        AnnotationUpdate(body=" ")
    with pytest.raises(ValidationError, match="tags cannot contain blank values"):
        AnnotationUpdate(tags=[" "])
    with pytest.raises(ValidationError):
        AnnotationUpdate(kind="not-a-kind")
