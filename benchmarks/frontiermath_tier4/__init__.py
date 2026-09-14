"""FrontierMath Tier 4 v2: High-Difficulty Research Mathematics with Strict Containment."""

from __future__ import annotations

from typing import Sequence
from ..base import BenchmarkSuite
from w1cip.benchmark_core.runner import BenchmarkTaskSpec


class FrontierMathTier4Suite(BenchmarkSuite):
    name = "FrontierMath Tier 4"
    version = "2.0.0"
    release_tag = "frontiermath-tier4-v2"
    is_public_local = True

    def get_smoke_task(self) -> BenchmarkTaskSpec:
        return BenchmarkTaskSpec(
            task_id="fm-t4-smoke-01",
            benchmark=self.name,
            category="Abstract Algebra",
            prompt="Let G be a finite group of order 35. By Sylow's theorem, is G necessarily cyclic? Answer with YES or NO.",
            expected_answer="YES",
            timeout_seconds=40.0,
        )

    def get_tasks(self) -> Sequence[BenchmarkTaskSpec]:
        return [
            self.get_smoke_task(),
            BenchmarkTaskSpec(
                task_id="fm-t4-nt-01",
                benchmark=self.name,
                category="Number Theory",
                prompt="Evaluate the Legendre symbol (3/7). Does 3 have a quadratic residue modulo 7? Answer with -1 or 1.",
                expected_answer="-1",
            ),
            BenchmarkTaskSpec(
                task_id="fm-t4-top-01",
                benchmark=self.name,
                category="Topology",
                prompt="What is the Euler characteristic chi(S^2) of the 2-dimensional sphere? Answer with the integer.",
                expected_answer="2",
            ),
        ]
