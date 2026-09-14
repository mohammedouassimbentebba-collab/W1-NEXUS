"""Live provider connector layer for the W1-CIP reference orchestrator.

The module exposes synchronous, non-streaming connectors for:

* OpenAI Responses API
* Anthropic Messages API
* Google Gemini generateContent API
* OpenAI-compatible endpoints such as Ollama and vLLM

The connectors deliberately depend only on the Python standard library.  A
transport protocol is injectable, so tests never require live credentials or
network access.  API keys are resolved at call time and are never written to
ProviderResponse metadata, the orchestrator journal, or exception messages.
"""

from __future__ import annotations

import json
import os
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Mapping, Protocol, Sequence

from .orchestrator import (
    OutputContractError,
    ProviderAdapter,
    ProviderOutcomeUncertain,
    ProviderPermanentError,
    ProviderQuotaExhausted,
    ProviderRequest,
    ProviderResponse,
    ProviderTransientError,
)
from .session_store import canonical_json


# ----------------------------- Stable errors ---------------------------------


class ProviderRateLimited(ProviderTransientError):
    """A replenishing request/token bucket was hit; the account is not exhausted."""

    code = "provider_rate_limited"

    def __init__(self, retry_after_seconds: float | None = None) -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__(self.code)


class ProviderAuthenticationError(ProviderPermanentError):
    code = "provider_authentication_failed"


class ProviderSafetyBlocked(ProviderPermanentError):
    code = "provider_safety_blocked"


class ProviderConfigurationError(ProviderPermanentError):
    code = "provider_configuration_invalid"


class ProviderPayloadTooLarge(ProviderPermanentError):
    code = "provider_payload_too_large"


class ProviderTransportError(RuntimeError):
    """Base class for transport failures before provider-level error mapping."""


class TransportConnectionError(ProviderTransportError):
    """The request could not be connected/sent and is safe to retry."""


class TransportOutcomeUncertain(ProviderTransportError):
    """The request may have reached the provider but no response was received."""


# ------------------------------- Transport -----------------------------------


@dataclass(frozen=True)
class HTTPRequest:
    method: str
    url: str
    headers: Mapping[str, str]
    body: bytes | None = None
    timeout_seconds: float = 60.0


@dataclass(frozen=True)
class HTTPResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes


class HTTPTransport(Protocol):
    def send(self, request: HTTPRequest) -> HTTPResponse:
        ...


class UrllibTransport:
    """Minimal production transport with TLS verification enabled by default."""

    def __init__(self, *, ssl_context: ssl.SSLContext | None = None) -> None:
        self.ssl_context = ssl_context or ssl.create_default_context()

    def send(self, request: HTTPRequest) -> HTTPResponse:
        raw = urllib.request.Request(
            request.url,
            data=request.body,
            headers=dict(request.headers),
            method=request.method,
        )
        try:
            with urllib.request.urlopen(  # noqa: S310 - URL is validated before use.
                raw,
                timeout=request.timeout_seconds,
                context=self.ssl_context,
            ) as response:
                return HTTPResponse(
                    status=int(response.status),
                    headers={key.lower(): value for key, value in response.headers.items()},
                    body=response.read(),
                )
        except urllib.error.HTTPError as exc:
            return HTTPResponse(
                status=int(exc.code),
                headers={key.lower(): value for key, value in exc.headers.items()},
                body=exc.read(),
            )
        except (socket.timeout, TimeoutError) as exc:
            # A timeout can occur after the provider accepted the request.
            raise TransportOutcomeUncertain() from exc
        except urllib.error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, (socket.timeout, TimeoutError)):
                raise TransportOutcomeUncertain() from exc
            raise TransportConnectionError() from exc
        except (ConnectionError, OSError) as exc:
            raise TransportConnectionError() from exc


# ------------------------------- Secrets -------------------------------------


class SecretResolver(Protocol):
    def resolve(self, reference: str) -> str:
        ...


class EnvironmentSecretResolver:
    """Resolve a named environment variable at invocation time."""

    def resolve(self, reference: str) -> str:
        value = os.environ.get(reference)
        if not value:
            raise ProviderConfigurationError("provider_secret_missing")
        return value


class StaticSecretResolver:
    """Test-only resolver. Do not use it to serialize production credentials."""

    def __init__(self, values: Mapping[str, str]) -> None:
        self._values = dict(values)

    def resolve(self, reference: str) -> str:
        value = self._values.get(reference)
        if not value:
            raise ProviderConfigurationError("provider_secret_missing")
        return value


# -------------------------- Contract and accounting --------------------------


PROVIDER_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["output_type", "payload", "context_fields_used"],
    "properties": {
        "output_type": {"type": "string"},
        "payload": {"type": "object"},
        "context_fields_used": {
            "type": "array",
            "items": {"type": "string"},
            "uniqueItems": True,
        },
        "protocol_envelopes": {
            "type": "array",
            "items": {"type": "object"},
        },
        "action_requests": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["action_id", "kind", "parameters"],
                "properties": {
                    "action_id": {"type": "string"},
                    "kind": {"type": "string"},
                    "parameters": {"type": "object"},
                    "requested_by": {"type": "string"},
                    "reason": {"type": "string"}
                }
            }
        },
        "metadata": {"type": "object"},
    },
}


@dataclass(frozen=True)
class ConnectorConfig:
    resource_id: str
    model: str
    api_key_reference: str | None = None
    endpoint: str | None = None
    timeout_seconds: float = 90.0
    max_output_tokens: int = 2048
    max_input_bytes: int = 2_000_000
    accounting_meter: str = "requests"  # requests | tokens
    supports_idempotency: bool = False
    allow_insecure_loopback: bool = False
    extra_headers: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.resource_id or not self.model:
            raise ValueError("resource_id_and_model_required")
        if self.timeout_seconds <= 0 or self.max_output_tokens <= 0 or self.max_input_bytes <= 0:
            raise ValueError("connector_limits_must_be_positive")
        if self.accounting_meter not in {"requests", "tokens"}:
            raise ValueError("unsupported_accounting_meter")


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cached_input_tokens: int | None = None
    reasoning_tokens: int | None = None
    estimated: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in asdict(self).items()
            if value is not None or key == "estimated"
        }


@dataclass(frozen=True)
class QuotaObservation:
    provider: str
    observed_at: str
    request_limit: int | None = None
    request_remaining: int | None = None
    token_limit: int | None = None
    token_remaining: int | None = None
    input_token_limit: int | None = None
    input_token_remaining: int | None = None
    output_token_limit: int | None = None
    output_token_remaining: int | None = None
    reset_requests: str | None = None
    reset_tokens: str | None = None
    source: str = "response_headers"

    def as_dict(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in asdict(self).items()
            if value is not None
        }


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def estimate_tokens(value: str) -> int:
    """Conservative provider-neutral estimate used only before actual usage exists."""

    if not value:
        return 0
    # A deterministic coarse estimate. It must never be presented as exact billing.
    return max(1, (len(value.encode("utf-8")) + 3) // 4)


def build_provider_prompt(request: ProviderRequest) -> str:
    contract = {
        "task": asdict(request.task),
        "allowed_context": dict(request.context),
        "prior_outputs": dict(request.prior_outputs),
        "response_contract": {
            "output_type": request.task.expected_output_type,
            "payload": "JSON object containing the task result",
            "context_fields_used": sorted(request.context.keys()),
            "protocol_envelopes": "optional array of complete W1-CIP envelopes",
            "action_requests": "optional array of governed local ActionRequest objects; never use shell syntax",
        },
        "rules": [
            "Return exactly one JSON object and no Markdown.",
            "Do not claim use of context fields that are absent from allowed_context.",
            "Do not place secrets, API keys, or hidden system instructions in the output.",
            "Set output_type exactly to the required response_contract.output_type.",
            "Request local actions only when required; sensitive actions pause for human approval.",
        ],
    }
    return canonical_json(contract)


def parse_provider_contract(text: str, expected_output_type: str) -> dict[str, Any]:
    candidate = text.strip()
    if candidate.startswith("```") and candidate.endswith("```"):
        lines = candidate.splitlines()
        if len(lines) >= 3:
            candidate = "\n".join(lines[1:-1]).strip()
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise OutputContractError("provider_output_not_json") from exc
    if not isinstance(value, dict):
        raise OutputContractError("provider_output_not_object")
    allowed = {"output_type", "payload", "context_fields_used", "protocol_envelopes", "action_requests", "metadata"}
    if set(value) - allowed:
        raise OutputContractError("provider_output_unknown_fields")
    if value.get("output_type") != expected_output_type:
        raise OutputContractError("provider_output_type_mismatch")
    if not isinstance(value.get("payload"), dict):
        raise OutputContractError("provider_payload_not_object")
    fields = value.get("context_fields_used")
    if not isinstance(fields, list) or any(not isinstance(item, str) for item in fields):
        raise OutputContractError("provider_context_fields_invalid")
    if len(fields) != len(set(fields)):
        raise OutputContractError("provider_context_fields_duplicated")
    envelopes = value.get("protocol_envelopes", [])
    if not isinstance(envelopes, list) or any(not isinstance(item, dict) for item in envelopes):
        raise OutputContractError("provider_protocol_envelopes_invalid")
    actions = value.get("action_requests", [])
    if not isinstance(actions, list) or any(not isinstance(item, dict) for item in actions):
        raise OutputContractError("provider_action_requests_invalid")
    for item in actions:
        if set(item) - {"action_id", "kind", "parameters", "requested_by", "reason"}:
            raise OutputContractError("provider_action_request_unknown_fields")
        if not isinstance(item.get("action_id"), str) or not isinstance(item.get("kind"), str):
            raise OutputContractError("provider_action_request_invalid")
        if not isinstance(item.get("parameters"), dict):
            raise OutputContractError("provider_action_request_invalid")
    metadata = value.get("metadata", {})
    if not isinstance(metadata, dict):
        raise OutputContractError("provider_metadata_invalid")
    return {
        "output_type": expected_output_type,
        "payload": value["payload"],
        "context_fields_used": tuple(fields),
        "protocol_envelopes": tuple(envelopes),
        "action_requests": tuple(actions),
        "metadata": metadata,
    }


# ------------------------------- Base class ----------------------------------


class BaseJSONConnector(ProviderAdapter):
    provider_name = "generic"
    default_endpoint = ""

    def __init__(
        self,
        config: ConnectorConfig,
        *,
        transport: HTTPTransport | None = None,
        secret_resolver: SecretResolver | None = None,
    ) -> None:
        self.config = config
        self.resource_id = config.resource_id
        self.supports_idempotency = config.supports_idempotency
        self.transport = transport or UrllibTransport()
        self.secret_resolver = secret_resolver or EnvironmentSecretResolver()
        self.endpoint = config.endpoint or self.default_endpoint
        _validate_endpoint(self.endpoint, allow_insecure_loopback=config.allow_insecure_loopback)

    def invoke(self, request: ProviderRequest) -> ProviderResponse:
        if request.resource_id != self.resource_id:
            raise ProviderConfigurationError("provider_resource_id_mismatch")
        prompt = build_provider_prompt(request)
        if len(prompt.encode("utf-8")) > self.config.max_input_bytes:
            raise ProviderPayloadTooLarge()
        body = self._build_body(request, prompt)
        encoded = canonical_json(body).encode("utf-8")
        headers = {
            "content-type": "application/json",
            "accept": "application/json",
            "user-agent": "w1-cip-reference/0.1",
            **{key.lower(): value for key, value in self.config.extra_headers.items()},
        }
        headers.update(self._authorization_headers(request))
        try:
            response = self.transport.send(
                HTTPRequest(
                    method="POST",
                    url=self._request_url(),
                    headers=headers,
                    body=encoded,
                    timeout_seconds=self.config.timeout_seconds,
                )
            )
        except TransportOutcomeUncertain as exc:
            raise ProviderOutcomeUncertain() from exc
        except TransportConnectionError as exc:
            raise ProviderTransientError("provider_connection_failed") from exc

        parsed_body = _decode_json_body(response.body)
        if not 200 <= response.status < 300:
            _raise_provider_http_error(
                provider=self.provider_name,
                status=response.status,
                headers=response.headers,
                body=parsed_body,
            )

        text = self._extract_text(parsed_body)
        contract = parse_provider_contract(text, request.task.expected_output_type)
        usage = self._extract_usage(parsed_body, prompt=prompt, output_text=text)
        quota = self._extract_quota(response.headers)
        provider_metadata = self._extract_metadata(parsed_body, response.headers)
        merged_metadata = {
            **contract["metadata"],
            "provider": self.provider_name,
            "model": self.config.model,
            "usage": usage.as_dict(),
            "quota_observation": quota.as_dict() if quota else None,
            **provider_metadata,
        }
        merged_metadata = {key: value for key, value in merged_metadata.items() if value is not None}
        units = 1
        if self.config.accounting_meter == "tokens":
            units = usage.total_tokens or (usage.input_tokens or 0) + (usage.output_tokens or 0)
            units = max(1, units)
        return ProviderResponse(
            output_type=contract["output_type"],
            payload=contract["payload"],
            units_consumed=units,
            context_fields_used=contract["context_fields_used"],
            protocol_envelopes=contract["protocol_envelopes"],
            action_requests=contract["action_requests"],
            metadata=merged_metadata,
        )

    def _request_url(self) -> str:
        return self.endpoint

    def _api_key(self) -> str:
        if not self.config.api_key_reference:
            raise ProviderConfigurationError("provider_secret_reference_missing")
        return self.secret_resolver.resolve(self.config.api_key_reference)

    def _authorization_headers(self, request: ProviderRequest) -> dict[str, str]:
        raise NotImplementedError

    def _build_body(self, request: ProviderRequest, prompt: str) -> dict[str, Any]:
        raise NotImplementedError

    def _extract_text(self, body: Mapping[str, Any]) -> str:
        raise NotImplementedError

    def _extract_usage(self, body: Mapping[str, Any], *, prompt: str, output_text: str) -> TokenUsage:
        return TokenUsage(
            input_tokens=estimate_tokens(prompt),
            output_tokens=estimate_tokens(output_text),
            total_tokens=estimate_tokens(prompt) + estimate_tokens(output_text),
            estimated=True,
        )

    def _extract_quota(self, headers: Mapping[str, str]) -> QuotaObservation | None:
        return None

    def _extract_metadata(
        self, body: Mapping[str, Any], headers: Mapping[str, str]
    ) -> dict[str, Any]:
        return {}


# ---------------------------- OpenAI Responses -------------------------------


class OpenAIResponsesConnector(BaseJSONConnector):
    provider_name = "openai"
    default_endpoint = "https://api.openai.com/v1/responses"

    def _authorization_headers(self, request: ProviderRequest) -> dict[str, str]:
        return {
            "authorization": f"Bearer {self._api_key()}",
            # This is a trace identifier, not a claim of inference idempotency.
            "x-client-request-id": request.idempotency_key,
        }

    def _build_body(self, request: ProviderRequest, prompt: str) -> dict[str, Any]:
        return {
            "model": self.config.model,
            "instructions": (
                "You are a W1-CIP task worker. Follow the JSON response contract exactly."
            ),
            "input": prompt,
            "max_output_tokens": self.config.max_output_tokens,
            "store": False,
        }

    def _extract_text(self, body: Mapping[str, Any]) -> str:
        direct = body.get("output_text")
        if isinstance(direct, str) and direct.strip():
            return direct
        texts: list[str] = []
        for item in body.get("output", []) if isinstance(body.get("output"), list) else []:
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            content = item.get("content", [])
            if not isinstance(content, list):
                continue
            for part in content:
                if isinstance(part, dict) and part.get("type") in {"output_text", "text"}:
                    text = part.get("text")
                    if isinstance(text, str):
                        texts.append(text)
        if not texts:
            raise OutputContractError("openai_response_text_missing")
        return "".join(texts)

    def _extract_usage(self, body: Mapping[str, Any], *, prompt: str, output_text: str) -> TokenUsage:
        usage = body.get("usage")
        if not isinstance(usage, dict):
            return super()._extract_usage(body, prompt=prompt, output_text=output_text)
        input_tokens = _as_int(usage.get("input_tokens"))
        output_tokens = _as_int(usage.get("output_tokens"))
        total_tokens = _as_int(usage.get("total_tokens"))
        details = usage.get("input_tokens_details")
        output_details = usage.get("output_tokens_details")
        return TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens or _sum_optional(input_tokens, output_tokens),
            cached_input_tokens=_as_int(details.get("cached_tokens")) if isinstance(details, dict) else None,
            reasoning_tokens=(
                _as_int(output_details.get("reasoning_tokens"))
                if isinstance(output_details, dict)
                else None
            ),
            estimated=False,
        )

    def _extract_quota(self, headers: Mapping[str, str]) -> QuotaObservation | None:
        values = {key.lower(): value for key, value in headers.items()}
        if not any(key.startswith("x-ratelimit-") for key in values):
            return None
        return QuotaObservation(
            provider=self.provider_name,
            observed_at=utc_now(),
            request_limit=_parse_int_header(values.get("x-ratelimit-limit-requests")),
            request_remaining=_parse_int_header(values.get("x-ratelimit-remaining-requests")),
            token_limit=_parse_int_header(values.get("x-ratelimit-limit-tokens")),
            token_remaining=_parse_int_header(values.get("x-ratelimit-remaining-tokens")),
            reset_requests=values.get("x-ratelimit-reset-requests"),
            reset_tokens=values.get("x-ratelimit-reset-tokens"),
        )

    def _extract_metadata(
        self, body: Mapping[str, Any], headers: Mapping[str, str]
    ) -> dict[str, Any]:
        values = {key.lower(): value for key, value in headers.items()}
        return {
            "provider_request_id": values.get("x-request-id"),
            "provider_response_id": body.get("id"),
            "provider_status": body.get("status"),
        }




class XAIResponsesConnector(OpenAIResponsesConnector):
    """xAI Responses API connector using the same request envelope family."""

    provider_name = "xai"
    default_endpoint = "https://api.x.ai/v1/responses"


# ---------------------------- Anthropic Messages -----------------------------


class AnthropicMessagesConnector(BaseJSONConnector):
    provider_name = "anthropic"
    default_endpoint = "https://api.anthropic.com/v1/messages"

    def _authorization_headers(self, request: ProviderRequest) -> dict[str, str]:
        return {
            "x-api-key": self._api_key(),
            "anthropic-version": "2023-06-01",
        }

    def _build_body(self, request: ProviderRequest, prompt: str) -> dict[str, Any]:
        return {
            "model": self.config.model,
            "max_tokens": self.config.max_output_tokens,
            "system": "You are a W1-CIP task worker. Return only the required JSON object.",
            "messages": [{"role": "user", "content": prompt}],
        }

    def _extract_text(self, body: Mapping[str, Any]) -> str:
        texts: list[str] = []
        content = body.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block.get("text")
                    if isinstance(text, str):
                        texts.append(text)
        if not texts:
            raise OutputContractError("anthropic_response_text_missing")
        return "".join(texts)

    def _extract_usage(self, body: Mapping[str, Any], *, prompt: str, output_text: str) -> TokenUsage:
        usage = body.get("usage")
        if not isinstance(usage, dict):
            return super()._extract_usage(body, prompt=prompt, output_text=output_text)
        input_tokens = _as_int(usage.get("input_tokens"))
        output_tokens = _as_int(usage.get("output_tokens"))
        cache_creation = _as_int(usage.get("cache_creation_input_tokens"))
        cache_read = _as_int(usage.get("cache_read_input_tokens"))
        effective_input = _sum_optional(input_tokens, cache_creation, cache_read)
        return TokenUsage(
            input_tokens=effective_input,
            output_tokens=output_tokens,
            total_tokens=_sum_optional(effective_input, output_tokens),
            cached_input_tokens=cache_read,
            estimated=False,
        )

    def _extract_quota(self, headers: Mapping[str, str]) -> QuotaObservation | None:
        values = {key.lower(): value for key, value in headers.items()}
        prefix = "anthropic-ratelimit-"
        if not any(key.startswith(prefix) for key in values):
            return None
        return QuotaObservation(
            provider=self.provider_name,
            observed_at=utc_now(),
            request_limit=_parse_int_header(values.get("anthropic-ratelimit-requests-limit")),
            request_remaining=_parse_int_header(values.get("anthropic-ratelimit-requests-remaining")),
            input_token_limit=_parse_int_header(values.get("anthropic-ratelimit-input-tokens-limit")),
            input_token_remaining=_parse_int_header(
                values.get("anthropic-ratelimit-input-tokens-remaining")
            ),
            output_token_limit=_parse_int_header(values.get("anthropic-ratelimit-output-tokens-limit")),
            output_token_remaining=_parse_int_header(
                values.get("anthropic-ratelimit-output-tokens-remaining")
            ),
            reset_requests=values.get("anthropic-ratelimit-requests-reset"),
            reset_tokens=_first_present(
                values.get("anthropic-ratelimit-input-tokens-reset"),
                values.get("anthropic-ratelimit-output-tokens-reset"),
            ),
        )

    def _extract_metadata(
        self, body: Mapping[str, Any], headers: Mapping[str, str]
    ) -> dict[str, Any]:
        values = {key.lower(): value for key, value in headers.items()}
        return {
            "provider_request_id": values.get("request-id") or values.get("x-request-id"),
            "provider_response_id": body.get("id"),
            "stop_reason": body.get("stop_reason"),
        }


# ----------------------------- Gemini Interactions ---------------------------


class GeminiInteractionsConnector(BaseJSONConnector):
    """Connector for the current Gemini Interactions API."""

    provider_name = "gemini_interactions"
    default_endpoint = "https://generativelanguage.googleapis.com/v1beta/interactions"

    def _authorization_headers(self, request: ProviderRequest) -> dict[str, str]:
        return {"x-goog-api-key": self._api_key()}

    def _build_body(self, request: ProviderRequest, prompt: str) -> dict[str, Any]:
        return {
            "model": self.config.model,
            "input": prompt,
            "system_instruction": (
                "You are a W1-CIP task worker. Return only one JSON object "
                "that follows the supplied response contract."
            ),
            "response_format": {
                "type": "text",
                "mime_type": "application/json",
                "schema": PROVIDER_OUTPUT_SCHEMA,
            },
            "generation_config": {
                "max_output_tokens": self.config.max_output_tokens,
            },
            "stream": False,
            "store": False,
        }

    def _extract_text(self, body: Mapping[str, Any]) -> str:
        status = body.get("status")
        if status in {"failed", "cancelled"}:
            raise ProviderPermanentError(f"gemini_interaction_{status}")
        texts: list[str] = []
        steps = body.get("steps")
        if isinstance(steps, list):
            for step in steps:
                if not isinstance(step, dict) or step.get("type") != "model_output":
                    continue
                content = step.get("content")
                if not isinstance(content, list):
                    continue
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        text = part.get("text")
                        if isinstance(text, str):
                            texts.append(text)
        if not texts:
            raise OutputContractError("gemini_interaction_text_missing")
        return "".join(texts)

    def _extract_usage(self, body: Mapping[str, Any], *, prompt: str, output_text: str) -> TokenUsage:
        usage = body.get("usage")
        if not isinstance(usage, dict):
            return super()._extract_usage(body, prompt=prompt, output_text=output_text)
        input_tokens = _as_int(usage.get("total_input_tokens"))
        output_tokens = _as_int(usage.get("total_output_tokens"))
        thought_tokens = _as_int(usage.get("total_thought_tokens"))
        total_tokens = _as_int(usage.get("total_tokens"))
        return TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens or _sum_optional(input_tokens, output_tokens, thought_tokens),
            cached_input_tokens=_as_int(usage.get("total_cached_tokens")),
            reasoning_tokens=thought_tokens,
            estimated=False,
        )

    def _extract_metadata(
        self, body: Mapping[str, Any], headers: Mapping[str, str]
    ) -> dict[str, Any]:
        return {
            "provider_response_id": body.get("id"),
            "provider_status": body.get("status"),
            "provider_model_version": body.get("model"),
        }


# ---------------------------- Gemini generateContent -------------------------


class GeminiGenerateContentConnector(BaseJSONConnector):
    provider_name = "gemini"
    default_endpoint = "https://generativelanguage.googleapis.com/v1beta/models"

    def _request_url(self) -> str:
        base = self.endpoint.rstrip("/")
        return f"{base}/{urllib.parse.quote(self.config.model, safe='')}:generateContent"

    def _authorization_headers(self, request: ProviderRequest) -> dict[str, str]:
        return {"x-goog-api-key": self._api_key()}

    def _build_body(self, request: ProviderRequest, prompt: str) -> dict[str, Any]:
        return {
            "systemInstruction": {
                "parts": [
                    {
                        "text": (
                            "You are a W1-CIP task worker. Return only one JSON object "
                            "that follows the supplied response contract."
                        )
                    }
                ]
            },
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "maxOutputTokens": self.config.max_output_tokens,
                "responseMimeType": "application/json",
                "responseJsonSchema": PROVIDER_OUTPUT_SCHEMA,
            },
        }

    def _extract_text(self, body: Mapping[str, Any]) -> str:
        prompt_feedback = body.get("promptFeedback")
        if isinstance(prompt_feedback, dict) and prompt_feedback.get("blockReason"):
            raise ProviderSafetyBlocked("gemini_prompt_blocked")
        candidates = body.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            raise OutputContractError("gemini_candidates_missing")
        first = candidates[0]
        if not isinstance(first, dict):
            raise OutputContractError("gemini_candidate_invalid")
        finish_reason = first.get("finishReason")
        if finish_reason in {"SAFETY", "BLOCKLIST", "PROHIBITED_CONTENT", "IMAGE_SAFETY"}:
            raise ProviderSafetyBlocked("gemini_candidate_blocked")
        content = first.get("content")
        if not isinstance(content, dict):
            raise OutputContractError("gemini_content_missing")
        texts: list[str] = []
        parts = content.get("parts")
        if isinstance(parts, list):
            for part in parts:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    texts.append(part["text"])
        if not texts:
            raise OutputContractError("gemini_response_text_missing")
        return "".join(texts)

    def _extract_usage(self, body: Mapping[str, Any], *, prompt: str, output_text: str) -> TokenUsage:
        usage = body.get("usageMetadata")
        if not isinstance(usage, dict):
            return super()._extract_usage(body, prompt=prompt, output_text=output_text)
        input_tokens = _as_int(usage.get("promptTokenCount"))
        output_tokens = _as_int(usage.get("candidatesTokenCount"))
        total_tokens = _as_int(usage.get("totalTokenCount"))
        reasoning_tokens = _as_int(usage.get("thoughtsTokenCount"))
        return TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens or _sum_optional(input_tokens, output_tokens, reasoning_tokens),
            cached_input_tokens=_as_int(usage.get("cachedContentTokenCount")),
            reasoning_tokens=reasoning_tokens,
            estimated=False,
        )

    def _extract_metadata(
        self, body: Mapping[str, Any], headers: Mapping[str, str]
    ) -> dict[str, Any]:
        candidates = body.get("candidates")
        first = candidates[0] if isinstance(candidates, list) and candidates else {}
        return {
            "provider_response_id": body.get("responseId"),
            "provider_model_version": body.get("modelVersion"),
            "finish_reason": first.get("finishReason") if isinstance(first, dict) else None,
        }


class GeminiOAuthGenerateContentConnector(GeminiGenerateContentConnector):
    """Gemini generateContent connector using an OAuth bearer token."""

    provider_name = "gemini_oauth"

    def _authorization_headers(self, request: ProviderRequest) -> dict[str, str]:
        return {"authorization": f"Bearer {self._api_key()}"}


# -------------------------- OpenAI-compatible local ---------------------------


class OpenAICompatibleConnector(BaseJSONConnector):
    """Connector for OpenAI-compatible chat endpoints, including local Ollama."""

    provider_name = "openai_compatible"
    default_endpoint = "http://127.0.0.1:11434/v1/chat/completions"

    def _authorization_headers(self, request: ProviderRequest) -> dict[str, str]:
        if not self.config.api_key_reference:
            return {}
        return {"authorization": f"Bearer {self._api_key()}"}

    def _build_body(self, request: ProviderRequest, prompt: str) -> dict[str, Any]:
        return {
            "model": self.config.model,
            "messages": [
                {
                    "role": "system",
                    "content": "Return only the W1-CIP JSON response object.",
                },
                {"role": "user", "content": prompt},
            ],
            "max_tokens": self.config.max_output_tokens,
            "stream": False,
            "response_format": {"type": "json_object"},
        }

    def _extract_text(self, body: Mapping[str, Any]) -> str:
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            raise OutputContractError("compatible_choices_missing")
        first = choices[0]
        if not isinstance(first, dict):
            raise OutputContractError("compatible_choice_invalid")
        message = first.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("content"), str):
            raise OutputContractError("compatible_response_text_missing")
        return message["content"]

    def _extract_usage(self, body: Mapping[str, Any], *, prompt: str, output_text: str) -> TokenUsage:
        usage = body.get("usage")
        if not isinstance(usage, dict):
            return super()._extract_usage(body, prompt=prompt, output_text=output_text)
        input_tokens = _as_int(usage.get("prompt_tokens"))
        output_tokens = _as_int(usage.get("completion_tokens"))
        total_tokens = _as_int(usage.get("total_tokens"))
        return TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens or _sum_optional(input_tokens, output_tokens),
            estimated=False,
        )

    def _extract_metadata(
        self, body: Mapping[str, Any], headers: Mapping[str, str]
    ) -> dict[str, Any]:
        choices = body.get("choices")
        first = choices[0] if isinstance(choices, list) and choices else {}
        return {
            "provider_response_id": body.get("id"),
            "finish_reason": first.get("finish_reason") if isinstance(first, dict) else None,
        }


# ------------------------------- Registry ------------------------------------


class ProviderRegistry:
    """Validate and construct provider connectors from redaction-safe config."""

    def __init__(
        self,
        *,
        transport: HTTPTransport | None = None,
        secret_resolver: SecretResolver | None = None,
    ) -> None:
        self.transport = transport
        self.secret_resolver = secret_resolver

    def build(self, provider_type: str, config: ConnectorConfig) -> ProviderAdapter:
        mapping = {
            "openai_responses": OpenAIResponsesConnector,
            "xai_responses": XAIResponsesConnector,
            "anthropic_messages": AnthropicMessagesConnector,
            "gemini_interactions": GeminiInteractionsConnector,
            "gemini_generate_content": GeminiGenerateContentConnector,
            "gemini_oauth_generate_content": GeminiOAuthGenerateContentConnector,
            "openai_compatible": OpenAICompatibleConnector,
        }
        connector_type = mapping.get(provider_type)
        if connector_type is None:
            raise ProviderConfigurationError("unknown_provider_connector_type")
        return connector_type(
            config,
            transport=self.transport,
            secret_resolver=self.secret_resolver,
        )


# ------------------------------ Error mapping --------------------------------


def _raise_provider_http_error(
    *,
    provider: str,
    status: int,
    headers: Mapping[str, str],
    body: Mapping[str, Any],
) -> None:
    retry_after = _retry_after_seconds(headers, body)
    fingerprint = _error_fingerprint(body).lower()

    if status in {401, 403}:
        raise ProviderAuthenticationError()
    if status == 402:
        raise ProviderQuotaExhausted(_reset_at_from_retry(retry_after))
    if status == 429:
        hard_quota_terms = {
            "insufficient_quota",
            "billing_hard_limit_reached",
            "credit balance",
            "spend limit",
            "monthly spend",
            "perday",
            "per-day",
            "daily quota",
            "quota_exceeded",
        }
        temporary_terms = {
            "rate_limit",
            "rate limit",
            "perminute",
            "per-minute",
            "requests per minute",
            "tokens per minute",
            "acceleration",
        }
        if any(term in fingerprint for term in hard_quota_terms) and not any(
            term in fingerprint for term in temporary_terms
        ):
            raise ProviderQuotaExhausted(_reset_at_from_retry(retry_after))
        raise ProviderRateLimited(retry_after)
    if status in {408, 409, 425, 500, 502, 503, 504}:
        raise ProviderTransientError(f"provider_http_{status}")
    if status in {400, 404, 405, 413, 415, 422}:
        raise ProviderPermanentError(f"provider_http_{status}")
    raise ProviderPermanentError("provider_http_error")


def _error_fingerprint(body: Mapping[str, Any]) -> str:
    values: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, dict):
            for key, child in value.items():
                if key.lower() in {"key", "token", "authorization", "api_key", "apikey"}:
                    continue
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(body)
    return " ".join(values)[:10_000]


def _retry_after_seconds(headers: Mapping[str, str], body: Mapping[str, Any]) -> float | None:
    values = {key.lower(): value for key, value in headers.items()}
    raw = values.get("retry-after")
    if raw:
        raw = raw.strip()
        try:
            return max(0.0, float(raw))
        except ValueError:
            try:
                parsed = parsedate_to_datetime(raw)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return max(0.0, (parsed - datetime.now(timezone.utc)).total_seconds())
            except (TypeError, ValueError, OverflowError):
                pass
    # Google-style RetryInfo: {"retryDelay": "1.5s"}
    stack: list[Any] = [body]
    while stack:
        value = stack.pop()
        if isinstance(value, dict):
            delay = value.get("retryDelay")
            if isinstance(delay, str) and delay.endswith("s"):
                try:
                    return max(0.0, float(delay[:-1]))
                except ValueError:
                    pass
            stack.extend(value.values())
        elif isinstance(value, list):
            stack.extend(value)
    return None


def _reset_at_from_retry(seconds: float | None) -> str | None:
    if seconds is None:
        return None
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat(
        timespec="seconds"
    ).replace("+00:00", "Z")


# -------------------------------- Helpers ------------------------------------


def _validate_endpoint(endpoint: str, *, allow_insecure_loopback: bool) -> None:
    parsed = urllib.parse.urlparse(endpoint)
    if parsed.scheme == "https" and parsed.netloc:
        return
    if parsed.scheme == "http" and allow_insecure_loopback and _is_loopback(parsed.hostname):
        return
    raise ProviderConfigurationError("provider_endpoint_must_use_https")


def _is_loopback(hostname: str | None) -> bool:
    if not hostname:
        return False
    lowered = hostname.lower().strip("[]")
    return lowered in {"localhost", "127.0.0.1", "::1"}


def _decode_json_body(body: bytes) -> dict[str, Any]:
    try:
        decoded = body.decode("utf-8")
        value = json.loads(decoded) if decoded else {}
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProviderPermanentError("provider_response_not_json") from exc
    if not isinstance(value, dict):
        raise ProviderPermanentError("provider_response_not_object")
    return value


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, str):
        try:
            parsed = int(value)
            return parsed if parsed >= 0 else None
        except ValueError:
            return None
    return None


def _parse_int_header(value: str | None) -> int | None:
    return _as_int(value)


def _sum_optional(*values: int | None) -> int | None:
    present = [value for value in values if value is not None]
    return sum(present) if present else None


def _first_present(*values: str | None) -> str | None:
    return next((value for value in values if value), None)
