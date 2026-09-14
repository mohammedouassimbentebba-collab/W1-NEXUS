"""Stable public Python SDK surface for W1 Nexus plugins and embedders.

Only symbols exported from :mod:`w1cip.sdk` are covered by the Step 39
compatibility contract. Internal ``w1cip.*`` modules may evolve during 0.1 dev
releases unless separately documented.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

SDK_VERSION = "1.2.0"
PLUGIN_API_VERSION = "1.0"
SDK_COMPATIBILITY_CONTRACT = {
    "sdk_major": 1,
    "plugin_api_major": 1,
    "breaking_changes_require_major_bump": True,
    "internal_modules_are_not_stable_api": True,
}


@dataclass(frozen=True)
class PluginContext:
    """Non-secret context supplied to one plugin-host process."""

    plugin_id: str
    plugin_version: str
    api_version: str
    granted_permissions: tuple[str, ...] = ()
    workspace_id: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


class PluginBase:
    """Convenience base class for W1 plugins.

    Plugins may override ``start``, ``health`` and ``stop``. Provider plugins
    implement ``invoke_provider(request)`` and return a normalized mapping with
    ``output_type`` and ``payload``.
    """

    def start(self, context: PluginContext) -> Mapping[str, Any] | None:
        return {"started": True}

    def health(self) -> Mapping[str, Any]:
        return {"ok": True}

    def stop(self) -> Mapping[str, Any] | None:
        return {"stopped": True}


# Lazy exports keep the plugin host lightweight and avoid forcing external
# adopters to import unstable implementation modules directly.
def __getattr__(name: str):
    if name in {"W1LocalClient", "LocalControlSettings"}:
        from w1cip.model_access import LocalControlSettings, W1LocalClient
        return {"W1LocalClient": W1LocalClient, "LocalControlSettings": LocalControlSettings}[name]
    if name in {"ProviderRequest", "ProviderResponse"}:
        from w1cip.orchestrator import ProviderRequest, ProviderResponse
        return {"ProviderRequest": ProviderRequest, "ProviderResponse": ProviderResponse}[name]
    if name in {"CollaborationClient", "CollaborationServerSettings", "SyncMutation"}:
        from w1cip.collaboration import CollaborationClient, CollaborationServerSettings, SyncMutation
        return {
            "CollaborationClient": CollaborationClient,
            "CollaborationServerSettings": CollaborationServerSettings,
            "SyncMutation": SyncMutation,
        }[name]
    if name in {"AITeamDefinition", "AITeamMember", "AIConnectionService", "provider_catalog"}:
        from w1cip.ai_connections import AITeamDefinition, AITeamMember, AIConnectionService, provider_catalog
        return {
            "AITeamDefinition": AITeamDefinition,
            "AITeamMember": AITeamMember,
            "AIConnectionService": AIConnectionService,
            "provider_catalog": provider_catalog,
        }[name]
    if name in {"ProviderCertificationRecord", "ProviderCertificationStore", "ProviderCertifier", "provider_certification_catalog"}:
        from w1cip.provider_certification import (
            ProviderCertificationRecord, ProviderCertificationStore, ProviderCertifier, provider_certification_catalog,
        )
        return {
            "ProviderCertificationRecord": ProviderCertificationRecord,
            "ProviderCertificationStore": ProviderCertificationStore,
            "ProviderCertifier": ProviderCertifier,
            "provider_certification_catalog": provider_certification_catalog,
        }[name]
    raise AttributeError(name)


__all__ = [
    "SDK_VERSION",
    "PLUGIN_API_VERSION",
    "SDK_COMPATIBILITY_CONTRACT",
    "PluginContext",
    "PluginBase",
    "W1LocalClient",
    "LocalControlSettings",
    "ProviderRequest",
    "ProviderResponse",
    "CollaborationClient",
    "CollaborationServerSettings",
    "SyncMutation",
    "AITeamDefinition",
    "AITeamMember",
    "AIConnectionService",
    "provider_catalog",
    "ProviderCertificationRecord",
    "ProviderCertificationStore",
    "ProviderCertifier",
    "provider_certification_catalog",
]
