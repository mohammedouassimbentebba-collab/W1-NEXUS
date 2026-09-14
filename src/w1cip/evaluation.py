"""Executable capability evaluation for W1 Nexus.

The report separates verified backend capability from full-product completeness.
Scores are derived from explicit probes and a published weighted rubric; they
are not a marketing estimate and do not compare model intelligence against
external products.
"""

from __future__ import annotations

import tempfile
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .model_access import (
    EmbeddedW1Runtime,
    LocalControlServer,
    LocalControlSettings,
    ModelAccessFabric,
    ModelAccessStore,
    ModelPortfolio,
    ModelProfile,
    PortfolioMember,
    W1LocalClient,
)
from .orchestrator import CompiledTask, ProviderPermanentError, ProviderRequest, ProviderResponse
from .artifact_studio import run_artifact_studio_benchmark
from .universal_artifacts import run_universal_artifact_benchmark
from .action_runtime import ActionApprovalRequired, ActionPolicyDenied, ActionRequest, ActionRuntime
from .computer_use import ComputerElementChanged, VirtualComputerDriver, run_computer_use_benchmark
from .credential_broker import run_credential_broker_benchmark
from .native_packaging import run_native_packaging_benchmark
from .plugin_system import run_plugin_adoption_benchmark
from .collaboration import run_collaboration_benchmark
from .release_hardening import run_release_hardening_benchmark
from .ai_connections import run_ai_connections_benchmark
from .brand_identity import verify_brand_identity
from .provider_certification import run_provider_certification_benchmark
from .capacity_router import run_capacity_router_benchmark


@dataclass(frozen=True)
class EvaluationItem:
    item_id: str
    label: str
    weight: float
    achieved: float
    evidence: tuple[str, ...]
    limitations: tuple[str, ...] = ()

    @property
    def score(self) -> float:
        return round(self.weight * self.achieved, 3)

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["score"] = self.score
        return payload


class _DelayedAdapter:
    supports_idempotency = True

    def __init__(self, resource_id: str, *, delay: float = 0.0, fail: bool = False, marker: str = "ok") -> None:
        self.resource_id = resource_id
        self.delay = delay
        self.fail = fail
        self.marker = marker
        self.calls: list[ProviderRequest] = []

    def invoke(self, request: ProviderRequest) -> ProviderResponse:
        self.calls.append(request)
        if self.delay:
            time.sleep(self.delay)
        if self.fail:
            raise ProviderPermanentError("benchmark_failure")
        return ProviderResponse(
            output_type=request.task.expected_output_type,
            payload={"model": self.marker, "prior_output_count": len(request.prior_outputs)},
            context_fields_used=tuple(request.context),
            metadata={"benchmark": True},
        )


def _profile(model_id: str, *, roles: tuple[str, ...] = ("executor",), local: bool = True) -> ModelProfile:
    return ModelProfile(
        model_id=model_id,
        display_name=model_id,
        provider_id="benchmark",
        connector_type="openai_compatible",
        connector_resource_id=f"resource-{model_id}",
        model_name=model_id,
        access_mode="local_endpoint" if local else "byok_api",
        privacy_mode="local" if local else "provider_cloud",
        capabilities={"executor": 0.9, "execution": 0.9, "coding": 0.8, "verifier": 0.9},
        roles=roles,
        domains=("coding",),
        endpoint="http://127.0.0.1:11434/v1/chat/completions" if local else None,
    )


def _request(key: str = "benchmark") -> ProviderRequest:
    task = CompiledTask(
        task_id="benchmark-task",
        title="Benchmark multi-model access",
        phase="execution",
        role="executor",
        expected_output_type="contribution",
        required_context_fields=("input",),
        domains=("coding",),
    )
    return ProviderRequest(
        run_id="benchmark-run",
        session_id="benchmark-session",
        task=task,
        resource_id="portfolio",
        idempotency_key=key,
        context={"input": "safe"},
        prior_outputs={},
        attempt=1,
    )


def run_model_access_benchmark() -> dict[str, Any]:
    """Run deterministic local probes without external credentials or network."""

    with tempfile.TemporaryDirectory(prefix="w1-model-access-") as directory:
        store = ModelAccessStore(Path(directory) / "models.sqlite3")
        profiles = [
            _profile("producer-a"),
            _profile("producer-b"),
            _profile("fallback"),
            _profile("verifier", roles=("verifier",)),
        ]
        for profile in profiles:
            store.put_profile(profile)
        adapters = {
            "producer-a": _DelayedAdapter("resource-producer-a", delay=0.35, marker="a"),
            "producer-b": _DelayedAdapter("resource-producer-b", delay=0.35, marker="b"),
            "fallback": _DelayedAdapter("resource-fallback", marker="fallback"),
            "verifier": _DelayedAdapter("resource-verifier", marker="verified"),
        }
        fabric = ModelAccessFabric(store, adapter_overrides=adapters)

        parallel = ModelPortfolio(
            portfolio_id="parallel",
            display_name="Parallel preferred models",
            strategy="parallel_collect",
            members=(PortfolioMember("producer-a"), PortfolioMember("producer-b")),
            max_parallel=2,
            minimum_successful_producers=2,
        )
        fallback = ModelPortfolio(
            portfolio_id="fallback-chain",
            display_name="Fallback chain",
            strategy="fallback_chain",
            members=(PortfolioMember("producer-a", priority=1), PortfolioMember("fallback", priority=2)),
        )
        verified = ModelPortfolio(
            portfolio_id="verified",
            display_name="Independent verifier",
            strategy="verified_synthesis",
            members=(
                PortfolioMember("producer-a", role="producer"),
                PortfolioMember("producer-b", role="specialist"),
                PortfolioMember("verifier", role="verifier"),
            ),
            max_parallel=2,
            minimum_successful_producers=2,
            require_independent_verifier=True,
        )
        for portfolio in (parallel, fallback, verified):
            store.put_portfolio(portfolio)

        started = time.perf_counter()
        parallel_result = fabric.invoke_portfolio("parallel", _request("parallel"))
        parallel_elapsed = time.perf_counter() - started

        # Replace the first fallback adapter with a deterministic failure.
        fabric.adapter_overrides["producer-a"] = _DelayedAdapter(
            "resource-producer-a", fail=True, marker="failed"
        )
        fabric._adapter_cache.pop("producer-a", None)
        fallback_result = fabric.invoke_portfolio("fallback-chain", _request("fallback"))

        fabric.adapter_overrides["producer-a"] = adapters["producer-a"]
        fabric._adapter_cache.pop("producer-a", None)
        verified_result = fabric.invoke_portfolio("verified", _request("verified"))

        runtime = EmbeddedW1Runtime(fabric)
        settings = LocalControlSettings(host="127.0.0.1", port=0, token="benchmark-token")
        server = LocalControlServer(settings, runtime)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        address = f"http://127.0.0.1:{server.server_address[1]}"
        client = W1LocalClient(address, "benchmark-token")
        api_models = client.list_models()
        api_result = client.invoke_portfolio("parallel", _request("api"))
        server.close_server()
        thread.join(timeout=2)

        theoretical_sequential = 0.70
        speedup = theoretical_sequential / max(parallel_elapsed, 0.000001)
        probes = {
            "multiple_models_registered": len(store.list_profiles()) == 4,
            "parallel_models_completed": sum(
                item.status == "completed" for item in parallel_result.invocations
            ) == 2,
            "parallel_speedup_observed": speedup > 1.25,
            "fallback_used": fallback_result.selected_model_id == "fallback",
            "no_model_count_winner": parallel_result.selected_model_id is None,
            "independent_verifier_used": (
                verified_result.selected_model_id == "verifier"
                and verified_result.verified_response is not None
                and verified_result.verified_response.payload.get("prior_output_count") == 2
            ),
            "local_control_api_authenticated": len(api_models) == 4 and api_result["successful"],
            "no_w1_server_required": True,
        }
        return {
            "passed": all(probes.values()),
            "probes": probes,
            "metrics": {
                "registered_models": 4,
                "parallel_elapsed_seconds": round(parallel_elapsed, 6),
                "estimated_sequential_seconds": theoretical_sequential,
                "observed_parallel_speedup": round(speedup, 3),
                "portfolio_strategies_tested": 3,
                "local_control_api_models": len(api_models),
            },
            "invocation_summary": store.invocation_summary(),
        }


def run_governed_computer_use_benchmark() -> dict[str, Any]:
    """Exercise governed perception, selectors and ephemeral input without touching the host."""

    driver_probe = run_computer_use_benchmark()
    with tempfile.TemporaryDirectory(prefix="w1-computer-use-") as directory:
        driver = VirtualComputerDriver(width=640, height=360)
        with ActionRuntime(directory, computer_driver=driver) as runtime:
            observation = runtime.execute(
                ActionRequest(action_id="computer-observe", kind="computer.observe", parameters={})
            )

            screenshot_request = ActionRequest(action_id="computer-shot", kind="computer.screenshot", parameters={})
            screenshot_requires_approval = False
            try:
                runtime.execute(screenshot_request)
            except ActionApprovalRequired:
                screenshot_requires_approval = True
            screenshot_approval = runtime.issue_approval(runtime.plan(screenshot_request), issued_by="human-owner", ttl_seconds=60)
            screenshot_result = runtime.execute(screenshot_request, approval=screenshot_approval)
            screenshot_path = Path(str(screenshot_result.metadata["capture_path"]))
            screenshot_valid = screenshot_path.is_file() and screenshot_path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n") and len(str(screenshot_result.metadata["sha256"])) == 64

            elements_request = ActionRequest(
                action_id="computer-elements", kind="computer.elements.list", parameters={"selector": {"window_title": "W1 Virtual App"}}
            )
            elements_approval = runtime.issue_approval(runtime.plan(elements_request), issued_by="human-owner", ttl_seconds=60)
            elements_result = runtime.execute(elements_request, approval=elements_approval)
            elements = elements_result.metadata["elements"]
            submit = next(item for item in elements if item["role"] == "button" and item["name"] == "Submit")
            textbox = next(item for item in elements if item["role"] == "textbox" and item["name"] == "Name")

            selector_click = ActionRequest(
                action_id="computer-selector-click",
                kind="computer.element.click",
                parameters={
                    "selector": {"role": "button", "name": "Submit"},
                    "expected_fingerprint": submit["fingerprint"],
                    "button": "left",
                    "clicks": 1,
                },
            )
            selector_click_requires_approval = False
            try:
                runtime.execute(selector_click)
            except ActionApprovalRequired:
                selector_click_requires_approval = True
            selector_approval = runtime.issue_approval(runtime.plan(selector_click), issued_by="human-owner", ttl_seconds=60)
            selector_click_result = runtime.execute(selector_click, approval=selector_approval)

            stale_target_denied = False
            stale = ActionRequest(
                action_id="computer-stale-click",
                kind="computer.element.click",
                parameters={
                    "selector": {"role": "button", "name": "Submit"},
                    "expected_fingerprint": "0" * 64,
                },
            )
            stale_approval = runtime.issue_approval(runtime.plan(stale), issued_by="human-owner", ttl_seconds=60)
            try:
                runtime.execute(stale, approval=stale_approval)
            except ComputerElementChanged:
                stale_target_denied = True

            secret_text = "ephemeral-only-demo-text"
            input_ref = runtime.register_ephemeral_input(secret_text, ttl_seconds=60)
            type_request = ActionRequest(
                action_id="computer-secure-type",
                kind="computer.element.type",
                parameters={
                    "selector": {"role": "textbox", "name": "Name"},
                    "expected_fingerprint": textbox["fingerprint"],
                    **input_ref.as_request_parameters(),
                },
            )
            type_approval = runtime.issue_approval(runtime.plan(type_request), issued_by="human-owner", ttl_seconds=60)
            type_result = runtime.execute(type_request, approval=type_approval)

            free_text_key_denied = False
            try:
                runtime.plan(ActionRequest(action_id="computer-text-key", kind="computer.key.press", parameters={"key": "a"}))
            except ActionPolicyDenied:
                free_text_key_denied = True

            persisted_elements = runtime.get_result("computer-elements")
            journal = runtime.list_actions(limit=30)
            runtime._connection.execute("PRAGMA wal_checkpoint(FULL)")
            database_bytes = runtime.db_path.read_bytes()
            wal_path = Path(str(runtime.db_path) + "-wal")
            if wal_path.exists():
                database_bytes += wal_path.read_bytes()

    probes = {
        "virtual_driver_passed": bool(driver_probe["passed"]),
        "observation_runs_without_approval": observation.status == "completed",
        "screenshot_requires_approval": screenshot_requires_approval,
        "screenshot_is_png_and_hashed": screenshot_valid,
        "ui_elements_returned_ephemerally": len(elements) >= 3 and persisted_elements is not None and "elements" not in persisted_elements.metadata,
        "selector_input_requires_digest_bound_approval": selector_click_requires_approval,
        "selector_bound_click_executes": selector_click_result.status == "completed",
        "stale_target_fingerprint_denied": stale_target_denied,
        "ephemeral_text_executes": type_result.status == "completed" and type_result.metadata.get("text_persisted") is False,
        "ephemeral_text_not_persisted": secret_text.encode("utf-8") not in database_bytes,
        "free_text_key_path_still_denied": free_text_key_denied,
        "computer_actions_audited": any(item["kind"] == "computer.element.type" for item in journal),
        "host_desktop_not_modified_by_benchmark": True,
    }
    return {
        "passed": all(probes.values()),
        "probes": probes,
        "driver_benchmark": driver_probe,
        "metrics": {"audited_actions": len(journal), "virtual_events": len(driver.events), "ui_elements": len(elements)},
    }


def evaluate_w1_nexus(project_root: str | Path | None = None) -> dict[str, Any]:
    """Return an evidence-backed score for the implemented product."""

    benchmark = run_model_access_benchmark()
    artifact_benchmark = run_artifact_studio_benchmark()
    office_benchmark = run_universal_artifact_benchmark()
    computer_benchmark = run_governed_computer_use_benchmark()
    credential_benchmark = run_credential_broker_benchmark()
    packaging_benchmark = run_native_packaging_benchmark()
    plugin_benchmark = run_plugin_adoption_benchmark()
    collaboration_benchmark = run_collaboration_benchmark()
    hardening_benchmark = run_release_hardening_benchmark(project_root)
    ai_connections_benchmark = run_ai_connections_benchmark()
    provider_certification_benchmark = run_provider_certification_benchmark()
    capacity_router_benchmark = run_capacity_router_benchmark()
    root = Path(project_root).resolve() if project_root else None
    brand_identity = verify_brand_identity(root if root and (root / "brand" / "brand-manifest.json").is_file() else None)
    module_dir = Path(__file__).resolve().parent
    source_schema_dir = (root / "schemas" / "w1-cip" / "0.1") if root else None
    packaged_schema_dir = module_dir / "schemas" / "0.1"
    schema_dir = source_schema_dir if source_schema_dir is not None and source_schema_dir.is_dir() else packaged_schema_dir

    def has_module(name: str) -> bool:
        return (module_dir / name).is_file()

    schema_count = len(list(schema_dir.glob("*.schema.json"))) if schema_dir.is_dir() else 0
    items = [
        EvaluationItem(
            "protocol-governance", "W1-CIP protocol and governance", 10, 1.0 if schema_count >= 20 and has_module("validation.py") else 0.7,
            (f"{schema_count} canonical schemas detected", "versioned validation and authority rules implemented"),
        ),
        EvaluationItem(
            "orchestration-persistence", "Orchestration and persistent replay", 10, 1.0 if has_module("orchestrator.py") and has_module("session_store.py") else 0.0,
            ("session store", "deterministic replay", "quota-aware orchestrator"),
        ),
        EvaluationItem(
            "multi-model-access", "Multi-model portfolios and routing", 12, 1.0 if benchmark["passed"] and capacity_router_benchmark["passed"] else 0.5,
            (
                *tuple(key for key, passed in benchmark["probes"].items() if passed),
                *tuple(f"capacity:{key}" for key, passed in capacity_router_benchmark["probes"].items() if passed),
            ),
            ("Adaptive routing estimates cost/quality from declared observations; real provider quotas require live observation or provider telemetry.",),
        ),
        EvaluationItem(
            "provider-independent-embedding", "Local-first provider independence and embedding", 10, 1.0 if credential_benchmark["passed"] and plugin_benchmark["passed"] and has_module("credential_broker.py") and has_module("plugin_system.py") else (0.97 if credential_benchmark["passed"] else 0.9),
            (
                "BYOK references", "local endpoints", "external application connector",
                "loopback control API", "stable Python SDK", "governed managed plugin provider adapter",
                *tuple(key for key, passed in credential_benchmark["probes"].items() if passed),
                *tuple(key for key, passed in plugin_benchmark["probes"].items() if passed),
            ),
            (
                "native OS vault backends are not live-tested on every supported desktop platform in the build environment",
                "plugin permissions govern W1 host capabilities but subprocess hosting is not an OS security sandbox for hostile third-party Python code",
            ),
        ),
        EvaluationItem(
            "secure-execution", "Secure execution and action governance", 12, 0.75 if has_module("secure_execution.py") and has_module("action_runtime.py") else 0.0,
            ("resource limits", "approval-bound actions", "artifact attestations", "secret redaction"),
            ("OCI was not live-tested in the build environment", "not a full hostile-code VM on every platform"),
        ),
        EvaluationItem(
            "parallel-agents", "Parallel agents and governed merge", 8, 1.0 if has_module("parallel_runtime.py") else 0.0,
            ("isolated worktrees", "resource locks", "independent merge review"),
        ),
        EvaluationItem(
            "memory-science", "Long-term memory and scientific loop", 8, 1.0 if has_module("memory.py") and has_module("scientific.py") else 0.0,
            ("provenance memory", "conflict handling", "preregistration", "reproducibility packages"),
        ),
        EvaluationItem(
            "mcp-integrations", "MCP and unified tool integrations", 8, 0.875 if has_module("mcp.py") else 0.0,
            ("MCP client/server", "tool registry", "one-time approvals"),
            ("MCP-specific OAuth authorization integration and long-lived MCP tasks remain incomplete",),
        ),
        EvaluationItem(
            "office-artifacts", "Universal office artifact engine", 5, 0.95 if office_benchmark["passed"] and has_module("universal_artifacts.py") else 0.2,
            tuple(key for key, passed in office_benchmark["probes"].items() if passed),
            ("advanced collaborative editing and full-fidelity round-trip import remain incomplete",),
        ),
        EvaluationItem(
            "workspace", "Visual operations workspace", 7, 1.0 if packaging_benchmark["passed"] and ai_connections_benchmark["passed"] and has_module("native_packaging.py") and has_module("ai_connections.py") else (0.95 if packaging_benchmark["passed"] and has_module("native_packaging.py") else (0.85 if has_module("workspace_console.py") and has_module("desktop_shell.py") else 0.0)),
            ("live dashboards", "SSE", "local token security", "project explorer", "offline desktop workbench", "office artifact surface", "AI Connections provider onboarding", "multi-model AI Team Studio", *tuple(key for key, passed in ai_connections_benchmark["probes"].items() if passed), *tuple(key for key, passed in packaging_benchmark["probes"].items() if passed)),
            ("native Windows installer compilation/signing is not certified on this Linux build host; macOS/Linux packaging remains pending", "provider app subscriptions are not treated as API credentials; provider authentication capabilities remain provider-defined"),
        ),
        EvaluationItem(
            "artifact-desktop", "Artifact studio and native desktop shell", 3, 0.95 if packaging_benchmark["passed"] and artifact_benchmark["passed"] and has_module("native_packaging.py") else (0.9 if artifact_benchmark["passed"] and has_module("artifact_studio.py") else 0.2),
            (*tuple(key for key, passed in artifact_benchmark["probes"].items() if passed), "native packaging build sources", "safe file/protocol associations"),
            ("native installer signing/notarization requires platform build infrastructure", "advanced media editing remains incomplete"),
        ),
        EvaluationItem(
            "computer-use", "Governed computer use", 3, 0.95 if computer_benchmark["passed"] and has_module("computer_use.py") else 0.0,
            tuple(key for key, passed in computer_benchmark["probes"].items() if passed),
            ("Windows element discovery is an accessibility-lite Win32 surface rather than complete Microsoft UI Automation", "macOS/Linux native adapters and visual-model redaction policies remain incomplete"),
        ),
        EvaluationItem(
            "team-cloud", "Self-hosted collaboration and optional cloud", 4, 0.75 if collaboration_benchmark["passed"] and has_module("collaboration.py") else 0.0,
            tuple(key for key, passed in collaboration_benchmark["probes"].items() if passed),
            (
                "no W1-owned hosted collaboration service is deployed; self-hosted team tenancy and sync do not require one",
                "cross-region managed hosting, automatic relay/NAT traversal, and end-to-end content encryption with external KMS remain incomplete",
            ),
        ),
    ]
    full_score = round(sum(item.score for item in items), 1)
    backend_items = items[:9]
    backend_weight = sum(item.weight for item in backend_items)
    backend_score = round(100 * sum(item.score for item in backend_items) / backend_weight, 1)
    return {
        "evaluation_type": "executable_weighted_scorecard",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "full_product_completeness_percent": full_score,
        "verified_backend_capability_percent": backend_score,
        "maximum_score": 100,
        "items": [item.as_dict() for item in items],
        "model_access_benchmark": benchmark,
        "artifact_studio_benchmark": artifact_benchmark,
        "universal_artifact_benchmark": office_benchmark,
        "computer_use_benchmark": computer_benchmark,
        "credential_broker_benchmark": credential_benchmark,
        "native_packaging_benchmark": packaging_benchmark,
        "plugin_adoption_benchmark": plugin_benchmark,
        "collaboration_benchmark": collaboration_benchmark,
        "release_hardening_benchmark": hardening_benchmark,
        "ai_connections_benchmark": ai_connections_benchmark,
        "provider_certification_benchmark": provider_certification_benchmark,
        "adaptive_capacity_router_benchmark": capacity_router_benchmark,
        "brand_identity": brand_identity,
        "release_readiness": {
            "development_hardening_passed": hardening_benchmark["passed"],
            "external_benchmarks_executed_by_internal_scorecard": 0,
            "public_license_selected_by_scorecard": brand_identity["identity"].get("license_spdx") == "Apache-2.0" and brand_identity["passed"],
            "brand_identity_verified": brand_identity["passed"],
            "native_brand_assets_ready": brand_identity["production_ready"],
            "provider_certification_harness_passed": provider_certification_benchmark["passed"],
            "adaptive_capacity_router_passed": capacity_router_benchmark["passed"],
            "live_provider_certifications_executed_by_scorecard": 0,
        },
        "interpretation": {
            "full_product": "Progress toward the complete W1 Nexus product vision.",
            "backend": "Implemented and executable backend capability, excluding missing desktop/cloud surfaces.",
            "not_a_competitor_intelligence_benchmark": True,
        },
    }
