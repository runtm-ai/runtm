"""Metrics management for telemetry.

Provides Counter and Histogram metrics for dashboards.
Uses low-cardinality labels only to avoid cardinality bombs.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .base import TelemetryMetric

# Low-cardinality label constraints
ALLOWED_METRIC_LABELS = frozenset(
    {
        "command",  # init, deploy, up, run, etc.
        "outcome",  # success, failure, timeout
        "error_type",  # validation, auth, network, etc.
        "template",  # backend-service, static-site, web-app
        "tier",  # starter, performance, pro
        "runtime",  # python, node, fullstack
    }
)

MAX_LABEL_VALUE_LENGTH = 50


@dataclass
class HistogramBucket:
    """A histogram bucket with upper bound and count."""

    upper_bound: float
    count: int = 0


@dataclass
class HistogramValue:
    """Histogram data with buckets, sum, and count."""

    buckets: list[HistogramBucket]
    sum: float = 0.0
    count: int = 0

    def record(self, value: float) -> None:
        """Record a value in the histogram."""
        self.sum += value
        self.count += 1
        for bucket in self.buckets:
            if value <= bucket.upper_bound:
                bucket.count += 1


# Default histogram buckets for duration in milliseconds
DEFAULT_DURATION_BUCKETS = [
    10,
    25,
    50,
    100,
    250,
    500,
    1000,
    2500,
    5000,
    10000,
    30000,
    60000,
    float("inf"),
]


def _sanitize_labels(labels: dict[str, str]) -> dict[str, str]:
    """Sanitize labels to enforce low cardinality.

    Args:
        labels: Raw labels dict

    Returns:
        Sanitized labels with only allowed keys and truncated values
    """
    result = {}
    for key, value in labels.items():
        if key in ALLOWED_METRIC_LABELS:
            # Truncate and sanitize value
            sanitized = str(value)[:MAX_LABEL_VALUE_LENGTH].lower()
            result[key] = sanitized
    return result


class Counter:
    """A monotonically increasing counter metric."""

    def __init__(self, name: str, description: str = "") -> None:
        """Initialize the counter.

        Args:
            name: Metric name (e.g., "runtm_cli_commands_total")
            description: Human-readable description
        """
        self.name = name
        self.description = description
        self._values: dict[tuple[tuple[str, str], ...], float] = {}

    def inc(self, labels: dict[str, str] | None = None, value: float = 1.0) -> None:
        """Increment the counter.

        Args:
            labels: Optional low-cardinality labels
            value: Amount to increment (default 1.0)
        """
        sanitized = _sanitize_labels(labels or {})
        key = tuple(sorted(sanitized.items()))

        if key not in self._values:
            self._values[key] = 0.0
        self._values[key] += value

    def collect(self) -> list[TelemetryMetric]:
        """Collect all metric values.

        Returns:
            List of TelemetryMetric objects
        """
        metrics = []
        timestamp = time.time_ns()

        for key, value in self._values.items():
            labels = dict(key)
            metrics.append(
                TelemetryMetric(
                    name=self.name,
                    value=value,
                    labels=labels,
                    timestamp_ns=timestamp,
                    metric_type="counter",
                )
            )

        return metrics

    def reset(self) -> None:
        """Reset all counter values."""
        self._values.clear()


class Histogram:
    """A histogram metric for measuring distributions."""

    def __init__(
        self,
        name: str,
        description: str = "",
        buckets: list[float] | None = None,
    ) -> None:
        """Initialize the histogram.

        Args:
            name: Metric name (e.g., "runtm_cli_command_duration_ms")
            description: Human-readable description
            buckets: Bucket upper bounds (defaults to duration buckets)
        """
        self.name = name
        self.description = description
        self._bucket_bounds = buckets or DEFAULT_DURATION_BUCKETS
        self._values: dict[tuple[tuple[str, str], ...], HistogramValue] = {}

    def observe(self, value: float, labels: dict[str, str] | None = None) -> None:
        """Record an observation.

        Args:
            value: The observed value
            labels: Optional low-cardinality labels
        """
        sanitized = _sanitize_labels(labels or {})
        key = tuple(sorted(sanitized.items()))

        if key not in self._values:
            self._values[key] = HistogramValue(
                buckets=[HistogramBucket(bound) for bound in self._bucket_bounds]
            )
        self._values[key].record(value)

    def collect(self) -> list[TelemetryMetric]:
        """Collect all metric values.

        Returns histogram as multiple metrics:
        - {name}_bucket with le label for each bucket
        - {name}_sum for total sum
        - {name}_count for total count
        """
        metrics = []
        timestamp = time.time_ns()

        for key, histogram in self._values.items():
            labels = dict(key)

            # Bucket metrics
            for bucket in histogram.buckets:
                bucket_labels = {**labels, "le": str(bucket.upper_bound)}
                metrics.append(
                    TelemetryMetric(
                        name=f"{self.name}_bucket",
                        value=float(bucket.count),
                        labels=bucket_labels,
                        timestamp_ns=timestamp,
                        metric_type="histogram",
                    )
                )

            # Sum metric
            metrics.append(
                TelemetryMetric(
                    name=f"{self.name}_sum",
                    value=histogram.sum,
                    labels=labels,
                    timestamp_ns=timestamp,
                    metric_type="histogram",
                )
            )

            # Count metric
            metrics.append(
                TelemetryMetric(
                    name=f"{self.name}_count",
                    value=float(histogram.count),
                    labels=labels,
                    timestamp_ns=timestamp,
                    metric_type="histogram",
                )
            )

        return metrics

    def reset(self) -> None:
        """Reset all histogram values."""
        self._values.clear()


@dataclass
class MetricsRegistry:
    """Registry for all metrics."""

    counters: dict[str, Counter] = field(default_factory=dict)
    histograms: dict[str, Histogram] = field(default_factory=dict)

    def counter(self, name: str, description: str = "") -> Counter:
        """Get or create a counter.

        Args:
            name: Metric name
            description: Human-readable description

        Returns:
            The counter
        """
        if name not in self.counters:
            self.counters[name] = Counter(name, description)
        return self.counters[name]

    def histogram(
        self,
        name: str,
        description: str = "",
        buckets: list[float] | None = None,
    ) -> Histogram:
        """Get or create a histogram.

        Args:
            name: Metric name
            description: Human-readable description
            buckets: Bucket upper bounds

        Returns:
            The histogram
        """
        if name not in self.histograms:
            self.histograms[name] = Histogram(name, description, buckets)
        return self.histograms[name]

    def collect_all(self) -> list[TelemetryMetric]:
        """Collect all metrics.

        Returns:
            List of all metric values
        """
        metrics = []
        for counter in self.counters.values():
            metrics.extend(counter.collect())
        for histogram in self.histograms.values():
            metrics.extend(histogram.collect())
        return metrics

    def reset_all(self) -> None:
        """Reset all metrics."""
        for counter in self.counters.values():
            counter.reset()
        for histogram in self.histograms.values():
            histogram.reset()


class MetricsManager:
    """Manages metrics for telemetry.

    Provides pre-defined metrics for CLI commands with low-cardinality labels.
    """

    def __init__(self) -> None:
        """Initialize the metrics manager."""
        self._registry = MetricsRegistry()

        # Pre-define standard metrics
        self._commands_total = self._registry.counter(
            "runtm_cli_commands_total",
            "Total number of CLI commands executed",
        )
        self._command_duration = self._registry.histogram(
            "runtm_cli_command_duration_ms",
            "Duration of CLI commands in milliseconds",
        )
        self._errors_total = self._registry.counter(
            "runtm_cli_errors_total",
            "Total number of CLI errors",
        )
        self._telemetry_dropped = self._registry.counter(
            "runtm_telemetry_dropped_events",
            "Number of telemetry events dropped due to backpressure",
        )
        self._telemetry_flush_failures = self._registry.counter(
            "runtm_telemetry_flush_failures",
            "Number of telemetry flush failures",
        )

    def record_command(
        self,
        command: str,
        outcome: str,
        duration_ms: float,
    ) -> None:
        """Record a command execution.

        Args:
            command: Command name (e.g., "deploy", "init")
            outcome: Outcome (success, failure, timeout)
            duration_ms: Duration in milliseconds
        """
        labels = {"command": command, "outcome": outcome}
        self._commands_total.inc(labels)
        self._command_duration.observe(duration_ms, {"command": command})

    def record_error(
        self,
        command: str,
        error_type: str,
    ) -> None:
        """Record an error.

        Args:
            command: Command name
            error_type: Error category (e.g., "validation", "auth", "network")
        """
        self._errors_total.inc({"command": command, "error_type": error_type})

    def record_dropped_events(self, count: int = 1) -> None:
        """Record dropped telemetry events.

        Args:
            count: Number of events dropped
        """
        self._telemetry_dropped.inc(value=float(count))

    def record_flush_failure(self) -> None:
        """Record a telemetry flush failure."""
        self._telemetry_flush_failures.inc()

    def collect(self) -> list[TelemetryMetric]:
        """Collect all metrics.

        Returns:
            List of all metric values
        """
        return self._registry.collect_all()

    def drain(self) -> list[TelemetryMetric]:
        """Collect and reset all metrics.

        Returns:
            List of all metric values (resets after collection)
        """
        metrics = self._registry.collect_all()
        self._registry.reset_all()
        return metrics
