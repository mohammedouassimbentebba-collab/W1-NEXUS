"""Comprehensive unit tests for NEXUS Benchmark Core Subsystem."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from w1cip.benchmark_core.failure_taxonomy import classify_failure
from w1cip.benchmark_core.latency_meter import LatencyMeter, LatencySnapshot
from w1cip.benchmark_core.model_adapter import (
    DeepSeekV4ProAdapter,
    KimiK3Adapter,
    ModelAdapter,
    MuseAdapter,
    get_model_adapter,
)
from w1cip.benchmark_core.provenance_tracker import (
    build_provenance_metadata,
    compute_environment_hash,
)
from w1cip.benchmark_core.runner import BenchmarkRunner, BenchmarkTaskSpec
from w1cip.benchmark_core.schema import (
    BenchmarkMode,
    FailureType,
    ModelResponse,
    ModelRunConfig,
    NormalizedBenchmarkRunRecord,
)
from w1cip.benchmark_core.secrets import (
    mask_secret,
    register_secret_for_redaction,
    resolve_api_key,
    scrub_secrets,
    validate_secret_presence,
)
from w1cip.benchmark_core.token_meter import TokenMeter
from w1cip.benchmark_core.tool_meter import ToolMeter


class BenchmarkCoreTests(unittest.TestCase):

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="nexus-bench-test-")
        self.workspace = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    # -------------------------------------------------------------------------
    # 1. Secrets & Masking
    # -------------------------------------------------------------------------

    def test_secret_masking(self) -> None:
        secret = "nvapi-FkTEST_SYNTHETIC_MOCK_SECRET_KEY_FOR_UNIT_TESTS_ONLY_IQdi"
        masked = mask_secret(secret)
        self.assertTrue(masked.startswith("nvapi-Fk"))
        self.assertTrue(masked.endswith("IQdi"))
        self.assertNotIn("TEST_SYNTHETIC", masked)

        self.assertEqual("[NOT CONFIGURED]", mask_secret(None))

    def test_secret_scrubbing(self) -> None:
        secret = "nvapi-SECRET_TEST_TOKEN_1234567890"
        register_secret_for_redaction(secret)

        data = {
            "prompt": "Test query",
            "auth_header": f"Bearer {secret}",
            "nested": {"token_value": secret, "count": 10},
        }
        scrubbed = scrub_secrets(data)
        self.assertNotIn(secret, json.dumps(scrubbed))
        self.assertEqual("Bearer [REDACTED_API_KEY]", scrubbed["auth_header"])
        self.assertEqual("[REDACTED_API_KEY]", scrubbed["nested"]["token_value"])

    # -------------------------------------------------------------------------
    # 2. Token Accounting & Measurement Integrity
    # -------------------------------------------------------------------------

    def test_token_meter_precision(self) -> None:
        meter = TokenMeter("deepseek-ai/deepseek-v4-pro-0813")
        snapshot = meter.record_usage(
            input_tokens=100,
            output_tokens=50,
            reasoning_tokens=None,  # Provider did not expose reasoning tokens
            cached_tokens=20,
            total_tokens=150,
        )
        self.assertEqual(100, snapshot.input_tokens)
        self.assertEqual(50, snapshot.output_tokens)
        self.assertIsNone(snapshot.reasoning_tokens)
        self.assertFalse(snapshot.is_estimated)

        cumulative = meter.get_cumulative()
        self.assertEqual(150, cumulative.total_tokens)
        self.assertEqual(1, cumulative.total_requests)

    def test_token_efficiency_calculations(self) -> None:
        metrics = TokenMeter.calculate_efficiency_metrics(
            total_tokens=10_000,
            success_count=5,
            task_count=10,
            tool_calls=20,
            score=5.0,
        )
        self.assertEqual(2000.0, metrics["tokens_per_success"])
        self.assertEqual(1000.0, metrics["tokens_per_task"])
        self.assertEqual(500.0, metrics["tokens_per_tool_call"])
        self.assertEqual(500.0, metrics["score_per_1M_tokens"])

    # -------------------------------------------------------------------------
    # 3. Latency Metrics
    # -------------------------------------------------------------------------

    def test_latency_meter_percentiles(self) -> None:
        meter = LatencyMeter()
        for val in [100.0, 200.0, 300.0, 400.0, 500.0, 600.0, 700.0, 800.0, 900.0, 1000.0]:
            meter.record_snapshot(
                LatencySnapshot(
                    request_start_epoch=0.0,
                    total_latency_ms=val,
                    model_time_ms=val * 0.8,
                    tool_time_ms=val * 0.2,
                )
            )

        pcts = meter.calculate_percentiles()
        self.assertEqual(10.0, pcts["count"])
        self.assertEqual(550.0, pcts["median"])
        self.assertAlmostEqual(910.0, pcts["p90"], delta=15.0)

    # -------------------------------------------------------------------------
    # 4. Tool Usage & Recovery Tracking
    # -------------------------------------------------------------------------

    def test_tool_recovery_tracking(self) -> None:
        meter = ToolMeter()
        meter.record_call("read_file", {"path": "/etc/hosts"}, success=True, execution_time_ms=12.0)
        meter.record_call("exec_bash", {"cmd": "curl bad"}, success=False, execution_time_ms=50.0)
        meter.record_call("exec_bash", {"cmd": "curl retry"}, success=True, execution_time_ms=30.0)

        self.assertEqual(3, meter.total_calls)
        self.assertEqual(2, meter.successful_calls)
        self.assertEqual(1, meter.failed_calls)
        self.assertEqual(1, meter.recovery_count)

        eff = meter.calculate_efficiency(overall_task_success=True)
        self.assertEqual(1.0, eff["recovery_rate"])
        self.assertEqual(0.3333, eff["tool_failure_rate"])

    # -------------------------------------------------------------------------
    # 5. Failure Taxonomy Classification
    # -------------------------------------------------------------------------

    def test_failure_classification(self) -> None:
        f_type, _ = classify_failure("Rate limit reached for requests per minute", status_code=429)
        self.assertEqual(FailureType.RATE_LIMIT, f_type)

        f_type, _ = classify_failure("Prompt exceeds maximum context length of 131072 tokens")
        self.assertEqual(FailureType.CONTEXT_LIMIT, f_type)

        f_type, _ = classify_failure("Execution deadline exceeded after 60 seconds")
        self.assertEqual(FailureType.TIMEOUT, f_type)

        f_type, _ = classify_failure("Connection timed out after 60 seconds")
        self.assertEqual(FailureType.PROVIDER_TIMEOUT, f_type)

        f_type, _ = classify_failure("Docker container failed to initialize", is_infra_pre_execution=True)
        self.assertEqual(FailureType.ENVIRONMENT_ERROR, f_type)

    # -------------------------------------------------------------------------
    # 6. Model Adapters Registration
    # -------------------------------------------------------------------------

    def test_model_adapters_factory(self) -> None:
        kimi = get_model_adapter("kimi")
        self.assertIsInstance(kimi, KimiK3Adapter)
        self.assertEqual("moonshotai/kimi-k3", kimi.model_id)

        muse = get_model_adapter("muse")
        self.assertIsInstance(muse, MuseAdapter)
        self.assertEqual("meta/muse-glimmer-30b", muse.model_id)

        deepseek = get_model_adapter("deepseek")
        self.assertIsInstance(deepseek, DeepSeekV4ProAdapter)
        self.assertEqual("deepseek-ai/deepseek-v4-pro-0813", deepseek.model_id)

    # -------------------------------------------------------------------------
    # 7. Runner Dry-Run & Checkpointing
    # -------------------------------------------------------------------------

    def test_runner_dry_run(self) -> None:
        runner = BenchmarkRunner(output_dir=self.workspace / "data")
        models = [get_model_adapter("deepseek")]
        tasks = [
            BenchmarkTaskSpec(
                task_id="test-task-01",
                benchmark="TestBench",
                category="General",
                prompt="Say hello.",
            )
        ]
        records = runner.run_matrix(models, tasks, dry_run=True)
        self.assertEqual(0, len(records))  # Dry-run generates zero paid/live records

    def test_runner_modes_a_b_c_execution(self) -> None:
        """Validates that Runner executes Mode A, Mode B, and Mode C with proper uplift metrics."""
        class MockAdapter(ModelAdapter):
            def __init__(self, responses: list[str]) -> None:
                super().__init__(
                    model_id="meta/muse-glimmer-30b",
                    display_name="Mock Muse",
                    credential_ref="mock-cred",
                )
                self.responses = responses
                self.call_count = 0

            def generate(self, prompt: str, system_prompt: str = None, config: ModelRunConfig = None) -> ModelResponse:
                idx = min(self.call_count, len(self.responses) - 1)
                self.call_count += 1
                return ModelResponse(
                    content=self.responses[idx],
                    model=self.model_id,
                    provider=self.provider_id,
                    latency_ms=15.0,
                    input_tokens=30,
                    output_tokens=20,
                    total_tokens=50,
                )

            def stream(self, prompt: str, system_prompt: str = None, config: ModelRunConfig = None):
                yield self.generate(prompt, system_prompt, config)

            def chat(self, messages, system_prompt=None, config=None):
                return self.generate(messages[-1]["content"])

            def tool_call(self, prompt, tools, system_prompt=None, config=None):
                return self.generate(prompt)

            def multimodal_input(self, prompt, images, system_prompt=None, config=None):
                return self.generate(prompt)

            def structured_output(self, prompt, schema, system_prompt=None, config=None):
                return self.generate(prompt)

        # Responses for Mode A (fails), Mode B (succeeds), Mode C (3 stages, succeeds)
        mock_model = MockAdapter(responses=[
            "Wrong answer",               # Mode A response
            "The expected answer is 42.", # Mode B response
            "Decompose problem into steps.", # Mode C stage 1: planner
            "Intermediate step is 40 + 2.",  # Mode C stage 2: solver
            "Final verified answer is 42.",  # Mode C stage 3: critic
        ])

        task = BenchmarkTaskSpec(
            task_id="math-01",
            benchmark="TestBench",
            category="Arithmetic",
            prompt="What is 40 + 2?",
            expected_answer="42",
        )

        runner = BenchmarkRunner(output_dir=self.workspace / "data")
        records = runner.run_matrix(
            models=[mock_model],
            tasks=[task],
            modes=[
                BenchmarkMode.MODE_A_RAW_MODEL,
                BenchmarkMode.MODE_B_NEXUS_AGENT,
                BenchmarkMode.MODE_C_MULTI_MODEL_COLLABORATION,
            ],
            run_id_prefix="test-matrix",
        )

        self.assertEqual(3, len(records))
        rec_a, rec_b, rec_c = records[0], records[1], records[2]

        self.assertEqual(BenchmarkMode.MODE_A_RAW_MODEL, rec_a.mode)
        self.assertFalse(rec_a.success)
        self.assertEqual(0.0, rec_a.score)

        self.assertEqual(BenchmarkMode.MODE_B_NEXUS_AGENT, rec_b.mode)
        self.assertTrue(rec_b.success)
        self.assertEqual(1.0, rec_b.score)

        self.assertEqual(BenchmarkMode.MODE_C_MULTI_MODEL_COLLABORATION, rec_c.mode)
        self.assertTrue(rec_c.success)
        self.assertEqual(1.0, rec_c.score)
        # Mode C aggregated tokens across 3 stages (30*3 in, 20*3 out = 150 total)
        self.assertEqual(90, rec_c.input_tokens)
        self.assertEqual(60, rec_c.output_tokens)
        self.assertEqual(150, rec_c.total_tokens)

        # Check normalized summary output
        summaries = list((self.workspace / "data" / "normalized").glob("*.json"))
        self.assertEqual(1, len(summaries))
        with open(summaries[0], "r", encoding="utf-8") as f:
            summary = json.load(f)
        uplift = summary["uplift_summary"]["meta/muse-glimmer-30b"]["TestBench"]
        self.assertEqual(0.0, uplift["raw_mean_score"])
        self.assertEqual(1.0, uplift["nexus_mean_score"])
        self.assertEqual(1.0, uplift["collab_mean_score"])
        self.assertEqual(100.0, uplift["nexus_abs_uplift_pp"])
        self.assertEqual(100.0, uplift["collab_abs_uplift_pp"])

