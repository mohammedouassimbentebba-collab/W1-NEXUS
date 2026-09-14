"""Core normalized data contracts and schemas for NEXUS Benchmarking Lab."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Sequence


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class BenchmarkMode(str, Enum):
    MODE_A_RAW_MODEL = "MODE_A_RAW_MODEL"
    MODE_B_NEXUS_AGENT = "MODE_B_NEXUS_AGENT"
    MODE_C_MULTI_MODEL_COLLABORATION = "MODE_C_MULTI_MODEL_COLLABORATION"


class BenchmarkStatus(str, Enum):
    NOT_STARTED = "NOT_STARTED"
    CONFIGURED = "CONFIGURED"
    SMOKE_TESTED = "SMOKE_TESTED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"


class FailureType(str, Enum):
    MODEL_ERROR = "MODEL_ERROR"
    WRONG_REASONING = "WRONG_REASONING"
    TOOL_ERROR = "TOOL_ERROR"
    TIMEOUT = "TIMEOUT"
    PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
    ENVIRONMENT_ERROR = "ENVIRONMENT_ERROR"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    RATE_LIMIT = "RATE_LIMIT"
    CONTEXT_LIMIT = "CONTEXT_LIMIT"
    PARSER_ERROR = "PARSER_ERROR"
    HARNESS_ERROR = "HARNESS_ERROR"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class ImageInput:
    image_bytes: bytes
    mime_type: str = "image/png"
    description: Optional[str] = None


@dataclass(frozen=True)
class ModelRunConfig:
    temperature: float = 0.0
    top_p: float = 1.0
    max_tokens: int = 4096
    timeout_seconds: float = 120.0
    seed: Optional[int] = 42
    stop_sequences: tuple[str, ...] = ()
    extra_body: Mapping[str, Any] = field(default_factory=dict)
    system_prompt: Optional[str] = None


@dataclass
class ModelResponse:
    content: str
    model: str
    provider: str
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    reasoning_tokens: Optional[int] = None
    cached_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    latency_ms: float = 0.0
    time_to_first_token_ms: Optional[float] = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    finish_reason: Optional[str] = None
    error: Optional[str] = None
    is_estimated: bool = False
    raw_response_metadata: dict[str, Any] = field(default_factory=dict)

    def as_normalized_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "provider": self.provider,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "cached_tokens": self.cached_tokens,
            "total_tokens": self.total_tokens,
            "latency_ms": round(self.latency_ms, 2),
            "time_to_first_token_ms": round(self.time_to_first_token_ms, 2) if self.time_to_first_token_ms is not None else None,
            "tool_calls": len(self.tool_calls),
            "finish_reason": self.finish_reason,
            "error": self.error,
            "is_estimated": self.is_estimated,
        }


@dataclass
class CumulativeTokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: Optional[int] = None
    cached_tokens: int = 0
    total_tokens: int = 0
    total_requests: int = 0


@dataclass
class HealthCheckResult:
    healthy: bool
    model: str
    provider: str
    latency_ms: float
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class NormalizedBenchmarkRunRecord:
    run_id: str
    benchmark: str
    benchmark_version: str
    benchmark_release_tag: str
    task_id: str
    task_category: str
    model: str
    provider: str
    mode: BenchmarkMode
    attempt: int
    max_attempts_allowed: int
    success: bool
    score: float
    input_tokens: Optional[int]
    output_tokens: Optional[int]
    reasoning_tokens: Optional[int]
    cached_tokens: Optional[int]
    total_tokens: Optional[int]
    tool_calls: int
    successful_tool_calls: int
    failed_tool_calls: int
    tool_call_types: list[str]
    latency_ms: float
    time_to_first_token_ms: Optional[float]
    model_time_ms: float
    tool_time_ms: float
    environment_time_ms: float
    retries: int
    failure_type: Optional[FailureType]
    failure_reason: Optional[str]
    timestamp: str = field(default_factory=utc_now)
    environment_hash: str = ""
    nexus_git_commit: str = ""
    runner_version: str = "1.0.0"
    is_public_local: bool = True
    context_regime: Optional[str] = None  # E.g., '512K', '768K', '1M'
    artifacts: list[str] = field(default_factory=list)
    agent_trace: list[dict[str, Any]] = field(default_factory=list)
    # Multi-model / multi-credential provenance. Safe metadata only.
    provider_id: Optional[str] = None
    endpoint_url: Optional[str] = None
    credential_ref: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if isinstance(self.mode, BenchmarkMode):
            data["mode"] = self.mode.value
        if isinstance(self.failure_type, FailureType):
            data["failure_type"] = self.failure_type.value
        return data

    def to_jsonl(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NormalizedBenchmarkRunRecord:
        clean_data = dict(data)
        if "mode" in clean_data and isinstance(clean_data["mode"], str):
            clean_data["mode"] = BenchmarkMode(clean_data["mode"])
        if "failure_type" in clean_data and clean_data["failure_type"] and isinstance(clean_data["failure_type"], str):
            try:
                clean_data["failure_type"] = FailureType(clean_data["failure_type"])
            except ValueError:
                clean_data["failure_type"] = FailureType.UNKNOWN
        # Drop unexpected keys if schema evolves
        valid_keys = {f.name for f in fields(cls)}
        filtered = {k: v for k, v in clean_data.items() if k in valid_keys}
        return cls(**filtered)

    @classmethod
    def from_jsonl(cls, json_str: str) -> NormalizedBenchmarkRunRecord:
        return cls.from_dict(json.loads(json_str))

