"""W1 Nexus command-line interface for the W1-CIP reference runtime."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import secrets
import sys
import traceback
import webbrowser
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from .cli_support import (
    CLIError,
    WorkspacePaths,
    action_policy,
    audit_markdown,
    build_providers,
    canonical_pretty,
    compile_plan,
    doctor,
    initialize_workspace,
    load_json,
    model_access_fabric,
    model_access_inventory,
    model_access_store,
    provider_inventory,
    require_workspace,
    run_snapshot,
    session_audit,
    table,
    trusted_recorders,
    write_json,
)
from .action_runtime import (
    ActionApprovalRequired,
    ActionRequest,
    ActionRuntime,
)
from .orchestrator import (
    ArtifactEnvelopeFactory,
    OrchestratorCore,
    OrchestratorJournal,
    OrchestratorRunResult,
    RuntimeEvent,
    CompiledTask,
)
from .reference_demo import DEMO_RUN_ID, DEMO_SESSION_ID, run_reference_demo
from .session_store import SessionNotFoundError, SessionStore
from .mcp import (
    MCPConfigurationError,
    MCPManager,
    MCPServerConfig,
    MCPStdioServer,
    ToolApprovalRequired,
)
from .parallel_runtime import (
    AgentJob,
    MergeCoordinator,
    ParallelAgentRuntime,
    ParallelRunPolicy,
)
from .memory import (
    KnowledgeContextProvider,
    MemoryDraft,
    MemoryPrincipal,
    MemoryProvenance,
    MemoryQuery,
    MemoryStore,
    utc_now,
)
from .secure_execution import (
    SecureExecutionFabric,
    SandboxProfile,
    SandboxAttestation,
    request_from_mapping,
)
from .scientific import (
    ScientificLab,
    ScientificPrincipal,
)
from .model_access import (
    EmbeddedW1Runtime,
    LocalControlServer,
    LocalControlSettings,
    ModelAccessFabric,
    ModelAccessStore,
    profile_from_mapping,
    portfolio_from_mapping,
    provider_request_from_mapping,
)
from .evaluation import evaluate_w1_nexus, run_model_access_benchmark, run_governed_computer_use_benchmark
from .ai_connections import (
    AIConnectionService,
    AIConnectionStore,
    AITeamDefinition,
    AITeamMember,
    provider_catalog,
    run_ai_connections_benchmark,
    team_from_mapping,
)
from .provider_certification import (
    ProviderCertificationStore,
    ProviderCertifier,
    provider_certification_catalog,
    run_provider_certification_benchmark,
    LiveCertificationRequired,
    BillableCertificationApprovalRequired,
)
from .capacity_router import (
    AdaptiveCapacityRouter,
    CapacityObservation,
    CapacityStore,
    RoutingPolicy,
    ROUTING_MODES,
    TEAM_MODES,
    CAPACITY_SOURCES,
    QUOTA_STATES,
    TERMS_STATUSES,
    run_capacity_router_benchmark,
)
from .intelligence_search import (
    IntelligenceSearchEngine,
    IntelligenceSearchStore,
    SEARCH_SOURCES,
    run_intelligence_search_benchmark,
    search_catalog,
)
from .intelligence_activation import (
    IntelligenceActivationService,
    IntelligenceActivationStore,
    REVIEW_DECISIONS,
    run_intelligence_activation_benchmark,
)
from .computer_use import detect_computer_backend
from .w1_gateway import (
    ENTITLEMENT_STATES,
    KNOWN_MODEL_IDENTITIES,
    MODEL_ARCHITECTURES,
    QUALITY_TIERS,
    ROUTE_SOURCE_TYPES,
    W1_PROVIDER_REGISTRY,
    GatewayStore,
    ModelIdentity,
    ModelRoute,
    RouteCost,
    RouteExplanation,
    RouteQuota,
    SelectedRoute,
    TeamBuilder,
    TeamPolicy,
    W1RouteSelector,
    gateway_statistics,
    ingest_discovered_models,
    populate_gateway_store,
    run_gateway_benchmark,
    verify_route_live,
)
from .intelligence_discovery import (
    AutonomousDiscoveryEngine,
    CertificationStatus,
    DiscoveredModel,
    DiversityDecision,
    DiversityPolicyEngine,
    DiversityPolicyLevel,
    EntitlementState,
    LocalRuntimeDiscoveryAdapter,
    ModelIdentityResolver,
    PublicCatalogDiscoveryAdapter,
    QualityEvidence,
    RouteCertificationRecord,
    TaskFitnessEngine,
    TrustClass,
)
from .benchmark_core.profiles import get_model_profile, get_provider
from .benchmark_core.availability import CredentialModelAvailabilityTester
from .benchmark_core.secrets import list_credential_refs
from .credential_broker import (
    CredentialBroker,
    CredentialBrokerStore,
    MemoryCredentialVault,
    OAuthAuthorizationPending,
    OAuthProviderConfig,
    OAuthSlowDown,
    create_native_credential_vault,
    credential_vault_status,
    provider_from_mapping,
    run_credential_broker_benchmark,
    workspace_credential_namespace,
)
from .artifact_studio import ArtifactStudioStore, WorkspaceFileService, run_artifact_studio_benchmark
from .desktop_shell import DesktopSettings, create_desktop_server, detect_desktop_backend, launch_desktop
from .native_packaging import (
    NATIVE_PACKAGING_VERSION,
    NativePackagingError,
    NativeServiceController,
    ProjectDescriptor,
    UpdateManifest,
    generate_windows_packaging_sources,
    native_build_plan,
    parse_deep_link,
    run_native_packaging_benchmark,
    verify_update_artifact,
    verify_windows_release_candidate,
)

from .collaboration import (
    COLLABORATION_PROTOCOL_VERSION,
    CollaborationClient,
    CollaborationConflict,
    CollaborationServerSettings,
    CollaborationService,
    CollaborationStore,
    SyncMutation,
    create_collaboration_server,
    run_collaboration_benchmark,
)

from .plugin_system import (
    PLUGIN_API_VERSION,
    SDK_VERSION,
    PluginManager,
    PluginManifest,
    compatibility_report,
    run_plugin_adoption_benchmark,
    scaffold_plugin,
)
from .release_hardening import (
    ExternalResultStore,
    dependency_inventory,
    external_benchmark_catalog,
    release_gate,
    render_threat_model_markdown,
    run_release_hardening_benchmark,
    source_manifest,
)
from .universal_artifacts import (
    UniversalArtifactStore,
    reference_artifact_models,
    render_artifact,
    run_universal_artifact_benchmark,
    validate_artifact_model,
)
from .workspace_console import (
    ConsoleSettings,
    WorkspaceSnapshotService,
    create_console_server,
)
from .mcp_integration import (
    build_registry_bundle,
    build_w1_mcp_server,
    load_mcp_configs,
    write_mcp_configs,
)

CLI_VERSION = "0.1.0-dev52"


class Terminal:
    def __init__(self, *, use_color: bool, json_mode: bool) -> None:
        self.use_color = use_color and sys.stdout.isatty() and not os.environ.get("NO_COLOR")
        self.json_mode = json_mode

    def _style(self, value: str, code: str) -> str:
        return f"\033[{code}m{value}\033[0m" if self.use_color else value

    def success(self, value: str) -> str:
        return self._style(value, "32;1")

    def warning(self, value: str) -> str:
        return self._style(value, "33;1")

    def error(self, value: str) -> str:
        return self._style(value, "31;1")

    def heading(self, value: str) -> str:
        return self._style(value, "36;1")

    def emit(self, value: Any, *, human: str | None = None) -> None:
        def _safe_print(text: str) -> None:
            try:
                print(text)
            except UnicodeEncodeError:
                enc = sys.stdout.encoding or "utf-8"
                print(text.encode(enc, errors="replace").decode(enc))

        if self.json_mode:
            _safe_print(canonical_pretty(value))
        elif human is not None:
            _safe_print(human)
        elif isinstance(value, str):
            _safe_print(value)
        else:
            _safe_print(canonical_pretty(value))

    def event_listener(self, mode: str):
        if mode == "silent":
            return None

        def listener(event: RuntimeEvent) -> None:
            payload = {
                "event_id": event.event_id,
                "event_type": event.event_type,
                "run_id": event.run_id,
                "session_id": event.session_id,
                "task_id": event.task_id,
                "occurred_at": event.occurred_at,
                "details": dict(event.details),
            }
            if mode == "json":
                print(json.dumps(payload, ensure_ascii=False, sort_keys=True), file=sys.stderr)
                return
            task = f" [{event.task_id}]" if event.task_id else ""
            detail = ""
            resource = event.details.get("resource_id")
            if resource:
                detail = f" resource={resource}"
            print(f"{event.occurred_at}  {event.event_type}{task}{detail}", file=sys.stderr)

        return listener


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="w1",
        description=(
            "W1 Nexus: quota-aware, multi-model, auditable orchestration for W1-CIP."
        ),
    )
    parser.add_argument("--version", action="version", version=f"w1 {CLI_VERSION}")
    parser.add_argument("--workspace", default=".", help="Project root containing .w1nexus")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    parser.add_argument("--no-color", action="store_true", help="Disable ANSI colors")
    parser.add_argument("--debug", action="store_true", help="Show tracebacks on failures")

    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init", help="Initialize a W1 workspace")
    init.add_argument("path", nargs="?", default=None)
    init.add_argument("--force", action="store_true")

    doc = commands.add_parser("doctor", help="Validate workspace, schemas, providers, and stores")
    doc.add_argument("--deep", action="store_true", help="Construct connectors and verify all sessions")

    providers = commands.add_parser("providers", help="Inspect provider connectors")
    provider_commands = providers.add_subparsers(dest="providers_command", required=True)
    provider_commands.add_parser("list", help="List redaction-safe provider configuration")
    provider_commands.add_parser("validate", help="Construct all enabled connectors without network calls")

    accounts = commands.add_parser("accounts", help="Manage OAuth providers and user-owned accounts through the local credential broker")
    account_commands = accounts.add_subparsers(dest="accounts_command", required=True)
    account_commands.add_parser("list", help="List redaction-safe connected accounts")
    account_show = account_commands.add_parser("show", help="Show one redaction-safe account record")
    account_show.add_argument("account_id")
    account_commands.add_parser("vault", help="Show native credential-vault availability without exposing secrets")
    account_commands.add_parser("benchmark", help="Run deterministic OAuth/credential broker probes")
    account_audit = account_commands.add_parser("audit", help="List redaction-safe credential broker audit events")
    account_audit.add_argument("--limit", type=int, default=50)
    account_providers = account_commands.add_parser("providers", help="Manage OAuth authorization-server metadata")
    account_provider_commands = account_providers.add_subparsers(dest="account_provider_command", required=True)
    account_provider_commands.add_parser("list", help="List configured OAuth providers and discovered capabilities")
    account_provider_add = account_provider_commands.add_parser("add", help="Add or update OAuth provider metadata from JSON")
    account_provider_add.add_argument("--file", required=True)
    account_provider_discover = account_provider_commands.add_parser("discover", help="Discover OAuth/OIDC authorization-server metadata")
    account_provider_discover.add_argument("provider_id")
    account_provider_caps = account_provider_commands.add_parser("capabilities", help="Show discovered provider account capabilities")
    account_provider_caps.add_argument("provider_id")
    account_login = account_commands.add_parser("login", help="Start authorization-code + PKCE login")
    account_login.add_argument("provider_id")
    account_login.add_argument("--client-id")
    account_login.add_argument("--redirect-uri", required=True)
    account_login.add_argument("--scope", action="append", default=[])
    account_login.add_argument("--account-id")
    account_login.add_argument("--open", action="store_true", help="Open the authorization URL in the default browser")
    account_complete = account_commands.add_parser("complete", help="Complete a PKCE login using a one-time authorization code")
    account_complete.add_argument("--state", required=True)
    account_complete.add_argument("--code-stdin", action="store_true", help="Read the authorization code from stdin instead of a hidden prompt")
    account_complete.add_argument("--display-name")
    account_device_start = account_commands.add_parser("device-start", help="Start OAuth device authorization")
    account_device_start.add_argument("provider_id")
    account_device_start.add_argument("--client-id")
    account_device_start.add_argument("--scope", action="append", default=[])
    account_device_start.add_argument("--account-id")
    account_device_poll = account_commands.add_parser("device-poll", help="Poll one device authorization session")
    account_device_poll.add_argument("device_id")
    account_device_poll.add_argument("--display-name")
    account_refresh = account_commands.add_parser("refresh", help="Refresh one account token using its vault-only refresh token")
    account_refresh.add_argument("account_id")
    account_revoke = account_commands.add_parser("revoke", help="Revoke provider credentials when supported and remove the local account")
    account_revoke.add_argument("account_id")
    account_remove = account_commands.add_parser("remove", help="Remove one local account and its vault entry without remote revocation")
    account_remove.add_argument("account_id")

    credentials = commands.add_parser("credentials", help="Store user-owned API credentials in the native OS vault")
    credential_commands = credentials.add_subparsers(dest="credentials_command", required=True)
    credential_commands.add_parser("list", help="List credential references without values")
    credential_set = credential_commands.add_parser("set", help="Store or replace a credential without putting the value on the command line")
    credential_set.add_argument("credential_id")
    credential_set.add_argument("--provider")
    credential_set.add_argument("--label")
    credential_set.add_argument("--stdin", action="store_true", help="Read the credential value from stdin instead of a hidden prompt")
    credential_remove = credential_commands.add_parser("remove", help="Delete a credential from the native vault and metadata store")
    credential_remove.add_argument("credential_id")

    connections = commands.add_parser("connections", help="Connect user-owned AI providers, accounts, local runtimes, and custom endpoints")
    connection_commands = connections.add_subparsers(dest="connections_command", required=True)
    connection_commands.add_parser("catalog", help="Show built-in AI provider connection types and supported auth modes")
    connection_commands.add_parser("list", help="List redaction-safe AI connections and their registered models")
    connection_commands.add_parser("benchmark", help="Run deterministic AI Connections and Team Studio probes")
    connection_commands.add_parser("certification-catalog", help="Show current provider API certification contracts")
    connection_commands.add_parser("certification-benchmark", help="Run deterministic certification harness probes without external calls")
    connection_plan = connection_commands.add_parser("certification-plan", help="Plan live provider certification without making network calls")
    connection_plan.add_argument("connection_id")
    connection_plan.add_argument("--model")
    connection_plan.add_argument("--level", choices=["preflight", "runtime", "full"], default="preflight")
    connection_certify = connection_commands.add_parser("certify", help="Execute explicit live provider certification and store redacted evidence")
    connection_certify.add_argument("connection_id")
    connection_certify.add_argument("--model")
    connection_certify.add_argument("--level", choices=["preflight", "runtime", "full"], default="preflight")
    connection_certify.add_argument("--live", action="store_true", help="Required acknowledgement that external provider calls will be made")
    connection_certify.add_argument("--allow-billable-probes", action="store_true", help="Allow generation/streaming/tool probes that may incur provider charges")
    connection_certify.add_argument("--timeout", type=float, default=30.0)
    connection_certifications = connection_commands.add_parser("certifications", help="List immutable redacted provider certification records")
    connection_certifications.add_argument("--connection")
    connection_api = connection_commands.add_parser("add-api-key", help="Connect a provider using an API credential read securely from stdin/prompt")
    connection_api.add_argument("connection_id")
    connection_api.add_argument("--provider", required=True, choices=["openai", "anthropic", "gemini", "xai", "custom-openai-compatible"])
    connection_api.add_argument("--label")
    connection_api.add_argument("--endpoint")
    connection_api.add_argument("--stdin", action="store_true")
    connection_oauth = connection_commands.add_parser("attach-account", help="Attach an existing W1 OAuth account to an AI provider connection")
    connection_oauth.add_argument("connection_id")
    connection_oauth.add_argument("--provider", required=True)
    connection_oauth.add_argument("--account", required=True)
    connection_oauth.add_argument("--label")
    connection_oauth.add_argument("--endpoint")
    connection_custom = connection_commands.add_parser("add-custom", help="Connect a custom OpenAI-compatible endpoint without credentials")
    connection_custom.add_argument("connection_id")
    connection_custom.add_argument("--endpoint", required=True)
    connection_custom.add_argument("--label")
    connection_local = connection_commands.add_parser("add-local", help="Connect a loopback OpenAI-compatible local runtime")
    connection_local.add_argument("connection_id")
    connection_local.add_argument("--endpoint", required=True)
    connection_local.add_argument("--label")
    connection_model = connection_commands.add_parser("add-model", help="Register a model under an AI connection")
    connection_model.add_argument("connection_id")
    connection_model.add_argument("model_id")
    connection_model.add_argument("--model-name", required=True)
    connection_model.add_argument("--display-name")
    connection_model.add_argument("--role", action="append", default=[])
    connection_model.add_argument("--domain", action="append", default=[])
    connection_model.add_argument("--capacity-source", choices=sorted(CAPACITY_SOURCES))
    connection_model.add_argument("--terms-status", choices=sorted(TERMS_STATUSES))
    connection_model.add_argument("--quality-score", type=float)
    connection_model.add_argument("--input-cost-per-million", type=float)
    connection_model.add_argument("--output-cost-per-million", type=float)
    connection_discover = connection_commands.add_parser("discover", help="Discover model IDs from the provider when its API supports listing")
    connection_discover.add_argument("connection_id")
    connection_remove = connection_commands.add_parser("remove", help="Remove connection metadata; credential/account remains separately managed")
    connection_remove.add_argument("connection_id")

    capacity = commands.add_parser("capacity", help="Route cooperative AI roles across quota, cost, quality, and provenance constraints")
    capacity_commands = capacity.add_subparsers(dest="capacity_command", required=True)
    capacity_commands.add_parser("benchmark", help="Run deterministic adaptive-capacity and collaboration-preservation probes")
    capacity_commands.add_parser("list", help="List redaction-safe capacity/quota observations")
    capacity_observe = capacity_commands.add_parser("observe", help="Record a model quota/cost observation without storing credentials")
    capacity_observe.add_argument("model_id")
    capacity_observe.add_argument("--source", choices=sorted(CAPACITY_SOURCES), required=True)
    capacity_observe.add_argument("--quota-state", choices=sorted(QUOTA_STATES), default="unknown")
    capacity_observe.add_argument("--remaining-tokens", type=int)
    capacity_observe.add_argument("--reset-at")
    capacity_observe.add_argument("--input-cost-per-million", type=float, default=0.0)
    capacity_observe.add_argument("--output-cost-per-million", type=float, default=0.0)
    capacity_observe.add_argument("--terms-status", choices=sorted(TERMS_STATUSES), default="official")
    capacity_observe.add_argument("--entitlement-verified", action="store_true")
    capacity_plan = capacity_commands.add_parser("plan", help="Build a collaboration-preserving adaptive ModelPortfolio for one task")
    capacity_plan.add_argument("--task", required=True, help="CompiledTask JSON file")
    capacity_plan.add_argument("--plan-id", default="adaptive-team")
    capacity_plan.add_argument("--display-name", default="Adaptive W1 Team")
    capacity_plan.add_argument("--team-mode", choices=sorted(TEAM_MODES), default="challenge")
    capacity_plan.add_argument("--routing-mode", choices=sorted(ROUTING_MODES), default="balanced")
    capacity_plan.add_argument("--quality-floor", type=float, default=0.65)
    capacity_plan.add_argument("--max-task-cost", type=float)
    capacity_plan.add_argument("--estimated-input-tokens", type=int, default=2500)
    capacity_plan.add_argument("--estimated-output-tokens", type=int, default=1500)
    capacity_plan.add_argument("--producer-count", type=int, default=2)
    capacity_plan.add_argument("--fallback-per-role", type=int, default=1)
    capacity_plan.add_argument("--allow-third-party-free", action="store_true")
    capacity_plan.add_argument("--no-paid", action="store_true")
    capacity_plan.add_argument("--no-local", action="store_true")
    capacity_plan.add_argument("--allow-unknown-terms", action="store_true")
    capacity_plan.add_argument("--save-portfolio", action="store_true")

    intelligence = commands.add_parser("intelligence", help="Search public AI catalogs from the local W1 process; no W1-owned server required")
    intelligence_commands = intelligence.add_subparsers(dest="intelligence_command", required=True)
    intelligence_commands.add_parser("benchmark", help="Run deterministic no-network Intelligence Search Engine probes")
    intelligence_commands.add_parser("catalog", help="Show supported public search sources and local-first security policy")
    intelligence_list = intelligence_commands.add_parser("list", help="List locally cached intelligence-search candidates")
    intelligence_list.add_argument("--source", choices=sorted(SEARCH_SOURCES))
    intelligence_list.add_argument("--limit", type=int, default=100)
    intelligence_search = intelligence_commands.add_parser("search", help="Search public AI/model ecosystems directly from this machine")
    intelligence_search.add_argument("query", nargs="?", default="free llm api")
    intelligence_search.add_argument("--source", action="append", choices=sorted(SEARCH_SOURCES), default=[])
    intelligence_search.add_argument("--limit-per-source", type=int, default=12)
    intelligence_commands.add_parser("clear", help="Clear only the local public-candidate search cache")
    intelligence_commands.add_parser("activation-benchmark", help="Run deterministic no-network candidate activation gate probes")
    intelligence_assess = intelligence_commands.add_parser("assess", help="Assess whether a cached candidate has enough local evidence for activation")
    intelligence_assess.add_argument("candidate_id")
    intelligence_assess.add_argument("--connection-id")
    intelligence_review = intelligence_commands.add_parser("review", help="Record a local manual review decision without creating entitlement")
    intelligence_review.add_argument("candidate_id")
    intelligence_review.add_argument("--decision", choices=sorted(REVIEW_DECISIONS), required=True)
    intelligence_review.add_argument("--note", default="")
    intelligence_activate = intelligence_commands.add_parser("activate", help="Activate a verified OpenRouter free candidate through an existing discovered connection")
    intelligence_activate.add_argument("candidate_id")
    intelligence_activate.add_argument("--connection-id")
    intelligence_activate.add_argument(
        "--profile-id",
        "--profile",
        dest="profile_id",
        default=None,
        help="Explicit model profile identifier to assign to the activated model candidate",
    )
    intelligence_activations = intelligence_commands.add_parser("activations", help="List local candidate review/activation state")
    intelligence_activations.add_argument("--limit", type=int, default=100)

    discovery = commands.add_parser("discovery", help="Autonomous Intelligence Discovery Framework (AID-CF)")
    discovery_commands = discovery.add_subparsers(dest="discovery_command", required=True)
    disc_scan = discovery_commands.add_parser("scan", help="Scan local and catalog sources for model intelligence")
    disc_scan.add_argument("--local", action="store_true", help="Scan local loopback runtimes only")
    disc_scan.add_argument("--catalogs", action="store_true", help="Scan public model catalogs only")
    disc_scan.add_argument("--accounts", action="store_true", help="Scan user accounts only")
    disc_list = discovery_commands.add_parser("list", help="List discovered models")
    disc_list.add_argument("--source", help="Filter by discovery source")
    disc_list.add_argument("--trust-class", choices=["LOCAL_LOOPBACK", "TRUSTED_ACCOUNT_API", "TRUSTED_PUBLIC_CATALOG", "PUBLIC_COMMUNITY_CATALOG", "UNKNOWN"])

    gateway = commands.add_parser("gateway", help="Universal Access Fabric & Intelligent Multi-Route Gateway")
    gateway_commands = gateway.add_subparsers(dest="gateway_command", required=True)
    gateway_commands.add_parser("list-providers", help="List registered providers in gateway")
    gateway_commands.add_parser("list-identities", help="List canonical model identities")
    gateway_commands.add_parser("doctor", help="Run comprehensive Gateway health, capacity, and certification diagnosis")

    gw_identities = gateway_commands.add_parser("identities", help="List resolved canonical model identities")
    gw_identities.add_argument("--vendor")
    gw_identities.add_argument("--family")
    gw_identities.add_argument("--architecture")

    gw_routes_alias = gateway_commands.add_parser("routes", help="List access routes with certification filtering")
    gw_routes_alias.add_argument("--provider")
    gw_routes_alias.add_argument("--identity")
    gw_routes_alias.add_argument("--account")
    gw_routes_alias.add_argument("--free-only", action="store_true")
    gw_routes_alias.add_argument("--certified-only", action="store_true")
    gw_routes_alias.add_argument("--local-only", action="store_true")

    gw_certify = gateway_commands.add_parser("certify", help="Certify a model route with expiration and evidence hash")
    gw_certify.add_argument("route_id")
    gw_certify.add_argument("--ttl-days", type=int, default=14)
    gw_certify.add_argument("--live", action="store_true")

    gw_revoke = gateway_commands.add_parser("revoke", help="Revoke certification for a model route")
    gw_revoke.add_argument("route_id")
    gw_revoke.add_argument("--reason", default="manual_revocation")

    gw_explain_alias = gateway_commands.add_parser("explain", help="Explain why W1 selected a route")
    gw_explain_alias.add_argument("route_id", nargs="?", default=None)
    gw_explain_alias.add_argument("--task", default="general", choices=["general", "coding", "reasoning", "research"])
    gw_explain_alias.add_argument("--min-quality", type=float, default=0.50)
    gw_explain_alias.add_argument("--min-context", type=int, default=0)
    gw_explain_alias.add_argument("--free-only", action="store_true")
    gw_explain_alias.add_argument("--certified-only", action="store_true")

    gw_routes = gateway_commands.add_parser("list-routes", help="List access routes")
    gw_routes.add_argument("--provider")
    gw_routes.add_argument("--identity")
    gw_routes.add_argument("--account")
    gw_routes.add_argument("--free-only", action="store_true")

    gw_select = gateway_commands.add_parser("select-route", help="Select optimal route for a task")
    gw_select.add_argument("--task", default="general", choices=["general", "coding", "reasoning", "research"])
    gw_select.add_argument("--min-quality", type=float, default=0.50)
    gw_select.add_argument("--min-context", type=int, default=0)
    gw_select.add_argument("--free-only", action="store_true")
    gw_select.add_argument("--certified-only", action="store_true")
    gw_select.add_argument("--vendor")
    gw_select.add_argument("--architecture", choices=sorted(MODEL_ARCHITECTURES))

    gw_team = gateway_commands.add_parser("build-team", help="Assemble governed multi-model team with diversity")
    gw_team.add_argument("--task", default="general", choices=["general", "coding", "reasoning", "research"])
    gw_team.add_argument("--budget", type=float, default=0.0)
    gw_team.add_argument("--team-mode", default="challenge", choices=["solo", "fallback", "parallel", "verify", "challenge"])
    gw_team.add_argument("--diversity", default="balanced", choices=["strict", "balanced", "relaxed"])
    gw_team.add_argument("--min-context", type=int, default=0)
    gw_team.add_argument("--no-verifier", action="store_true")
    gw_team.add_argument("--certified-only", action="store_true")

    gw_explain = gateway_commands.add_parser("explain-route", help="Explain why W1 selected a route and rejected alternatives")
    gw_explain.add_argument("--task", default="general", choices=["general", "coding", "reasoning", "research"])
    gw_explain.add_argument("--min-quality", type=float, default=0.50)
    gw_explain.add_argument("--min-context", type=int, default=0)
    gw_explain.add_argument("--free-only", action="store_true")
    gw_explain.add_argument("--certified-only", action="store_true")

    gw_inspect = gateway_commands.add_parser("inspect", help="Inspect detailed telemetry and quota for a route")
    gw_inspect.add_argument("route_id")

    gw_verify = gateway_commands.add_parser("verify-route", help="Verify route health and connectivity")
    gw_verify.add_argument("route_id")
    gw_verify.add_argument("--live", action="store_true")

    gw_discover = gateway_commands.add_parser("discover", help="Discover and ingest models dynamically into gateway store")
    gw_discover.add_argument("--provider")
    gw_discover.add_argument("--account", default="default")

    gateway_commands.add_parser("benchmark", help="Run offline contract benchmark for W1 Gateway")
    gateway_commands.add_parser("stats", help="Show registry and routing statistics")

    teams = commands.add_parser("teams", help="Build multi-company or single-provider AI teams over connected model profiles")
    team_commands = teams.add_subparsers(dest="teams_command", required=True)
    team_commands.add_parser("list", help="List AI Team Studio definitions")
    team_commands.add_parser("benchmark", help="Run deterministic AI Connections and Team Studio probes")
    team_add = team_commands.add_parser("add", help="Create/update a team from JSON")
    team_add.add_argument("--file", required=True)
    team_show = team_commands.add_parser("show", help="Show one AI team")
    team_show.add_argument("team_id")
    team_remove = team_commands.add_parser("remove", help="Remove a team and its compiled ModelPortfolio")
    team_remove.add_argument("team_id")

    models = commands.add_parser("models", help="Manage preferred local, cloud, private, and external models")
    model_commands = models.add_subparsers(dest="models_command", required=True)
    model_commands.add_parser("list", help="List registered model profiles and portfolios")
    model_commands.add_parser("discover", help="Discover credential x model catalog availability (may hit /v1/models)")
    model_inspect = model_commands.add_parser("inspect", help="Inspect a model profile contract (no secrets)")
    model_inspect.add_argument("model")
    model_inspect.add_argument("--credential", dest="credential_ref", default=None, help="Credential ref for provenance output")
    model_test = model_commands.add_parser("test", help="Run a tiny credential x model availability probe")
    model_test.add_argument("model", nargs="?", default=None, help="Optional model key/id; defaults to all known profiles")
    model_test.add_argument("--credential", dest="credential_ref", default=None)
    model_commands.add_parser("matrix", help="Build the full credential x model availability matrix (offline-safe injectable)")
    model_commands.add_parser("sync", help="Synchronize model-access.json into the local registry")
    model_add = model_commands.add_parser("add", help="Add or update one model profile from JSON")
    model_add.add_argument("--file", required=True)
    model_remove = model_commands.add_parser("remove", help="Remove one model profile")
    model_remove.add_argument("model_id")
    portfolio_commands = model_commands.add_parser("portfolios", help="Manage multi-model portfolios")
    portfolio_subcommands = portfolio_commands.add_subparsers(dest="portfolio_command", required=True)
    portfolio_subcommands.add_parser("list", help="List portfolios")
    portfolio_add = portfolio_subcommands.add_parser("add", help="Add or update a portfolio from JSON")
    portfolio_add.add_argument("--file", required=True)
    portfolio_remove = portfolio_subcommands.add_parser("remove", help="Remove a portfolio")
    portfolio_remove.add_argument("portfolio_id")
    model_invoke = model_commands.add_parser("invoke", help="Invoke one model or a multi-model portfolio")
    target = model_invoke.add_mutually_exclusive_group(required=True)
    target.add_argument("--model")
    target.add_argument("--portfolio")
    model_invoke.add_argument("--request", required=True, help="Normalized ProviderRequest JSON")
    model_commands.add_parser("benchmark", help="Run deterministic multi-model access probes")

    access = commands.add_parser("access", help="Expose W1 as a local embeddable model-access service")
    access_commands = access.add_subparsers(dest="access_command", required=True)
    access_serve = access_commands.add_parser("serve", help="Run the loopback-only Local Control API")
    access_serve.add_argument("--host", default="127.0.0.1")
    access_serve.add_argument("--port", type=int, default=8871)

    evaluate = commands.add_parser("evaluate", help="Run the executable W1 Nexus capability scorecard")
    evaluate.add_argument("--output", help="Write the full JSON scorecard")

    plan = commands.add_parser("plan", help="Compile a goal into tasks without invoking any model")
    plan.add_argument("--goal", required=True, help="GoalContract legal payload JSON")
    plan.add_argument("--team", required=True, help="TeamPlan legal payload JSON")
    plan.add_argument("--resources", required=True, help="ExecutionResourcePlan legal payload JSON")
    plan.add_argument("--context-map", help="JSON map from deliverable_id to permitted context fields")
    plan.add_argument("--domain", action="append", default=[])
    plan.add_argument("--output", help="Write plan JSON to this path")

    run = commands.add_parser("run", help="Run or safely continue a session-backed workflow")
    _add_run_arguments(run, include_identifiers=True)

    resume = commands.add_parser("resume", help="Resume a run from its saved invocation metadata")
    resume.add_argument("run_id")
    resume.add_argument("--events", choices=["text", "json", "silent"], default="text")

    status = commands.add_parser("status", help="Show operational run state")
    status.add_argument("run_id")

    sessions = commands.add_parser("sessions", help="Inspect stored protocol sessions")
    session_commands = sessions.add_subparsers(dest="sessions_command", required=True)
    session_commands.add_parser("list", help="List sessions")
    show = session_commands.add_parser("show", help="Show session summary")
    show.add_argument("session_id")
    verify = session_commands.add_parser("verify", help="Verify hash chain and deterministic replay")
    verify.add_argument("session_id")
    replay = session_commands.add_parser("replay", help="Replay session and summarize projection")
    replay.add_argument("session_id")
    append = session_commands.add_parser("append", help="Append one envelope or an array of envelopes")
    append.add_argument("--file", required=True)
    append.add_argument("--expected-last-sequence", type=int)
    rebuild = session_commands.add_parser("rebuild", help="Rebuild projections from the immutable ledger")
    rebuild.add_argument("session_id")

    audit = commands.add_parser("audit", help="Export an auditable session report")
    audit.add_argument("session_id")
    audit.add_argument("--format", choices=["json", "markdown"], default="markdown")
    audit.add_argument("--output")
    audit.add_argument("--include-events", action="store_true")

    export = commands.add_parser("export", help="Export an orchestrator run report")
    export.add_argument("run_id")
    export.add_argument("--format", choices=["json", "markdown"], default="markdown")
    export.add_argument("--output")

    demo = commands.add_parser("demo", help="Run the complete offline reference workflow")
    demo.add_argument("--reset", action="store_true", help="Reset demo databases first")
    demo.add_argument("--events", choices=["text", "json", "silent"], default="text")

    benchmark = commands.add_parser("benchmark", help="Run deterministic resilience checks")
    benchmark.add_argument("--reset", action="store_true")

    actions = commands.add_parser("actions", help="Plan, approve, execute, inspect, and undo governed local actions")
    action_commands = actions.add_subparsers(dest="actions_command", required=True)
    action_plan = action_commands.add_parser("plan", help="Evaluate an action request without executing it")
    action_plan.add_argument("--request", required=True, help="ActionRequest JSON")
    action_approve = action_commands.add_parser("approve", help="Issue a one-time local approval bound to an action digest")
    action_approve.add_argument("--request", required=True)
    action_approve.add_argument("--issued-by", required=True)
    action_approve.add_argument("--ttl-seconds", type=int, default=600)
    action_approve.add_argument("--output")
    action_execute = action_commands.add_parser("execute", help="Execute a governed action")
    action_execute.add_argument("--request", required=True)
    action_execute.add_argument("--approval", help="Approval JSON created by actions approve")
    action_execute.add_argument("--approve", action="store_true", help="Explicitly issue and consume a local one-time approval")
    action_execute.add_argument("--issued-by", default="local-user")
    action_execute.add_argument("--ttl-seconds", type=int, default=600)
    action_list = action_commands.add_parser("list", help="List action journal entries")
    action_list.add_argument("--limit", type=int, default=100)
    action_show = action_commands.add_parser("show", help="Show one action result")
    action_show.add_argument("action_id")
    action_undo = action_commands.add_parser("undo", help="Restore backups for a reversible action")
    action_undo.add_argument("action_id")
    action_undo.add_argument("--undo-action-id")

    computer = commands.add_parser("computer", help="Observe and perform bounded governed desktop input")
    computer_commands = computer.add_subparsers(dest="computer_command", required=True)
    computer_commands.add_parser("status", help="Show the native computer-use backend and supported surface")
    computer_commands.add_parser("benchmark", help="Run the deterministic virtual computer-use benchmark")
    computer_observe = computer_commands.add_parser("observe", help="Observe screen geometry, cursor, and active-window title")
    computer_observe.add_argument("--action-id")
    computer_screenshot = computer_commands.add_parser("screenshot", help="Capture a governed screenshot into short-lived W1 state")
    computer_screenshot.add_argument("--action-id")
    computer_screenshot.add_argument("--approve", action="store_true")
    computer_screenshot.add_argument("--issued-by", default="local-user")

    def add_selector_arguments(parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--element-id")
        parser.add_argument("--role")
        parser.add_argument("--name")
        parser.add_argument("--name-glob")
        parser.add_argument("--class-name")
        parser.add_argument("--control-id", type=int)
        parser.add_argument("--process-id", type=int)
        parser.add_argument("--window-title")
        parser.add_argument("--window-glob")

    computer_elements = computer_commands.add_parser("elements", help="Inspect governed local UI/accessibility elements")
    add_selector_arguments(computer_elements)
    computer_elements.add_argument("--action-id")
    computer_elements.add_argument("--approve", action="store_true")
    computer_elements.add_argument("--issued-by", default="local-user")

    computer_element_click = computer_commands.add_parser("click-element", help="Click one selector-bound, fingerprint-verified UI element")
    add_selector_arguments(computer_element_click)
    computer_element_click.add_argument("--fingerprint", required=True)
    computer_element_click.add_argument("--button", choices=["left", "right", "middle"], default="left")
    computer_element_click.add_argument("--clicks", type=int, default=1)
    computer_element_click.add_argument("--action-id")
    computer_element_click.add_argument("--approve", action="store_true")
    computer_element_click.add_argument("--issued-by", default="local-user")

    computer_type = computer_commands.add_parser("type", help="Type process-local ephemeral text into a verified UI element")
    add_selector_arguments(computer_type)
    computer_type.add_argument("--fingerprint", required=True)
    computer_type.add_argument("--stdin", action="store_true", help="Read text from stdin instead of a hidden prompt")
    computer_type.add_argument("--ttl", type=int, default=120)
    computer_type.add_argument("--action-id")
    computer_type.add_argument("--approve", action="store_true", help="Required: acquire ephemeral input and execute immediately")
    computer_type.add_argument("--issued-by", default="local-user")

    computer_choose = computer_commands.add_parser("choose-file", help="Fill a verified native file field with a workspace file path")
    add_selector_arguments(computer_choose)
    computer_choose.add_argument("path")
    computer_choose.add_argument("--fingerprint", required=True)
    computer_choose.add_argument("--no-submit", action="store_true")
    computer_choose.add_argument("--action-id")
    computer_choose.add_argument("--approve", action="store_true")
    computer_choose.add_argument("--issued-by", default="local-user")
    for name in ("move", "click", "scroll"):
        computer_input = computer_commands.add_parser(name, help=f"Plan or execute governed pointer {name}")
        computer_input.add_argument("x", type=int)
        computer_input.add_argument("y", type=int)
        computer_input.add_argument("--action-id")
        computer_input.add_argument("--approve", action="store_true", help="Explicitly approve and execute this exact action")
        computer_input.add_argument("--issued-by", default="local-user")
    computer_click = computer_commands.choices["click"]
    computer_click.add_argument("--button", choices=["left", "right", "middle"], default="left")
    computer_click.add_argument("--clicks", type=int, default=1)
    computer_scroll = computer_commands.choices["scroll"]
    computer_scroll.add_argument("--delta", type=int, required=True)
    computer_key = computer_commands.add_parser("key", help="Plan or execute one bounded navigation key")
    computer_key.add_argument("key", choices=["tab", "enter", "escape", "space", "left", "right", "up", "down", "home", "end", "pageup", "pagedown"])
    computer_key.add_argument("--action-id")
    computer_key.add_argument("--approve", action="store_true", help="Explicitly approve and execute this exact action")
    computer_key.add_argument("--issued-by", default="local-user")

    worktrees = commands.add_parser("worktrees", help="Manage isolated Git worktrees for agents")
    worktree_commands = worktrees.add_subparsers(dest="worktrees_command", required=True)
    worktree_commands.add_parser("list", help="List registered agent worktrees")
    wt_create = worktree_commands.add_parser("create", help="Create an isolated worktree and branch")
    wt_create.add_argument("agent_id")
    wt_create.add_argument("--branch", required=True)
    wt_create.add_argument("--ref", default="HEAD")
    wt_create.add_argument("--approve", action="store_true")
    wt_create.add_argument("--issued-by", default="local-user")
    wt_remove = worktree_commands.add_parser("remove", help="Remove a registered worktree")
    wt_remove.add_argument("agent_id")
    wt_remove.add_argument("--force", action="store_true")
    wt_remove.add_argument("--approve", action="store_true")
    wt_remove.add_argument("--issued-by", default="local-user")

    mcp = commands.add_parser("mcp", help="Manage MCP servers and expose W1 as an MCP server")
    mcp_commands = mcp.add_subparsers(dest="mcp_command", required=True)
    mcp_servers = mcp_commands.add_parser("servers", help="Manage configured MCP servers")
    mcp_server_commands = mcp_servers.add_subparsers(dest="mcp_servers_command", required=True)
    mcp_server_commands.add_parser("list", help="List redaction-safe MCP server configuration")
    mcp_add = mcp_server_commands.add_parser("add", help="Add an MCP server")
    mcp_add.add_argument("server_id")
    mcp_add.add_argument("--transport", choices=["stdio", "streamable_http"], required=True)
    mcp_add.add_argument("--command-json", help="JSON array used to launch a stdio server")
    mcp_add.add_argument("--url", help="Streamable HTTP endpoint")
    mcp_add.add_argument("--timeout-seconds", type=int, default=30)
    mcp_add.add_argument("--trust-mode", choices=["untrusted", "trusted"], default="untrusted")
    mcp_add.add_argument("--auto-approve-tool", action="append", default=[])
    mcp_add.add_argument("--allow-tool", action="append", default=[])
    mcp_add.add_argument("--env", action="append", default=[], help="CHILD_NAME=SOURCE_ENV")
    mcp_add.add_argument("--header-env", action="append", default=[], help="HEADER=SOURCE_ENV")
    mcp_add.add_argument("--bearer-token-env")
    mcp_add.add_argument("--workspace-root")
    mcp_add.add_argument("--allow-insecure-loopback", action="store_true")
    mcp_remove = mcp_server_commands.add_parser("remove", help="Remove an MCP server")
    mcp_remove.add_argument("server_id")
    mcp_probe = mcp_commands.add_parser("probe", help="Initialize a server and discover capabilities")
    mcp_probe.add_argument("server_id")
    mcp_serve = mcp_commands.add_parser("serve", help="Expose governed W1 tools through MCP")
    mcp_serve.add_argument("--stdio", action="store_true", default=True)

    tools = commands.add_parser("tools", help="Discover and call local or MCP tools through one registry")
    tool_commands = tools.add_subparsers(dest="tools_command", required=True)
    tool_list = tool_commands.add_parser("list", help="List unified tools")
    tool_list.add_argument("--server", action="append", default=[])
    tool_list.add_argument("--local-only", action="store_true")
    tool_plan = tool_commands.add_parser("plan", help="Validate and classify a tool call")
    tool_plan.add_argument("--call-id", required=True)
    tool_plan.add_argument("--name", required=True)
    tool_plan.add_argument("--arguments", required=True, help="JSON object file")
    tool_plan.add_argument("--server", action="append", default=[])
    tool_approve = tool_commands.add_parser("approve", help="Issue a one-time approval for a tool call")
    tool_approve.add_argument("--call-id", required=True)
    tool_approve.add_argument("--name", required=True)
    tool_approve.add_argument("--arguments", required=True)
    tool_approve.add_argument("--server", action="append", default=[])
    tool_approve.add_argument("--issued-by", required=True)
    tool_approve.add_argument("--ttl-seconds", type=int, default=600)
    tool_approve.add_argument("--output")
    tool_call = tool_commands.add_parser("call", help="Call a governed tool")
    tool_call.add_argument("--call-id", required=True)
    tool_call.add_argument("--name", required=True)
    tool_call.add_argument("--arguments", required=True)
    tool_call.add_argument("--server", action="append", default=[])
    tool_call.add_argument("--approval")
    tool_call.add_argument("--approve", action="store_true")
    tool_call.add_argument("--issued-by", default="local-user")
    tool_call.add_argument("--ttl-seconds", type=int, default=600)
    tool_history = tool_commands.add_parser("history", help="List audited tool calls")
    tool_history.add_argument("--limit", type=int, default=100)

    resources = commands.add_parser("resources", help="List or read MCP resources")
    resource_commands = resources.add_subparsers(dest="resources_command", required=True)
    resource_list = resource_commands.add_parser("list")
    resource_list.add_argument("--server", required=True)
    resource_read = resource_commands.add_parser("read")
    resource_read.add_argument("--server", required=True)
    resource_read.add_argument("--uri", required=True)

    prompts = commands.add_parser("prompts", help="List or get MCP prompts")
    prompt_commands = prompts.add_subparsers(dest="prompts_command", required=True)
    prompt_list = prompt_commands.add_parser("list")
    prompt_list.add_argument("--server", required=True)
    prompt_get = prompt_commands.add_parser("get")
    prompt_get.add_argument("--server", required=True)
    prompt_get.add_argument("--name", required=True)
    prompt_get.add_argument("--arguments", help="JSON object file")

    sandboxes = commands.add_parser("sandboxes", help="Run and inspect governed process or OCI sandboxes")
    sandbox_commands = sandboxes.add_subparsers(dest="sandboxes_command", required=True)
    sandbox_commands.add_parser("doctor", help="Detect OCI engines and local enforcement capabilities")
    sandbox_profiles = sandbox_commands.add_parser("profiles", help="Manage sandbox profiles")
    sandbox_profile_commands = sandbox_profiles.add_subparsers(dest="sandbox_profiles_command", required=True)
    sandbox_profile_commands.add_parser("list", help="List installed sandbox profiles")
    sandbox_profile_show = sandbox_profile_commands.add_parser("show", help="Show one sandbox profile")
    sandbox_profile_show.add_argument("profile_id")
    sandbox_profile_add = sandbox_profile_commands.add_parser("add", help="Add or replace a sandbox profile JSON")
    sandbox_profile_add.add_argument("--file", required=True)
    sandbox_plan = sandbox_commands.add_parser("plan", help="Resolve backend and enforcement without executing")
    sandbox_plan.add_argument("--request", required=True)
    sandbox_run = sandbox_commands.add_parser("run", help="Execute one sandbox request")
    sandbox_run.add_argument("--request", required=True)
    sandbox_status = sandbox_commands.add_parser("status", help="Show one sandbox execution")
    sandbox_status.add_argument("execution_id")
    sandbox_list = sandbox_commands.add_parser("list", help="List sandbox executions")
    sandbox_list.add_argument("--limit", type=int, default=100)
    sandbox_attest = sandbox_commands.add_parser("verify-attestation", help="Verify the local HMAC attestation for an execution")
    sandbox_attest.add_argument("execution_id")

    agents = commands.add_parser("agents", help="Run isolated agents concurrently in Git worktrees")
    agent_commands = agents.add_subparsers(dest="agents_command", required=True)
    agents_run = agent_commands.add_parser("run", help="Execute a parallel agent plan")
    agents_run.add_argument("--plan", required=True, help="Parallel plan JSON")
    agents_run.add_argument("--approved-by", required=True)
    agents_status = agent_commands.add_parser("status", help="Show one parallel run")
    agents_status.add_argument("run_id")

    merge = commands.add_parser("merge", help="Propose, review, and apply governed agent merges")
    merge_commands = merge.add_subparsers(dest="merge_command", required=True)
    merge_propose = merge_commands.add_parser("propose", help="Create and test an integration merge")
    merge_propose.add_argument("run_id")
    merge_propose.add_argument("--test-argv-json", help="JSON array command executed in the integration worktree")
    merge_review = merge_commands.add_parser("review", help="Record an independent merge review")
    merge_review.add_argument("proposal_id")
    merge_review.add_argument("--reviewer", required=True)
    merge_review.add_argument("--outcome", choices=["approved", "revision_required", "rejected"], required=True)
    merge_review.add_argument("--rationale", required=True)
    merge_apply = merge_commands.add_parser("apply", help="Fast-forward the target to an approved integration commit")
    merge_apply.add_argument("proposal_id")
    merge_apply.add_argument("--approved-by", required=True)

    memory = commands.add_parser("memory", help="Manage governed long-term memory and knowledge graph")
    memory_commands = memory.add_subparsers(dest="memory_command", required=True)
    memory_add = memory_commands.add_parser("add", help="Create an immutable memory revision from JSON")
    memory_add.add_argument("--file", required=True)
    memory_revise = memory_commands.add_parser("revise", help="Append a new revision to an existing memory")
    memory_revise.add_argument("memory_id")
    memory_revise.add_argument("--file", required=True)
    memory_get = memory_commands.add_parser("get", help="Read one accessible memory")
    memory_get.add_argument("memory_id")
    memory_get.add_argument("--include-terminal", action="store_true")
    memory_history = memory_commands.add_parser("history", help="Show append-only revision history")
    memory_history.add_argument("memory_id")
    memory_list = memory_commands.add_parser("list", help="List accessible memories in one project")
    memory_list.add_argument("--include-terminal", action="store_true")
    memory_list.add_argument("--limit", type=int, default=200)
    memory_search = memory_commands.add_parser("search", help="Hybrid structured, lexical, vector, and graph retrieval")
    memory_search.add_argument("query", nargs="?", default="")
    memory_search.add_argument("--kind", action="append", default=[])
    memory_search.add_argument("--tag", action="append", default=[])
    memory_search.add_argument("--subject")
    memory_search.add_argument("--predicate")
    memory_search.add_argument("--min-confidence", type=float, default=0.0)
    memory_search.add_argument("--include-stale", action="store_true")
    memory_search.add_argument("--include-conflicted", action="store_true")
    memory_search.add_argument("--include-global", action="store_true")
    memory_search.add_argument("--graph-anchor")
    memory_search.add_argument("--graph-depth", type=int, default=1)
    memory_search.add_argument("--limit", type=int, default=20)
    memory_context = memory_commands.add_parser("context", help="Build a provenance-preserving token-bounded context bundle")
    memory_context.add_argument("query")
    memory_context.add_argument("--token-budget", type=int)
    memory_context.add_argument("--kind", action="append", default=[])
    memory_context.add_argument("--tag", action="append", default=[])
    memory_context.add_argument("--include-global", action="store_true")
    memory_graph = memory_commands.add_parser("graph", help="Traverse accessible knowledge-graph edges")
    memory_graph.add_argument("--anchor", required=True)
    memory_graph.add_argument("--depth", type=int, default=1)
    memory_graph.add_argument("--limit", type=int, default=100)
    memory_revoke = memory_commands.add_parser("revoke", help="Append a terminal revocation revision")
    memory_revoke.add_argument("memory_id")
    memory_revoke.add_argument("--reason", required=True)
    memory_grant = memory_commands.add_parser("grant", help="Grant explicit memory permissions")
    memory_grant.add_argument("memory_id")
    memory_grant.add_argument("--to-type", required=True)
    memory_grant.add_argument("--to-id", required=True)
    memory_grant.add_argument("--permission", action="append", required=True)
    memory_ungrant = memory_commands.add_parser("ungrant", help="Revoke explicit memory permissions")
    memory_ungrant.add_argument("memory_id")
    memory_ungrant.add_argument("--from-type", required=True)
    memory_ungrant.add_argument("--from-id", required=True)
    memory_ungrant.add_argument("--permission", action="append", required=True)
    memory_conflicts = memory_commands.add_parser("conflicts", help="List or resolve contradictory memories")
    memory_conflict_commands = memory_conflicts.add_subparsers(dest="memory_conflicts_command", required=True)
    memory_conflict_list = memory_conflict_commands.add_parser("list")
    memory_conflict_list.add_argument("--status", choices=["open", "resolved"], default="open")
    memory_conflict_resolve = memory_conflict_commands.add_parser("resolve")
    memory_conflict_resolve.add_argument("conflict_id")
    memory_conflict_resolve.add_argument("--winner", required=True)
    memory_conflict_resolve.add_argument("--rationale", required=True)
    memory_verify = memory_commands.add_parser("verify", help="Verify event and revision hash chains")
    memory_export = memory_commands.add_parser("export", help="Export one governed project scope")
    memory_export.add_argument("--output")
    for memory_parser in (
        memory_add, memory_revise, memory_get, memory_history, memory_list, memory_search,
        memory_context, memory_graph, memory_revoke, memory_grant, memory_ungrant, memory_conflict_list,
        memory_conflict_resolve, memory_verify, memory_export,
    ):
        memory_parser.add_argument("--namespace")
        memory_parser.add_argument("--project")
        memory_parser.add_argument("--principal-type")
        memory_parser.add_argument("--principal-id")
        memory_parser.add_argument("--group", action="append", default=[])

    science = commands.add_parser("science", help="Run preregistered scientific and engineering studies")
    science_commands = science.add_subparsers(dest="science_command", required=True)
    science_create = science_commands.add_parser("create", help="Create a draft study from a preregistration plan")
    science_create.add_argument("--plan", required=True)
    science_commands.add_parser("list", help="List scientific studies")
    science_show = science_commands.add_parser("show", help="Export one study with provenance and integrity")
    science_show.add_argument("study_id")
    science_preregister = science_commands.add_parser("preregister", help="Lock the study plan before observations")
    science_preregister.add_argument("study_id")
    science_run = science_commands.add_parser("run", help="Execute the preregistered experiment in Secure Execution Fabric")
    science_run.add_argument("study_id")
    science_run.add_argument("--request", required=True)
    science_observe = science_commands.add_parser("observe", help="Append immutable observations from a JSON array")
    science_observe.add_argument("study_id")
    science_observe.add_argument("--file", required=True)
    science_observe.add_argument("--source-run-id")
    science_analyze = science_commands.add_parser("analyze", help="Run one preregistered or explicitly exploratory analysis")
    science_analyze.add_argument("study_id")
    science_analyze.add_argument("--analysis-id", required=True)
    science_analyze.add_argument("--exploratory-spec")
    science_evaluate = science_commands.add_parser("evaluate", help="Apply preregistered falsification criteria")
    science_evaluate.add_argument("study_id")
    science_replicate = science_commands.add_parser("replicate", help="Compare an independent replication study")
    science_replicate.add_argument("original_study_id")
    science_replicate.add_argument("replication_study_id")
    science_review = science_commands.add_parser("review", help="Record an independent scientific review")
    science_review.add_argument("study_id")
    science_review.add_argument("--reviewer-type", default="human")
    science_review.add_argument("--reviewer-id", required=True)
    science_review.add_argument("--outcome", choices=["approved", "revision_required", "rejected"], required=True)
    science_review.add_argument("--rationale", required=True)
    science_package = science_commands.add_parser("package", help="Build a verified reproducibility ZIP")
    science_package.add_argument("study_id")
    science_package.add_argument("--output", required=True)
    science_verify_package = science_commands.add_parser("verify-package", help="Verify hashes inside a reproducibility package")
    science_verify_package.add_argument("--file", required=True)
    science_publish = science_commands.add_parser("publish-memory", help="Explicitly publish an approved supported hypothesis to Memory Fabric")
    science_publish.add_argument("study_id")
    science_publish.add_argument("--hypothesis-id", required=True)
    science_publish.add_argument("--visibility", choices=["private", "team", "public"], default="private")
    science_verify = science_commands.add_parser("verify", help="Verify the scientific event and observation hash chains")
    science_verify.add_argument("--study-id")
    science_export = science_commands.add_parser("export", help="Export a complete study JSON")
    science_export.add_argument("study_id")
    science_export.add_argument("--output")
    science_bridge = science_commands.add_parser("bridge", help="Build a W1-CIP Evidence/Verification bridge manifest")
    science_bridge.add_argument("study_id")
    science_bridge.add_argument("--output")
    for science_parser in (
        science_create, science_show, science_preregister, science_run, science_observe,
        science_analyze, science_evaluate, science_replicate, science_package,
        science_publish, science_verify, science_export, science_bridge,
    ):
        science_parser.add_argument("--principal-type")
        science_parser.add_argument("--principal-id")

    workspace_ui = commands.add_parser("workspace", aliases=["console"], help="Run the local W1 Nexus visual workspace")
    workspace_ui_commands = workspace_ui.add_subparsers(dest="workspace_ui_command", required=True)
    workspace_serve = workspace_ui_commands.add_parser("serve", help="Serve the live local operations console")
    workspace_serve.add_argument("--host", default="127.0.0.1")
    workspace_serve.add_argument("--port", type=int, default=8765)
    workspace_serve.add_argument("--allow-operations", action="store_true", help="Enable opt-in state-changing console operations")
    workspace_serve.add_argument("--no-browser", action="store_true")
    workspace_snapshot = workspace_ui_commands.add_parser("snapshot", help="Export one redaction-safe workspace snapshot")
    workspace_snapshot.add_argument("--output")

    studio = commands.add_parser("studio", help="Inspect, version, review, and publish project artifacts")
    studio_commands = studio.add_subparsers(dest="studio_command", required=True)
    studio_tree = studio_commands.add_parser("tree", help="List the safe project file tree")
    studio_tree.add_argument("--path", default="")
    studio_tree.add_argument("--depth", type=int, default=5)
    studio_open = studio_commands.add_parser("open", help="Read one project file")
    studio_open.add_argument("path")
    studio_preview = studio_commands.add_parser("preview", help="Build a safe preview descriptor")
    studio_preview.add_argument("path")
    studio_diff = studio_commands.add_parser("git-diff", help="Show Git diff for the project or one path")
    studio_diff.add_argument("--path")
    studio_artifacts = studio_commands.add_parser("artifacts", help="Manage immutable artifact drafts")
    artifact_commands = studio_artifacts.add_subparsers(dest="artifact_command", required=True)
    artifact_commands.add_parser("list", help="List artifacts")
    artifact_create = artifact_commands.add_parser("create", help="Create a draft from a workspace file or content")
    artifact_create.add_argument("--artifact-id", required=True)
    artifact_create.add_argument("--path", required=True)
    artifact_create.add_argument("--title", required=True)
    artifact_create.add_argument("--created-by", default="local-author")
    artifact_create.add_argument("--content-file")
    artifact_save = artifact_commands.add_parser("save", help="Save an immutable draft version")
    artifact_save.add_argument("artifact_id")
    artifact_save.add_argument("--content-file", required=True)
    artifact_save.add_argument("--created-by", default="local-author")
    artifact_save.add_argument("--expected-parent-hash", required=True)
    artifact_history = artifact_commands.add_parser("history", help="Show versions, comments, reviews, and publications")
    artifact_history.add_argument("artifact_id")
    artifact_comment = artifact_commands.add_parser("comment", help="Add a review comment")
    artifact_comment.add_argument("artifact_id")
    artifact_comment.add_argument("--version", type=int, required=True)
    artifact_comment.add_argument("--author", required=True)
    artifact_comment.add_argument("--body", required=True)
    artifact_comment.add_argument("--line", type=int)
    artifact_review = artifact_commands.add_parser("review", help="Record an independent artifact review")
    artifact_review.add_argument("artifact_id")
    artifact_review.add_argument("--version", type=int, required=True)
    artifact_review.add_argument("--reviewer", required=True)
    artifact_review.add_argument("--outcome", choices=["approved", "changes_requested", "rejected"], required=True)
    artifact_review.add_argument("--rationale", required=True)
    artifact_publish = artifact_commands.add_parser("publish", help="Publish a reviewed artifact through Action Runtime")
    artifact_publish.add_argument("artifact_id")
    artifact_publish.add_argument("--published-by", default="local-owner")
    artifact_publish.add_argument("--approve-action", action="store_true")
    artifact_commands.add_parser("verify", help="Verify artifact version and event hash chains")
    studio_terminal = studio_commands.add_parser("terminal", help="Plan or run a governed terminal command")
    terminal_commands = studio_terminal.add_subparsers(dest="terminal_command", required=True)
    for terminal_name in ("plan", "run"):
        terminal_parser = terminal_commands.add_parser(terminal_name)
        terminal_parser.add_argument("--action-id", required=True)
        terminal_parser.add_argument("--argv-json", required=True)
        terminal_parser.add_argument("--cwd", default=".")
        terminal_parser.add_argument("--issued-by", default="local-owner")
        terminal_parser.add_argument("--approve", action="store_true")

    office = commands.add_parser("office", help="Create, patch, review, and export governed office artifacts")
    office_commands = office.add_subparsers(dest="office_command", required=True)
    office_validate = office_commands.add_parser("validate", help="Validate a canonical W1 artifact model")
    office_validate.add_argument("--spec", required=True)
    office_create = office_commands.add_parser("create", help="Create an immutable office artifact")
    office_create.add_argument("--spec", required=True)
    office_create.add_argument("--created-by", default="local-author")
    office_commands.add_parser("list", help="List office artifacts")
    office_show = office_commands.add_parser("show", help="Show the current office artifact model")
    office_show.add_argument("artifact_id")
    office_history = office_commands.add_parser("history", help="Show revisions, reviews, and exports")
    office_history.add_argument("artifact_id")
    office_patch = office_commands.add_parser("patch", help="Apply a reviewable JSON Pointer patch")
    office_patch.add_argument("artifact_id")
    office_patch.add_argument("--patch", required=True)
    office_patch.add_argument("--created-by", default="local-author")
    office_patch.add_argument("--expected-parent-hash", required=True)
    office_review = office_commands.add_parser("review", help="Record an independent artifact review")
    office_review.add_argument("artifact_id")
    office_review.add_argument("--version", type=int, required=True)
    office_review.add_argument("--reviewer", required=True)
    office_review.add_argument("--outcome", choices=["approved", "changes_requested", "rejected"], required=True)
    office_review.add_argument("--rationale", required=True)
    office_export = office_commands.add_parser("export", help="Export a reviewed artifact through Action Runtime")
    office_export.add_argument("artifact_id")
    office_export.add_argument("--format", choices=["docx", "xlsx", "pptx", "pdf", "json"], required=True)
    office_export.add_argument("--output", required=True)
    office_export.add_argument("--exported-by", default="local-owner")
    office_export.add_argument("--approve-action", action="store_true")
    office_commands.add_parser("verify", help="Verify office revision and event hash chains")
    office_demo = office_commands.add_parser("demo", help="Run the local universal artifact benchmark")
    office_demo.add_argument("--output-dir")

    plugins = commands.add_parser("plugins", help="Manage governed W1 plugins and the stable adoption SDK")
    plugin_commands = plugins.add_subparsers(dest="plugin_command", required=True)
    plugin_commands.add_parser("benchmark", help="Run deterministic plugin/adoption probes")
    plugin_validate = plugin_commands.add_parser("validate", help="Validate a local plugin manifest, tree digest, and compatibility")
    plugin_validate.add_argument("source")
    plugin_scaffold = plugin_commands.add_parser("scaffold", help="Create a minimal plugin using only the stable w1cip.sdk surface")
    plugin_scaffold.add_argument("destination")
    plugin_scaffold.add_argument("--id", required=True, dest="plugin_id")
    plugin_scaffold.add_argument("--name", required=True)
    plugin_install = plugin_commands.add_parser("install", help="Copy and integrity-lock a local plugin into this workspace")
    plugin_install.add_argument("source")
    plugin_install.add_argument("--grant-permission", action="append", default=[])
    plugin_install.add_argument("--enable", action="store_true")
    plugin_commands.add_parser("list", help="List workspace plugins")
    plugin_show = plugin_commands.add_parser("show", help="Show one plugin record without secrets")
    plugin_show.add_argument("plugin_id")
    plugin_verify = plugin_commands.add_parser("verify", help="Verify the installed plugin tree against its locked digest")
    plugin_verify.add_argument("plugin_id")
    plugin_conf = plugin_commands.add_parser("conformance", help="Run the stable plugin contract conformance suite against a source directory or installed id")
    plugin_conf.add_argument("target")
    plugin_conf.add_argument("--timeout", type=float, default=5.0)
    plugin_enable = plugin_commands.add_parser("enable", help="Enable a compatible plugin after permissions and integrity checks")
    plugin_enable.add_argument("plugin_id")
    plugin_disable = plugin_commands.add_parser("disable", help="Disable a plugin")
    plugin_disable.add_argument("plugin_id")
    plugin_grant = plugin_commands.add_parser("grant", help="Grant declared W1 host permissions to an installed plugin")
    plugin_grant.add_argument("plugin_id")
    plugin_grant.add_argument("permission", nargs="+")
    plugin_revoke = plugin_commands.add_parser("revoke", help="Revoke permissions; the plugin auto-disables if required grants become incomplete")
    plugin_revoke.add_argument("plugin_id")
    plugin_revoke.add_argument("permission", nargs="+")
    plugin_health = plugin_commands.add_parser("health", help="Run the plugin lifecycle health probe in its subprocess host")
    plugin_health.add_argument("plugin_id")
    plugin_health.add_argument("--timeout", type=float, default=5.0)

    desktop = commands.add_parser("desktop", help="Run the local-first W1 Desktop and Artifact Studio")
    desktop_commands = desktop.add_subparsers(dest="desktop_command", required=True)
    desktop_doctor = desktop_commands.add_parser("doctor", help="Inspect the available desktop shell backend")
    desktop_launch = desktop_commands.add_parser("launch", help="Open a native webview when available, otherwise the installable browser shell")
    desktop_launch.add_argument("--host", default="127.0.0.1")
    desktop_launch.add_argument("--port", type=int, default=8766)
    desktop_launch.add_argument("--allow-operations", action="store_true")
    desktop_serve = desktop_commands.add_parser("serve", help="Serve the installable local desktop shell")
    desktop_serve.add_argument("--host", default="127.0.0.1")
    desktop_serve.add_argument("--port", type=int, default=8766)
    desktop_serve.add_argument("--allow-operations", action="store_true")
    desktop_serve.add_argument("--no-browser", action="store_true")

    packaging = commands.add_parser("packaging", help="Inspect and generate native desktop packaging assets")
    packaging_commands = packaging.add_subparsers(dest="packaging_command", required=True)
    packaging_doctor = packaging_commands.add_parser("doctor", help="Inspect native build tools without claiming unavailable platform builds")
    packaging_doctor.add_argument("--target", choices=["windows", "macos", "linux"], default="windows")
    packaging_doctor.add_argument("--architecture", default="x64")
    packaging_commands.add_parser("benchmark", help="Run deterministic packaging, deep-link, update-integrity, and service-lifecycle probes")
    packaging_rc = packaging_commands.add_parser("rc-check", help="Verify Windows release-candidate source, brand, and packaging invariants without claiming a signed build")
    packaging_rc.add_argument("--source-root", default=".")
    packaging_sources = packaging_commands.add_parser("sources", help="Generate deterministic Windows PyInstaller/Inno Setup sources")
    packaging_sources.add_argument("--output", default="packaging/windows")
    packaging_sources.add_argument("--architecture", default="x64")
    packaging_link = packaging_commands.add_parser("deep-link", help="Parse and validate one W1 deep link without executing shell commands")
    packaging_link.add_argument("uri")
    packaging_project = packaging_commands.add_parser("project", help="Create or inspect a .w1nexus project descriptor")
    packaging_project_commands = packaging_project.add_subparsers(dest="packaging_project_command", required=True)
    packaging_project_create = packaging_project_commands.add_parser("create", help="Create a relative, non-secret W1 project descriptor")
    packaging_project_create.add_argument("path")
    packaging_project_create.add_argument("--name", required=True)
    packaging_project_create.add_argument("--workspace-path", default=".")
    packaging_project_create.add_argument("--open-target")
    packaging_project_show = packaging_project_commands.add_parser("show", help="Inspect and resolve a W1 project descriptor")
    packaging_project_show.add_argument("path")
    packaging_service = packaging_commands.add_parser("service", help="Control the loopback W1 Desktop service with PID-reuse protection")
    packaging_service_commands = packaging_service.add_subparsers(dest="packaging_service_command", required=True)
    packaging_service_commands.add_parser("status")
    packaging_service_start = packaging_service_commands.add_parser("start")
    packaging_service_start.add_argument("--port", type=int, default=8766)
    packaging_service_stop = packaging_service_commands.add_parser("stop")
    packaging_service_stop.add_argument("--timeout", type=float, default=5.0)
    packaging_verify = packaging_commands.add_parser("verify-update", help="Verify update manifest selection plus file size/SHA-256 before native signature verification")
    packaging_verify.add_argument("--manifest", required=True)
    packaging_verify.add_argument("--artifact", required=True)
    packaging_verify.add_argument("--platform", default="windows")
    packaging_verify.add_argument("--architecture", default="x64")
    packaging_verify.add_argument("--allow-missing-signature-metadata", action="store_true")

    collab = commands.add_parser("collab", aliases=["collaboration"], help="Manage optional self-hosted W1 team collaboration and project sync")
    collab_commands = collab.add_subparsers(dest="collab_command", required=True)
    collab_commands.add_parser("benchmark", help="Run deterministic local team/sync/TLS/audit probes without W1 cloud")

    collab_team = collab_commands.add_parser("team", help="Manage collaboration teams")
    collab_team_commands = collab_team.add_subparsers(dest="collab_team_command", required=True)
    collab_team_create = collab_team_commands.add_parser("create")
    collab_team_create.add_argument("--name", required=True)
    collab_team_create.add_argument("--owner", required=True)
    collab_team_create.add_argument("--id", dest="team_id")
    collab_team_list = collab_team_commands.add_parser("list")
    collab_team_list.add_argument("--principal")
    collab_team_show = collab_team_commands.add_parser("show")
    collab_team_show.add_argument("team_id")
    collab_team_members = collab_team_commands.add_parser("members")
    collab_team_members.add_argument("team_id")
    collab_team_members.add_argument("--actor", required=True)
    collab_team_role = collab_team_commands.add_parser("set-role")
    collab_team_role.add_argument("team_id")
    collab_team_role.add_argument("principal_id")
    collab_team_role.add_argument("role", choices=["owner", "admin", "member", "viewer"])
    collab_team_role.add_argument("--actor", required=True)
    collab_team_remove = collab_team_commands.add_parser("remove-member")
    collab_team_remove.add_argument("team_id")
    collab_team_remove.add_argument("principal_id")
    collab_team_remove.add_argument("--actor", required=True)

    collab_invite = collab_commands.add_parser("invite", help="Create, accept, or revoke one-time team invitations")
    collab_invite_commands = collab_invite.add_subparsers(dest="collab_invite_command", required=True)
    collab_invite_create = collab_invite_commands.add_parser("create")
    collab_invite_create.add_argument("team_id")
    collab_invite_create.add_argument("--role", choices=["admin", "member", "viewer"], default="member")
    collab_invite_create.add_argument("--created-by", required=True)
    collab_invite_create.add_argument("--expires-hours", type=int, default=72)
    collab_invite_accept = collab_invite_commands.add_parser("accept")
    collab_invite_accept.add_argument("--principal", required=True)
    collab_invite_accept.add_argument("--stdin", action="store_true", help="Read invitation token from stdin instead of a hidden prompt")
    collab_invite_revoke = collab_invite_commands.add_parser("revoke")
    collab_invite_revoke.add_argument("invitation_id")
    collab_invite_revoke.add_argument("--actor", required=True)

    collab_project = collab_commands.add_parser("project", help="Manage team projects and synchronized state")
    collab_project_commands = collab_project.add_subparsers(dest="collab_project_command", required=True)
    collab_project_create = collab_project_commands.add_parser("create")
    collab_project_create.add_argument("team_id")
    collab_project_create.add_argument("--name", required=True)
    collab_project_create.add_argument("--created-by", required=True)
    collab_project_create.add_argument("--id", dest="project_id")
    collab_project_list = collab_project_commands.add_parser("list")
    collab_project_list.add_argument("team_id")
    collab_project_list.add_argument("--principal", required=True)
    collab_project_state = collab_project_commands.add_parser("state")
    collab_project_state.add_argument("project_id")
    collab_project_state.add_argument("--principal", required=True)

    collab_share = collab_commands.add_parser("share", help="Grant or revoke project access")
    collab_share_commands = collab_share.add_subparsers(dest="collab_share_command", required=True)
    collab_share_grant = collab_share_commands.add_parser("grant")
    collab_share_grant.add_argument("project_id")
    collab_share_grant.add_argument("principal_id")
    collab_share_grant.add_argument("access", choices=["viewer", "editor", "manager"])
    collab_share_grant.add_argument("--actor", required=True)
    collab_share_revoke = collab_share_commands.add_parser("revoke")
    collab_share_revoke.add_argument("project_id")
    collab_share_revoke.add_argument("principal_id")
    collab_share_revoke.add_argument("--actor", required=True)

    collab_token = collab_commands.add_parser("token", help="Issue or revoke hash-stored self-hosted access tokens")
    collab_token_commands = collab_token.add_subparsers(dest="collab_token_command", required=True)
    collab_token_issue = collab_token_commands.add_parser("issue")
    collab_token_issue.add_argument("team_id")
    collab_token_issue.add_argument("--principal", required=True)
    collab_token_issue.add_argument("--label", default="client")
    collab_token_issue.add_argument("--expires-hours", type=int, default=24 * 30)
    collab_token_revoke = collab_token_commands.add_parser("revoke")
    collab_token_revoke.add_argument("token_id")
    collab_token_revoke.add_argument("--actor", required=True)

    collab_replica = collab_commands.add_parser("replica", help="Register a synchronization device identity")
    collab_replica_commands = collab_replica.add_subparsers(dest="collab_replica_command", required=True)
    collab_replica_register = collab_replica_commands.add_parser("register")
    collab_replica_register.add_argument("team_id")
    collab_replica_register.add_argument("--principal", required=True)
    collab_replica_register.add_argument("--id", dest="device_id")
    collab_replica_register.add_argument("--label", default="device")

    collab_sync = collab_commands.add_parser("sync", help="Apply/pull local replicated project events with explicit conflict detection")
    collab_sync_commands = collab_sync.add_subparsers(dest="collab_sync_command", required=True)
    collab_sync_push = collab_sync_commands.add_parser("push")
    collab_sync_push.add_argument("--mutation", required=True, help="JSON file containing one SyncMutation")
    collab_sync_push.add_argument("--principal", required=True)
    collab_sync_pull = collab_sync_commands.add_parser("pull")
    collab_sync_pull.add_argument("project_id")
    collab_sync_pull.add_argument("--principal", required=True)
    collab_sync_pull.add_argument("--after", type=int, default=0)
    collab_sync_pull.add_argument("--limit", type=int, default=500)

    collab_conflicts = collab_commands.add_parser("conflicts", help="Inspect and explicitly resolve sync conflicts")
    collab_conflict_commands = collab_conflicts.add_subparsers(dest="collab_conflict_command", required=True)
    collab_conflict_list = collab_conflict_commands.add_parser("list")
    collab_conflict_list.add_argument("project_id")
    collab_conflict_list.add_argument("--principal", required=True)
    collab_conflict_resolve = collab_conflict_commands.add_parser("resolve")
    collab_conflict_resolve.add_argument("conflict_id")
    collab_conflict_resolve.add_argument("--mutation", required=True)
    collab_conflict_resolve.add_argument("--principal", required=True)

    collab_audit = collab_commands.add_parser("audit", help="Inspect or verify the federated collaboration audit chain")
    collab_audit_commands = collab_audit.add_subparsers(dest="collab_audit_command", required=True)
    collab_audit_list = collab_audit_commands.add_parser("list")
    collab_audit_list.add_argument("team_id")
    collab_audit_list.add_argument("--principal", required=True)
    collab_audit_list.add_argument("--limit", type=int, default=100)
    collab_audit_verify = collab_audit_commands.add_parser("verify")
    collab_audit_verify.add_argument("team_id")

    collab_serve = collab_commands.add_parser("serve", help="Serve the Collaboration Fabric; non-loopback binding requires TLS")
    collab_serve.add_argument("--host", default="127.0.0.1")
    collab_serve.add_argument("--port", type=int, default=8780)
    collab_serve.add_argument("--certfile")
    collab_serve.add_argument("--keyfile")

    release = commands.add_parser("release", help="Run release hardening, evidence, and external-benchmark gates")
    release_commands = release.add_subparsers(dest="release_command", required=True)
    release_commands.add_parser("benchmark", help="Run deterministic local fuzz/load/recovery/release-hardening probes")
    release_commands.add_parser("catalog", help="Show registered external benchmark families without inventing scores")
    release_manifest = release_commands.add_parser("manifest", help="Hash the package-relevant source tree for release evidence")
    release_manifest.add_argument("--output")
    release_dependencies = release_commands.add_parser("dependencies", help="Emit direct dependency inventory without online vulnerability claims")
    release_dependencies.add_argument("--output")
    release_threat = release_commands.add_parser("threat-model", help="Render the current machine-backed threat model")
    release_threat.add_argument("--output")
    release_gate_parser = release_commands.add_parser("gate", help="Evaluate development or public-release gates")
    release_gate_parser.add_argument("--public", action="store_true", help="Require an intentionally selected LICENSE and public-release prerequisites")
    release_record = release_commands.add_parser("record", help="Record one externally executed benchmark result with raw evidence SHA-256")
    release_record.add_argument("--result", required=True, help="JSON result document")
    release_record.add_argument("--evidence", required=True, help="Raw upstream result/log artifact")
    release_commands.add_parser("results", help="List immutable external benchmark result records")
    release_verify = release_commands.add_parser("verify-result", help="Verify a result record against its raw evidence artifact")
    release_verify.add_argument("result_id")
    release_verify.add_argument("--evidence", required=True)

    capabilities = commands.add_parser("capabilities", help="Show implemented strengths and explicit limits")
    capabilities.add_argument("--compact", action="store_true")

    bench_lab = commands.add_parser("benchmark-lab", help="NEXUS Frontier Model Benchmarking & Telemetry Lab")
    bench_lab_commands = bench_lab.add_subparsers(dest="bench_lab_command", required=True)

    bl_integrity = bench_lab_commands.add_parser("integrity", help="Verify benchmark adapter integrity against official specifications")
    bl_integrity.add_argument("--all", action="store_true", default=True, help="Validate all 9 benchmark suites")

    bl_availability = bench_lab_commands.add_parser("availability", help="Run dedicated 10-point model availability and entitlement diagnostic")
    bl_availability.add_argument("--all", action="store_true", default=True, help="Probe all 3 candidate models")

    bl_preflight = bench_lab_commands.add_parser("preflight", help="Run 8 preflight qualification checks")
    bl_preflight.add_argument("--model", choices=["all", "kimi", "muse", "deepseek"], default="all")

    bl_smoke = bench_lab_commands.add_parser("smoke", help="Run benchmark smoke tests across all 9 suites")
    bl_smoke.add_argument("--all", action="store_true", default=False, help="Run across all models and suites")
    bl_smoke.add_argument("--model", choices=["all", "kimi", "muse", "deepseek"], default="all")
    bl_smoke.add_argument("--mode", choices=["both", "raw", "nexus"], default="both")
    bl_smoke.add_argument("--workers", type=int, default=3, help="Concurrency workers (default 3)")

    bl_run = bench_lab_commands.add_parser("run", help="Execute complete or partial benchmark evaluation matrix")
    bl_run.add_argument("--model", choices=["all", "kimi", "muse", "deepseek"], default="all")
    bl_run.add_argument("--benchmark", default="all", help="Benchmark name or 'all'")
    bl_run.add_argument("--mode", choices=["both", "raw", "nexus"], default="both")
    bl_run.add_argument("--workers", type=int, default=1)
    bl_run.add_argument("--dry-run", action="store_true")
    bl_run.add_argument("--no-resume", action="store_true")

    bl_report = bench_lab_commands.add_parser("report", help="Generate HTML, CSV, and Markdown reports")
    bl_report.add_argument("--input-file", default=None)

    bl_dash = bench_lab_commands.add_parser("dashboard", help="Display or open interactive telemetry dashboard")
    bl_dash.add_argument("--open", action="store_true", help="Open dashboard in default web browser")

    return parser


def _add_run_arguments(parser: argparse.ArgumentParser, *, include_identifiers: bool) -> None:
    if include_identifiers:
        parser.add_argument("--run-id", required=True)
        parser.add_argument("--session-id", required=True)
        parser.add_argument("--goal-id", required=True)
        parser.add_argument("--team-plan-id", required=True)
        parser.add_argument("--resource-plan-id", required=True)
    parser.add_argument("--context", help="Context JSON; defaults to .w1nexus/context.json")
    parser.add_argument("--domain", action="append", default=[])
    parser.add_argument("--max-attempts", type=int, default=2)
    parser.add_argument("--events", choices=["text", "json", "silent"], default="text")


def _paths(args: argparse.Namespace, *, required: bool = True) -> WorkspacePaths:
    if required:
        return require_workspace(args.workspace)
    return WorkspacePaths.from_root(args.workspace)


def _open_store(paths: WorkspacePaths) -> SessionStore:
    return SessionStore(paths.session_db, trusted_recorders=trusted_recorders(paths))


def _save_run_invocation(paths: WorkspacePaths, payload: Mapping[str, Any]) -> None:
    write_json(paths.runs_dir / f"{payload['run_id']}.json", dict(payload))


def _load_run_invocation(paths: WorkspacePaths, run_id: str) -> dict[str, Any]:
    path = paths.runs_dir / f"{run_id}.json"
    if not path.is_file():
        raise CLIError(
            "run_invocation_not_found",
            f"Saved invocation not found for {run_id}. Re-run with the original identifiers.",
        )
    return load_json(path)


def _result_payload(result: OrchestratorRunResult) -> dict[str, Any]:
    return {
        "run_id": result.run_id,
        "session_id": result.session_id,
        "status": result.status,
        "tasks": [asdict(task) for task in result.tasks],
        "final_output": result.final_output,
        "appended_message_ids": list(result.appended_message_ids),
        "resource_plan": result.resource_plan,
        "disclosures": list(result.disclosures),
        "error_code": result.error_code,
    }


def _run_human(payload: Mapping[str, Any], terminal: Terminal) -> str:
    rows = []
    for task in payload["tasks"]:
        status = task["status"]
        if status == "completed":
            status = terminal.success(status)
        elif status in {"blocked", "failed", "awaiting_reset", "awaiting_user"}:
            status = terminal.warning(status)
        rows.append(
            (
                task["task_id"],
                status,
                task.get("resource_id") or "-",
                task.get("attempts", 0),
                "yes" if task.get("requires_extra_review") else "no",
            )
        )
    summary = table(
        ["TASK", "STATUS", "RESOURCE", "ATTEMPTS", "EXTRA REVIEW"],
        rows,
    )
    output = [
        terminal.heading(f"Run {payload['run_id']}: {payload['status']}"),
        "",
        summary,
    ]
    if payload.get("disclosures"):
        output.extend(["", terminal.warning("Routing disclosures:"), canonical_pretty(payload["disclosures"])])
    if payload.get("final_output") is not None:
        output.extend(["", terminal.heading("Final output"), canonical_pretty(payload["final_output"])])
    if payload.get("error_code"):
        output.extend(["", terminal.error(f"Error: {payload['error_code']}")])
    return "\n".join(output)


def command_init(args: argparse.Namespace, terminal: Terminal) -> int:
    root = args.path or args.workspace
    paths = initialize_workspace(root, force=args.force)
    payload = {
        "workspace": str(paths.root),
        "state_directory": str(paths.state_dir),
        "config": str(paths.config),
        "providers": str(paths.providers),
        "context": str(paths.context),
        "memory_database": str(paths.memory_db),
        "scientific_database": str(paths.science_db),
        "model_access_database": str(paths.model_access_db),
        "model_access_config": str(paths.model_access_config),
        "credential_broker_database": str(paths.credential_broker_db),
    }
    terminal.emit(
        payload,
        human=(
            f"{terminal.success('Initialized W1 workspace')}\n"
            f"Root: {paths.root}\n"
            f"Config: {paths.config}\n"
            "Secrets remain environment references or OS-native vault entries; no secret values were written to workspace files."
        ),
    )
    return 0


def command_doctor(args: argparse.Namespace, terminal: Terminal) -> int:
    paths = _paths(args)
    payload = doctor(paths, deep=args.deep)
    rows = []
    for check in payload["checks"]:
        if check["ok"]:
            state = terminal.success("PASS")
        elif check["severity"] == "warning":
            state = terminal.warning("WARN")
        else:
            state = terminal.error("FAIL")
        rows.append((state, check["name"], json.dumps(check["details"], ensure_ascii=False)))
    terminal.emit(payload, human=table(["STATE", "CHECK", "DETAILS"], rows))
    return 0 if payload["ok"] else 1


def command_providers(args: argparse.Namespace, terminal: Terminal) -> int:
    paths = _paths(args)
    inventory = provider_inventory(paths)
    if args.providers_command == "validate":
        built = build_providers(paths)
        payload = {
            "valid": True,
            "enabled_resources": sorted(built),
            "network_requests_made": False,
            "secret_values_loaded": False,
        }
        terminal.emit(payload, human=f"{terminal.success('Provider configuration valid')}\n" + "\n".join(sorted(built)))
        return 0
    rows = [
        (
            item["resource_id"],
            item["type"],
            item["model"],
            "yes" if item["enabled"] else "no",
            "yes" if item["secret_present"] else "no",
            "yes" if item["model_pinned"] else "no",
        )
        for item in inventory
    ]
    terminal.emit(
        {"providers": inventory},
        human=table(["RESOURCE", "CONNECTOR", "MODEL", "ENABLED", "SECRET", "PINNED"], rows),
    )
    return 0





def _credential_value_from_input(*, stdin: bool, prompt: str) -> str:
    value = sys.stdin.read().rstrip("\r\n") if stdin else getpass.getpass(prompt)
    if not value:
        raise CLIError("credential_value_required", "Credential value cannot be empty.")
    return value


def _credential_store(paths: WorkspacePaths) -> CredentialBrokerStore:
    return CredentialBrokerStore(paths.credential_broker_db)


def _credential_broker(paths: WorkspacePaths, store: CredentialBrokerStore) -> CredentialBroker:
    try:
        vault = create_native_credential_vault(workspace_credential_namespace(paths.root))
    except Exception as exc:
        code = getattr(exc, "code", "credential_vault_unavailable")
        raise CLIError(str(code), str(exc)) from exc
    return CredentialBroker(store, vault)


def command_credentials(args: argparse.Namespace, terminal: Terminal) -> int:
    paths = _paths(args)
    with _credential_store(paths) as store:
        if args.credentials_command == "list":
            items = store.list_credentials()
            rows = [
                (item["credential_id"], item.get("provider_id") or "-", item.get("label") or "-", item["credential_reference"])
                for item in items
            ]
            terminal.emit({"credentials": items, "count": len(items)}, human=table(["CREDENTIAL", "PROVIDER", "LABEL", "REFERENCE"], rows))
            return 0
        broker = _credential_broker(paths, store)
        if args.credentials_command == "set":
            value = _credential_value_from_input(stdin=args.stdin, prompt=f"Credential value for {args.credential_id}: ")
            reference = broker.store_credential(args.credential_id, value, provider_id=args.provider, label=args.label)
            terminal.emit(
                {"credential_id": args.credential_id, "credential_reference": reference, "value_persisted_in_sqlite": False, "vault_backend": broker.vault.backend_name},
                human=f"Stored {args.credential_id} in {broker.vault.backend_name}. Reference: {reference}",
            )
            return 0
        removed = broker.delete_credential(args.credential_id)
        terminal.emit({"credential_id": args.credential_id, "removed": removed}, human=f"Removed: {removed}")
        return 0 if removed else 1


def command_accounts(args: argparse.Namespace, terminal: Terminal) -> int:
    paths = _paths(args)
    if args.accounts_command == "vault":
        payload = credential_vault_status(workspace_credential_namespace(paths.root))
        terminal.emit(
            payload,
            human=(
                f"Native credential vault: {'AVAILABLE' if payload['available'] else 'UNAVAILABLE'}\n"
                f"Backend: {payload.get('backend') or '-'}\n"
                "Insecure file fallback: disabled"
            ),
        )
        return 0 if payload["available"] else 1
    if args.accounts_command == "benchmark":
        payload = run_credential_broker_benchmark()
        terminal.emit(
            payload,
            human=(
                f"Credential broker benchmark: {'PASS' if payload['passed'] else 'FAIL'}\n"
                f"Probes: {sum(bool(v) for v in payload['probes'].values())}/{len(payload['probes'])}\n"
                "Live provider network calls: 0"
            ),
        )
        return 0 if payload["passed"] else 1

    with _credential_store(paths) as store:
        if args.accounts_command == "list":
            accounts = [item.redacted_dict() for item in store.list_accounts()]
            rows = [
                (item["account_id"], item["provider_id"], item.get("display_name") or "-", item["status"], "yes" if item["refresh_present"] else "no")
                for item in accounts
            ]
            terminal.emit({"accounts": accounts, "count": len(accounts)}, human=table(["ACCOUNT", "PROVIDER", "NAME", "STATUS", "REFRESH"], rows))
            return 0
        if args.accounts_command == "show":
            account = store.get_account(args.account_id).redacted_dict()
            terminal.emit(account)
            return 0
        if args.accounts_command == "audit":
            events = store.audit_events(limit=args.limit)
            terminal.emit({"events": events, "count": len(events), "verification": store.verify_audit_chain()})
            return 0
        if args.accounts_command == "providers":
            if args.account_provider_command == "list":
                providers = [item.redacted_dict() | {"capabilities": item.capabilities()} for item in store.list_providers()]
                rows = [
                    (item["provider_id"], item["issuer"], "yes" if item["capabilities"]["authorization_code"] else "no", "yes" if item["capabilities"]["device_authorization"] else "no")
                    for item in providers
                ]
                terminal.emit({"providers": providers, "count": len(providers)}, human=table(["PROVIDER", "ISSUER", "AUTH CODE", "DEVICE"], rows))
                return 0
            if args.account_provider_command == "add":
                provider = provider_from_mapping(load_json(args.file))
                store.put_provider(provider)
                store.audit("provider.registered", provider_id=provider.provider_id, details={"issuer": provider.issuer, "source": "cli"})
                terminal.emit(provider.redacted_dict() | {"capabilities": provider.capabilities()}, human=f"Added OAuth provider: {provider.provider_id}")
                return 0
            if args.account_provider_command == "capabilities":
                provider = store.get_provider(args.provider_id)
                terminal.emit({"provider_id": provider.provider_id, "capabilities": provider.capabilities()})
                return 0
            broker = CredentialBroker(store, MemoryCredentialVault())
            provider = broker.discover_provider(args.provider_id)
            terminal.emit(provider.redacted_dict() | {"capabilities": provider.capabilities()}, human=f"Discovered OAuth metadata for {provider.provider_id}")
            return 0

        broker = _credential_broker(paths, store)
        if args.accounts_command == "login":
            started = broker.start_authorization(
                args.provider_id,
                client_id=args.client_id,
                redirect_uri=args.redirect_uri,
                scopes=tuple(args.scope),
                account_id=args.account_id,
            )
            payload = asdict(started)
            if args.open:
                payload["browser_opened"] = bool(webbrowser.open(started.authorization_url, new=1, autoraise=True))
            terminal.emit(
                payload,
                human=(
                    f"Authorization started for {started.provider_id}.\n"
                    f"Open: {started.authorization_url}\n"
                    f"State: {started.state}\n"
                    "After the provider redirects back, run `w1 accounts complete --state <state>` and enter the one-time code securely."
                ),
            )
            return 0
        if args.accounts_command == "complete":
            code = _credential_value_from_input(stdin=args.code_stdin, prompt="One-time authorization code: ")
            account = broker.complete_authorization(state=args.state, code=code, display_name=args.display_name)
            terminal.emit(account.redacted_dict(), human=f"Connected account: {account.account_id} ({account.provider_id})")
            return 0
        if args.accounts_command == "device-start":
            started = broker.start_device_authorization(
                args.provider_id,
                client_id=args.client_id,
                scopes=tuple(args.scope),
                account_id=args.account_id,
            )
            terminal.emit(
                asdict(started),
                human=(
                    f"Device authorization started.\nVerification URL: {started.verification_uri}\n"
                    f"User code: {started.user_code}\nDevice session: {started.device_id}"
                ),
            )
            return 0
        if args.accounts_command == "device-poll":
            try:
                account = broker.poll_device_authorization(args.device_id, display_name=args.display_name)
            except OAuthAuthorizationPending as exc:
                terminal.emit({"device_id": args.device_id, "status": "authorization_pending"}, human="Authorization is still pending.")
                return 3
            except OAuthSlowDown as exc:
                terminal.emit({"device_id": args.device_id, "status": "slow_down"}, human="Polling too quickly; wait for the provider interval.")
                return 3
            terminal.emit(account.redacted_dict(), human=f"Connected account: {account.account_id}")
            return 0
        if args.accounts_command == "refresh":
            account = broker.refresh_account(args.account_id)
            terminal.emit(account.redacted_dict(), human=f"Refreshed account: {account.account_id}")
            return 0
        if args.accounts_command == "revoke":
            removed = broker.revoke_account(args.account_id)
            terminal.emit({"account_id": args.account_id, "revoked": True, "local_removed": removed})
            return 0
        removed = broker.remove_account(args.account_id)
        terminal.emit({"account_id": args.account_id, "removed": removed})
        return 0 if removed else 1


def command_connections(args: argparse.Namespace, terminal: Terminal) -> int:
    paths = _paths(args)
    connection_store = AIConnectionStore(paths.ai_connections_db)
    model_store = model_access_store(paths)
    if args.connections_command == "catalog":
        payload = {"providers": provider_catalog(), "count": len(provider_catalog())}
        terminal.emit(
            payload,
            human=table(
                ["PROVIDER", "NAME", "AUTH", "FAMILY"],
                [
                    (item["provider_id"], item["display_name"], ",".join(item["supported_auth_modes"]), item["family"])
                    for item in payload["providers"]
                ],
            ),
        )
        return 0
    if args.connections_command == "benchmark":
        payload = run_ai_connections_benchmark()
        terminal.emit(
            payload,
            human=(
                f"AI Connections benchmark: {'PASS' if payload['passed'] else 'FAIL'}\n"
                f"Probes: {sum(1 for value in payload['probes'].values() if value)}/{len(payload['probes'])}\n"
                f"Provider catalog: {payload['metrics']['providers_in_catalog']}\n"
                f"W1-owned cloud calls: {payload['metrics']['w1_owned_cloud_calls']}"
            ),
        )
        return 0 if payload["passed"] else 1
    if args.connections_command == "list":
        service = AIConnectionService(connection_store, model_store)
        inventory = service.inventory()
        rows = [
            (
                item["connection_id"], item["provider_id"], item["auth_mode"], item["status"], len(item.get("models", []))
            )
            for item in inventory["connections"]
        ]
        terminal.emit(inventory, human=table(["CONNECTION", "PROVIDER", "AUTH", "STATUS", "MODELS"], rows))
        return 0
    if args.connections_command == "certification-catalog":
        payload = {"version": "1.0", "providers": provider_certification_catalog()}
        terminal.emit(
            payload,
            human=table(
                ["PROVIDER", "RUNTIME", "PREFERRED", "STREAM", "TOOLS"],
                [
                    (item["provider_id"], item["runtime_surface"], item["preferred_surface"], item["streaming_supported"], item["native_tool_calling_supported"])
                    for item in payload["providers"]
                ],
            ),
        )
        return 0
    if args.connections_command == "certification-benchmark":
        payload = run_provider_certification_benchmark()
        terminal.emit(
            payload,
            human=(
                f"Provider Certification benchmark: {'PASS' if payload['passed'] else 'FAIL'}\n"
                f"Probes: {sum(1 for value in payload['probes'].values() if value)}/{len(payload['probes'])}\n"
                "Live provider calls: 0"
            ),
        )
        return 0 if payload["passed"] else 1
    if args.connections_command == "certifications":
        cert_store = ProviderCertificationStore(paths.provider_certifications_db)
        try:
            records = cert_store.list(args.connection)
        finally:
            cert_store.close()
        terminal.emit(
            {"certifications": records, "count": len(records)},
            human=(
                "No provider certifications recorded."
                if not records
                else table(
                    ["ID", "CONNECTION", "PROVIDER", "MODEL", "LEVEL", "STATUS"],
                    [(item["certification_id"], item["connection_id"], item["provider_id"], item.get("model_name") or "-", item["level"], item["status"]) for item in records],
                )
            ),
        )
        return 0
    if args.connections_command == "certification-plan":
        cert_store = ProviderCertificationStore(paths.provider_certifications_db)
        try:
            certifier = ProviderCertifier(connection_store, cert_store)
            payload = certifier.plan(args.connection_id, model_name=args.model, level=args.level)
        finally:
            cert_store.close()
        terminal.emit(payload, human=f"Certification plan for {args.connection_id}: {args.level}; no network call executed.")
        return 0
    if args.connections_command == "certify":
        cert_store = ProviderCertificationStore(paths.provider_certifications_db)
        try:
            preflight_certifier = ProviderCertifier(connection_store, cert_store)
            plan = preflight_certifier.plan(args.connection_id, model_name=args.model, level=args.level)
            if not args.live:
                raise CLIError("live_certification_requires_explicit_live_flag", "Refusing provider network calls without --live.")
            if plan["billable_acknowledgement_required"] and not args.allow_billable_probes:
                raise CLIError("billable_certification_requires_explicit_acknowledgement", "Runtime/full certification may incur provider charges; pass --allow-billable-probes explicitly.")
            connection = connection_store.get_connection(args.connection_id)
            if connection.credential_reference:
                with _credential_store(paths) as credential_store:
                    broker = _credential_broker(paths, credential_store)
                    certifier = ProviderCertifier(connection_store, cert_store, credential_broker=broker)
                    record = certifier.run(
                        args.connection_id, model_name=args.model, level=args.level, live=True,
                        allow_billable=bool(args.allow_billable_probes), timeout_seconds=float(args.timeout),
                    )
            else:
                certifier = ProviderCertifier(connection_store, cert_store)
                record = certifier.run(
                    args.connection_id, model_name=args.model, level=args.level, live=True,
                    allow_billable=bool(args.allow_billable_probes), timeout_seconds=float(args.timeout),
                )
        finally:
            cert_store.close()
        terminal.emit(
            {"certification": record.as_dict()},
            human=f"Provider certification {record.status}: {record.provider_id} / {record.model_name or '-'} ({record.level})",
        )
        return 0 if record.status == "CERTIFIED" else 1
    if args.connections_command == "remove":
        removed = connection_store.delete_connection(args.connection_id)
        terminal.emit({"connection_id": args.connection_id, "removed": removed}, human=f"Removed metadata: {removed}")
        return 0 if removed else 1

    with _credential_store(paths) as credential_store:
        broker: CredentialBroker | None = None
        if args.connections_command in {"add-api-key", "discover"}:
            broker = _credential_broker(paths, credential_store)
        service = AIConnectionService(
            connection_store,
            model_store,
            credential_broker=broker,
            credential_store=credential_store,
        )
        if args.connections_command == "add-api-key":
            value = _credential_value_from_input(
                stdin=args.stdin, prompt=f"API credential for {args.connection_id}: "
            )
            record = service.add_api_key_connection(
                connection_id=args.connection_id,
                provider_id=args.provider,
                secret_value=value,
                label=args.label,
                endpoint=args.endpoint,
            )
            terminal.emit(
                {"connection": record.redacted_dict(), "raw_secret_persisted_in_connection_store": False},
                human=f"Connected {record.display_name} as {record.connection_id}. Secret is stored in the native vault.",
            )
            return 0
        if args.connections_command == "attach-account":
            record = service.attach_oauth_account(
                connection_id=args.connection_id,
                provider_id=args.provider,
                account_id=args.account,
                label=args.label,
                endpoint=args.endpoint,
            )
            terminal.emit({"connection": record.redacted_dict()}, human=f"Attached account to {record.connection_id}.")
            return 0
        if args.connections_command == "add-custom":
            record = service.add_custom_connection(
                connection_id=args.connection_id, endpoint=args.endpoint, label=args.label
            )
            terminal.emit({"connection": record.redacted_dict()}, human=f"Connected custom AI endpoint: {record.connection_id}")
            return 0
        if args.connections_command == "add-local":
            record = service.add_local_connection(
                connection_id=args.connection_id, endpoint=args.endpoint, label=args.label
            )
            terminal.emit({"connection": record.redacted_dict()}, human=f"Connected local runtime: {record.connection_id}")
            return 0
        if args.connections_command == "add-model":
            profile = service.add_model(
                connection_id=args.connection_id,
                model_id=args.model_id,
                model_name=args.model_name,
                display_name=args.display_name,
                roles=tuple(args.role),
                domains=tuple(args.domain),
                capabilities=({"general": float(args.quality_score)} if args.quality_score is not None else None),
                metadata={k: v for k, v in {
                    "capacity_source": args.capacity_source,
                    "terms_status": args.terms_status,
                    "input_cost_per_million": args.input_cost_per_million,
                    "output_cost_per_million": args.output_cost_per_million,
                }.items() if v is not None},
            )
            terminal.emit({"model": profile.redacted_dict()}, human=f"Registered model {profile.model_id}.")
            return 0
        if args.connections_command == "discover":
            models = service.discover_models(args.connection_id)
            terminal.emit({"connection_id": args.connection_id, "models": list(models), "count": len(models)})
            return 0
    raise CLIError("connections_command_unknown", "Unsupported connections command.")



def command_intelligence(args: argparse.Namespace, terminal: Terminal) -> int:
    paths = _paths(args)
    store = IntelligenceSearchStore(paths.intelligence_search_db)
    activation_store = IntelligenceActivationStore(paths.intelligence_search_db)
    if args.intelligence_command == "benchmark":
        payload = run_intelligence_search_benchmark()
        terminal.emit(
            payload,
            human=(
                f"Intelligence Search Engine benchmark: {'PASS' if payload['passed'] else 'FAIL'}\n"
                f"Probes: {sum(bool(v) for v in payload['probes'].values())}/{len(payload['probes'])}\n"
                "Search is local-first and does not require a W1-owned server."
            ),
        )
        return 0 if payload["passed"] else 1
    if args.intelligence_command == "activation-benchmark":
        payload = run_intelligence_activation_benchmark()
        terminal.emit(
            payload,
            human=(
                f"Intelligence Activation benchmark: {'PASS' if payload['passed'] else 'FAIL'}\n"
                f"Probes: {sum(bool(v) for v in payload['probes'].values())}/{len(payload['probes'])}\n"
                "Candidates remain inert until local provider evidence satisfies the activation gate."
            ),
        )
        return 0 if payload["passed"] else 1
    if args.intelligence_command == "catalog":
        terminal.emit(search_catalog())
        return 0
    if args.intelligence_command == "list":
        items = store.list(limit=args.limit, source_id=args.source)
        terminal.emit(
            {"candidates": [item.as_dict() for item in items], "count": len(items)},
            human=(
                table(
                    ["SOURCE", "TITLE", "FREE", "REVIEW", "SCORE"],
                    [(item.source_id, item.title, item.free_status, item.review_status, f"{item.score:.2f}") for item in items],
                ) if items else "No intelligence-search candidates cached."
            ),
        )
        return 0
    if args.intelligence_command == "clear":
        count = store.clear()
        terminal.emit({"cleared": count}, human=f"Cleared {count} cached intelligence-search candidate(s).")
        return 0
    if args.intelligence_command in {"assess", "review", "activate"}:
        connection_store = AIConnectionStore(paths.ai_connections_db)
        model_store = ModelAccessStore(paths.model_access_db)
        service = IntelligenceActivationService(
            search_store=store, activation_store=activation_store, connection_store=connection_store,
            model_store=model_store, capacity_store=CapacityStore(paths.capacity_router_db),
            connection_service=AIConnectionService(connection_store, model_store),
        )
        if args.intelligence_command == "assess":
            assessment = service.assess(args.candidate_id, connection_id=args.connection_id)
            terminal.emit(assessment.as_dict())
            return 0
        if args.intelligence_command == "review":
            record = service.review(args.candidate_id, decision=args.decision, note=args.note)
            terminal.emit({"record": record.as_dict(), "audit_chain_valid": activation_store.verify_audit()})
            return 0
        record = service.activate(
            args.candidate_id,
            connection_id=getattr(args, "connection_id", None),
            profile_id=getattr(args, "profile_id", None),
        )
        terminal.emit(
            {"record": record.as_dict(), "audit_chain_valid": activation_store.verify_audit()},
            human=f"Activated {record.candidate_id} as {record.profile_id}; third-party-free routing still requires explicit opt-in.",
        )
        return 0
    if args.intelligence_command == "activations":
        records = activation_store.list(limit=args.limit)
        terminal.emit({
            "records": [item.as_dict() for item in records],
            "count": len(records),
            "audit_chain_valid": activation_store.verify_audit(),
            "raw_credentials_stored": False,
        })
        return 0
    engine = IntelligenceSearchEngine(store)
    report = engine.search(
        args.query,
        sources=(tuple(args.source) if args.source else None),
        limit_per_source=args.limit_per_source,
    )
    terminal.emit(
        report.as_dict(),
        human=(
            table(
                ["SOURCE", "TITLE", "FREE", "REVIEW", "SCORE"],
                [(item.source_id, item.title, item.free_status, item.review_status, f"{item.score:.2f}") for item in report.candidates[:30]],
            ) if report.candidates else "No candidates found."
        ),
    )
    return 0


def command_capacity(args: argparse.Namespace, terminal: Terminal) -> int:
    paths = _paths(args)
    if args.capacity_command == "benchmark":
        payload = run_capacity_router_benchmark()
        terminal.emit(
            payload,
            human=(
                f"Adaptive Capacity Router benchmark: {'PASS' if payload['passed'] else 'FAIL'}\n"
                f"Probes: {sum(bool(v) for v in payload['probes'].values())}/{len(payload['probes'])}\n"
                "Collaboration topology is preserved while quota/cost routing changes role assignments."
            ),
        )
        return 0 if payload["passed"] else 1

    store = CapacityStore(paths.capacity_router_db)
    if args.capacity_command == "list":
        items = [item.as_dict() for item in store.list()]
        terminal.emit(
            {"observations": items, "count": len(items), "raw_credentials_stored": False},
            human=(
                table(
                    ["MODEL", "SOURCE", "QUOTA", "REMAINING", "TERMS"],
                    [(item["model_id"], item["source"], item["quota_state"], item["remaining_tokens"], item["terms_status"]) for item in items],
                ) if items else "No capacity observations recorded."
            ),
        )
        return 0

    if args.capacity_command == "observe":
        observation = CapacityObservation(
            model_id=args.model_id,
            source=args.source,
            quota_state=args.quota_state,
            remaining_tokens=args.remaining_tokens,
            reset_at=args.reset_at,
            input_cost_per_million=float(args.input_cost_per_million),
            output_cost_per_million=float(args.output_cost_per_million),
            terms_status=args.terms_status,
            entitlement_verified=bool(args.entitlement_verified),
        )
        store.put(observation)
        terminal.emit(
            {"observation": observation.as_dict(), "raw_credentials_stored": False},
            human=f"Recorded capacity observation for {observation.model_id}: {observation.source} / {observation.quota_state}",
        )
        return 0

    task_payload = load_json(args.task)
    try:
        task = CompiledTask(
            task_id=str(task_payload["task_id"]),
            title=str(task_payload.get("title") or task_payload["task_id"]),
            phase=str(task_payload.get("phase") or "execution"),
            role=str(task_payload.get("role") or "producer"),
            expected_output_type=str(task_payload.get("expected_output_type") or "text"),
            depends_on=tuple(str(x) for x in task_payload.get("depends_on", ())),
            deliverable_id=(str(task_payload["deliverable_id"]) if task_payload.get("deliverable_id") is not None else None),
            criterion_id=(str(task_payload["criterion_id"]) if task_payload.get("criterion_id") is not None else None),
            required_context_fields=tuple(str(x) for x in task_payload.get("required_context_fields", ())),
            estimated_units=int(task_payload.get("estimated_units", 1)),
            domains=tuple(str(x) for x in task_payload.get("domains", ())),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise CLIError("capacity_task_invalid", f"Invalid CompiledTask JSON: {exc}") from exc
    policy = RoutingPolicy(
        mode=args.routing_mode,
        quality_floor=float(args.quality_floor),
        max_task_cost_usd=args.max_task_cost,
        estimated_input_tokens=int(args.estimated_input_tokens),
        estimated_output_tokens=int(args.estimated_output_tokens),
        producer_count=int(args.producer_count),
        fallback_per_role=int(args.fallback_per_role),
        allow_paid=not bool(args.no_paid),
        allow_local=not bool(args.no_local),
        allow_third_party_free=bool(args.allow_third_party_free),
        require_known_terms=not bool(args.allow_unknown_terms),
    )
    model_store = model_access_store(paths)
    router = AdaptiveCapacityRouter.from_store(store)
    plan = router.plan(
        plan_id=args.plan_id,
        display_name=args.display_name,
        team_mode=args.team_mode,
        task=task,
        profiles=model_store.list_profiles(enabled_only=True),
        policy=policy,
    )
    if args.save_portfolio:
        model_store.put_portfolio(plan.portfolio)
    terminal.emit(
        {"capacity_plan": plan.as_dict(), "portfolio_saved": bool(args.save_portfolio)},
        human=(
            f"Adaptive team plan: {plan.portfolio.strategy}\n"
            f"Roles: {', '.join(member.role + '=' + member.model_id for member in plan.portfolio.members)}\n"
            f"Estimated direct cost: ${plan.estimated_cost_usd:.6f}\n"
            "Consumer app subscriptions were not assumed to grant API access."
        ),
    )
    return 0


def command_discovery(args: argparse.Namespace, terminal: Terminal) -> int:
    paths = _paths(args)
    store = GatewayStore(paths.gateway_db)
    engine = AutonomousDiscoveryEngine()

    if args.discovery_command == "scan":
        inc_local = args.local or (not args.catalogs and not args.accounts)
        inc_catalogs = args.catalogs or (not args.local and not args.accounts)
        inc_accounts = args.accounts or (not args.local and not args.catalogs)

        discovered = engine.scan(
            include_local=inc_local,
            include_catalogs=inc_catalogs,
            include_accounts=inc_accounts,
        )

        identities, routes = engine.resolve_and_project(discovered)
        for i_dict in identities:
            tier = i_dict.get("quality_tier", "unknown")
            if tier not in QUALITY_TIERS:
                tier = "unknown"
            arch = i_dict.get("architecture", "unknown")
            if arch not in MODEL_ARCHITECTURES:
                arch = "unknown"
            identity_obj = ModelIdentity(
                canonical_id=i_dict["canonical_id"],
                family=i_dict["family"],
                vendor=i_dict["vendor"],
                architecture=arch,
                quality_tier=tier,
                context_window=i_dict.get("context_window", 131072),
                capabilities=frozenset(i_dict.get("capabilities", {}).keys()),
                metadata={"aliases": i_dict.get("aliases", [])},
            )
            store.put_identity(identity_obj)

        for r_dict in routes:
            stype = r_dict.get("source_type", "official_free")
            if stype not in ROUTE_SOURCE_TYPES:
                stype = "official_free"
            estate = r_dict.get("entitlement_state", "unverified")
            if estate not in ENTITLEMENT_STATES:
                estate = "unverified"
            route_obj = ModelRoute(
                route_id=r_dict["route_id"],
                model_identity_id=r_dict["model_identity_id"],
                provider_id=r_dict["provider_id"],
                account_id=r_dict.get("account_id", "default"),
                model_id_at_provider=r_dict.get("model_id_at_provider", ""),
                source_type=stype,
                entitlement_state=estate,
                cost=RouteCost(r_dict.get("cost", {}).get("input_per_million", 0.0), r_dict.get("cost", {}).get("output_per_million", 0.0)),
                quota=RouteQuota(state=r_dict.get("quota", {}).get("state", "available")),
                auth_mode=r_dict.get("auth_mode", "api_key"),
                api_key_env=r_dict.get("api_key_env"),
                endpoint=r_dict.get("endpoint", ""),
                health=r_dict.get("health", "healthy"),
            )
            store.put_route(route_obj)


        payload = {
            "discovered_count": len(discovered),
            "identities_resolved": len(identities),
            "routes_projected": len(routes),
            "sources_scanned": [a.adapter_id for a in engine.adapters],
            "models": [m.as_dict() for m in discovered],
        }
        human = (
            terminal.heading("W1 Autonomous Intelligence Discovery (AID-CF):\n")
            + f"  Discovered models: {len(discovered)}\n"
            + f"  Canonical identities resolved: {len(identities)}\n"
            + f"  Model routes projected: {len(routes)}\n"
            + "All models normalized into GatewayStore projection safely under strict loopback and trust policies."
        )
        terminal.emit(payload, human=human)
        return 0

    if args.discovery_command == "list":
        discovered = engine.scan()
        if args.source:
            discovered = [m for m in discovered if m.provenance and m.provenance.source_id == args.source]
        if args.trust_class:
            discovered = [m for m in discovered if m.provenance and m.provenance.trust_class.value == args.trust_class]
        payload = {
            "discovered": [m.as_dict() for m in discovered],
            "count": len(discovered),
        }
        terminal.emit(
            payload,
            human=table(
                ["PROVIDER", "RAW_MODEL_ID", "TRUST_CLASS", "SOURCE_TYPE", "FREE"],
                [
                    (
                        m.provider_id,
                        m.raw_model_id,
                        m.provenance.trust_class.value if m.provenance else "UNKNOWN",
                        m.source_type,
                        "YES" if m.is_free else "NO",
                    )
                    for m in discovered
                ],
            ),
        )
        return 0

    raise CLIError("discovery_command_unknown", "Unsupported discovery command.")


def command_gateway(args: argparse.Namespace, terminal: Terminal) -> int:
    paths = _paths(args)
    store = GatewayStore(paths.gateway_db)

    if args.gateway_command == "doctor":
        doc = store.gateway_doctor()
        human = (
            terminal.heading("W1 Gateway & Universal Access Fabric Doctor:\n")
            + f"  Providers:       {doc['providers_count']}\n"
            + f"  Identities:      {doc['identities_count']}\n"
            + f"  Routes:          {doc['routes_count']}\n"
            + f"  Certified:       {doc['certified_count']}\n"
            + f"  Expired:         {doc['expired_count']}\n"
            + f"  Revoked:         {doc['revoked_count']}\n"
            + f"  Uncertified:     {doc['uncertified_count']}\n"
            + f"  Free/zero-cost:  {doc['free_routes_count']}\n"
            + f"  Local runtimes:  {doc['local_routes_count']}\n"
            + f"  Subscription:    {doc['subscription_routes_count']}\n"
            + f"  System Health:   {terminal.success('OPTIMAL') if doc['health_status'] == 'OPTIMAL' else terminal.warning(doc['health_status'])}"
        )
        terminal.emit(doc, human=human)
        return 0

    if args.gateway_command == "benchmark":
        payload = run_gateway_benchmark()
        human = (
            terminal.heading("W1 Intelligent Gateway Offline Contract Benchmark: ")
            + (terminal.success("PASS") if payload["passed"] else terminal.error("FAIL"))
            + f"\nProbes passed: {payload['probes_passed']}/{payload['probe_count']}\n"
            + f"Total providers: {payload['metrics']['providers']} | Total routes: {payload['metrics']['routes']} (Free: {payload['metrics']['free_routes']})\n"
            + "Zero network calls made. Zero API keys or cookies required."
        )
        terminal.emit(payload, human=human)
        return 0 if payload["passed"] else 1

    if args.gateway_command == "stats":
        stats = gateway_statistics()
        terminal.emit(stats)
        return 0

    if args.gateway_command == "list-providers":
        providers = [p.as_dict() for p in W1_PROVIDER_REGISTRY]
        terminal.emit(
            {"providers": providers, "count": len(providers)},
            human=table(
                ["PROVIDER_ID", "DISPLAY_NAME", "AUTH_MODE", "FREE_TIER", "PRIVACY"],
                [(p["provider_id"], p["display_name"], p["auth_mode"], "YES" if p["has_free_tier"] else "NO", p["privacy_mode"]) for p in providers],
            ),
        )
        return 0

    if args.gateway_command in {"identities", "list-identities"}:
        identities = store.list_identities()
        if not identities:
            populate_gateway_store(store)
            identities = store.list_identities()
        if getattr(args, "vendor", None):
            identities = [i for i in identities if i.get("vendor") == args.vendor]
        if getattr(args, "family", None):
            identities = [i for i in identities if i.get("family") == args.family]
        if getattr(args, "architecture", None):
            identities = [i for i in identities if i.get("architecture") == args.architecture]
        terminal.emit(
            {"identities": identities, "count": len(identities)},
            human=table(
                ["CANONICAL_ID", "VENDOR", "FAMILY", "TIER", "ARCH", "CONTEXT"],
                [(i["canonical_id"], i["vendor"], i["family"], i["quality_tier"], i.get("architecture", "dense"), f"{i['context_window']:,}") for i in identities],
            ),
        )
        return 0

    if args.gateway_command in {"routes", "list-routes"}:
        routes = store.list_routes(
            provider_id=getattr(args, "provider", None),
            identity_id=getattr(args, "identity", None),
            account_id=getattr(args, "account", None),
            free_only=getattr(args, "free_only", False),
        )
        if not routes and not getattr(args, "provider", None) and not getattr(args, "identity", None):
            populate_gateway_store(store)
            routes = store.list_routes(free_only=getattr(args, "free_only", False))
        if getattr(args, "local_only", False):
            routes = [r for r in routes if r.get("source_type") == "local"]
        if getattr(args, "certified_only", False):
            certs = {c.route_id: c for c in store.list_certifications()}
            routes = [r for r in routes if r["route_id"] in certs and certs[r["route_id"]].is_active()]
        terminal.emit(
            {"routes": routes, "count": len(routes)},
            human=table(
                ["ROUTE_ID", "PROVIDER", "ACCOUNT", "SOURCE", "HEALTH", "FREE"],
                [(r["route_id"], r["provider_id"], r.get("account_id", "default"), r["source_type"], r.get("health", "unknown"), "YES" if r.get("is_free") else "NO") for r in routes],
            ),
        )
        return 0

    if args.gateway_command == "certify":
        if store.route_count() == 0:
            populate_gateway_store(store)
        rec = store.certify_route(args.route_id, ttl_days=args.ttl_days)
        terminal.emit(rec.as_dict(), human=f"Certified route '{args.route_id}' (Status: {rec.status.value}, Expires: {rec.expires_at}).")
        return 0

    if args.gateway_command == "revoke":
        rec = store.revoke_route(args.route_id, reason=args.reason)
        terminal.emit(rec.as_dict(), human=f"Revoked route '{args.route_id}' (Reason: {args.reason}).")
        return 0

    if args.gateway_command == "select-route":
        selector = W1RouteSelector.from_store(store) if store.route_count() > 0 else W1RouteSelector.from_registry()
        res = selector.select_best(
            task_type=args.task,
            min_context=args.min_context,
            min_quality=args.min_quality,
            free_only=args.free_only,
            require_certified=getattr(args, "certified_only", False),
            vendor=args.vendor,
            architecture=args.architecture,
        )
        terminal.emit(res.as_dict())
        return 0 if res.selected else 1

    if args.gateway_command in {"explain", "explain-route"}:
        selector = W1RouteSelector.from_store(store) if store.route_count() > 0 else W1RouteSelector.from_registry()
        explanation = selector.explain_route(
            task_type=args.task,
            min_context=args.min_context,
            min_quality=args.min_quality,
            free_only=args.free_only,
            require_certified=getattr(args, "certified_only", False),
        )
        terminal.emit(explanation.as_dict(), human=explanation.render_human())
        return 0

    if args.gateway_command == "build-team":
        builder = TeamBuilder.from_store(store) if store.route_count() > 0 else TeamBuilder.from_registry()
        policy = TeamPolicy(
            budget=args.budget,
            team_mode=args.team_mode,
            diversity_level=getattr(args, "diversity", "balanced"),
            min_context=args.min_context,
            require_verifier=not args.no_verifier,
            require_certified=getattr(args, "certified_only", False),
        )
        team = builder.build_team(args.task, policy=policy)
        portfolio = team.to_portfolio()
        payload = {
            "team": team.as_dict(),
            "portfolio": portfolio.as_dict() if hasattr(portfolio, "as_dict") else asdict(portfolio),
        }
        human = (
            terminal.heading(f"Governed AI Team ({team.team_mode}):\n")
            + f"  Producer: {team.producer.route.route_id} ({team.producer.identity.vendor}/{team.producer.identity.quality_tier})\n"
            + f"  Reviewer: {team.reviewer.route.route_id} ({team.reviewer.identity.vendor}/{team.reviewer.identity.quality_tier})\n"
        )
        if team.verifier:
            human += f"  Verifier: {team.verifier.route.route_id} ({team.verifier.identity.vendor}/{team.verifier.identity.quality_tier})\n"
        human += f"  Diversity Score: {team.diversity.score:.2f} (Provider: {team.diversity.provider_diversity}, Vendor: {team.diversity.vendor_diversity}, Family: {team.diversity.family_diversity})"
        terminal.emit(payload, human=human)
        return 0

    if args.gateway_command == "inspect":
        if store.route_count() == 0:
            populate_gateway_store(store)
        route_dict = store.get_route(args.route_id)
        if not route_dict:
            raise CLIError("gateway_route_not_found", f"Route {args.route_id} was not found in Gateway store.")
        identity_dict = store.get_identity(route_dict["model_identity_id"])
        cert = store.get_certification(args.route_id)
        payload = {
            "route": route_dict,
            "identity": identity_dict,
            "certification": cert.as_dict() if cert else None,
        }
        terminal.emit(payload)
        return 0

    if args.gateway_command == "verify-route":
        if store.route_count() == 0:
            populate_gateway_store(store)
        route_dict = store.get_route(args.route_id)
        if not route_dict:
            raise CLIError("gateway_route_not_found", f"Route {args.route_id} was not found.")
        route_obj = ModelRoute(
            route_id=route_dict["route_id"],
            model_identity_id=route_dict["model_identity_id"],
            provider_id=route_dict["provider_id"],
            account_id=route_dict.get("account_id", "default"),
            model_id_at_provider=route_dict.get("model_id_at_provider", ""),
            source_type=route_dict["source_type"],
            entitlement_state=route_dict.get("entitlement_state", "unverified"),
            cost=RouteCost(route_dict.get("cost", {}).get("input_per_million", 0.0), route_dict.get("cost", {}).get("output_per_million", 0.0)),
            quota=RouteQuota(state=route_dict.get("quota", {}).get("state", "unknown")),
            auth_mode=route_dict.get("auth_mode", "api_key"),
            api_key_env=route_dict.get("api_key_env"),
            endpoint=route_dict.get("endpoint", ""),
            health=route_dict.get("health", "unknown"),
        )
        res = verify_route_live(route_obj)
        terminal.emit(res, human=f"Route {args.route_id} verified. Status: {res['status']}.")
        return 0

    if args.gateway_command == "discover":
        if not args.provider:
            res = populate_gateway_store(store)
            terminal.emit(res, human=f"Populated Gateway store with {res['identities_registered']} identities and {res['routes_registered']} routes.")
            return 0
        prov = next((p for p in W1_PROVIDER_REGISTRY if p.provider_id == args.provider), None)
        if not prov:
            raise CLIError("gateway_provider_unknown", f"Provider {args.provider} is not recognized.")
        models_for_prov = [m.as_dict() for m in KNOWN_MODEL_IDENTITIES if m.provider_id == args.provider]
        res = ingest_discovered_models(
            store,
            provider_id=args.provider,
            account_id=args.account,
            models=models_for_prov,
            base_endpoint=prov.base_url,
            auth_mode=prov.auth_mode,
        )
        terminal.emit(res, human=f"Ingested {res['routes_ingested']} route(s) for provider {args.provider} (Account: {args.account}).")
        return 0

    raise CLIError("gateway_command_unknown", "Unsupported gateway command.")


def command_teams(args: argparse.Namespace, terminal: Terminal) -> int:
    paths = _paths(args)
    store = AIConnectionStore(paths.ai_connections_db)
    model_store = model_access_store(paths)
    service = AIConnectionService(store, model_store)
    if args.teams_command == "benchmark":
        payload = run_ai_connections_benchmark()
        terminal.emit(payload, human=f"AI Team Studio benchmark: {'PASS' if payload['passed'] else 'FAIL'}")
        return 0 if payload["passed"] else 1
    if args.teams_command == "list":
        teams = [item.as_dict() for item in store.list_teams()]
        terminal.emit(
            {"teams": teams, "count": len(teams)},
            human=(
                "No AI teams registered."
                if not teams
                else "\n".join(f"{item['team_id']}  {item['mode']}  members={len(item['members'])}" for item in teams)
            ),
        )
        return 0
    if args.teams_command == "show":
        team = store.get_team(args.team_id)
        terminal.emit({"team": team.as_dict(), "portfolio": asdict(model_store.get_portfolio(team.team_id))})
        return 0
    if args.teams_command == "add":
        team = team_from_mapping(load_json(args.file))
        portfolio = service.save_team(team)
        terminal.emit(
            {"team": team.as_dict(), "compiled_portfolio": asdict(portfolio)},
            human=f"Saved AI team {team.team_id} -> {portfolio.strategy}.",
        )
        return 0
    removed = service.remove_team(args.team_id)
    terminal.emit({"team_id": args.team_id, "removed": removed}, human=f"Removed: {removed}")
    return 0 if removed else 1


def _models_matrix_rows():
    """Build credential x model matrix rows without exposing any secret."""
    from .benchmark_core.profiles import KNOWN_MODEL_PROFILES
    rows = []
    for credential_ref in list_credential_refs():
        for model_id in KNOWN_MODEL_PROFILES:
            profile = get_model_profile(model_id)
            rows.append(
                {
                    "provider_id": profile.provider_id,
                    "credential_ref": credential_ref,
                    "model_id": model_id,
                    "display_name": profile.display_name,
                    "streaming": profile.streaming_preferred,
                    "tool_calling": profile.capabilities.tool_calling,
                    "structured_output": profile.capabilities.structured_output,
                }
            )
    return rows


def command_models(args: argparse.Namespace, terminal: Terminal) -> int:
    paths = _paths(args)
    store = model_access_store(paths)

    if args.models_command == "discover":
        matrix = CredentialModelAvailabilityTester()
        rows = []
        for credential_ref in list_credential_refs():
            ids = matrix.discover_for_credential(credential_ref)
            for model_id in ids:
                profile = get_model_profile(model_id) if model_id in getattr(matrix, "model_ids", []) else None
                rows.append(
                    {
                        "provider_id": get_provider("nvidia").provider_id,
                        "credential_ref": credential_ref,
                        "model_id": model_id,
                        "display_name": profile.display_name if profile else model_id,
                        "catalog_visible": True,
                    }
                )
        terminal.emit(
            {"discovered_models": rows},
            human=(
                "Credential x Model Discovery (catalog visibility):\n" +
                "\n".join(
                    f"  {r['credential_ref']:<16} {r['model_id']}"
                    for r in rows
                ) or "No credentialed models discovered."
            ),
        )
        return 0

    if args.models_command == "inspect":
        profile = get_model_profile(args.model)
        payload = profile.redacted_dict()
        payload["credential_ref"] = args.credential_ref
        terminal.emit(
            payload,
            human=(
                f"Provider: {profile.provider_id}\n"
                f"Model ID: {profile.model_id}\n"
                f"Display: {profile.display_name}\n"
                f"Credential Ref: {args.credential_ref or '(legacy/auto)'}\n"
                f"Streaming: {profile.streaming_preferred} | Tool Calling: {profile.capabilities.tool_calling} | Structured: {profile.capabilities.structured_output}"
            ),
        )
        return 0

    if args.models_command in {"test", "matrix"}:
        matrix = CredentialModelAvailabilityTester()
        records = matrix.matrix()
        if args.models_command == "test" and args.model:
            target = get_model_profile(args.model).model_id
            if args.credential_ref:
                records = [r for r in records if r.model_id == target and r.credential_ref == args.credential_ref]
            else:
                records = [r for r in records if r.model_id == target]
        terminal.emit(
            {"availability_matrix": [asdict(r) for r in records]},
            human=(
                "Credential x Model Availability:\n" +
                "\n".join(
                    f"  {r.credential_ref:<16} {r.model_id:<34} catalog={str(r.catalog_visible):<5} "
                    f"inference={str(r.inference_available):<5} operational={str(r.operational)}"
                    for r in records
                ) or "No credentialed models."
            ),
        )
        return 0

    if args.models_command in {"list", "sync"}:
        payload = model_access_inventory(paths)
        rows = [
            (
                item["model_id"], item["provider_id"], item["access_mode"],
                item["privacy_mode"], "yes" if item["enabled"] else "no",
            )
            for item in payload["models"]
        ]
        human = table(["MODEL", "PROVIDER", "ACCESS", "PRIVACY", "ENABLED"], rows)
        if payload["portfolios"]:
            human += "\n\nPortfolios:\n" + "\n".join(
                f"- {item['portfolio_id']}: {item['strategy']} ({len(item['members'])} models)"
                for item in payload["portfolios"]
            )
        terminal.emit(payload, human=human)
        return 0
    if args.models_command == "add":
        profile = profile_from_mapping(load_json(args.file))
        store.put_profile(profile)
        terminal.emit({"model": profile.redacted_dict()}, human=f"Added model profile: {profile.model_id}")
        return 0
    if args.models_command == "remove":
        removed = store.delete_profile(args.model_id)
        terminal.emit({"removed": removed, "model_id": args.model_id}, human=f"Removed: {removed}")
        return 0 if removed else 1
    if args.models_command == "portfolios":
        if args.portfolio_command == "list":
            portfolios = [asdict(item) for item in store.list_portfolios()]
            terminal.emit(
                {"portfolios": portfolios},
                human="\n".join(
                    f"{item['portfolio_id']}  {item['strategy']}  members={len(item['members'])}"
                    for item in portfolios
                ) or "No portfolios registered.",
            )
            return 0
        if args.portfolio_command == "add":
            portfolio = portfolio_from_mapping(load_json(args.file))
            store.put_portfolio(portfolio)
            terminal.emit({"portfolio": asdict(portfolio)}, human=f"Added portfolio: {portfolio.portfolio_id}")
            return 0
        removed = store.delete_portfolio(args.portfolio_id)
        terminal.emit({"removed": removed, "portfolio_id": args.portfolio_id}, human=f"Removed: {removed}")
        return 0 if removed else 1
    if args.models_command == "invoke":
        request = provider_request_from_mapping(load_json(args.request))
        fabric = model_access_fabric(paths, store=store)
        if args.model:
            result = fabric.invoke_model(store.get_profile(args.model), request).as_dict()
        else:
            result = fabric.invoke_portfolio(args.portfolio, request).as_dict()
        terminal.emit(result)
        return 0 if result.get("successful", result.get("status") == "completed") else 1
    payload = run_model_access_benchmark()
    terminal.emit(
        payload,
        human=(
            f"Multi-model benchmark: {'PASS' if payload['passed'] else 'FAIL'}\n"
            f"Models: {payload['metrics']['registered_models']}\n"
            f"Observed parallel speedup: {payload['metrics']['observed_parallel_speedup']}x\n"
            f"Strategies tested: {payload['metrics']['portfolio_strategies_tested']}"
        ),
    )
    return 0 if payload["passed"] else 1


def _ensure_model_access_token(paths: WorkspacePaths) -> str:
    if paths.model_access_token.is_file():
        token = paths.model_access_token.read_text(encoding="utf-8").strip()
        if token:
            return token
    token = secrets.token_urlsafe(32)
    paths.model_access_token.parent.mkdir(parents=True, exist_ok=True)
    paths.model_access_token.write_text(token + "\n", encoding="utf-8")
    try:
        os.chmod(paths.model_access_token, 0o600)
    except OSError:
        pass
    return token


def command_access(args: argparse.Namespace, terminal: Terminal) -> int:
    paths = _paths(args)
    store = model_access_store(paths)
    token = _ensure_model_access_token(paths)
    runtime = EmbeddedW1Runtime(model_access_fabric(paths, store=store))
    settings = LocalControlSettings(host=args.host, port=args.port, token=token)
    server = LocalControlServer(settings, runtime)
    payload = {
        "service": "w1-local-control",
        "url": f"http://{args.host}:{server.server_address[1]}",
        "token_file": str(paths.model_access_token),
        "w1_owned_server_required": False,
        "models": len(store.list_profiles(enabled_only=True)),
        "portfolios": len(store.list_portfolios()),
    }
    terminal.emit(
        payload,
        human=(
            f"W1 Local Control API: {payload['url']}\n"
            f"Token file: {paths.model_access_token}\n"
            "The service is loopback-only and uses the user's own model access."
        ),
    )
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return 0


def command_evaluate(args: argparse.Namespace, terminal: Terminal) -> int:
    paths = _paths(args)
    payload = evaluate_w1_nexus(paths.root)
    if args.output:
        write_json(args.output, payload)
    terminal.emit(
        payload,
        human=(
            terminal.heading("Audited W1 Nexus evaluation") + "\n"
            f"Verified backend capability: {payload['verified_backend_capability_percent']}%\n"
            f"Complete W1 Nexus product: {payload['full_product_completeness_percent']}%\n"
            f"Multi-model benchmark: {'PASS' if payload['model_access_benchmark']['passed'] else 'FAIL'}\n"
            f"Artifact Studio benchmark: {'PASS' if payload['artifact_studio_benchmark']['passed'] else 'FAIL'}\n"
            f"Universal Artifact benchmark: {'PASS' if payload['universal_artifact_benchmark']['passed'] else 'FAIL'}\n"
            f"Governed Computer Use benchmark: {'PASS' if payload['computer_use_benchmark']['passed'] else 'FAIL'}\n"
            f"Credential Broker benchmark: {'PASS' if payload['credential_broker_benchmark']['passed'] else 'FAIL'}\n"
            f"Native Packaging benchmark: {'PASS' if payload['native_packaging_benchmark']['passed'] else 'FAIL'}\n"
            f"Release Hardening benchmark: {'PASS' if payload['release_hardening_benchmark']['passed'] else 'FAIL'}\n"
            "This score is generated from executable probes and the published weighted rubric."
        ),
    )
    return 0 if (
        payload["model_access_benchmark"]["passed"]
        and payload["artifact_studio_benchmark"]["passed"]
        and payload["universal_artifact_benchmark"]["passed"]
        and payload["computer_use_benchmark"]["passed"]
        and payload["credential_broker_benchmark"]["passed"]
        and payload["native_packaging_benchmark"]["passed"]
        and payload["release_hardening_benchmark"]["passed"]
    ) else 1

def command_plan(args: argparse.Namespace, terminal: Terminal) -> int:
    goal = load_json(args.goal)
    team = load_json(args.team)
    resources = load_json(args.resources)
    context_map = load_json(args.context_map) if args.context_map else {}
    payload = compile_plan(
        goal,
        team,
        resources,
        context_fields_by_deliverable=context_map,
        domains=args.domain,
    )
    if args.output:
        write_json(args.output, payload)
    rows = []
    for task in payload["tasks"]:
        routing = task["routing"]
        rows.append(
            (
                task["task_id"],
                task["phase"],
                task["role"],
                routing.get("selected_resource_id") or "-",
                routing.get("action"),
                "yes" if routing.get("requires_extra_review") else "no",
            )
        )
    human = (
        terminal.heading(
            f"Compiled {payload['task_count']} tasks; estimated units={payload['estimated_units']}"
        )
        + "\n\n"
        + table(["TASK", "PHASE", "ROLE", "RESOURCE", "ACTION", "EXTRA REVIEW"], rows)
    )
    terminal.emit(payload, human=human)
    return 0


def _runtime_identity(paths: WorkspacePaths) -> dict[str, Any]:
    config = load_json(paths.config)
    identity = config.get("runtime_identity")
    if not isinstance(identity, dict):
        raise CLIError("runtime_identity_missing", "workspace.json must define runtime_identity.")
    actor = identity.get("actor")
    recorded_by = identity.get("recorded_by")
    authorized_by = identity.get("authorized_by")
    if not all(isinstance(item, dict) for item in (actor, recorded_by, authorized_by)):
        raise CLIError("runtime_identity_invalid", "runtime_identity fields must be objects.")
    entity_id = authorized_by.get("entity_id")
    if not isinstance(entity_id, str) or entity_id.startswith("replace-with-"):
        raise CLIError(
            "runtime_authority_not_configured",
            "Set runtime_identity.authorized_by to an active orchestrator RoleAssignment.",
        )
    return {
        "actor": dict(actor),
        "recorded_by": dict(recorded_by),
        "authorized_by": dict(authorized_by),
    }


def _execute_session_run(
    *,
    paths: WorkspacePaths,
    run_id: str,
    session_id: str,
    goal_id: str,
    team_plan_id: str,
    resource_plan_id: str,
    context_path: str | None,
    domains: Sequence[str],
    max_attempts: int,
    event_mode: str,
    terminal: Terminal,
) -> dict[str, Any]:
    context_file = Path(context_path) if context_path else paths.context
    context = load_json(context_file) if context_file.is_file() else {}
    providers = build_providers(paths)
    listener = terminal.event_listener(event_mode)
    identity = _runtime_identity(paths)
    memory_config = load_json(paths.config).get("memory", {})
    if not isinstance(memory_config, dict):
        memory_config = {}
    principal_config = memory_config.get("principal", {})
    if not isinstance(principal_config, dict):
        principal_config = {}
    memory_principal = MemoryPrincipal(
        str(principal_config.get("principal_type", "human")),
        str(principal_config.get("principal_id", "local-owner")),
        tuple(str(item) for item in principal_config.get("groups", [])),
    )
    with (
        _open_store(paths) as store,
        OrchestratorJournal(paths.journal_db) as journal,
        _open_action_runtime(paths) as action_runtime,
        MemoryStore(paths.memory_db) as memory_store,
    ):
        memory_provider = KnowledgeContextProvider(
            memory_store,
            namespace_id=str(memory_config.get("namespace_id", "w1-local")),
            project_id=str(memory_config.get("project_id", "default-project")),
            principal=memory_principal,
        )
        factory = ArtifactEnvelopeFactory(
            actor=identity["actor"],
            authorized_by=identity["authorized_by"],
            recorded_by=identity["recorded_by"],
            correlation_id=f"run-{run_id}",
        )
        core = OrchestratorCore(
            session_store=store,
            journal=journal,
            providers=providers,
            envelope_factory=factory,
            event_listener=listener,
            action_runtime=action_runtime,
            memory_context_provider=memory_provider,
        )
        result = core.run_from_session(
            run_id=run_id,
            session_id=session_id,
            goal_id=goal_id,
            team_plan_id=team_plan_id,
            resource_plan_id=resource_plan_id,
            context=context,
            domains=frozenset(domains),
            max_attempts_per_resource=max_attempts,
        )
        payload = _result_payload(result)
        payload["session_integrity"] = store.verify_integrity(session_id).valid
        payload["memory_integrity"] = memory_store.verify_integrity()["valid"]
    _save_run_invocation(
        paths,
        {
            "run_id": run_id,
            "session_id": session_id,
            "goal_id": goal_id,
            "team_plan_id": team_plan_id,
            "resource_plan_id": resource_plan_id,
            "context_path": str(context_file),
            "domains": list(domains),
            "max_attempts": max_attempts,
        },
    )
    return payload


def command_run(args: argparse.Namespace, terminal: Terminal) -> int:
    paths = _paths(args)
    payload = _execute_session_run(
        paths=paths,
        run_id=args.run_id,
        session_id=args.session_id,
        goal_id=args.goal_id,
        team_plan_id=args.team_plan_id,
        resource_plan_id=args.resource_plan_id,
        context_path=args.context,
        domains=args.domain,
        max_attempts=args.max_attempts,
        event_mode=args.events,
        terminal=terminal,
    )
    terminal.emit(payload, human=_run_human(payload, terminal))
    return 0 if payload["status"] == "completed" else 3


def command_resume(args: argparse.Namespace, terminal: Terminal) -> int:
    paths = _paths(args)
    invocation = _load_run_invocation(paths, args.run_id)
    payload = _execute_session_run(
        paths=paths,
        run_id=args.run_id,
        session_id=invocation["session_id"],
        goal_id=invocation["goal_id"],
        team_plan_id=invocation["team_plan_id"],
        resource_plan_id=invocation["resource_plan_id"],
        context_path=invocation.get("context_path"),
        domains=invocation.get("domains", []),
        max_attempts=int(invocation.get("max_attempts", 2)),
        event_mode=args.events,
        terminal=terminal,
    )
    terminal.emit(payload, human=_run_human(payload, terminal))
    return 0 if payload["status"] == "completed" else 3


def command_status(args: argparse.Namespace, terminal: Terminal) -> int:
    paths = _paths(args)
    with OrchestratorJournal(paths.journal_db) as journal:
        payload = run_snapshot(journal, args.run_id)
    tasks = [
        (
            item["task_id"],
            item["status"],
            item["resource_id"] or "-",
            item["attempts"],
            item["error_code"] or "-",
        )
        for item in payload["tasks"]
    ]
    human = terminal.heading(f"Run {args.run_id}: {payload['status']}") + "\n\n" + table(
        ["TASK", "STATUS", "RESOURCE", "ATTEMPTS", "ERROR"], tasks
    )
    terminal.emit(payload, human=human)
    return 0


def command_sessions(args: argparse.Namespace, terminal: Terminal) -> int:
    paths = _paths(args)
    with _open_store(paths) as store:
        if args.sessions_command == "append":
            document = load_json(args.file, required_object=False)
            envelopes = document if isinstance(document, list) else [document]
            if not envelopes or any(not isinstance(item, dict) for item in envelopes):
                raise CLIError("envelope_document_invalid", "Expected one envelope object or a non-empty array.")
            results = []
            expected = args.expected_last_sequence
            for envelope in envelopes:
                result = store.append(dict(envelope), expected_last_sequence=expected)
                results.append({
                    "session_id": result.session_id,
                    "sequence": result.sequence,
                    "message_id": result.message_id,
                    "event_hash": result.event_hash,
                    "already_present": result.already_present,
                })
                expected = result.sequence
            terminal.emit({"appended": results})
            return 0
        if args.sessions_command == "rebuild":
            state = store.rebuild_projections(args.session_id)
            payload = {
                "session_id": args.session_id,
                "last_sequence": state.last_sequence,
                "latest_entity_count": len(state.latest_versions),
                "active_protocol_event_count": len(state.active_protocol_events),
            }
            terminal.emit(payload, human=f"{terminal.success('Projections rebuilt')}\n{canonical_pretty(payload)}")
            return 0
        if args.sessions_command == "list":
            sessions = [asdict(item) for item in store.list_sessions()]
            rows = [
                (item["session_id"], item["event_count"], item["last_recorded_at"] or "-")
                for item in sessions
            ]
            terminal.emit({"sessions": sessions}, human=table(["SESSION", "EVENTS", "LAST RECORDED"], rows))
            return 0
        if args.sessions_command == "show":
            try:
                summary = asdict(store.get_summary(args.session_id))
            except SessionNotFoundError as exc:
                raise CLIError("session_not_found", f"Session not found: {args.session_id}") from exc
            terminal.emit(summary)
            return 0
        if args.sessions_command == "verify":
            report = store.verify_integrity(args.session_id)
            payload = {
                "session_id": args.session_id,
                "valid": report.valid,
                "errors": list(report.errors),
                "last_sequence": report.last_sequence,
                "last_event_hash": report.last_event_hash,
            }
            human = terminal.success("VALID") if report.valid else terminal.error("INVALID")
            terminal.emit(payload, human=f"{human}  {args.session_id}\n{canonical_pretty(payload)}")
            return 0 if report.valid else 1
        state = store.replay(args.session_id)
        payload = {
            "session_id": args.session_id,
            "last_sequence": state.last_sequence,
            "last_recorded_at": state.last_recorded_at,
            "record_count": len(state.records),
            "latest_entity_count": len(state.latest_versions),
            "active_protocol_event_count": len(state.active_protocol_events),
            "compensated_protocol_event_count": len(state.compensated_protocol_events),
            "consumed_approval_count": len(state.consumed_approvals),
        }
        terminal.emit(payload)
        return 0


def command_audit(args: argparse.Namespace, terminal: Terminal) -> int:
    paths = _paths(args)
    with _open_store(paths) as store:
        payload = session_audit(store, args.session_id)
    if not args.include_events:
        payload = dict(payload)
        payload.pop("events", None)
    text = canonical_pretty(payload) if args.format == "json" else audit_markdown(payload)
    if args.output:
        output = Path(args.output)
    else:
        suffix = "json" if args.format == "json" else "md"
        output = paths.reports_dir / f"session-{args.session_id}-audit.{suffix}"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text + ("\n" if not text.endswith("\n") else ""), encoding="utf-8")
    terminal.emit(
        {"output": str(output), "session_id": args.session_id, "integrity": payload["integrity"]},
        human=f"{terminal.success('Audit written')}\n{output}",
    )
    return 0 if payload["integrity"]["valid"] else 1


def _run_markdown(payload: Mapping[str, Any]) -> str:
    lines = [
        f"# W1 Nexus Run Report: `{payload['run_id']}`",
        "",
        f"- Session: `{payload['session_id']}`",
        f"- Status: **{payload['status']}**",
        f"- Started provider calls: **{payload['started_calls']}**",
        f"- Last error: `{payload['last_error_code']}`",
        "",
        "## Tasks",
        "",
        "| Task | Status | Resource | Attempts | Extra review |",
        "|---|---|---|---:|---|",
    ]
    for task in payload["tasks"]:
        lines.append(
            f"| `{task['task_id']}` | {task['status']} | {task['resource_id'] or '-'} | "
            f"{task['attempts']} | {'yes' if task['requires_extra_review'] else 'no'} |"
        )
    lines.extend(["", "## Final result", "", "```json", canonical_pretty(payload["result"]), "```", ""])
    return "\n".join(lines)


def command_export(args: argparse.Namespace, terminal: Terminal) -> int:
    paths = _paths(args)
    with OrchestratorJournal(paths.journal_db) as journal:
        payload = run_snapshot(journal, args.run_id)
    text = canonical_pretty(payload) if args.format == "json" else _run_markdown(payload)
    if args.output:
        output = Path(args.output)
    else:
        suffix = "json" if args.format == "json" else "md"
        output = paths.reports_dir / f"run-{args.run_id}.{suffix}"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text + ("\n" if not text.endswith("\n") else ""), encoding="utf-8")
    terminal.emit({"output": str(output), "run_id": args.run_id}, human=f"{terminal.success('Run report written')}\n{output}")
    return 0


def command_demo(args: argparse.Namespace, terminal: Terminal) -> int:
    paths = initialize_workspace(args.workspace, force=False)
    result, audit = run_reference_demo(
        paths.root,
        event_listener=terminal.event_listener(args.events),
        reset=args.reset,
    )
    payload = _result_payload(result)
    payload["audit"] = audit
    report = paths.reports_dir / "reference-demo-result.json"
    write_json(report, payload)
    human = _run_human(payload, terminal) + "\n\n" + (
        f"Session integrity: {terminal.success('valid') if audit['session_integrity'] else terminal.error('INVALID')}\n"
        f"Protocol events: {audit['event_count']}\n"
        f"Provider calls: {audit['started_calls']}\n"
        f"Report: {report}"
    )
    terminal.emit(payload, human=human)
    return 0 if result.status == "completed" and audit["session_integrity"] else 1


def command_benchmark(args: argparse.Namespace, terminal: Terminal) -> int:
    paths = initialize_workspace(args.workspace, force=False)
    first, first_audit = run_reference_demo(paths.root, reset=args.reset)
    second, second_audit = run_reference_demo(paths.root, reset=False)
    with OrchestratorJournal(paths.journal_db) as journal:
        calls = journal.call_rows(DEMO_RUN_ID)
    leaked_fields = []
    keys = []
    for call in calls:
        request = json.loads(call["request_json"])
        context = request.get("context", {})
        if "private-note" in context:
            leaked_fields.append({"call_id": call["call_id"], "field": "private-note"})
        keys.append(call["idempotency_key"])
    checks = {
        "completed": first.status == "completed",
        "integrity_valid": bool(first_audit["session_integrity"]),
        "quota_fallback_disclosed": any(
            item.get("reason") == "quota_exhausted" for item in first.disclosures
        ),
        "extra_review_required": any(
            item.get("additional_review_required") is True for item in first.disclosures
        ),
        "context_minimised": not leaked_fields,
        "idempotency_keys_unique": len(keys) == len(set(keys)),
        "rerun_did_not_duplicate_events": first_audit["event_count"] == second_audit["event_count"],
        "rerun_did_not_duplicate_calls": first_audit["started_calls"] == second_audit["started_calls"],
        "same_final_output": first.final_output == second.final_output,
    }
    payload = {
        "ok": all(checks.values()),
        "checks": checks,
        "event_count": first_audit["event_count"],
        "provider_call_count": first_audit["started_calls"],
        "leaked_fields": leaked_fields,
        "scope": "W1 internal resilience invariants; not a third-party product benchmark",
    }
    rows = [("PASS" if ok else "FAIL", name) for name, ok in checks.items()]
    terminal.emit(payload, human=table(["STATE", "CHECK"], rows))
    return 0 if payload["ok"] else 1


def _load_action_request(path: str | Path) -> ActionRequest:
    payload = load_json(path)
    try:
        return ActionRequest(
            action_id=payload["action_id"],
            kind=payload["kind"],
            parameters=payload.get("parameters", {}),
            requested_by=payload.get("requested_by", "local-user"),
            reason=payload.get("reason"),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise CLIError("action_request_invalid", str(exc)) from exc


def _open_action_runtime(paths: WorkspacePaths) -> ActionRuntime:
    return ActionRuntime(paths.root, state_dir=paths.state_dir, policy=action_policy(paths))


def command_actions(args: argparse.Namespace, terminal: Terminal) -> int:
    paths = _paths(args)
    with _open_action_runtime(paths) as runtime:
        if args.actions_command == "list":
            payload = {"actions": runtime.list_actions(limit=args.limit)}
            rows = [
                (item["action_id"], item["kind"], item["status"], item["created_at"])
                for item in payload["actions"]
            ]
            terminal.emit(payload, human=table(["ACTION", "KIND", "STATUS", "CREATED"], rows))
            return 0
        if args.actions_command == "show":
            result = runtime.get_result(args.action_id)
            if result is None:
                raise CLIError("action_not_found", f"Action not found: {args.action_id}")
            terminal.emit(asdict(result))
            return 0
        if args.actions_command == "undo":
            result = runtime.undo(args.action_id, undo_action_id=args.undo_action_id)
            terminal.emit(asdict(result))
            return 0
        request = _load_action_request(args.request)
        plan = runtime.plan(request)
        if args.actions_command == "plan":
            terminal.emit(asdict(plan))
            return 0
        if args.actions_command == "approve":
            approval = runtime.issue_approval(
                plan,
                issued_by=args.issued_by,
                ttl_seconds=args.ttl_seconds,
            )
            payload = asdict(approval)
            output = Path(args.output) if args.output else paths.state_dir / f"{approval.approval_id}.json"
            write_json(output, payload)
            terminal.emit({"approval": payload, "output": str(output)})
            return 0
        approval = load_json(args.approval) if args.approval else None
        if args.approve:
            approval = asdict(runtime.issue_approval(plan, issued_by=args.issued_by, ttl_seconds=args.ttl_seconds))
        try:
            result = runtime.execute(request, approval=approval)
        except ActionApprovalRequired as exc:
            raise CLIError(
                "action_approval_required",
                f"Approval required for digest {exc.plan.action_digest}. Run `w1 actions approve` or pass --approve.",
            ) from exc
        terminal.emit(asdict(result))
        return 0 if result.status == "completed" else 1


def _computer_action_id(args: argparse.Namespace, kind: str) -> str:
    supplied = getattr(args, "action_id", None)
    if supplied:
        return supplied
    return f"computer-{kind.replace('.', '-')}-{secrets.token_hex(6)}"


def _computer_selector_from_args(args: argparse.Namespace) -> dict[str, Any]:
    mapping = {
        "element_id": getattr(args, "element_id", None),
        "role": getattr(args, "role", None),
        "name": getattr(args, "name", None),
        "name_glob": getattr(args, "name_glob", None),
        "class_name": getattr(args, "class_name", None),
        "control_id": getattr(args, "control_id", None),
        "process_id": getattr(args, "process_id", None),
        "window_title": getattr(args, "window_title", None),
        "window_glob": getattr(args, "window_glob", None),
    }
    return {key: value for key, value in mapping.items() if value is not None}


def command_computer(args: argparse.Namespace, terminal: Terminal) -> int:
    if args.computer_command == "status":
        payload = detect_computer_backend()
        terminal.emit(payload)
        return 0 if payload["available"] else 2
    if args.computer_command == "benchmark":
        payload = run_governed_computer_use_benchmark()
        terminal.emit(payload)
        return 0 if payload["passed"] else 1

    paths = _paths(args)
    with _open_action_runtime(paths) as runtime:
        if args.computer_command == "observe":
            request = ActionRequest(
                action_id=_computer_action_id(args, "observe"),
                kind="computer.observe",
                parameters={},
                requested_by="local-user",
            )
            result = runtime.execute(request)
            terminal.emit(asdict(result))
            return 0

        if args.computer_command == "screenshot":
            request = ActionRequest(
                action_id=_computer_action_id(args, "screenshot"), kind="computer.screenshot", parameters={}, requested_by=args.issued_by
            )
            plan = runtime.plan(request)
            if not args.approve:
                terminal.emit({"plan": asdict(plan), "executed": False})
                return 2
            approval = runtime.issue_approval(plan, issued_by=args.issued_by)
            result = runtime.execute(request, approval=approval)
            terminal.emit({"plan": asdict(plan), "result": asdict(result), "executed": True})
            return 0

        if args.computer_command == "elements":
            selector = _computer_selector_from_args(args)
            request = ActionRequest(
                action_id=_computer_action_id(args, "elements"),
                kind="computer.elements.list",
                parameters={"selector": selector},
                requested_by=args.issued_by,
            )
            plan = runtime.plan(request)
            if not args.approve:
                terminal.emit({"plan": asdict(plan), "executed": False})
                return 2
            approval = runtime.issue_approval(plan, issued_by=args.issued_by)
            result = runtime.execute(request, approval=approval)
            terminal.emit({"plan": asdict(plan), "result": asdict(result), "executed": True})
            return 0

        if args.computer_command == "click-element":
            selector = _computer_selector_from_args(args)
            request = ActionRequest(
                action_id=_computer_action_id(args, "click-element"),
                kind="computer.element.click",
                parameters={
                    "selector": selector, "expected_fingerprint": args.fingerprint,
                    "button": args.button, "clicks": args.clicks,
                },
                requested_by=args.issued_by,
            )
            plan = runtime.plan(request)
            if not args.approve:
                terminal.emit({"plan": asdict(plan), "executed": False})
                return 2
            approval = runtime.issue_approval(plan, issued_by=args.issued_by)
            result = runtime.execute(request, approval=approval)
            terminal.emit({"plan": asdict(plan), "result": asdict(result), "executed": True})
            return 0

        if args.computer_command == "type":
            if not args.approve:
                terminal.emit({
                    "executed": False,
                    "requires_immediate_approval": True,
                    "reason": "Ephemeral text is never persisted, so secure typing must be approved and executed in the same process.",
                })
                return 2
            selector = _computer_selector_from_args(args)
            text = sys.stdin.read().rstrip("\r\n") if args.stdin else getpass.getpass("Ephemeral text (not journalled): ")
            input_ref = runtime.register_ephemeral_input(text, ttl_seconds=args.ttl)
            request = ActionRequest(
                action_id=_computer_action_id(args, "type"),
                kind="computer.element.type",
                parameters={
                    "selector": selector, "expected_fingerprint": args.fingerprint,
                    **input_ref.as_request_parameters(),
                },
                requested_by=args.issued_by,
            )
            plan = runtime.plan(request)
            approval = runtime.issue_approval(plan, issued_by=args.issued_by)
            result = runtime.execute(request, approval=approval)
            terminal.emit({"plan": asdict(plan), "result": asdict(result), "executed": True})
            return 0

        if args.computer_command == "choose-file":
            selector = _computer_selector_from_args(args)
            request = ActionRequest(
                action_id=_computer_action_id(args, "choose-file"),
                kind="computer.file.choose",
                parameters={
                    "selector": selector, "expected_fingerprint": args.fingerprint,
                    "path": args.path, "submit": not args.no_submit,
                },
                requested_by=args.issued_by,
            )
            plan = runtime.plan(request)
            if not args.approve:
                terminal.emit({"plan": asdict(plan), "executed": False})
                return 2
            approval = runtime.issue_approval(plan, issued_by=args.issued_by)
            result = runtime.execute(request, approval=approval)
            terminal.emit({"plan": asdict(plan), "result": asdict(result), "executed": True})
            return 0

        if args.computer_command == "move":
            kind = "computer.pointer.move"
            parameters = {"x": args.x, "y": args.y}
        elif args.computer_command == "click":
            kind = "computer.pointer.click"
            parameters = {"x": args.x, "y": args.y, "button": args.button, "clicks": args.clicks}
        elif args.computer_command == "scroll":
            kind = "computer.scroll"
            parameters = {"x": args.x, "y": args.y, "delta": args.delta}
        else:
            kind = "computer.key.press"
            parameters = {"key": args.key}

        request = ActionRequest(
            action_id=_computer_action_id(args, args.computer_command),
            kind=kind,
            parameters=parameters,
            requested_by=args.issued_by,
        )
        plan = runtime.plan(request)
        if not args.approve:
            terminal.emit({"plan": asdict(plan), "executed": False})
            return 2
        approval = runtime.issue_approval(plan, issued_by=args.issued_by)
        result = runtime.execute(request, approval=approval)
        terminal.emit({"plan": asdict(plan), "result": asdict(result), "executed": True})
        return 0 if result.status == "completed" else 1


def _worktree_request(args: argparse.Namespace) -> ActionRequest:
    if args.worktrees_command == "create":
        return ActionRequest(
            action_id=f"worktree-create-{args.agent_id}",
            kind="git.worktree.create",
            parameters={"agent_id": args.agent_id, "branch": args.branch, "ref": args.ref},
            requested_by=args.issued_by,
        )
    return ActionRequest(
        action_id=f"worktree-remove-{args.agent_id}",
        kind="git.worktree.remove",
        parameters={"agent_id": args.agent_id, "force": bool(args.force)},
        requested_by=args.issued_by,
    )


def command_worktrees(args: argparse.Namespace, terminal: Terminal) -> int:
    paths = _paths(args)
    with _open_action_runtime(paths) as runtime:
        if args.worktrees_command == "list":
            payload = {"worktrees": runtime.list_worktrees()}
            rows = [(item["agent_id"], item["branch"], item["path"]) for item in payload["worktrees"]]
            terminal.emit(payload, human=table(["AGENT", "BRANCH", "PATH"], rows))
            return 0
        request = _worktree_request(args)
        plan = runtime.plan(request)
        if not args.approve:
            terminal.emit({"plan": asdict(plan), "executed": False})
            return 2
        approval = runtime.issue_approval(plan, issued_by=args.issued_by)
        result = runtime.execute(request, approval=approval)
        terminal.emit(asdict(result))
        return 0 if result.status == "completed" else 1



def _parse_name_pairs(values: Sequence[str], *, field_name: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise CLIError("mcp_pair_invalid", f"{field_name} values must use NAME=ENV syntax: {value}")
        name, source = value.split("=", 1)
        if not name or not source:
            raise CLIError("mcp_pair_invalid", f"{field_name} values must use NAME=ENV syntax: {value}")
        result[name] = source
    return result


def _tool_arguments(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    value = load_json(path)
    return dict(value)


def command_mcp(args: argparse.Namespace, terminal: Terminal) -> int:
    paths = _paths(args)
    configs = load_mcp_configs(paths.mcp_servers)
    if args.mcp_command == "servers":
        if args.mcp_servers_command == "list":
            payload = {"protocol_version": "2025-11-25", "servers": [item.redacted() for item in configs]}
            rows = [(item.server_id, item.transport, "enabled" if item.enabled else "disabled", item.trust_mode) for item in configs]
            terminal.emit(payload, human=table(["SERVER", "TRANSPORT", "STATE", "TRUST"], rows))
            return 0
        if args.mcp_servers_command == "remove":
            remaining = [item for item in configs if item.server_id != args.server_id]
            if len(remaining) == len(configs):
                raise CLIError("mcp_server_not_found", f"MCP server not found: {args.server_id}")
            write_mcp_configs(paths.mcp_servers, remaining)
            terminal.emit({"removed": args.server_id, "server_count": len(remaining)})
            return 0
        if any(item.server_id == args.server_id for item in configs):
            raise CLIError("mcp_server_duplicate", f"MCP server already exists: {args.server_id}")
        command: tuple[str, ...] = ()
        if args.command_json:
            try:
                raw_command = json.loads(args.command_json)
            except json.JSONDecodeError as exc:
                raise CLIError("mcp_command_json_invalid", str(exc)) from exc
            if not isinstance(raw_command, list) or not raw_command or any(not isinstance(item, str) or not item for item in raw_command):
                raise CLIError("mcp_command_json_invalid", "--command-json must be a non-empty JSON array of strings")
            command = tuple(raw_command)
        try:
            config = MCPServerConfig(
                server_id=args.server_id,
                transport=args.transport,
                command=command,
                url=args.url,
                timeout_seconds=args.timeout_seconds,
                environment=_parse_name_pairs(args.env, field_name="--env"),
                headers_from_env=_parse_name_pairs(args.header_env, field_name="--header-env"),
                bearer_token_env=args.bearer_token_env,
                trust_mode=args.trust_mode,
                auto_approve_tools=tuple(args.auto_approve_tool),
                allowed_tools=tuple(args.allow_tool),
                workspace_root=args.workspace_root,
                allow_insecure_loopback=bool(args.allow_insecure_loopback),
            )
        except MCPConfigurationError as exc:
            raise CLIError(exc.code, str(exc)) from exc
        configs.append(config)
        write_mcp_configs(paths.mcp_servers, configs)
        terminal.emit({"added": config.redacted(), "server_count": len(configs)})
        return 0
    if args.mcp_command == "probe":
        manager = MCPManager(configs)
        try:
            payload = manager.probe(args.server_id)
        except Exception as exc:
            raise CLIError(getattr(exc, "code", "mcp_probe_failed"), str(exc)) from exc
        terminal.emit(payload)
        return 0
    if args.mcp_command == "serve":
        server, bundle = build_w1_mcp_server(
            workspace_root=paths.root,
            state_dir=paths.state_dir,
            session_db=paths.session_db,
            mcp_config_path=paths.mcp_servers,
            workspace_config_path=paths.config,
            trusted_recorders=trusted_recorders(paths),
        )
        try:
            MCPStdioServer(server).serve_forever()
        finally:
            bundle.close()
        return 0
    raise CLIError("mcp_command_invalid")


def _build_tool_bundle(paths: WorkspacePaths, args: argparse.Namespace):
    servers = tuple(getattr(args, "server", ()) or ())
    include_remote = not bool(getattr(args, "local_only", False))
    return build_registry_bundle(
        workspace_root=paths.root,
        state_dir=paths.state_dir,
        session_db=paths.session_db,
        mcp_config_path=paths.mcp_servers,
        trusted_recorders=trusted_recorders(paths),
        include_remote=include_remote,
        server_ids=(servers or None),
    )


def command_tools(args: argparse.Namespace, terminal: Terminal) -> int:
    paths = _paths(args)
    with _build_tool_bundle(paths, args) as bundle:
        registry = bundle.registry
        if args.tools_command == "list":
            tools = [asdict(item) for item in registry.list_tools()]
            rows = [(item["name"], item["source"], item["risk"], "yes" if item["requires_approval"] else "no") for item in tools]
            terminal.emit({"tools": tools, "count": len(tools)}, human=table(["TOOL", "SOURCE", "RISK", "APPROVAL"], rows))
            return 0
        if args.tools_command == "history":
            payload = {"calls": registry.list_calls(limit=args.limit)}
            terminal.emit(payload)
            return 0
        arguments = _tool_arguments(args.arguments)
        plan = registry.plan(call_id=args.call_id, tool_name=args.name, arguments=arguments)
        if args.tools_command == "plan":
            terminal.emit(asdict(plan))
            return 0
        if args.tools_command == "approve":
            approval = registry.issue_approval(plan, issued_by=args.issued_by, ttl_seconds=args.ttl_seconds)
            output = Path(args.output) if args.output else paths.state_dir / f"{approval.approval_id}.json"
            write_json(output, asdict(approval))
            terminal.emit({"approval": asdict(approval), "output": str(output)})
            return 0
        approval = load_json(args.approval) if args.approval else None
        if args.approve:
            approval = asdict(registry.issue_approval(plan, issued_by=args.issued_by, ttl_seconds=args.ttl_seconds))
        try:
            result = registry.call(call_id=args.call_id, tool_name=args.name, arguments=arguments, approval=approval)
        except ToolApprovalRequired as exc:
            raise CLIError("tool_approval_required", f"Approval required for digest {exc.plan.call_digest}. Run `w1 tools approve` or pass --approve.") from exc
        terminal.emit(asdict(result))
        return 0 if result.status == "completed" and not result.is_error else 1


def command_resources(args: argparse.Namespace, terminal: Terminal) -> int:
    paths = _paths(args)
    manager = MCPManager(load_mcp_configs(paths.mcp_servers))
    client = manager.client(args.server)
    try:
        client.initialize()
        if args.resources_command == "list":
            resources = client.list_resources()
            terminal.emit({"server_id": args.server, "resources": resources, "count": len(resources)})
        else:
            terminal.emit(client.read_resource(args.uri))
    finally:
        client.close()
    return 0


def command_prompts(args: argparse.Namespace, terminal: Terminal) -> int:
    paths = _paths(args)
    manager = MCPManager(load_mcp_configs(paths.mcp_servers))
    client = manager.client(args.server)
    try:
        client.initialize()
        if args.prompts_command == "list":
            prompts = client.list_prompts()
            terminal.emit({"server_id": args.server, "prompts": prompts, "count": len(prompts)})
        else:
            terminal.emit(client.get_prompt(args.name, {str(k): str(v) for k, v in _tool_arguments(args.arguments).items()}))
    finally:
        client.close()
    return 0


def _sandbox_request_from_file(path: str) -> Any:
    payload = dict(load_json(path))
    secret_env = dict(payload.pop("secret_env", {}))
    if "secret_values" in payload:
        raise CLIError("sandbox_secret_values_forbidden", "Store secret environment references, not secret values, in request JSON")
    secret_values: dict[str, str] = {}
    for secret_name, environment_name in secret_env.items():
        value = os.environ.get(str(environment_name))
        if value is None:
            raise CLIError("sandbox_secret_missing", f"Missing environment variable {environment_name} for secret {secret_name}")
        secret_values[str(secret_name)] = value
    payload["secret_values"] = secret_values
    return request_from_mapping(payload)


def command_sandboxes(args: argparse.Namespace, terminal: Terminal) -> int:
    paths = _paths(args)
    fabric = SecureExecutionFabric(paths.root, state_dir=paths.state_dir)
    try:
        if args.sandboxes_command == "doctor":
            terminal.emit(fabric.doctor())
            return 0
        if args.sandboxes_command == "profiles":
            if args.sandbox_profiles_command == "list":
                terminal.emit({"profiles": fabric.journal.list_profiles()})
                return 0
            if args.sandbox_profiles_command == "show":
                terminal.emit(asdict(fabric.load_profile(args.profile_id)))
                return 0
            profile = SandboxProfile.from_mapping(load_json(args.file))
            digest = fabric.save_profile(profile)
            terminal.emit({"profile_id": profile.profile_id, "profile_digest": digest, "status": "saved"})
            return 0
        if args.sandboxes_command == "list":
            terminal.emit({"executions": fabric.journal.list_executions(limit=args.limit)})
            return 0
        if args.sandboxes_command == "status":
            terminal.emit(fabric.journal.get(args.execution_id))
            return 0
        if args.sandboxes_command == "verify-attestation":
            entry = fabric.journal.get(args.execution_id)
            result = entry.get("result") or {}
            attestation = result.get("attestation")
            if not attestation:
                raise CLIError("sandbox_attestation_missing", "Execution has no attestation")
            fabric.verify_attestation(attestation)
            terminal.emit({"execution_id": args.execution_id, "valid": True, "attestation_id": attestation["attestation_id"]})
            return 0
        request = _sandbox_request_from_file(args.request)
        if args.sandboxes_command == "plan":
            terminal.emit(fabric.plan(request))
            return 0
        result = fabric.execute(request)
        terminal.emit(asdict(result))
        return 0 if result.status == "completed" else 1
    finally:
        fabric.close()


def _parallel_runtime(args: argparse.Namespace) -> ParallelAgentRuntime:
    paths = _paths(args)
    return ParallelAgentRuntime(paths.root, journal_path=paths.state_dir / "parallel-runtime.sqlite3")


def command_agents(args: argparse.Namespace, terminal: Terminal) -> int:
    runtime = _parallel_runtime(args)
    try:
        if args.agents_command == "status":
            terminal.emit(runtime.status(args.run_id))
            return 0
        payload = load_json(args.plan)
        policy = ParallelRunPolicy(**dict(payload.get("policy", {})))
        runtime.close()
        paths = _paths(args)
        runtime = ParallelAgentRuntime(paths.root, policy=policy, journal_path=paths.state_dir / "parallel-runtime.sqlite3")
        jobs = []
        for item in payload.get("jobs", []):
            value = dict(item)
            for key in ("argv", "dependencies", "resource_paths", "expected_outputs", "sandbox_artifact_globs"):
                value[key] = tuple(value.get(key, ()))
            jobs.append(AgentJob(**value))
        result = runtime.run(
            jobs,
            run_id=str(payload["run_id"]),
            approved_by=args.approved_by,
            target_branch=payload.get("target_branch"),
        )
        terminal.emit(result)
        return 0 if result["run"]["status"] == "completed" else 1
    finally:
        runtime.close()


def command_merge(args: argparse.Namespace, terminal: Terminal) -> int:
    runtime = _parallel_runtime(args)
    coordinator = MergeCoordinator(runtime)
    try:
        if args.merge_command == "propose":
            argv = ()
            if args.test_argv_json:
                parsed = json.loads(args.test_argv_json)
                if not isinstance(parsed, list) or not all(isinstance(x, str) for x in parsed):
                    raise CLIError("invalid_test_argv", "--test-argv-json must be a JSON string array")
                argv = tuple(parsed)
            terminal.emit(asdict(coordinator.propose(args.run_id, test_argv=argv)))
            return 0
        if args.merge_command == "review":
            terminal.emit(asdict(coordinator.review(args.proposal_id, reviewer_id=args.reviewer, outcome=args.outcome, rationale=args.rationale)))
            return 0
        commit = coordinator.apply(args.proposal_id, approved_by=args.approved_by)
        terminal.emit({"proposal_id": args.proposal_id, "commit_sha": commit, "status": "applied"})
        return 0
    finally:
        runtime.close()



def _memory_identity(paths: WorkspacePaths, args: argparse.Namespace) -> tuple[str, str, MemoryPrincipal, Mapping[str, Any]]:
    config = load_json(paths.config)
    memory_config = config.get("memory", {})
    if not isinstance(memory_config, dict):
        raise CLIError("memory_configuration_invalid", "workspace memory configuration must be an object")
    principal_config = memory_config.get("principal", {})
    if not isinstance(principal_config, dict):
        principal_config = {}
    namespace_id = getattr(args, "namespace", None) or memory_config.get("namespace_id", "w1-local")
    project_id = getattr(args, "project", None) or memory_config.get("project_id", "default-project")
    principal_type = getattr(args, "principal_type", None) or principal_config.get("principal_type", "human")
    principal_id = getattr(args, "principal_id", None) or principal_config.get("principal_id", "local-owner")
    groups = tuple(dict.fromkeys([*principal_config.get("groups", []), *getattr(args, "group", [])]))
    try:
        principal = MemoryPrincipal(str(principal_type), str(principal_id), tuple(str(item) for item in groups))
    except Exception as exc:
        raise CLIError("memory_principal_invalid", str(exc)) from exc
    return str(namespace_id), str(project_id), principal, memory_config


def _memory_draft(document: Mapping[str, Any], *, namespace_id: str, project_id: str, actor: MemoryPrincipal) -> MemoryDraft:
    source = document.get("source", {})
    if not isinstance(source, dict):
        raise CLIError("memory_source_invalid", "source must be an object")
    captured_by_value = source.get("captured_by")
    if captured_by_value is None:
        captured_by = actor
    elif isinstance(captured_by_value, dict):
        captured_by = MemoryPrincipal(
            str(captured_by_value.get("principal_type", actor.principal_type)),
            str(captured_by_value.get("principal_id", actor.principal_id)),
        )
    else:
        raise CLIError("memory_captured_by_invalid", "source.captured_by must be an object")
    provenance = MemoryProvenance(
        source_type=str(source.get("source_type", "user_statement")),
        captured_by=captured_by,
        captured_at=str(source.get("captured_at", utc_now())) if source.get("captured_at") else utc_now(),
        source_ref=source.get("source_ref"),
        source_excerpt=source.get("source_excerpt"),
    )
    value = document.get("value")
    if "value" not in document:
        raise CLIError("memory_value_required", "Memory input requires value")
    return MemoryDraft(
        memory_id=document.get("memory_id"),
        namespace_id=str(document.get("namespace_id", namespace_id)),
        project_id=str(document.get("project_id", project_id)),
        kind=str(document.get("kind", "fact")),
        subject=str(document.get("subject", "")),
        predicate=str(document.get("predicate", "")),
        value=value,
        text=document.get("text"),
        summary=document.get("summary"),
        confidence=float(document.get("confidence", 1.0)),
        sensitivity=str(document.get("sensitivity", "internal")),
        visibility=str(document.get("visibility", "private")),
        valid_from=document.get("valid_from"),
        valid_until=document.get("valid_until"),
        stale_after=document.get("stale_after"),
        tags=tuple(str(item) for item in document.get("tags", [])),
        object_entity=document.get("object_entity"),
        team_id=document.get("team_id"),
        provenance=provenance,
    )


def _memory_record_payload(record: Any) -> dict[str, Any]:
    payload = asdict(record)
    payload["owner"] = record.owner.as_ref()
    payload["citation"] = record.citation
    payload["is_stale"] = record.is_stale
    return payload


def command_memory(args: argparse.Namespace, terminal: Terminal) -> int:
    paths = _paths(args)
    namespace_id, project_id, principal, memory_config = _memory_identity(paths, args)
    with MemoryStore(paths.memory_db) as store:
        command = args.memory_command
        if command == "add":
            document = load_json(args.file)
            draft = _memory_draft(document, namespace_id=namespace_id, project_id=project_id, actor=principal)
            share_with = []
            for item in document.get("share_with", []):
                if not isinstance(item, dict):
                    raise CLIError("memory_share_entry_invalid", "share_with entries must be objects")
                target = MemoryPrincipal(str(item["principal_type"]), str(item["principal_id"]))
                share_with.append((target, tuple(str(value) for value in item.get("permissions", ["read"]))))
            terminal.emit(_memory_record_payload(store.add(draft, actor=principal, share_with=share_with)))
            return 0
        if command == "revise":
            draft = _memory_draft(load_json(args.file), namespace_id=namespace_id, project_id=project_id, actor=principal)
            terminal.emit(_memory_record_payload(store.revise(args.memory_id, draft, actor=principal)))
            return 0
        if command == "get":
            terminal.emit(_memory_record_payload(store.get(args.memory_id, actor=principal, include_terminal=args.include_terminal)))
            return 0
        if command == "history":
            terminal.emit({"memory_id": args.memory_id, "revisions": store.history(args.memory_id, actor=principal)})
            return 0
        if command == "list":
            records = store.list_memories(
                namespace_id=namespace_id,
                project_id=project_id,
                actor=principal,
                include_terminal=args.include_terminal,
                limit=args.limit,
            )
            terminal.emit({"memories": [_memory_record_payload(item) for item in records], "count": len(records)})
            return 0
        if command == "search":
            query = MemoryQuery(
                namespace_id=namespace_id,
                project_id=project_id,
                text=args.query,
                kinds=tuple(args.kind),
                tags=tuple(args.tag),
                subject=args.subject,
                predicate=args.predicate,
                min_confidence=args.min_confidence,
                include_stale=args.include_stale,
                include_conflicted=args.include_conflicted,
                include_global=args.include_global,
                graph_anchor=args.graph_anchor,
                graph_depth=args.graph_depth,
                limit=args.limit,
            )
            results = store.search(query, actor=principal)
            terminal.emit({
                "results": [
                    {
                        "record": _memory_record_payload(item.record),
                        "score": item.score,
                        "lexical_score": item.lexical_score,
                        "vector_score": item.vector_score,
                        "graph_score": item.graph_score,
                        "freshness_score": item.freshness_score,
                        "reasons": list(item.reasons),
                    }
                    for item in results
                ],
                "count": len(results),
            })
            return 0
        if command == "context":
            budget = args.token_budget or int(memory_config.get("default_token_budget", 1200))
            bundle = store.build_context_bundle(
                MemoryQuery(
                    namespace_id=namespace_id,
                    project_id=project_id,
                    text=args.query,
                    kinds=tuple(args.kind),
                    tags=tuple(args.tag),
                    include_global=args.include_global,
                    limit=100,
                ),
                actor=principal,
                token_budget=budget,
            )
            payload = asdict(bundle)
            payload["prompt_context"] = bundle.as_prompt_context()
            terminal.emit(payload)
            return 0
        if command == "graph":
            edges = store.graph_neighbors(
                namespace_id=namespace_id,
                project_id=project_id,
                anchor=args.anchor,
                actor=principal,
                depth=args.depth,
                limit=args.limit,
            )
            terminal.emit({"anchor": args.anchor, "edges": edges, "count": len(edges)})
            return 0
        if command == "revoke":
            terminal.emit(_memory_record_payload(store.revoke(args.memory_id, actor=principal, reason=args.reason)))
            return 0
        if command == "grant":
            target = MemoryPrincipal(args.to_type, args.to_id)
            store.grant(args.memory_id, target, tuple(args.permission), actor=principal)
            terminal.emit({"memory_id": args.memory_id, "principal": target.as_ref(), "permissions": args.permission})
            return 0
        if command == "ungrant":
            target = MemoryPrincipal(args.from_type, args.from_id)
            store.revoke_grant(args.memory_id, target, tuple(args.permission), actor=principal)
            terminal.emit({"memory_id": args.memory_id, "principal": target.as_ref(), "revoked_permissions": args.permission})
            return 0
        if command == "conflicts":
            if args.memory_conflicts_command == "list":
                conflicts = store.list_conflicts(
                    namespace_id=namespace_id,
                    project_id=project_id,
                    actor=principal,
                    status=args.status,
                )
                terminal.emit({"conflicts": conflicts, "count": len(conflicts)})
                return 0
            terminal.emit(store.resolve_conflict(
                args.conflict_id,
                winner_memory_id=args.winner,
                actor=principal,
                rationale=args.rationale,
            ))
            return 0
        if command == "verify":
            terminal.emit(store.verify_integrity())
            return 0
        if command == "export":
            payload = store.export_scope(namespace_id=namespace_id, project_id=project_id, actor=principal)
            output = Path(args.output) if args.output else paths.reports_dir / f"memory-{namespace_id}-{project_id}.json"
            write_json(output, payload)
            terminal.emit({"output": str(output), "memory_count": len(payload["memories"]), "integrity": payload["integrity"]})
            return 0
    raise CLIError("memory_command_unknown")


def _science_actor(paths: WorkspacePaths, args: argparse.Namespace) -> ScientificPrincipal:
    config = load_json(paths.config)
    default = config.get("science", {}).get("principal", {})
    return ScientificPrincipal(
        str(getattr(args, "principal_type", None) or default.get("principal_type", "human")),
        str(getattr(args, "principal_id", None) or default.get("principal_id", "local-researcher")),
    )


def command_science(args: argparse.Namespace, terminal: Terminal) -> int:
    paths = _paths(args)
    actor = _science_actor(paths, args)
    command = args.science_command
    if command == "verify-package":
        terminal.emit(ScientificLab.verify_package(args.file))
        return 0
    with ScientificLab(paths.science_db, workspace_root=paths.root) as lab:
        if command == "create":
            terminal.emit(asdict(lab.create_study(load_json(args.plan), actor=actor)))
            return 0
        if command == "list":
            studies = lab.list_studies()
            terminal.emit({"studies": studies, "count": len(studies)})
            return 0
        if command == "show":
            terminal.emit(lab.export_study(args.study_id))
            return 0
        if command == "preregister":
            terminal.emit(asdict(lab.preregister(args.study_id, actor=actor)))
            return 0
        if command == "run":
            request = load_json(args.request)
            terminal.emit(lab.execute_experiment(args.study_id, request, actor=actor))
            return 0
        if command == "observe":
            rows = load_json(args.file, required_object=False)
            if not isinstance(rows, list):
                raise CLIError("scientific_observation_array_required")
            terminal.emit(lab.add_observations(args.study_id, rows, actor=actor, source_run_id=args.source_run_id))
            return 0
        if command == "analyze":
            exploratory = load_json(args.exploratory_spec) if args.exploratory_spec else None
            terminal.emit(lab.run_analysis(args.study_id, args.analysis_id, actor=actor, exploratory_spec=exploratory))
            return 0
        if command == "evaluate":
            terminal.emit(lab.evaluate(args.study_id, actor=actor))
            return 0
        if command == "replicate":
            terminal.emit(lab.compare_replication(args.original_study_id, args.replication_study_id, actor=actor))
            return 0
        if command == "review":
            reviewer = ScientificPrincipal(args.reviewer_type, args.reviewer_id)
            terminal.emit(lab.review(args.study_id, reviewer=reviewer, outcome=args.outcome, rationale=args.rationale))
            return 0
        if command == "package":
            terminal.emit(lab.build_reproducibility_package(args.study_id, args.output, actor=actor))
            return 0
        if command == "publish-memory":
            memory_actor = MemoryPrincipal(actor.principal_type, actor.principal_id)
            with MemoryStore(paths.memory_db) as memory_store:
                terminal.emit(lab.publish_supported_hypothesis_to_memory(
                    args.study_id, args.hypothesis_id, memory_store=memory_store,
                    actor=memory_actor, visibility=args.visibility,
                ))
            return 0
        if command == "verify":
            terminal.emit(lab.verify(study_id=args.study_id))
            return 0
        if command == "export":
            payload = lab.export_study(args.study_id)
            output = Path(args.output) if args.output else paths.reports_dir / f"science-{args.study_id}.json"
            write_json(output, payload)
            terminal.emit({"output": str(output), "study_id": args.study_id, "integrity": payload["integrity"]})
            return 0
        if command == "bridge":
            payload = lab.build_w1cip_bridge_manifest(args.study_id)
            output = Path(args.output) if args.output else paths.reports_dir / f"science-{args.study_id}-w1cip-bridge.json"
            write_json(output, payload)
            terminal.emit({"output": str(output), "study_id": args.study_id, "claim_count": len(payload["claim_assessments"]), "integrity_status": payload["source"]["integrity_status"]})
            return 0
    raise CLIError("scientific_command_unknown")


def command_workspace(args: argparse.Namespace, terminal: Terminal) -> int:
    paths = _paths(args)
    if args.workspace_ui_command == "snapshot":
        payload = WorkspaceSnapshotService(paths).snapshot()
        if args.output:
            write_json(args.output, payload)
            terminal.emit({"output": str(Path(args.output)), "snapshot_digest": payload["snapshot_digest"]})
        else:
            terminal.emit(payload)
        return 0

    settings = ConsoleSettings(
        host=args.host,
        port=args.port,
        allow_operations=bool(args.allow_operations),
    )
    try:
        server = create_console_server(paths, settings)
    except ValueError as exc:
        raise CLIError(str(exc), "The Workspace console may bind only to a loopback address.") from exc
    host, port = server.server_address[:2]
    display_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    url = f"http://{display_host}:{port}/"
    terminal.emit(
        {
            "url": url,
            "workspace": str(paths.root),
            "operations_enabled": bool(args.allow_operations),
            "token_path": str(server.application.token_path),
        },
        human=(
            f"{terminal.success('W1 Nexus Workspace is live')}\n"
            f"{url}\n"
            f"Mode: {'operations enabled' if args.allow_operations else 'read-only'}\n"
            "Press Ctrl+C to stop."
        ),
    )
    if not args.no_browser:
        try:
            webbrowser.open(url, new=2)
        except Exception:
            pass
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()
    return 0



def _open_artifact_studio(paths: WorkspacePaths) -> ArtifactStudioStore:
    return ArtifactStudioStore(paths.artifact_studio_db, paths.root)


def command_studio(args: argparse.Namespace, terminal: Terminal) -> int:
    paths = _paths(args)
    if args.studio_command == "tree":
        payload = WorkspaceFileService(paths.root).tree(args.path, max_depth=args.depth)
        terminal.emit(payload)
        return 0
    if args.studio_command == "open":
        terminal.emit(WorkspaceFileService(paths.root).read(args.path).as_dict())
        return 0
    if args.studio_command == "preview":
        terminal.emit(WorkspaceFileService(paths.root).preview(args.path))
        return 0
    if args.studio_command == "git-diff":
        terminal.emit(WorkspaceFileService(paths.root).git_diff(args.path))
        return 0
    if args.studio_command == "terminal":
        try:
            argv = json.loads(args.argv_json)
        except json.JSONDecodeError as exc:
            raise CLIError("argv_json_invalid", str(exc)) from exc
        if not isinstance(argv, list) or not argv or not all(isinstance(item, str) for item in argv):
            raise CLIError("argv_array_required")
        request = ActionRequest(
            action_id=args.action_id,
            kind="command.run",
            parameters={"argv": argv, "cwd": args.cwd},
            requested_by="studio-user",
            reason="Governed Artifact Studio terminal command",
        )
        with _open_action_runtime(paths) as runtime:
            plan = runtime.plan(request)
            if args.terminal_command == "plan":
                terminal.emit(asdict(plan))
                return 0
            approval = None
            if plan.requires_approval:
                if not args.approve:
                    raise ActionApprovalRequired(plan)
                approval = runtime.issue_approval(plan, issued_by=args.issued_by)
            result = runtime.execute(request, approval=approval)
            terminal.emit(asdict(result))
            return 0 if result.status == "completed" else 1
    with _open_artifact_studio(paths) as store:
        if args.artifact_command == "list":
            payload = {"artifacts": store.list_artifacts()}
            terminal.emit(payload)
            return 0
        if args.artifact_command == "create":
            content = Path(args.content_file).read_text(encoding="utf-8") if args.content_file else None
            terminal.emit(store.create_draft(
                artifact_id=args.artifact_id, path=args.path, title=args.title,
                created_by=args.created_by, content=content,
            ).as_dict())
            return 0
        if args.artifact_command == "save":
            content = Path(args.content_file).read_text(encoding="utf-8")
            terminal.emit(store.save_version(
                args.artifact_id, content=content, created_by=args.created_by,
                expected_parent_hash=args.expected_parent_hash,
            ).as_dict())
            return 0
        if args.artifact_command == "history":
            terminal.emit(store.history(args.artifact_id))
            return 0
        if args.artifact_command == "comment":
            terminal.emit(store.add_comment(
                args.artifact_id, version=args.version, author=args.author,
                body=args.body, line_number=args.line,
            ))
            return 0
        if args.artifact_command == "review":
            terminal.emit(store.review(
                args.artifact_id, version=args.version, reviewer=args.reviewer,
                outcome=args.outcome, rationale=args.rationale,
            ))
            return 0
        if args.artifact_command == "publish":
            with _open_action_runtime(paths) as runtime:
                terminal.emit(store.publish(
                    args.artifact_id, published_by=args.published_by,
                    action_runtime=runtime, issue_action_approval=args.approve_action,
                ))
            return 0
        payload = store.verify()
        terminal.emit(payload)
        return 0 if payload["valid"] else 1



def command_office(args: argparse.Namespace, terminal: Terminal) -> int:
    paths = _paths(args)
    if args.office_command == "validate":
        model = load_json(args.spec)
        normalized = validate_artifact_model(model)
        terminal.emit({
            "valid": True,
            "artifact_id": normalized["artifact_id"],
            "kind": normalized["kind"],
            "title": normalized["title"],
        })
        return 0
    if args.office_command == "demo":
        benchmark = run_universal_artifact_benchmark()
        exported: list[str] = []
        if args.output_dir:
            try:
                output_root = WorkspaceFileService(paths.root).resolve(args.output_dir, allow_missing=True)
            except Exception as exc:
                raise CLIError("office_output_path_invalid", str(exc)) from exc
            output_root.mkdir(parents=True, exist_ok=True)
            format_map = {
                "document": ("docx", "pdf", "json"),
                "spreadsheet": ("xlsx", "pdf", "json"),
                "presentation": ("pptx", "pdf", "json"),
            }
            for kind, model in reference_artifact_models().items():
                for selected_format in format_map[kind]:
                    selected = output_root / f"{kind}.{selected_format}"
                    selected.write_bytes(render_artifact(model, selected_format))
                    exported.append(selected.relative_to(paths.root).as_posix())
        payload = {**benchmark, "exported": exported}
        terminal.emit(payload, human=(
            f"Universal Artifact Engine: {'PASS' if benchmark['passed'] else 'FAIL'}\n"
            f"Exports: {len(exported)}"
        ))
        return 0 if benchmark["passed"] else 1
    with UniversalArtifactStore(paths.office_artifacts_db, paths.root) as store:
        if args.office_command == "create":
            terminal.emit(store.create(load_json(args.spec), created_by=args.created_by).as_dict())
            return 0
        if args.office_command == "list":
            terminal.emit({"artifacts": store.list_artifacts()})
            return 0
        if args.office_command == "show":
            terminal.emit(store.current(args.artifact_id).as_dict())
            return 0
        if args.office_command == "history":
            terminal.emit(store.history(args.artifact_id))
            return 0
        if args.office_command == "patch":
            operations = load_json(args.patch, required_object=False)
            if not isinstance(operations, list):
                raise CLIError("patch_array_required", "Patch file must contain a JSON array.")
            terminal.emit(store.patch(
                args.artifact_id, operations, created_by=args.created_by,
                expected_parent_hash=args.expected_parent_hash,
            ).as_dict())
            return 0
        if args.office_command == "review":
            terminal.emit(store.review(
                args.artifact_id, version=args.version, reviewer=args.reviewer,
                outcome=args.outcome, rationale=args.rationale,
            ))
            return 0
        if args.office_command == "export":
            with _open_action_runtime(paths) as runtime:
                terminal.emit(store.export(
                    args.artifact_id, format=args.format, output_path=args.output,
                    exported_by=args.exported_by, action_runtime=runtime,
                    issue_action_approval=args.approve_action,
                ).as_dict())
            return 0
        payload = store.verify()
        terminal.emit(payload)
        return 0 if payload["valid"] else 1

def command_plugins(args: argparse.Namespace, terminal: Terminal) -> int:
    paths = _paths(args)
    manager = PluginManager(paths.root, w1_version="0.1.0.dev50")
    if args.plugin_command == "benchmark":
        payload = run_plugin_adoption_benchmark()
        terminal.emit(payload, human=(
            f"Plugin & Adoption SDK benchmark: {'PASS' if payload['passed'] else 'FAIL'}\n"
            f"Probes: {sum(payload['probes'].values())}/{len(payload['probes'])}\n"
            f"Plugin API: {payload['metrics']['plugin_api_version']} | SDK: {payload['metrics']['sdk_version']}"
        ))
        return 0 if payload["passed"] else 1
    if args.plugin_command == "validate":
        payload = manager.validate_source(args.source)
        terminal.emit(payload, human=(
            f"Plugin: {payload['manifest']['plugin_id']} {payload['manifest']['version']}\n"
            f"Digest: {payload['digest']}\n"
            f"Compatible: {payload['compatibility']['compatible']}"
        ))
        return 0 if payload["compatibility"]["compatible"] else 1
    if args.plugin_command == "scaffold":
        payload = scaffold_plugin(args.destination, plugin_id=args.plugin_id, name=args.name)
        terminal.emit(payload, human=f"Created W1 plugin scaffold at {payload['path']}")
        return 0
    if args.plugin_command == "install":
        record = manager.install(args.source, granted_permissions=args.grant_permission, enable=bool(args.enable))
        terminal.emit(record.as_dict(), human=(
            f"Installed {record.manifest.plugin_id} {record.manifest.version}\n"
            f"Enabled: {record.enabled}\nDigest: {record.digest}"
        ))
        return 0
    if args.plugin_command == "list":
        records = [item.as_dict() for item in manager.list()]
        terminal.emit({"plugin_api_version": PLUGIN_API_VERSION, "sdk_version": SDK_VERSION, "plugins": records}, human=(
            "No plugins installed." if not records else "\n".join(
                f"{item['manifest']['plugin_id']} {item['manifest']['version']}  {'enabled' if item['enabled'] else 'disabled'}" for item in records
            )
        ))
        return 0
    if args.plugin_command == "show":
        terminal.emit(manager.get(args.plugin_id).as_dict())
        return 0
    if args.plugin_command == "verify":
        terminal.emit(manager.verify(args.plugin_id))
        return 0
    if args.plugin_command == "conformance":
        payload = manager.conformance(args.target, timeout_seconds=args.timeout)
        terminal.emit(payload, human=(
            f"Plugin conformance: {'PASS' if payload['passed'] else 'FAIL'}\n"
            f"Probes: {sum(payload['probes'].values())}/{len(payload['probes'])}"
        ))
        return 0 if payload["passed"] else 1
    if args.plugin_command == "enable":
        terminal.emit(manager.set_enabled(args.plugin_id, True).as_dict())
        return 0
    if args.plugin_command == "disable":
        terminal.emit(manager.set_enabled(args.plugin_id, False).as_dict())
        return 0
    if args.plugin_command == "grant":
        terminal.emit(manager.grant(args.plugin_id, args.permission).as_dict())
        return 0
    if args.plugin_command == "revoke":
        terminal.emit(manager.revoke(args.plugin_id, args.permission).as_dict())
        return 0
    if args.plugin_command == "health":
        terminal.emit(manager.run(args.plugin_id, operation="health", timeout_seconds=args.timeout))
        return 0
    raise CLIError("plugin_command_unknown", "Unsupported plugin command.")


def command_packaging(args: argparse.Namespace, terminal: Terminal) -> int:
    if args.packaging_command == "doctor":
        plan = native_build_plan(target=args.target, architecture=args.architecture)
        payload = plan.as_dict()
        terminal.emit(payload, human=(
            f"Native packaging target: {plan.target}/{plan.architecture}\n"
            f"Host: {plan.host_platform}\n"
            f"Can compile installer here: {plan.can_compile_native_installer_here}\n"
            f"Signing identity configured: {plan.signing_configured}"
        ))
        return 0
    if args.packaging_command == "benchmark":
        payload = run_native_packaging_benchmark()
        terminal.emit(payload, human=(
            f"Native Desktop Packaging benchmark: {'PASS' if payload['passed'] else 'FAIL'}\n"
            f"Probes: {sum(payload['probes'].values())}/{len(payload['probes'])}\n"
            "This benchmark generates installer sources but does not claim a native installer was compiled or signed on this host."
        ))
        return 0 if payload["passed"] else 1
    if args.packaging_command == "rc-check":
        payload = verify_windows_release_candidate(Path(args.source_root).expanduser().resolve())
        terminal.emit(payload, human=(
            f"Windows source RC: {'READY' if payload['passed'] else 'BLOCKED'}\n"
            f"Checks: {sum(payload['checks'].values())}/{len(payload['checks'])}\n"
            f"Native EXE compiled here: {payload['claims']['windows_exe_compiled_here']}\n"
            f"Authenticode signed here: {payload['claims']['authenticode_signed_here']}"
        ))
        return 0 if payload["passed"] else 1
    if args.packaging_command == "deep-link":
        action = parse_deep_link(args.uri)
        terminal.emit(action.as_dict())
        return 0
    if args.packaging_command == "sources":
        paths = _paths(args)
        try:
            output = WorkspaceFileService(paths.root).resolve(args.output, allow_missing=True)
        except Exception as exc:
            raise CLIError("native_packaging_output_invalid", str(exc)) from exc
        payload = generate_windows_packaging_sources(output, architecture=args.architecture)
        terminal.emit(payload, human=(
            f"Generated Windows packaging sources: {payload['output_dir']}\n"
            f"Files: {len(payload['files'])}\n"
            "Compilation/signing must run on a compatible native build host."
        ))
        return 0
    if args.packaging_command == "project":
        paths = _paths(args)
        try:
            selected = WorkspaceFileService(paths.root).resolve(args.path, allow_missing=args.packaging_project_command == "create")
        except Exception as exc:
            raise CLIError("native_project_path_invalid", str(exc)) from exc
        if args.packaging_project_command == "create":
            descriptor = ProjectDescriptor(
                name=args.name, workspace=args.workspace_path, open_target=args.open_target
            )
            descriptor.write(selected)
            terminal.emit({"path": str(selected), "descriptor": descriptor.as_dict()})
            return 0
        descriptor = ProjectDescriptor.load(selected)
        terminal.emit({
            "path": str(selected), "descriptor": descriptor.as_dict(),
            "resolved_workspace": str(descriptor.resolve_workspace(selected)),
        })
        return 0
    if args.packaging_command == "service":
        paths = _paths(args)
        controller = NativeServiceController(paths.root)
        if args.packaging_service_command == "status":
            terminal.emit(controller.status())
            return 0
        if args.packaging_service_command == "start":
            state = controller.start(port=args.port)
            terminal.emit(state.as_dict(), human=(
                f"W1 Desktop service started on loopback port {state.port} (pid {state.pid})."
            ))
            return 0
        payload = controller.stop(timeout_seconds=args.timeout)
        terminal.emit(payload)
        return 0
    if args.packaging_command == "verify-update":
        payload = load_json(args.manifest)
        manifest = UpdateManifest.from_mapping(payload)
        artifact = manifest.select(args.platform, args.architecture)
        result = verify_update_artifact(
            artifact, args.artifact,
            require_signature_metadata=not args.allow_missing_signature_metadata,
        )
        terminal.emit({
            "manifest_channel": manifest.channel, "selected_artifact": artifact.as_dict(),
            "verification": result,
        }, human=(
            "Update integrity: VERIFIED\n"
            f"SHA-256: {result['sha256']}\n"
            f"Native signature verified here: {result['native_signature_verified']}"
        ))
        return 0
    raise CLIError("native_packaging_command_unknown", "Unsupported packaging command.")



def command_collab(args: argparse.Namespace, terminal: Terminal) -> int:
    if args.collab_command == "benchmark":
        payload = run_collaboration_benchmark()
        terminal.emit(payload, human=(
            f"Optional Collaboration Fabric benchmark: {'PASS' if payload['passed'] else 'FAIL'}\n"
            f"Probes: {sum(payload['probes'].values())}/{len(payload['probes'])}\n"
            f"Protocol: {payload['metrics']['protocol_version']} | W1-owned cloud calls: {payload['metrics']['w1_owned_cloud_calls']}"
        ))
        return 0 if payload["passed"] else 1

    paths = _paths(args)
    with CollaborationStore(paths.collaboration_db) as store:
        if args.collab_command == "team":
            if args.collab_team_command == "create":
                terminal.emit(store.create_team(args.name, owner_principal_id=args.owner, team_id=args.team_id).as_dict())
                return 0
            if args.collab_team_command == "list":
                terminal.emit({"teams": [item.as_dict() for item in store.list_teams(principal_id=args.principal)]})
                return 0
            if args.collab_team_command == "show":
                terminal.emit(store.get_team(args.team_id).as_dict())
                return 0
            if args.collab_team_command == "members":
                terminal.emit({"members": [item.as_dict() for item in store.list_members(args.team_id, actor=args.actor)]})
                return 0
            if args.collab_team_command == "remove-member":
                terminal.emit(store.remove_member(args.team_id, args.principal_id, actor=args.actor))
                return 0
            terminal.emit(store.set_member_role(args.team_id, args.principal_id, args.role, actor=args.actor).as_dict())
            return 0

        if args.collab_command == "invite":
            if args.collab_invite_command == "create":
                record, token = store.create_invitation(args.team_id, role=args.role, created_by=args.created_by, expires_in_hours=args.expires_hours)
                terminal.emit({"invitation": record.as_dict(), "one_time_token": token, "token_stored_plaintext": False}, human=(
                    f"Invitation {record.invitation_id} created.\n"
                    f"One-time token (shown once): {token}\n"
                    "Only its SHA-256 digest is stored by W1."
                ))
                return 0
            if args.collab_invite_command == "accept":
                token = sys.stdin.read().strip() if args.stdin else getpass.getpass("Invitation token: ").strip()
                if not token:
                    raise CLIError("collaboration_invitation_token_required", "Invitation token is required.")
                terminal.emit(store.accept_invitation(token, principal_id=args.principal).as_dict())
                return 0
            terminal.emit(store.revoke_invitation(args.invitation_id, actor=args.actor).as_dict())
            return 0

        if args.collab_command == "project":
            if args.collab_project_command == "create":
                terminal.emit(store.create_project(args.team_id, args.name, created_by=args.created_by, project_id=args.project_id).as_dict())
                return 0
            if args.collab_project_command == "list":
                terminal.emit({"projects": [item.as_dict() for item in store.list_projects(args.team_id, principal_id=args.principal)]})
                return 0
            terminal.emit(store.get_state(args.project_id, principal_id=args.principal))
            return 0

        if args.collab_command == "share":
            if args.collab_share_command == "grant":
                terminal.emit(store.grant_project_access(args.project_id, args.principal_id, args.access, granted_by=args.actor).as_dict())
                return 0
            store.revoke_project_access(args.project_id, args.principal_id, actor=args.actor)
            terminal.emit({"revoked": True, "project_id": args.project_id, "principal_id": args.principal_id})
            return 0

        if args.collab_command == "token":
            if args.collab_token_command == "issue":
                record, token = store.issue_access_token(args.team_id, principal_id=args.principal, label=args.label, expires_in_hours=args.expires_hours)
                terminal.emit({"record": record.as_dict(), "one_time_token": token, "token_stored_plaintext": False}, human=(
                    f"Access token {record.token_id} issued.\n"
                    f"One-time token (shown once): {token}\n"
                    "The server stores only its SHA-256 digest."
                ))
                return 0
            terminal.emit(store.revoke_access_token(args.token_id, actor=args.actor).as_dict())
            return 0

        if args.collab_command == "replica":
            terminal.emit(store.register_replica(args.team_id, principal_id=args.principal, device_id=args.device_id, label=args.label).as_dict())
            return 0

        if args.collab_command == "sync":
            if args.collab_sync_command == "push":
                mutation = SyncMutation.from_mapping(load_json(args.mutation))
                terminal.emit(store.apply_mutation(mutation, principal_id=args.principal))
                return 0
            terminal.emit(store.pull_events(args.project_id, principal_id=args.principal, after_ordinal=args.after, limit=args.limit))
            return 0

        if args.collab_command == "conflicts":
            if args.collab_conflict_command == "list":
                terminal.emit({"conflicts": [item.as_dict() for item in store.list_conflicts(args.project_id, principal_id=args.principal)]})
                return 0
            mutation = SyncMutation.from_mapping(load_json(args.mutation))
            terminal.emit(store.resolve_conflict(args.conflict_id, mutation, principal_id=args.principal))
            return 0

        if args.collab_command == "audit":
            if args.collab_audit_command == "list":
                terminal.emit({"events": store.list_audit(args.team_id, principal_id=args.principal, limit=args.limit)})
                return 0
            payload = store.verify_audit(args.team_id)
            terminal.emit(payload)
            return 0 if payload["valid"] else 1

        if args.collab_command == "serve":
            settings = CollaborationServerSettings(host=args.host, port=args.port, certfile=args.certfile, keyfile=args.keyfile)
            server = create_collaboration_server(settings, CollaborationService(store))
            scheme = "https" if settings.tls_enabled else "http"
            display_host = "127.0.0.1" if args.host in {"0.0.0.0", "::"} else args.host
            url = f"{scheme}://{display_host}:{server.server_address[1]}"
            terminal.emit({
                "url": url, "protocol_version": COLLABORATION_PROTOCOL_VERSION,
                "self_hosted": True, "w1_owned_cloud_required": False,
                "tls_enabled": settings.tls_enabled,
            }, human=(
                f"W1 Collaboration Fabric is serving at {url}\n"
                "Self-hosted mode; no W1-owned cloud is required. Press Ctrl+C to stop."
            ))
            try:
                server.serve_forever(poll_interval=0.2)
            finally:
                server.server_close()
            return 0

    raise CLIError("collaboration_command_unknown", "Unsupported collaboration command.")


def command_release(args: argparse.Namespace, terminal: Terminal) -> int:
    root = Path(args.workspace).resolve()
    if args.release_command == "benchmark":
        payload = run_release_hardening_benchmark(root if (root / "pyproject.toml").is_file() else None)
        terminal.emit(payload, human=(
            f"Release hardening benchmark: {'PASS' if payload['passed'] else 'FAIL'}\n"
            f"Probes: {sum(payload['probes'].values())}/{len(payload['probes'])}\n"
            f"Fuzz cases: {payload['metrics']['fuzz_cases']} | load mutations: {payload['metrics']['load_mutations']}\n"
            "External benchmark scores claimed by this benchmark: 0"
        ))
        return 0 if payload["passed"] else 1
    if args.release_command == "catalog":
        payload = external_benchmark_catalog()
        terminal.emit(payload, human="\n".join(
            f"{item['benchmark_id']}: {item['name']} [{item['adapter_status']}]"
            for item in payload["benchmarks"]
        ))
        return 0
    if args.release_command == "manifest":
        payload = source_manifest(root)
        if args.output:
            write_json(args.output, payload)
        terminal.emit(payload, human=f"Source tree SHA-256: {payload['tree_sha256']} ({len(payload['files'])} files)")
        return 0
    if args.release_command == "dependencies":
        payload = dependency_inventory(root)
        if args.output:
            write_json(args.output, payload)
        terminal.emit(payload)
        return 0
    if args.release_command == "threat-model":
        markdown = render_threat_model_markdown()
        if args.output:
            Path(args.output).write_text(markdown, encoding="utf-8")
        terminal.emit({"threat_model_version": "1.0", "output": args.output}, human=markdown)
        return 0
    if args.release_command == "gate":
        payload = release_gate(root, public_release=bool(args.public))
        terminal.emit(payload, human=(
            f"{'Public' if args.public else 'Development'} release gate: {'PASS' if payload['passed'] else 'BLOCKED'}\n"
            + ("Blockers: " + ", ".join(payload['blockers']) if payload['blockers'] else "No gate blockers detected.")
        ))
        return 0 if payload["passed"] else 1
    workspace_root = _paths(args).root
    store = ExternalResultStore(workspace_root)
    if args.release_command == "record":
        payload = store.record(load_json(args.result), args.evidence)
        terminal.emit(payload, human=(
            f"Recorded {payload['benchmark_id']} as {payload['result_id']}\n"
            f"Raw evidence SHA-256: {payload['raw_evidence_sha256']}"
        ))
        return 0
    if args.release_command == "results":
        payload = {"results": store.list()}
        terminal.emit(payload)
        return 0
    if args.release_command == "verify-result":
        payload = store.verify(args.result_id, args.evidence)
        terminal.emit(payload)
        return 0 if payload["valid"] else 1
    raise CLIError("release_command_unknown", "Unsupported release command.")


def command_desktop(args: argparse.Namespace, terminal: Terminal) -> int:
    paths = _paths(args)
    if args.desktop_command == "doctor":
        backend = detect_desktop_backend()
        payload = {
            "backend": backend,
            "native_webview_available": backend == "native-webview",
            "browser_pwa_available": True,
            "local_first": True,
            "w1_owned_server_required": False,
            "artifact_studio_benchmark": run_artifact_studio_benchmark(),
            "universal_artifact_benchmark": run_universal_artifact_benchmark(),
        }
        terminal.emit(payload, human=(
            f"Desktop backend: {backend}\n"
            f"Artifact Studio benchmark: {'PASS' if payload['artifact_studio_benchmark']['passed'] else 'FAIL'}\n"
            f"Universal Artifact benchmark: {'PASS' if payload['universal_artifact_benchmark']['passed'] else 'FAIL'}\n"
            "The browser-PWA shell is always available; pywebview is optional for a native window."
        ))
        return 0 if payload["artifact_studio_benchmark"]["passed"] and payload["universal_artifact_benchmark"]["passed"] else 1
    settings = DesktopSettings(
        host=args.host, port=args.port, allow_operations=bool(args.allow_operations),
        open_browser=not getattr(args, "no_browser", False),
    )
    if args.desktop_command == "launch":
        backend = launch_desktop(paths, settings)
        terminal.emit({"backend": backend, "status": "closed", "w1_owned_server_required": False})
        return 0
    try:
        server = create_desktop_server(paths, settings)
    except ValueError as exc:
        raise CLIError(str(exc), "The Desktop shell may bind only to a loopback address.") from exc
    url = f"http://127.0.0.1:{server.server_port}/"
    terminal.emit({
        "url": url, "workspace": str(paths.root), "operations_enabled": settings.allow_operations,
        "backend": detect_desktop_backend(), "token_path": str(server.application.token_path),
        "w1_owned_server_required": False,
    }, human=(
        f"{terminal.success('W1 Nexus Desktop is live')}\n{url}\n"
        f"Backend: {detect_desktop_backend()}\n"
        f"Mode: {'operations enabled' if settings.allow_operations else 'read-only'}\n"
        "Press Ctrl+C to stop."
    ))
    if settings.open_browser:
        try:
            webbrowser.open(url, new=2)
        except Exception:
            pass
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        server.server_close()
    return 0

def command_benchmark_lab(args: argparse.Namespace, terminal: Terminal) -> int:
    from .benchmark_core import (
        BenchmarkMode,
        BenchmarkReportGenerator,
        BenchmarkRunner,
        PreflightValidator,
        get_model_adapter,
    )
    from benchmarks import get_all_suites

    if args.bench_lab_command == "integrity":
        from .benchmark_core.integrity import BenchmarkIntegrityValidator
        validator = BenchmarkIntegrityValidator()
        records = validator.validate_all()
        rows = [
            (
                r.integrity_status,
                r.benchmark,
                r.official_version,
                f"{r.local_task_count}/{r.official_task_count}",
                "YES" if r.official_evaluator else "NO",
                "YES" if r.official_environment else "NO",
                r.notes[:50] + "..." if len(r.notes) > 50 else r.notes,
            )
            for r in records
        ]
        terminal.emit(
            {"ok": True, "suites_validated": len(records), "integrity_status": "DEMO_ONLY_QUALIFIED"},
            human=terminal.heading("NEXUS Benchmark Adapter Integrity Audit")
            + "\n\n"
            + terminal.warning("INTEGRITY CONSTRAINT NOTICE:\n"
                               "All 9 benchmark adapters currently operate in DEMO_ONLY mode using representative tasks.\n"
                               "They measure telemetry, latency, and scaffolding uplift but MUST NOT be claimed as official scores.\n")
            + "\n"
            + table(["INTEGRITY", "BENCHMARK", "VERSION", "TASKS (L/O)", "OFF_EVAL", "OFF_ENV", "NOTES"], rows)
            + f"\nDetailed audit saved to: data/integrity/benchmark_integrity_report.json\nand NEXUS_BENCHMARK_INTEGRITY_REPORT.md\n",
        )
        from .benchmark_core.integrity_reports import generate_integrity_and_status_reports
        generate_integrity_and_status_reports()
        return 0

    if args.bench_lab_command == "availability":
        from .benchmark_core.availability import ModelAvailabilityTester
        tester = ModelAvailabilityTester()
        records = tester.check_all()
        from .benchmark_core.integrity_reports import generate_integrity_and_status_reports
        generate_integrity_and_status_reports()
        all_passed = all(r.availability and r.streaming and r.tool_calling and r.structured_output for r in records)
        rows = [
            (
                "PASS" if r.availability else "FAIL",
                r.display_name,
                r.entitlement,
                str(r.http_status or "N/A"),
                f"{r.latency_ms:.1f}ms",
                "YES" if r.streaming else "NO",
                "YES" if r.tool_calling else "NO",
                "YES" if r.structured_output else "NO",
                r.provider_error[:40] + "..." if r.provider_error and len(r.provider_error) > 40 else (r.provider_error or "None"),
            )
            for r in records
        ]
        gate_status = "READY" if all_passed else "COMPARISON BLOCKED — MODEL AVAILABILITY INCOMPLETE"
        terminal.emit(
            {"ok": all_passed, "gate_status": gate_status, "models": [asdict(r) for r in records]},
            human=terminal.heading("NEXUS Frontier Model Availability & Entitlement Diagnostic")
            + f"\n\nGate Status: {terminal.success(gate_status) if all_passed else terminal.error(gate_status)}\n\n"
            + table(["STATUS", "MODEL", "ENTITLEMENT", "HTTP", "LATENCY", "STREAM", "TOOL", "STRUCT", "PROVIDER_ERROR"], rows)
            + f"\nDiagnostic report saved to: data/availability/model_availability_report.json\n",
        )
        return 0 if all_passed else 1

    if args.bench_lab_command == "preflight":
        validator = PreflightValidator()
        if args.model == "all":
            models = [get_model_adapter("kimi"), get_model_adapter("muse"), get_model_adapter("deepseek")]
        else:
            models = [get_model_adapter(args.model)]
        results = validator.run_all_checks(models)
        all_passed = all(r.passed for r in results)
        rows = [
            ("PASS" if r.passed else "FAIL", r.check_name, f"{r.latency_ms:.1f}ms", r.details)
            for r in results
        ]
        terminal.emit(
            {"ok": all_passed, "checks": [asdict(r) for r in results]},
            human=terminal.heading("NEXUS Benchmarking Lab — Preflight Qualification")
            + "\n\n"
            + table(["STATUS", "CHECK", "LATENCY", "DETAILS"], rows),
        )
        return 0 if all_passed else 1

    if args.bench_lab_command == "smoke":
        suites = get_all_suites()
        tasks = [s.get_smoke_task() for s in suites]
        if getattr(args, "all", False) or args.model == "all":
            models = [get_model_adapter("kimi"), get_model_adapter("muse"), get_model_adapter("deepseek")]
        else:
            models = [get_model_adapter(args.model)]

        modes = []
        if args.mode in ("both", "raw"):
            modes.append(BenchmarkMode.MODE_A_RAW_MODEL)
        if args.mode in ("both", "nexus"):
            modes.append(BenchmarkMode.MODE_B_NEXUS_AGENT)

        runner = BenchmarkRunner()
        records = runner.run_matrix(
            models=models,
            tasks=tasks,
            modes=modes,
            workers=getattr(args, "workers", 3),
            run_id_prefix="nexus-smoke",
        )
        all_passed = all(r.success for r in records) if records else False
        rows = [
            ("PASS" if r.success else "FAIL", r.benchmark, r.model.split("/")[-1], r.mode.value, f"{r.latency_ms:.1f}ms", f"{r.total_tokens or 0} tok")
            for r in records
        ]
        terminal.emit(
            {"ok": all_passed, "smoke_runs": len(records), "success_count": sum(1 for r in records if r.success)},
            human=terminal.heading("NEXUS Benchmarking Lab — Smoke Tests (9 Suites)")
            + "\n\n"
            + table(["STATUS", "BENCHMARK", "MODEL", "MODE", "LATENCY", "TOKENS"], rows),
        )
        return 0 if all_passed else 1

    if args.bench_lab_command == "run":
        suites = get_all_suites()
        if args.benchmark != "all":
            suites = [s for s in suites if args.benchmark.lower() in s.name.lower()]
        tasks = []
        for s in suites:
            tasks.extend(s.get_tasks())

        if args.model == "all":
            models = [get_model_adapter("kimi"), get_model_adapter("muse"), get_model_adapter("deepseek")]
        else:
            models = [get_model_adapter(args.model)]

        modes = []
        if args.mode in ("both", "raw"):
            modes.append(BenchmarkMode.MODE_A_RAW_MODEL)
        if args.mode in ("both", "nexus"):
            modes.append(BenchmarkMode.MODE_B_NEXUS_AGENT)

        runner = BenchmarkRunner()
        records = runner.run_matrix(
            models=models,
            tasks=tasks,
            modes=modes,
            workers=args.workers,
            dry_run=args.dry_run,
            resume=not args.no_resume,
        )
        terminal.emit({"total_evaluations": len(records), "dry_run": args.dry_run})
        return 0

    if args.bench_lab_command == "report":
        from .benchmark_core import BenchmarkReportGenerator, BenchmarkRunner
        runner = BenchmarkRunner()
        records = runner.load_all_records()
        gen = BenchmarkReportGenerator()
        generated = gen.generate_all(records)
        report_lines = "\n".join(f" - {k}: {v.name}" for k, v in generated.items())
        terminal.emit(
            {
                "reports_generated": len(generated),
                "report_dir": str(gen.reports_dir),
                "total_records_processed": len(records),
            },
            human=terminal.heading("NEXUS Benchmark Reports Generated")
            + f"\nProcessed {len(records)} records across data/raw/\n"
            + report_lines
            + f"\n\nReports available in: {gen.reports_dir}",
        )
        return 0

    if args.bench_lab_command == "dashboard":
        dash_path = Path(__file__).resolve().parent.parent.parent / "reports" / "dashboard.html"
        if args.open:
            import webbrowser
            webbrowser.open(dash_path.as_uri())
        terminal.emit({"dashboard": str(dash_path)}, human=f"Dashboard: {dash_path}")
        return 0

    raise CLIError("benchmark_lab_command_unknown", "Unsupported benchmark-lab command.")


def command_capabilities(args: argparse.Namespace, terminal: Terminal) -> int:
    payload = {
        "implemented": [
            "multi-provider task-specific routing",
            "quota-aware failover with protected reserves",
            "independent executor/verifier/reviewer/decision roles",
            "append-only hash-chained protocol ledger",
            "deterministic replay and integrity verification",
            "safe resume with provider idempotency keys",
            "context minimisation and declared field use",
            "plan-only mode with no model calls",
            "JSON event stream and stable machine-readable outputs",
            "auditable run and session exports",
            "workspace-confined reversible file actions",
            "one-time digest-bound approvals for sensitive actions",
            "shell-free command execution with timeout and secret stripping",
            "isolated Git worktrees for parallel agents",
            "MCP 2025-11-25 stdio and Streamable HTTP clients",
            "governed W1 MCP server with tools, resources, and prompts",
            "unified namespaced tool registry with schema validation and one-time approvals",
            "parallel dependency-aware agents in isolated Git worktrees",
            "shared-resource locks, cancellation, timeouts, and persisted progress events",
            "conflict-aware integration proposals with independent review before merge",
            "OCI sandbox profiles with read-only roots, cgroup limits, pids limits, and network namespaces",
            "POSIX local process limits for trusted code when OCI is unavailable",
            "ephemeral secret injection, artifact hashing, resource telemetry, and local execution attestations",
            "provenance-preserving long-term memory with strict project isolation and ACLs",
            "hybrid structured, FTS, hashed-vector, and knowledge-graph retrieval",
            "explicit memory conflicts, expiry, stale-data handling, and token-bounded context bundles",
            "local-first federated Intelligence Search across selected public AI catalogs without a W1-owned server",
            "preregistered scientific studies with immutable observations and falsification criteria",
            "controlled experiments through Secure Execution Fabric with reproducibility packages",
            "planned versus exploratory statistical analyses, independent scientific review, and replication tracking",
            "local-first graphical W1 Nexus Workspace with live SSE operations telemetry",
            "visual agent graph, model quota monitor, memory/science inspectors, Git merge and audit consoles",
            "user-defined multi-model portfolios across local, cloud, private, plugin, and external-app access",
            "parallel collection, fallback chains, and independent verified synthesis without model-count voting",
            "loopback Local Control API and Python SDK for embedding W1 in third-party products",
            "fully local-first operation with no W1-owned server requirement",
            "immutable Artifact Studio drafts, independent review, governed publication, and version integrity",
            "offline project explorer, code/text editor, previews, Git diff, model portfolio selector, and governed terminal",
            "installable browser-PWA desktop shell with optional pywebview native window",
            "canonical universal artifact model for documents, spreadsheets, and presentations",
            "reviewable JSON Pointer patches with immutable revisions and independent approval",
            "governed binary export to DOCX, XLSX, PPTX, PDF, and canonical JSON",
            "structural export validation and local visual office previews",
            "governed Windows desktop observation, PNG screenshot capture, and Win32 accessibility-lite element discovery",
            "selector-bound fingerprint-verified click, workspace file chooser input, and one-shot ephemeral text that is not journalled",
            "digest-bound approvals, short-lived sensitive captures, and deterministic virtual benchmark for computer-use actions",
            "OS-native credential vault adapters for Windows Credential Manager, macOS Keychain, and Linux Secret Service with no plaintext file fallback",
            "generic OAuth authorization-code + PKCE and device authorization with one-time state/verifier handling",
            "multi-account refresh/revocation lifecycle, provider metadata discovery, and redaction-safe credential audit chain",
            "broker credential references usable directly by Provider Connectors and Model Access without putting secrets in model profiles or workspace SQLite",
            "stable native application identity, safe w1:// deep links, and relative .w1nexus project descriptors that never become shell commands",
            "PID-identity-bound loopback Desktop service lifecycle with private state and no command-line secrets",
            "integrity-first update manifests with HTTPS, exact size/SHA-256 verification, and explicit native-signature verification boundary",
            "deterministic Windows PyInstaller/Inno Setup packaging sources with per-user URL/file associations and signing hooks",
            "stable w1cip.sdk 1.x embedding surface and Plugin API 1.0 with fail-closed manifests, integrity locks, explicit grants, subprocess hosting, and conformance tests",
            "self-hosted team tenancy with one-time invitations, role membership, project ACLs, and hash-stored bearer access tokens",
            "optimistic multi-device project synchronization with per-replica sequence/hash chains, idempotent replay, explicit conflict records, and deliberate resolution",
            "append-only team audit federation with verifiable hash chains and an authenticated self-hosted Collaboration API",
            "transport policy that permits plaintext HTTP only on loopback and requires TLS for non-loopback collaboration service bindings",
            "stable CollaborationClient and SyncMutation SDK exports with no W1-owned cloud dependency",
            "explicit live Provider Certification harness with preflight/runtime/full levels, immutable redacted evidence, bounded billable probes, and no implicit provider calls",
            "current provider contract snapshots for OpenAI Responses, Anthropic Messages, Gemini Interactions/generateContent compatibility, xAI Responses, and local/custom OpenAI-compatible runtimes",
        ],
        "not_yet_implemented": [
            "live end-to-end certification evidence for real OpenAI, Anthropic, Gemini, xAI, local, and custom provider accounts/models; the Step 44 harness does not mark a provider certified until explicit live probes are recorded",
            "live end-to-end certification of every OS-native credential backend and provider-specific OAuth consent configuration",
            "automatic loopback OAuth callback listener and cryptographic verification of OIDC ID-token identity claims",
            "native Windows installer compilation and Authenticode signing certification on a Windows build host; macOS notarization and Linux native package generation remain pending",
            "automatic update download/apply flow after native signature verification; Step 38 currently supplies the trusted manifest/integrity boundary",
            "complete Microsoft UI Automation semantics, visual-model screenshot redaction/grounding, and macOS/Linux native computer-use adapters",
            "full-fidelity import and round-trip editing of arbitrary existing office files",
            "advanced PDF annotation, collaborative cursor editing, and rich media editing",
            "distributed multi-host execution",
            "semantic code review beyond Git conflicts and test results",
            "VM-grade isolation and remote confidential-computing attestation",
            "portable hard isolation on hosts without Docker or Podman",
            "outbound hostname allowlists without an external proxy or firewall",
            "digital signatures and external key management",
            "at-rest encryption and external KMS for memory values",
            "foundation-model embedding service enabled by default",
            "advanced exact statistical tests and domain-specific scientific instrument drivers",
            "automatic laboratory hardware control without explicit adapters",
            "OS-level sandboxing of arbitrary third-party plugin code; Step 39 permissions govern W1 host capabilities but are not a VM/OCI security boundary",
            "signed plugin marketplace, remote package acquisition, dependency resolver, and richer tool/UI plugin extension points beyond the managed provider adapter contract",
            "W1-hosted collaboration cloud, managed multi-region relay/NAT traversal, and hosted account provisioning; Step 40 is self-hosted first",
            "end-to-end content encryption with external KMS/device public-key identities and managed disaster recovery for collaboration data",
            "CRDT/OT rich-text cursor co-editing; Step 40 uses explicit optimistic conflicts rather than silently merging semantic edits",
            "scheduled and trigger-based runs",
            "benchmark evidence proving universal superiority over other products",
        ],
        "design_goal": (
            "Outperform single-agent work tools on governed multi-model collaboration, "
            "traceability, quota resilience, and independent verification."
        ),
    }
    if args.compact:
        human = "\n".join(f"+ {item}" for item in payload["implemented"])
    else:
        human = (
            terminal.heading("Implemented strengths")
            + "\n"
            + "\n".join(f"+ {item}" for item in payload["implemented"])
            + "\n\n"
            + terminal.heading("Explicit limits")
            + "\n"
            + "\n".join(f"- {item}" for item in payload["not_yet_implemented"])
        )
    terminal.emit(payload, human=human)
    return 0


def dispatch(args: argparse.Namespace, terminal: Terminal) -> int:
    commands = {
        "init": command_init,
        "doctor": command_doctor,
        "providers": command_providers,
        "accounts": command_accounts,
        "credentials": command_credentials,
        "connections": command_connections,
        "capacity": command_capacity,
        "discovery": command_discovery,
        "gateway": command_gateway,
        "intelligence": command_intelligence,
        "teams": command_teams,
        "models": command_models,
        "access": command_access,
        "evaluate": command_evaluate,
        "plan": command_plan,
        "run": command_run,
        "resume": command_resume,
        "status": command_status,
        "sessions": command_sessions,
        "audit": command_audit,
        "export": command_export,
        "demo": command_demo,
        "benchmark": command_benchmark,
        "actions": command_actions,
        "computer": command_computer,
        "worktrees": command_worktrees,
        "mcp": command_mcp,
        "tools": command_tools,
        "resources": command_resources,
        "prompts": command_prompts,
        "sandboxes": command_sandboxes,
        "agents": command_agents,
        "merge": command_merge,
        "memory": command_memory,
        "science": command_science,
        "workspace": command_workspace,
        "console": command_workspace,
        "studio": command_studio,
        "office": command_office,
        "desktop": command_desktop,
        "packaging": command_packaging,
        "plugins": command_plugins,
        "collab": command_collab,
        "collaboration": command_collab,
        "release": command_release,
        "capabilities": command_capabilities,
        "benchmark-lab": command_benchmark_lab,
    }
    return commands[args.command](args, terminal)


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    terminal = Terminal(use_color=not args.no_color, json_mode=args.json)
    try:
        return dispatch(args, terminal)
    except CLIError as exc:
        if args.json:
            print(canonical_pretty({"ok": False, "error_code": exc.code, "message": str(exc)}))
        else:
            print(terminal.error(f"{exc.code}: {exc}"), file=sys.stderr)
        if args.debug:
            traceback.print_exc()
        return exc.exit_code
    except KeyboardInterrupt:
        if args.json:
            print(canonical_pretty({"ok": False, "error_code": "interrupted"}))
        else:
            print(terminal.warning("Interrupted."), file=sys.stderr)
        return 130
    except Exception as exc:  # pragma: no cover - final user-facing guard
        code = getattr(exc, "code", "unexpected_error")
        if args.json:
            print(canonical_pretty({"ok": False, "error_code": code, "message": str(exc)}))
        else:
            print(terminal.error(f"{code}: {exc}"), file=sys.stderr)
        if args.debug:
            traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
