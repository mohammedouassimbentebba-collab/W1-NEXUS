"""AA Intelligence Index v4.1.1: Official Composite Intelligence Aggregator."""

from __future__ import annotations

from typing import Sequence
from ..base import BenchmarkSuite
from w1cip.benchmark_core.runner import BenchmarkTaskSpec


class AAIntelligenceIndexSuite(BenchmarkSuite):
    name = "AA Intelligence Index"
    version = "4.1.1"
    release_tag = "aa-intelligence-index-v4.1.1"
    is_public_local = True

    # Standard component indicators
    COMPONENT_WEIGHTS = {
        "verbal_reasoning": 0.25,
        "mathematical_precision": 0.25,
        "code_synthesis": 0.25,
        "instruction_adherence": 0.25,
    }

    def get_smoke_task(self) -> BenchmarkTaskSpec:
        return BenchmarkTaskSpec(
            task_id="aa-index-smoke-01",
            benchmark=self.name,
            category="Instruction Adherence",
            prompt="Reply with exactly three words: 'ALPHA BETA GAMMA'. No other characters.",
            expected_answer="ALPHA BETA GAMMA",
            timeout_seconds=30.0,
        )

    def get_tasks(self) -> Sequence[BenchmarkTaskSpec]:
        return [
            self.get_smoke_task(),
            BenchmarkTaskSpec(
                task_id="aa-index-verb-01",
                benchmark=self.name,
                category="Verbal Reasoning",
                prompt="If all Bloops are Razzies and all Razzies are Lollies, are all Bloops definitely Lollies? Answer with YES or NO.",
                expected_answer="YES",
            ),
            BenchmarkTaskSpec(
                task_id="aa-index-math-01",
                benchmark=self.name,
                category="Mathematical Precision",
                prompt="A train travels 180 km in 2 hours and 15 minutes. What is its average speed in km/h? Answer with the number.",
                expected_answer="80",
            ),
            BenchmarkTaskSpec(
                task_id="aa-index-code-01",
                benchmark=self.name,
                category="Code Synthesis",
                prompt="Write a Python lambda that takes two numbers a and b and returns their sum. Answer with only the lambda expression.",
                expected_answer="lambda",
            ),
        ]
