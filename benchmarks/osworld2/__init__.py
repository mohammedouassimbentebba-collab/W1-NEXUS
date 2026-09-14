"""OSWorld 2.0 Benchmark Suite pinned to official release osworld-v2-2026.08.08."""

from __future__ import annotations

from typing import Sequence
from ..base import BenchmarkSuite
from w1cip.benchmark_core.runner import BenchmarkTaskSpec


class OSWorld2Suite(BenchmarkSuite):
    name = "OSWorld 2.0"
    version = "2.0.0"
    release_tag = "osworld-v2-2026.08.08"
    is_public_local = True

    def get_smoke_task(self) -> BenchmarkTaskSpec:
        return BenchmarkTaskSpec(
            task_id="osworld2-smoke-01",
            benchmark=self.name,
            category="OS/Desktop",
            prompt="Find the file named 'quarterly_report.xlsx' located in the Documents directory and report its file size in KB given output: '142 KB, modified 2026-08-01'.",
            expected_answer="142",
            timeout_seconds=30.0,
        )

    def get_tasks(self) -> Sequence[BenchmarkTaskSpec]:
        return [
            self.get_smoke_task(),
            BenchmarkTaskSpec(
                task_id="osworld2-web-01",
                benchmark=self.name,
                category="Web Navigation",
                prompt="Navigate to the internal portal http://localhost:8080/settings and find the value of 'Telemetry_Mode' given: 'Telemetry_Mode: Governed_Strict'. Answer with the value.",
                expected_answer="Governed_Strict",
            ),
            BenchmarkTaskSpec(
                task_id="osworld2-office-01",
                benchmark=self.name,
                category="Office Automation",
                prompt="In LibreOffice Calc sheet, cell B10 contains formula '=SUM(B2:B9)'. If values are 10, 20, 30, 40, what is the computed sum?",
                expected_answer="100",
            ),
            BenchmarkTaskSpec(
                task_id="osworld2-sys-01",
                benchmark=self.name,
                category="System Administration",
                prompt="Determine the PID of the process 'nginx' from ps output: 'root 4192 0.0 0.1 nginx: master process'. Answer with the PID number.",
                expected_answer="4192",
            ),
        ]
