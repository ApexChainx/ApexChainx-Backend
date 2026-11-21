"""Tests for the analytics-query workspace schemas (#852)."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.sla_operations.schemas.analytics_queries import (
    MAX_QUERY_RANGE_DAYS,
    AnalyticsAggregateResponse,
    AnalyticsBucket,
    AnalyticsGranularity,
    AnalyticsGroupBy,
    AnalyticsMetric,
    AnalyticsOrderBy,
    AnalyticsQueryParams,
    AnalyticsSeverity,
    AnalyticsSortDirection,
)

START = datetime(2026, 1, 1, tzinfo=UTC)


def query_payload(**overrides):
    payload = {
        "period_start": START,
        "period_end": START + timedelta(days=30),
        "metrics": [AnalyticsMetric.OUTAGE_COUNT, AnalyticsMetric.MTTR_MINUTES],
    }
    payload.update(overrides)
    return payload


def bucket_payload(**overrides):
    payload = {
        "bucket_start": START,
        "bucket_end": START + timedelta(days=1),
        "metric_values": {AnalyticsMetric.OUTAGE_COUNT: 2.0, AnalyticsMetric.MTTR_MINUTES: 45.5},
        "sample_count": 2,
    }
    payload.update(overrides)
    return payload


def response_payload(**overrides):
    payload = {
        "query": query_payload(),
        "buckets": [bucket_payload()],
        "totals": {AnalyticsMetric.OUTAGE_COUNT: 2.0, AnalyticsMetric.MTTR_MINUTES: 45.5},
        "total_buckets": 1,
        "returned_buckets": 1,
        "total_records": 2,
        "has_more": False,
        "generated_at": START + timedelta(days=31),
    }
    payload.update(overrides)
    return payload


def test_query_accepts_defaults_and_enum_values():
    query = AnalyticsQueryParams.model_validate(
        query_payload(granularity="week", group_by="severity", metrics=["availability_percentage"])
    )

    assert query.granularity is AnalyticsGranularity.WEEK
    assert query.group_by is AnalyticsGroupBy.SEVERITY
    assert query.metrics == [AnalyticsMetric.AVAILABILITY_PERCENTAGE]
    assert query.limit == 100
    assert query.offset == 0
    assert query.include_partial is False
    assert query.order_by is AnalyticsOrderBy.BUCKET_START
    assert query.sort_direction is AnalyticsSortDirection.ASC


@pytest.mark.parametrize(
    "overrides",
    [
        {"period_end": START},
        {"period_end": START + timedelta(days=MAX_QUERY_RANGE_DAYS + 1)},
        {"period_start": START.replace(tzinfo=None)},
        {"metrics": []},
        {"metrics": [AnalyticsMetric.OUTAGE_COUNT] * 2},
        {"metrics": [AnalyticsMetric.OUTAGE_COUNT] * 11},
        {"site_ids": ["site-1", "site-1"]},
        {"site_ids": [""]},
        {"site_ids": [f"site-{index}" for index in range(101)]},
        {"severities": [AnalyticsSeverity.CRITICAL] * 2},
        {"limit": 0},
        {"limit": 1001},
        {"offset": -1},
        {"unexpected": True},
    ],
)
def test_query_rejects_out_of_range_or_duplicate_inputs(overrides):
    with pytest.raises(ValidationError):
        AnalyticsQueryParams.model_validate(query_payload(**overrides))


def test_query_accepts_maximum_allowed_range_and_page_size():
    query = AnalyticsQueryParams.model_validate(
        query_payload(period_end=START + timedelta(days=MAX_QUERY_RANGE_DAYS), limit=1000)
    )

    assert query.limit == 1000


def test_bucket_accepts_grouped_payload():
    bucket = AnalyticsBucket.model_validate(bucket_payload(dimension_value="critical"))

    assert bucket.dimension_value == "critical"
    assert bucket.sample_count == 2


@pytest.mark.parametrize(
    "overrides",
    [
        {"bucket_end": START},
        {"bucket_start": START.replace(tzinfo=None)},
        {"sample_count": -1},
        {"metric_values": {AnalyticsMetric.OUTAGE_COUNT: -1.0}},
        {"dimension_value": ""},
    ],
)
def test_bucket_rejects_invalid_payloads(overrides):
    with pytest.raises(ValidationError):
        AnalyticsBucket.model_validate(bucket_payload(**overrides))


def test_response_accepts_paginated_payload():
    response = AnalyticsAggregateResponse.model_validate(
        response_payload(query=query_payload(limit=1), total_buckets=2, has_more=True)
    )

    assert response.returned_buckets == 1
    assert response.has_more is True


@pytest.mark.parametrize(
    "overrides",
    [
        {"returned_buckets": 0},
        {"total_buckets": 0},
        {"has_more": True},
        {"totals": {AnalyticsMetric.OUTAGE_COUNT: 2.0}},
        {"buckets": [bucket_payload(metric_values={AnalyticsMetric.REWARD_AMOUNT: 10.0})]},
        {"buckets": [bucket_payload(sample_count=11)]},
        {"query": query_payload(limit=0)},
        {
            "buckets": [
                bucket_payload(),
                bucket_payload(
                    bucket_start=START + timedelta(days=1), bucket_end=START + timedelta(days=2)
                ),
            ],
            "returned_buckets": 2,
            "total_buckets": 2,
        },
        {"generated_at": START.replace(tzinfo=None)},
    ],
)
def test_response_rejects_inconsistent_payloads(overrides):
    with pytest.raises(ValidationError):
        AnalyticsAggregateResponse.model_validate(response_payload(**overrides))


def test_enum_values_are_machine_readable():
    assert AnalyticsGranularity.HOUR.value == "hour"
    assert AnalyticsGroupBy.SITE_ID.value == "site_id"
    assert AnalyticsMetric.MTTR_MINUTES.value == "mttr_minutes"
    assert AnalyticsOrderBy.SAMPLE_COUNT.value == "sample_count"
    assert AnalyticsSortDirection.DESC.value == "desc"
