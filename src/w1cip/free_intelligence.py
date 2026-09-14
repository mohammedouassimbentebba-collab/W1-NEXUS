"""Free/local intelligence discovery for W1 Nexus.

This module deliberately separates *discovery* from *entitlement*.  Finding a
model or a gateway never proves that the user may call it for free.  Official
free-tier eligibility must be verified by the provider/account and third-party
free pools remain explicit opt-in.  Loopback discovery never imports browser
cookies or consumer-app sessions.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Mapping

from .ai_connections import AIConnectionService, AIConnectionStore
from .capacity_router import CapacityObservation, CapacityStore
from .model_access import ModelAccessStore

DISCOVERY_VERSION = "1.0"


@dataclass(frozen=True)
class DiscoveryTarget:
    target_id: str
    display_name: str
    kind: str
    model_list_url: str
    capacity_source: str
    terms_status: str
    requires_auth: bool = False
    third_party: bool = False
    notes: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DiscoveryFinding:
    target_id: str
    display_name: str
    kind: str
    status: str
    capacity_source: str
    terms_status: str
    endpoint: str | None = None
    models: tuple[str, ...] = ()
    requires_auth: bool = False
    third_party: bool = False
    message: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


BUILTIN_DISCOVERY_TARGETS: tuple[DiscoveryTarget, ...] = (
    DiscoveryTarget(
        target_id="ollama-local",
        display_name="Ollama / local OpenAI-compatible runtime",
        kind="local_runtime",
        model_list_url="http://127.0.0.1:11434/v1/models",
        capacity_source="local",
        terms_status="official",
        notes="Local loopback discovery. No network account is required by W1.",
    ),
    DiscoveryTarget(
        target_id="freellmapi-local",
        display_name="FreeLLMAPI local gateway",
        kind="local_gateway",
        model_list_url="http://127.0.0.1:3001/v1/models",
        capacity_source="third_party_free",
        terms_status="unknown",
        requires_auth=True,
        third_party=True,
        notes="Optional user-run gateway. Provider-specific terms must be reviewed before automatic routing.",
    ),
    DiscoveryTarget(
        target_id="9router-local",
        display_name="9Router local gateway",
        kind="local_gateway",
        model_list_url="http://127.0.0.1:20128/v1/models",
        capacity_source="third_party_free",
        terms_status="unknown",
        requires_auth=True,
        third_party=True,
        notes="Optional user-run gateway. Provider-specific terms must be reviewed before automatic routing.",
    ),
)


def _extract_openai_models(payload: Mapping[str, Any]) -> tuple[str, ...]:
    rows = payload.get("data")
    if not isinstance(rows, list):
        rows = payload.get("models")
    values: list[str] = []
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, str):
                values.append(row)
            elif isinstance(row, Mapping):
                value = row.get("id") or row.get("name") or row.get("model")
                if value:
                    values.append(str(value))
    return tuple(dict.fromkeys(values))


def _loopback_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    return parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}


class FreeIntelligenceDiscovery:
    """Discover user-accessible local/free-capacity opportunities.

    The scanner is fail-closed: public third-party targets are not contacted;
    only loopback gateways listed above and providers already connected by the
    user can be queried.  Discovery results are observations, not proof of API
    entitlement or billing status.
    """

    def __init__(
        self,
        *,
        connection_store: AIConnectionStore,
        model_store: ModelAccessStore,
        capacity_store: CapacityStore,
        service: AIConnectionService | None = None,
    ) -> None:
        self.connection_store = connection_store
        self.model_store = model_store
        self.capacity_store = capacity_store
        self.service = service or AIConnectionService(connection_store, model_store)

    def scan_loopback(
        self,
        *,
        include_third_party: bool = False,
        timeout_seconds: float = 1.5,
        opener: Any = urllib.request.urlopen,
    ) -> tuple[DiscoveryFinding, ...]:
        findings: list[DiscoveryFinding] = []
        for target in BUILTIN_DISCOVERY_TARGETS:
            if target.third_party and not include_third_party:
                findings.append(DiscoveryFinding(
                    target.target_id, target.display_name, target.kind, "opt_in_required",
                    target.capacity_source, target.terms_status,
                    endpoint=target.model_list_url, requires_auth=target.requires_auth,
                    third_party=True, message="Third-party discovery requires explicit opt-in.",
                ))
                continue
            if not _loopback_url(target.model_list_url):
                findings.append(DiscoveryFinding(
                    target.target_id, target.display_name, target.kind, "blocked",
                    target.capacity_source, target.terms_status,
                    endpoint=target.model_list_url, requires_auth=target.requires_auth,
                    third_party=target.third_party, message="Only loopback automatic discovery is permitted.",
                ))
                continue
            request = urllib.request.Request(target.model_list_url, headers={"Accept": "application/json"}, method="GET")
            try:
                with opener(request, timeout=timeout_seconds) as response:  # noqa: S310 - loopback checked above
                    raw = response.read().decode("utf-8")
                    payload = json.loads(raw) if raw.strip() else {}
                models = _extract_openai_models(payload if isinstance(payload, Mapping) else {})
                findings.append(DiscoveryFinding(
                    target.target_id, target.display_name, target.kind,
                    "available" if models else "detected",
                    target.capacity_source, target.terms_status,
                    endpoint=target.model_list_url, models=models,
                    requires_auth=target.requires_auth, third_party=target.third_party,
                    message=(f"Discovered {len(models)} model(s)." if models else "Gateway responded but returned no model list."),
                ))
            except urllib.error.HTTPError as exc:
                status = "auth_required" if exc.code in {401, 403} else "unavailable"
                findings.append(DiscoveryFinding(
                    target.target_id, target.display_name, target.kind, status,
                    target.capacity_source, target.terms_status,
                    endpoint=target.model_list_url, requires_auth=target.requires_auth,
                    third_party=target.third_party,
                    message=f"HTTP {exc.code}",
                ))
            except (urllib.error.URLError, TimeoutError, socket.timeout, OSError, json.JSONDecodeError):
                findings.append(DiscoveryFinding(
                    target.target_id, target.display_name, target.kind, "not_detected",
                    target.capacity_source, target.terms_status,
                    endpoint=target.model_list_url, requires_auth=target.requires_auth,
                    third_party=target.third_party,
                    message="No compatible loopback service detected.",
                ))
        return tuple(findings)

    def scan_connected(self, *, timeout_seconds: float = 8.0) -> tuple[DiscoveryFinding, ...]:
        findings: list[DiscoveryFinding] = []
        for connection in self.connection_store.list_connections():
            if not connection.enabled:
                continue
            try:
                models = self.service.discover_models(connection.connection_id, timeout_seconds=timeout_seconds)
                findings.append(DiscoveryFinding(
                    target_id=f"connection:{connection.connection_id}",
                    display_name=connection.display_name,
                    kind="connected_provider",
                    status="models_discovered" if models else "connected",
                    capacity_source="unknown",
                    terms_status="official" if connection.provider_id != "custom-openai-compatible" else "unknown",
                    endpoint=connection.endpoint,
                    models=tuple(models),
                    requires_auth=connection.auth_mode != "none",
                    third_party=connection.provider_id == "custom-openai-compatible",
                    message=(
                        "Models were discovered. Free-tier/billing entitlement is not inferred from model visibility."
                    ),
                    metadata={"connection_id": connection.connection_id, "provider_id": connection.provider_id},
                ))
            except Exception as exc:  # discovery must report rather than abort the whole scan
                findings.append(DiscoveryFinding(
                    target_id=f"connection:{connection.connection_id}", display_name=connection.display_name,
                    kind="connected_provider", status="discovery_failed", capacity_source="unknown",
                    terms_status="official" if connection.provider_id != "custom-openai-compatible" else "unknown",
                    endpoint=connection.endpoint, requires_auth=connection.auth_mode != "none",
                    third_party=connection.provider_id == "custom-openai-compatible",
                    message=getattr(exc, "code", exc.__class__.__name__),
                    metadata={"connection_id": connection.connection_id, "provider_id": connection.provider_id},
                ))
        return tuple(findings)

    def adopt_local_ollama(self, finding: DiscoveryFinding) -> dict[str, Any]:
        if finding.target_id != "ollama-local" or finding.status not in {"available", "detected"}:
            raise ValueError("ollama_discovery_not_adoptable")
        connection_id = "local-ollama"
        service = self.service
        try:
            connection = self.connection_store.get_connection(connection_id)
        except Exception:
            connection = service.add_local_connection(
                connection_id=connection_id,
                endpoint="http://127.0.0.1:11434/v1/chat/completions",
                label="Local Ollama",
            )
        added: list[str] = []
        for raw_model in finding.models:
            model_id = "ollama-" + "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in raw_model.lower()).strip("-")
            try:
                service.add_model(
                    connection_id=connection.connection_id,
                    model_id=model_id,
                    model_name=raw_model,
                    display_name=raw_model,
                    roles=("producer", "synthesizer"),
                    domains=("general",),
                    capabilities={"general": 0.68},
                    privacy_mode="local",
                    metadata={
                        "capacity_source": "local",
                        "terms_status": "official",
                        "input_cost_per_million": 0.0,
                        "output_cost_per_million": 0.0,
                        "discovered_by": "free_intelligence_v1",
                    },
                )
                self.capacity_store.put(CapacityObservation(
                    model_id=model_id,
                    source="local",
                    quota_state="unlimited",
                    terms_status="official",
                    metadata={"discovered_by": "free_intelligence_v1", "raw_model": raw_model},
                ))
                added.append(model_id)
            except Exception:
                # Existing profiles are already usable; adoption stays idempotent.
                if any(item.model_id == model_id for item in self.model_store.list_profiles()):
                    added.append(model_id)
        return {"connection_id": connection.connection_id, "models": added, "count": len(added)}


def discovery_catalog() -> list[dict[str, Any]]:
    return [item.as_dict() for item in BUILTIN_DISCOVERY_TARGETS]
