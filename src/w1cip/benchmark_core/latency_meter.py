"""High-precision latency meter separating model, tool, and environment times."""

from __future__ import annotations

import statistics
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence


@dataclass
class LatencySnapshot:
    request_start_epoch: float
    time_to_first_token_ms: Optional[float] = None
    time_to_last_token_ms: Optional[float] = None
    model_time_ms: float = 0.0
    tool_time_ms: float = 0.0
    environment_time_ms: float = 0.0
    total_latency_ms: float = 0.0


class LatencyMeter:
    """Measures fine-grained phase latencies without conflating inference with environment setup."""

    def __init__(self) -> None:
        self._samples: list[LatencySnapshot] = []

    def start_measurement(self) -> LatencyTimer:
        return LatencyTimer(self)

    def record_snapshot(self, snapshot: LatencySnapshot) -> None:
        self._samples.append(snapshot)

    def calculate_percentiles(self, metric: str = "total_latency_ms") -> dict[str, float]:
        values = [getattr(s, metric) for s in self._samples if getattr(s, metric, None) is not None]
        if not values:
            return {"count": 0.0, "mean": 0.0, "median": 0.0, "p90": 0.0, "p95": 0.0, "p99": 0.0}

        sorted_vals = sorted(values)
        count = len(sorted_vals)

        def percentile(p: float) -> float:
            k = (count - 1) * p
            f = int(k)
            c = min(f + 1, count - 1)
            d0 = sorted_vals[f] * (c - k)
            d1 = sorted_vals[c] * (k - f)
            return round(d0 + d1, 2)

        return {
            "count": float(count),
            "mean": round(statistics.mean(sorted_vals), 2),
            "median": round(statistics.median(sorted_vals), 2),
            "p90": percentile(0.90),
            "p95": percentile(0.95),
            "p99": percentile(0.99),
        }


class LatencyTimer:
    """Contextual timer for a single benchmark task trial."""

    def __init__(self, meter: Optional[LatencyMeter] = None) -> None:
        self._meter = meter
        self._start_perf = time.perf_counter()
        self._start_epoch = time.time()
        self.ttft_ms: Optional[float] = None
        self.ttlt_ms: Optional[float] = None
        self.model_time_ms: float = 0.0
        self.tool_time_ms: float = 0.0
        self.environment_time_ms: float = 0.0
        self.total_latency_ms: float = 0.0

    def mark_first_token(self) -> None:
        if self.ttft_ms is None:
            self.ttft_ms = (time.perf_counter() - self._start_perf) * 1000.0

    def record_ttft(self, duration_ms: Optional[float]) -> None:
        if duration_ms is not None and self.ttft_ms is None:
            self.ttft_ms = duration_ms

    def record_model_duration(self, duration_ms: float) -> None:
        self.model_time_ms += duration_ms

    def record_tool_duration(self, duration_ms: float) -> None:
        self.tool_time_ms += duration_ms

    def record_environment_duration(self, duration_ms: float) -> None:
        self.environment_time_ms += duration_ms

    def stop(self) -> LatencySnapshot:
        total = (time.perf_counter() - self._start_perf) * 1000.0
        self.total_latency_ms = round(total, 2)
        if self.ttlt_ms is None:
            self.ttlt_ms = self.total_latency_ms

        snapshot = LatencySnapshot(
            request_start_epoch=self._start_epoch,
            time_to_first_token_ms=round(self.ttft_ms, 2) if self.ttft_ms is not None else None,
            time_to_last_token_ms=round(self.ttlt_ms, 2) if self.ttlt_ms is not None else None,
            model_time_ms=round(self.model_time_ms, 2),
            tool_time_ms=round(self.tool_time_ms, 2),
            environment_time_ms=round(self.environment_time_ms, 2),
            total_latency_ms=self.total_latency_ms,
        )
        if self._meter:
            self._meter.record_snapshot(snapshot)
        return snapshot
