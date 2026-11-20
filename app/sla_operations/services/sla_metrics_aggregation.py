"""Deterministic aggregation helpers for SLA operation metrics."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class SLAMetricsAggregation:
    """Aggregated SLA metrics for a collection of outage records."""

    total_outages: int
    violation_rate: float
    avg_mttr: float
    payout_sum: float

    def to_dict(self) -> dict[str, int | float]:
        return asdict(self)

    def model_dump(self) -> dict[str, int | float]:
        """Provide Pydantic-style serialization for workspace consumers."""
        return self.to_dict()

    def __getitem__(self, key: str) -> int | float:
        return getattr(self, key)


class SLAMetricsAggregationService:
    """Aggregate SLA metrics without database or application-state dependencies."""

    VIOLATED_STATUSES = frozenset({"violated", "breached", "violation"})

    def aggregate(self, records: Iterable[Any]) -> SLAMetricsAggregation:
        if records is None:
            raise TypeError("records must be an iterable, not None")
        if isinstance(records, (str, bytes, Mapping)):
            raise TypeError("records must be an iterable of SLA records")

        try:
            iterator = iter(records)
        except TypeError as exc:
            raise TypeError("records must be an iterable of SLA records") from exc

        mttr_values: list[float] = []
        payout_values: list[float] = []
        violations = 0
        outage_count = 0

        for index, record in enumerate(iterator):
            status = self._field(record, "status", index)
            mttr = self._finite_number(record, "mttr_minutes", index)
            amount = self._finite_number(record, "amount", index)

            if mttr < 0:
                raise ValueError(f"Record {index} has a negative mttr_minutes")
            if not isinstance(status, str) or not status.strip():
                raise ValueError(f"Record {index} has an invalid status")

            outage_count += 1
            mttr_values.append(mttr)
            payout_values.append(amount)
            if status.strip().lower() in self.VIOLATED_STATUSES:
                violations += 1

        if outage_count == 0:
            return SLAMetricsAggregation(
                total_outages=0,
                violation_rate=0.0,
                avg_mttr=0.0,
                payout_sum=0.0,
            )

        violation_rate = violations / outage_count
        return SLAMetricsAggregation(
            total_outages=outage_count,
            violation_rate=round(violation_rate, 4),
            avg_mttr=round(sum(mttr_values) / outage_count, 2),
            payout_sum=round(sum(payout_values), 2),
        )

    def aggregate_metrics(self, records: Iterable[Any]) -> SLAMetricsAggregation:
        return self.aggregate(records)

    @staticmethod
    def _field(record: Any, name: str, index: int) -> Any:
        if isinstance(record, Mapping):
            if name not in record:
                raise ValueError(f"Record {index} is missing required field '{name}'")
            return record[name]
        if not hasattr(record, name):
            raise ValueError(f"Record {index} is missing required field '{name}'")
        return getattr(record, name)

    @classmethod
    def _finite_number(cls, record: Any, name: str, index: int) -> float:
        value = cls._field(record, name, index)
        if isinstance(value, bool):
            raise ValueError(f"Record {index} has an invalid {name}")
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Record {index} has an invalid {name}") from exc
        if not math.isfinite(number):
            raise ValueError(f"Record {index} has a non-finite {name}")
        return number


# Common names make the workspace helper easy to discover and compose.
SlaMetricsAggregationService = SLAMetricsAggregationService
SLAMetricsAggregation = SLAMetricsAggregation


def aggregate_sla_metrics(records: Iterable[Any]) -> SLAMetricsAggregation:
    return SLAMetricsAggregationService().aggregate(records)
