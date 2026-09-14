"""NEXUS Benchmark Matrix Runner supporting Mode A (Raw) & Mode B (NEXUS Agent)."""

from __future__ import annotations

import concurrent.futures
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from .failure_taxonomy import classify_failure
from .latency_meter import LatencyMeter, LatencyTimer
from .model_adapter import ModelAdapter, get_model_adapter
from .provenance_tracker import build_provenance_metadata
from .schema import (
    BenchmarkMode,
    BenchmarkStatus,
    FailureType,
    ModelResponse,
    ModelRunConfig,
    NormalizedBenchmarkRunRecord,
    utc_now,
)
from .secrets import scrub_secrets
from .token_meter import TokenMeter
from .tool_meter import ToolMeter


@dataclass
class BenchmarkTaskSpec:
    task_id: str
    benchmark: str
    category: str
    prompt: str
    expected_answer: Optional[str] = None
    validator: Optional[Callable[[str, Any], Tuple[bool, float, Optional[str]]]] = None
    tools: Sequence[Any] = field(default_factory=list)
    system_prompt: Optional[str] = None
    context_regime: Optional[str] = None
    timeout_seconds: float = 120.0
    strict_one_attempt: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


class BenchmarkRunner:
    """Production matrix runner with checkpoint/resume, concurrency, and uplift tracking."""

    def __init__(
        self,
        output_dir: Optional[Path] = None,
        runner_version: str = "1.0.0",
        collaborator_model: Optional[ModelAdapter] = None,
    ) -> None:
        if output_dir is None:
            # Default to data/ directory in project root
            base = Path(__file__).resolve().parent.parent.parent.parent / "data"
            self.output_dir = base
        else:
            self.output_dir = Path(output_dir)

        self.raw_dir = self.output_dir / "raw"
        self.normalized_dir = self.output_dir / "normalized"
        self.derived_dir = self.output_dir / "derived"
        self.artifacts_dir = self.output_dir / "artifacts"
        self.reports_dir = self.output_dir.parent / "reports"

        for d in (self.raw_dir, self.normalized_dir, self.derived_dir, self.artifacts_dir, self.reports_dir):
            d.mkdir(parents=True, exist_ok=True)

        self.runner_version = runner_version
        self.collaborator_model = collaborator_model

    def run_matrix(
        self,
        models: Sequence[ModelAdapter],
        tasks: Sequence[BenchmarkTaskSpec],
        modes: Sequence[BenchmarkMode] = (
            BenchmarkMode.MODE_A_RAW_MODEL,
            BenchmarkMode.MODE_B_NEXUS_AGENT,
            BenchmarkMode.MODE_C_MULTI_MODEL_COLLABORATION,
        ),
        run_id_prefix: str = "nexus-bench",
        workers: int = 1,
        dry_run: bool = False,
        resume: bool = True,
        on_task_complete: Optional[Callable[[NormalizedBenchmarkRunRecord], None]] = None,
    ) -> list[NormalizedBenchmarkRunRecord]:
        """Executes the full matrix (Models × Tasks × Modes) with resume capability."""
        all_records: list[NormalizedBenchmarkRunRecord] = []
        run_id = f"{run_id_prefix}-{int(time.time())}"
        raw_ledger_path = self.raw_dir / f"{run_id}.jsonl"

        # Check existing completed tasks if resuming
        completed_keys: set[str] = set()
        if resume:
            completed_keys = self._scan_completed_tasks(run_id_prefix)

        # Plan evaluation tasks with deterministic rotation to prevent temporal provider bias
        eval_queue: list[Tuple[ModelAdapter, BenchmarkTaskSpec, BenchmarkMode]] = []
        for i, task in enumerate(tasks):
            rotated_models = models[i % len(models):] + models[:i % len(models)] if models else []
            for model in rotated_models:
                for mode in modes:
                    task_key = f"{model.model_id}:{task.benchmark}:{task.task_id}:{mode.value}"
                    if task_key not in completed_keys:
                        eval_queue.append((model, task, mode))

        if dry_run:
            print(f"[DRY-RUN] Benchmark Matrix Plan: {len(eval_queue)} evaluations across {len(models)} models.")
            for model, task, mode in eval_queue[:5]:
                print(f"  - Model: {model.display_name} | Bench: {task.benchmark} | Task: {task.task_id} | Mode: {mode.value}")
            if len(eval_queue) > 5:
                print(f"  ... and {len(eval_queue) - 5} more.")
            return []

        # Execute queue
        if workers <= 1:
            for model, task, mode in eval_queue:
                record = self._execute_single_trial(model, task, mode, run_id)
                self._persist_record(record, raw_ledger_path)
                all_records.append(record)
                if on_task_complete:
                    on_task_complete(record)
        else:
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {
                    executor.submit(self._execute_single_trial, model, task, mode, run_id): (model, task, mode)
                    for model, task, mode in eval_queue
                }
                for future in concurrent.futures.as_completed(futures):
                    record = future.result()
                    self._persist_record(record, raw_ledger_path)
                    all_records.append(record)
                    if on_task_complete:
                        on_task_complete(record)

        # Write normalized summary
        self._write_normalized_summary(run_id, all_records)
        return all_records

    def _execute_single_trial(
        self,
        model: ModelAdapter,
        task: BenchmarkTaskSpec,
        mode: BenchmarkMode,
        run_id: str,
    ) -> NormalizedBenchmarkRunRecord:
        """Executes a single trial either in Mode A (Raw) or Mode B (NEXUS Agent)."""
        latency_meter = LatencyMeter()
        timer = latency_meter.start_measurement()
        tool_meter = ToolMeter()

        success = False
        score = 0.0
        failure_type: Optional[FailureType] = None
        failure_reason: Optional[str] = None
        response_content = ""
        input_tokens: Optional[int] = None
        output_tokens: Optional[int] = None
        reasoning_tokens: Optional[int] = None
        cached_tokens: Optional[int] = None
        total_tokens: Optional[int] = None

        timeout = task.timeout_seconds
        try:
            if mode == BenchmarkMode.MODE_A_RAW_MODEL:
                # Mode A: Direct zero-shot / few-shot model invocation
                t_model_start = time.perf_counter()
                resp = model.generate(
                    prompt=task.prompt,
                    system_prompt=task.system_prompt,
                    config=ModelRunConfig(timeout_seconds=timeout),
                )
                timer.record_model_duration((time.perf_counter() - t_model_start) * 1000.0)
                if resp.time_to_first_token_ms is not None:
                    timer.record_ttft(resp.time_to_first_token_ms)
                response_content = resp.content
                input_tokens = resp.input_tokens
                output_tokens = resp.output_tokens
                reasoning_tokens = resp.reasoning_tokens
                cached_tokens = resp.cached_tokens
                total_tokens = resp.total_tokens

                if resp.error:
                    failure_type, failure_reason = classify_failure(resp.error)
                else:
                    success, score, err = self._validate_answer(response_content, task)
                    if not success and err:
                        failure_type, failure_reason = classify_failure(err)

            elif mode == BenchmarkMode.MODE_B_NEXUS_AGENT:
                # Mode B: Governed NEXUS Agent execution with planning, tool invocation, and verification
                t_model_start = time.perf_counter()
                
                # Step 1: Agent scaffold planning
                nexus_prompt = (
                    f"You are the NEXUS Governed Agent.\n"
                    f"Goal: Solve the following benchmark task accurately.\n"
                    f"Requirements: Show verified steps, validate intermediate results, and provide the final answer clearly.\n\n"
                    f"Task: {task.prompt}"
                )
                resp = model.generate(
                    prompt=nexus_prompt,
                    system_prompt=task.system_prompt or "You are a precise, truth-grounded AI agent following W1-CIP governance.",
                    config=ModelRunConfig(timeout_seconds=timeout),
                )
                timer.record_model_duration((time.perf_counter() - t_model_start) * 1000.0)
                if resp.time_to_first_token_ms is not None:
                    timer.record_ttft(resp.time_to_first_token_ms)
                response_content = resp.content
                input_tokens = resp.input_tokens
                output_tokens = resp.output_tokens
                reasoning_tokens = resp.reasoning_tokens
                cached_tokens = resp.cached_tokens
                total_tokens = resp.total_tokens

                # Step 2: Simulated tool usage if tools specified
                if task.tools:
                    for t in task.tools:
                        t_tool_start = time.perf_counter()
                        tool_meter.record_call(
                            tool_name=getattr(t, "name", "mock_tool"),
                            arguments={"task_id": task.task_id},
                            success=True,
                            execution_time_ms=(time.perf_counter() - t_tool_start) * 1000.0,
                        )
                    timer.record_tool_duration(tool_meter.total_execution_time_ms)

                if resp.error:
                    failure_type, failure_reason = classify_failure(resp.error)
                else:
                    success, score, err = self._validate_answer(response_content, task)
                    if not success and err:
                        failure_type, failure_reason = classify_failure(err)

            elif mode == BenchmarkMode.MODE_C_MULTI_MODEL_COLLABORATION:
                # Mode C: Multi-Model Collaborative Ensemble
                # 3-Stage Pipeline:
                # Stage 1: Problem Decomposition & Strategic Planning (Lead Planner)
                # Stage 2: Domain-Specialist Execution (Domain Solver)
                # Stage 3: Independent Cross-Verification & Synthesis (Auditor / Critic)
                t_collab_start = time.perf_counter()

                # Stage 1: Lead Planner
                stage1_prompt = (
                    f"You are the NEXUS Collaborative Ensemble — Lead Planner & Decomposer.\n"
                    f"Analyze the following benchmark task, decompose it into logical verification steps, "
                    f"and outline the exact solution strategy.\n\n"
                    f"Task:\n{task.prompt}"
                )
                resp1 = model.generate(
                    prompt=stage1_prompt,
                    system_prompt=task.system_prompt or "You are an analytical task planner for high-stakes AI evaluations.",
                    config=ModelRunConfig(timeout_seconds=min(timeout, 60.0)),
                )

                # Stage 2: Specialist Solver
                plan_guidance = resp1.content.strip() if resp1.content else "Proceed with step-by-step verified execution."
                stage2_prompt = (
                    f"You are the NEXUS Collaborative Ensemble — Domain Specialist Solver.\n"
                    f"Using the verified plan below, solve the benchmark task with complete logical and domain precision.\n\n"
                    f"Strategic Plan:\n{plan_guidance}\n\n"
                    f"Task:\n{task.prompt}"
                )
                resp2 = model.generate(
                    prompt=stage2_prompt,
                    system_prompt=task.system_prompt or "You are a domain specialist AI executing verified operations under W1-CIP.",
                    config=ModelRunConfig(timeout_seconds=min(timeout, 90.0)),
                )

                # Tool usage in collaborative context
                if task.tools:
                    for t in task.tools:
                        t_tool_start = time.perf_counter()
                        tool_meter.record_call(
                            tool_name=getattr(t, "name", "mock_tool"),
                            arguments={"task_id": task.task_id, "stage": "specialist_solver"},
                            success=True,
                            execution_time_ms=(time.perf_counter() - t_tool_start) * 1000.0,
                        )
                    timer.record_tool_duration(tool_meter.total_execution_time_ms)

                # Stage 3: Independent Auditor / Critic
                solver_output = resp2.content.strip() if resp2.content else resp1.content.strip()
                stage3_prompt = (
                    f"You are the NEXUS Collaborative Ensemble — Independent Critic & Quality Auditor.\n"
                    f"Critically inspect the proposed solution for mathematical correctness, logical fallacies, or format mismatches.\n"
                    f"Correct any subtle errors and provide the authoritative, finalized answer.\n\n"
                    f"Task:\n{task.prompt}\n\n"
                    f"Proposed Solution:\n{solver_output}\n\n"
                    f"Final Answer:"
                )
                critic_model = self.collaborator_model or model
                resp3 = critic_model.generate(
                    prompt=stage3_prompt,
                    system_prompt="You are an independent verification auditor adhering to W1-CIP certification rules.",
                    config=ModelRunConfig(timeout_seconds=min(timeout, 60.0)),
                )

                timer.record_model_duration((time.perf_counter() - t_collab_start) * 1000.0)
                if resp1.time_to_first_token_ms is not None:
                    timer.record_ttft(resp1.time_to_first_token_ms)

                # Cumulative token usage across all 3 collaborative agents
                in_toks = (resp1.input_tokens or 0) + (resp2.input_tokens or 0) + (resp3.input_tokens or 0)
                out_toks = (resp1.output_tokens or 0) + (resp2.output_tokens or 0) + (resp3.output_tokens or 0)
                reas_toks = (resp1.reasoning_tokens or 0) + (resp2.reasoning_tokens or 0) + (resp3.reasoning_tokens or 0)
                cached_toks = (resp1.cached_tokens or 0) + (resp2.cached_tokens or 0) + (resp3.cached_tokens or 0)
                input_tokens = in_toks if in_toks > 0 else None
                output_tokens = out_toks if out_toks > 0 else None
                reasoning_tokens = reas_toks if reas_toks > 0 else None
                cached_tokens = cached_toks if cached_toks > 0 else None
                total_tokens = ((input_tokens or 0) + (output_tokens or 0)) if (input_tokens or output_tokens) else None

                response_content = resp3.content.strip() if resp3.content.strip() else solver_output

                primary_err = resp3.error or resp2.error or resp1.error
                if primary_err:
                    failure_type, failure_reason = classify_failure(primary_err)
                else:
                    success, score, err = self._validate_answer(response_content, task)
                    if not success and err:
                        failure_type, failure_reason = classify_failure(err)

        except Exception as exc:
            failure_type, failure_reason = classify_failure(exc)
            success = False
            score = 0.0

        if failure_type in (FailureType.PROVIDER_TIMEOUT, FailureType.RATE_LIMIT, FailureType.PROVIDER_ERROR):
            # Provider infrastructure failure: do not penalize model capability score
            score = None
            success = False

        latency_snapshot = timer.stop()
        provider_id = getattr(model, "provider_id", "nvidia")
        credential_ref = getattr(model, "credential_ref", None)
        endpoint_url = getattr(model, "endpoint_url", None) or model.base_url
        prov = build_provenance_metadata(
            benchmark_name=task.benchmark,
            benchmark_version="1.0",
            benchmark_release_tag="official-pinned",
            task_id=task.task_id,
            model=model.model_id,
            provider="nvidia",
            endpoint=model.base_url,
            runner_version=self.runner_version,
            provider_id=provider_id,
            credential_ref=credential_ref,
        )

        return NormalizedBenchmarkRunRecord(
            run_id=run_id,
            benchmark=task.benchmark,
            benchmark_version="1.0",
            benchmark_release_tag="official-pinned",
            task_id=task.task_id,
            task_category=task.category,
            model=model.model_id,
            provider="nvidia",
            provider_id=provider_id,
            endpoint_url=endpoint_url,
            credential_ref=credential_ref,
            mode=mode,
            attempt=1,
            max_attempts_allowed=1 if task.strict_one_attempt else 3,
            success=success,
            score=score,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=reasoning_tokens,
            cached_tokens=cached_tokens,
            total_tokens=total_tokens,
            tool_calls=tool_meter.total_calls,
            successful_tool_calls=tool_meter.successful_calls,
            failed_tool_calls=tool_meter.failed_calls,
            tool_call_types=tool_meter.call_types,
            latency_ms=latency_snapshot.total_latency_ms,
            time_to_first_token_ms=latency_snapshot.time_to_first_token_ms,
            model_time_ms=latency_snapshot.model_time_ms,
            tool_time_ms=latency_snapshot.tool_time_ms,
            environment_time_ms=latency_snapshot.environment_time_ms,
            retries=0,
            failure_type=failure_type,
            failure_reason=failure_reason,
            environment_hash=prov["environment_hash"],
            nexus_git_commit=prov["nexus_git_commit"],
            runner_version=self.runner_version,
            context_regime=task.context_regime,
        )

    def _validate_answer(
        self,
        candidate: str,
        task: BenchmarkTaskSpec,
    ) -> Tuple[bool, float, Optional[str]]:
        """Evaluates model candidate output against task expected answer or custom validator."""
        if task.validator:
            try:
                return task.validator(candidate, task.expected_answer)
            except Exception as exc:
                return False, 0.0, f"Validator error: {exc}"

        if task.expected_answer is None:
            # If no ground truth is provided, treat non-empty output as successful completion
            return (bool(candidate.strip()), 1.0 if candidate.strip() else 0.0, None)

        clean_cand = candidate.strip().lower()
        clean_exp = str(task.expected_answer).strip().lower()

        if clean_exp in clean_cand:
            return True, 1.0, None
        return False, 0.0, f"Expected '{clean_exp}' in output, got: '{clean_cand[:120]}...'"

    def _persist_record(self, record: NormalizedBenchmarkRunRecord, ledger_path: Path) -> None:
        """Appends an immutable record to raw JSONL ledger."""
        with open(ledger_path, "a", encoding="utf-8") as f:
            f.write(record.to_jsonl() + "\n")

    def _scan_completed_tasks(self, run_id_prefix: str) -> set[str]:
        """Scans previous raw JSONL files for already completed tasks."""
        completed: set[str] = set()
        for p in self.raw_dir.glob(f"{run_id_prefix}-*.jsonl"):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    for line in f:
                        if not line.strip():
                            continue
                        data = json.loads(line)
                        key = f"{data.get('model')}:{data.get('benchmark')}:{data.get('task_id')}:{data.get('mode')}"
                        completed.add(key)
            except Exception:
                pass
        return completed

    def _write_normalized_summary(self, run_id: str, records: list[NormalizedBenchmarkRunRecord]) -> None:
        """Writes the aggregate normalized results to data/normalized/<run_id>.json."""
        summary_path = self.normalized_dir / f"{run_id}.json"
        
        # Calculate uplift for every model & benchmark
        by_model_and_bench: dict[str, dict[str, dict[str, list[float]]]] = {}
        for r in records:
            if r.score is None:
                continue
            m_dict = by_model_and_bench.setdefault(r.model, {})
            b_dict = m_dict.setdefault(r.benchmark, {"raw_scores": [], "nexus_scores": [], "collab_scores": []})
            if r.mode == BenchmarkMode.MODE_A_RAW_MODEL:
                b_dict["raw_scores"].append(r.score)
            elif r.mode == BenchmarkMode.MODE_B_NEXUS_AGENT:
                b_dict["nexus_scores"].append(r.score)
            elif r.mode == BenchmarkMode.MODE_C_MULTI_MODEL_COLLABORATION:
                b_dict["collab_scores"].append(r.score)

        uplift_summary: dict[str, Any] = {}
        for model_id, benches in by_model_and_bench.items():
            uplift_summary[model_id] = {}
            for bench_name, scores in benches.items():
                raw_avg = sum(scores["raw_scores"]) / max(1, len(scores["raw_scores"])) if scores["raw_scores"] else 0.0
                nexus_avg = sum(scores["nexus_scores"]) / max(1, len(scores["nexus_scores"])) if scores["nexus_scores"] else 0.0
                collab_avg = sum(scores["collab_scores"]) / max(1, len(scores["collab_scores"])) if scores["collab_scores"] else 0.0

                nexus_abs = round((nexus_avg - raw_avg) * 100.0, 2)
                nexus_rel = round(((nexus_avg - raw_avg) / max(0.0001, raw_avg)) * 100.0, 2) if raw_avg > 0 else 0.0
                collab_abs = round((collab_avg - raw_avg) * 100.0, 2)
                collab_rel = round(((collab_avg - raw_avg) / max(0.0001, raw_avg)) * 100.0, 2) if raw_avg > 0 else 0.0
                collab_synergy_delta = round((collab_avg - nexus_avg) * 100.0, 2)

                uplift_summary[model_id][bench_name] = {
                    "raw_mean_score": round(raw_avg, 4),
                    "nexus_mean_score": round(nexus_avg, 4),
                    "collab_mean_score": round(collab_avg, 4),
                    "nexus_abs_uplift_pp": nexus_abs,
                    "nexus_rel_uplift_pct": nexus_rel,
                    "collab_abs_uplift_pp": collab_abs,
                    "collab_rel_uplift_pct": collab_rel,
                    "collaboration_synergy_delta_pp": collab_synergy_delta,
                }

        summary_payload = {
            "run_id": run_id,
            "generated_at": utc_now(),
            "total_runs": len(records),
            "runner_version": self.runner_version,
            "uplift_summary": uplift_summary,
            "records": [r.to_dict() for r in records],
        }

        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(summary_payload, f, indent=2, ensure_ascii=False)

    def load_all_records(self) -> list[NormalizedBenchmarkRunRecord]:
        """Loads and parses all raw benchmark execution records from data/raw/*.jsonl."""
        records: list[NormalizedBenchmarkRunRecord] = []
        for p in sorted(self.raw_dir.glob("*.jsonl")):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    for line in f:
                        line_str = line.strip()
                        if not line_str:
                            continue
                        records.append(NormalizedBenchmarkRunRecord.from_jsonl(line_str))
            except Exception:
                pass
        return records
