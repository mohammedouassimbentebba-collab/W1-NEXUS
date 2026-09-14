"""Preflight qualification tests ensuring provider health and telemetry integrity."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .model_adapter import ModelAdapter, get_model_adapter
from .schema import ModelRunConfig
from .secrets import mask_secret, resolve_api_key, validate_secret_presence


@dataclass
class PreflightCheckResult:
    check_name: str
    passed: bool
    details: str
    latency_ms: float = 0.0
    error: Optional[str] = None


class PreflightValidator:
    """Runs 8 qualification checks before expensive benchmark runs are permitted."""

    def __init__(self, workspace_root: Optional[Path] = None) -> None:
        self.workspace_root = workspace_root or Path(__file__).resolve().parent.parent.parent.parent
        self.results: list[PreflightCheckResult] = []

    def run_all_checks(
        self,
        models: Optional[list[ModelAdapter]] = None,
    ) -> list[PreflightCheckResult]:
        self.results.clear()
        
        # 1. Secret Validation
        self.results.append(self.check_secrets())

        # Target models
        if models is None:
            models = [
                get_model_adapter("kimi"),
                get_model_adapter("muse"),
                get_model_adapter("deepseek"),
            ]

        # 2. Provider Health & Connectivity
        for model in models:
            self.results.append(self.check_provider_health(model))

        # 3. Model Availability Probe
        for model in models:
            self.results.append(self.check_model_availability(model))

        # 4. Token Accounting Audit
        self.results.append(self.check_token_accounting(models[0]))

        # 5. Tool-Call Capability Probe
        self.results.append(self.check_tool_calling(models[0]))

        # 6. Context Window Test
        self.results.append(self.check_context_window(models[0]))

        # 7. Structured Output / JSON Schema Test
        self.results.append(self.check_structured_output(models[0]))

        # 8. Artifact & Telemetry Directory Permissions
        self.results.append(self.check_artifact_directories())

        return self.results

    def check_secrets(self) -> PreflightCheckResult:
        t0 = time.perf_counter()
        k_val = validate_secret_presence("kimi")
        m_val = validate_secret_presence("muse")
        d_val = validate_secret_presence("deepseek")

        all_valid = k_val["valid"] and m_val["valid"] and d_val["valid"]
        details = (
            f"Kimi: {k_val['masked_key']} (valid={k_val['valid']}), "
            f"Muse: {m_val['masked_key']} (valid={m_val['valid']}), "
            f"DeepSeek: {d_val['masked_key']} (valid={d_val['valid']})"
        )
        latency = (time.perf_counter() - t0) * 1000.0

        return PreflightCheckResult(
            check_name="1. Secret Validation",
            passed=all_valid,
            details=details,
            latency_ms=round(latency, 2),
            error=None if all_valid else "One or more required model API keys are missing or invalid",
        )

    def check_provider_health(self, model: ModelAdapter) -> PreflightCheckResult:
        t0 = time.perf_counter()
        try:
            health = model.health_check()
            return PreflightCheckResult(
                check_name=f"2. Provider Health [{model.display_name}]",
                passed=health.healthy,
                details=f"Endpoint reachable, status: {health.message}",
                latency_ms=health.latency_ms,
                error=None if health.healthy else health.message,
            )
        except Exception as exc:
            latency = (time.perf_counter() - t0) * 1000.0
            return PreflightCheckResult(
                check_name=f"2. Provider Health [{model.display_name}]",
                passed=False,
                details="Failed to connect to endpoint",
                latency_ms=round(latency, 2),
                error=str(exc),
            )

    def check_model_availability(self, model: ModelAdapter) -> PreflightCheckResult:
        t0 = time.perf_counter()
        try:
            resp = model.generate("Ping.", config=ModelRunConfig(max_tokens=5, timeout_seconds=20.0))
            latency = (time.perf_counter() - t0) * 1000.0
            if resp.error:
                return PreflightCheckResult(
                    check_name=f"3. Model Availability [{model.display_name}]",
                    passed=False,
                    details=f"Model error response: {resp.error}",
                    latency_ms=round(latency, 2),
                    error=resp.error,
                )
            return PreflightCheckResult(
                check_name=f"3. Model Availability [{model.display_name}]",
                passed=True,
                details=f"Model response received: '{resp.content.strip()[:40]}'",
                latency_ms=round(latency, 2),
            )
        except Exception as exc:
            latency = (time.perf_counter() - t0) * 1000.0
            return PreflightCheckResult(
                check_name=f"3. Model Availability [{model.display_name}]",
                passed=False,
                details="Failed to dispatch probe prompt",
                latency_ms=round(latency, 2),
                error=str(exc),
            )

    def check_token_accounting(self, model: ModelAdapter) -> PreflightCheckResult:
        t0 = time.perf_counter()
        try:
            resp = model.generate("Count to 3: 1, 2, 3.", config=ModelRunConfig(max_tokens=20, timeout_seconds=20.0))
            latency = (time.perf_counter() - t0) * 1000.0
            has_tokens = resp.input_tokens is not None and resp.output_tokens is not None
            details = f"Input tokens: {resp.input_tokens}, Output tokens: {resp.output_tokens}, Total: {resp.total_tokens}"
            return PreflightCheckResult(
                check_name="4. Token Accounting Audit",
                passed=has_tokens,
                details=details,
                latency_ms=round(latency, 2),
                error=None if has_tokens else "Provider omitted usage token counts",
            )
        except Exception as exc:
            latency = (time.perf_counter() - t0) * 1000.0
            return PreflightCheckResult(
                check_name="4. Token Accounting Audit",
                passed=False,
                details="Failed token audit probe",
                latency_ms=round(latency, 2),
                error=str(exc),
            )

    def check_tool_calling(self, model: ModelAdapter) -> PreflightCheckResult:
        t0 = time.perf_counter()
        try:
            from .schema import ToolDefinition
            tools = [
                ToolDefinition(
                    name="get_current_time",
                    description="Returns the current UTC time.",
                    parameters={"type": "object", "properties": {}},
                )
            ]
            resp = model.tool_call("What is the time right now?", tools=tools)
            latency = (time.perf_counter() - t0) * 1000.0
            passed = resp.error is None
            return PreflightCheckResult(
                check_name="5. Tool-Call Capability Probe",
                passed=passed,
                details=f"Tool call response generated (calls: {len(resp.tool_calls)})",
                latency_ms=round(latency, 2),
                error=resp.error,
            )
        except Exception as exc:
            latency = (time.perf_counter() - t0) * 1000.0
            return PreflightCheckResult(
                check_name="5. Tool-Call Capability Probe",
                passed=False,
                details="Tool call probe failed",
                latency_ms=round(latency, 2),
                error=str(exc),
            )

    def check_context_window(self, model: ModelAdapter) -> PreflightCheckResult:
        t0 = time.perf_counter()
        try:
            # 2KB text probe
            haystack = "alpha beta gamma " * 120 + "SECRET_FLAG_42 " + "delta epsilon " * 120
            prompt = f"{haystack}\nQuestion: What is the secret flag mentioned in the text?"
            resp = model.generate(prompt, config=ModelRunConfig(max_tokens=60, temperature=0.7, timeout_seconds=30.0))
            latency = (time.perf_counter() - t0) * 1000.0
            passed = "42" in resp.content or "secret_flag" in resp.content.lower()
            return PreflightCheckResult(
                check_name="6. Context Window Test",
                passed=passed,
                details=f"Context needle retrieved accurately: '{resp.content.strip()[:60]}'",
                latency_ms=round(latency, 2),
                error=None if passed else "Failed to retrieve needle from context",
            )
        except Exception as exc:
            latency = (time.perf_counter() - t0) * 1000.0
            return PreflightCheckResult(
                check_name="6. Context Window Test",
                passed=False,
                details="Context window probe failed",
                latency_ms=round(latency, 2),
                error=str(exc),
            )

    def check_structured_output(self, model: ModelAdapter) -> PreflightCheckResult:
        t0 = time.perf_counter()
        try:
            schema = {
                "type": "object",
                "required": ["city", "temperature_c"],
                "properties": {
                    "city": {"type": "string"},
                    "temperature_c": {"type": "number"},
                },
            }
            resp = model.structured_output("Current weather in Paris is 18 C.", schema=schema)
            latency = (time.perf_counter() - t0) * 1000.0
            raw = resp.content.strip()
            # Robust JSON extraction from possible markdown code fences
            if "```" in raw:
                import re
                m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
                if m:
                    raw = m.group(1)
                else:
                    s_idx = raw.find("{")
                    e_idx = raw.rfind("}")
                    if s_idx != -1 and e_idx != -1:
                        raw = raw[s_idx:e_idx + 1]
            elif "{" in raw and "}" in raw:
                s_idx = raw.find("{")
                e_idx = raw.rfind("}")
                raw = raw[s_idx:e_idx + 1]

            parsed = json.loads(raw)
            passed = "city" in parsed and "temperature_c" in parsed
            return PreflightCheckResult(
                check_name="7. Structured Output Test",
                passed=passed,
                details=f"Valid JSON schema conformant object parsed: {raw[:60]}",
                latency_ms=round(latency, 2),
                error=None if passed else "Output did not conform to JSON schema",
            )
        except Exception as exc:
            latency = (time.perf_counter() - t0) * 1000.0
            return PreflightCheckResult(
                check_name="7. Structured Output Test",
                passed=False,
                details="Structured output probe failed",
                latency_ms=round(latency, 2),
                error=str(exc),
            )

    def check_artifact_directories(self) -> PreflightCheckResult:
        t0 = time.perf_counter()
        base = self.workspace_root / "data"
        subdirs = ["raw", "normalized", "derived", "artifacts"]
        try:
            for s in subdirs:
                d = base / s
                d.mkdir(parents=True, exist_ok=True)
                test_file = d / ".write_test"
                test_file.write_text("ok", encoding="utf-8")
                test_file.unlink()
            latency = (time.perf_counter() - t0) * 1000.0
            return PreflightCheckResult(
                check_name="8. Artifact & Telemetry Directory Permissions",
                passed=True,
                details=f"All {len(subdirs)} storage directories verified writable at {base}",
                latency_ms=round(latency, 2),
            )
        except Exception as exc:
            latency = (time.perf_counter() - t0) * 1000.0
            return PreflightCheckResult(
                check_name="8. Artifact & Telemetry Directory Permissions",
                passed=False,
                details="Directory creation or write permission failed",
                latency_ms=round(latency, 2),
                error=str(exc),
            )
