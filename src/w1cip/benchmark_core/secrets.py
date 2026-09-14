"""Zero-leak API key security, startup validation, and secret masking."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .profiles import Credential


ENV_VAR_MAPPINGS = {
    "kimi": ("NVIDIA_API_KEY_KIMI", "NVIDIA_API_KEY"),
    "muse": ("NVIDIA_API_KEY_MUSE", "NVIDIA_API_KEY"),
    "deepseek": ("NVIDIA_API_KEY_DEEPSEEK", "NVIDIA_API_KEY"),
}

# Normalized credential registry.  A credential_ref maps to a secret source
# (environment variable name).  The *same* underlying key may back several
# credentials; we never assume one model == one distinct API account.
#
# Credential ids intentionally mirror the legacy environment variables so the
# existing .env layout continues to work unchanged.
CREDENTIAL_REGISTRY: dict[str, str] = {
    "nvidia-main": "NVIDIA_API_KEY",
    "nvidia-kimi": "NVIDIA_API_KEY_KIMI",
    "nvidia-muse": "NVIDIA_API_KEY_MUSE",
    "nvidia-deepseek": "NVIDIA_API_KEY_DEEPSEEK",
}

_CREDENTIAL_OBJECTS: dict[str, Credential] = {
    ref: Credential(credential_ref=ref, provider_id="nvidia", secret_ref=s_ref, label=f"Default {ref}")
    for ref, s_ref in CREDENTIAL_REGISTRY.items()
}

# In-memory registry of active secrets (for redaction only, NEVER serialized)
_REGISTERED_SECRETS: set[str] = set()


def load_env_file_if_present(env_path: Optional[Path] = None) -> None:
    """Safely loads simple key=value pairs from a local .env file if it exists."""
    if env_path is None:
        # Check standard project roots
        candidates = [
            Path(".env"),
            Path("../.env"),
            Path(__file__).resolve().parent.parent.parent.parent / ".env",
            Path(__file__).resolve().parent.parent.parent.parent.parent / ".env",
        ]
        for candidate in candidates:
            if candidate.is_file():
                env_path = candidate
                break

    if env_path and env_path.is_file():
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, val = line.split("=", 1)
                    key = key.strip()
                    val = val.strip().strip("'\"")
                    if key and key not in os.environ:
                        os.environ[key] = val
        except Exception:
            pass


# Automatically probe local .env on import
load_env_file_if_present()


def resolve_api_key(model_family_or_id: str) -> Optional[str]:
    """Resolves the appropriate API key without exposing it."""
    key_low = model_family_or_id.lower()
    env_keys: Tuple[str, ...] = ("NVIDIA_API_KEY",)
    for model_type, keys in ENV_VAR_MAPPINGS.items():
        if model_type in key_low:
            env_keys = keys
            break

    for env_key in env_keys:
        val = os.environ.get(env_key)
        if val and val.strip():
            secret = val.strip()
            if len(secret) > 8:
                _REGISTERED_SECRETS.add(secret)
            return secret
    return None


def register_secret_for_redaction(secret: str) -> None:
    """Registers a secret string to ensure it is scrubbed from any output."""
    if secret and len(secret) > 6:
        _REGISTERED_SECRETS.add(secret)


def mask_secret(secret: Optional[str]) -> str:
    """Returns a safe, masked representation of a secret (e.g. nvapi-Fk...IQdi)."""
    if not secret:
        return "[NOT CONFIGURED]"
    clean = secret.strip()
    if len(clean) <= 12:
        return "[CONFIGURED: ***]"
    return f"{clean[:8]}...{clean[-4:]}"


def validate_secret_presence(model_family_or_id: str) -> dict[str, Any]:
    """Validates availability of the required API key without exposing the value."""
    key = resolve_api_key(model_family_or_id)
    if not key:
        return {
            "valid": False,
            "configured": False,
            "masked_key": "[MISSING]",
            "error": "No API key found in environment variables (checked NVIDIA_API_KEY and model-specific overrides)",
        }
    
    if not key.startswith("nvapi-"):
        return {
            "valid": False,
            "configured": True,
            "masked_key": mask_secret(key),
            "error": "Key format invalid: NVIDIA NIM keys are expected to start with 'nvapi-'",
        }
    
    return {
        "valid": True,
        "configured": True,
        "masked_key": mask_secret(key),
        "error": None,
    }


def resolve_secret(secret_ref: str) -> Optional[str]:
    """Resolve a secret value by its reference (env var name) without exposing it."""
    val = os.environ.get(secret_ref)
    if val and val.strip():
        secret = val.strip()
        if len(secret) > 8:
            _REGISTERED_SECRETS.add(secret)
        return secret
    return None


def secret_ref_for_credential(credential_ref: str) -> Optional[str]:
    """Map a normalized credential_ref to its secret source, if registered."""
    return CREDENTIAL_REGISTRY.get(credential_ref)


def register_credential(
    credential_ref: str,
    secret_ref: str,
    provider_id: str = "nvidia",
    label: str = "",
    metadata: Optional[Mapping[str, Any]] = None,
) -> Credential:
    """Dynamically register a Credential and its secret reference."""
    CREDENTIAL_REGISTRY[credential_ref] = secret_ref
    cred = Credential(
        credential_ref=credential_ref,
        provider_id=provider_id,
        secret_ref=secret_ref,
        label=label,
        metadata=dict(metadata or {}),
    )
    _CREDENTIAL_OBJECTS[credential_ref] = cred
    return cred


def get_credential(credential_ref: str) -> Optional[Credential]:
    """Return the Credential object for a given credential_ref."""
    if credential_ref in _CREDENTIAL_OBJECTS:
        return _CREDENTIAL_OBJECTS[credential_ref]
    secret_ref = CREDENTIAL_REGISTRY.get(credential_ref)
    if secret_ref:
        cred = Credential(
            credential_ref=credential_ref,
            provider_id="nvidia",
            secret_ref=secret_ref,
            label=credential_ref,
        )
        _CREDENTIAL_OBJECTS[credential_ref] = cred
        return cred
    return None


def list_credentials(provider_id: Optional[str] = None) -> list[Credential]:
    """Return all registered Credential objects, optionally filtered by provider."""
    # Ensure any keys in CREDENTIAL_REGISTRY have objects in _CREDENTIAL_OBJECTS
    for ref, s_ref in CREDENTIAL_REGISTRY.items():
        if ref not in _CREDENTIAL_OBJECTS:
            _CREDENTIAL_OBJECTS[ref] = Credential(
                credential_ref=ref,
                provider_id="nvidia" if ref.startswith("nvidia-") else "unknown",
                secret_ref=s_ref,
                label=ref,
            )
    creds = list(_CREDENTIAL_OBJECTS.values())
    if provider_id is not None:
        return [c for c in creds if c.provider_id == provider_id]
    return creds


def resolve_credential_key(credential_ref: str) -> Optional[str]:
    """Resolve the actual secret value for a normalized credential_ref."""
    secret_ref = CREDENTIAL_REGISTRY.get(credential_ref)
    if not secret_ref:
        return None
    return resolve_secret(secret_ref)


def list_credential_refs(provider_id: str = "nvidia") -> list[str]:
    """Return registered credential refs for a provider (metadata only, no secrets)."""
    creds = list_credentials(provider_id)
    return [c.credential_ref for c in creds]


def scrub_secrets(obj: Any) -> Any:
    """Recursively scrubs all registered secrets from dictionaries, lists, and strings."""
    if not _REGISTERED_SECRETS:
        return obj

    if isinstance(obj, str):
        scrubbed = obj
        for secret in _REGISTERED_SECRETS:
            if secret in scrubbed:
                scrubbed = scrubbed.replace(secret, "[REDACTED_API_KEY]")
        return scrubbed
    elif isinstance(obj, dict):
        return {
            (k if not any(s in str(k) for s in _REGISTERED_SECRETS) else "[REDACTED_KEY]"): scrub_secrets(v)
            for k, v in obj.items()
            if not any(forbidden in str(k).lower() for forbidden in ("api_key", "secret", "bearer", "authorization"))
        }
    elif isinstance(obj, list):
        return [scrub_secrets(item) for item in obj]
    elif isinstance(obj, tuple):
        return tuple(scrub_secrets(item) for item in obj)
    return obj
