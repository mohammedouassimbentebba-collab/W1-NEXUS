"""Tests for Multi-Model Intelligence Benchmark Framework (Step 52.1 / Gate 5).

Validates:
1. Simulated mode structural execution, metrics calculation, and report generation.
2. Live mode fail-closed safety (refuses to execute or fabricate fake traces when credentials are missing).
3. Multi-vendor provider credentials audit (OpenAI, Anthropic, Google, Moonshot, DeepSeek, Zhipu, Qwen, Ollama).
4. Fair comparison modes (Equal Resource Budget, Maximum Quality, Cost Normalized).
5. Output artifacts persistence and integrity.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from w1cip.real_intelligence_benchmark import (
    ComparisonMode,
    LiveCredentialsMissingError,
    RealIntelligenceBenchmarkEngine,
    SUPPORTED_LIVE_PROVIDERS,
    calculate_cost,
    get_benchmark_tasks,
    get_standard_topologies,
    run_real_intelligence_benchmark,
)


class RealIntelligenceBenchmarkTests(unittest.TestCase):

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="w1-real-intel-test-")
        self.workspace = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_pricing_calculator(self) -> None:
        cost = calculate_cost("gemini-2.5-pro", 1_000_000, 1_000_000)
        self.assertEqual(6.25, cost)

        cost_4o = calculate_cost("gpt-4o", 10_000, 5_000)
        self.assertEqual(0.075, cost_4o)

    def test_tasks_and_topologies_definitions(self) -> None:
        tasks = get_benchmark_tasks()
        self.assertEqual(5, len(tasks))
        categories = {t.category for t in tasks}
        self.assertEqual(
            {
                "Software Engineering",
                "Research & Synthesis",
                "Long-Horizon Planning",
                "Artifact Production",
                "Adversarial Review / Bug Detection",
            },
            categories,
        )

        topologies = get_standard_topologies()
        self.assertEqual(8, len(topologies))  # Single, 2, 3, 5 + 4 ablations

    def test_multi_vendor_provider_registry(self) -> None:
        expected_providers = {"openai", "anthropic", "google", "deepseek", "moonshot", "zhipu", "qwen", "meta_local"}
        self.assertTrue(expected_providers.issubset(set(SUPPORTED_LIVE_PROVIDERS.keys())))

    def test_live_mode_refuses_execution_when_credentials_missing(self) -> None:
        engine = RealIntelligenceBenchmarkEngine(
            execution_mode="LIVE",
            workspace_root=self.workspace,
        )
        task = get_benchmark_tasks()[0]
        top = get_standard_topologies()[0]  # Single baseline (requires OpenAI)

        # In uncredentialed test environments, LIVE execution must fail closed
        with self.assertRaises(LiveCredentialsMissingError) as ctx:
            engine.run_trial(task, top, trial_index=1)
        self.assertIn("LIVE Benchmark refused", str(ctx.exception))
        self.assertIn("strictly refuses to synthesize fake model traces", str(ctx.exception))

    def test_simulated_mode_runs_and_renders_reports(self) -> None:
        engine = RealIntelligenceBenchmarkEngine(
            execution_mode="SIMULATED",
            comparison_mode=ComparisonMode.MAXIMUM_QUALITY,
            trials_per_config=2,
            workspace_root=self.workspace,
        )
        ledger = engine.run_full_benchmark()

        meta = ledger["benchmark_metadata"]
        self.assertEqual("0.1.0.dev52", meta["version"])
        self.assertEqual("SIMULATED", meta["execution_mode"])
        self.assertEqual("PASS", meta["framework_status"])
        self.assertEqual("PENDING_LIVE_CREDENTIALS", meta["live_validation_status"])
        self.assertEqual("HIGH-FIDELITY SIMULATED STRUCTURAL BENCHMARK", meta["classification"])

        summary = ledger["executive_summary"]
        self.assertEqual("HYPOTHESIS_INDICATED_REQUIRES_LIVE_VALIDATION", summary["verdict"])
        self.assertIn("requires live validation", summary["verdict_rationale"])

        json_path, md_path = engine.save_and_render_reports(ledger)
        self.assertTrue(json_path.is_file())
        self.assertTrue(md_path.is_file())

        md_text = md_path.read_text(encoding="utf-8")
        self.assertIn("Gate 5's benchmark framework has been validated", md_text)
        self.assertIn("empirical superiority of W1 over single-model baselines has **NOT yet been established**", md_text)
        self.assertIn("HIGH-FIDELITY SIMULATED STRUCTURAL BENCHMARK", md_text)
        self.assertIn("Mode A: Equal Resource Budget", md_text)
        self.assertIn("Mode B: Maximum Quality", md_text)
        self.assertIn("Mode C: Cost-Normalized", md_text)

    def test_comparison_modes_execution(self) -> None:
        # Verify Equal Resource Budget Mode executes cleanly
        engine_budget = RealIntelligenceBenchmarkEngine(
            execution_mode="SIMULATED",
            comparison_mode=ComparisonMode.EQUAL_RESOURCE_BUDGET,
            trials_per_config=1,
            workspace_root=self.workspace,
        )
        ledger = engine_budget.run_full_benchmark()
        self.assertEqual("EQUAL_RESOURCE_BUDGET", ledger["benchmark_metadata"]["comparison_mode"])


if __name__ == "__main__":
    unittest.main()
