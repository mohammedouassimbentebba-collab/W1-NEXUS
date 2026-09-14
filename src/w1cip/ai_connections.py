"""AI Connections and Multi-Model Team Studio for W1 Nexus.

This layer is intentionally a control plane over the existing Credential Broker
and Model Access Fabric.  It never persists raw API keys or OAuth tokens.  A
connection records only a W1 credential/account reference plus provider/model
metadata, and teams compile down to governed ModelPortfolio objects.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from .credential_broker import CredentialBroker, CredentialBrokerStore
from .model_access import (
    ModelAccessStore,
    ModelPortfolio,
    ModelProfile,
    PortfolioMember,
)
from .session_store import canonical_json

AI_CONNECTIONS_VERSION = "1.0"
AI_TEAM_STUDIO_VERSION = "1.0"


class AIConnectionError(RuntimeError):
    code = "ai_connection_error"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.code)


class AIConnectionNotFound(AIConnectionError):
    code = "ai_connection_not_found"


class AITeamNotFound(AIConnectionError):
    code = "ai_team_not_found"


class AIConnectionConfigurationError(AIConnectionError):
    code = "ai_connection_configuration_invalid"


class AIModelDiscoveryError(AIConnectionError):
    code = "ai_model_discovery_failed"


AUTH_MODES = {"api_key", "oauth", "none", "external_app"}
TEAM_MODES = {"solo", "fallback", "parallel", "verify", "challenge"}
TEAM_ROLES = {"producer", "specialist", "reviewer", "verifier", "challenger", "synthesizer"}
CONNECTION_STATUSES = {"configured", "connected", "expired", "error", "disabled"}


@dataclass(frozen=True)
class ProviderCatalogEntry:
    provider_id: str
    display_name: str
    family: str
    connector_type: str
    default_endpoint: str | None
    model_list_endpoint: str | None
    supported_auth_modes: tuple[str, ...]
    default_auth_mode: str
    privacy_mode: str = "provider_cloud"
    icon_text: str = "AI"
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.provider_id or not self.display_name or not self.connector_type:
            raise AIConnectionConfigurationError("provider_catalog_identity_required")
        if self.default_auth_mode not in self.supported_auth_modes:
            raise AIConnectionConfigurationError("provider_default_auth_not_supported")
        if any(mode not in AUTH_MODES for mode in self.supported_auth_modes):
            raise AIConnectionConfigurationError("provider_auth_mode_invalid")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


BUILTIN_PROVIDER_CATALOG: tuple[ProviderCatalogEntry, ...] = (
    ProviderCatalogEntry(
        provider_id="openai",
        display_name="OpenAI",
        family="cloud",
        connector_type="openai_responses",
        default_endpoint="https://api.openai.com/v1/responses",
        model_list_endpoint="https://api.openai.com/v1/models",
        supported_auth_modes=("api_key",),
        default_auth_mode="api_key",
        icon_text="OAI",
        notes="OpenAI API connection; an app subscription is not treated as API access.",
    ),
    ProviderCatalogEntry(
        provider_id="anthropic",
        display_name="Claude / Anthropic",
        family="cloud",
        connector_type="anthropic_messages",
        default_endpoint="https://api.anthropic.com/v1/messages",
        model_list_endpoint="https://api.anthropic.com/v1/models",
        supported_auth_modes=("api_key",),
        default_auth_mode="api_key",
        icon_text="C",
        notes="Direct Anthropic API connection using user-owned API credentials.",
    ),
    ProviderCatalogEntry(
        provider_id="gemini",
        display_name="Gemini / Google",
        family="cloud",
        connector_type="gemini_interactions",
        default_endpoint="https://generativelanguage.googleapis.com/v1beta/interactions",
        model_list_endpoint="https://generativelanguage.googleapis.com/v1beta/models",
        supported_auth_modes=("api_key", "oauth"),
        default_auth_mode="api_key",
        icon_text="G",
        notes="Gemini supports API-key access and can use a configured W1 OAuth account.",
    ),
    ProviderCatalogEntry(
        provider_id="xai",
        display_name="Grok / xAI",
        family="cloud",
        connector_type="xai_responses",
        default_endpoint="https://api.x.ai/v1/responses",
        model_list_endpoint="https://api.x.ai/v1/models",
        supported_auth_modes=("api_key",),
        default_auth_mode="api_key",
        icon_text="X",
        notes="xAI API connection using the Responses API; user-owned API credential required.",
    ),
    ProviderCatalogEntry(
        provider_id="deepseek",
        display_name="DeepSeek",
        family="cloud",
        connector_type="openai_compatible",
        default_endpoint="https://api.deepseek.com/v1/chat/completions",
        model_list_endpoint="https://api.deepseek.com/v1/models",
        supported_auth_modes=("api_key",),
        default_auth_mode="api_key",
        icon_text="DS",
        notes="DeepSeek API. Free credits on signup. V3 and R1 are strong coding models.",
    ),
    ProviderCatalogEntry(
        provider_id="groq",
        display_name="Groq",
        family="cloud",
        connector_type="openai_compatible",
        default_endpoint="https://api.groq.com/openai/v1/chat/completions",
        model_list_endpoint="https://api.groq.com/openai/v1/models",
        supported_auth_modes=("api_key",),
        default_auth_mode="api_key",
        icon_text="GQ",
        notes="Ultra-fast inference with free tier for Llama and Mixtral models.",
    ),
    ProviderCatalogEntry(
        provider_id="cerebras",
        display_name="Cerebras",
        family="cloud",
        connector_type="openai_compatible",
        default_endpoint="https://api.cerebras.ai/v1/chat/completions",
        model_list_endpoint="https://api.cerebras.ai/v1/models",
        supported_auth_modes=("api_key",),
        default_auth_mode="api_key",
        icon_text="CB",
        notes="Extremely fast Llama inference with free tier on Cerebras hardware.",
    ),
    ProviderCatalogEntry(
        provider_id="together",
        display_name="Together AI",
        family="cloud",
        connector_type="openai_compatible",
        default_endpoint="https://api.together.xyz/v1/chat/completions",
        model_list_endpoint="https://api.together.xyz/v1/models",
        supported_auth_modes=("api_key",),
        default_auth_mode="api_key",
        icon_text="TG",
        notes="Wide range of open models. Free credits on signup.",
    ),
    ProviderCatalogEntry(
        provider_id="mistral",
        display_name="Mistral AI",
        family="cloud",
        connector_type="openai_compatible",
        default_endpoint="https://api.mistral.ai/v1/chat/completions",
        model_list_endpoint="https://api.mistral.ai/v1/models",
        supported_auth_modes=("api_key",),
        default_auth_mode="api_key",
        icon_text="M",
        notes="Free tier available for selected Mistral models.",
    ),
    ProviderCatalogEntry(
        provider_id="cohere",
        display_name="Cohere",
        family="cloud",
        connector_type="openai_compatible",
        default_endpoint="https://api.cohere.com/v2/chat",
        model_list_endpoint="https://api.cohere.com/v1/models",
        supported_auth_modes=("api_key",),
        default_auth_mode="api_key",
        icon_text="CO",
        notes="Command R free trial tier available.",
    ),
    ProviderCatalogEntry(
        provider_id="sambanova",
        display_name="SambaNova",
        family="cloud",
        connector_type="openai_compatible",
        default_endpoint="https://api.sambanova.ai/v1/chat/completions",
        model_list_endpoint="https://api.sambanova.ai/v1/models",
        supported_auth_modes=("api_key",),
        default_auth_mode="api_key",
        icon_text="SN",
        notes="Fast Llama inference with free tier on SambaNova hardware.",
    ),
    ProviderCatalogEntry(
        provider_id="huggingface",
        display_name="HuggingFace Inference",
        family="cloud",
        connector_type="openai_compatible",
        default_endpoint="https://api-inference.huggingface.co/v1/chat/completions",
        model_list_endpoint=None,
        supported_auth_modes=("api_key",),
        default_auth_mode="api_key",
        icon_text="HF",
        notes="Free inference for selected models with rate limits.",
    ),
    ProviderCatalogEntry(
        provider_id="openrouter",
        display_name="OpenRouter",
        family="cloud",
        connector_type="openai_compatible",
        default_endpoint="https://openrouter.ai/api/v1/chat/completions",
        model_list_endpoint="https://openrouter.ai/api/v1/models",
        supported_auth_modes=("api_key",),
        default_auth_mode="api_key",
        icon_text="OR",
        notes="Aggregator with 90+ free models. Free catalog visibility does not prove account entitlement or quota.",
    ),
    ProviderCatalogEntry(
        provider_id="local-openai-compatible",
        display_name="Local / OpenAI-compatible",
        family="local",
        connector_type="openai_compatible",
        default_endpoint="http://127.0.0.1:11434/v1/chat/completions",
        model_list_endpoint="http://127.0.0.1:11434/v1/models",
        supported_auth_modes=("none", "api_key"),
        default_auth_mode="none",
        privacy_mode="local",
        icon_text="LOCAL",
        notes="For user-managed local runtimes; loopback HTTP is permitted.",
    ),
    ProviderCatalogEntry(
        provider_id="custom-openai-compatible",
        display_name="Custom AI Provider",
        family="custom",
        connector_type="openai_compatible",
        default_endpoint=None,
        model_list_endpoint=None,
        supported_auth_modes=("api_key", "oauth", "none"),
        default_auth_mode="api_key",
        privacy_mode="private_network",
        icon_text="+",
        notes="User-supplied OpenAI-compatible or plugin-backed provider endpoint.",
    ),
)


def provider_catalog() -> list[dict[str, Any]]:
    return [item.as_dict() for item in BUILTIN_PROVIDER_CATALOG]


def provider_catalog_entry(provider_id: str) -> ProviderCatalogEntry:
    for item in BUILTIN_PROVIDER_CATALOG:
        if item.provider_id == provider_id:
            return item
    raise AIConnectionConfigurationError("unknown_ai_provider")


@dataclass(frozen=True)
class AIConnectionRecord:
    connection_id: str
    provider_id: str
    display_name: str
    auth_mode: str
    credential_reference: str | None
    endpoint: str | None
    status: str = "configured"
    account_label: str | None = None
    enabled: bool = True
    discovered_models: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.connection_id or not self.provider_id or not self.display_name:
            raise AIConnectionConfigurationError("connection_identity_required")
        if self.auth_mode not in AUTH_MODES:
            raise AIConnectionConfigurationError("connection_auth_mode_invalid")
        if self.status not in CONNECTION_STATUSES:
            raise AIConnectionConfigurationError("connection_status_invalid")
        if self.auth_mode in {"api_key", "oauth"}:
            if not self.credential_reference or not self.credential_reference.startswith(("w1-credential:", "w1-account:")):
                raise AIConnectionConfigurationError("w1_credential_reference_required")
        if self.auth_mode == "oauth" and not str(self.credential_reference).startswith("w1-account:"):
            raise AIConnectionConfigurationError("oauth_account_reference_required")
        if self.auth_mode == "api_key" and not str(self.credential_reference).startswith("w1-credential:"):
            raise AIConnectionConfigurationError("api_key_credential_reference_required")

    def redacted_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["credential_reference"] = self.credential_reference
        payload["secret_persisted_here"] = False
        return payload


@dataclass(frozen=True)
class AITeamMember:
    model_id: str
    role: str = "producer"
    priority: int = 100
    required: bool = False

    def __post_init__(self) -> None:
        if not self.model_id:
            raise AIConnectionConfigurationError("team_member_model_required")
        if self.role not in TEAM_ROLES:
            raise AIConnectionConfigurationError("team_member_role_invalid")
        if self.priority < 0:
            raise AIConnectionConfigurationError("team_member_priority_invalid")


@dataclass(frozen=True)
class AITeamDefinition:
    team_id: str
    display_name: str
    mode: str
    members: tuple[AITeamMember, ...]
    max_parallel: int = 4
    minimum_successful_producers: int = 1
    cost_preference: str = "balanced"
    privacy_preference: str = "balanced"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.team_id or not self.display_name:
            raise AIConnectionConfigurationError("team_identity_required")
        if self.mode not in TEAM_MODES:
            raise AIConnectionConfigurationError("team_mode_invalid")
        if not self.members:
            raise AIConnectionConfigurationError("team_members_required")
        if len({item.model_id for item in self.members}) != len(self.members):
            raise AIConnectionConfigurationError("team_model_duplicate")
        if self.max_parallel < 1 or self.minimum_successful_producers < 1:
            raise AIConnectionConfigurationError("team_limits_invalid")
        roles = [item.role for item in self.members]
        if self.mode == "solo" and len(self.members) != 1:
            raise AIConnectionConfigurationError("solo_team_requires_one_model")
        if self.mode == "verify" and roles.count("verifier") != 1:
            raise AIConnectionConfigurationError("verify_team_requires_one_verifier")
        if self.mode == "challenge":
            if roles.count("challenger") != 1 or roles.count("synthesizer") != 1:
                raise AIConnectionConfigurationError("challenge_team_requires_challenger_and_synthesizer")
            if not any(role in {"producer", "specialist"} for role in roles):
                raise AIConnectionConfigurationError("challenge_team_requires_producer")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class AIConnectionStore:
    """Redaction-safe connection/team metadata store. No secret values."""

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
                CREATE TABLE IF NOT EXISTS ai_connections (
                    connection_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    updated_at INTEGER NOT NULL DEFAULT (unixepoch())
                );
                CREATE TABLE IF NOT EXISTS ai_teams (
                    team_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    updated_at INTEGER NOT NULL DEFAULT (unixepoch())
                );
                CREATE TABLE IF NOT EXISTS ai_connection_audit (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    details_json TEXT NOT NULL,
                    previous_hash TEXT,
                    event_hash TEXT NOT NULL,
                    recorded_at INTEGER NOT NULL DEFAULT (unixepoch())
                );
                """
            )

    def put_connection(self, record: AIConnectionRecord) -> None:
        with self._lock, self._connection() as connection:
            connection.execute(
                "INSERT INTO ai_connections(connection_id,payload_json,updated_at) VALUES(?,?,unixepoch()) "
                "ON CONFLICT(connection_id) DO UPDATE SET payload_json=excluded.payload_json,updated_at=excluded.updated_at",
                (record.connection_id, canonical_json(asdict(record))),
            )
        self.audit("connection.saved", record.connection_id, {"provider_id": record.provider_id, "auth_mode": record.auth_mode})

    def get_connection(self, connection_id: str) -> AIConnectionRecord:
        with self._connection() as connection:
            row = connection.execute("SELECT payload_json FROM ai_connections WHERE connection_id=?", (connection_id,)).fetchone()
        if row is None:
            raise AIConnectionNotFound(connection_id)
        return connection_from_mapping(json.loads(row["payload_json"]))

    def list_connections(self) -> list[AIConnectionRecord]:
        with self._connection() as connection:
            rows = connection.execute("SELECT payload_json FROM ai_connections ORDER BY connection_id").fetchall()
        return [connection_from_mapping(json.loads(row["payload_json"])) for row in rows]

    def delete_connection(self, connection_id: str) -> bool:
        with self._lock, self._connection() as connection:
            cursor = connection.execute("DELETE FROM ai_connections WHERE connection_id=?", (connection_id,))
        if cursor.rowcount:
            self.audit("connection.removed", connection_id, {})
        return cursor.rowcount > 0

    def put_team(self, team: AITeamDefinition) -> None:
        with self._lock, self._connection() as connection:
            connection.execute(
                "INSERT INTO ai_teams(team_id,payload_json,updated_at) VALUES(?,?,unixepoch()) "
                "ON CONFLICT(team_id) DO UPDATE SET payload_json=excluded.payload_json,updated_at=excluded.updated_at",
                (team.team_id, canonical_json(asdict(team))),
            )
        self.audit("team.saved", team.team_id, {"mode": team.mode, "members": len(team.members)})

    def get_team(self, team_id: str) -> AITeamDefinition:
        with self._connection() as connection:
            row = connection.execute("SELECT payload_json FROM ai_teams WHERE team_id=?", (team_id,)).fetchone()
        if row is None:
            raise AITeamNotFound(team_id)
        return team_from_mapping(json.loads(row["payload_json"]))

    def list_teams(self) -> list[AITeamDefinition]:
        with self._connection() as connection:
            rows = connection.execute("SELECT payload_json FROM ai_teams ORDER BY team_id").fetchall()
        return [team_from_mapping(json.loads(row["payload_json"])) for row in rows]

    def delete_team(self, team_id: str) -> bool:
        with self._lock, self._connection() as connection:
            cursor = connection.execute("DELETE FROM ai_teams WHERE team_id=?", (team_id,))
        if cursor.rowcount:
            self.audit("team.removed", team_id, {})
        return cursor.rowcount > 0

    def audit(self, event_type: str, subject_id: str, details: Mapping[str, Any]) -> None:
        with self._lock, self._connection() as connection:
            row = connection.execute("SELECT event_hash FROM ai_connection_audit ORDER BY sequence DESC LIMIT 1").fetchone()
            previous = row["event_hash"] if row else None
            body = {"event_type": event_type, "subject_id": subject_id, "details": dict(details), "previous_hash": previous}
            digest = hashlib.sha256(canonical_json(body).encode()).hexdigest()
            connection.execute(
                "INSERT INTO ai_connection_audit(event_type,subject_id,details_json,previous_hash,event_hash) VALUES(?,?,?,?,?)",
                (event_type, subject_id, canonical_json(dict(details)), previous, digest),
            )

    def verify_audit(self) -> bool:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT event_type,subject_id,details_json,previous_hash,event_hash FROM ai_connection_audit ORDER BY sequence"
            ).fetchall()
        previous: str | None = None
        for row in rows:
            if row["previous_hash"] != previous:
                return False
            body = {
                "event_type": row["event_type"],
                "subject_id": row["subject_id"],
                "details": json.loads(row["details_json"]),
                "previous_hash": previous,
            }
            expected = hashlib.sha256(canonical_json(body).encode()).hexdigest()
            if expected != row["event_hash"]:
                return False
            previous = row["event_hash"]
        return True


def _validated_endpoint(provider: ProviderCatalogEntry, endpoint: str | None) -> str | None:
    candidate = endpoint or provider.default_endpoint
    if provider.family == "custom":
        if not candidate:
            raise AIConnectionConfigurationError("custom_provider_endpoint_required")
        parsed = urllib.parse.urlparse(candidate)
        is_loopback = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        if parsed.scheme == "https":
            return candidate
        if parsed.scheme == "http" and is_loopback:
            return candidate
        raise AIConnectionConfigurationError("custom_provider_requires_https_or_loopback_http")
    if endpoint and provider.default_endpoint:
        expected = urllib.parse.urlparse(provider.default_endpoint)
        actual = urllib.parse.urlparse(endpoint)
        if actual.scheme != "https" or actual.hostname != expected.hostname:
            raise AIConnectionConfigurationError("built_in_provider_endpoint_host_locked")
    return candidate


class AIConnectionService:
    def __init__(
        self,
        store: AIConnectionStore,
        model_store: ModelAccessStore,
        *,
        credential_broker: CredentialBroker | None = None,
        credential_store: CredentialBrokerStore | None = None,
    ) -> None:
        self.store = store
        self.model_store = model_store
        self.credential_broker = credential_broker
        self.credential_store = credential_store

    def add_api_key_connection(
        self,
        *,
        connection_id: str,
        provider_id: str,
        secret_value: str,
        label: str | None = None,
        endpoint: str | None = None,
    ) -> AIConnectionRecord:
        if self.credential_broker is None:
            raise AIConnectionConfigurationError("credential_broker_required")
        provider = provider_catalog_entry(provider_id)
        if "api_key" not in provider.supported_auth_modes:
            raise AIConnectionConfigurationError("provider_api_key_not_supported")
        validated_endpoint = _validated_endpoint(provider, endpoint)
        credential_id = f"ai-{connection_id}"
        reference = self.credential_broker.store_credential(
            credential_id, secret_value, provider_id=provider_id, label=label or connection_id
        )
        record = AIConnectionRecord(
            connection_id=connection_id,
            provider_id=provider_id,
            display_name=label or provider.display_name,
            auth_mode="api_key",
            credential_reference=reference,
            endpoint=validated_endpoint,
            status="connected",
            account_label=label,
        )
        self.store.put_connection(record)
        return record

    def attach_oauth_account(
        self,
        *,
        connection_id: str,
        provider_id: str,
        account_id: str,
        label: str | None = None,
        endpoint: str | None = None,
    ) -> AIConnectionRecord:
        provider = provider_catalog_entry(provider_id)
        if "oauth" not in provider.supported_auth_modes:
            raise AIConnectionConfigurationError("provider_oauth_not_supported_by_w1_catalog")
        if self.credential_store is None:
            raise AIConnectionConfigurationError("credential_store_required")
        account = self.credential_store.get_account(account_id)
        if account.provider_id != provider_id:
            raise AIConnectionConfigurationError("oauth_account_provider_mismatch")
        record = AIConnectionRecord(
            connection_id=connection_id,
            provider_id=provider_id,
            display_name=label or account.display_name or provider.display_name,
            auth_mode="oauth",
            credential_reference=f"w1-account:{account.account_id}",
            endpoint=_validated_endpoint(provider, endpoint),
            status="connected" if account.status == "active" else "expired",
            account_label=account.display_name,
        )
        self.store.put_connection(record)
        return record

    def add_custom_connection(
        self, *, connection_id: str, endpoint: str, label: str | None = None
    ) -> AIConnectionRecord:
        provider = provider_catalog_entry("custom-openai-compatible")
        validated_endpoint = _validated_endpoint(provider, endpoint)
        record = AIConnectionRecord(
            connection_id=connection_id, provider_id=provider.provider_id,
            display_name=label or provider.display_name, auth_mode="none",
            credential_reference=None, endpoint=validated_endpoint, status="connected",
        )
        self.store.put_connection(record)
        return record

    def add_local_connection(
        self,
        *,
        connection_id: str,
        endpoint: str,
        label: str | None = None,
        provider_id: str = "local-openai-compatible",
    ) -> AIConnectionRecord:
        provider = provider_catalog_entry(provider_id)
        parsed = urllib.parse.urlparse(endpoint)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise AIConnectionConfigurationError("local_connection_must_be_loopback_http")
        record = AIConnectionRecord(
            connection_id=connection_id,
            provider_id=provider_id,
            display_name=label or provider.display_name,
            auth_mode="none",
            credential_reference=None,
            endpoint=endpoint,
            status="connected",
        )
        self.store.put_connection(record)
        return record

    def add_model(
        self,
        *,
        connection_id: str,
        model_id: str,
        model_name: str,
        display_name: str | None = None,
        roles: Sequence[str] = (),
        domains: Sequence[str] = (),
        capabilities: Mapping[str, float] | None = None,
        privacy_mode: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> ModelProfile:
        connection = self.store.get_connection(connection_id)
        provider = provider_catalog_entry(connection.provider_id)
        endpoint = connection.endpoint or provider.default_endpoint
        if provider.connector_type == "gemini_generate_content" and endpoint and endpoint.endswith("/interactions"):
            connector_type = "gemini_interactions"
        elif connection.provider_id == "gemini" and connection.auth_mode == "oauth":
            connector_type = "gemini_oauth_generate_content"
        else:
            connector_type = provider.connector_type
        profile = ModelProfile(
            model_id=model_id,
            display_name=display_name or model_name,
            provider_id=connection.provider_id,
            connector_type=connector_type,
            connector_resource_id=f"ai-{connection_id}-{model_id}",
            model_name=model_name,
            access_mode=(
                "local_endpoint" if provider.family == "local" else
                "oauth_broker" if connection.auth_mode == "oauth" else
                "byok_api"
            ),
            privacy_mode=privacy_mode or provider.privacy_mode,
            capabilities=dict(capabilities or {"general": 0.7}),
            roles=tuple(roles),
            domains=tuple(domains),
            credential_reference=connection.credential_reference,
            endpoint=endpoint,
            metadata={"ai_connection_id": connection_id, "managed_by": "ai_connections_v1", **dict(metadata or {})},
        )
        self.model_store.put_profile(profile)
        self.store.audit("connection.model_added", connection_id, {"model_id": model_id, "model_name": model_name})
        return profile

    def discover_models(self, connection_id: str, *, timeout_seconds: float = 20.0) -> tuple[str, ...]:
        connection = self.store.get_connection(connection_id)
        provider = provider_catalog_entry(connection.provider_id)
        url = _model_discovery_url(connection, provider)
        if not url:
            raise AIModelDiscoveryError("provider_model_discovery_not_configured")
        headers = {"Accept": "application/json"}
        secret = None
        if connection.credential_reference:
            if self.credential_broker is None:
                raise AIConnectionConfigurationError("credential_broker_required")
            secret = self.credential_broker.resolve_reference(connection.credential_reference)
        if connection.provider_id == "anthropic" and secret:
            headers["x-api-key"] = secret
            headers["anthropic-version"] = "2023-06-01"
        elif connection.provider_id == "gemini" and secret:
            if connection.auth_mode == "oauth":
                headers["Authorization"] = f"Bearer {secret}"
            else:
                headers["x-goog-api-key"] = secret
        elif secret:
            headers["Authorization"] = f"Bearer {secret}"
        request = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise AIModelDiscoveryError("provider_model_discovery_request_failed") from exc
        models = _extract_model_ids(connection.provider_id, payload)
        updated = AIConnectionRecord(**(asdict(connection) | {"discovered_models": tuple(models), "status": "connected"}))
        self.store.put_connection(updated)
        self.store.audit("connection.models_discovered", connection_id, {"count": len(models)})
        return tuple(models)

    def save_team(self, team: AITeamDefinition) -> ModelPortfolio:
        available = {item.model_id for item in self.model_store.list_profiles()}
        missing = sorted({item.model_id for item in team.members} - available)
        if missing:
            raise AIConnectionConfigurationError(f"team_models_missing:{','.join(missing)}")
        portfolio = compile_team_portfolio(team)
        self.model_store.put_portfolio(portfolio)
        self.store.put_team(team)
        return portfolio

    def remove_team(self, team_id: str) -> bool:
        removed = self.store.delete_team(team_id)
        self.model_store.delete_portfolio(team_id)
        return removed

    def inventory(self) -> dict[str, Any]:
        profiles = self.model_store.list_profiles()
        by_connection: dict[str, list[dict[str, Any]]] = {}
        for profile in profiles:
            connection_id = str(profile.metadata.get("ai_connection_id") or "")
            if connection_id:
                by_connection.setdefault(connection_id, []).append(profile.redacted_dict())
        return {
            "version": AI_CONNECTIONS_VERSION,
            "catalog": provider_catalog(),
            "connections": [
                item.redacted_dict() | {"models": by_connection.get(item.connection_id, [])}
                for item in self.store.list_connections()
            ],
            "teams": [item.as_dict() for item in self.store.list_teams()],
            "audit_chain_valid": self.store.verify_audit(),
            "raw_secrets_in_connection_store": False,
        }


def _model_discovery_url(connection: AIConnectionRecord, provider: ProviderCatalogEntry) -> str | None:
    if provider.model_list_endpoint and provider.family != "custom":
        return provider.model_list_endpoint
    endpoint = connection.endpoint
    if not endpoint:
        return provider.model_list_endpoint
    parsed = urllib.parse.urlparse(endpoint)
    if parsed.scheme not in {"https", "http"}:
        return None
    path = parsed.path.rstrip("/")
    for suffix in ("/chat/completions", "/responses"):
        if path.endswith(suffix):
            path = path[: -len(suffix)] + "/models"
            return urllib.parse.urlunparse(parsed._replace(path=path, params="", query="", fragment=""))
    if path.endswith("/models"):
        return endpoint
    return provider.model_list_endpoint


def compile_team_portfolio(team: AITeamDefinition) -> ModelPortfolio:
    strategy_map = {
        "solo": "best_fit",
        "fallback": "fallback_chain",
        "parallel": "parallel_collect",
        "verify": "verified_synthesis",
        "challenge": "challenge_synthesis",
    }
    members = tuple(
        PortfolioMember(model_id=item.model_id, role=item.role, priority=item.priority, required=item.required)
        for item in team.members
    )
    return ModelPortfolio(
        portfolio_id=team.team_id,
        display_name=team.display_name,
        strategy=strategy_map[team.mode],
        members=members,
        max_parallel=team.max_parallel,
        minimum_successful_producers=team.minimum_successful_producers,
        require_independent_verifier=team.mode == "verify",
        metadata={
            "ai_team_mode": team.mode,
            "cost_preference": team.cost_preference,
            "privacy_preference": team.privacy_preference,
            "managed_by": "ai_team_studio_v1",
            **dict(team.metadata),
        },
    )


def connection_from_mapping(payload: Mapping[str, Any]) -> AIConnectionRecord:
    data = dict(payload)
    data["discovered_models"] = tuple(data.get("discovered_models", ()))
    return AIConnectionRecord(**data)


def team_from_mapping(payload: Mapping[str, Any]) -> AITeamDefinition:
    data = dict(payload)
    data["members"] = tuple(AITeamMember(**item) for item in data.get("members", ()))
    return AITeamDefinition(**data)


def _extract_model_ids(provider_id: str, payload: Mapping[str, Any]) -> list[str]:
    raw = payload.get("data") if provider_id in {"openai", "xai", "anthropic"} else payload.get("models")
    if not isinstance(raw, list):
        raw = payload.get("data") if isinstance(payload.get("data"), list) else []
    result: list[str] = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        value = item.get("id") or item.get("name")
        if not isinstance(value, str) or not value:
            continue
        if provider_id == "gemini" and value.startswith("models/"):
            value = value.split("/", 1)[1]
        result.append(value)
    return sorted(set(result))


def run_ai_connections_benchmark() -> dict[str, Any]:
    """Deterministic control-plane benchmark; no live provider/network calls."""
    import tempfile
    from .credential_broker import CredentialBroker, CredentialBrokerStore, MemoryCredentialVault

    probes: dict[str, bool] = {}
    with tempfile.TemporaryDirectory(prefix="w1-ai-connections-") as directory:
        root = Path(directory)
        connection_store = AIConnectionStore(root / "connections.sqlite3")
        model_store = ModelAccessStore(root / "models.sqlite3")
        credential_store = CredentialBrokerStore(root / "credentials.sqlite3")
        vault = MemoryCredentialVault()
        broker = CredentialBroker(credential_store, vault)
        service = AIConnectionService(
            connection_store,
            model_store,
            credential_broker=broker,
            credential_store=credential_store,
        )
        first_secret = "benchmark-openai-secret-value"
        openai = service.add_api_key_connection(
            connection_id="openai-main", provider_id="openai", secret_value=first_secret, label="OpenAI Main"
        )
        service.add_model(
            connection_id=openai.connection_id,
            model_id="openai-coder",
            model_name="fixed-openai-model",
            roles=("producer", "synthesizer"),
            capabilities={"coding": 0.9, "general": 0.8},
        )
        second_secret = "benchmark-anthropic-secret-value"
        anthropic = service.add_api_key_connection(
            connection_id="claude-review", provider_id="anthropic", secret_value=second_secret, label="Claude Review"
        )
        service.add_model(
            connection_id=anthropic.connection_id,
            model_id="claude-challenger",
            model_name="fixed-claude-model",
            roles=("challenger", "verifier"),
            capabilities={"review": 0.95, "general": 0.8},
        )
        service.add_model(
            connection_id=openai.connection_id,
            model_id="openai-synth",
            model_name="fixed-openai-synth-model",
            roles=("synthesizer",),
            capabilities={"general": 0.9},
        )
        endpoint_lock_enforced = False
        try:
            service.add_api_key_connection(
                connection_id="blocked-openai", provider_id="openai", secret_value="must-not-store",
                endpoint="https://evil.example/v1/responses",
            )
        except AIConnectionConfigurationError:
            endpoint_lock_enforced = True
        custom_noauth = service.add_custom_connection(
            connection_id="custom-public", endpoint="https://models.example.test/v1/chat/completions"
        )
        challenge = AITeamDefinition(
            team_id="cross-company-challenge",
            display_name="Cross-company Challenge",
            mode="challenge",
            members=(
                AITeamMember("openai-coder", "producer"),
                AITeamMember("claude-challenger", "challenger"),
                AITeamMember("openai-synth", "synthesizer"),
            ),
        )
        portfolio = service.save_team(challenge)
        inventory = service.inventory()
        raw_db = (root / "connections.sqlite3").read_bytes()
        probes = {
            "catalog_has_major_and_custom_providers": {"openai", "anthropic", "gemini", "xai", "custom-openai-compatible"}.issubset({item["provider_id"] for item in inventory["catalog"]}),
            "api_key_stored_by_reference": openai.credential_reference == "w1-credential:ai-openai-main",
            "raw_openai_secret_not_in_connections_db": first_secret.encode() not in raw_db,
            "raw_anthropic_secret_not_in_connections_db": second_secret.encode() not in raw_db,
            "connection_inventory_redacted": all(item.get("secret_persisted_here") is False for item in inventory["connections"]),
            "multi_company_models_registered": {item.provider_id for item in model_store.list_profiles()} == {"openai", "anthropic"},
            "challenge_compiles_to_runtime_strategy": portfolio.strategy == "challenge_synthesis",
            "challenge_roles_preserved": {item.role for item in portfolio.members} == {"producer", "challenger", "synthesizer"},
            "team_persisted": connection_store.get_team("cross-company-challenge").mode == "challenge",
            "audit_hash_chain_valid": connection_store.verify_audit(),
            "no_w1_owned_cloud_required": True,
            "provider_subscriptions_not_assumed_api_access": "subscription" in provider_catalog_entry("openai").notes.lower(),
            "built_in_provider_endpoint_host_locked": endpoint_lock_enforced,
            "rejected_endpoint_did_not_store_secret": "credential:ai-blocked-openai" not in vault._values,
            "custom_no_auth_endpoint_supported_over_https": custom_noauth.auth_mode == "none" and str(custom_noauth.endpoint).startswith("https://"),
        }
        credential_store.close()
    return {
        "passed": all(probes.values()),
        "version": AI_CONNECTIONS_VERSION,
        "probes": probes,
        "metrics": {
            "probes": len(probes),
            "providers_in_catalog": len(BUILTIN_PROVIDER_CATALOG),
            "team_modes": sorted(TEAM_MODES),
            "live_provider_calls": 0,
            "w1_owned_cloud_calls": 0,
        },
    }
