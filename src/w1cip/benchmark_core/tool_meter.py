"""Tool usage accounting, action latency, and recovery resilience metrics."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ToolCallRecord:
    tool_name: str
    arguments: dict[str, Any]
    success: bool
    execution_time_ms: float
    error_message: Optional[str] = None
    recovered_later: bool = False


class ToolMeter:
    """Tracks tool invocation metrics, action errors, and recovery rates."""

    def __init__(self) -> None:
        self.records: list[ToolCallRecord] = []
        self._consecutive_failures: int = 0
        self.recovery_count: int = 0

    def record_call(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        success: bool,
        execution_time_ms: float,
        error_message: Optional[str] = None,
    ) -> ToolCallRecord:
        record = ToolCallRecord(
            tool_name=tool_name,
            arguments=arguments,
            success=success,
            execution_time_ms=round(execution_time_ms, 2),
            error_message=error_message,
        )

        if success:
            if self._consecutive_failures > 0:
                record.recovered_later = True
                self.recovery_count += 1
                self._consecutive_failures = 0
        else:
            self._consecutive_failures += 1

        self.records.append(record)
        return record

    @property
    def total_calls(self) -> int:
        return len(self.records)

    @property
    def successful_calls(self) -> int:
        return sum(1 for r in self.records if r.success)

    @property
    def failed_calls(self) -> int:
        return sum(1 for r in self.records if not r.success)

    @property
    def call_types(self) -> list[str]:
        return sorted(list({r.tool_name for r in self.records}))

    @property
    def total_execution_time_ms(self) -> float:
        return round(sum(r.execution_time_ms for r in self.records), 2)

    def calculate_efficiency(self, overall_task_success: bool) -> dict[str, float]:
        total = self.total_calls
        success = self.successful_calls
        failed = self.failed_calls

        tool_failure_rate = round(failed / max(1, total), 4) if total > 0 else 0.0
        success_per_tool_call = round((1.0 if overall_task_success else 0.0) / max(1, total), 4) if total > 0 else 0.0
        tools_per_success = float(total) if overall_task_success and total > 0 else 0.0
        recovery_rate = round(self.recovery_count / max(1, failed), 4) if failed > 0 else 1.0

        return {
            "total_tool_calls": float(total),
            "successful_tool_calls": float(success),
            "failed_tool_calls": float(failed),
            "tool_failure_rate": tool_failure_rate,
            "success_per_tool_call": success_per_tool_call,
            "average_tool_calls_per_success": tools_per_success,
            "recovery_after_tool_failure": self.recovery_count,
            "recovery_rate": recovery_rate,
        }
