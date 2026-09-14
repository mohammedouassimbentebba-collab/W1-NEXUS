"""Live provider certification for W1 Nexus AI Connections.

The certification layer verifies the user's configured provider connection
against the public provider API surface without persisting raw credentials or
raw model output.  Live network calls are always explicit.  Billable probes
(runtime structured-contract invocation, streaming, native tool calling) are
fail-closed unless the caller explicitly acknowledges them.

A certification is evidence, not a permanent claim: provider APIs and model
capabilities change.  Records therefore include the provider contract snapshot,
probe timestamps, endpoint host, model name, response fingerprints, and status.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
import urllib.parse
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from .ai_connections import (
    AIConnectionRecord,
    AIConnectionStore,
    provider_catalog_entry,
)
from .credential_broker import CredentialBroker
from .orchestrator import CompiledTask, ProviderRequest
from .provider_connectors import (
    ConnectorConfig,
    HTTPRequest,
    HTTPResponse,
    HTTPTransport,
    ProviderRegistry,
    StaticSecretResolver,
    UrllibTransport,
)
from .session_store import canonical_json

PROVIDER_CERTIFICATION_VERSION = "1.0"
CERTIFICATION_LEVELS = {"preflight", "runtime", "full"}
CERTIFICATION_STATUSES = {"CERTIFIED", "PARTIAL", "FAILED", "BLOCKED", "NOT_RUN"}


class ProviderCertificationError(RuntimeError):
    code = "provider_certification_error"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.code)


class LiveCertificationRequired(ProviderCertificationError):
    code = "live_certification_requires_explicit_live_flag"


class BillableCertificationApprovalRequired(ProviderCertificationError):
    code = "billable_certification_requires_explicit_acknowledgement"


class CertificationConfigurationError(ProviderCertificationError):
    code = "provider_certification_configuration_invalid"


@dataclass(frozen=True)
class ProviderContractSnapshot:
    provider_id: str
    runtime_surface: str
    preferred_surface: str
    model_list_endpoint: str | None
    auth_contract: str
    streaming_supported: bool
    native_tool_calling_supported: bool
    source_note: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


# Snapshot date: 2026-08-09.  The URLs themselves are not used for execution;
# the execution hosts are taken from the guarded AI Connections catalog.
PROVIDER_CONTRACTS: dict[str, ProviderContractSnapshot] = {
    "openai": ProviderContractSnapshot(
        provider_id="openai",
        runtime_surface="Responses API",
        preferred_surface="Responses API",
        model_list_endpoint="https://api.openai.com/v1/models",
        auth_contract="Authorization: Bearer <API key>",
        streaming_supported=True,
        native_tool_calling_supported=True,
        source_note="OpenAI API reference: Responses + Models",
    ),
    "anthropic": ProviderContractSnapshot(
        provider_id="anthropic",
        runtime_surface="Messages API",
        preferred_surface="Messages API",
        model_list_endpoint="https://api.anthropic.com/v1/models",
        auth_contract="x-api-key + anthropic-version",
        streaming_supported=True,
        native_tool_calling_supported=True,
        source_note="Anthropic Claude API: Messages + Models",
    ),
    "gemini": ProviderContractSnapshot(
        provider_id="gemini",
        runtime_surface="Interactions API for API-key connections; generateContent for OAuth compatibility",
        preferred_surface="Interactions API",
        model_list_endpoint="https://generativelanguage.googleapis.com/v1beta/models",
        auth_contract="x-goog-api-key or OAuth Bearer token",
        streaming_supported=True,
        native_tool_calling_supported=True,
        source_note="Google Gemini API: Interactions / generateContent / Models",
    ),
    "xai": ProviderContractSnapshot(
        provider_id="xai",
        runtime_surface="Responses API",
        preferred_surface="Responses API",
        model_list_endpoint="https://api.x.ai/v1/models",
        auth_contract="Authorization: Bearer <xAI API key>",
        streaming_supported=True,
        native_tool_calling_supported=True,
        source_note="xAI API: Responses preferred; Chat Completions remains compatible",
    ),
    "local-openai-compatible": ProviderContractSnapshot(
        provider_id="local-openai-compatible",
        runtime_surface="OpenAI-compatible Chat Completions",
        preferred_surface="User-managed local API",
        model_list_endpoint=None,
        auth_contract="none or user-owned key",
        streaming_supported=True,
        native_tool_calling_supported=True,
        source_note="Local runtime contract selected by the user",
    ),
    "custom-openai-compatible": ProviderContractSnapshot(
        provider_id="custom-openai-compatible",
        runtime_surface="OpenAI-compatible Chat Completions",
        preferred_surface="User-supplied HTTPS/loopback endpoint",
        model_list_endpoint=None,
        auth_contract="none, API key, or configured OAuth reference",
        streaming_supported=True,
        native_tool_calling_supported=True,
        source_note="Custom provider contract; capability must be measured live",
    ),
}


def provider_certification_catalog() -> list[dict[str, Any]]:
    return [PROVIDER_CONTRACTS[key].as_dict() for key in sorted(PROVIDER_CONTRACTS)]


@dataclass(frozen=True)
class CertificationProbeResult:
    probe: str
    status: str  # PASS | FAIL | SKIP
    live: bool
    billable: bool
    latency_seconds: float
    status_code: int | None = None
    evidence_sha256: str | None = None
    detail_code: str | None = None
    provider_request_id: str | None = None

    def __post_init__(self) -> None:
        if self.status not in {"PASS", "FAIL", "SKIP"}:
            raise CertificationConfigurationError("certification_probe_status_invalid")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProviderCertificationRecord:
    certification_id: str
    connection_id: str
    provider_id: str
    model_name: str | None
    level: str
    status: str
    started_at: str
    completed_at: str
    endpoint_host: str | None
    contract_snapshot: Mapping[str, Any]
    probes: tuple[CertificationProbeResult, ...]
    live_calls: int
    billable_calls: int
    raw_secrets_persisted: bool = False
    raw_provider_output_persisted: bool = False

    def __post_init__(self) -> None:
        if self.level not in CERTIFICATION_LEVELS:
            raise CertificationConfigurationError("certification_level_invalid")
        if self.status not in CERTIFICATION_STATUSES:
            raise CertificationConfigurationError("certification_status_invalid")

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["probes"] = [item.as_dict() for item in self.probes]
        return payload


class ProviderCertificationStore:
    """Immutable certification evidence records.  No raw response bodies."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(str(self.database_path), check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._initialize()

    @contextmanager
    def transaction(self):
        with self._lock:
            try:
                yield self._connection
            except Exception:
                self._connection.rollback()
                raise
            else:
                self._connection.commit()

    def _initialize(self) -> None:
        with self.transaction() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS provider_certifications (
                    certification_id TEXT PRIMARY KEY,
                    connection_id TEXT NOT NULL,
                    provider_id TEXT NOT NULL,
                    model_name TEXT,
                    status TEXT NOT NULL,
                    completed_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS provider_certifications_connection_idx "
                "ON provider_certifications(connection_id, completed_at)"
            )

    def close(self) -> None:
        self._connection.close()

    def put(self, record: ProviderCertificationRecord) -> None:
        payload = canonical_json(record.as_dict())
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO provider_certifications "
                "(certification_id, connection_id, provider_id, model_name, status, completed_at, payload_json, payload_sha256) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record.certification_id,
                    record.connection_id,
                    record.provider_id,
                    record.model_name,
                    record.status,
                    record.completed_at,
                    payload,
                    digest,
                ),
            )

    def list(self, connection_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            if connection_id:
                rows = self._connection.execute(
                    "SELECT payload_json, payload_sha256 FROM provider_certifications "
                    "WHERE connection_id = ? ORDER BY completed_at DESC",
                    (connection_id,),
                ).fetchall()
            else:
                rows = self._connection.execute(
                    "SELECT payload_json, payload_sha256 FROM provider_certifications ORDER BY completed_at DESC"
                ).fetchall()
        values: list[dict[str, Any]] = []
        for row in rows:
            payload = str(row["payload_json"])
            digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
            if digest != row["payload_sha256"]:
                raise ProviderCertificationError("provider_certification_evidence_tampered")
            values.append(json.loads(payload))
        return values


class _ReferenceResolver:
    def __init__(self, reference: str | None, secret: str | None) -> None:
        self.reference = reference
        self.secret = secret

    def resolve(self, reference: str) -> str:
        if not self.reference or reference != self.reference or self.secret is None:
            raise CertificationConfigurationError("certification_secret_reference_mismatch")
        return self.secret


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _safe_host(url: str | None) -> str | None:
    if not url:
        return None
    return urllib.parse.urlparse(url).hostname


def _fingerprint(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _response_request_id(headers: Mapping[str, str]) -> str | None:
    lowered = {str(k).lower(): str(v) for k, v in headers.items()}
    return lowered.get("x-request-id") or lowered.get("request-id") or lowered.get("x-goog-request-id")


def _json_body(response: HTTPResponse) -> dict[str, Any] | list[Any] | None:
    try:
        value = json.loads(response.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, (dict, list)) else None


def _auth_headers(connection: AIConnectionRecord, secret: str | None) -> dict[str, str]:
    if connection.provider_id == "anthropic" and secret:
        return {"x-api-key": secret, "anthropic-version": "2023-06-01"}
    if connection.provider_id == "gemini" and secret:
        if connection.auth_mode == "oauth":
            return {"authorization": f"Bearer {secret}"}
        return {"x-goog-api-key": secret}
    if secret:
        return {"authorization": f"Bearer {secret}"}
    return {}


def _discovery_url(connection: AIConnectionRecord) -> str | None:
    provider = provider_catalog_entry(connection.provider_id)
    if provider.model_list_endpoint and provider.family != "custom":
        return provider.model_list_endpoint
    endpoint = connection.endpoint
    if not endpoint:
        return provider.model_list_endpoint
    parsed = urllib.parse.urlparse(endpoint)
    path = parsed.path.rstrip("/")
    for suffix in ("/chat/completions", "/responses", "/interactions"):
        if path.endswith(suffix):
            path = path[: -len(suffix)] + "/models"
            return urllib.parse.urlunparse(parsed._replace(path=path, query="", params="", fragment=""))
    if path.endswith("/models"):
        return endpoint
    return None


def _extract_model_ids(provider_id: str, payload: Any) -> tuple[str, ...]:
    values: list[str] = []
    if isinstance(payload, dict):
        candidates = payload.get("data") if isinstance(payload.get("data"), list) else payload.get("models")
        if isinstance(candidates, list):
            for item in candidates:
                if not isinstance(item, dict):
                    continue
                raw = item.get("id") or item.get("name")
                if isinstance(raw, str) and raw:
                    if provider_id == "gemini" and raw.startswith("models/"):
                        raw = raw.split("/", 1)[1]
                    values.append(raw)
    return tuple(sorted(set(values)))


def _runtime_connector_type(connection: AIConnectionRecord) -> str:
    provider = provider_catalog_entry(connection.provider_id)
    if connection.provider_id == "gemini" and connection.auth_mode == "oauth":
        return "gemini_oauth_generate_content"
    return provider.connector_type


def _runtime_request(connection: AIConnectionRecord, model: str, *, timeout_seconds: float) -> ProviderRequest:
    return ProviderRequest(
        run_id="provider-certification",
        session_id="provider-certification",
        task=CompiledTask(
            task_id="provider-certification-contract",
            title="Return the minimal W1 provider certification contract",
            phase="certification",
            role="provider",
            expected_output_type="contribution",
            domains=("provider-certification",),
        ),
        resource_id=f"cert-{connection.connection_id}",
        idempotency_key=f"cert-{uuid.uuid4().hex}",
        context={"certification_probe": "w1-runtime-contract"},
        prior_outputs={},
        attempt=1,
    )


def _stream_request(connection: AIConnectionRecord, model: str, secret: str | None, timeout: float) -> HTTPRequest:
    provider = provider_catalog_entry(connection.provider_id)
    headers = {"content-type": "application/json", "accept": "text/event-stream", **_auth_headers(connection, secret)}
    prompt = "Reply with CERT_OK only."
    endpoint = connection.endpoint or provider.default_endpoint
    if connection.provider_id in {"openai", "xai"}:
        body = {"model": model, "input": prompt, "max_output_tokens": 32, "stream": True, "store": False}
    elif connection.provider_id == "anthropic":
        body = {"model": model, "max_tokens": 32, "messages": [{"role": "user", "content": prompt}], "stream": True}
    elif connection.provider_id == "gemini":
        endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{urllib.parse.quote(model, safe='-._')}:streamGenerateContent?alt=sse"
        body = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"maxOutputTokens": 32}}
    else:
        body = {"model": model, "messages": [{"role": "user", "content": prompt}], "max_tokens": 32, "stream": True}
    if not endpoint:
        raise CertificationConfigurationError("certification_endpoint_missing")
    return HTTPRequest("POST", endpoint, headers, canonical_json(body).encode("utf-8"), timeout)


def _tool_request(connection: AIConnectionRecord, model: str, secret: str | None, timeout: float) -> HTTPRequest:
    provider = provider_catalog_entry(connection.provider_id)
    headers = {"content-type": "application/json", "accept": "application/json", **_auth_headers(connection, secret)}
    endpoint = connection.endpoint or provider.default_endpoint
    schema = {
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "required": ["value"],
        "additionalProperties": False,
    }
    prompt = "Call the w1_cert_probe function with value CERT_OK. Do not answer normally."
    if connection.provider_id in {"openai", "xai"}:
        body = {
            "model": model,
            "input": prompt,
            "max_output_tokens": 64,
            "store": False,
            "tools": [{"type": "function", "name": "w1_cert_probe", "description": "W1 certification probe", "parameters": schema, "strict": True}],
            "tool_choice": {"type": "function", "name": "w1_cert_probe"},
        }
    elif connection.provider_id == "anthropic":
        body = {
            "model": model,
            "max_tokens": 64,
            "messages": [{"role": "user", "content": prompt}],
            "tools": [{"name": "w1_cert_probe", "description": "W1 certification probe", "input_schema": schema}],
            "tool_choice": {"type": "tool", "name": "w1_cert_probe"},
        }
    elif connection.provider_id == "gemini":
        endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{urllib.parse.quote(model, safe='-._')}:generateContent"
        gemini_schema = {
            "type": "OBJECT",
            "properties": {"value": {"type": "STRING"}},
            "required": ["value"],
        }
        body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "tools": [{"functionDeclarations": [{"name": "w1_cert_probe", "description": "W1 certification probe", "parameters": gemini_schema}]}],
            "toolConfig": {"functionCallingConfig": {"mode": "ANY", "allowedFunctionNames": ["w1_cert_probe"]}},
            "generationConfig": {"maxOutputTokens": 64},
        }
    else:
        body = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 64,
            "stream": False,
            "tools": [{"type": "function", "function": {"name": "w1_cert_probe", "description": "W1 certification probe", "parameters": schema}}],
            "tool_choice": {"type": "function", "function": {"name": "w1_cert_probe"}},
        }
    if not endpoint:
        raise CertificationConfigurationError("certification_endpoint_missing")
    return HTTPRequest("POST", endpoint, headers, canonical_json(body).encode("utf-8"), timeout)


def _stream_body_valid(body: bytes) -> bool:
    text = body.decode("utf-8", errors="replace")
    return "data:" in text or "event:" in text


def _tool_body_valid(provider_id: str, payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    if provider_id in {"openai", "xai"}:
        output = payload.get("output")
        return isinstance(output, list) and any(
            isinstance(item, dict) and item.get("type") in {"function_call", "tool_call"} and item.get("name") == "w1_cert_probe"
            for item in output
        )
    if provider_id == "anthropic":
        content = payload.get("content")
        return isinstance(content, list) and any(
            isinstance(item, dict) and item.get("type") == "tool_use" and item.get("name") == "w1_cert_probe"
            for item in content
        )
    if provider_id == "gemini":
        candidates = payload.get("candidates")
        if not isinstance(candidates, list):
            return False
        for candidate in candidates:
            content = candidate.get("content") if isinstance(candidate, dict) else None
            parts = content.get("parts") if isinstance(content, dict) else None
            if isinstance(parts, list) and any(
                isinstance(part, dict) and isinstance(part.get("functionCall"), dict) and part["functionCall"].get("name") == "w1_cert_probe"
                for part in parts
            ):
                return True
        return False
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return False
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    calls = message.get("tool_calls") if isinstance(message, dict) else None
    return isinstance(calls, list) and any(
        isinstance(call, dict) and isinstance(call.get("function"), dict) and call["function"].get("name") == "w1_cert_probe"
        for call in calls
    )


class ProviderCertifier:
    def __init__(
        self,
        connection_store: AIConnectionStore,
        certification_store: ProviderCertificationStore,
        *,
        credential_broker: CredentialBroker | None = None,
        transport: HTTPTransport | None = None,
    ) -> None:
        self.connection_store = connection_store
        self.certification_store = certification_store
        self.credential_broker = credential_broker
        self.transport = transport or UrllibTransport()

    def plan(self, connection_id: str, *, model_name: str | None, level: str) -> dict[str, Any]:
        if level not in CERTIFICATION_LEVELS:
            raise CertificationConfigurationError("certification_level_invalid")
        connection = self.connection_store.get_connection(connection_id)
        provider = provider_catalog_entry(connection.provider_id)
        contract = PROVIDER_CONTRACTS.get(connection.provider_id)
        if contract is None:
            raise CertificationConfigurationError("provider_certification_contract_missing")
        billable = 0 if level == "preflight" else 1 if level == "runtime" else 3
        if level != "preflight" and not model_name:
            raise CertificationConfigurationError("certification_model_required")
        return {
            "version": PROVIDER_CERTIFICATION_VERSION,
            "connection": connection.redacted_dict(),
            "provider_contract": contract.as_dict(),
            "level": level,
            "model_name": model_name,
            "planned_probes": (
                ["configuration", "credential_resolution", "model_discovery"]
                + (["w1_runtime_contract"] if level in {"runtime", "full"} else [])
                + (["streaming", "native_tool_calling"] if level == "full" else [])
            ),
            "live_calls_required": True,
            "maximum_billable_calls": billable,
            "billable_acknowledgement_required": billable > 0 and provider.family == "cloud",
            "raw_secrets_persisted": False,
            "raw_provider_output_persisted": False,
        }

    def run(
        self,
        connection_id: str,
        *,
        model_name: str | None,
        level: str,
        live: bool,
        allow_billable: bool = False,
        timeout_seconds: float = 30.0,
    ) -> ProviderCertificationRecord:
        plan = self.plan(connection_id, model_name=model_name, level=level)
        if not live:
            raise LiveCertificationRequired()
        connection = self.connection_store.get_connection(connection_id)
        provider = provider_catalog_entry(connection.provider_id)
        if plan["billable_acknowledgement_required"] and not allow_billable:
            raise BillableCertificationApprovalRequired()
        secret: str | None = None
        if connection.credential_reference:
            if self.credential_broker is None:
                raise CertificationConfigurationError("credential_broker_required")
            secret = self.credential_broker.resolve_reference(connection.credential_reference)
        started = utc_now()
        probes: list[CertificationProbeResult] = []
        live_calls = 0
        billable_calls = 0

        endpoint = connection.endpoint or provider.default_endpoint
        probes.append(CertificationProbeResult(
            probe="configuration",
            status="PASS" if endpoint else "FAIL",
            live=False,
            billable=False,
            latency_seconds=0.0,
            detail_code="endpoint_guarded" if endpoint else "endpoint_missing",
        ))
        credential_ok = connection.auth_mode == "none" or bool(secret)
        probes.append(CertificationProbeResult(
            probe="credential_resolution",
            status="PASS" if credential_ok else "FAIL",
            live=False,
            billable=False,
            latency_seconds=0.0,
            detail_code="credential_reference_resolved" if credential_ok else "credential_missing",
        ))

        discovery = _discovery_url(connection)
        if discovery:
            began = time.monotonic()
            response = self.transport.send(HTTPRequest("GET", discovery, {"accept": "application/json", **_auth_headers(connection, secret)}, None, timeout_seconds))
            live_calls += 1
            payload = _json_body(response)
            models = _extract_model_ids(connection.provider_id, payload)
            matched = True if not model_name else model_name in models
            passed = 200 <= response.status < 300 and bool(models) and matched
            probes.append(CertificationProbeResult(
                probe="model_discovery",
                status="PASS" if passed else "FAIL",
                live=True,
                billable=False,
                latency_seconds=time.monotonic() - began,
                status_code=response.status,
                evidence_sha256=_fingerprint(response.body),
                detail_code=("model_catalog_verified" if passed else "model_catalog_or_selected_model_missing"),
                provider_request_id=_response_request_id(response.headers),
            ))
        else:
            probes.append(CertificationProbeResult(
                probe="model_discovery", status="SKIP", live=False, billable=False,
                latency_seconds=0.0, detail_code="provider_has_no_model_list_contract",
            ))

        if level in {"runtime", "full"}:
            assert model_name is not None
            began = time.monotonic()
            try:
                resolver = _ReferenceResolver(connection.credential_reference, secret)
                registry = ProviderRegistry(transport=self.transport, secret_resolver=resolver)
                config = ConnectorConfig(
                    resource_id=f"cert-{connection.connection_id}",
                    model=model_name,
                    api_key_reference=connection.credential_reference,
                    endpoint=endpoint,
                    timeout_seconds=timeout_seconds,
                    max_output_tokens=256,
                    accounting_meter="tokens",
                    allow_insecure_loopback=provider.family == "local",
                )
                adapter = registry.build(_runtime_connector_type(connection), config)
                response = adapter.invoke(_runtime_request(connection, model_name, timeout_seconds=timeout_seconds))
                evidence = canonical_json({"output_type": response.output_type, "payload_keys": sorted(response.payload), "metadata_keys": sorted(response.metadata)}).encode("utf-8")
                runtime_passed = response.output_type == "contribution"
                detail = "w1_structured_contract_verified" if runtime_passed else "w1_structured_contract_failed"
            except Exception as exc:  # normalized to a stable error type name; never serialize provider body/secret.
                runtime_passed = False
                evidence = exc.__class__.__name__.encode("utf-8")
                detail = f"runtime_error:{exc.__class__.__name__}"
            live_calls += 1
            billable_calls += 1 if provider.family == "cloud" else 0
            probes.append(CertificationProbeResult(
                probe="w1_runtime_contract",
                status="PASS" if runtime_passed else "FAIL",
                live=True,
                billable=provider.family == "cloud",
                latency_seconds=time.monotonic() - began,
                evidence_sha256=_fingerprint(evidence),
                detail_code=detail,
            ))

        if level == "full":
            assert model_name is not None
            for probe_name, request_builder, validator in (
                ("streaming", _stream_request, lambda r: 200 <= r.status < 300 and _stream_body_valid(r.body)),
                ("native_tool_calling", _tool_request, lambda r: 200 <= r.status < 300 and _tool_body_valid(connection.provider_id, _json_body(r))),
            ):
                began = time.monotonic()
                try:
                    request = request_builder(connection, model_name, secret, timeout_seconds)
                    response = self.transport.send(request)
                    passed = bool(validator(response))
                    status_code = response.status
                    digest = _fingerprint(response.body)
                    request_id = _response_request_id(response.headers)
                    detail = f"{probe_name}_verified" if passed else f"{probe_name}_contract_not_observed"
                except Exception as exc:
                    passed = False
                    status_code = None
                    digest = _fingerprint(exc.__class__.__name__.encode("utf-8"))
                    request_id = None
                    detail = f"{probe_name}_error:{exc.__class__.__name__}"
                live_calls += 1
                billable_calls += 1 if provider.family == "cloud" else 0
                probes.append(CertificationProbeResult(
                    probe=probe_name,
                    status="PASS" if passed else "FAIL",
                    live=True,
                    billable=provider.family == "cloud",
                    latency_seconds=time.monotonic() - began,
                    status_code=status_code,
                    evidence_sha256=digest,
                    detail_code=detail,
                    provider_request_id=request_id,
                ))

        failed = [item for item in probes if item.status == "FAIL"]
        passed = [item for item in probes if item.status == "PASS"]
        status = "CERTIFIED" if not failed and passed else "PARTIAL" if passed else "FAILED"
        completed = utc_now()
        contract = PROVIDER_CONTRACTS[connection.provider_id]
        record = ProviderCertificationRecord(
            certification_id=f"cert-{uuid.uuid4().hex}",
            connection_id=connection.connection_id,
            provider_id=connection.provider_id,
            model_name=model_name,
            level=level,
            status=status,
            started_at=started,
            completed_at=completed,
            endpoint_host=_safe_host(endpoint),
            contract_snapshot=contract.as_dict(),
            probes=tuple(probes),
            live_calls=live_calls,
            billable_calls=billable_calls,
        )
        self.certification_store.put(record)
        return record


# ------------------------ Deterministic local benchmark ----------------------


class _QueueTransport:
    def __init__(self, outcomes: Sequence[HTTPResponse]) -> None:
        self.outcomes = list(outcomes)
        self.requests: list[HTTPRequest] = []

    def send(self, request: HTTPRequest) -> HTTPResponse:
        self.requests.append(request)
        if not self.outcomes:
            raise AssertionError("provider_certification_transport_exhausted")
        return self.outcomes.pop(0)


def _json_response(status: int, value: Any, *, headers: Mapping[str, str] | None = None) -> HTTPResponse:
    return HTTPResponse(status, dict(headers or {}), json.dumps(value).encode("utf-8"))


def run_provider_certification_benchmark() -> dict[str, Any]:
    """Deterministic contract/security probes.  Performs zero live provider calls."""
    import tempfile
    from .credential_broker import CredentialBrokerStore, MemoryCredentialVault

    probes: dict[str, bool] = {}
    secret = "cert-secret-never-persist"
    with tempfile.TemporaryDirectory(prefix="w1-provider-cert-") as directory:
        root = Path(directory)
        connection_store = AIConnectionStore(root / "connections.sqlite3")
        credential_store = CredentialBrokerStore(root / "credentials.sqlite3")
        broker = CredentialBroker(credential_store, MemoryCredentialVault())
        reference = broker.store_credential("cert-openai", secret, provider_id="openai")
        connection = AIConnectionRecord(
            connection_id="openai-cert",
            provider_id="openai",
            display_name="OpenAI certification",
            auth_mode="api_key",
            credential_reference=reference,
            endpoint="https://api.openai.com/v1/responses",
            status="connected",
        )
        connection_store.put_connection(connection)
        store = ProviderCertificationStore(root / "certifications.sqlite3")

        models = _json_response(200, {"data": [{"id": "gpt-cert"}]}, headers={"x-request-id": "req-models"})
        runtime_contract = {
            "output_type": "contribution",
            "payload": {"cert": "ok"},
            "context_fields_used": ["certification_probe"],
        }
        runtime = _json_response(200, {
            "id": "resp-cert",
            "status": "completed",
            "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(runtime_contract)}]}],
            "usage": {"input_tokens": 10, "output_tokens": 10, "total_tokens": 20},
        })
        stream = HTTPResponse(200, {"content-type": "text/event-stream"}, b"event: response.output_text.delta\ndata: {\"delta\":\"CERT_OK\"}\n\ndata: [DONE]\n")
        tool = _json_response(200, {"output": [{"type": "function_call", "name": "w1_cert_probe", "arguments": "{\\\"value\\\":\\\"CERT_OK\\\"}"}]})
        transport = _QueueTransport([models, runtime, stream, tool])
        certifier = ProviderCertifier(connection_store, store, credential_broker=broker, transport=transport)
        plan = certifier.plan("openai-cert", model_name="gpt-cert", level="full")
        probes["plan_is_live_and_bounded"] = plan["live_calls_required"] and plan["maximum_billable_calls"] == 3
        try:
            certifier.run("openai-cert", model_name="gpt-cert", level="full", live=False, allow_billable=True)
            probes["live_flag_fail_closed"] = False
        except LiveCertificationRequired:
            probes["live_flag_fail_closed"] = True
        try:
            certifier.run("openai-cert", model_name="gpt-cert", level="full", live=True, allow_billable=False)
            probes["billable_ack_fail_closed"] = False
        except BillableCertificationApprovalRequired:
            probes["billable_ack_fail_closed"] = True
        result = certifier.run("openai-cert", model_name="gpt-cert", level="full", live=True, allow_billable=True)
        probes["full_certification_contract"] = result.status == "CERTIFIED" and len(result.probes) == 6
        probes["live_call_accounting"] = result.live_calls == 4 and result.billable_calls == 3
        probes["secret_not_in_certification_db"] = secret.encode("utf-8") not in (root / "certifications.sqlite3").read_bytes()
        probes["raw_provider_output_not_persisted"] = b"CERT_OK" not in (root / "certifications.sqlite3").read_bytes()
        records = store.list("openai-cert")
        probes["immutable_evidence_roundtrip"] = len(records) == 1 and records[0]["certification_id"] == result.certification_id
        probes["runtime_request_uses_guarded_host"] = urllib.parse.urlparse(transport.requests[1].url).hostname == "api.openai.com"
        probes["auth_header_used_only_in_transport"] = transport.requests[0].headers.get("authorization") == f"Bearer {secret}"
        probes["provider_contract_catalog_present"] = len(provider_certification_catalog()) >= 6
        probes["xai_prefers_responses"] = PROVIDER_CONTRACTS["xai"].preferred_surface == "Responses API"
        probes["gemini_prefers_interactions"] = PROVIDER_CONTRACTS["gemini"].preferred_surface == "Interactions API"
        credential_store.close()
        store.close()

    return {
        "version": PROVIDER_CERTIFICATION_VERSION,
        "passed": all(probes.values()),
        "probes": probes,
        "metrics": {
            "probes": len(probes),
            "live_provider_calls": 0,
            "w1_owned_cloud_calls": 0,
            "provider_contracts": len(PROVIDER_CONTRACTS),
        },
        "boundary": (
            "This benchmark validates the certification harness locally. It does not certify any real provider account/model. "
            "Real certification requires explicit --live and user-owned credentials; billable probes require separate acknowledgement."
        ),
    }
