"""Local-first, provider-neutral multi-model access fabric for W1 Nexus.

The fabric is intentionally independent from W1-owned infrastructure.  Users
can register many models at once, including local endpoints, BYOK cloud
connectors, custom Python plugins, and external applications exposed over a
small JSON protocol.  Portfolios coordinate several preferred models without
reducing their outputs to model-count voting.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import sqlite3
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence

from .orchestrator import (
    CompiledTask,
    ProviderAdapter,
    ProviderPermanentError,
    ProviderRequest,
    ProviderResponse,
)
from .provider_connectors import ConnectorConfig, ProviderRegistry
from .session_store import canonical_json


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class ModelAccessError(RuntimeError):
    code = "model_access_error"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.code)


class ModelProfileNotFound(ModelAccessError):
    code = "model_profile_not_found"


class ModelPortfolioNotFound(ModelAccessError):
    code = "model_portfolio_not_found"


class ModelAccessDenied(ModelAccessError):
    code = "model_access_denied"


class ModelAccessConfigurationError(ModelAccessError):
    code = "model_access_configuration_invalid"


class ModelPortfolioExecutionError(ModelAccessError):
    code = "model_portfolio_execution_failed"


class ExternalApplicationError(ModelAccessError):
    code = "external_application_error"


ACCESS_MODES = {
    "local_endpoint",
    "byok_api",
    "oauth_broker",
    "external_application",
    "custom_plugin",
}
PRIVACY_MODES = {"local", "private_network", "provider_cloud"}
PORTFOLIO_STRATEGIES = {
    "best_fit",
    "fallback_chain",
    "parallel_collect",
    "verified_synthesis",
    "challenge_synthesis",
}
MEMBER_ROLES = {"producer", "specialist", "reviewer", "verifier", "challenger", "synthesizer"}


@dataclass(frozen=True)
class ModelProfile:
    model_id: str
    display_name: str
    provider_id: str
    connector_type: str
    connector_resource_id: str
    model_name: str
    access_mode: str
    privacy_mode: str
    capabilities: Mapping[str, float]
    roles: tuple[str, ...] = ()
    domains: tuple[str, ...] = ()
    modalities: tuple[str, ...] = ("text",)
    credential_reference: str | None = None
    endpoint: str | None = None
    plugin_factory: str | None = None
    external_command: tuple[str, ...] = ()
    enabled: bool = True
    max_parallel: int = 1
    cost_weight: float = 1.0
    latency_weight: float = 1.0
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        identifiers = {
            "model_id": self.model_id,
            "provider_id": self.provider_id,
            "connector_resource_id": self.connector_resource_id,
            "model_name": self.model_name,
        }
        for label, value in identifiers.items():
            if not isinstance(value, str) or not value.strip():
                raise ModelAccessConfigurationError(f"{label}_required")
        if self.access_mode not in ACCESS_MODES:
            raise ModelAccessConfigurationError("unsupported_model_access_mode")
        if self.privacy_mode not in PRIVACY_MODES:
            raise ModelAccessConfigurationError("unsupported_model_privacy_mode")
        if self.max_parallel < 1:
            raise ModelAccessConfigurationError("model_max_parallel_must_be_positive")
        if self.cost_weight <= 0 or self.latency_weight <= 0:
            raise ModelAccessConfigurationError("model_weights_must_be_positive")
        for key, value in self.capabilities.items():
            if not isinstance(key, str) or not key:
                raise ModelAccessConfigurationError("capability_name_invalid")
            if not isinstance(value, (int, float)) or not 0 <= float(value) <= 1:
                raise ModelAccessConfigurationError("capability_score_out_of_range")
        if self.access_mode == "custom_plugin" and not self.plugin_factory:
            raise ModelAccessConfigurationError("plugin_factory_required")
        if self.access_mode == "external_application" and not (self.endpoint or self.external_command):
            raise ModelAccessConfigurationError("external_application_transport_required")

    def redacted_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["credential_reference"] = self.credential_reference
        if not self.credential_reference:
            payload["credential_present"] = True
            payload["credential_source"] = "none_or_local"
        elif self.credential_reference.startswith(("w1-account:", "w1-credential:")):
            payload["credential_present"] = None
            payload["credential_source"] = "w1_credential_broker"
        else:
            payload["credential_present"] = bool(os.environ.get(self.credential_reference))
            payload["credential_source"] = "environment"
        return payload


@dataclass(frozen=True)
class PortfolioMember:
    model_id: str
    role: str = "producer"
    priority: int = 100
    required: bool = False
    capability_overrides: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.model_id:
            raise ModelAccessConfigurationError("portfolio_member_model_id_required")
        if self.role not in MEMBER_ROLES:
            raise ModelAccessConfigurationError("portfolio_member_role_invalid")
        if self.priority < 0:
            raise ModelAccessConfigurationError("portfolio_member_priority_invalid")


@dataclass(frozen=True)
class ModelPortfolio:
    portfolio_id: str
    display_name: str
    strategy: str
    members: tuple[PortfolioMember, ...]
    max_parallel: int = 4
    minimum_successful_producers: int = 1
    require_independent_verifier: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.portfolio_id or not self.display_name:
            raise ModelAccessConfigurationError("portfolio_identity_required")
        if self.strategy not in PORTFOLIO_STRATEGIES:
            raise ModelAccessConfigurationError("portfolio_strategy_invalid")
        if not self.members:
            raise ModelAccessConfigurationError("portfolio_members_required")
        if len({member.model_id for member in self.members}) != len(self.members):
            raise ModelAccessConfigurationError("portfolio_member_duplicate")
        if self.max_parallel < 1 or self.minimum_successful_producers < 1:
            raise ModelAccessConfigurationError("portfolio_limits_invalid")
        if self.strategy == "verified_synthesis":
            verifier_count = sum(member.role == "verifier" for member in self.members)
            if verifier_count < 1:
                raise ModelAccessConfigurationError("verified_synthesis_requires_verifier")
            if self.require_independent_verifier is False:
                raise ModelAccessConfigurationError("verified_synthesis_requires_independence")
        if self.strategy == "challenge_synthesis":
            challenger_count = sum(member.role == "challenger" for member in self.members)
            synthesizer_count = sum(member.role == "synthesizer" for member in self.members)
            producer_count = sum(member.role in {"producer", "specialist"} for member in self.members)
            if challenger_count < 1 or synthesizer_count < 1 or producer_count < 1:
                raise ModelAccessConfigurationError("challenge_synthesis_roles_invalid")


@dataclass(frozen=True)
class ModelCandidate:
    model_id: str
    score: float
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class ModelInvocation:
    model_id: str
    role: str
    status: str
    latency_seconds: float
    response: ProviderResponse | None = None
    error_code: str | None = None
    score: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "role": self.role,
            "status": self.status,
            "latency_seconds": round(self.latency_seconds, 6),
            "error_code": self.error_code,
            "score": self.score,
            "response": asdict(self.response) if self.response else None,
        }


@dataclass(frozen=True)
class PortfolioResult:
    portfolio_id: str
    strategy: str
    started_at: str
    completed_at: str
    invocations: tuple[ModelInvocation, ...]
    verified_response: ProviderResponse | None = None
    selected_model_id: str | None = None
    warnings: tuple[str, ...] = ()

    @property
    def successful(self) -> bool:
        return any(item.status == "completed" for item in self.invocations)

    def as_dict(self) -> dict[str, Any]:
        return {
            "portfolio_id": self.portfolio_id,
            "strategy": self.strategy,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "successful": self.successful,
            "selected_model_id": self.selected_model_id,
            "verified_response": asdict(self.verified_response) if self.verified_response else None,
            "warnings": list(self.warnings),
            "invocations": [item.as_dict() for item in self.invocations],
        }


class AdapterFactory(Protocol):
    def __call__(self, profile: ModelProfile) -> ProviderAdapter:
        ...


class ModelAccessStore:
    """SQLite-backed registry and invocation audit log."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS model_profiles (
                    model_id TEXT PRIMARY KEY,
                    profile_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS model_portfolios (
                    portfolio_id TEXT PRIMARY KEY,
                    portfolio_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS model_invocations (
                    invocation_id TEXT PRIMARY KEY,
                    portfolio_id TEXT,
                    model_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    status TEXT NOT NULL,
                    latency_seconds REAL NOT NULL,
                    error_code TEXT,
                    request_digest TEXT NOT NULL,
                    response_digest TEXT,
                    recorded_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_model_invocations_portfolio
                    ON model_invocations(portfolio_id, recorded_at);
                """
            )

    def put_profile(self, profile: ModelProfile) -> None:
        payload = canonical_json(asdict(profile))
        with self._lock, self._connection() as connection:
            connection.execute(
                "INSERT INTO model_profiles(model_id, profile_json, updated_at) VALUES(?,?,?) "
                "ON CONFLICT(model_id) DO UPDATE SET profile_json=excluded.profile_json, updated_at=excluded.updated_at",
                (profile.model_id, payload, utc_now()),
            )

    def get_profile(self, model_id: str) -> ModelProfile:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT profile_json FROM model_profiles WHERE model_id=?", (model_id,)
            ).fetchone()
        if row is None:
            raise ModelProfileNotFound()
        payload = json.loads(row["profile_json"])
        payload["roles"] = tuple(payload.get("roles", ()))
        payload["domains"] = tuple(payload.get("domains", ()))
        payload["modalities"] = tuple(payload.get("modalities", ()))
        payload["external_command"] = tuple(payload.get("external_command", ()))
        return ModelProfile(**payload)

    def list_profiles(self, *, enabled_only: bool = False) -> list[ModelProfile]:
        with self._connection() as connection:
            rows = connection.execute("SELECT profile_json FROM model_profiles ORDER BY model_id").fetchall()
        profiles = []
        for row in rows:
            payload = json.loads(row["profile_json"])
            payload["roles"] = tuple(payload.get("roles", ()))
            payload["domains"] = tuple(payload.get("domains", ()))
            payload["modalities"] = tuple(payload.get("modalities", ()))
            payload["external_command"] = tuple(payload.get("external_command", ()))
            profile = ModelProfile(**payload)
            if not enabled_only or profile.enabled:
                profiles.append(profile)
        return profiles

    def delete_profile(self, model_id: str) -> bool:
        with self._lock, self._connection() as connection:
            cursor = connection.execute("DELETE FROM model_profiles WHERE model_id=?", (model_id,))
            return cursor.rowcount > 0

    def put_portfolio(self, portfolio: ModelPortfolio) -> None:
        payload = canonical_json(asdict(portfolio))
        with self._lock, self._connection() as connection:
            connection.execute(
                "INSERT INTO model_portfolios(portfolio_id, portfolio_json, updated_at) VALUES(?,?,?) "
                "ON CONFLICT(portfolio_id) DO UPDATE SET portfolio_json=excluded.portfolio_json, updated_at=excluded.updated_at",
                (portfolio.portfolio_id, payload, utc_now()),
            )

    def get_portfolio(self, portfolio_id: str) -> ModelPortfolio:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT portfolio_json FROM model_portfolios WHERE portfolio_id=?", (portfolio_id,)
            ).fetchone()
        if row is None:
            raise ModelPortfolioNotFound()
        payload = json.loads(row["portfolio_json"])
        payload["members"] = tuple(PortfolioMember(**item) for item in payload.get("members", []))
        return ModelPortfolio(**payload)

    def list_portfolios(self) -> list[ModelPortfolio]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT portfolio_json FROM model_portfolios ORDER BY portfolio_id"
            ).fetchall()
        result = []
        for row in rows:
            payload = json.loads(row["portfolio_json"])
            payload["members"] = tuple(PortfolioMember(**item) for item in payload.get("members", []))
            result.append(ModelPortfolio(**payload))
        return result

    def delete_portfolio(self, portfolio_id: str) -> bool:
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                "DELETE FROM model_portfolios WHERE portfolio_id=?", (portfolio_id,)
            )
            return cursor.rowcount > 0

    def record_invocation(
        self,
        *,
        invocation_id: str,
        portfolio_id: str | None,
        model_id: str,
        role: str,
        status: str,
        latency_seconds: float,
        error_code: str | None,
        request: ProviderRequest,
        response: ProviderResponse | None,
    ) -> None:
        request_digest = hashlib.sha256(canonical_json(asdict(request)).encode()).hexdigest()
        response_digest = (
            hashlib.sha256(canonical_json(asdict(response)).encode()).hexdigest()
            if response is not None else None
        )
        with self._lock, self._connection() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO model_invocations(" 
                "invocation_id, portfolio_id, model_id, role, status, latency_seconds, error_code, "
                "request_digest, response_digest, recorded_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    invocation_id,
                    portfolio_id,
                    model_id,
                    role,
                    status,
                    latency_seconds,
                    error_code,
                    request_digest,
                    response_digest,
                    utc_now(),
                ),
            )

    def invocation_summary(self) -> dict[str, Any]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT status, COUNT(*) AS count, AVG(latency_seconds) AS avg_latency "
                "FROM model_invocations GROUP BY status"
            ).fetchall()
            total = connection.execute("SELECT COUNT(*) AS count FROM model_invocations").fetchone()["count"]
        return {
            "total": total,
            "by_status": {
                row["status"]: {
                    "count": row["count"],
                    "average_latency_seconds": row["avg_latency"],
                }
                for row in rows
            },
        }


class PythonPluginAdapterFactory:
    """Build managed Step 39 plugins or legacy ``module:callable`` factories.

    ``w1-plugin:<plugin-id>`` uses the governed workspace PluginManager. The
    legacy import form remains supported for backwards compatibility, but it is
    explicitly not covered by the managed-plugin integrity/permission boundary.
    """

    def __init__(self, plugin_manager: Any | None = None) -> None:
        self.plugin_manager = plugin_manager

    def build(self, profile: ModelProfile) -> ProviderAdapter:
        if not profile.plugin_factory or ":" not in profile.plugin_factory:
            raise ModelAccessConfigurationError("plugin_factory_format_invalid")
        if profile.plugin_factory.startswith("w1-plugin:"):
            plugin_id = profile.plugin_factory.split(":", 1)[1]
            if not plugin_id or self.plugin_manager is None:
                raise ModelAccessConfigurationError("managed_plugin_manager_required")
            return self.plugin_manager.provider_adapter(plugin_id, profile.connector_resource_id)
        module_name, callable_name = profile.plugin_factory.split(":", 1)
        module = importlib.import_module(module_name)
        factory = getattr(module, callable_name, None)
        if not callable(factory):
            raise ModelAccessConfigurationError("plugin_factory_not_callable")
        adapter = factory(profile)
        if not hasattr(adapter, "invoke") or not hasattr(adapter, "resource_id"):
            raise ModelAccessConfigurationError("plugin_adapter_contract_invalid")
        return adapter


class ExternalApplicationConnector:
    """Connect to a user-managed external application over loopback HTTP or stdio.

    The external application receives the normalized ProviderRequest and must
    return a normalized ProviderResponse object.  No browser/session scraping is
    performed; authentication remains owned by the external application.
    """

    supports_idempotency = True

    def __init__(self, profile: ModelProfile, *, timeout_seconds: float = 120.0) -> None:
        self.profile = profile
        self.resource_id = profile.connector_resource_id
        self.timeout_seconds = timeout_seconds

    def invoke(self, request: ProviderRequest) -> ProviderResponse:
        payload = canonical_json({"request": asdict(request)}).encode("utf-8")
        if self.profile.external_command:
            return self._invoke_stdio(payload)
        if self.profile.endpoint:
            return self._invoke_http(payload)
        raise ExternalApplicationError("external_application_transport_missing")

    def _invoke_stdio(self, payload: bytes) -> ProviderResponse:
        try:
            completed = subprocess.run(
                list(self.profile.external_command),
                input=payload + b"\n",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.timeout_seconds,
                check=False,
                env={key: value for key, value in os.environ.items() if not _looks_secret(key)},
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ExternalApplicationError("external_stdio_failed") from exc
        if completed.returncode != 0:
            raise ExternalApplicationError("external_stdio_nonzero_exit")
        try:
            response = json.loads(completed.stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ExternalApplicationError("external_stdio_invalid_json") from exc
        return _provider_response_from_mapping(response)

    def _invoke_http(self, payload: bytes) -> ProviderResponse:
        assert self.profile.endpoint is not None
        parsed = urllib.parse.urlparse(self.profile.endpoint)
        if parsed.scheme not in {"http", "https"}:
            raise ExternalApplicationError("external_http_scheme_invalid")
        if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ExternalApplicationError("external_http_must_be_loopback")
        headers = {"Content-Type": "application/json"}
        if self.profile.credential_reference:
            token = os.environ.get(self.profile.credential_reference)
            if not token:
                raise ExternalApplicationError("external_application_credential_missing")
            headers["Authorization"] = f"Bearer {token}"
        raw = urllib.request.Request(self.profile.endpoint, data=payload, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(raw, timeout=self.timeout_seconds) as response:  # noqa: S310
                body = response.read()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ExternalApplicationError("external_http_failed") from exc
        try:
            return _provider_response_from_mapping(json.loads(body.decode("utf-8")))
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise ExternalApplicationError("external_http_invalid_response") from exc


def _looks_secret(name: str) -> bool:
    lowered = name.lower()
    return any(token in lowered for token in ("token", "secret", "password", "api_key", "apikey"))


def _provider_response_from_mapping(payload: Mapping[str, Any]) -> ProviderResponse:
    if not isinstance(payload, Mapping):
        raise ExternalApplicationError("external_response_object_required")
    output_type = payload.get("output_type")
    body = payload.get("payload")
    if not isinstance(output_type, str) or not isinstance(body, Mapping):
        raise ExternalApplicationError("external_response_contract_invalid")
    return ProviderResponse(
        output_type=output_type,
        payload=deepcopy(dict(body)),
        units_consumed=int(payload.get("units_consumed", 1)),
        context_fields_used=tuple(payload.get("context_fields_used", ())),
        protocol_envelopes=tuple(payload.get("protocol_envelopes", ())),
        action_requests=tuple(payload.get("action_requests", ())),
        metadata=deepcopy(dict(payload.get("metadata", {}))),
    )


class ModelAccessFabric:
    """Build adapters, rank preferred models, and execute multi-model portfolios."""

    def __init__(
        self,
        store: ModelAccessStore,
        *,
        provider_registry: ProviderRegistry | None = None,
        plugin_factory: PythonPluginAdapterFactory | None = None,
        adapter_overrides: Mapping[str, ProviderAdapter] | None = None,
    ) -> None:
        self.store = store
        self.provider_registry = provider_registry or ProviderRegistry()
        self.plugin_factory = plugin_factory or PythonPluginAdapterFactory()
        self.adapter_overrides = dict(adapter_overrides or {})
        self._adapter_cache: dict[str, ProviderAdapter] = {}
        self._adapter_lock = threading.RLock()

    def adapter_for(self, profile: ModelProfile) -> ProviderAdapter:
        override = self.adapter_overrides.get(profile.model_id)
        if override is not None:
            return override
        with self._adapter_lock:
            cached = self._adapter_cache.get(profile.model_id)
            if cached is not None:
                return cached
            if profile.access_mode == "custom_plugin":
                adapter = self.plugin_factory.build(profile)
            elif profile.access_mode == "external_application":
                adapter = ExternalApplicationConnector(profile)
            else:
                config = ConnectorConfig(
                    resource_id=profile.connector_resource_id,
                    model=profile.model_name,
                    api_key_reference=profile.credential_reference,
                    endpoint=profile.endpoint,
                    allow_insecure_loopback=profile.access_mode == "local_endpoint",
                    accounting_meter=str(profile.metadata.get("accounting_meter", "tokens")),
                    supports_idempotency=bool(profile.metadata.get("supports_idempotency", False)),
                    max_output_tokens=int(profile.metadata.get("max_output_tokens", 2048)),
                    timeout_seconds=float(profile.metadata.get("timeout_seconds", 90.0)),
                )
                adapter = self.provider_registry.build(profile.connector_type, config)
            self._adapter_cache[profile.model_id] = adapter
            return adapter

    def rank(self, task: CompiledTask, profiles: Iterable[ModelProfile]) -> tuple[ModelCandidate, ...]:
        candidates: list[ModelCandidate] = []
        for profile in profiles:
            if not profile.enabled:
                continue
            score = 0.0
            reasons: list[str] = []
            required = set(task.domains) | {task.role, task.phase}
            for capability in required:
                value = float(profile.capabilities.get(capability, 0.0))
                score += value
                reasons.append(f"{capability}={value:.2f}")
            if task.role in profile.roles:
                score += 0.5
                reasons.append("declared-role")
            domain_overlap = set(task.domains) & set(profile.domains)
            score += 0.25 * len(domain_overlap)
            if domain_overlap:
                reasons.append("domain-overlap")
            if profile.privacy_mode == "local":
                score += 0.1
                reasons.append("local-privacy")
            score -= 0.05 * profile.cost_weight
            score -= 0.02 * profile.latency_weight
            candidates.append(ModelCandidate(profile.model_id, round(score, 6), tuple(reasons)))
        return tuple(sorted(candidates, key=lambda item: (-item.score, item.model_id)))

    def invoke_model(self, profile: ModelProfile, request: ProviderRequest, *, role: str = "producer", portfolio_id: str | None = None, score: float | None = None) -> ModelInvocation:
        adapter = self.adapter_for(profile)
        started = time.perf_counter()
        response: ProviderResponse | None = None
        status = "failed"
        error_code: str | None = None
        try:
            response = adapter.invoke(request)
            status = "completed"
        except BaseException as exc:  # preserved as structured audit result
            error_code = str(getattr(exc, "code", exc.__class__.__name__))
        latency = time.perf_counter() - started
        invocation = ModelInvocation(
            model_id=profile.model_id,
            role=role,
            status=status,
            latency_seconds=latency,
            response=response,
            error_code=error_code,
            score=score,
        )
        invocation_id = hashlib.sha256(
            f"{request.idempotency_key}:{profile.model_id}:{role}".encode()
        ).hexdigest()
        self.store.record_invocation(
            invocation_id=invocation_id,
            portfolio_id=portfolio_id,
            model_id=profile.model_id,
            role=role,
            status=status,
            latency_seconds=latency,
            error_code=error_code,
            request=request,
            response=response,
        )
        return invocation

    def invoke_portfolio(self, portfolio_id: str, request: ProviderRequest) -> PortfolioResult:
        portfolio = self.store.get_portfolio(portfolio_id)
        profiles = {profile.model_id: profile for profile in self.store.list_profiles(enabled_only=True)}
        missing = [member.model_id for member in portfolio.members if member.model_id not in profiles]
        if missing:
            raise ModelAccessConfigurationError(f"portfolio_models_missing:{','.join(sorted(missing))}")
        started_at = utc_now()
        ranked = {item.model_id: item for item in self.rank(request.task, profiles.values())}
        if portfolio.strategy == "best_fit":
            result = self._invoke_best_fit(portfolio, request, profiles, ranked)
        elif portfolio.strategy == "fallback_chain":
            result = self._invoke_fallback(portfolio, request, profiles, ranked)
        elif portfolio.strategy == "parallel_collect":
            result = self._invoke_parallel(portfolio, request, profiles, ranked)
        elif portfolio.strategy == "verified_synthesis":
            result = self._invoke_verified(portfolio, request, profiles, ranked)
        else:
            result = self._invoke_challenge(portfolio, request, profiles, ranked)
        return PortfolioResult(
            portfolio_id=portfolio.portfolio_id,
            strategy=portfolio.strategy,
            started_at=started_at,
            completed_at=utc_now(),
            invocations=result[0],
            verified_response=result[1],
            selected_model_id=result[2],
            warnings=result[3],
        )

    def _member_order(self, portfolio: ModelPortfolio, ranked: Mapping[str, ModelCandidate]) -> list[PortfolioMember]:
        return sorted(
            portfolio.members,
            key=lambda member: (
                member.priority,
                -ranked.get(member.model_id, ModelCandidate(member.model_id, -999, ())).score,
                member.model_id,
            ),
        )

    def _invoke_best_fit(self, portfolio: ModelPortfolio, request: ProviderRequest, profiles: Mapping[str, ModelProfile], ranked: Mapping[str, ModelCandidate]) -> tuple[tuple[ModelInvocation, ...], ProviderResponse | None, str | None, tuple[str, ...]]:
        eligible = [member for member in portfolio.members if member.role in {"producer", "specialist"}]
        if not eligible:
            eligible = list(portfolio.members)
        selected = max(eligible, key=lambda member: ranked.get(member.model_id, ModelCandidate(member.model_id, -999, ())).score)
        score = ranked.get(selected.model_id)
        invocation = self.invoke_model(
            profiles[selected.model_id], request, role=selected.role,
            portfolio_id=portfolio.portfolio_id, score=score.score if score else None,
        )
        if invocation.status != "completed":
            raise ModelPortfolioExecutionError(invocation.error_code)
        return (invocation,), invocation.response, selected.model_id, ()

    def _invoke_fallback(self, portfolio: ModelPortfolio, request: ProviderRequest, profiles: Mapping[str, ModelProfile], ranked: Mapping[str, ModelCandidate]) -> tuple[tuple[ModelInvocation, ...], ProviderResponse | None, str | None, tuple[str, ...]]:
        invocations: list[ModelInvocation] = []
        for member in self._member_order(portfolio, ranked):
            candidate = ranked.get(member.model_id)
            invocation = self.invoke_model(
                profiles[member.model_id], request, role=member.role,
                portfolio_id=portfolio.portfolio_id, score=candidate.score if candidate else None,
            )
            invocations.append(invocation)
            if invocation.status == "completed":
                warnings = () if len(invocations) == 1 else ("fallback_model_used",)
                return tuple(invocations), invocation.response, member.model_id, warnings
        raise ModelPortfolioExecutionError("all_portfolio_models_failed")

    def _invoke_parallel(self, portfolio: ModelPortfolio, request: ProviderRequest, profiles: Mapping[str, ModelProfile], ranked: Mapping[str, ModelCandidate]) -> tuple[tuple[ModelInvocation, ...], ProviderResponse | None, str | None, tuple[str, ...]]:
        members = [member for member in self._member_order(portfolio, ranked) if member.role in {"producer", "specialist", "reviewer"}]
        if not members:
            raise ModelPortfolioExecutionError("portfolio_has_no_parallel_members")
        invocations: list[ModelInvocation] = []

        adaptive_counts = portfolio.metadata.get("adaptive_primary_counts", {}) if isinstance(portfolio.metadata, Mapping) else {}
        adaptive = bool(portfolio.metadata.get("adaptive_capacity_router")) if isinstance(portfolio.metadata, Mapping) else False
        primary_count = int(adaptive_counts.get("producer", len(members))) if adaptive and isinstance(adaptive_counts, Mapping) else len(members)
        primary_count = max(1, min(primary_count, len(members)))
        primary_members = members[:primary_count]
        standby_members = members[primary_count:] if adaptive else []

        with ThreadPoolExecutor(max_workers=min(portfolio.max_parallel, len(primary_members))) as executor:
            futures = {
                executor.submit(
                    self.invoke_model,
                    profiles[member.model_id],
                    request,
                    role=member.role,
                    portfolio_id=portfolio.portfolio_id,
                    score=ranked.get(member.model_id).score if member.model_id in ranked else None,
                ): member
                for member in primary_members
            }
            for future in as_completed(futures):
                invocations.append(future.result())

        successful = [item for item in invocations if item.status == "completed"]
        fallback_used = False
        # Adaptive portfolios keep standby producers cold. They are invoked only
        # when a primary fails (including quota/provider failures), preserving
        # collaboration without spending tokens on every fallback upfront.
        for member in standby_members:
            if len(successful) >= primary_count:
                break
            candidate = ranked.get(member.model_id)
            fallback_request = ProviderRequest(
                run_id=request.run_id,
                session_id=request.session_id,
                task=request.task,
                resource_id=profiles[member.model_id].connector_resource_id,
                idempotency_key=f"{request.idempotency_key}:producer-fallback:{member.model_id}",
                context=request.context,
                prior_outputs=request.prior_outputs,
                attempt=request.attempt,
            )
            invocation = self.invoke_model(
                profiles[member.model_id], fallback_request, role=member.role,
                portfolio_id=portfolio.portfolio_id, score=candidate.score if candidate else None,
            )
            invocations.append(invocation)
            fallback_used = True
            if invocation.status == "completed":
                successful.append(invocation)

        invocations.sort(key=lambda item: item.model_id)
        if len(successful) < portfolio.minimum_successful_producers:
            raise ModelPortfolioExecutionError("portfolio_minimum_success_not_met")
        warnings = ["parallel_outputs_require_governed_review"]
        if fallback_used:
            warnings.append("producer_fallback_used")
        if len(successful) < primary_count:
            warnings.append("producer_target_count_degraded")
        # No model-count vote and no hidden winner. Consumers receive all outputs.
        return tuple(invocations), None, None, tuple(warnings)

    def _invoke_verified(self, portfolio: ModelPortfolio, request: ProviderRequest, profiles: Mapping[str, ModelProfile], ranked: Mapping[str, ModelCandidate]) -> tuple[tuple[ModelInvocation, ...], ProviderResponse | None, str | None, tuple[str, ...]]:
        verifier_members = [member for member in self._member_order(portfolio, ranked) if member.role == "verifier"]
        producer_members = [member for member in portfolio.members if member.role in {"producer", "specialist"}]
        producer_ids = {member.model_id for member in producer_members}
        if any(member.model_id in producer_ids for member in verifier_members):
            raise ModelAccessConfigurationError("verifier_must_be_independent_model")
        producer_portfolio = ModelPortfolio(
            portfolio_id=portfolio.portfolio_id,
            display_name=portfolio.display_name,
            strategy="parallel_collect",
            members=tuple(producer_members),
            max_parallel=portfolio.max_parallel,
            minimum_successful_producers=portfolio.minimum_successful_producers,
            metadata=portfolio.metadata,
        )
        producer_invocations, _, _, producer_warnings = self._invoke_parallel(
            producer_portfolio, request, profiles, ranked
        )
        successful = [item for item in producer_invocations if item.response is not None]
        prior_outputs = {
            item.model_id: {
                "output_type": item.response.output_type,
                "payload": dict(item.response.payload),
                "metadata": dict(item.response.metadata),
            }
            for item in successful
            if item.response is not None
        }
        verifier_invocations: list[ModelInvocation] = []
        for verifier_member in verifier_members:
            verifier_request = ProviderRequest(
                run_id=request.run_id,
                session_id=request.session_id,
                task=request.task,
                resource_id=profiles[verifier_member.model_id].connector_resource_id,
                idempotency_key=f"{request.idempotency_key}:verify:{verifier_member.model_id}",
                context=request.context,
                prior_outputs=prior_outputs,
                attempt=request.attempt,
            )
            candidate = ranked.get(verifier_member.model_id)
            verifier = self.invoke_model(
                profiles[verifier_member.model_id], verifier_request,
                role="verifier", portfolio_id=portfolio.portfolio_id,
                score=candidate.score if candidate else None,
            )
            verifier_invocations.append(verifier)
            if verifier.status == "completed" and verifier.response is not None:
                warnings = list(producer_warnings)
                if len(verifier_invocations) > 1:
                    warnings.append("verifier_fallback_used")
                return tuple(list(producer_invocations) + verifier_invocations), verifier.response, verifier_member.model_id, tuple(warnings)
        raise ModelPortfolioExecutionError("portfolio_verifier_failed")


    def _invoke_challenge(self, portfolio: ModelPortfolio, request: ProviderRequest, profiles: Mapping[str, ModelProfile], ranked: Mapping[str, ModelCandidate]) -> tuple[tuple[ModelInvocation, ...], ProviderResponse | None, str | None, tuple[str, ...]]:
        """Producer(s) -> challenger fallback chain -> synthesizer fallback chain.

        The challenger sees producer outputs and the synthesizer sees producer
        outputs plus the successful challenge.  Multiple challenger/synthesizer
        members are treated as role-level failover candidates, preserving the
        collaboration topology when quota or provider failures occur.
        """
        ordered = self._member_order(portfolio, ranked)
        challenger_members = [member for member in ordered if member.role == "challenger"]
        synthesizer_members = [member for member in ordered if member.role == "synthesizer"]
        producer_members = [member for member in portfolio.members if member.role in {"producer", "specialist"}]
        producer_portfolio = ModelPortfolio(
            portfolio_id=portfolio.portfolio_id,
            display_name=portfolio.display_name,
            strategy="parallel_collect",
            members=tuple(producer_members),
            max_parallel=portfolio.max_parallel,
            minimum_successful_producers=portfolio.minimum_successful_producers,
            metadata=portfolio.metadata,
        )
        producer_invocations, _, _, producer_warnings = self._invoke_parallel(producer_portfolio, request, profiles, ranked)
        producer_outputs = {
            item.model_id: {
                "output_type": item.response.output_type,
                "payload": dict(item.response.payload),
                "metadata": dict(item.response.metadata),
            }
            for item in producer_invocations
            if item.response is not None
        }
        challenger_invocations: list[ModelInvocation] = []
        challenger: ModelInvocation | None = None
        for challenger_member in challenger_members:
            challenger_request = ProviderRequest(
                run_id=request.run_id,
                session_id=request.session_id,
                task=request.task,
                resource_id=profiles[challenger_member.model_id].connector_resource_id,
                idempotency_key=f"{request.idempotency_key}:challenge:{challenger_member.model_id}",
                context=request.context,
                prior_outputs=producer_outputs,
                attempt=request.attempt,
            )
            challenger_candidate = ranked.get(challenger_member.model_id)
            invocation = self.invoke_model(
                profiles[challenger_member.model_id], challenger_request, role="challenger",
                portfolio_id=portfolio.portfolio_id,
                score=challenger_candidate.score if challenger_candidate else None,
            )
            challenger_invocations.append(invocation)
            if invocation.status == "completed" and invocation.response is not None:
                challenger = invocation
                break
        if challenger is None or challenger.response is None:
            raise ModelPortfolioExecutionError("portfolio_challenger_failed")
        synthesis_inputs = dict(producer_outputs)
        synthesis_inputs["_w1_challenge"] = {
            "model_id": challenger.model_id,
            "output_type": challenger.response.output_type,
            "payload": dict(challenger.response.payload),
            "metadata": dict(challenger.response.metadata),
        }
        synthesizer_invocations: list[ModelInvocation] = []
        synthesizer: ModelInvocation | None = None
        for synthesizer_member in synthesizer_members:
            synth_request = ProviderRequest(
                run_id=request.run_id,
                session_id=request.session_id,
                task=request.task,
                resource_id=profiles[synthesizer_member.model_id].connector_resource_id,
                idempotency_key=f"{request.idempotency_key}:synthesize:{synthesizer_member.model_id}",
                context=request.context,
                prior_outputs=synthesis_inputs,
                attempt=request.attempt,
            )
            synth_candidate = ranked.get(synthesizer_member.model_id)
            invocation = self.invoke_model(
                profiles[synthesizer_member.model_id], synth_request, role="synthesizer",
                portfolio_id=portfolio.portfolio_id, score=synth_candidate.score if synth_candidate else None,
            )
            synthesizer_invocations.append(invocation)
            if invocation.status == "completed" and invocation.response is not None:
                synthesizer = invocation
                break
        if synthesizer is None or synthesizer.response is None:
            raise ModelPortfolioExecutionError("portfolio_synthesizer_failed")
        warnings = [*producer_warnings, "challenge_is_review_not_formal_verification"]
        if len(challenger_invocations) > 1:
            warnings.append("challenger_fallback_used")
        if len(synthesizer_invocations) > 1:
            warnings.append("synthesizer_fallback_used")
        invocations = tuple(list(producer_invocations) + challenger_invocations + synthesizer_invocations)
        return invocations, synthesizer.response, synthesizer.model_id, tuple(warnings)


class EmbeddedW1Runtime:
    """Small SDK surface for embedding W1 model access into another application."""

    def __init__(self, fabric: ModelAccessFabric) -> None:
        self.fabric = fabric

    def list_models(self) -> list[dict[str, Any]]:
        return [profile.redacted_dict() for profile in self.fabric.store.list_profiles()]

    def list_portfolios(self) -> list[dict[str, Any]]:
        return [asdict(item) for item in self.fabric.store.list_portfolios()]

    def invoke_model(self, model_id: str, request: ProviderRequest) -> dict[str, Any]:
        profile = self.fabric.store.get_profile(model_id)
        return self.fabric.invoke_model(profile, request).as_dict()

    def invoke_portfolio(self, portfolio_id: str, request: ProviderRequest) -> dict[str, Any]:
        return self.fabric.invoke_portfolio(portfolio_id, request).as_dict()


@dataclass(frozen=True)
class LocalControlSettings:
    host: str = "127.0.0.1"
    port: int = 8871
    token: str = ""

    def __post_init__(self) -> None:
        if self.host not in {"127.0.0.1", "localhost", "::1"}:
            raise ModelAccessConfigurationError("control_api_loopback_only")
        if not self.token:
            raise ModelAccessConfigurationError("control_api_token_required")


class _ControlHandler(BaseHTTPRequestHandler):
    server: "LocalControlServer"  # type: ignore[assignment]

    def log_message(self, format: str, *args: Any) -> None:  # pragma: no cover - silence default logs
        return

    def _authorized(self) -> bool:
        return self.headers.get("Authorization") == f"Bearer {self.server.settings.token}"

    def _json(self, status: int, payload: Any) -> None:
        body = canonical_json(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/v1/health":
            self._json(HTTPStatus.OK, {"ok": True, "service": "w1-local-control"})
            return
        if not self._authorized():
            self._json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error_code": "unauthorized"})
            return
        if self.path == "/v1/models":
            self._json(HTTPStatus.OK, {"models": self.server.runtime.list_models()})
            return
        if self.path == "/v1/portfolios":
            self._json(HTTPStatus.OK, {"portfolios": self.server.runtime.list_portfolios()})
            return
        self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error_code": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        if not self._authorized():
            self._json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error_code": "unauthorized"})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size <= 0 or size > 5_000_000:
                raise ValueError("invalid_body_size")
            payload = json.loads(self.rfile.read(size).decode("utf-8"))
            request = provider_request_from_mapping(payload["request"])
            if self.path == "/v1/invoke/model":
                result = self.server.runtime.invoke_model(str(payload["model_id"]), request)
            elif self.path == "/v1/invoke/portfolio":
                result = self.server.runtime.invoke_portfolio(str(payload["portfolio_id"]), request)
            else:
                self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error_code": "not_found"})
                return
            self._json(HTTPStatus.OK, {"ok": True, "result": result})
        except BaseException as exc:
            self._json(
                HTTPStatus.BAD_REQUEST,
                {"ok": False, "error_code": str(getattr(exc, "code", exc.__class__.__name__))},
            )


class LocalControlServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, settings: LocalControlSettings, runtime: EmbeddedW1Runtime) -> None:
        self.settings = settings
        self.runtime = runtime
        self._serve_done = threading.Event()
        super().__init__((settings.host, settings.port), _ControlHandler)

    def serve_forever(self, poll_interval: float = 0.5) -> None:
        try:
            super().serve_forever(poll_interval)
        finally:
            self._serve_done.set()

    def close_server(self, *, join_timeout: float = 2.0) -> None:
        try:
            self.shutdown()
            self._serve_done.wait(timeout=join_timeout)
        finally:
            self.server_close()


class W1LocalClient:
    """Dependency-light client for applications embedding the local W1 service."""

    def __init__(self, base_url: str, token: str, *, timeout_seconds: float = 120.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout_seconds = timeout_seconds

    def _request(self, method: str, path: str, payload: Any | None = None) -> Any:
        body = canonical_json(payload).encode("utf-8") if payload is not None else None
        raw = urllib.request.Request(
            self.base_url + path,
            data=body,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(raw, timeout=self.timeout_seconds) as response:  # noqa: S310
            return json.loads(response.read().decode("utf-8"))

    def list_models(self) -> list[dict[str, Any]]:
        return self._request("GET", "/v1/models")["models"]

    def invoke_portfolio(self, portfolio_id: str, request: ProviderRequest) -> dict[str, Any]:
        return self._request(
            "POST", "/v1/invoke/portfolio",
            {"portfolio_id": portfolio_id, "request": asdict(request)},
        )["result"]


def provider_request_from_mapping(payload: Mapping[str, Any]) -> ProviderRequest:
    task_payload = payload.get("task")
    if not isinstance(task_payload, Mapping):
        raise ModelAccessConfigurationError("provider_request_task_required")
    task = CompiledTask(
        task_id=str(task_payload["task_id"]),
        title=str(task_payload["title"]),
        phase=str(task_payload["phase"]),
        role=str(task_payload["role"]),
        expected_output_type=str(task_payload["expected_output_type"]),
        depends_on=tuple(task_payload.get("depends_on", ())),
        deliverable_id=task_payload.get("deliverable_id"),
        criterion_id=task_payload.get("criterion_id"),
        required_context_fields=tuple(task_payload.get("required_context_fields", ())),
        estimated_units=int(task_payload.get("estimated_units", 1)),
        domains=tuple(task_payload.get("domains", ())),
    )
    return ProviderRequest(
        run_id=str(payload["run_id"]),
        session_id=str(payload["session_id"]),
        task=task,
        resource_id=str(payload["resource_id"]),
        idempotency_key=str(payload["idempotency_key"]),
        context=deepcopy(dict(payload.get("context", {}))),
        prior_outputs=deepcopy(dict(payload.get("prior_outputs", {}))),
        attempt=int(payload.get("attempt", 1)),
    )


def profile_from_mapping(payload: Mapping[str, Any]) -> ModelProfile:
    data = dict(payload)
    for key in ("roles", "domains", "modalities", "external_command"):
        data[key] = tuple(data.get(key, ()))
    return ModelProfile(**data)


def portfolio_from_mapping(payload: Mapping[str, Any]) -> ModelPortfolio:
    data = dict(payload)
    data["members"] = tuple(PortfolioMember(**item) for item in data.get("members", ()))
    return ModelPortfolio(**data)


class PortfolioProviderAdapter:
    """Expose a multi-model portfolio through the existing Orchestrator adapter contract."""

    def __init__(self, fabric: ModelAccessFabric, portfolio_id: str, *, resource_id: str | None = None) -> None:
        self.fabric = fabric
        self.portfolio_id = portfolio_id
        self.resource_id = resource_id or f"portfolio-{portfolio_id}"
        portfolio = fabric.store.get_portfolio(portfolio_id)
        profiles = {item.model_id: item for item in fabric.store.list_profiles(enabled_only=True)}
        supports = []
        for member in portfolio.members:
            profile = profiles.get(member.model_id)
            if profile is None:
                supports.append(False)
                continue
            supports.append(bool(getattr(fabric.adapter_for(profile), "supports_idempotency", False)))
        self.supports_idempotency = bool(supports) and all(supports)

    def invoke(self, request: ProviderRequest) -> ProviderResponse:
        result = self.fabric.invoke_portfolio(self.portfolio_id, request)
        if result.verified_response is not None:
            response = result.verified_response
        elif result.selected_model_id is not None:
            selected = next(
                item for item in result.invocations
                if item.model_id == result.selected_model_id and item.response is not None
            )
            response = selected.response
            assert response is not None
        else:
            candidates = [
                {
                    "model_id": item.model_id,
                    "role": item.role,
                    "payload": dict(item.response.payload),
                    "metadata": dict(item.response.metadata),
                }
                for item in result.invocations
                if item.response is not None
            ]
            response = ProviderResponse(
                output_type=request.task.expected_output_type,
                payload={
                    "_w1_multi_model_candidates": candidates,
                    "selection_required": True,
                    "selection_basis": "governed_review_not_model_count_vote",
                },
                context_fields_used=tuple(request.context),
                metadata={"parallel_collect": True},
            )
        metadata = dict(response.metadata)
        metadata["w1_model_portfolio"] = {
            "portfolio_id": self.portfolio_id,
            "strategy": result.strategy,
            "selected_model_id": result.selected_model_id,
            "warnings": list(result.warnings),
            "invocations": [
                {
                    "model_id": item.model_id,
                    "role": item.role,
                    "status": item.status,
                    "latency_seconds": item.latency_seconds,
                    "error_code": item.error_code,
                }
                for item in result.invocations
            ],
        }
        return ProviderResponse(
            output_type=response.output_type,
            payload=deepcopy(dict(response.payload)),
            units_consumed=sum(
                item.response.units_consumed for item in result.invocations if item.response is not None
            ),
            context_fields_used=response.context_fields_used,
            protocol_envelopes=response.protocol_envelopes,
            action_requests=response.action_requests,
            metadata=metadata,
        )
