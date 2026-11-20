from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.sla_operations.schemas.notifications import (
    NotificationSeverity,
    NotificationSubject,
    OperationalNotificationEventType,
    OperationalNotificationPayload,
)


def test_notification_subject_requires_identifier_and_payload_valid_sla_event():
    subject = NotificationSubject(outage_id="outage-1", sla_result_id=101)
    payload = OperationalNotificationPayload(
        event_id="evt-1",
        event_type=OperationalNotificationEventType.SLA_VIOLATION,
        severity=NotificationSeverity.CRITICAL,
        occurred_at=datetime(2026, 11, 20, 13, 30, tzinfo=UTC),
        subject=subject,
        title="SLA violated",
        message="MTTR exceeded threshold",
        attributes={"mttr_minutes": 95},
        dedupe_key="sla.violation:outage-1:101",
    )
    assert payload.event_type == OperationalNotificationEventType.SLA_VIOLATION
    assert payload.subject.outage_id == "outage-1"


def test_notification_subject_rejects_empty():
    with pytest.raises(ValidationError):
        NotificationSubject()


def test_notification_payload_requires_outage_id_for_outage_events():
    subject = NotificationSubject(site_id="site-1")
    with pytest.raises(ValidationError):
        OperationalNotificationPayload(
            event_id="evt-2",
            event_type=OperationalNotificationEventType.OUTAGE_DETECTED,
            severity=NotificationSeverity.WARNING,
            occurred_at=datetime(2026, 11, 20, tzinfo=UTC),
            subject=subject,
            title="Outage detected",
            message="Site unreachable",
        )


def test_notification_payload_requires_sla_identifier_for_sla_events():
    subject = NotificationSubject(site_id="site-1")
    with pytest.raises(ValidationError):
        OperationalNotificationPayload(
            event_id="evt-3",
            event_type=OperationalNotificationEventType.SLA_WARNING,
            severity=NotificationSeverity.WARNING,
            occurred_at=datetime(2026, 11, 20, tzinfo=UTC),
            subject=subject,
            title="SLA warning",
            message="Approaching threshold",
        )


def test_notification_payload_rejects_invalid_enum_blank_fields_and_extra():
    with pytest.raises(ValidationError):
        OperationalNotificationPayload.model_validate(
            {
                "event_id": "evt-4",
                "event_type": "not.real",
                "severity": "critical",
                "occurred_at": "2026-11-20T13:30:00Z",
                "subject": {"outage_id": "o1"},
                "title": "t",
                "message": "m",
            }
        )
    with pytest.raises(ValidationError):
        OperationalNotificationPayload(
            event_id="evt-5",
            event_type=OperationalNotificationEventType.OUTAGE_RESOLVED,
            severity=NotificationSeverity.INFO,
            occurred_at=datetime(2026, 11, 20, tzinfo=UTC),
            subject=NotificationSubject(outage_id="o1"),
            title=" ",
            message="Resolved",
        )
    with pytest.raises(ValidationError):
        OperationalNotificationPayload.model_validate(
            {
                "event_id": "evt-6",
                "event_type": "outage.resolved",
                "severity": "info",
                "occurred_at": "2026-11-20T13:30:00Z",
                "subject": {"outage_id": "o1"},
                "title": "Resolved",
                "message": "ok",
                "unknown": 1,
            }
        )
