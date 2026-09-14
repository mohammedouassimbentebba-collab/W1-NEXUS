"""Terminal-Bench Science 0.1: 5 Scientific Disciplines."""

from __future__ import annotations

from typing import Sequence
from ..base import BenchmarkSuite
from w1cip.benchmark_core.runner import BenchmarkTaskSpec


class TerminalBenchScienceSuite(BenchmarkSuite):
    name = "Terminal-Bench Science"
    version = "0.1.0"
    release_tag = "tb-science-v0.1.0"
    is_public_local = True

    DOMAINS = (
        "life sciences",
        "physical sciences",
        "earth sciences",
        "mathematical sciences",
        "engineering sciences",
    )

    def get_smoke_task(self) -> BenchmarkTaskSpec:
        return BenchmarkTaskSpec(
            task_id="tb-sci-smoke-01",
            benchmark=self.name,
            category="physical sciences",
            prompt="According to Einstein's mass-energy equivalence E = mc^2, what is the speed of light c approximately in meters per second (in standard scientific notation like 3e8)?",
            expected_answer="3e8",
            timeout_seconds=30.0,
        )

    def get_tasks(self) -> Sequence[BenchmarkTaskSpec]:
        return [
            self.get_smoke_task(),
            BenchmarkTaskSpec(
                task_id="tb-sci-life-01",
                benchmark=self.name,
                category="life sciences",
                prompt="Which nitrogenous base pairs with Adenine in DNA (Adenine, Thymine, Cytosine, or Guanine)? Answer with the base name.",
                expected_answer="Thymine",
            ),
            BenchmarkTaskSpec(
                task_id="tb-sci-earth-01",
                benchmark=self.name,
                category="earth sciences",
                prompt="What is the most abundant gas in Earth's atmosphere (Nitrogen, Oxygen, Carbon Dioxide, or Argon)?",
                expected_answer="Nitrogen",
            ),
            BenchmarkTaskSpec(
                task_id="tb-sci-math-01",
                benchmark=self.name,
                category="mathematical sciences",
                prompt="What is the derivative of f(x) = x^3 with respect to x? Answer with the algebraic expression.",
                expected_answer="3x^2",
            ),
            BenchmarkTaskSpec(
                task_id="tb-sci-eng-01",
                benchmark=self.name,
                category="engineering sciences",
                prompt="According to Ohm's law V = I * R, if Voltage is 24V and Resistance is 8 Ohms, what is the Current I in Amperes?",
                expected_answer="3",
            ),
        ]
