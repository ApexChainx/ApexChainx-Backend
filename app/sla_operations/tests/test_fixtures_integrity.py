from datetime import datetime

from app.sla_operations.fixtures import get_sample_outage_summaries


def test_sample_outage_summaries_are_deterministic() -> None:
    first = get_sample_outage_summaries()
    second = get_sample_outage_summaries()

    assert first == second
    assert [sample["outage_id"] for sample in first] == [
        "sample-outage-met",
        "sample-outage-violated",
        "sample-outage-open",
    ]
    assert [(sample["severity"], sample["mttr_minutes"]) for sample in first] == [
        ("high", 30),
        ("critical", 90),
        ("medium", None),
    ]


def test_sample_outage_summaries_have_consistent_fields_and_times() -> None:
    required_fields = {
        "outage_id",
        "severity",
        "status",
        "started_at",
        "resolved_at",
        "mttr_minutes",
        "threshold_minutes",
        "affected_services",
    }

    for sample in get_sample_outage_summaries():
        assert set(sample) == required_fields
        assert sample["outage_id"]
        assert sample["severity"] in {"critical", "high", "medium", "low"}
        assert sample["threshold_minutes"] > 0
        assert sample["affected_services"]
        assert all(isinstance(service, str) and service for service in sample["affected_services"])

        started_at = datetime.fromisoformat(sample["started_at"])
        assert started_at.tzinfo is not None
        if sample["status"] == "resolved":
            assert sample["resolved_at"] is not None
            assert sample["mttr_minutes"] is not None
            resolved_at = datetime.fromisoformat(sample["resolved_at"])
            assert resolved_at.tzinfo is not None
            assert int((resolved_at - started_at).total_seconds() // 60) == sample["mttr_minutes"]
        else:
            assert sample["resolved_at"] is None
            assert sample["mttr_minutes"] is None


def test_sample_outage_summaries_do_not_leak_mutations_between_calls() -> None:
    first = get_sample_outage_summaries()
    first[0]["affected_services"].append("mutated-service")
    first[0]["outage_id"] = "mutated-outage"

    second = get_sample_outage_summaries()
    assert second[0]["outage_id"] == "sample-outage-met"
    assert second[0]["affected_services"] == ["mobile-data", "voice"]
