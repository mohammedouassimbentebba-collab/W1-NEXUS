"""Support utilities for the dependency-light W1 Nexus command-line interface."""

from __future__ import annotations

import json
import os
import platform
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from jsonschema import Draft202012Validator

from .orchestrator import CompiledWorkflow, GoalTaskCompiler, OrchestratorJournal
from .action_runtime import ActionPolicy, ActionRuntime
from .provider_connectors import ConnectorConfig, ProviderRegistry
from .plugin_system import PluginManager
from .credential_broker import CredentialBrokerStore, WorkspaceCredentialSecretResolver, credential_vault_status, workspace_credential_namespace
from .collaboration import CollaborationStore
from .session_store import SessionNotFoundError, SessionStore, _default_schema_directory
from .mcp_integration import DEFAULT_MCP_DOCUMENT, load_mcp_configs
from .validation import route_execution_resource
from .memory import MemoryStore
from .scientific import ScientificLab
from .model_access import (
    ModelAccessFabric,
    ModelAccessStore,
    PortfolioProviderAdapter,
    profile_from_mapping,
    portfolio_from_mapping,
)

WORKSPACE_VERSION = 13
DEFAULT_TRUSTED_RECORDER = {"principal_type": "runtime", "principal_id": "runtime-local-001"}


class CLIError(RuntimeError):
    """Stable user-facing CLI failure."""

    def __init__(self, code: str, message: str | None = None, *, exit_code: int = 2) -> None:
        self.code = code
        self.exit_code = exit_code
        super().__init__(message or code)


@dataclass(frozen=True)
class WorkspacePaths:
    root: Path
    state_dir: Path
    config: Path
    providers: Path
    context: Path
    mcp_servers: Path
    tool_registry_db: Path
    session_db: Path
    journal_db: Path
    conversation_db: Path
    memory_db: Path
    science_db: Path
    model_access_db: Path
    model_access_config: Path
    model_access_token: Path
    credential_broker_db: Path
    ai_connections_db: Path
    provider_certifications_db: Path
    capacity_router_db: Path
    intelligence_search_db: Path
    collaboration_db: Path
    artifact_studio_db: Path
    office_artifacts_db: Path
    desktop_shell_token: Path
    gateway_db: Path
    reports_dir: Path
    runs_dir: Path

    @classmethod
    def from_root(cls, root: str | Path) -> "WorkspacePaths":
        root_path = Path(root).expanduser().resolve()
        state = root_path / ".w1nexus"
        return cls(
            root=root_path,
            state_dir=state,
            config=state / "workspace.json",
            providers=state / "providers.json",
            context=state / "context.json",
            mcp_servers=state / "mcp-servers.json",
            tool_registry_db=state / "tool-registry.sqlite3",
            session_db=state / "session.sqlite3",
            journal_db=state / "orchestrator.sqlite3",
            conversation_db=state / "conversation.sqlite3",
            memory_db=state / "memory.sqlite3",
            science_db=state / "science.sqlite3",
            model_access_db=state / "model-access.sqlite3",
            model_access_config=state / "model-access.json",
            model_access_token=state / "model-access.token",
            credential_broker_db=state / "credential-broker.sqlite3",
            ai_connections_db=state / "ai-connections.sqlite3",
            provider_certifications_db=state / "provider-certifications.sqlite3",
            capacity_router_db=state / "capacity-router.sqlite3",
            intelligence_search_db=state / "intelligence-search.sqlite3",
            collaboration_db=state / "collaboration.sqlite3",
            artifact_studio_db=state / "artifact-studio.sqlite3",
            office_artifacts_db=state / "office-artifacts.sqlite3",
            desktop_shell_token=state / "desktop-shell.token",
            gateway_db=state / "gateway.sqlite3",
            reports_dir=state / "reports",
            runs_dir=state / "runs",
        )


def canonical_pretty(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def load_json(path: str | Path, *, required_object: bool = True) -> Any:
    selected = Path(path)
    try:
        value = json.loads(selected.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CLIError("file_not_found", f"File not found: {selected}") from exc
    except json.JSONDecodeError as exc:
        raise CLIError(
            "invalid_json",
            f"Invalid JSON in {selected} at line {exc.lineno}, column {exc.colno}.",
        ) from exc
    if required_object and not isinstance(value, dict):
        raise CLIError("json_object_required", f"Expected a JSON object in {selected}.")
    return value


def write_json(path: str | Path, value: Any, *, overwrite: bool = True) -> None:
    selected = Path(path)
    if selected.exists() and not overwrite:
        raise CLIError("file_exists", f"Refusing to overwrite {selected}.")
    selected.parent.mkdir(parents=True, exist_ok=True)
    temporary = selected.with_suffix(selected.suffix + ".tmp")
    temporary.write_text(canonical_pretty(value) + "\n", encoding="utf-8")
    temporary.replace(selected)


def initialize_workspace(root: str | Path, *, force: bool = False) -> WorkspacePaths:
    paths = WorkspacePaths.from_root(root)
    paths.state_dir.mkdir(parents=True, exist_ok=True)
    paths.reports_dir.mkdir(parents=True, exist_ok=True)
    paths.runs_dir.mkdir(parents=True, exist_ok=True)

    workspace = {
        "workspace_version": WORKSPACE_VERSION,
        "name": paths.root.name or "w1-workspace",
        "trusted_recorders": [DEFAULT_TRUSTED_RECORDER],
        "session_database": "session.sqlite3",
        "orchestrator_database": "orchestrator.sqlite3",
        "conversation_database": "conversation.sqlite3",
        "providers_file": "providers.json",
        "context_file": "context.json",
        "mcp_servers_file": "mcp-servers.json",
        "reports_directory": "reports",
        "scientific_database": "science.sqlite3",
        "model_access_database": "model-access.sqlite3",
        "model_access_config": "model-access.json",
        "credential_broker_database": "credential-broker.sqlite3",
        "ai_connections_database": "ai-connections.sqlite3",
        "provider_certifications_database": "provider-certifications.sqlite3",
        "capacity_router_database": "capacity-router.sqlite3",
        "intelligence_search_database": "intelligence-search.sqlite3",
        "intelligence_search": {
            "version": "1.0",
            "local_first": True,
            "w1_owned_server_required": False,
            "background_cloud_index": False,
            "on_demand": True,
            "sources": ["openrouter", "github", "huggingface"],
            "third_party_auto_routing": False,
            "credentials_cached": False
        },
        "adaptive_capacity": {
            "version": "1.1",
            "collaboration_first": True,
            "default_routing_mode": "balanced",
            "consumer_subscription_api_access_assumed": False,
            "third_party_free_auto_routing": False,
            "quality_floor_required": True,
            "provider_diversity_preferred_for_review": True,
            "automatic_free_discovery": True,
            "loopback_discovery": True,
            "third_party_discovery_requires_opt_in": True
        },
        "ai_connections": {
            "version": "1.0",
            "local_first": True,
            "multi_provider": True,
            "subscription_does_not_imply_api_access": True,
            "raw_secrets_in_metadata_store": False,
            "team_modes": ["solo", "fallback", "parallel", "verify", "challenge"]
        },
        "credential_broker": {
            "local_first": True,
            "native_vault_required": True,
            "insecure_file_fallback": False,
            "allow_cookie_or_session_import": False
        },
        "collaboration_database": "collaboration.sqlite3",
        "collaboration": {
            "protocol_version": "1.0",
            "local_first": True,
            "self_hosted_first": True,
            "w1_owned_cloud_required": False,
            "non_loopback_tls_required": True,
            "silent_conflict_overwrite": False
        },
        "artifact_studio_database": "artifact-studio.sqlite3",
        "office_artifacts_database": "office-artifacts.sqlite3",
        "desktop_shell": {"local_first": True, "w1_owned_server_required": False},
        "native_packaging": {
            "app_id": "com.w1.nexus",
            "url_scheme": "w1",
            "project_extension": ".w1nexus",
            "service_loopback_only": True,
            "native_signature_required_for_updates": True
        },
        "defaults": {
            "max_attempts_per_resource": 2,
            "event_output": "text",
        },
        "runtime_identity": {
            "actor": {"principal_type": "runtime", "principal_id": "runtime-local-001"},
            "recorded_by": {"principal_type": "runtime", "principal_id": "runtime-local-001"},
            "authorized_by": {
                "entity_type": "role_assignment",
                "entity_id": "replace-with-orchestrator-role-assignment-id",
                "entity_version": 1
            }
        },
        "safety": {
            "allow_insecure_remote_endpoints": False,
            "require_pinned_model_ids": True,
            "store_api_keys": False,
        },
        "science": {
            "principal": {"principal_type": "human", "principal_id": "local-researcher"},
            "default_sandbox_profile": "trusted-local",
            "require_preregistration": True,
            "automatic_finding_persistence": False
        },
        "conversation_context": {
            "version": "1.0",
            "full_history_local": True,
            "default_context_token_budget": 2400,
            "recent_exact_turns_preferred": True,
            "long_term_memory_context_enabled": True,
            "automatic_model_inference_to_long_term_memory": False
        },
        "memory": {
            "namespace_id": "w1-local",
            "project_id": "default-project",
            "principal": {
                "principal_type": "human",
                "principal_id": "local-owner",
                "groups": ["workspace-team"]
            },
            "default_token_budget": 1200,
            "include_global_by_default": False,
            "automatic_model_inference_persistence": False
        },
        "action_runtime": {
            "policy": {
                "allowed_commands": ["git", "python", "python3", "pytest"],
                "denied_commands": ["bash", "sh", "zsh", "cmd", "cmd.exe", "powershell", "pwsh", "curl", "wget", "ssh", "scp", "sftp", "ftp", "telnet", "nc", "ncat", "netcat"],
                "denied_path_globs": [".git", ".git/**", ".w1nexus", ".w1nexus/**"],
                "max_command_seconds": 60,
                "max_output_bytes": 1000000,
                "max_backup_bytes": 25000000,
                "require_approval_for_writes": False,
                "require_approval_for_delete": True,
                "require_approval_for_commands": True,
                "require_approval_for_worktrees": True,
                "require_approval_for_computer_input": True,
                "max_computer_clicks": 3,
                "max_computer_scroll_delta": 1200,
                "allow_symlinks": False,
                "allow_network": False
            }
        },
    }
    providers = {
        "providers": [
            {
                "type": "openai_responses",
                "resource_id": "openai-primary",
                "model": "replace-with-pinned-openai-model-id",
                "api_key_reference": "OPENAI_API_KEY",
                "accounting_meter": "tokens",
            },
            {
                "type": "anthropic_messages",
                "resource_id": "anthropic-reviewer",
                "model": "replace-with-pinned-claude-model-id",
                "api_key_reference": "ANTHROPIC_API_KEY",
                "accounting_meter": "tokens",
            },
            {
                "type": "gemini_interactions",
                "resource_id": "gemini-executor",
                "model": "replace-with-pinned-gemini-model-id",
                "api_key_reference": "GEMINI_API_KEY",
                "accounting_meter": "tokens",
            },
            {
                "type": "openai_compatible",
                "resource_id": "ollama-local",
                "model": "replace-with-local-model-id",
                "endpoint": "http://127.0.0.1:11434/v1/chat/completions",
                "allow_insecure_loopback": True,
                "accounting_meter": "requests",
            },
        ]
    }
    model_access = {
        "local_first": True,
        "requires_w1_owned_server": False,
        "profiles": [
            {
                "model_id": "local-preferred",
                "display_name": "Local preferred model",
                "provider_id": "local",
                "connector_type": "openai_compatible",
                "connector_resource_id": "local-preferred-resource",
                "model_name": "replace-with-local-model-id",
                "access_mode": "local_endpoint",
                "privacy_mode": "local",
                "capabilities": {"executor": 0.8, "execution": 0.8},
                "roles": ["executor"],
                "endpoint": "http://127.0.0.1:11434/v1/chat/completions",
                "enabled": False
            },
            {
                "model_id": "cloud-reviewer",
                "display_name": "User-owned cloud reviewer",
                "provider_id": "user-selected-provider",
                "connector_type": "openai_responses",
                "connector_resource_id": "cloud-reviewer-resource",
                "model_name": "replace-with-pinned-model-id",
                "access_mode": "byok_api",
                "privacy_mode": "provider_cloud",
                "credential_reference": "USER_MODEL_API_KEY",
                "capabilities": {"reviewer": 0.9, "review": 0.9},
                "roles": ["reviewer"],
                "enabled": False
            }
        ],
        "portfolios": [
            {
                "portfolio_id": "preferred-team",
                "display_name": "User preferred multi-model team",
                "strategy": "parallel_collect",
                "members": [
                    {"model_id": "local-preferred", "role": "producer", "priority": 10},
                    {"model_id": "cloud-reviewer", "role": "reviewer", "priority": 20}
                ],
                "max_parallel": 2,
                "minimum_successful_producers": 1
            }
        ],
        "control_api": {"host": "127.0.0.1", "port": 8871}
    }
    context = {
        "example-field": "Replace this file with the minimum context required by the task."
    }

    for path, value in (
        (paths.config, workspace),
        (paths.providers, providers),
        (paths.context, context),
        (paths.model_access_config, model_access),
        (paths.mcp_servers, DEFAULT_MCP_DOCUMENT),
    ):
        if force or not path.exists():
            write_json(path, value)

    gitignore = paths.state_dir / ".gitignore"
    if force or not gitignore.exists():
        gitignore.write_text(
            "session.sqlite3*\norchestrator.sqlite3*\nconversation.sqlite3*\nmemory.sqlite3*\nmodel-access.sqlite3*\nmodel-access.token\ncredential-broker.sqlite3*\nai-connections.sqlite3*\nprovider-certifications.sqlite3*\ncapacity-router.sqlite3*\ncollaboration.sqlite3*\nartifact-studio.sqlite3*\noffice-artifacts.sqlite3*\naction-runtime.sqlite3*\naction-secret.key\naction-backups/\nworktrees/\nreports/\nruns/\ncontext.json\nworkspace-console.token\n",
            encoding="utf-8",
        )
    return paths


def require_workspace(root: str | Path) -> WorkspacePaths:
    paths = WorkspacePaths.from_root(root)
    if not paths.config.is_file():
        raise CLIError(
            "workspace_not_initialized",
            f"No W1 workspace found at {paths.root}. Run `w1 init {paths.root}` first.",
        )
    config = load_json(paths.config)
    version = config.get("workspace_version")
    if version not in set(range(1, WORKSPACE_VERSION + 1)):
        raise CLIError("workspace_version_unsupported", "Unsupported workspace version.")
    return paths


def trusted_recorders(paths: WorkspacePaths) -> set[tuple[str, str]]:
    config = load_json(paths.config)
    result: set[tuple[str, str]] = set()
    for item in config.get("trusted_recorders", []):
        if not isinstance(item, dict):
            continue
        principal_type = item.get("principal_type")
        principal_id = item.get("principal_id")
        if isinstance(principal_type, str) and isinstance(principal_id, str):
            result.add((principal_type, principal_id))
    if not result:
        raise CLIError("trusted_recorder_missing", "At least one trusted recorder is required.")
    return result


def action_policy(paths: WorkspacePaths) -> ActionPolicy:
    config = load_json(paths.config)
    runtime = config.get("action_runtime", {})
    if not isinstance(runtime, dict):
        raise CLIError("action_runtime_configuration_invalid", "action_runtime must be an object.")
    policy = runtime.get("policy", {})
    if not isinstance(policy, dict):
        raise CLIError("action_policy_configuration_invalid", "action_runtime.policy must be an object.")
    try:
        return ActionPolicy.from_mapping(policy)
    except (TypeError, ValueError) as exc:
        raise CLIError("action_policy_configuration_invalid", str(exc)) from exc


def provider_entries(paths: WorkspacePaths) -> list[dict[str, Any]]:
    document = load_json(paths.providers)
    entries = document.get("providers")
    if not isinstance(entries, list):
        raise CLIError("providers_array_required", "providers.json must contain a providers array.")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(entries):
        if not isinstance(item, dict):
            raise CLIError("provider_entry_invalid", f"Provider entry {index} is not an object.")
        resource_id = item.get("resource_id")
        provider_type = item.get("type")
        if not isinstance(resource_id, str) or not resource_id:
            raise CLIError("provider_resource_id_missing", f"Provider entry {index} has no resource_id.")
        if resource_id in seen:
            raise CLIError("provider_resource_id_duplicate", f"Duplicate resource_id: {resource_id}")
        if not isinstance(provider_type, str):
            raise CLIError("provider_type_missing", f"Provider {resource_id} has no type.")
        seen.add(resource_id)
        result.append(dict(item))
    return result


def connector_config_from_entry(entry: Mapping[str, Any]) -> ConnectorConfig:
    allowed = {
        "resource_id",
        "model",
        "api_key_reference",
        "endpoint",
        "timeout_seconds",
        "max_output_tokens",
        "max_input_bytes",
        "accounting_meter",
        "supports_idempotency",
        "allow_insecure_loopback",
        "extra_headers",
    }
    unexpected = set(entry) - allowed - {"type", "enabled", "description"}
    if unexpected:
        raise CLIError(
            "provider_unknown_fields",
            f"Unknown provider fields for {entry.get('resource_id')}: {', '.join(sorted(unexpected))}",
        )
    payload = {key: entry[key] for key in allowed if key in entry}
    try:
        return ConnectorConfig(**payload)
    except (TypeError, ValueError) as exc:
        raise CLIError("provider_configuration_invalid", str(exc)) from exc


def build_providers(paths: WorkspacePaths) -> dict[str, Any]:
    registry = ProviderRegistry(secret_resolver=WorkspaceCredentialSecretResolver(paths.credential_broker_db))
    providers: dict[str, Any] = {}
    for entry in provider_entries(paths):
        if entry.get("enabled", True) is False:
            continue
        config = connector_config_from_entry(entry)
        try:
            providers[config.resource_id] = registry.build(str(entry["type"]), config)
        except Exception as exc:
            code = getattr(exc, "code", "provider_configuration_invalid")
            raise CLIError(code, f"Provider {config.resource_id}: {exc}") from exc
    # Model Access Fabric profiles and portfolios are optional additions to the
    # legacy providers.json connector list. They require no W1-owned service.
    access_store = model_access_store(paths)
    access_fabric = ModelAccessFabric(access_store, provider_registry=registry)
    for profile in access_store.list_profiles(enabled_only=True):
        if profile.connector_resource_id in providers:
            raise CLIError("provider_resource_id_duplicate", profile.connector_resource_id)
        providers[profile.connector_resource_id] = access_fabric.adapter_for(profile)
    for portfolio in access_store.list_portfolios():
        resource_id = f"portfolio-{portfolio.portfolio_id}"
        if resource_id in providers:
            raise CLIError("provider_resource_id_duplicate", resource_id)
        try:
            providers[resource_id] = PortfolioProviderAdapter(
                access_fabric, portfolio.portfolio_id, resource_id=resource_id
            )
        except Exception as exc:
            # A portfolio made exclusively from disabled/incomplete profiles is
            # retained for editing but is not an enabled runtime resource.
            if any(
                access_store.get_profile(member.model_id).enabled
                for member in portfolio.members
                if member.model_id in {p.model_id for p in access_store.list_profiles()}
            ):
                raise CLIError(getattr(exc, "code", "model_portfolio_invalid"), str(exc)) from exc
    if not providers:
        raise CLIError("no_enabled_providers", "No enabled providers are configured.")
    return providers


def provider_inventory(paths: WorkspacePaths) -> list[dict[str, Any]]:
    inventory: list[dict[str, Any]] = []
    broker_store: CredentialBrokerStore | None = None
    try:
        for entry in provider_entries(paths):
            secret = entry.get("api_key_reference")
            model = entry.get("model")
            pinned = isinstance(model, str) and model and "replace-with" not in model and "latest" not in model
            secret_source = "none_or_local"
            secret_present: bool | None = True
            if isinstance(secret, str) and secret.startswith(("w1-account:", "w1-credential:")):
                secret_source = "w1_credential_broker"
                if broker_store is None:
                    broker_store = CredentialBrokerStore(paths.credential_broker_db)
                try:
                    if secret.startswith("w1-account:"):
                        broker_store.get_account(secret.split(":", 1)[1])
                    else:
                        broker_store.credential_metadata(secret.split(":", 1)[1])
                    secret_present = True
                except Exception:
                    secret_present = False
            elif secret:
                secret_source = "environment"
                secret_present = bool(os.environ.get(str(secret)))
            inventory.append(
                {
                    "resource_id": entry.get("resource_id"),
                    "type": entry.get("type"),
                    "model": model,
                    "enabled": entry.get("enabled", True),
                    "endpoint": entry.get("endpoint") or "provider default",
                    "secret_reference": secret or "none/local",
                    "secret_source": secret_source,
                    "secret_present": secret_present,
                    "model_pinned": pinned,
                }
            )
    finally:
        if broker_store is not None:
            broker_store.close()
    return inventory



def model_access_store(paths: WorkspacePaths, *, sync_config: bool = True) -> ModelAccessStore:
    store = ModelAccessStore(paths.model_access_db)
    if sync_config and paths.model_access_config.is_file():
        document = load_json(paths.model_access_config)
        profiles = document.get("profiles", [])
        portfolios = document.get("portfolios", [])
        if not isinstance(profiles, list) or not isinstance(portfolios, list):
            raise CLIError("model_access_config_invalid", "profiles and portfolios must be arrays.")
        try:
            for payload in profiles:
                if not isinstance(payload, dict):
                    raise TypeError("profile must be an object")
                store.put_profile(profile_from_mapping(payload))
            for payload in portfolios:
                if not isinstance(payload, dict):
                    raise TypeError("portfolio must be an object")
                store.put_portfolio(portfolio_from_mapping(payload))
        except Exception as exc:
            code = getattr(exc, "code", "model_access_config_invalid")
            raise CLIError(str(code), str(exc)) from exc
    return store


def model_access_fabric(paths: WorkspacePaths, *, store: ModelAccessStore | None = None) -> ModelAccessFabric:
    selected_store = store or model_access_store(paths)
    registry = ProviderRegistry(secret_resolver=WorkspaceCredentialSecretResolver(paths.credential_broker_db))
    from .model_access import PythonPluginAdapterFactory
    plugin_factory = PythonPluginAdapterFactory(PluginManager(paths.root))
    return ModelAccessFabric(selected_store, provider_registry=registry, plugin_factory=plugin_factory)


def model_access_inventory(paths: WorkspacePaths) -> dict[str, Any]:
    store = model_access_store(paths)
    return {
        "local_first": True,
        "requires_w1_owned_server": False,
        "models": [profile.redacted_dict() for profile in store.list_profiles()],
        "portfolios": [asdict(portfolio) for portfolio in store.list_portfolios()],
        "invocations": store.invocation_summary(),
    }

def compile_plan(
    goal: Mapping[str, Any],
    team: Mapping[str, Any],
    resources: Mapping[str, Any],
    *,
    context_fields_by_deliverable: Mapping[str, Iterable[str]] | None = None,
    domains: Iterable[str] = (),
) -> dict[str, Any]:
    compiler = GoalTaskCompiler(
        context_fields_by_deliverable=context_fields_by_deliverable or {},
        domains=tuple(domains),
    )
    workflow = compiler.compile(goal, team)
    tasks: list[dict[str, Any]] = []
    estimated_total = 0
    for task in workflow.tasks:
        routing = route_execution_resource(
            dict(resources),
            role=task.role,
            phase=task.phase,
            risk_level=workflow.risk_level,
            task_complexity=workflow.task_complexity,
            domains=set(task.domains),
            estimated_units=task.estimated_units,
        )
        estimated_total += task.estimated_units
        tasks.append(
            {
                "task_id": task.task_id,
                "title": task.title,
                "phase": task.phase,
                "role": task.role,
                "depends_on": list(task.depends_on),
                "expected_output_type": task.expected_output_type,
                "required_context_fields": list(task.required_context_fields),
                "estimated_units": task.estimated_units,
                "routing": asdict(routing),
            }
        )
    return {
        "risk_level": workflow.risk_level,
        "error_cost": workflow.error_cost,
        "task_complexity": workflow.task_complexity,
        "task_count": len(workflow.tasks),
        "estimated_units": estimated_total,
        "tasks": tasks,
    }


def workflow_from_json(value: Mapping[str, Any]) -> CompiledWorkflow:
    from .orchestrator import CompiledTask

    tasks = []
    for item in value.get("tasks", []):
        tasks.append(
            CompiledTask(
                task_id=item["task_id"],
                title=item["title"],
                phase=item["phase"],
                role=item["role"],
                expected_output_type=item["expected_output_type"],
                depends_on=tuple(item.get("depends_on", [])),
                deliverable_id=item.get("deliverable_id"),
                criterion_id=item.get("criterion_id"),
                required_context_fields=tuple(item.get("required_context_fields", [])),
                estimated_units=int(item.get("estimated_units", 1)),
                domains=tuple(item.get("domains", [])),
            )
        )
    return CompiledWorkflow(
        tasks=tuple(tasks),
        risk_level=value["risk_level"],
        error_cost=value["error_cost"],
        task_complexity=value["task_complexity"],
    )


def run_snapshot(journal: OrchestratorJournal, run_id: str) -> dict[str, Any]:
    run = journal.load_run(run_id)
    if run is None:
        raise CLIError("run_not_found", f"Run not found: {run_id}")
    tasks = []
    for row in journal.task_rows(run_id):
        tasks.append(
            {
                "task_id": row["task_id"],
                "status": row["status"],
                "attempts": int(row["attempts"]),
                "resource_id": row["resource_id"],
                "error_code": row["error_code"],
                "requires_extra_review": bool(row["requires_extra_review"]),
                "output": json.loads(row["output_json"]) if row["output_json"] else None,
            }
        )
    return {
        "run_id": run_id,
        "session_id": run["session_id"],
        "status": run["status"],
        "created_at": run["created_at"],
        "updated_at": run["updated_at"],
        "last_error_code": run["last_error_code"],
        "result": json.loads(run["result_json"]) if run["result_json"] else None,
        "resource_plan": json.loads(run["resource_plan_json"]),
        "tasks": tasks,
        "quota_observations": journal.quota_observations(run_id),
        "started_calls": journal.count_started_calls(run_id),
    }


def session_audit(store: SessionStore, session_id: str) -> dict[str, Any]:
    try:
        summary = store.get_summary(session_id)
    except SessionNotFoundError as exc:
        raise CLIError("session_not_found", f"Session not found: {session_id}") from exc
    integrity = store.verify_integrity(session_id)
    events = store.get_events(session_id)
    entity_counts: dict[str, int] = {}
    message_types: dict[str, int] = {}
    for event in events:
        message_type = str(event.get("type", "unknown"))
        message_types[message_type] = message_types.get(message_type, 0) + 1
        entity = event.get("entity")
        if isinstance(entity, dict):
            entity_type = str(entity.get("entity_type", "unknown"))
            entity_counts[entity_type] = entity_counts.get(entity_type, 0) + 1
    return {
        "session": asdict(summary),
        "integrity": {
            "valid": integrity.valid,
            "errors": list(integrity.errors),
            "last_sequence": integrity.last_sequence,
            "last_event_hash": integrity.last_event_hash,
        },
        "event_type_counts": dict(sorted(message_types.items())),
        "entity_type_counts": dict(sorted(entity_counts.items())),
        "active_protocol_effects": store.get_active_protocol_effects(session_id),
        "events": events,
    }


def audit_markdown(audit: Mapping[str, Any]) -> str:
    session = audit["session"]
    integrity = audit["integrity"]
    lines = [
        f"# W1-CIP Session Audit: `{session['session_id']}`",
        "",
        "## Summary",
        "",
        f"- Events: **{session['event_count']}**",
        f"- Last sequence: **{session['last_sequence']}**",
        f"- Last recorded at: `{session['last_recorded_at']}`",
        f"- Integrity: **{'valid' if integrity['valid'] else 'INVALID'}**",
        f"- Last event hash: `{session['last_event_hash']}`",
        "",
        "## Entity types",
        "",
    ]
    for name, count in audit.get("entity_type_counts", {}).items():
        lines.append(f"- `{name}`: {count}")
    lines.extend(["", "## Message types", ""])
    for name, count in audit.get("event_type_counts", {}).items():
        lines.append(f"- `{name}`: {count}")
    effects = audit.get("active_protocol_effects", [])
    lines.extend(["", "## Active protocol effects", ""])
    if effects:
        for effect in effects:
            entity = effect.get("entity", {})
            lines.append(
                f"- `{entity.get('entity_type', 'unknown')}:{entity.get('entity_id', 'unknown')}`"
            )
    else:
        lines.append("- None")
    lines.append("")
    return "\n".join(lines)


def schema_diagnostics(schema_dir: Path) -> dict[str, Any]:
    errors: list[str] = []
    count = 0
    if not schema_dir.is_dir():
        return {"valid": False, "count": 0, "errors": ["schema_directory_missing"]}
    for path in sorted(schema_dir.glob("*.schema.json")):
        count += 1
        try:
            Draft202012Validator.check_schema(load_json(path))
        except Exception as exc:
            errors.append(f"{path.name}: {exc}")
    return {"valid": not errors, "count": count, "errors": errors}


def doctor(paths: WorkspacePaths, *, deep: bool = False) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, details: Any = None, severity: str = "error") -> None:
        checks.append({"name": name, "ok": ok, "details": details, "severity": severity})

    add("python_version", sys.version_info >= (3, 11), platform.python_version())
    try:
        config = load_json(paths.config)
        version = config.get("workspace_version")
        add("workspace_config", isinstance(version, int) and 1 <= version <= WORKSPACE_VERSION, config)
        add(
            "workspace_current_version",
            version == WORKSPACE_VERSION,
            {"found": version, "current": WORKSPACE_VERSION},
            severity="warning",
        )
        identity = config.get("runtime_identity", {})
        authorized_by = identity.get("authorized_by", {}) if isinstance(identity, dict) else {}
        authority_id = authorized_by.get("entity_id") if isinstance(authorized_by, dict) else None
        authority_ok = isinstance(authority_id, str) and not authority_id.startswith("replace-with-")
        add(
            "runtime_authority_configured",
            authority_ok,
            {"authorized_by": authorized_by},
            severity="warning",
        )
    except CLIError as exc:
        add("workspace_config", False, str(exc))

    vault_status = credential_vault_status(workspace_credential_namespace(paths.root))
    add(
        "credential_vault",
        bool(vault_status.get("available")),
        vault_status,
        severity="warning",
    )

    try:
        with CollaborationStore(paths.collaboration_db) as collaboration_store:
            team_count = len(collaboration_store.list_teams())
            add(
                "collaboration_fabric",
                True,
                {
                    "database": str(paths.collaboration_db),
                    "team_count": team_count,
                    "self_hosted_first": True,
                    "w1_owned_cloud_required": False,
                    "non_loopback_tls_required": True,
                },
            )
    except Exception as exc:
        add("collaboration_fabric", False, str(exc))

    try:
        access_inventory = model_access_inventory(paths)
        add(
            "model_access_fabric",
            True,
            {
                "model_count": len(access_inventory["models"]),
                "portfolio_count": len(access_inventory["portfolios"]),
                "local_first": access_inventory["local_first"],
                "requires_w1_owned_server": access_inventory["requires_w1_owned_server"],
            },
        )
    except Exception as exc:
        add("model_access_fabric", False, str(exc))

    try:
        inventory = provider_inventory(paths)
        add("provider_configuration", True, {"count": len(inventory)})
        missing = [item["resource_id"] for item in inventory if not item["secret_present"]]
        add("provider_secrets_present", not missing, {"missing": missing}, severity="warning")
        unpinned = [item["resource_id"] for item in inventory if not item["model_pinned"]]
        add("provider_models_pinned", not unpinned, {"unpinned": unpinned}, severity="warning")
        if deep:
            build_providers(paths)
            add("provider_connectors_construct", True)
    except Exception as exc:
        add("provider_configuration", False, str(exc))

    schema_dir = _default_schema_directory()
    schemas = schema_diagnostics(schema_dir) if schema_dir is not None else {
        "valid": False,
        "count": 0,
        "errors": ["schema_directory_missing"],
    }
    add("schemas", schemas["valid"], schemas)

    try:
        recorders = trusted_recorders(paths)
        add("trusted_recorders", bool(recorders), sorted(recorders))
    except CLIError as exc:
        add("trusted_recorders", False, str(exc))
        recorders = set()

    try:
        mcp_configs = load_mcp_configs(paths.mcp_servers)
        add(
            "mcp_configuration",
            True,
            {
                "protocol_version": "2025-11-25",
                "server_count": len(mcp_configs),
                "enabled_count": sum(1 for item in mcp_configs if item.enabled),
                "remote_secret_values_stored": False,
            },
        )
    except Exception as exc:
        add("mcp_configuration", False, str(exc))

    try:
        policy = action_policy(paths)
        with ActionRuntime(paths.root, state_dir=paths.state_dir, policy=policy) as runtime:
            add(
                "action_runtime",
                True,
                {
                    "database": str(runtime.db_path),
                    "network_policy": "allowed" if policy.allow_network else "denied_best_effort",
                    "allowed_commands": list(policy.allowed_commands),
                    "approval_for_commands": policy.require_approval_for_commands,
                    "approval_for_delete": policy.require_approval_for_delete,
                },
            )
    except Exception as exc:
        add("action_runtime", False, str(exc))

    try:
        with MemoryStore(paths.memory_db) as memory_store:
            memory_report = memory_store.verify_integrity()
            add(
                "memory_fabric",
                True,
                {
                    "database": str(paths.memory_db),
                    "memory_count": memory_report["memory_count"],
                    "revision_count": memory_report["revision_count"],
                    "fts_enabled": memory_report["fts_enabled"],
                },
            )
    except Exception as exc:
        add("memory_fabric", False, str(exc))

    try:
        with ScientificLab(paths.science_db, workspace_root=paths.root) as scientific_lab:
            science_report = scientific_lab.verify()
            add(
                "scientific_lab",
                science_report["ok"],
                {
                    "database": str(paths.science_db),
                    "study_count": science_report["study_count"],
                    "event_count": science_report["event_count"],
                    "errors": science_report["errors"],
                },
            )
    except Exception as exc:
        add("scientific_lab", False, str(exc))

    if paths.session_db.exists() and recorders:
        try:
            with SessionStore(paths.session_db, trusted_recorders=recorders) as store:
                sessions = store.list_sessions()
                invalid = []
                for summary in sessions:
                    report = store.verify_integrity(summary.session_id)
                    if not report.valid:
                        invalid.append({"session_id": summary.session_id, "errors": report.errors})
                add("session_store_integrity", not invalid, {"sessions": len(sessions), "invalid": invalid})
        except Exception as exc:
            add("session_store_integrity", False, str(exc))
    else:
        add("session_store_integrity", True, {"sessions": 0, "note": "not_created_yet"})

    fatal_failures = [c for c in checks if not c["ok"] and c["severity"] == "error"]
    warnings = [c for c in checks if not c["ok"] and c["severity"] == "warning"]
    return {
        "ok": not fatal_failures,
        "warning_count": len(warnings),
        "error_count": len(fatal_failures),
        "checks": checks,
    }


def table(headers: Iterable[str], rows: Iterable[Iterable[Any]]) -> str:
    header_list = [str(item) for item in headers]
    row_list = [["" if value is None else str(value) for value in row] for row in rows]
    widths = [len(value) for value in header_list]
    for row in row_list:
        if len(row) != len(widths):
            raise ValueError("table_row_width_mismatch")
        for index, value in enumerate(row):
            widths[index] = max(widths[index], len(value))
    render = lambda row: "  ".join(value.ljust(widths[index]) for index, value in enumerate(row))
    separator = "  ".join("-" * width for width in widths)
    return "\n".join([render(header_list), separator, *[render(row) for row in row_list]])
