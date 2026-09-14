"""Local-first desktop shell and Artifact Studio HTTP surface.

The shell can be embedded in an optional native ``pywebview`` window when that
package is installed.  The zero-dependency fallback is an installable local web
application opened in the user's browser.  Both modes use the same loopback-only
server and never require a W1-owned service.
"""

from __future__ import annotations

import html
import importlib.resources
import json
import os
import secrets
import threading
import urllib.parse
import webbrowser
from dataclasses import asdict, dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping, Sequence

from .action_runtime import ActionApproval, ActionApprovalRequired, ActionRequest, ActionRuntime
from .artifact_studio import (
    ArtifactConflict,
    ArtifactNotFound,
    ArtifactPathDenied,
    ArtifactReviewRequired,
    ArtifactStudioStore,
    WorkspaceFileService,
    canonical_json,
    utc_now,
)
from .cli_support import WorkspacePaths, action_policy, load_json
from .model_access import ModelAccessStore
from .ai_connections import AIConnectionService, AIConnectionStore, team_from_mapping, provider_catalog
from .capacity_router import CapacityObservation, CapacityStore, AdaptiveCapacityRouter, RoutingPolicy, run_capacity_router_benchmark
from .orchestrator import CompiledTask
from .credential_broker import CredentialBroker, CredentialBrokerStore, CredentialVaultUnavailable, create_native_credential_vault, workspace_credential_namespace
from .universal_artifacts import (
    ArtifactExportUnavailable,
    ArtifactIndependentReviewRequired,
    ArtifactVersionConflict,
    UniversalArtifactError,
    UniversalArtifactStore,
    validate_artifact_model,
)
from .workspace_console import LOOPBACK_HOSTS, WorkspaceSnapshotService
from .conversation_context import ConversationStore
from .free_intelligence import FreeIntelligenceDiscovery, discovery_catalog
from .intelligence_search import IntelligenceSearchEngine, IntelligenceSearchStore, search_catalog
from .intelligence_activation import IntelligenceActivationService, IntelligenceActivationStore
from .memory import MemoryPrincipal, MemoryStore

DESKTOP_VERSION = "0.1.0-dev50"


def _asset_bytes(name: str) -> bytes:
    return importlib.resources.files("w1cip.desktop_assets").joinpath(name).read_bytes()


@dataclass(frozen=True)
class DesktopSettings:
    host: str = "127.0.0.1"
    port: int = 8766
    allow_operations: bool = False
    open_browser: bool = True

    def validate(self) -> None:
        if self.host not in LOOPBACK_HOSTS:
            raise ValueError("desktop_shell_loopback_only")
        if not 0 <= self.port <= 65535:
            raise ValueError("desktop_shell_invalid_port")


class DesktopShellApplication:
    def __init__(self, paths: WorkspacePaths, settings: DesktopSettings) -> None:
        settings.validate()
        self.paths = paths
        self.settings = settings
        self.token_path = paths.state_dir / "desktop-shell.token"
        self.token = self._load_or_create_token()
        self.files = WorkspaceFileService(paths.root)
        self.artifacts = ArtifactStudioStore(paths.state_dir / "artifact-studio.sqlite3", paths.root)
        self.office = UniversalArtifactStore(paths.office_artifacts_db, paths.root)
        self.conversations = ConversationStore(paths.conversation_db)
        self.snapshot_service = WorkspaceSnapshotService(paths)
        self._operation_lock = threading.RLock()

    def close(self) -> None:
        with self._operation_lock:
            if getattr(self, "_closed", False):
                return
            self._closed = True
            for resource in (self.artifacts, self.office, self.conversations):
                try:
                    resource.close()
                except Exception:
                    pass

    def _load_or_create_token(self) -> str:
        if self.token_path.is_file():
            value = self.token_path.read_text(encoding="utf-8").strip()
            if len(value) >= 32:
                return value
        value = secrets.token_urlsafe(32)
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.token_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, (value + "\n").encode())
        finally:
            os.close(fd)
        return value

    def bootstrap(self) -> dict[str, Any]:
        return {
            "token": self.token,
            "allowOperations": self.settings.allow_operations,
            "workspaceName": self.paths.root.name,
            "desktopVersion": DESKTOP_VERSION,
            "shellBackend": detect_desktop_backend(),
            "localFirst": True,
            "w1OwnedServerRequired": False,
        }

    def shell_state(self) -> dict[str, Any]:
        snapshot = self.snapshot_service.snapshot()
        model_store = ModelAccessStore(self.paths.model_access_db)
        models = [item.redacted_dict() for item in model_store.list_profiles()]
        portfolios = [asdict(item) for item in model_store.list_portfolios()]
        ai_inventory = AIConnectionService(
            AIConnectionStore(self.paths.ai_connections_db), model_store
        ).inventory()
        try:
            with CredentialBrokerStore(self.paths.credential_broker_db) as credential_store:
                ai_inventory["oauth_accounts"] = [item.redacted_dict() for item in credential_store.list_accounts()]
        except Exception:
            ai_inventory["oauth_accounts"] = []
        capacity_observations = [item.as_dict() for item in CapacityStore(self.paths.capacity_router_db).list()]
        conversations = self.conversations.list_conversations(limit=50)
        intelligence_candidates = [item.as_dict() for item in IntelligenceSearchStore(self.paths.intelligence_search_db).list(limit=50)]
        intelligence_activation_store = IntelligenceActivationStore(self.paths.intelligence_search_db)
        intelligence_activation_records = [item.as_dict() for item in intelligence_activation_store.list(limit=100)]
        payload = {
            "desktop": {
                "version": DESKTOP_VERSION,
                "allow_operations": self.settings.allow_operations,
                "backend": detect_desktop_backend(),
                "local_first": True,
                "w1_owned_server_required": False,
            },
            "workspace": snapshot["workspace"],
            "metrics": snapshot["metrics"],
            "models": models,
            "portfolios": portfolios,
            "ai_connections": ai_inventory,
            "adaptive_capacity": {
                "observations": capacity_observations,
                "count": len(capacity_observations),
                "collaboration_first": True,
                "consumer_subscription_api_access_assumed": False,
                "third_party_free_requires_explicit_opt_in": True,
                "free_discovery_catalog": discovery_catalog(),
            },
            "intelligence_search": {
                "catalog": search_catalog(),
                "candidates": intelligence_candidates,
                "count": len(intelligence_candidates),
                "activations": intelligence_activation_records,
                "activation_count": len(intelligence_activation_records),
                "activation_audit_chain_valid": intelligence_activation_store.verify_audit(),
                "local_first": True,
                "w1_owned_server_required": False,
            },
            "conversation_context": {
                "conversations": conversations,
                "count": len(conversations),
                "full_history_local": True,
                "automatic_model_inference_to_long_term_memory": False,
            },
            "artifacts": self.artifacts.list_artifacts(),
            "office_artifacts": self.office.list_artifacts(),
            "git": snapshot.get("git", {}),
        }
        return payload

    def get(self, route: str, query: Mapping[str, list[str]]) -> Any:
        if route == "/api/v1/state":
            return self.shell_state()
        if route == "/api/v1/tree":
            return self.files.tree(
                query.get("path", [""])[0],
                max_depth=int(query.get("depth", ["5"])[0]),
                include_hidden=query.get("hidden", ["false"])[0].lower() == "true",
            )
        if route == "/api/v1/file":
            return self.files.read(self._required_query(query, "path")).as_dict()
        if route == "/api/v1/preview":
            return self.files.preview(self._required_query(query, "path"))
        if route == "/api/v1/git/diff":
            return self.files.git_diff(query.get("path", [None])[0])
        if route == "/api/v1/artifacts":
            return {"artifacts": self.artifacts.list_artifacts()}
        if route == "/api/v1/artifact":
            artifact_id = self._required_query(query, "artifact_id")
            return self.artifacts.history(artifact_id)
        if route == "/api/v1/office/artifacts":
            return {"artifacts": self.office.list_artifacts()}
        if route == "/api/v1/office/artifact":
            return self.office.history(self._required_query(query, "artifact_id"))
        if route == "/api/v1/ai/catalog":
            return {"providers": provider_catalog()}
        if route == "/api/v1/ai/capacity":
            observations = [item.as_dict() for item in CapacityStore(self.paths.capacity_router_db).list()]
            return {
                "observations": observations,
                "count": len(observations),
                "benchmark": run_capacity_router_benchmark(),
                "consumer_subscription_api_access_assumed": False,
            }
        if route == "/api/v1/ai/state":
            inventory = AIConnectionService(
                AIConnectionStore(self.paths.ai_connections_db),
                ModelAccessStore(self.paths.model_access_db),
            ).inventory()
            try:
                with CredentialBrokerStore(self.paths.credential_broker_db) as credential_store:
                    inventory["oauth_accounts"] = [item.redacted_dict() for item in credential_store.list_accounts()]
            except Exception:
                inventory["oauth_accounts"] = []
            return inventory
        if route == "/api/v1/ai/free-discovery/catalog":
            return {"version": "1.0", "targets": discovery_catalog(), "third_party_requires_opt_in": True}
        if route == "/api/v1/ai/intelligence-search/catalog":
            return search_catalog()
        if route == "/api/v1/ai/intelligence-search/results":
            source = query.get("source", [None])[0]
            items = IntelligenceSearchStore(self.paths.intelligence_search_db).list(
                limit=int(query.get("limit", ["100"])[0]), source_id=source
            )
            return {"candidates": [item.as_dict() for item in items], "count": len(items)}
        if route == "/api/v1/ai/intelligence-search/activations":
            activation_store = IntelligenceActivationStore(self.paths.intelligence_search_db)
            records = activation_store.list(limit=int(query.get("limit", ["100"])[0]))
            return {
                "records": [item.as_dict() for item in records],
                "count": len(records),
                "audit_chain_valid": activation_store.verify_audit(),
                "raw_credentials_stored": False,
            }
        if route == "/api/v1/chat/conversations":
            return {"conversations": self.conversations.list_conversations(limit=int(query.get("limit", ["100"])[0]))}
        if route == "/api/v1/chat/messages":
            conversation_id = self._required_query(query, "conversation_id")
            return {"conversation_id": conversation_id, "messages": [m.as_dict() for m in self.conversations.messages(conversation_id)]}
        if route == "/api/v1/chat/context":
            conversation_id = self._required_query(query, "conversation_id")
            token_budget = int(query.get("token_budget", ["2400"])[0])
            search_text = query.get("query", [""])[0]
            config = load_json(self.paths.config)
            memory_cfg = config.get("memory", {}) if isinstance(config, Mapping) else {}
            principal_cfg = memory_cfg.get("principal", {}) if isinstance(memory_cfg, Mapping) else {}
            principal = MemoryPrincipal(
                principal_type=str(principal_cfg.get("principal_type") or "human"),
                principal_id=str(principal_cfg.get("principal_id") or "local-owner"),
                groups=tuple(str(x) for x in principal_cfg.get("groups", ())),
            )
            with MemoryStore(self.paths.memory_db) as memory_store:
                pack = self.conversations.build_context(
                    conversation_id, query=search_text, token_budget=token_budget,
                    memory_store=memory_store,
                    memory_namespace=str(memory_cfg.get("namespace_id") or "w1-local"),
                    memory_project=str(memory_cfg.get("project_id") or "default-project"),
                    memory_principal=principal,
                )
            return pack.as_dict()
        if route == "/api/v1/health":
            return {"ok": True, "version": DESKTOP_VERSION, "time": utc_now()}
        raise KeyError("route_not_found")

    def post(self, route: str, payload: Mapping[str, Any]) -> Any:
        if not self.settings.allow_operations:
            raise PermissionError("desktop_shell_operations_disabled")
        with self._operation_lock:
            if route == "/api/v1/chat/message":
                message = self.conversations.append_message(
                    str(payload.get("conversation_id") or "chat-main"),
                    role=str(payload.get("role") or "user"),
                    content=str(payload.get("content") or ""),
                    title=str(payload.get("title") or "") or None,
                    model_id=str(payload.get("model_id") or "") or None,
                    metadata=(dict(payload.get("metadata") or {}) if isinstance(payload.get("metadata"), Mapping) else {}),
                )
                return {"message": message.as_dict(), "long_term_memory_promoted": False}
            if route == "/api/v1/chat/summary":
                summary = self.conversations.record_summary(
                    str(payload["conversation_id"]),
                    start_sequence=int(payload["start_sequence"]),
                    end_sequence=int(payload["end_sequence"]),
                    summary=str(payload["summary"]),
                    source=str(payload.get("source") or "explicit"),
                )
                return {"summary": asdict(summary) | {"token_estimate": summary.token_estimate}}
            if route.startswith("/api/v1/ai/"):
                connection_store = AIConnectionStore(self.paths.ai_connections_db)
                model_store = ModelAccessStore(self.paths.model_access_db)
                with CredentialBrokerStore(self.paths.credential_broker_db) as credential_store:
                    broker = None
                    needs_broker = route in {"/api/v1/ai/connection/api-key", "/api/v1/ai/discover"}
                    if route == "/api/v1/ai/free-discovery/scan" and bool(payload.get("scan_connected", True)):
                        needs_broker = True
                    if needs_broker:
                        try:
                            vault = create_native_credential_vault(workspace_credential_namespace(self.paths.root))
                            broker = CredentialBroker(credential_store, vault)
                        except CredentialVaultUnavailable:
                            # Loopback free/local discovery must still work on hosts without a native vault.
                            # Explicit credential operations remain fail-closed.
                            if route != "/api/v1/ai/free-discovery/scan":
                                raise
                    service = AIConnectionService(
                        connection_store, model_store, credential_broker=broker, credential_store=credential_store
                    )
                    if route == "/api/v1/ai/connection/api-key":
                        secret = payload.get("secret")
                        if not isinstance(secret, str) or not secret:
                            raise ValueError("secret_required")
                        return service.add_api_key_connection(
                            connection_id=str(payload["connection_id"]),
                            provider_id=str(payload["provider_id"]),
                            secret_value=secret,
                            label=str(payload.get("label") or "") or None,
                            endpoint=str(payload.get("endpoint") or "") or None,
                        ).redacted_dict()
                    if route == "/api/v1/ai/connection/account":
                        return service.attach_oauth_account(
                            connection_id=str(payload["connection_id"]),
                            provider_id=str(payload["provider_id"]),
                            account_id=str(payload["account_id"]),
                            label=str(payload.get("label") or "") or None,
                            endpoint=str(payload.get("endpoint") or "") or None,
                        ).redacted_dict()
                    if route == "/api/v1/ai/connection/custom":
                        return service.add_custom_connection(
                            connection_id=str(payload["connection_id"]),
                            endpoint=str(payload["endpoint"]),
                            label=str(payload.get("label") or "") or None,
                        ).redacted_dict()
                    if route == "/api/v1/ai/connection/local":
                        return service.add_local_connection(
                            connection_id=str(payload["connection_id"]),
                            endpoint=str(payload["endpoint"]),
                            label=str(payload.get("label") or "") or None,
                        ).redacted_dict()
                    if route == "/api/v1/ai/model":
                        capabilities = payload.get("capabilities")
                        if capabilities is not None and not isinstance(capabilities, Mapping):
                            raise ValueError("capabilities_object_required")
                        return service.add_model(
                            connection_id=str(payload["connection_id"]),
                            model_id=str(payload["model_id"]),
                            model_name=str(payload["model_name"]),
                            display_name=str(payload.get("display_name") or "") or None,
                            roles=tuple(str(x) for x in payload.get("roles", ())),
                            domains=tuple(str(x) for x in payload.get("domains", ())),
                            capabilities=dict(capabilities or {"general": 0.7}),
                            metadata=(dict(payload.get("metadata") or {}) if isinstance(payload.get("metadata"), Mapping) else {}),
                        ).redacted_dict()
                    if route == "/api/v1/ai/capacity/observe":
                        observation = CapacityObservation(
                            model_id=str(payload["model_id"]),
                            source=str(payload["source"]),
                            quota_state=str(payload.get("quota_state") or "unknown"),
                            remaining_tokens=(int(payload["remaining_tokens"]) if payload.get("remaining_tokens") is not None else None),
                            reset_at=str(payload.get("reset_at") or "") or None,
                            input_cost_per_million=float(payload.get("input_cost_per_million", 0.0)),
                            output_cost_per_million=float(payload.get("output_cost_per_million", 0.0)),
                            terms_status=str(payload.get("terms_status") or "official"),
                            entitlement_verified=bool(payload.get("entitlement_verified", False)),
                        )
                        CapacityStore(self.paths.capacity_router_db).put(observation)
                        return {"observation": observation.as_dict(), "raw_credentials_stored": False}
                    if route == "/api/v1/ai/capacity/plan":
                        task_payload = payload.get("task")
                        policy_payload = payload.get("policy")
                        if not isinstance(task_payload, Mapping):
                            raise ValueError("capacity_task_object_required")
                        if policy_payload is not None and not isinstance(policy_payload, Mapping):
                            raise ValueError("capacity_policy_object_required")
                        task = CompiledTask(
                            task_id=str(task_payload.get("task_id") or "desktop-adaptive-task"),
                            title=str(task_payload.get("title") or "Adaptive AI task"),
                            phase=str(task_payload.get("phase") or "execution"),
                            role=str(task_payload.get("role") or "producer"),
                            expected_output_type=str(task_payload.get("expected_output_type") or "text"),
                            domains=tuple(str(x) for x in task_payload.get("domains", ("general",))),
                        )
                        pp = dict(policy_payload or {})
                        policy = RoutingPolicy(
                            mode=str(pp.get("mode") or "balanced"),
                            quality_floor=float(pp.get("quality_floor", 0.65)),
                            max_task_cost_usd=(float(pp["max_task_cost_usd"]) if pp.get("max_task_cost_usd") is not None else None),
                            estimated_input_tokens=int(pp.get("estimated_input_tokens", 2500)),
                            estimated_output_tokens=int(pp.get("estimated_output_tokens", 1500)),
                            producer_count=int(pp.get("producer_count", 2)),
                            fallback_per_role=int(pp.get("fallback_per_role", 1)),
                            allow_paid=bool(pp.get("allow_paid", True)),
                            allow_local=bool(pp.get("allow_local", True)),
                            allow_third_party_free=bool(pp.get("allow_third_party_free", False)),
                            require_known_terms=bool(pp.get("require_known_terms", True)),
                        )
                        router = AdaptiveCapacityRouter.from_store(CapacityStore(self.paths.capacity_router_db))
                        plan = router.plan(
                            plan_id=str(payload.get("plan_id") or "desktop-adaptive-team"),
                            display_name=str(payload.get("display_name") or "Adaptive W1 Team"),
                            team_mode=str(payload.get("team_mode") or "challenge"),
                            task=task,
                            profiles=model_store.list_profiles(enabled_only=True),
                            policy=policy,
                        )
                        if bool(payload.get("save_portfolio", False)):
                            model_store.put_portfolio(plan.portfolio)
                        return {"capacity_plan": plan.as_dict(), "portfolio_saved": bool(payload.get("save_portfolio", False))}
                    if route == "/api/v1/ai/team":
                        team_payload = payload.get("team")
                        if not isinstance(team_payload, Mapping):
                            raise ValueError("team_object_required")
                        team = team_from_mapping(team_payload)
                        portfolio = service.save_team(team)
                        return {"team": team.as_dict(), "compiled_portfolio": asdict(portfolio)}
                    if route == "/api/v1/ai/discover":
                        return {"models": list(service.discover_models(str(payload["connection_id"])))}
                    if route == "/api/v1/ai/intelligence-search/search":
                        query_text = str(payload.get("query") or "").strip()
                        sources_payload = payload.get("sources")
                        if sources_payload is None:
                            sources = None
                        elif isinstance(sources_payload, list):
                            sources = tuple(str(x) for x in sources_payload)
                        else:
                            raise ValueError("intelligence_search_sources_array_required")
                        engine = IntelligenceSearchEngine(IntelligenceSearchStore(self.paths.intelligence_search_db))
                        report = engine.search(
                            query_text,
                            sources=sources,
                            limit_per_source=int(payload.get("limit_per_source", 12)),
                        )
                        return report.as_dict()
                    if route in {
                        "/api/v1/ai/intelligence-search/assess",
                        "/api/v1/ai/intelligence-search/review",
                        "/api/v1/ai/intelligence-search/activate",
                    }:
                        search_store = IntelligenceSearchStore(self.paths.intelligence_search_db)
                        activation_store = IntelligenceActivationStore(self.paths.intelligence_search_db)
                        activation = IntelligenceActivationService(
                            search_store=search_store,
                            activation_store=activation_store,
                            connection_store=connection_store,
                            model_store=model_store,
                            capacity_store=CapacityStore(self.paths.capacity_router_db),
                            connection_service=service,
                        )
                        candidate_id = str(payload.get("candidate_id") or "").strip()
                        if not candidate_id:
                            raise ValueError("intelligence_candidate_id_required")
                        if route.endswith("/assess"):
                            assessment = activation.assess(
                                candidate_id,
                                connection_id=str(payload.get("connection_id") or "") or None,
                            )
                            return assessment.as_dict()
                        if route.endswith("/review"):
                            record = activation.review(
                                candidate_id,
                                decision=str(payload.get("decision") or "deferred"),
                                note=str(payload.get("note") or ""),
                            )
                            return {"record": record.as_dict(), "audit_chain_valid": activation_store.verify_audit()}
                        record = activation.activate(
                            candidate_id,
                            connection_id=str(payload.get("connection_id") or "") or None,
                            profile_id=str(payload.get("profile_id") or "") or None,
                        )
                        return {
                            "record": record.as_dict(),
                            "audit_chain_valid": activation_store.verify_audit(),
                            "third_party_free_router_opt_in_required": True,
                        }
                    if route == "/api/v1/ai/intelligence-search/clear":
                        count = IntelligenceSearchStore(self.paths.intelligence_search_db).clear()
                        return {"cleared": count}
                    if route == "/api/v1/ai/free-discovery/scan":
                        discovery = FreeIntelligenceDiscovery(
                            connection_store=connection_store, model_store=model_store,
                            capacity_store=CapacityStore(self.paths.capacity_router_db), service=service,
                        )
                        findings = list(discovery.scan_loopback(
                            include_third_party=bool(payload.get("include_third_party", False)),
                            timeout_seconds=float(payload.get("timeout_seconds", 1.5)),
                        ))
                        if bool(payload.get("scan_connected", True)):
                            findings.extend(discovery.scan_connected(timeout_seconds=float(payload.get("provider_timeout_seconds", 8.0))))
                        return {
                            "version": "1.0",
                            "findings": [item.as_dict() for item in findings],
                            "third_party_opt_in": bool(payload.get("include_third_party", False)),
                            "entitlement_inferred": False,
                        }
                    if route == "/api/v1/ai/free-discovery/adopt-local":
                        finding_payload = payload.get("finding")
                        if not isinstance(finding_payload, Mapping):
                            raise ValueError("discovery_finding_object_required")
                        from .free_intelligence import DiscoveryFinding
                        finding = DiscoveryFinding(
                            target_id=str(finding_payload["target_id"]),
                            display_name=str(finding_payload.get("display_name") or finding_payload["target_id"]),
                            kind=str(finding_payload.get("kind") or "local_runtime"),
                            status=str(finding_payload.get("status") or "not_detected"),
                            capacity_source=str(finding_payload.get("capacity_source") or "local"),
                            terms_status=str(finding_payload.get("terms_status") or "official"),
                            endpoint=str(finding_payload.get("endpoint") or "") or None,
                            models=tuple(str(x) for x in finding_payload.get("models", ())),
                            requires_auth=bool(finding_payload.get("requires_auth", False)),
                            third_party=bool(finding_payload.get("third_party", False)),
                            message=str(finding_payload.get("message") or ""),
                            metadata=(dict(finding_payload.get("metadata") or {}) if isinstance(finding_payload.get("metadata"), Mapping) else {}),
                        )
                        discovery = FreeIntelligenceDiscovery(
                            connection_store=connection_store, model_store=model_store,
                            capacity_store=CapacityStore(self.paths.capacity_router_db), service=service,
                        )
                        return discovery.adopt_local_ollama(finding)
                    raise KeyError("route_not_found")
            if route == "/api/v1/office/create":
                model = payload.get("model")
                if not isinstance(model, Mapping):
                    raise ValueError("office_model_object_required")
                return self.office.create(
                    validate_artifact_model(model),
                    created_by=str(payload.get("created_by") or "local-author"),
                ).as_dict()
            if route == "/api/v1/office/patch":
                operations = payload.get("patch")
                if not isinstance(operations, list):
                    raise ValueError("office_patch_array_required")
                return self.office.patch(
                    str(payload["artifact_id"]), operations,
                    created_by=str(payload.get("created_by") or "local-author"),
                    expected_parent_hash=str(payload["expected_parent_hash"]),
                ).as_dict()
            if route == "/api/v1/office/review":
                return self.office.review(
                    str(payload["artifact_id"]), version=int(payload["version"]),
                    reviewer=str(payload.get("reviewer") or "independent-reviewer"),
                    outcome=str(payload["outcome"]), rationale=str(payload["rationale"]),
                )
            if route == "/api/v1/office/export":
                with ActionRuntime(self.paths.root, state_dir=self.paths.state_dir, policy=action_policy(self.paths)) as runtime:
                    return self.office.export(
                        str(payload["artifact_id"]), format=str(payload["format"]),
                        output_path=str(payload["output_path"]),
                        exported_by=str(payload.get("exported_by") or "local-owner"),
                        action_runtime=runtime,
                        issue_action_approval=bool(payload.get("issue_action_approval", False)),
                    ).as_dict()
            if route == "/api/v1/office/verify":
                return self.office.verify()
            if route == "/api/v1/artifacts/draft":
                return self.artifacts.create_draft(
                    artifact_id=str(payload["artifact_id"]), path=str(payload["path"]),
                    title=str(payload.get("title") or Path(str(payload["path"])).name),
                    created_by=str(payload.get("created_by") or "local-user"),
                    content=payload.get("content"),
                ).as_dict()
            if route == "/api/v1/artifacts/version":
                return self.artifacts.save_version(
                    str(payload["artifact_id"]), content=str(payload["content"]),
                    created_by=str(payload.get("created_by") or "local-user"),
                    expected_parent_hash=str(payload["expected_parent_hash"]),
                ).as_dict()
            if route == "/api/v1/artifacts/comment":
                return self.artifacts.add_comment(
                    str(payload["artifact_id"]), version=int(payload["version"]),
                    author=str(payload.get("author") or "local-reviewer"),
                    body=str(payload["body"]),
                    line_number=int(payload["line_number"]) if payload.get("line_number") else None,
                )
            if route == "/api/v1/artifacts/review":
                return self.artifacts.review(
                    str(payload["artifact_id"]), version=int(payload["version"]),
                    reviewer=str(payload.get("reviewer") or "local-reviewer"),
                    outcome=str(payload["outcome"]), rationale=str(payload["rationale"]),
                )
            if route == "/api/v1/artifacts/publish":
                with ActionRuntime(self.paths.root, state_dir=self.paths.state_dir, policy=action_policy(self.paths)) as runtime:
                    return self.artifacts.publish(
                        str(payload["artifact_id"]),
                        published_by=str(payload.get("published_by") or "local-owner"),
                        action_runtime=runtime,
                        issue_action_approval=bool(payload.get("issue_action_approval", False)),
                    )
            if route == "/api/v1/terminal/plan":
                request = self._terminal_request(payload)
                with ActionRuntime(self.paths.root, state_dir=self.paths.state_dir, policy=action_policy(self.paths)) as runtime:
                    return asdict(runtime.plan(request))
            if route == "/api/v1/terminal/run":
                request = self._terminal_request(payload)
                with ActionRuntime(self.paths.root, state_dir=self.paths.state_dir, policy=action_policy(self.paths)) as runtime:
                    plan = runtime.plan(request)
                    approval: ActionApproval | None = None
                    if plan.requires_approval:
                        if not bool(payload.get("approve", False)):
                            raise ActionApprovalRequired(plan)
                        approval = runtime.issue_approval(plan, issued_by=str(payload.get("issued_by") or "local-owner"))
                    return asdict(runtime.execute(request, approval=approval))
            if route == "/api/v1/artifacts/verify":
                return self.artifacts.verify()
        raise KeyError("route_not_found")

    @staticmethod
    def _required_query(query: Mapping[str, list[str]], key: str) -> str:
        value = query.get(key, [""])[0]
        if not value:
            raise ValueError(f"{key}_required")
        return value

    @staticmethod
    def _terminal_request(payload: Mapping[str, Any]) -> ActionRequest:
        argv = payload.get("argv")
        if not isinstance(argv, list) or not argv or not all(isinstance(item, str) for item in argv):
            raise ValueError("argv_array_required")
        action_id = str(payload.get("action_id") or ("terminal-" + secrets.token_hex(8)))
        parameters: dict[str, Any] = {"argv": argv, "cwd": str(payload.get("cwd") or ".")}
        if payload.get("timeout_seconds") is not None:
            parameters["timeout_seconds"] = int(payload["timeout_seconds"])
        return ActionRequest(
            action_id=action_id, kind="command.run", parameters=parameters,
            requested_by=str(payload.get("requested_by") or "desktop-user"),
            reason=str(payload.get("reason") or "Governed Desktop terminal command"),
        )


class DesktopShellServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], application: DesktopShellApplication) -> None:
        self.application = application
        self._state_lock = threading.RLock()
        self._state = "STARTED"  # STARTED, RUNNING, SHUTTING_DOWN, CLOSED
        self._serve_thread: threading.Thread | None = None
        self._serve_done = threading.Event()
        super().__init__(address, DesktopShellHandler)

    @property
    def lifecycle_state(self) -> str:
        with self._state_lock:
            return self._state

    def serve_forever(self, poll_interval: float = 0.5) -> None:
        with self._state_lock:
            if self._state == "CLOSED":
                raise RuntimeError("Cannot serve on a closed DesktopShellServer")
            self._state = "RUNNING"
            self._serve_thread = threading.current_thread()
            self._serve_done.clear()
        try:
            super().serve_forever(poll_interval=poll_interval)
        finally:
            with self._state_lock:
                if self._state != "CLOSED":
                    self._state = "SHUTTING_DOWN"
                self._serve_done.set()

    def close_server(self, join_timeout: float = 3.0) -> None:
        """Graceful, idempotent shutdown and resource cleanup in strict lifecycle order:
        SHUTTING_DOWN -> shutdown() -> wait for serve_forever to finish -> server_close() -> application.close() -> CLOSED.
        """
        with self._state_lock:
            if self._state == "CLOSED":
                return
            self._state = "SHUTTING_DOWN"

        # 1. Signal serve_forever selector loop to terminate
        try:
            self.shutdown()
        except OSError:
            # Handle socket already closed prior to shutdown
            pass

        # 2. Wait for serve_forever loop to completely exit if running in a separate thread
        if self._serve_thread and self._serve_thread is not threading.current_thread():
            self._serve_done.wait(timeout=join_timeout)

        # 3. Close the listening socket descriptor and application state safely AFTER the loop has stopped
        with self._state_lock:
            if self._state == "CLOSED":
                return
            try:
                self.application.close()
            finally:
                try:
                    super().server_close()
                finally:
                    self._state = "CLOSED"

    def server_close(self) -> None:
        """Idempotent server and application close."""
        with self._state_lock:
            if self._state == "CLOSED":
                return
            self._state = "SHUTTING_DOWN"
            try:
                self.application.close()
            finally:
                try:
                    super().server_close()
                finally:
                    self._state = "CLOSED"


class DesktopShellHandler(BaseHTTPRequestHandler):
    server: DesktopShellServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=(), usb=()")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
        )
        self.send_header("Cache-Control", "no-store")

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self._headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, value: Any) -> None:
        self._send(status, (canonical_json(value) + "\n").encode(), "application/json; charset=utf-8")

    def _valid_host(self) -> bool:
        raw = self.headers.get("Host", "")
        host = raw.rsplit(":", 1)[0].strip("[]").lower()
        return host in LOOPBACK_HOSTS

    def _authorised(self, query: Mapping[str, list[str]]) -> bool:
        header = self.headers.get("Authorization", "")
        if header.startswith("Bearer ") and secrets.compare_digest(header[7:], self.server.application.token):
            return True
        candidate = query.get("token", [""])[0]
        return bool(candidate and secrets.compare_digest(candidate, self.server.application.token))

    def do_GET(self) -> None:
        if not self._valid_host():
            self._json(HTTPStatus.MISDIRECTED_REQUEST, {"error": "desktop_shell_invalid_host"})
            return
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        if parsed.path == "/":
            text = _asset_bytes("index.html").decode().replace(
                "__W1_DESKTOP_BOOTSTRAP__",
                html.escape(json.dumps(self.server.application.bootstrap(), ensure_ascii=False), quote=True),
            )
            self._send(HTTPStatus.OK, text.encode(), "text/html; charset=utf-8")
            return
        if parsed.path.startswith("/assets/"):
            name = Path(parsed.path).name
            allowed = {"styles.css", "app.js", "sw.js", "logo.svg", "manifest.webmanifest"}
            if name not in allowed:
                self._json(HTTPStatus.NOT_FOUND, {"error": "asset_not_found"})
                return
            content_type = {
                ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                ".svg": "image/svg+xml; charset=utf-8", ".webmanifest": "application/manifest+json",
            }[Path(name).suffix]
            self._send(HTTPStatus.OK, _asset_bytes(name), content_type)
            return
        if parsed.path.startswith("/api/") and not self._authorised(query):
            self._json(HTTPStatus.UNAUTHORIZED, {"error": "desktop_shell_unauthorized"})
            return
        try:
            self._json(HTTPStatus.OK, self.server.application.get(parsed.path, query))
        except KeyError:
            self._json(HTTPStatus.NOT_FOUND, {"error": "route_not_found"})
        except (ValueError, ArtifactPathDenied) as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": getattr(exc, "code", str(exc))})
        except ArtifactNotFound as exc:
            self._json(HTTPStatus.NOT_FOUND, {"error": exc.code})

    def do_POST(self) -> None:
        if not self._valid_host():
            self._json(HTTPStatus.MISDIRECTED_REQUEST, {"error": "desktop_shell_invalid_host"})
            return
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        if not self._authorised(query):
            self._json(HTTPStatus.UNAUTHORIZED, {"error": "desktop_shell_unauthorized"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 3_000_000:
                raise ValueError("request_too_large")
            payload = json.loads(self.rfile.read(length).decode() or "{}")
            if not isinstance(payload, dict):
                raise ValueError("json_object_required")
            result = self.server.application.post(parsed.path, payload)
            self._json(HTTPStatus.OK, {"ok": True, "result": result})
        except PermissionError as exc:
            self._json(HTTPStatus.FORBIDDEN, {"ok": False, "error": str(exc)})
        except ActionApprovalRequired as exc:
            self._json(HTTPStatus.CONFLICT, {"ok": False, "error": exc.code, "plan": asdict(exc.plan)})
        except ArtifactReviewRequired as exc:
            self._json(HTTPStatus.CONFLICT, {"ok": False, "error": exc.code, "message": str(exc)})
        except (ArtifactIndependentReviewRequired, ArtifactVersionConflict) as exc:
            self._json(HTTPStatus.CONFLICT, {"ok": False, "error": getattr(exc, "code", str(exc)), "message": str(exc)})
        except ArtifactExportUnavailable as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": getattr(exc, "code", str(exc)), "message": str(exc)})
        except ArtifactConflict as exc:
            self._json(HTTPStatus.CONFLICT, {"ok": False, "error": exc.code, "message": str(exc)})
        except ArtifactNotFound as exc:
            self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": exc.code})
        except UniversalArtifactError as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": getattr(exc, "code", str(exc)), "message": str(exc)})
        except (ValueError, ArtifactPathDenied, json.JSONDecodeError) as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": getattr(exc, "code", str(exc))})
        except KeyError:
            self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "route_not_found"})
        except Exception as exc:
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": type(exc).__name__})


def detect_desktop_backend() -> str:
    try:
        import webview  # type: ignore  # noqa: F401
        return "native-webview"
    except Exception:
        return "browser-pwa"


def create_desktop_server(paths: WorkspacePaths, settings: DesktopSettings | None = None) -> DesktopShellServer:
    selected = settings or DesktopSettings()
    selected.validate()
    application = DesktopShellApplication(paths, selected)
    return DesktopShellServer((selected.host, selected.port), application)


def serve_desktop(paths: WorkspacePaths, settings: DesktopSettings | None = None) -> None:
    selected = settings or DesktopSettings()
    server = create_desktop_server(paths, selected)
    url = f"http://{selected.host}:{server.server_port}/"
    if selected.open_browser:
        threading.Timer(0.25, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        server.close_server()


def launch_desktop(paths: WorkspacePaths, settings: DesktopSettings | None = None) -> str:
    """Launch an optional native webview or the installable browser shell.

    Returns the backend selected.  The call blocks until the window/server exits.
    """
    selected = settings or DesktopSettings()
    server = create_desktop_server(paths, selected)
    url = f"http://{selected.host}:{server.server_port}/"
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True)
    thread.start()
    backend = detect_desktop_backend()
    try:
        if backend == "native-webview":
            import webview  # type: ignore
            webview.create_window("W1 Nexus", url, width=1440, height=900, min_size=(960, 640))
            webview.start()
        else:
            webbrowser.open(url)
            thread.join()
    finally:
        server.close_server(join_timeout=3.0)
        thread.join(timeout=3.0)
    return backend
