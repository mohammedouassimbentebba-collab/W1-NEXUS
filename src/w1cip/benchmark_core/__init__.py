"""NEXUS Frontier Model Benchmarking & Telemetry Lab Core Engine."""

from __future__ import annotations

from .failure_taxonomy import classify_failure
from .latency_meter import LatencyMeter, LatencySnapshot, LatencyTimer
from .profiles import (
    AutoCredentialSelector,
    Capabilities,
    Credential,
    CredentialSelector,
    ExplicitCredentialSelector,
    ModelProfile,
    ProviderConfig,
    get_model_profile,
    get_provider,
    list_model_profiles,
    list_providers,
    register_model_profile,
    register_provider,
)
from .availability import (
    CredentialModelAvailability,
    CredentialModelAvailabilityTester,
    discover_catalog_model_ids,
)
from .model_adapter import (
    DeepSeekV4ProAdapter,
    KimiK3Adapter,
    ModelAdapter,
    MuseAdapter,
    OpenAICompatibleAdapter,
    get_model_adapter,
    resolve_model,
)
from .preflight import PreflightCheckResult, PreflightValidator
from .provenance_tracker import build_provenance_metadata, compute_environment_hash, get_nexus_git_commit
from .report_generator import BenchmarkReportGenerator
from .runner import BenchmarkRunner, BenchmarkTaskSpec
from .schema import (
    BenchmarkMode,
    BenchmarkStatus,
    CumulativeTokenUsage,
    FailureType,
    HealthCheckResult,
    ImageInput,
    ModelResponse,
    ModelRunConfig,
    NormalizedBenchmarkRunRecord,
    ToolDefinition,
    utc_now,
)
from .secrets import (
    get_credential,
    list_credential_refs,
    list_credentials,
    mask_secret,
    register_credential,
    register_secret_for_redaction,
    resolve_api_key,
    resolve_credential_key,
    scrub_secrets,
    validate_secret_presence,
)
from .token_meter import TokenAccountingSnapshot, TokenMeter
from .tool_meter import ToolCallRecord, ToolMeter

__all__ = [
    "BenchmarkMode",
    "BenchmarkReportGenerator",
    "Capabilities",
    "Credential",
    "CredentialModelAvailability",
    "CredentialModelAvailabilityTester",
    "ModelProfile",
    "ProviderConfig",
    "discover_catalog_model_ids",
    "get_model_profile",
    "get_provider",
    "BenchmarkRunner",
    "BenchmarkStatus",
    "BenchmarkTaskSpec",
    "CumulativeTokenUsage",
    "DeepSeekV4ProAdapter",
    "FailureType",
    "HealthCheckResult",
    "ImageInput",
    "KimiK3Adapter",
    "LatencyMeter",
    "LatencySnapshot",
    "LatencyTimer",
    "ModelAdapter",
    "ModelResponse",
    "ModelRunConfig",
    "MuseAdapter",
    "NormalizedBenchmarkRunRecord",
    "PreflightCheckResult",
    "PreflightValidator",
    "TokenAccountingSnapshot",
    "TokenMeter",
    "ToolCallRecord",
    "ToolDefinition",
    "ToolMeter",
    "build_provenance_metadata",
    "classify_failure",
    "compute_environment_hash",
    "get_model_adapter",
    "get_nexus_git_commit",
    "mask_secret",
    "register_secret_for_redaction",
    "resolve_api_key",
    "scrub_secrets",
    "utc_now",
    "validate_secret_presence",
]
