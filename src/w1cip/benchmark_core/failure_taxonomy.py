"""Rigorous failure classification taxonomy and error diagnostics."""

from __future__ import annotations

from typing import Any, Optional, Tuple

from .schema import FailureType


def classify_failure(
    error: Any,
    status_code: Optional[int] = None,
    phase: str = "execution",
    is_infra_pre_execution: bool = False,
) -> Tuple[FailureType, str]:
    """Classifies an error into a standardized FailureType and diagnostic message.

    Never classifies a failure as just 'failed'. Distinguishes infrastructure/environment
    failures from model reasoning or output errors.
    """
    if is_infra_pre_execution:
        return FailureType.ENVIRONMENT_ERROR, f"Infrastructure setup failure before task dispatch: {error}"

    err_str = str(error)
    err_low = err_str.lower()

    # 1. Rate limiting
    if status_code == 429 or "rate limit" in err_low or "quota" in err_low or "too many requests" in err_low:
        return FailureType.RATE_LIMIT, f"HTTP 429 Rate limit / quota exceeded: {err_str}"

    # 2. Context limits
    if "context length" in err_low or "context window" in err_low or "maximum context" in err_low or "too long" in err_low:
        return FailureType.CONTEXT_LIMIT, f"Context window exceeded: {err_str}"

    # 3. Timeouts
    if "timeout" in err_low or "timed out" in err_low or "deadline exceeded" in err_low:
        if "connection" in err_low or "request timed out" in err_low or "read operation" in err_low or "gateway" in err_low or "provider" in err_low or "socket" in err_low or "apitimeouterror" in err_low:
            return FailureType.PROVIDER_TIMEOUT, f"Upstream provider network/queue timeout during {phase}: {err_str}"
        return FailureType.TIMEOUT, f"Execution timed out during {phase}: {err_str}"

    # 4. Provider / Upstream HTTP 5xx
    if (status_code and 500 <= status_code < 600) or "bad gateway" in err_low or "service unavailable" in err_low or "gateway timeout" in err_low:
        return FailureType.PROVIDER_ERROR, f"Upstream provider infrastructure failure (HTTP {status_code}): {err_str}"

    # 5. Tool execution errors
    if phase == "tool_execution" or "tool failed" in err_low or "command exited with code" in err_low:
        return FailureType.TOOL_ERROR, f"Tool execution failed: {err_str}"

    # 6. Parser / JSON decoding errors
    if "jsondecodeerror" in err_low or "failed to parse" in err_low or "invalid json" in err_low or "syntax error" in err_low:
        return FailureType.PARSER_ERROR, f"Model output parser failure: {err_str}"

    # 7. Model hallucinations or non-compliant output
    if "contract mismatch" in err_low or "schema violation" in err_low or "missing required field" in err_low:
        return FailureType.MODEL_ERROR, f"Model output contract violation: {err_str}"

    # 8. Incorrect reasoning or failed unit tests / assertions
    if "assertionerror" in err_low or "test failed" in err_low or "ground truth mismatch" in err_low or "incorrect answer" in err_low:
        return FailureType.WRONG_REASONING, f"Logical/reasoning defect: {err_str}"

    # 9. Harness or framework internal errors
    if phase in ("harness_setup", "evaluation_validation") or "benchmark harness error" in err_low:
        return FailureType.HARNESS_ERROR, f"Harness validation error: {err_str}"

    # 10. OS / Sandbox / Environment errors
    if "connection refused" in err_low or "permission denied" in err_low or "docker error" in err_low or "no such file or directory" in err_low:
        return FailureType.ENVIRONMENT_ERROR, f"Environment/sandbox failure: {err_str}"

    return FailureType.UNKNOWN, f"Unclassified failure: {err_str}"
