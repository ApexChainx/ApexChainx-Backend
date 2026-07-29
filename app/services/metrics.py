from __future__ import annotations

import time
import threading
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field


@dataclass
class MetricPoint:
    timestamp: datetime
    value: float
    tags: dict[str, str] = field(default_factory=dict)


class MetricsRegistry:
    """Thread-safe metrics registry for collecting and exposing application metrics."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._counters: Dict[str, float] = defaultdict(float)
        self._gauges: Dict[str, float] = {}
        self._histograms: Dict[str, deque] = defaultdict(lambda: deque(maxlen=1000))
        self._timers: Dict[str, List[float]] = defaultdict(list)

    def increment_counter(self, name: str, value: float = 1.0, tags: Optional[Dict[str, str]] = None) -> None:
        """Increment a counter metric."""
        with self._lock:
            key = self._make_key(name, tags)
            self._counters[key] += value

    def set_gauge(self, name: str, value: float, tags: Optional[Dict[str, str]] = None) -> None:
        """Set a gauge metric value."""
        with self._lock:
            key = self._make_key(name, tags)
            self._gauges[key] = value

    def record_histogram(self, name: str, value: float, tags: Optional[Dict[str, str]] = None) -> None:
        """Record a histogram value."""
        with self._lock:
            key = self._make_key(name, tags)
            self._histograms[key].append(MetricPoint(datetime.now(timezone.utc), value, tags or {}))

    def record_timer(self, name: str, duration_ms: float, tags: Optional[Dict[str, str]] = None) -> None:
        """Record a timing measurement."""
        with self._lock:
            key = self._make_key(name, tags)
            self._timers[key].append(duration_ms)
            # Keep only last 1000 measurements per timer
            if len(self._timers[key]) > 1000:
                self._timers[key] = self._timers[key][-1000:]

    def _make_key(self, name: str, tags: Optional[Dict[str, str]] = None) -> str:
        """Create a unique key for a metric with optional tags."""
        if not tags:
            return name
        tag_str = ",".join(f"{k}={v}" for k, v in sorted(tags.items()))
        return f"{name}{{{tag_str}}}"

    def get_metrics_summary(self) -> dict[str, Any]:
        """Get a summary of all metrics for exposure."""
        with self._lock:
            summary: dict[str, Any] = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "counters": dict(self._counters),
                "gauges": dict(self._gauges),
                "histograms": {},
                "timers": {},
            }

            # Summarize histograms
            for key, points in self._histograms.items():
                if points:
                    values = [p.value for p in points]
                    summary["histograms"][key] = {
                        "count": len(values),
                        "min": min(values),
                        "max": max(values),
                        "avg": sum(values) / len(values),
                        "latest": points[-1].timestamp.isoformat(),
                    }

            # Summarize timers
            for key, timings in self._timers.items():
                if timings:
                    summary["timers"][key] = {
                        "count": len(timings),
                        "min_ms": min(timings),
                        "max_ms": max(timings),
                        "avg_ms": sum(timings) / len(timings),
                        "p95_ms": self._percentile(timings, 95),
                        "p99_ms": self._percentile(timings, 99),
                    }

            return summary

    def _percentile(self, values: List[float], percentile: float) -> float:
        """Calculate percentile of values."""
        if not values:
            return 0.0
        sorted_values = sorted(values)
        index = int((percentile / 100) * len(sorted_values))
        return sorted_values[min(index, len(sorted_values) - 1)]


# Global metrics registry instance
metrics = MetricsRegistry()


class TimerContext:
    """Context manager for timing operations."""

    def __init__(self, name: str, tags: Optional[Dict[str, str]] = None) -> None:
        self.name = name
        self.tags = tags
        self.start_time = None

    def __enter__(self) -> TimerContext:
        self.start_time = time.time()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self.start_time is not None:
            duration_ms = (time.time() - self.start_time) * 1000
            metrics.record_timer(self.name, duration_ms, self.tags)


def timer(name: str, tags: Optional[Dict[str, str]] = None) -> TimerContext:
    """Create a timer context manager."""
    return TimerContext(name, tags)


def increment_counter(name: str, value: float = 1.0, tags: Optional[Dict[str, str]] = None) -> None:
    """Increment a counter metric."""
    metrics.increment_counter(name, value, tags)


def set_gauge(name: str, value: float, tags: Optional[Dict[str, str]] = None) -> None:
    """Set a gauge metric value."""
    metrics.set_gauge(name, value, tags)


def record_histogram(name: str, value: float, tags: Optional[Dict[str, str]] = None) -> None:
    """Record a histogram value."""
    metrics.record_histogram(name, value, tags)


# --- SLA Dispute Metrics ---
SLADISPUTE_NOTIFICATION_ATTEMPT_TOTAL = "sladispute_notification_attempt_total"
SLADISPUTE_NOTIFICATION_DURATION_MS = "sladispute_notification_duration_ms"
