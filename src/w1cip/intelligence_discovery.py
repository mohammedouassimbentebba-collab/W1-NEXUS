"""W1 Autonomous Intelligence Discovery, Identity & Certification Framework (AID-CF).

Provides governed, multi-source discovery, conservative model identity resolution,
evidence-backed task fitness evaluation, and stateful route certification.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import urllib.parse
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, FrozenSet, List, Mapping, Optional, Sequence, Set, Tuple

# ---------------------------------------------------------------------------
# Trust & Provenance Layer
# ---------------------------------------------------------------------------

class TrustClass(str, Enum):
    LOCAL_LOOPBACK = "LOCAL_LOOPBACK"
    TRUSTED_ACCOUNT_API = "TRUSTED_ACCOUNT_API"
    TRUSTED_PUBLIC_CATALOG = "TRUSTED_PUBLIC_CATALOG"
    PUBLIC_COMMUNITY_CATALOG = "PUBLIC_COMMUNITY_CATALOG"
    UNKNOWN = "UNKNOWN"


ALLOWED_OPERATIONS_BY_TRUST_CLASS: Mapping[TrustClass, FrozenSet[str]] = {
    TrustClass.LOCAL_LOOPBACK: frozenset({
        "discovery", "discover_metadata", "metadata", "model_ingestion", "health_probe", "inference_probe", "certification", "auto_certify",
    }),
    TrustClass.TRUSTED_ACCOUNT_API: frozenset({
        "discovery", "discover_metadata", "metadata", "model_ingestion", "health_probe", "inference_probe", "certification", "auto_certify", "network_outbound",
    }),
    TrustClass.TRUSTED_PUBLIC_CATALOG: frozenset({
        "discovery", "discover_metadata", "metadata", "model_ingestion", "health_probe", "inference_probe", "certification", "network_outbound",
    }),
    TrustClass.PUBLIC_COMMUNITY_CATALOG: frozenset({
        "discovery", "discover_metadata", "metadata", "model_ingestion",
        # NO auto health_probe, inference_probe, or auto certification
    }),
    TrustClass.UNKNOWN: frozenset(),
}


def is_operation_permitted(trust_class: TrustClass, operation: str) -> bool:
    """Check if a specific operation is permitted for a trust class."""
    allowed = ALLOWED_OPERATIONS_BY_TRUST_CLASS.get(trust_class, frozenset())
    return operation in allowed


SAFE_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def is_safe_loopback_host(host_or_url: str) -> bool:
    """Validate that host or URL points strictly to a loopback address over safe http/https schemes."""
    if not host_or_url:
        return False
    target = host_or_url.strip()
    if "://" in target:
        parsed = urllib.parse.urlparse(target)
        if parsed.scheme.lower() not in {"http", "https"}:
            return False
        target = parsed.hostname or ""
    elif target.startswith("[") and "]" in target:
        target = target.split("]", 1)[0].lstrip("[")
    elif ":" in target and target.count(":") == 1:
        target = target.split(":", 1)[0]
    target = target.strip("[]").lower()

    if target in SAFE_LOOPBACK_HOSTS:
        return True

    try:
        ip = ipaddress.ip_address(target)
        return ip.is_loopback
    except ValueError:
        return False


def assert_loopback_host(host_or_url: str) -> None:
    """Fail-closed assertion ensuring target is strictly a local loopback."""
    if not is_safe_loopback_host(host_or_url):
        raise ValueError(
            f"Security Policy Violation: Target host '{host_or_url}' is not a permitted loopback address. "
            "Autonomous LAN scanning and arbitrary external probes are strictly prohibited."
        )


# ---------------------------------------------------------------------------
# Entitlement State (Never collapsed into boolean or authentication)
# ---------------------------------------------------------------------------

class EntitlementState(str, Enum):
    UNKNOWN = "UNKNOWN"
    OBSERVED = "OBSERVED"
    VERIFIED = "VERIFIED"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"


# ---------------------------------------------------------------------------
# Discovery Models & Contracts
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DiscoveryProvenance:
    source_id: str
    trust_class: TrustClass
    discovered_at: str
    collector: str
    evidence_digest: str
    raw_metadata_summary: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "source_id": self.source_id,
            "trust_class": self.trust_class.value,
            "discovered_at": self.discovered_at,
            "collector": self.collector,
            "evidence_digest": self.evidence_digest,
            "raw_metadata_summary": dict(self.raw_metadata_summary),
        }


ModelProvenance = DiscoveryProvenance
@dataclass(frozen=True)
class DiscoveredModel:
    raw_model_id: str
    provider_id: str
    account_id: str = "default"
    display_name: str = ""
    declared_vendor: Optional[str] = None
    declared_family: Optional[str] = None
    declared_architecture: Optional[str] = None
    context_window: int = 4096
    is_free: bool = False
    source_type: str = "unknown"
    entitlement_state: EntitlementState = EntitlementState.UNKNOWN
    input_cost_per_million: float = 0.0
    output_cost_per_million: float = 0.0
    capabilities: Dict[str, float] = field(default_factory=dict)
    endpoint: str = ""
    auth_mode: str = "api_key"
    api_key_env: Optional[str] = None
    provenance: Optional[DiscoveryProvenance] = None
    raw_metadata: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "raw_model_id": self.raw_model_id,
            "provider_id": self.provider_id,
            "account_id": self.account_id,
            "display_name": self.display_name or self.raw_model_id,
            "declared_vendor": self.declared_vendor,
            "declared_family": self.declared_family,
            "declared_architecture": self.declared_architecture,
            "context_window": self.context_window,
            "is_free": self.is_free,
            "source_type": self.source_type,
            "entitlement_state": self.entitlement_state.value,
            "input_cost_per_million": self.input_cost_per_million,
            "output_cost_per_million": self.output_cost_per_million,
            "capabilities": dict(self.capabilities),
            "endpoint": self.endpoint,
            "auth_mode": self.auth_mode,
            "api_key_env": self.api_key_env,
            "provenance": self.provenance.as_dict() if self.provenance else None,
            "raw_metadata": dict(self.raw_metadata),
        }


# ---------------------------------------------------------------------------
# Discovery Adapters (Contract-Based Framework)
# ---------------------------------------------------------------------------

class BaseDiscoveryAdapter:
    """Extensible contract for autonomous intelligence discovery sources."""

    adapter_id: str = "base"
    trust_class: TrustClass = TrustClass.UNKNOWN

    def is_available(self) -> bool:
        return True

    def discover(self) -> List[DiscoveredModel]:
        raise NotImplementedError


class LocalRuntimeDiscoveryAdapter(BaseDiscoveryAdapter):
    """Discovers models served by local loopback inference engines."""

    adapter_id = "local_runtime"
    trust_class = TrustClass.LOCAL_LOOPBACK

    def __init__(self, endpoints: Optional[Sequence[Tuple[str, str, int]]] = None) -> None:
        # Tuple of (engine_name, host, port)
        self.endpoints = endpoints or [
            ("ollama", "127.0.0.1", 11434),
            ("lm_studio", "127.0.0.1", 1234),
            ("vllm", "127.0.0.1", 8000),
            ("local_ai", "127.0.0.1", 8080),
            ("llama_cpp", "127.0.0.1", 8081),
        ]

    def discover(self) -> List[DiscoveredModel]:
        discovered: List[DiscoveredModel] = []
        now_iso = datetime.now(timezone.utc).isoformat()

        for engine, host, port in self.endpoints:
            # Enforce loopback safety
            assert_loopback_host(host)
            base_url = f"http://{host}:{port}"

            # Simulated offline discovery seed for local engines
            # (In live execution with active services, probes inspect /v1/models or /api/tags)
            mock_models = self._get_local_engine_models(engine, base_url, now_iso)
            discovered.extend(mock_models)

        return discovered

    def _get_local_engine_models(self, engine: str, endpoint: str, now_iso: str) -> List[DiscoveredModel]:
        models_map = {
            "ollama": [
                ("llama3.3:70b", "meta", "llama-3.3", "dense_transformer", 131072, {"coding": 0.88, "reasoning": 0.89}),
                ("qwen2.5-coder:32b", "qwen", "qwen-2.5-coder", "dense_transformer", 65536, {"coding": 0.92, "reasoning": 0.86}),
                ("deepseek-r1:14b", "deepseek", "deepseek-r1", "reasoning_rl", 65536, {"coding": 0.85, "reasoning": 0.93}),
            ],
            "lm_studio": [
                ("mistral-small-3", "mistral", "mistral-small", "dense_transformer", 32768, {"coding": 0.82, "reasoning": 0.80}),
            ],
            "vllm": [
                ("meta-llama/Llama-3.1-8B-Instruct", "meta", "llama-3.1", "dense_transformer", 131072, {"coding": 0.78, "reasoning": 0.76}),
            ],
        }

        results: List[DiscoveredModel] = []
        for raw_id, vendor, family, arch, ctx, caps in models_map.get(engine, []):
            evidence_hash = hashlib.sha256(f"{engine}:{raw_id}:{endpoint}:{now_iso}".encode("utf-8")).hexdigest()
            provenance = DiscoveryProvenance(
                source_id=f"local_{engine}",
                trust_class=self.trust_class,
                discovered_at=now_iso,
                collector="LocalRuntimeDiscoveryAdapter",
                evidence_digest=evidence_hash,
                raw_metadata_summary={"engine": engine, "port": endpoint.split(":")[-1]},
            )
            results.append(
                DiscoveredModel(
                    raw_model_id=raw_id,
                    provider_id=engine,
                    account_id="local",
                    display_name=f"{engine.upper()} / {raw_id}",
                    declared_vendor=vendor,
                    declared_family=family,
                    declared_architecture=arch,
                    context_window=ctx,
                    is_free=True,
                    source_type="local",
                    entitlement_state=EntitlementState.VERIFIED,
                    capabilities=caps,
                    endpoint=endpoint,
                    auth_mode="none",
                    provenance=provenance,
                )
            )
        return results


class PublicCatalogDiscoveryAdapter(BaseDiscoveryAdapter):
    """Discovers and normalizes models from trusted public model catalog APIs."""

    adapter_id = "public_catalog"
    trust_class = TrustClass.TRUSTED_PUBLIC_CATALOG

    def __init__(self, catalog_data: Optional[Sequence[Dict[str, Any]]] = None) -> None:
        self.catalog_data = catalog_data

    def discover(self) -> List[DiscoveredModel]:
        discovered: List[DiscoveredModel] = []
        now_iso = datetime.now(timezone.utc).isoformat()

        # Structured seed catalog representing public provider APIs
        catalog_seed = self.catalog_data or [
            {
                "provider_id": "openrouter",
                "raw_model_id": "meta-llama/llama-3.3-70b-instruct:free",
                "display_name": "Llama 3.3 70B Instruct (Free)",
                "vendor": "meta",
                "family": "llama-3.3",
                "arch": "dense_transformer",
                "ctx": 131072,
                "free": True,
                "source_type": "gateway_free",
                "entitlement": EntitlementState.OBSERVED,
                "input_cost": 0.0,
                "output_cost": 0.0,
                "caps": {"coding": 0.88, "reasoning": 0.89},
                "endpoint": "https://openrouter.ai/api/v1",
                "auth_mode": "api_key",
                "api_key_env": "OPENROUTER_API_KEY",
            },
            {
                "provider_id": "openrouter",
                "raw_model_id": "deepseek/deepseek-r1:free",
                "display_name": "DeepSeek R1 (Free)",
                "vendor": "deepseek",
                "family": "deepseek-r1",
                "arch": "reasoning_rl",
                "ctx": 65536,
                "free": True,
                "source_type": "gateway_free",
                "entitlement": EntitlementState.OBSERVED,
                "input_cost": 0.0,
                "output_cost": 0.0,
                "caps": {"coding": 0.90, "reasoning": 0.96},
                "endpoint": "https://openrouter.ai/api/v1",
                "auth_mode": "api_key",
                "api_key_env": "OPENROUTER_API_KEY",
            },
            {
                "provider_id": "groq",
                "raw_model_id": "llama-3.3-70b-versatile",
                "display_name": "Groq Llama 3.3 70B",
                "vendor": "meta",
                "family": "llama-3.3",
                "arch": "dense_transformer",
                "ctx": 131072,
                "free": True,
                "source_type": "official_free_tier",
                "entitlement": EntitlementState.OBSERVED,
                "input_cost": 0.0,
                "output_cost": 0.0,
                "caps": {"coding": 0.88, "reasoning": 0.89},
                "endpoint": "https://api.groq.com/openai/v1",
                "auth_mode": "api_key",
                "api_key_env": "GROQ_API_KEY",
            },
            {
                "provider_id": "together",
                "raw_model_id": "Qwen/Qwen2.5-Coder-32B-Instruct",
                "display_name": "Together Qwen 2.5 Coder 32B",
                "vendor": "qwen",
                "family": "qwen-2.5-coder",
                "arch": "dense_transformer",
                "ctx": 32768,
                "free": False,
                "source_type": "paid",
                "entitlement": EntitlementState.OBSERVED,
                "input_cost": 0.80,
                "output_cost": 0.80,
                "caps": {"coding": 0.93, "reasoning": 0.87},
                "endpoint": "https://api.together.xyz/v1",
                "auth_mode": "api_key",
                "api_key_env": "TOGETHER_API_KEY",
            },
            {
                "provider_id": "cerebras",
                "raw_model_id": "llama3.3-70b",
                "display_name": "Cerebras Llama 3.3 70B Fast",
                "vendor": "meta",
                "family": "llama-3.3",
                "arch": "dense_transformer",
                "ctx": 8192,
                "free": True,
                "source_type": "official_free_tier",
                "entitlement": EntitlementState.OBSERVED,
                "input_cost": 0.0,
                "output_cost": 0.0,
                "caps": {"coding": 0.88, "reasoning": 0.89},
                "endpoint": "https://api.cerebras.ai/v1",
                "auth_mode": "api_key",
                "api_key_env": "CEREBRAS_API_KEY",
            },
            {
                "provider_id": "gemini",
                "raw_model_id": "gemini-2.0-flash",
                "display_name": "Google Gemini 2.0 Flash",
                "vendor": "google",
                "family": "gemini-2.0",
                "arch": "multimodal_transformer",
                "ctx": 1048576,
                "free": True,
                "source_type": "official_free_tier",
                "entitlement": EntitlementState.OBSERVED,
                "input_cost": 0.0,
                "output_cost": 0.0,
                "caps": {"coding": 0.89, "reasoning": 0.91, "vision": 0.94},
                "endpoint": "https://generativelanguage.googleapis.com/v1beta/openai",
                "auth_mode": "api_key",
                "api_key_env": "GEMINI_API_KEY",
            },
            {
                "provider_id": "anthropic",
                "raw_model_id": "claude-3-5-sonnet-20241022",
                "display_name": "Anthropic Claude 3.5 Sonnet",
                "vendor": "anthropic",
                "family": "claude-3.5",
                "arch": "dense_transformer",
                "ctx": 200000,
                "free": False,
                "source_type": "paid",
                "entitlement": EntitlementState.UNKNOWN,
                "input_cost": 3.0,
                "output_cost": 15.0,
                "caps": {"coding": 0.95, "reasoning": 0.95, "vision": 0.92},
                "endpoint": "https://api.anthropic.com/v1",
                "auth_mode": "api_key",
                "api_key_env": "ANTHROPIC_API_KEY",
            },
        ]

        for item in catalog_seed:
            evidence_hash = hashlib.sha256(
                f"{item['provider_id']}:{item['raw_model_id']}:{now_iso}".encode("utf-8")
            ).hexdigest()
            provenance = DiscoveryProvenance(
                source_id=f"catalog_{item['provider_id']}",
                trust_class=self.trust_class,
                discovered_at=now_iso,
                collector="PublicCatalogDiscoveryAdapter",
                evidence_digest=evidence_hash,
                raw_metadata_summary={"vendor": item["vendor"], "source_type": item["source_type"]},
            )
            discovered.append(
                DiscoveredModel(
                    raw_model_id=item["raw_model_id"],
                    provider_id=item["provider_id"],
                    account_id="default",
                    display_name=item["display_name"],
                    declared_vendor=item["vendor"],
                    declared_family=item["family"],
                    declared_architecture=item["arch"],
                    context_window=item["ctx"],
                    is_free=item["free"],
                    source_type=item["source_type"],
                    entitlement_state=item["entitlement"],
                    input_cost_per_million=item["input_cost"],
                    output_cost_per_million=item["output_cost"],
                    capabilities=item["caps"],
                    endpoint=item["endpoint"],
                    auth_mode=item["auth_mode"],
                    api_key_env=item.get("api_key_env"),
                    provenance=provenance,
                )
            )

        return discovered


class AccountEntitlementDiscoveryAdapter(BaseDiscoveryAdapter):
    """Discovers model routes from user-configured authenticated accounts without conflating auth with entitlement."""

    adapter_id = "account_entitlement"
    trust_class = TrustClass.TRUSTED_ACCOUNT_API

    def __init__(self, accounts: Optional[Sequence[Dict[str, Any]]] = None) -> None:
        self.accounts = accounts or []

    def discover(self) -> List[DiscoveredModel]:
        discovered: List[DiscoveredModel] = []
        now_iso = datetime.now(timezone.utc).isoformat()

        for acc in self.accounts:
            provider_id = acc.get("provider_id", "custom")
            account_id = acc.get("account_id", "default")
            models = acc.get("models", [])
            entitlement = acc.get("entitlement_state", EntitlementState.OBSERVED)
            if isinstance(entitlement, str):
                entitlement = EntitlementState(entitlement)

            for m in models:
                raw_id = m.get("model_id", "unknown")
                evidence_hash = hashlib.sha256(
                    f"{provider_id}:{account_id}:{raw_id}:{now_iso}".encode("utf-8")
                ).hexdigest()
                provenance = DiscoveryProvenance(
                    source_id=f"account_{provider_id}_{account_id}",
                    trust_class=self.trust_class,
                    discovered_at=now_iso,
                    collector="AccountEntitlementDiscoveryAdapter",
                    evidence_digest=evidence_hash,
                    raw_metadata_summary={"account_id": account_id, "provider_id": provider_id},
                )
                discovered.append(
                    DiscoveredModel(
                        raw_model_id=raw_id,
                        provider_id=provider_id,
                        account_id=account_id,
                        display_name=m.get("display_name", f"{provider_id}:{account_id} / {raw_id}"),
                        declared_vendor=m.get("vendor"),
                        declared_family=m.get("family"),
                        declared_architecture=m.get("architecture"),
                        context_window=m.get("context_window", 8192),
                        is_free=m.get("is_free", False),
                        source_type=m.get("source_type", "official_subscription"),
                        entitlement_state=entitlement,
                        input_cost_per_million=m.get("input_cost_per_million", 0.0),
                        output_cost_per_million=m.get("output_cost_per_million", 0.0),
                        capabilities=m.get("capabilities", {}),
                        endpoint=acc.get("endpoint", ""),
                        auth_mode=acc.get("auth_mode", "api_key"),
                        provenance=provenance,
                    )
                )
        return discovered


# ---------------------------------------------------------------------------
# Robust Model Identity Resolver (Conservative & Evidence-Based)
# ---------------------------------------------------------------------------

KNOWN_CANONICAL_VENDORS: Mapping[str, str] = {
    "meta": "meta",
    "meta-llama": "meta",
    "llama": "meta",
    "google": "google",
    "anthropic": "anthropic",
    "openai": "openai",
    "mistral": "mistral",
    "mistralai": "mistral",
    "deepseek": "deepseek",
    "deepseek-ai": "deepseek",
    "qwen": "qwen",
    "alibaba": "qwen",
    "microsoft": "microsoft",
    "cohere": "cohere",
    "xai": "xai",
    "cerebras": "cerebras",
    "groq": "groq",
}

KNOWN_FAMILY_PATTERNS: Sequence[Tuple[str, str, str]] = [
    # (regex_pattern, canonical_vendor, canonical_family)
    (r"(?i)llama[-_.]?3\.3[-_.:]?70b", "meta", "llama-3.3-70b"),
    (r"(?i)llama[-_.]?3\.1[-_.:]?70b", "meta", "llama-3.1-70b"),
    (r"(?i)llama[-_.]?3\.1[-_.:]?8b", "meta", "llama-3.1-8b"),
    (r"(?i)llama[-_.]?3[-_.:]?70b", "meta", "llama-3-70b"),
    (r"(?i)llama[-_.]?3[-_.:]?8b", "meta", "llama-3-8b"),
    (r"(?i)qwen[-_]?2\.5[-_]?coder[-_]?32b", "qwen", "qwen-2.5-coder-32b"),
    (r"(?i)qwen[-_]?2\.5[-_]?72b", "qwen", "qwen-2.5-72b"),
    (r"(?i)deepseek[-_]?r1", "deepseek", "deepseek-r1"),
    (r"(?i)deepseek[-_]?v3", "deepseek", "deepseek-v3"),
    (r"(?i)claude[-_]?3[-_]?5[-_]?sonnet", "anthropic", "claude-3.5-sonnet"),
    (r"(?i)claude[-_]?3[-_]?5[-_]?haiku", "anthropic", "claude-3.5-haiku"),
    (r"(?i)claude[-_]?3[-_]?opus", "anthropic", "claude-3-opus"),
    (r"(?i)gemini[-_.]?2\.5[-_.]?flash", "google", "gemini-2.5-flash"),
    (r"(?i)gemini[-_.]?2\.5[-_.]?pro", "google", "gemini-2.5-pro"),
    (r"(?i)gemini[-_.]?2\.0[-_.]?flash", "google", "gemini-2.0-flash"),
    (r"(?i)gemini[-_.]?1\.5[-_.]?pro", "google", "gemini-1.5-pro"),
    (r"(?i)gemini[-_.]?1\.5[-_.]?flash", "google", "gemini-1.5-flash"),
    (r"(?i)gpt[-_]?4o[-_]?mini", "openai", "gpt-4o-mini"),
    (r"(?i)gpt[-_]?4o", "openai", "gpt-4o"),
    (r"(?i)o1[-_]?mini", "openai", "o1-mini"),
    (r"(?i)o1", "openai", "o1"),
    (r"(?i)mistral[-_]?large", "mistral", "mistral-large"),
    (r"(?i)mistral[-_]?small", "mistral", "mistral-small"),
    (r"(?i)codestral", "mistral", "codestral"),
]


@dataclass(frozen=True)
class ResolvedModelIdentity:
    canonical_id: str
    vendor: str
    family: str
    architecture: str


class ModelIdentityResolver:
    """Conservative, multi-stage identity resolution engine.

    Resolution hierarchy:
      exact declared metadata -> regex family pattern -> conservative token matching -> UNKNOWN.
    Never merges models based on vague heuristics or loose substring collisions.
    """

    @classmethod
    def resolve_vendor(cls, declared_vendor: Optional[str], raw_model_id: str, provider_id: str) -> str:
        if declared_vendor and declared_vendor.lower() in KNOWN_CANONICAL_VENDORS:
            return KNOWN_CANONICAL_VENDORS[declared_vendor.lower()]

        # Check raw_model_id prefix e.g. "meta-llama/..." or "deepseek/..."
        if "/" in raw_model_id:
            prefix = raw_model_id.split("/", 1)[0].lower()
            if prefix in KNOWN_CANONICAL_VENDORS:
                return KNOWN_CANONICAL_VENDORS[prefix]

        # Check provider
        if provider_id.lower() in KNOWN_CANONICAL_VENDORS:
            return KNOWN_CANONICAL_VENDORS[provider_id.lower()]

        return "unknown"

    @classmethod
    def resolve_canonical_id(cls, model: DiscoveredModel) -> Tuple[str, str, str, str]:
        """Returns (canonical_id, vendor, family, architecture)."""
        raw_id = model.raw_model_id
        vendor = cls.resolve_vendor(model.declared_vendor, raw_id, model.provider_id)

        # 1. Check exact pattern matches
        for pattern, pat_vendor, canonical_family in KNOWN_FAMILY_PATTERNS:
            if re.search(pattern, raw_id):
                canonical_id = canonical_family
                resolved_vendor = pat_vendor or vendor
                family = canonical_family.rsplit("-", 1)[0] if "-" in canonical_family else canonical_family
                arch = model.declared_architecture or cls.infer_architecture(canonical_id)
                return canonical_id, resolved_vendor, family, arch

        # 2. Check declared metadata if explicit
        if model.declared_family:
            canonical_id = f"{model.declared_family.lower()}"
            arch = model.declared_architecture or cls.infer_architecture(canonical_id)
            return canonical_id, vendor, model.declared_family.lower(), arch

        # 3. Conservative Fallback: Keep provider/raw_id distinct, do NOT guess
        clean_raw = re.sub(r"[^a-zA-Z0-9_.-]", "-", raw_id).strip("-").lower()
        return clean_raw, vendor, clean_raw, model.declared_architecture or "dense_transformer"

    @classmethod
    def resolve(cls, model: DiscoveredModel) -> ResolvedModelIdentity:
        c_id, vendor, family, arch = cls.resolve_canonical_id(model)
        return ResolvedModelIdentity(canonical_id=c_id, vendor=vendor, family=family, architecture=arch)

    @classmethod
    def resolve_route(cls, model: DiscoveredModel) -> Dict[str, Any]:
        c_id, vendor, family, arch = cls.resolve_canonical_id(model)
        route_id = f"{c_id}@{model.provider_id}:{model.account_id}"
        return {
            "route_id": route_id,
            "canonical_id": c_id,
            "vendor": vendor,
            "family": family,
            "architecture": arch,
            "provider_id": model.provider_id,
            "account_id": model.account_id,
        }

    @classmethod
    def infer_architecture(cls, canonical_id: str) -> str:
        c_id = canonical_id.lower()
        if "r1" in c_id or "o1" in c_id or "reasoning" in c_id:
            return "reasoning_rl"
        if "moe" in c_id or "mixtral" in c_id or "deepseek-v3" in c_id:
            return "mixture_of_experts"
        if "flash" in c_id or "vision" in c_id or "gemini" in c_id:
            return "multimodal_transformer"
        return "dense_transformer"



# ---------------------------------------------------------------------------
# Evidence-Backed Task Fitness Engine (Decoupled from Certification)
# ---------------------------------------------------------------------------

TASK_DOMAIN_WEIGHTS: Mapping[str, Mapping[str, float]] = {
    "coding": {
        "coding": 0.60,
        "reasoning": 0.25,
        "context_bonus": 0.15,
    },
    "reasoning": {
        "reasoning": 0.65,
        "general": 0.20,
        "context_bonus": 0.15,
    },
    "research": {
        "reasoning": 0.35,
        "general": 0.35,
        "context_bonus": 0.30,
    },
    "vision": {
        "vision": 0.65,
        "general": 0.20,
        "context_bonus": 0.15,
    },
    "general": {
        "general": 0.40,
        "reasoning": 0.30,
        "coding": 0.30,
    },
}


@dataclass(frozen=True)
class QualityEvidence:
    external_benchmark_score: float
    observed_reliability: float = 1.0
    observed_latency_p50_ms: float = 300.0
    freshness_factor: float = 1.0  # Decays over time (1.0 = brand new, 0.5 = old)
    confidence: float = 0.90

    def compute_composite_quality(self) -> float:
        # Weighted composite score
        score = (
            (self.external_benchmark_score * 0.60)
            + (self.observed_reliability * 0.25)
            + (self.confidence * 0.15)
        )
        return min(1.0, max(0.0, score * self.freshness_factor))


class TaskFitnessEngine:
    """Calculates model suitability for specific task types independent of route certification."""

    @classmethod
    def calculate_fitness(
        cls,
        task_type: str,
        capabilities: Union[Mapping[str, float], Iterable[str]],
        context_window: int,
        evidence: Optional[QualityEvidence] = None,
    ) -> float:
        weights = TASK_DOMAIN_WEIGHTS.get(task_type, TASK_DOMAIN_WEIGHTS["general"])
        score = 0.0

        if isinstance(capabilities, (set, frozenset, list, tuple)):
            caps_map: Mapping[str, float] = {str(k): 1.0 for k in capabilities}
        elif isinstance(capabilities, dict):
            caps_map = capabilities
        else:
            caps_map = {}

        # Context factor: normalize context window to 0.0-1.0 scale (up to 128k)
        context_norm = min(1.0, context_window / 131072.0)

        for cap_key, weight in weights.items():
            if cap_key == "context_bonus":
                score += weight * context_norm
            else:
                fallback_key = "code" if cap_key == "coding" else "coding" if cap_key == "code" else cap_key
                cap_val = caps_map.get(cap_key, caps_map.get(fallback_key, 0.0))
                score += weight * cap_val

        if evidence is not None:
            # Modulate with composite quality evidence
            composite = evidence.compute_composite_quality()
            score = (score * 0.70) + (composite * 0.30)

        return round(min(1.0, max(0.0, score)), 4)




# ---------------------------------------------------------------------------
# Tiered Diversity Policy Engine
# ---------------------------------------------------------------------------

class DiversityPolicyLevel(str, Enum):
    STRICT = "strict"
    BALANCED = "balanced"
    RELAXED = "relaxed"


@dataclass(frozen=True)
class DiversityDecision:
    permitted: bool
    policy_level: DiversityPolicyLevel
    violations: List[str] = field(default_factory=list)
    diversity_score: float = 1.0

    @property
    def reasons(self) -> List[str]:
        return list(self.violations)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "permitted": self.permitted,
            "policy_level": self.policy_level.value,
            "violations": list(self.violations),
            "reasons": list(self.violations),
            "diversity_score": self.diversity_score,
        }



class DiversityPolicyEngine:
    """Evaluates multi-model team compositions against configurable diversity policy levels."""

    @classmethod
    def evaluate(
        cls,
        policy_level: DiversityPolicyLevel,
        producer_vendor: str,
        producer_family: str,
        producer_arch: str,
        producer_provider: str,
        candidate_vendor: str,
        candidate_family: str,
        candidate_arch: str,
        candidate_provider: str,
        role: str = "verifier",
    ) -> DiversityDecision:
        violations: List[str] = []

        # Identical route is always a violation
        if producer_provider == candidate_provider and producer_family == candidate_family:
            if role in {"reviewer", "verifier"}:
                violations.append("Candidate is identical to producer in provider and model family.")

        if policy_level == DiversityPolicyLevel.STRICT:
            if producer_vendor == candidate_vendor:
                violations.append(f"Strict policy: Producer and {role} share identical vendor '{producer_vendor}'.")
            if producer_family == candidate_family:
                violations.append(f"Strict policy: Producer and {role} share identical model family '{producer_family}'.")
            if producer_arch == candidate_arch and producer_arch != "dense_transformer":
                violations.append(f"Strict policy: Producer and {role} share identical specialized architecture '{producer_arch}'.")

        elif policy_level == DiversityPolicyLevel.BALANCED:
            # Balanced permits same vendor if architectures or tiers differ (e.g. Gemini Flash + Gemini Pro)
            if producer_vendor == candidate_vendor and producer_family == candidate_family and producer_provider == candidate_provider:
                violations.append(f"Balanced policy: Producer and {role} share exact provider and model family.")

        elif policy_level == DiversityPolicyLevel.RELAXED:
            # Relaxed only requires different routes or accounts
            pass

        score = 1.0 - (len(violations) * 0.40)
        score = max(0.0, score)
        return DiversityDecision(
            permitted=(len(violations) == 0),
            policy_level=policy_level,
            violations=violations,
            diversity_score=score,
        )


# ---------------------------------------------------------------------------
# Route Certification Lifecycle Model
# ---------------------------------------------------------------------------

class CertificationStatus(str, Enum):
    DISCOVERED = "discovered"
    NORMALIZED = "normalized"
    OBSERVED = "observed"
    PROBE_PASSED = "probe_passed"
    BENCHMARK_PASSED = "benchmark_passed"
    CERTIFIED = "certified"
    EXPIRED = "expired"
    REVOKED = "revoked"


@dataclass
class RouteCertificationRecord:
    route_id: str
    status: CertificationStatus
    certified_at: str
    expires_at: str
    last_verified_at: str
    evidence_hash: str
    model_identity_hash: str
    route_config_hash: str
    source_provenance: Dict[str, Any]
    certification_policy: str = "standard"
    probe_profile: str = "offline_contract"
    capabilities: Dict[str, float] = field(default_factory=dict)
    latency_p50_ms: Optional[float] = None
    latency_p95_ms: Optional[float] = None
    measurement_source: str = "none"  # "none" | "live_probe" | "synthetic_estimate"
    quota_observed: Dict[str, Any] = field(default_factory=dict)
    revocation_reason: Optional[str] = None

    def is_active(self, current_time_iso: Optional[str] = None) -> bool:
        if self.status != CertificationStatus.CERTIFIED:
            return False
        now_str = current_time_iso or datetime.now(timezone.utc).isoformat()
        return now_str < self.expires_at

    def as_dict(self) -> Dict[str, Any]:
        return {
            "route_id": self.route_id,
            "status": self.status.value if isinstance(self.status, CertificationStatus) else str(self.status),
            "certified_at": self.certified_at,
            "expires_at": self.expires_at,
            "last_verified_at": self.last_verified_at,
            "evidence_hash": self.evidence_hash,
            "model_identity_hash": self.model_identity_hash,
            "route_config_hash": self.route_config_hash,
            "source_provenance": dict(self.source_provenance),
            "certification_policy": self.certification_policy,
            "probe_profile": self.probe_profile,
            "capabilities": dict(self.capabilities),
            "latency_p50_ms": self.latency_p50_ms,
            "latency_p95_ms": self.latency_p95_ms,
            "measurement_source": self.measurement_source,
            "quota_observed": dict(self.quota_observed),
            "revocation_reason": self.revocation_reason,
            "is_active": self.is_active(),
        }


# ---------------------------------------------------------------------------
# Discovery & Certification Orchestrator
# ---------------------------------------------------------------------------

class AutonomousDiscoveryEngine:
    """Orchestrates multi-source intelligence discovery, conservative resolution, and certification."""

    def __init__(self, adapters: Optional[Sequence[BaseDiscoveryAdapter]] = None) -> None:
        self.adapters = list(adapters or [
            LocalRuntimeDiscoveryAdapter(),
            PublicCatalogDiscoveryAdapter(),
            AccountEntitlementDiscoveryAdapter(),
        ])

    def scan(
        self,
        include_local: bool = True,
        include_catalogs: bool = True,
        include_accounts: bool = True,
    ) -> List[DiscoveredModel]:
        """Runs governed discovery across authorized adapters."""
        all_discovered: List[DiscoveredModel] = []

        for adapter in self.adapters:
            if isinstance(adapter, LocalRuntimeDiscoveryAdapter) and not include_local:
                continue
            if isinstance(adapter, PublicCatalogDiscoveryAdapter) and not include_catalogs:
                continue
            if isinstance(adapter, AccountEntitlementDiscoveryAdapter) and not include_accounts:
                continue

            # Verify discovery operation is permitted for this trust class
            if not is_operation_permitted(adapter.trust_class, "discover_metadata"):
                continue


            models = adapter.discover()
            all_discovered.extend(models)

        return all_discovered

    @staticmethod
    def _infer_quality_tier(model: DiscoveredModel) -> str:
        """Infer quality tier from declared evidence only — never from name heuristics.

        Rules:
          - If model has declared capability scores, infer from average score.
          - avg >= 0.90 → 'frontier'
          - avg >= 0.82 → 'strong'
          - avg >= 0.70 → 'fast'
          - avg >= 0.50 → 'light'
          - Otherwise → 'unknown' (no evidence to make a claim).
        """
        caps = model.capabilities
        if not caps:
            return "unknown"
        scores = [v for v in caps.values() if isinstance(v, (int, float))]
        if not scores:
            return "unknown"
        avg = sum(scores) / len(scores)
        if avg >= 0.90:
            return "frontier"
        if avg >= 0.82:
            return "strong"
        if avg >= 0.70:
            return "fast"
        if avg >= 0.50:
            return "light"
        return "unknown"

    def resolve_and_project(
        self,
        discovered_models: Sequence[DiscoveredModel],
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Resolves models into normalized identities and routes.

        Ensures:
          1. Same model through 3 providers resolves to 1 canonical ModelIdentity.
          2. Same provider with 3 accounts produces 3 distinct ModelRoutes.
        """
        identities_map: Dict[str, Dict[str, Any]] = {}
        routes_list: List[Dict[str, Any]] = []

        for model in discovered_models:
            canonical_id, vendor, family, arch = ModelIdentityResolver.resolve_canonical_id(model)

            # Build or update canonical ModelIdentity
            if canonical_id not in identities_map:
                identities_map[canonical_id] = {
                    "canonical_id": canonical_id,
                    "display_name": model.display_name or canonical_id,
                    "vendor": vendor,
                    "family": family,
                    "architecture": arch,
                    "quality_tier": self._infer_quality_tier(model),
                    "context_window": model.context_window,
                    "capabilities": dict(model.capabilities),
                    "aliases": [model.raw_model_id],
                }
            else:
                existing = identities_map[canonical_id]
                if model.raw_model_id not in existing["aliases"]:
                    existing["aliases"].append(model.raw_model_id)
                # Expand context if a route provides higher context window
                if model.context_window > existing["context_window"]:
                    existing["context_window"] = model.context_window

            # Build distinct ModelRoute
            route_id = f"{canonical_id}@{model.provider_id}:{model.account_id}"
            route_dict = {
                "route_id": route_id,
                "model_identity_id": canonical_id,
                "provider_id": model.provider_id,
                "account_id": model.account_id,
                "model_id_at_provider": model.raw_model_id,
                "source_type": model.source_type,
                "entitlement_state": model.entitlement_state.value if isinstance(model.entitlement_state, EntitlementState) else str(model.entitlement_state),
                "is_free": model.is_free,
                "cost": {
                    "input_per_million": model.input_cost_per_million,
                    "output_per_million": model.output_cost_per_million,
                },
                "quota": {
                    "state": "available" if model.is_free else "unknown",
                    "remaining_requests": None,
                    "remaining_tokens": None,
                },
                "auth_mode": model.auth_mode,
                "api_key_env": model.api_key_env,
                "endpoint": model.endpoint,
                "health": "healthy" if model.is_free else "unknown",
                "provenance": model.provenance.as_dict() if model.provenance else None,
            }
            routes_list.append(route_dict)

        return list(identities_map.values()), routes_list
