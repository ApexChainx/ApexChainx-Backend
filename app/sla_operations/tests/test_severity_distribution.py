from app.sla_operations.services.severity_distribution_report import compute_severity_distribution


def test_counts_and_ratios_by_severity_and_status():
    outages = [
        {"severity": "critical", "status": "open"},
        {"severity": " Critical ", "status": "open"},
        {"severity": "high", "status": "resolved"},
        {"severity": "low", "status": "open"},
    ]

    report = compute_severity_distribution(outages)

    assert report.total == 4
    assert report.unclassified == 0
    assert list(report.by_severity) == ["critical", "high", "medium", "low", "unknown"]
    assert report.by_severity["critical"].count == 2
    assert report.by_severity["critical"].ratio == 0.5
    assert report.by_severity["critical"].statuses == {"open": 2, "resolved": 0, "unknown": 0}
    assert report.by_severity["critical"].status_ratios == {"open": 1.0, "resolved": 0.0, "unknown": 0.0}
    assert report.by_severity["high"].status_ratios["resolved"] == 1.0
    assert report.by_status["open"].count == 3
    assert report.by_status["open"].ratio == 0.75
    assert report.by_status["resolved"].ratio == 0.25


def test_empty_dataset_has_zeroed_canonical_categories():
    report = compute_severity_distribution([])

    assert report.total == 0
    assert report.unclassified == 0
    assert all(bucket.count == 0 and bucket.ratio == 0.0 for bucket in report.by_severity.values())
    assert all(total.count == 0 and total.ratio == 0.0 for total in report.by_status.values())
    assert list(report.by_severity) == ["critical", "high", "medium", "low", "unknown"]
    assert list(report.by_status) == ["open", "resolved", "unknown"]


def test_missing_and_unknown_values_are_bucketed():
    outages = [
        {},
        None,
        {"severity": " critical ", "status": None},
        {"severity": "SEV1", "status": "investigating"},
    ]

    report = compute_severity_distribution(outages)

    assert report.total == 4
    assert report.unclassified == 2
    assert report.by_severity["unknown"].count == 2
    assert report.by_severity["critical"].count == 1
    assert report.by_severity["sev1"].count == 1
    assert list(report.by_severity)[-1] == "sev1"
    assert report.by_status["unknown"].count == 3
    assert report.by_status["investigating"].count == 1
    assert report.by_status["investigating"].ratio == 0.25


def test_configured_categories_are_present_even_when_empty():
    report = compute_severity_distribution([], severities=["sev1"], statuses=["monitoring"])

    assert report.by_severity["sev1"].count == 0
    assert report.by_status["monitoring"].ratio == 0.0
    assert list(report.by_severity)[-1] == "sev1"
    assert list(report.by_status)[-1] == "monitoring"


def test_ratios_are_rounded_to_four_decimals():
    report = compute_severity_distribution(
        [
            {"severity": "critical", "status": "open"},
            {"severity": "high", "status": "open"},
            {"severity": "low", "status": "open"},
        ]
    )

    assert report.by_severity["critical"].ratio == 0.3333
    assert report.by_status["open"].ratio == 1.0


def test_none_and_non_iterable_inputs_are_safe():
    for outages in (None, 42):
        report = compute_severity_distribution(outages)
        assert report.total == 0
        assert report.unclassified == 0


def test_input_rows_are_not_mutated():
    outages = [{"severity": " HIGH ", "status": "Open"}]
    before = [dict(outages[0])]

    compute_severity_distribution(outages)

    assert outages == before
