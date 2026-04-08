"""
Metrics Store — aggregated performance tracking for intents and tools.

Tracks:
- Latency percentiles (p50, p95, p99) per intent and tool
- Success rates for tools
- Rolling window with configurable retention
- Thread-safe for concurrent access

v0.2 fixes vs v0.1:
- Single source of truth: _samples only; aggregates computed on read
- Percentile index corrected to (n-1)*p interpolation
- defaultdict replaced with plain dict + .get() in read paths
- Cleanup trigger decoupled: per-key size cap is separate from global time scan
- Dict mutation during iteration fixed: collect-then-delete pattern
- typing imports replaced with built-in generics (PEP 585)
- logger.debug uses lazy % formatting
"""
from __future__ import annotations

import logging
import math
import time
import threading
from collections import deque
from dataclasses import dataclass

logger = logging.getLogger(__name__)

DEFAULT_RETENTION_SECONDS = 3600
MAX_SAMPLES_PER_KEY = 1000


# ── Data types ────────────────────────────────────────────────────────────────

@dataclass(slots=True)
class MetricSample:
    """Single timing and outcome sample."""
    timestamp: float
    latency_ms: int
    success: bool


@dataclass(frozen=True)
class AggregatedMetrics:
    """Snapshot of computed metrics for one intent/tool."""
    total_requests: int
    success_count: int
    p50_ms: int
    p95_ms: int
    p99_ms: int

    @property
    def success_rate(self) -> float:
        """Success rate as a percentage (0–100)."""
        if self.total_requests == 0:
            return 0.0
        return (self.success_count / self.total_requests) * 100


# ── Percentile helper ─────────────────────────────────────────────────────────

def _percentile(sorted_samples: list[int], p: float) -> int:
    """
    Return the p-th percentile of a pre-sorted list using linear interpolation.

    Uses the (n-1)*p index method so that:
      - p50 of [100, 200]       → 150  (not 200 as the naive int(n*p) gives)
      - p50 of [100]            → 100
      - p99 of [1..100]         → 99

    Args:
        sorted_samples: non-empty ascending list of integer latency values
        p: percentile in [0, 1]
    """
    n = len(sorted_samples)
    if n == 1:
        return sorted_samples[0]
    idx = (n - 1) * p
    lo = math.floor(idx)
    hi = math.ceil(idx)
    if lo == hi:
        return sorted_samples[lo]
    # Linear interpolation between neighbours
    frac = idx - lo
    return round(sorted_samples[lo] * (1 - frac) + sorted_samples[hi] * frac)


def _compute(samples: deque[MetricSample]) -> AggregatedMetrics:
    """Compute aggregated metrics from a raw sample deque."""
    if not samples:
        return AggregatedMetrics(
            total_requests=0, success_count=0,
            p50_ms=0, p95_ms=0, p99_ms=0,
        )

    total = len(samples)
    successes = sum(1 for s in samples if s.success)
    latencies = sorted(s.latency_ms for s in samples)

    return AggregatedMetrics(
        total_requests=total,
        success_count=successes,
        p50_ms=_percentile(latencies, 0.50),
        p95_ms=_percentile(latencies, 0.95),
        p99_ms=_percentile(latencies, 0.99),
    )


# ── MetricsStore ──────────────────────────────────────────────────────────────

class MetricsStore:
    """
    Thread-safe metrics collection and aggregation.

    Single source of truth: one deque of MetricSample per key.
    Aggregates (percentiles, success rate) are derived on read so the store
    never holds two copies of the same data in different shapes.

    Usage:
        store = MetricsStore()
        store.record("web_search", latency_ms=250, success=True)

        agg = store.get("web_search")
        print(agg.p95_ms, agg.success_rate)
    """

    def __init__(self, retention_seconds: int = DEFAULT_RETENTION_SECONDS) -> None:
        self._retention_seconds = retention_seconds
        self._lock = threading.RLock()
        # Plain dict; absent keys are not auto-created on read.
        self._samples: dict[str, deque[MetricSample]] = {}
        self._last_cleanup = time.monotonic()

    # ── Write ─────────────────────────────────────────────────────────────────

    def record(self, key: str, latency_ms: int, success: bool) -> None:
        """
        Record one sample for *key* (an intent or tool name).

        Triggers per-key eviction when the deque hits MAX_SAMPLES_PER_KEY,
        and a full time-based cleanup scan every 5 minutes.
        """
        with self._lock:
            if key not in self._samples:
                self._samples[key] = deque(maxlen=MAX_SAMPLES_PER_KEY)

            self._samples[key].append(
                MetricSample(
                    timestamp=time.monotonic(),
                    latency_ms=latency_ms,
                    success=success,
                )
            )

            now = time.monotonic()
            if now - self._last_cleanup > 300:
                self._evict_old_samples(now)
                self._last_cleanup = now

    # ── Read ──────────────────────────────────────────────────────────────────

    def get(self, key: str) -> AggregatedMetrics:
        """
        Return computed metrics for *key*.

        Returns a zero-valued AggregatedMetrics if *key* has no samples,
        without inserting a dead entry into the store.
        """
        with self._lock:
            samples = self._samples.get(key)
            if not samples:
                return AggregatedMetrics(
                    total_requests=0, success_count=0,
                    p50_ms=0, p95_ms=0, p99_ms=0,
                )
            return _compute(samples)

    def get_latency_percentiles(self, key: str) -> tuple[int, int, int]:
        """Return (p50_ms, p95_ms, p99_ms) for *key*."""
        agg = self.get(key)
        return agg.p50_ms, agg.p95_ms, agg.p99_ms

    def get_success_rate(self, key: str) -> float:
        """Return success rate (0–100) for *key*."""
        return self.get(key).success_rate

    def get_request_count(self, key: str) -> int:
        """Return total recorded samples for *key*."""
        return self.get(key).total_requests

    def get_all_metrics(self) -> dict[str, dict[str, object]]:
        """
        Return a snapshot of all keys as a plain dict, suitable for
        monitoring or JSON serialisation.

        Shape per key:
            {
                "total_requests": int,
                "success_rate":   float,
                "p50_latency_ms": int,
                "p95_latency_ms": int,
                "p99_latency_ms": int,
            }
        """
        with self._lock:
            result: dict[str, dict[str, object]] = {}
            for key, samples in self._samples.items():
                agg = _compute(samples)
                result[key] = {
                    "total_requests": agg.total_requests,
                    "success_rate":   agg.success_rate,
                    "p50_latency_ms": agg.p50_ms,
                    "p95_latency_ms": agg.p95_ms,
                    "p99_latency_ms": agg.p99_ms,
                }
            return result

    # ── Maintenance ───────────────────────────────────────────────────────────

    def _evict_old_samples(self, now: float) -> None:
        """
        Drop samples older than the retention window.

        Collects stale keys first, then deletes after iteration to avoid
        mutating the dict mid-loop.
        """
        cutoff = now - self._retention_seconds
        keys_to_delete: list[str] = []

        for key, samples in self._samples.items():
            before = len(samples)
            # deque has no in-place filter; rebuild from the live tail
            live = deque(
                (s for s in samples if s.timestamp >= cutoff),
                maxlen=MAX_SAMPLES_PER_KEY,
            )
            dropped = before - len(live)
            if dropped:
                logger.debug(
                    "[metrics] evicted %d old samples for '%s'", dropped, key
                )
            if live:
                self._samples[key] = live
            else:
                keys_to_delete.append(key)

        for key in keys_to_delete:
            del self._samples[key]
            logger.debug("[metrics] removed empty key '%s'", key)

    def reset(self) -> None:
        """Clear all stored samples."""
        with self._lock:
            self._samples.clear()
            logger.info("[metrics] all metrics data cleared")