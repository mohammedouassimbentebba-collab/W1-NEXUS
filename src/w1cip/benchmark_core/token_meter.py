"""High-precision token accounting, cost computation, and efficiency metrics."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from .schema import CumulativeTokenUsage, ModelResponse


# Standard pricing table (USD per 1M tokens) for estimated cost calculation
BENCHMARK_PRICING_TABLE: dict[str, dict[str, float]] = {
    "moonshotai/kimi-k3": {"input": 1.50, "output": 6.00},
    "meta/muse-glimmer-30b": {"input": 0.50, "output": 1.50},
    "deepseek-ai/deepseek-v4-pro-0813": {"input": 0.55, "output": 2.19},
    "default": {"input": 1.00, "output": 3.00},
}


@dataclass
class TokenAccountingSnapshot:
    input_tokens: Optional[int]
    output_tokens: Optional[int]
    reasoning_tokens: Optional[int]
    cached_tokens: Optional[int]
    total_tokens: Optional[int]
    is_estimated: bool = False
    estimated_cost_usd: float = 0.0


class TokenMeter:
    """Thread-safe, high-precision token consumption meter."""

    def __init__(self, model_id: str) -> None:
        self.model_id = model_id
        self._cumulative = CumulativeTokenUsage()
        self._task_records: list[TokenAccountingSnapshot] = []

    def record_usage(
        self,
        input_tokens: Optional[int],
        output_tokens: Optional[int],
        reasoning_tokens: Optional[int] = None,
        cached_tokens: Optional[int] = None,
        total_tokens: Optional[int] = None,
        is_estimated: bool = False,
    ) -> TokenAccountingSnapshot:
        # Measure or compute total if input and output are exact
        if total_tokens is None and input_tokens is not None and output_tokens is not None:
            total_tokens = input_tokens + output_tokens

        cost = self.estimate_cost(input_tokens or 0, output_tokens or 0)
        snapshot = TokenAccountingSnapshot(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=reasoning_tokens,
            cached_tokens=cached_tokens,
            total_tokens=total_tokens,
            is_estimated=is_estimated,
            estimated_cost_usd=cost,
        )
        self._task_records.append(snapshot)

        # Update cumulative
        if input_tokens is not None:
            self._cumulative.input_tokens += input_tokens
        if output_tokens is not None:
            self._cumulative.output_tokens += output_tokens
        if cached_tokens is not None:
            self._cumulative.cached_tokens += cached_tokens
        if total_tokens is not None:
            self._cumulative.total_tokens += total_tokens
        if reasoning_tokens is not None:
            if self._cumulative.reasoning_tokens is None:
                self._cumulative.reasoning_tokens = 0
            self._cumulative.reasoning_tokens += reasoning_tokens

        self._cumulative.total_requests += 1
        return snapshot

    def estimate_cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        pricing = BENCHMARK_PRICING_TABLE.get(self.model_id, BENCHMARK_PRICING_TABLE["default"])
        cost = (prompt_tokens / 1_000_000.0) * pricing["input"] + (completion_tokens / 1_000_000.0) * pricing["output"]
        return round(cost, 6)

    def get_cumulative(self) -> CumulativeTokenUsage:
        return self._cumulative

    @staticmethod
    def calculate_efficiency_metrics(
        total_tokens: int,
        success_count: int,
        task_count: int,
        tool_calls: int,
        score: float,
    ) -> dict[str, float]:
        """Calculates derived token efficiency metrics."""
        tokens_per_success = round(total_tokens / max(1, success_count), 2) if success_count > 0 else 0.0
        tokens_per_task = round(total_tokens / max(1, task_count), 2) if task_count > 0 else 0.0
        tokens_per_tool_call = round(total_tokens / max(1, tool_calls), 2) if tool_calls > 0 else 0.0
        score_per_1M_tokens = round((score / max(1, total_tokens)) * 1_000_000.0, 4) if total_tokens > 0 else 0.0
        success_per_100k_tokens = round((success_count / max(1, total_tokens)) * 100_000.0, 4) if total_tokens > 0 else 0.0

        return {
            "tokens_per_success": tokens_per_success,
            "tokens_per_task": tokens_per_task,
            "tokens_per_tool_call": tokens_per_tool_call,
            "score_per_1M_tokens": score_per_1M_tokens,
            "success_per_100k_tokens": success_per_100k_tokens,
        }
