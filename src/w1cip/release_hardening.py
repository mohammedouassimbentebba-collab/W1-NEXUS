"""Release-hardening and external-benchmark evidence support for W1 Nexus.

This module intentionally separates three concerns:

1. deterministic local hardening probes that can run without Internet access;
2. a catalog of external benchmark families with explicit applicability limits;
3. evidence recording/verification for externally executed benchmark runs.

No external benchmark score is synthesized by this module. A score can only be
recorded when the caller supplies a result document plus a raw evidence artifact
whose SHA-256 digest is persisted alongside the claim.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import random
import re
import shutil
import tempfile
import time
import tomllib
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from .brand_identity import verify_brand_identity
from .collaboration import CollaborationStore, SyncMutation
from .native_packaging import DeepLinkDenied, ProjectDescriptor, ProjectDescriptorInvalid, parse_deep_link

HARDENING_VERSION = "1.0"
RELEASE_POLICY_VERSION = "1.0"
MAX_EXTERNAL_RESULT_BYTES = 2 * 1024 * 1024


class ReleaseHardeningError(RuntimeError):
    code = "release_hardening_error"


class ExternalBenchmarkEvidenceError(ReleaseHardeningError):
    code = "external_benchmark_evidence_invalid"


@dataclass(frozen=True)
class ExternalBenchmarkSpec:
    benchmark_id: str
    name: str
    version_label: str
    scope: str
    official_url: str
    applicability: str
    execution_boundary: str
    adapter_status: str
    score_claim_policy: str = "No score may be claimed without raw evidence and pinned upstream provenance."

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


EXTERNAL_BENCHMARKS: tuple[ExternalBenchmarkSpec, ...] = (
    ExternalBenchmarkSpec(
        benchmark_id="mcp-conformance-2026-07-28",
        name="Model Context Protocol Conformance",
        version_label="MCP specification 2026-07-28; pin conformance harness release/commit at execution time",
        scope="MCP client/server interoperability and protocol conformance",
        official_url="https://github.com/modelcontextprotocol/conformance",
        applicability="Directly applicable to W1's MCP client/server surface.",
        execution_boundary="Run the official upstream conformance harness against W1 endpoints; do not substitute W1's own tests.",
        adapter_status="planned-direct",
    ),
    ExternalBenchmarkSpec(
        benchmark_id="swe-bench-verified",
        name="SWE-bench Verified",
        version_label="Verified 500-instance human-validated subset; pin dataset/evaluator revision per run",
        scope="Real-world software issue resolution",
        official_url="https://github.com/swe-bench/SWE-bench",
        applicability="Applicable only to a configured W1 coding-agent harness and selected model portfolio, not to the W1 runtime alone.",
        execution_boundary="Use official containers/evaluator and record model, provider, budget, repository image, and patch extraction policy.",
        adapter_status="adapter-required",
    ),
    ExternalBenchmarkSpec(
        benchmark_id="claw-swe-bench-lite",
        name="Claw-SWE-Bench Lite",
        version_label="80-instance Lite subset; pin repository commit and dataset release per run",
        scope="Coding-agent harness quality under a fixed workspace/patch/evaluator contract",
        official_url="https://github.com/opensquilla/claw-swe-bench",
        applicability="Strong fit for comparing W1 as an agent harness while controlling the underlying model.",
        execution_boundary="Hold model, prompt, runtime budget, workspace contract, and evaluator fixed when comparing harnesses.",
        adapter_status="adapter-required",
    ),
    ExternalBenchmarkSpec(
        benchmark_id="bfcl-v4",
        name="Berkeley Function Calling Leaderboard V4",
        version_label="BFCL V4; pin upstream commit/release and test subset per run",
        scope="Function/tool calling, multi-turn and agentic tool-use behavior",
        official_url="https://github.com/ShishirPatil/gorilla",
        applicability="Measures the combined model/tool-calling stack; W1 results must name the exact model portfolio and adapter.",
        execution_boundary="Do not attribute model function-calling accuracy to W1 alone; report W1 orchestration and model configuration separately.",
        adapter_status="adapter-required",
    ),
    ExternalBenchmarkSpec(
        benchmark_id="osworld-v2-2026-06-24",
        name="OSWorld 2.0",
        version_label="osworld-v2-2026.06.24",
        scope="Long-horizon real-world computer-use workflows",
        official_url="https://github.com/xlang-ai/OSWorld-V2",
        applicability="Applicable to W1 Computer Use only when paired with a vision/reasoning model and the official environment release.",
        execution_boundary="Use matching task/assets/site/provider-image release components and report safety interventions plus step/token budgets.",
        adapter_status="native-environment-required",
    ),
)


@dataclass(frozen=True)
class ThreatScenario:
    threat_id: str
    boundary: str
    threat: str
    mitigations: tuple[str, ...]
    residual_risk: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


THREAT_SCENARIOS: tuple[ThreatScenario, ...] = (
    ThreatScenario(
        "T01", "Provider/model boundary", "Untrusted model output attempts to trigger privileged or destructive actions.",
        ("Action Runtime policy evaluation", "digest-bound one-time approvals", "review/evidence gates", "bounded tool schemas"),
        "A user can still approve a harmful but correctly described action; policy review remains necessary.",
    ),
    ThreatScenario(
        "T02", "Credential boundary", "API/OAuth secrets leak into logs, databases, prompts, plugins, or command history.",
        ("OS-native vault adapters", "reference-only account/credential identifiers", "redacted hash-chained audit", "no cookie/session-token import"),
        "Native vault security inherits the host account/OS trust model.",
    ),
    ThreatScenario(
        "T03", "Plugin boundary", "Third-party plugin crashes W1 or abuses W1-granted capabilities.",
        ("copy-on-install integrity lock", "explicit permission grants", "subprocess host", "timeout/output bounds", "secret-like environment scrubbing"),
        "The subprocess host is not an OS sandbox for hostile Python; hostile plugins require Secure Execution/VM/OCI isolation.",
    ),
    ThreatScenario(
        "T04", "Computer-use boundary", "Stale UI state causes actions to land on the wrong element or sensitive text enters durable audit.",
        ("selector fingerprints", "pre-action element verification", "ephemeral one-shot input handles", "governed screenshots/UI inspection"),
        "Accessibility-lite adapters may miss semantic changes that full platform accessibility APIs would expose.",
    ),
    ThreatScenario(
        "T05", "Desktop integration boundary", "Deep links or project files are used as command-injection/path-traversal vectors.",
        ("fixed route allowlist", "command/argv/token parameter denial", "relative project descriptors", "path traversal rejection"),
        "Future route expansion must preserve fail-closed parsing and avoid shell interpretation.",
    ),
    ThreatScenario(
        "T06", "Collaboration boundary", "Remote member overwrites state, replays mutations, or tampers with audit history.",
        ("per-key base revisions", "device sequences", "mutation idempotency", "explicit ConflictRecords", "hash-chained audit", "TLS required off-loopback"),
        "Self-hosted operators remain responsible for host security, backups, certificates, and availability.",
    ),
    ThreatScenario(
        "T07", "Update/release boundary", "Tampered or substituted desktop update is published or installed.",
        ("HTTPS-only update metadata", "exact size/SHA-256 verification", "native-signature boundary", "release evidence hashes"),
        "Authenticode/notarization must be verified on real target-platform release infrastructure.",
    ),
    ThreatScenario(
        "T08", "Memory/context boundary", "Cross-project or over-broad context leaks private information between tasks/models.",
        ("least-privilege ContextGrant", "project isolation", "memory ACLs", "provenance/conflict metadata"),
        "Misconfigured grants can still expose data intentionally allowed by the operator.",
    ),
    ThreatScenario(
        "T09", "AI connection endpoint boundary", "A provider credential is redirected to an attacker-controlled endpoint during model onboarding.",
        ("built-in provider hostname lock", "custom providers separated from built-in identities", "HTTPS required for remote custom endpoints", "loopback-only plaintext custom/local endpoints", "Credential Broker references instead of persisted raw secrets"),
        "A user can intentionally configure and trust a custom HTTPS provider; that endpoint receives credentials explicitly assigned to that custom connection.",
    ),
)


def external_benchmark_catalog() -> dict[str, Any]:
    return {
        "catalog_version": "1.0",
        "generated_at": _utc_now(),
        "benchmarks": [item.as_dict() for item in EXTERNAL_BENCHMARKS],
        "score_policy": "Unexecuted benchmarks are reported as NOT_RUN; missing evidence is never converted into a score.",
    }


def benchmark_spec(benchmark_id: str) -> ExternalBenchmarkSpec:
    for item in EXTERNAL_BENCHMARKS:
        if item.benchmark_id == benchmark_id:
            return item
    raise ExternalBenchmarkEvidenceError(f"unknown_external_benchmark:{benchmark_id}")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


BANNED_RELEASE_DIR_PARTS: frozenset[str] = frozenset({
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".git",
    "dist",
    "build",
    "%SystemDrive%",
    "node_modules",
    ".idea",
    ".vscode",
})

BANNED_RELEASE_EXTENSIONS: frozenset[str] = frozenset({
    ".pyc",
    ".pyo",
    ".pyd",
    ".sqlite3",
    ".db",
})


def _tree_manifest(root: Path, *, prefixes: tuple[str, ...] = ("src/", "schemas/", "packaging/", ".github/")) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if root.name != "w1cip" and not rel.startswith(prefixes):
            continue
        if any(part in BANNED_RELEASE_DIR_PARTS for part in path.parts) or path.suffix in BANNED_RELEASE_EXTENSIONS:
            continue
        entries.append({"path": rel, "size": path.stat().st_size, "sha256": sha256_file(path)})
    return entries


def source_manifest(root: str | Path) -> dict[str, Any]:
    selected = Path(root).resolve()
    entries = _tree_manifest(selected)
    digest = hashlib.sha256(_canonical_json(entries).encode("utf-8")).hexdigest()
    return {"manifest_version": "1.0", "root_name": selected.name, "files": entries, "tree_sha256": digest}


def dependency_inventory(root: str | Path) -> dict[str, Any]:
    selected = Path(root).resolve()
    data = tomllib.loads((selected / "pyproject.toml").read_text(encoding="utf-8"))
    declared = list(data.get("project", {}).get("dependencies", []))
    optional = data.get("project", {}).get("optional-dependencies", {})
    installed: list[dict[str, str]] = []
    for requirement in declared:
        name = re.split(r"[<>=!~\[]", requirement, maxsplit=1)[0].strip()
        try:
            version = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            version = "not-installed"
        installed.append({"name": name, "declared": requirement, "installed_version": version})
    return {
        "inventory_version": "1.0",
        "generated_at": _utc_now(),
        "python": os.sys.version.split()[0],
        "direct_dependencies": installed,
        "optional_dependencies": optional,
        "network_vulnerability_database_queried": False,
    }


def _dependency_constraints_bounded(root: Path) -> bool:
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    all_requirements: list[str] = list(data.get("project", {}).get("dependencies", []))
    for values in data.get("project", {}).get("optional-dependencies", {}).values():
        all_requirements.extend(values)
    if not all_requirements:
        return False
    return all(
        any(op in req for op in (">=", "==", "~=")) and any(op in req for op in ("<", "==", "~="))
        for req in all_requirements
    )


def _installed_dependency_constraints_bounded() -> bool:
    try:
        requirements = importlib.metadata.requires("w1-nexus") or []
    except importlib.metadata.PackageNotFoundError:
        return True
    selected = [req.split(";", 1)[0].strip() for req in requirements if req.split(";", 1)[0].strip()]
    if not selected:
        return True
    return all(
        any(op in req for op in (">=", "==", "~=")) and any(op in req for op in ("<", "==", "~="))
        for req in selected
    )


def _private_key_material_absent(root: Path) -> bool:
    marker = b"-----BEGIN " + b"PRIVATE KEY-----"
    rsa = b"-----BEGIN RSA " + b"PRIVATE KEY-----"
    for path in root.rglob("*"):
        if not path.is_file() or any(part in {".git", "__pycache__", "dist", "build"} for part in path.parts):
            continue
        if path.stat().st_size > 2 * 1024 * 1024:
            continue
        try:
            content = path.read_bytes()
        except OSError:
            continue
        if marker in content or rsa in content:
            return False
    return True


def _run_parser_fuzz(seed: int = 41001, cases: int = 400) -> dict[str, Any]:
    rng = random.Random(seed)
    denied = 0
    accepted_safe = 0
    failures: list[str] = []
    denied_keys = ("command", "argv", "token", "secret", "password")
    for index in range(cases):
        if index % 2 == 0:
            key = rng.choice(denied_keys)
            payload = rng.choice(("calc.exe", "../escape", "$(id)", "a;b", "secret-value"))
            uri = f"w1://workspace/open?{key}={payload}"
            try:
                parse_deep_link(uri)
            except DeepLinkDenied:
                denied += 1
            else:
                failures.append(f"sensitive_parameter_accepted:{key}")
        else:
            safe = f"folder-{rng.randrange(10_000)}/file-{rng.randrange(10_000)}.txt"
            uri = f"w1://workspace/open?path={safe}"
            try:
                parsed = parse_deep_link(uri)
                if parsed.parameters.get("path") == safe:
                    accepted_safe += 1
            except Exception as exc:  # pragma: no cover - probe captures unexpected drift
                failures.append(f"safe_path_rejected:{type(exc).__name__}")
    return {
        "seed": seed,
        "cases": cases,
        "sensitive_cases_denied": denied,
        "safe_cases_accepted": accepted_safe,
        "failures": failures,
        "passed": not failures and denied == cases // 2 and accepted_safe == cases // 2,
    }


def _run_descriptor_property_probe(seed: int = 41002, cases: int = 200) -> dict[str, Any]:
    rng = random.Random(seed)
    denied = 0
    safe = 0
    failures: list[str] = []
    for index in range(cases):
        if index % 2 == 0:
            path = rng.choice(("../outside", "a/../../outside", "/absolute", "C:/absolute", "x/../.."))
            try:
                ProjectDescriptor(name="Probe", workspace=path).validate()
            except ProjectDescriptorInvalid:
                denied += 1
            else:
                failures.append(f"traversal_accepted:{path}")
        else:
            path = f"workspace-{rng.randrange(9999)}/project-{rng.randrange(9999)}"
            try:
                ProjectDescriptor(name="Probe", workspace=path).validate()
                safe += 1
            except ProjectDescriptorInvalid:
                failures.append(f"safe_descriptor_rejected:{path}")
    return {"seed": seed, "cases": cases, "traversal_denied": denied, "safe_accepted": safe, "failures": failures, "passed": not failures}


def _run_collaboration_load_recovery(mutations: int = 350) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="w1-hardening-collab-") as directory:
        database = Path(directory) / "collaboration.sqlite3"
        store = CollaborationStore(database)
        try:
            team = store.create_team("Hardening", owner_principal_id="owner", team_id="hardening-team")
            project = store.create_project(team.team_id, "Load", created_by="owner", project_id="hardening-project")
            replica = store.register_replica(team.team_id, principal_id="owner", device_id="hardening-device")
            previous_hash: str | None = None
            started = time.perf_counter()
            last_result: Mapping[str, Any] | None = None
            for index in range(1, mutations + 1):
                mutation = SyncMutation(
                    mutation_id=f"hardening-{index}", project_id=project.project_id, device_id=replica.device_id,
                    sequence=index, base_revision=0, key=f"load/key/{index}", operation="set", value={"n": index},
                    previous_event_hash=previous_hash, created_at="2026-08-08T00:00:00Z",
                ).normalized()
                last_result = store.apply_mutation(mutation, principal_id="owner")
                previous_hash = str(last_result["event_hash"])
            elapsed = time.perf_counter() - started
            state = store.get_state(project.project_id, principal_id="owner")
            audit_before = store.verify_audit(team.team_id)
        finally:
            store.close()

        reopened = CollaborationStore(database)
        try:
            state_after = reopened.get_state(project.project_id, principal_id="owner")
            audit_after = reopened.verify_audit(team.team_id)
            with reopened.connection:
                reopened.connection.execute(
                    "UPDATE audit_events SET details_json='{}' WHERE team_id=? AND ordinal=(SELECT MIN(ordinal) FROM audit_events WHERE team_id=?)",
                    (team.team_id, team.team_id),
                )
            tamper_detected = not reopened.verify_audit(team.team_id)["valid"]
        finally:
            reopened.close()

    throughput = mutations / max(elapsed, 0.000001)
    passed = (
        len(state["values"]) == mutations
        and len(state_after["values"]) == mutations
        and audit_before["valid"] and audit_after["valid"] and tamper_detected
    )
    return {
        "mutations": mutations,
        "elapsed_seconds": round(elapsed, 4),
        "mutations_per_second": round(throughput, 2),
        "state_keys_before_restart": len(state["values"]),
        "state_keys_after_restart": len(state_after["values"]),
        "audit_valid_before_restart": bool(audit_before["valid"]),
        "audit_valid_after_restart": bool(audit_after["valid"]),
        "audit_tamper_detected": tamper_detected,
        "passed": passed,
    }


def run_release_hardening_benchmark(project_root: str | Path | None = None) -> dict[str, Any]:
    requested_root = Path(project_root).resolve() if project_root else None
    source_candidate = Path(__file__).resolve().parents[2]
    if requested_root is not None and (requested_root / "pyproject.toml").is_file():
        root = requested_root
        source_context = True
    elif (source_candidate / "pyproject.toml").is_file():
        root = source_candidate
        source_context = True
    else:
        root = Path(__file__).resolve().parent
        source_context = False
    parser_fuzz = _run_parser_fuzz()
    descriptor_fuzz = _run_descriptor_property_probe()
    collaboration_load = _run_collaboration_load_recovery()
    manifest_one = source_manifest(root)
    manifest_two = source_manifest(root)
    required_docs = ["SECURITY.md", "docs/THREAT-MODEL.md", "docs/EXTERNAL-BENCHMARKS.md", "docs/RELEASE-POLICY.md"]
    brand_identity = verify_brand_identity(root if source_context else None, require_production_assets=False)
    probes = {
        "deep_link_property_fuzz_passed": parser_fuzz["passed"],
        "project_descriptor_property_fuzz_passed": descriptor_fuzz["passed"],
        "collaboration_load_restart_recovery_passed": collaboration_load["passed"],
        "audit_tamper_detection_passed": collaboration_load["audit_tamper_detected"],
        "source_manifest_is_deterministic": manifest_one["tree_sha256"] == manifest_two["tree_sha256"],
        "direct_dependency_ranges_are_bounded": _dependency_constraints_bounded(root) if source_context else _installed_dependency_constraints_bounded(),
        "obvious_private_key_material_absent": _private_key_material_absent(root),
        "security_and_release_docs_present": all((root / item).is_file() for item in required_docs) if source_context else True,
        "brand_release_identity_verified": brand_identity["passed"],
        "external_benchmark_catalog_has_no_synthetic_scores": all(item.score_claim_policy.startswith("No score") for item in EXTERNAL_BENCHMARKS),
    }
    return {
        "hardening_version": HARDENING_VERSION,
        "passed": all(probes.values()),
        "probes": probes,
        "metrics": {
            "probe_count": len(probes),
            "fuzz_cases": parser_fuzz["cases"] + descriptor_fuzz["cases"],
            "load_mutations": collaboration_load["mutations"],
            "load_mutations_per_second": collaboration_load["mutations_per_second"],
            "source_tree_sha256": manifest_one["tree_sha256"],
            "external_benchmark_families_registered": len(EXTERNAL_BENCHMARKS),
            "external_benchmark_scores_claimed": 0,
            "network_calls": 0,
            "source_checkout_context": source_context,
        },
        "parser_fuzz": parser_fuzz,
        "descriptor_fuzz": descriptor_fuzz,
        "collaboration_load_recovery": collaboration_load,
        "limitations": [
            "The deterministic fuzz suite is seeded and local; it does not replace long-running coverage-guided fuzzing.",
            "Dependency inventory does not query an online vulnerability database.",
            "External benchmark catalog entries are NOT_RUN until upstream harnesses are executed and evidence is recorded.",
            "Native signing/notarization and target-platform installer execution require real release hosts and signing identities.",
        ],
    }


class ExternalResultStore:
    """Workspace-local immutable external benchmark result registry."""

    def __init__(self, workspace_root: str | Path) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        self.root = self.workspace_root / ".w1nexus" / "release" / "external-results"
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, result_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,120}", result_id):
            raise ExternalBenchmarkEvidenceError("external_result_id_invalid")
        return self.root / f"{result_id}.json"

    def record(self, result_document: Mapping[str, Any], evidence_path: str | Path) -> dict[str, Any]:
        allowed = {
            "result_id", "benchmark_id", "benchmark_version", "upstream_commit", "adapter_version",
            "executed_at", "system_under_test", "model_configuration", "budget", "metrics", "environment", "notes",
        }
        unknown = set(result_document) - allowed
        if unknown:
            raise ExternalBenchmarkEvidenceError(f"external_result_unknown_fields:{','.join(sorted(unknown))}")
        required = {"result_id", "benchmark_id", "benchmark_version", "upstream_commit", "adapter_version", "executed_at", "system_under_test", "metrics", "environment"}
        missing = required - set(result_document)
        if missing:
            raise ExternalBenchmarkEvidenceError(f"external_result_missing_fields:{','.join(sorted(missing))}")
        spec = benchmark_spec(str(result_document["benchmark_id"]))
        evidence = Path(evidence_path).resolve()
        if not evidence.is_file():
            raise ExternalBenchmarkEvidenceError("external_result_evidence_missing")
        if evidence.stat().st_size > 256 * 1024 * 1024:
            raise ExternalBenchmarkEvidenceError("external_result_evidence_too_large")
        result_id = str(result_document["result_id"])
        target = self._path(result_id)
        if target.exists():
            raise ExternalBenchmarkEvidenceError("external_result_is_immutable")
        metrics = result_document["metrics"]
        if not isinstance(metrics, Mapping) or not metrics:
            raise ExternalBenchmarkEvidenceError("external_result_metrics_object_required")
        if not str(result_document["upstream_commit"]).strip():
            raise ExternalBenchmarkEvidenceError("external_result_upstream_commit_required")
        payload = dict(result_document)
        payload.update({
            "benchmark_name": spec.name,
            "evidence_path": evidence.name,
            "raw_evidence_sha256": sha256_file(evidence),
            "raw_evidence_size": evidence.stat().st_size,
            "recorded_at": _utc_now(),
            "record_format": "w1-external-benchmark-result/1.0",
        })
        serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if len(serialized.encode("utf-8")) > MAX_EXTERNAL_RESULT_BYTES:
            raise ExternalBenchmarkEvidenceError("external_result_document_too_large")
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(serialized, encoding="utf-8")
        temporary.replace(target)
        return payload

    def list(self) -> list[dict[str, Any]]:
        return [json.loads(path.read_text(encoding="utf-8")) for path in sorted(self.root.glob("*.json"))]

    def verify(self, result_id: str, evidence_path: str | Path) -> dict[str, Any]:
        target = self._path(result_id)
        if not target.is_file():
            raise ExternalBenchmarkEvidenceError("external_result_not_found")
        payload = json.loads(target.read_text(encoding="utf-8"))
        evidence = Path(evidence_path).resolve()
        actual = sha256_file(evidence)
        valid = actual == payload.get("raw_evidence_sha256") and evidence.stat().st_size == payload.get("raw_evidence_size")
        return {"result_id": result_id, "valid": valid, "expected_sha256": payload.get("raw_evidence_sha256"), "actual_sha256": actual}


def release_gate(project_root: str | Path, *, public_release: bool = False) -> dict[str, Any]:
    root = Path(project_root).resolve()
    hardening = run_release_hardening_benchmark(root)
    license_present = (root / "LICENSE").is_file() or (root / "LICENSE.txt").is_file()
    brand = verify_brand_identity(root, require_production_assets=False)
    results = ExternalResultStore(root).list() if (root / ".w1nexus" / "release" / "external-results").is_dir() else []
    checks = {
        "hardening_benchmark_passed": hardening["passed"],
        "security_policy_present": (root / "SECURITY.md").is_file(),
        "threat_model_present": (root / "docs" / "THREAT-MODEL.md").is_file(),
        "release_policy_present": (root / "docs" / "RELEASE-POLICY.md").is_file(),
        "external_benchmark_policy_present": (root / "docs" / "EXTERNAL-BENCHMARKS.md").is_file(),
        "public_license_present": license_present,
        "release_identity_verified": brand["passed"],
    }
    blockers: list[str] = []
    if public_release and not license_present:
        blockers.append("public_license_not_selected")
    if public_release and not brand["passed"]:
        blockers.append("release_identity_invalid")
    if not hardening["passed"]:
        blockers.append("hardening_benchmark_failed")
    passed = all(value for key, value in checks.items() if key != "public_license_present") and (license_present or not public_release)
    return {
        "release_policy_version": RELEASE_POLICY_VERSION,
        "public_release_requested": public_release,
        "passed": passed,
        "checks": checks,
        "blockers": blockers,
        "external_results_recorded": len(results),
        "external_results_required_for_competitor_claims": True,
        "public_release_note": (
            "Apache-2.0 release identity detected; native publication still requires target-platform packaging/signing evidence."
            if license_present and brand["passed"] else
            "A public release remains blocked until an intentional license and valid release identity are present."
        ),
        "brand_identity": brand,
        "hardening": hardening,
    }


def render_threat_model_markdown() -> str:
    lines = [
        "# W1 Nexus Threat Model\n",
        f"Threat-model contract version: `{HARDENING_VERSION}`.\n",
        "This document describes trust boundaries, implemented mitigations, and explicit residual risk. It is not a claim that the system is immune to compromise.\n",
        "## Trust assumptions\n",
        "- The local OS account and host kernel are trusted unless execution is delegated to a stronger sandbox/VM boundary.",
        "- Models, remote providers, plugins, MCP servers, collaboration peers, project files, deep links, and external benchmark inputs are treated as untrusted by default.",
        "- Sensitive actions require explicit W1 policy/approval rather than being authorized solely by model output.\n",
        "## Threat register\n",
    ]
    for item in THREAT_SCENARIOS:
        lines.extend([
            f"### {item.threat_id} — {item.boundary}",
            f"**Threat:** {item.threat}",
            "**Mitigations:** " + "; ".join(item.mitigations) + ".",
            f"**Residual risk:** {item.residual_risk}\n",
        ])
    lines.extend([
        "## Release-security boundary\n",
        "W1 distinguishes integrity verification from platform-native signature verification. A SHA-256 match is not represented as Authenticode/notarization success. Public artifacts must preserve that distinction.\n",
        "## External evaluation boundary\n",
        "Internal executable scorecards measure implementation completeness. External benchmark scores are separate evidence and must be backed by an upstream-pinned run plus raw evidence digest.\n",
    ])
    return "\n".join(lines)


__all__ = [
    "HARDENING_VERSION", "RELEASE_POLICY_VERSION", "ExternalBenchmarkSpec", "ExternalResultStore",
    "ExternalBenchmarkEvidenceError", "ThreatScenario", "EXTERNAL_BENCHMARKS", "THREAT_SCENARIOS",
    "benchmark_spec", "dependency_inventory", "external_benchmark_catalog", "release_gate",
    "render_threat_model_markdown", "run_release_hardening_benchmark", "sha256_file", "source_manifest",
]
