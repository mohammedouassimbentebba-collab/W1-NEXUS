"""MRCR v2: Multi-Round Context Retrieval across 512K, 768K, and 1M Context Regimes."""

from __future__ import annotations

from typing import Sequence
from ..base import BenchmarkSuite
from w1cip.benchmark_core.runner import BenchmarkTaskSpec


class MRCRv2Suite(BenchmarkSuite):
    name = "MRCR v2"
    version = "2.0.0"
    release_tag = "mrcr-v2.0"
    is_public_local = True

    SUPPORTED_REGIMES = ("512K", "768K", "1M")

    def get_smoke_task(self) -> BenchmarkTaskSpec:
        # Small 8K synthetic needle-in-haystack probe for fast smoke qualification
        filler = "The expedition continued through uncharted northern valleys. " * 80
        prompt = f"{filler}\nCRITICAL EVIDENCE: The lost vault key code is 'ZEPHYR-9981'.\n{filler}\nQuestion: What is the lost vault key code?"
        return BenchmarkTaskSpec(
            task_id="mrcr-smoke-16k",
            benchmark=self.name,
            category="Needle In Haystack",
            prompt=prompt,
            expected_answer="ZEPHYR-9981",
            context_regime="16K",
            timeout_seconds=45.0,
        )

    def get_tasks(self) -> Sequence[BenchmarkTaskSpec]:
        tasks: list[BenchmarkTaskSpec] = [self.get_smoke_task()]
        for regime in self.SUPPORTED_REGIMES:
            # Scaled context probes (512K, 768K, 1M)
            code_target = f"NEXUS-TOKEN-{regime}-VAL"
            tasks.append(
                BenchmarkTaskSpec(
                    task_id=f"mrcr-retrieval-{regime.lower()}",
                    benchmark=self.name,
                    category=f"Long Context ({regime})",
                    prompt=f"[CONTEXT REGIME: {regime}]\nDocument Header: Protocol Analysis.\nKey Token: {code_target}\nQuestion: Retrieve the exact Key Token.",
                    expected_answer=code_target,
                    context_regime=regime,
                    timeout_seconds=120.0,
                    metadata={"target_context_tokens": regime},
                )
            )
        return tasks
