"""Provider, Credential, and ModelProfile data model for NEXUS.

This module separates the concerns that the original NEXUS model adapter layer
conflated:

    Provider
        -> Credential / API Account
            -> Endpoint
                -> Available Models
                    -> Model Profile / Contract

A credential is *never* an API key; it is a reference (``credential_ref``) to a
secret that is resolved only at execution time.  Records and telemetry may
persist ``credential_ref`` but never the secret material itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Mapping, Optional, Sequence
import abc


NVIDIA_NIM_BASE_URL = "https://integrate.api.nvidia.com/v1"


@dataclass(frozen=True)
class ProviderConfig:
    """A logical provider (e.g. NVIDIA NIM) and its default endpoint."""

    provider_id: str
    provider_type: str = "openai_compatible"  # e.g. "openai_compatible"
    default_endpoint: str = NVIDIA_NIM_BASE_URL
    endpoint_configuration: Mapping[str, Any] = field(default_factory=dict)
    supported_authentication_mechanism: str = "bearer_token"

    def redacted_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "provider_type": self.provider_type,
            "default_endpoint": self.default_endpoint,
            "endpoint_configuration": dict(self.endpoint_configuration),
            "supported_authentication_mechanism": self.supported_authentication_mechanism,
        }


@dataclass(frozen=True)
class Credential:
    """An API account reference. The secret itself is never stored here."""

    credential_ref: str
    provider_id: str
    secret_ref: str  # env var name or vault key reference
    label: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def redacted_dict(self) -> dict[str, Any]:
        return {
            "credential_ref": self.credential_ref,
            "provider_id": self.provider_id,
            "secret_ref": self.secret_ref,
            "label": self.label,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class Capabilities:
    streaming: bool = True
    tool_calling: bool = True
    structured_output: bool = True
    multimodal: bool = False


@dataclass(frozen=True)
class ModelProfile:
    """Model-independent contract: transport policy + model-specific parameters.

    This object must never contain an API key or credential secret.
    """

    provider_id: str
    model_id: str
    display_name: str
    capabilities: Capabilities = field(default_factory=Capabilities)
    # Transport policy
    streaming_preferred: bool = True
    timeout_seconds: float = 120.0
    min_max_tokens: int = 4096
    # Model-specific parameters (wire contract)
    default_temperature: float = 1.0
    default_top_p: float = 0.95
    reasoning_effort: Optional[str] = None
    chat_template_kwargs: Optional[Mapping[str, Any]] = None
    extra_body: Mapping[str, Any] = field(default_factory=dict)

    def redacted_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "display_name": self.display_name,
            "capabilities": {
                "streaming": self.capabilities.streaming,
                "tool_calling": self.capabilities.tool_calling,
                "structured_output": self.capabilities.structured_output,
                "multimodal": self.capabilities.multimodal,
            },
            "streaming_preferred": self.streaming_preferred,
            "timeout_seconds": self.timeout_seconds,
            "min_max_tokens": self.min_max_tokens,
            "default_temperature": self.default_temperature,
            "default_top_p": self.default_top_p,
            "reasoning_effort": self.reasoning_effort,
            "chat_template_kwargs": dict(self.chat_template_kwargs) if self.chat_template_kwargs else None,
            "extra_body": dict(self.extra_body),
        }


# ---------------------------------------------------------------------------
# Built-in NVIDIA NIM provider + known model profiles.
# ---------------------------------------------------------------------------

NVIDIA_PROVIDER = ProviderConfig(
    provider_id="nvidia",
    provider_type="openai_compatible",
    default_endpoint=NVIDIA_NIM_BASE_URL,
)


def _profile(
    model_id: str,
    display_name: str,
    *,
    streaming_preferred: bool = True,
    timeout_seconds: float = 120.0,
    min_max_tokens: int = 4096,
    default_temperature: float = 1.0,
    default_top_p: float = 0.95,
    reasoning_effort: Optional[str] = None,
    chat_template_kwargs: Optional[Mapping[str, Any]] = None,
    capabilities: Optional[Capabilities] = None,
) -> ModelProfile:
    return ModelProfile(
        provider_id=NVIDIA_PROVIDER.provider_id,
        model_id=model_id,
        display_name=display_name,
        capabilities=capabilities or Capabilities(),
        streaming_preferred=streaming_preferred,
        timeout_seconds=timeout_seconds,
        min_max_tokens=min_max_tokens,
        default_temperature=default_temperature,
        default_top_p=default_top_p,
        reasoning_effort=reasoning_effort,
        chat_template_kwargs=chat_template_kwargs,
    )


KIMI_K3_PROFILE = _profile(
    model_id="moonshotai/kimi-k3",
    display_name="Kimi K3",
    streaming_preferred=True,
    timeout_seconds=90.0,
    min_max_tokens=8192,
    default_temperature=1.0,
    default_top_p=0.95,
    reasoning_effort="max",
)

MUSE_GLIMMER_PROFILE = _profile(
    model_id="meta/muse-glimmer-30b",
    display_name="Muse (Glimmer 30B)",
    streaming_preferred=False,
    timeout_seconds=120.0,
    min_max_tokens=4096,
    default_temperature=0.7,
    default_top_p=0.95,
)

DEEPSEEK_V4_PRO_PROFILE = _profile(
    model_id="deepseek-ai/deepseek-v4-pro-0813",
    display_name="DeepSeek V4 Pro",
    streaming_preferred=True,
    timeout_seconds=240.0,
    min_max_tokens=16384,
    default_temperature=1.0,
    default_top_p=0.95,
    chat_template_kwargs={"thinking": False},
)

_MUTABLE_MODEL_PROFILES: dict[str, ModelProfile] = {
    KIMI_K3_PROFILE.model_id: KIMI_K3_PROFILE,
    MUSE_GLIMMER_PROFILE.model_id: MUSE_GLIMMER_PROFILE,
    DEEPSEEK_V4_PRO_PROFILE.model_id: DEEPSEEK_V4_PRO_PROFILE,
}

KNOWN_MODEL_PROFILES: Mapping[str, ModelProfile] = _MUTABLE_MODEL_PROFILES

# Convenience lookup by short family key for backward compatibility.
FAMILY_TO_MODEL_ID: dict[str, str] = {
    "kimi": KIMI_K3_PROFILE.model_id,
    "muse": MUSE_GLIMMER_PROFILE.model_id,
    "deepseek": DEEPSEEK_V4_PRO_PROFILE.model_id,
}

_MUTABLE_PROVIDERS: dict[str, ProviderConfig] = {
    NVIDIA_PROVIDER.provider_id: NVIDIA_PROVIDER,
}

KNOWN_PROVIDERS: Mapping[str, ProviderConfig] = _MUTABLE_PROVIDERS


def register_provider(provider: ProviderConfig) -> ProviderConfig:
    """Register or update a ProviderConfig."""
    _MUTABLE_PROVIDERS[provider.provider_id] = provider
    return provider


def list_providers() -> list[ProviderConfig]:
    """List all registered providers."""
    return list(_MUTABLE_PROVIDERS.values())


def get_provider(provider_id: str) -> ProviderConfig:
    if provider_id not in _MUTABLE_PROVIDERS:
        raise ValueError(f"Unknown provider: '{provider_id}'. Known: {sorted(_MUTABLE_PROVIDERS)}")
    return _MUTABLE_PROVIDERS[provider_id]


def register_model_profile(profile: ModelProfile, short_alias: Optional[str] = None) -> ModelProfile:
    """Register or update a ModelProfile."""
    _MUTABLE_MODEL_PROFILES[profile.model_id] = profile
    if short_alias:
        FAMILY_TO_MODEL_ID[short_alias.lower()] = profile.model_id
    return profile


def list_model_profiles(provider_id: Optional[str] = None) -> list[ModelProfile]:
    """List registered ModelProfiles, optionally filtered by provider."""
    profiles = list(_MUTABLE_MODEL_PROFILES.values())
    if provider_id is not None:
        return [p for p in profiles if p.provider_id == provider_id]
    return profiles


def get_model_profile(model_id_or_key: str) -> ModelProfile:
    """Resolve a ModelProfile by full model id or short family key."""
    lowered = model_id_or_key.lower()
    if lowered in _MUTABLE_MODEL_PROFILES:
        return _MUTABLE_MODEL_PROFILES[lowered]
    if lowered in FAMILY_TO_MODEL_ID:
        return _MUTABLE_MODEL_PROFILES[FAMILY_TO_MODEL_ID[lowered]]
    # Dynamic fallback for arbitrary catalog models:
    if "/" in model_id_or_key:
        dyn_profile = _profile(
            model_id=model_id_or_key,
            display_name=model_id_or_key.split("/")[-1].replace("-", " ").title(),
            streaming_preferred=True,
            timeout_seconds=90.0,
            min_max_tokens=4096,
        )
        register_model_profile(dyn_profile)
        return dyn_profile

    raise ValueError(f"Unknown model identifier: '{model_id_or_key}'. Expected one of: kimi, muse, deepseek, or a provider/model path.")


# ---------------------------------------------------------------------------
# Credential Selection Policy Abstraction
# ---------------------------------------------------------------------------


class CredentialSelector(abc.ABC):
    """Abstract strategy for choosing which credential to bind to a model evaluation.

    Extensible for policies such as EXPLICIT, AUTO, LOAD_BALANCED,
    LEAST_RATE_LIMIT_PRESSURE, ROUND_ROBIN, or FAILOVER.
    """

    @abc.abstractmethod
    def select(
        self,
        provider_id: str,
        model_id: str,
        available_credentials: Sequence[Credential],
        entitlements: Optional[Mapping[str, Sequence[str]]] = None,
    ) -> Credential:
        """Select an authorized credential for the given provider and model."""
        pass


class ExplicitCredentialSelector(CredentialSelector):
    """EXPLICIT mode: selects a specifically requested credential_ref."""

    def __init__(self, credential_ref: str) -> None:
        self.credential_ref = credential_ref

    def select(
        self,
        provider_id: str,
        model_id: str,
        available_credentials: Sequence[Credential],
        entitlements: Optional[Mapping[str, Sequence[str]]] = None,
    ) -> Credential:
        for cred in available_credentials:
            if cred.credential_ref == self.credential_ref:
                if entitlements is not None:
                    allowed = entitlements.get(cred.credential_ref, [])
                    if allowed and model_id not in allowed:
                        raise PermissionError(
                            f"Credential '{self.credential_ref}' is not entitled for model '{model_id}'"
                        )
                return cred
        raise ValueError(
            f"Explicit credential '{self.credential_ref}' not found in available credentials: "
            f"{[c.credential_ref for c in available_credentials]}"
        )


class AutoCredentialSelector(CredentialSelector):
    """AUTO mode: selects a credential that is authorized for the requested model."""

    def __init__(self, preferred_credentials: Optional[Sequence[str]] = None) -> None:
        self.preferred_credentials = list(preferred_credentials) if preferred_credentials else []

    def select(
        self,
        provider_id: str,
        model_id: str,
        available_credentials: Sequence[Credential],
        entitlements: Optional[Mapping[str, Sequence[str]]] = None,
    ) -> Credential:
        candidates = [c for c in available_credentials if c.provider_id == provider_id]
        if not candidates:
            raise ValueError(f"No configured credentials available for provider '{provider_id}'")

        # Re-order by preference if specified
        if self.preferred_credentials:
            pref_order = {ref: idx for idx, ref in enumerate(self.preferred_credentials)}
            candidates.sort(key=lambda c: pref_order.get(c.credential_ref, 999))

        # Check entitlements if known
        if entitlements:
            for cred in candidates:
                allowed_models = entitlements.get(cred.credential_ref, [])
                if model_id in allowed_models:
                    return cred

        # Fallback to the first provider candidate
        return candidates[0]