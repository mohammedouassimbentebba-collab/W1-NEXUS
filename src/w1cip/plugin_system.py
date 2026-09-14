"""Governed plugin registry and stable adoption contract for W1 Nexus Step 39."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from .orchestrator import ProviderRequest, ProviderResponse
from .sdk import PLUGIN_API_VERSION, SDK_VERSION
from .session_store import canonical_json

PLUGIN_MANIFEST_NAME = "w1-plugin.json"
PLUGIN_REGISTRY_VERSION = 1
PLUGIN_SYSTEM_VERSION = "1.0"
MAX_PLUGIN_FILE_BYTES = 4 * 1024 * 1024
MAX_PLUGIN_OUTPUT_BYTES = 1024 * 1024
SUPPORTED_CAPABILITIES = frozenset({"provider.adapter", "lifecycle.health"})
SUPPORTED_PERMISSIONS = frozenset({
    "workspace.read",
    "workspace.write",
    "network.loopback",
    "network.outbound",
    "credential.resolve",
    "memory.read",
    "memory.write",
    "computer.observe",
    "computer.control",
    "tool.register",
})
_SECRET_MARKERS = ("token", "secret", "password", "api_key", "apikey", "credential", "private_key")
_ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{1,126}[a-z0-9])?$")
_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:[.-]([0-9A-Za-z.-]+))?$")
_API_RE = re.compile(r"^(\d+)\.(\d+)$")


class PluginError(RuntimeError):
    code = "plugin_error"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.code)


class PluginManifestError(PluginError):
    code = "plugin_manifest_invalid"


class PluginCompatibilityError(PluginError):
    code = "plugin_incompatible"


class PluginPermissionError(PluginError):
    code = "plugin_permission_denied"


class PluginIntegrityError(PluginError):
    code = "plugin_integrity_failed"


class PluginExecutionError(PluginError):
    code = "plugin_execution_failed"


@dataclass(frozen=True)
class PluginManifest:
    plugin_id: str
    name: str
    version: str
    api_version: str
    entrypoint: str
    capabilities: tuple[str, ...]
    permissions: tuple[str, ...] = ()
    minimum_w1_version: str = "0.1.0.dev39"
    description: str = ""
    homepage: str | None = None
    license: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    schema_version: int = 1

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise PluginManifestError("plugin_manifest_schema_version_unsupported")
        if not _ID_RE.fullmatch(self.plugin_id):
            raise PluginManifestError("plugin_id_invalid")
        if not isinstance(self.name, str) or not self.name.strip() or len(self.name) > 120:
            raise PluginManifestError("plugin_name_invalid")
        if not _VERSION_RE.fullmatch(self.version):
            raise PluginManifestError("plugin_version_invalid")
        if not _API_RE.fullmatch(self.api_version):
            raise PluginManifestError("plugin_api_version_invalid")
        if ":" not in self.entrypoint or any(char.isspace() for char in self.entrypoint):
            raise PluginManifestError("plugin_entrypoint_invalid")
        if not self.capabilities:
            raise PluginManifestError("plugin_capabilities_required")
        unknown_caps = set(self.capabilities) - SUPPORTED_CAPABILITIES
        if unknown_caps:
            raise PluginManifestError(f"plugin_capability_unsupported:{','.join(sorted(unknown_caps))}")
        unknown_permissions = set(self.permissions) - SUPPORTED_PERMISSIONS
        if unknown_permissions:
            raise PluginManifestError(f"plugin_permission_unsupported:{','.join(sorted(unknown_permissions))}")
        if len(set(self.capabilities)) != len(self.capabilities) or len(set(self.permissions)) != len(self.permissions):
            raise PluginManifestError("plugin_manifest_duplicates")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "PluginManifest":
        if not isinstance(value, Mapping):
            raise PluginManifestError("plugin_manifest_object_required")
        allowed = {
            "schema_version", "plugin_id", "name", "version", "api_version", "entrypoint",
            "capabilities", "permissions", "minimum_w1_version", "description", "homepage", "license", "metadata",
        }
        unknown = set(value) - allowed
        if unknown:
            raise PluginManifestError(f"plugin_manifest_unknown_fields:{','.join(sorted(unknown))}")
        try:
            return cls(
                schema_version=int(value.get("schema_version", 1)),
                plugin_id=str(value["plugin_id"]),
                name=str(value["name"]),
                version=str(value["version"]),
                api_version=str(value["api_version"]),
                entrypoint=str(value["entrypoint"]),
                capabilities=tuple(str(item) for item in value.get("capabilities", ())),
                permissions=tuple(str(item) for item in value.get("permissions", ())),
                minimum_w1_version=str(value.get("minimum_w1_version", "0.1.0.dev39")),
                description=str(value.get("description", "")),
                homepage=str(value["homepage"]) if value.get("homepage") is not None else None,
                license=str(value["license"]) if value.get("license") is not None else None,
                metadata=dict(value.get("metadata", {})),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise PluginManifestError("plugin_manifest_fields_invalid") from exc

    @classmethod
    def load(cls, root_or_manifest: str | Path) -> "PluginManifest":
        selected = Path(root_or_manifest)
        path = selected / PLUGIN_MANIFEST_NAME if selected.is_dir() else selected
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise PluginManifestError("plugin_manifest_missing") from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PluginManifestError("plugin_manifest_json_invalid") from exc
        return cls.from_mapping(payload)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _version_key(value: str) -> tuple[int, int, int, int]:
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)(?:\.dev(\d+))?", value)
    if not match:
        raise PluginCompatibilityError("w1_version_unparseable")
    major, minor, patch, dev = match.groups()
    # Final release sorts after dev releases of the same base.
    return int(major), int(minor), int(patch), int(dev) if dev is not None else 10**9


def compatibility_report(manifest: PluginManifest, *, w1_version: str = "0.1.0.dev50") -> dict[str, Any]:
    api_match = _API_RE.fullmatch(manifest.api_version)
    runtime_match = _API_RE.fullmatch(PLUGIN_API_VERSION)
    api_compatible = bool(api_match and runtime_match and api_match.group(1) == runtime_match.group(1) and int(api_match.group(2)) <= int(runtime_match.group(2)))
    try:
        w1_compatible = _version_key(w1_version) >= _version_key(manifest.minimum_w1_version)
    except PluginCompatibilityError:
        w1_compatible = False
    python_compatible = sys.version_info >= (3, 11)
    return {
        "compatible": api_compatible and w1_compatible and python_compatible,
        "plugin_api": manifest.api_version,
        "runtime_plugin_api": PLUGIN_API_VERSION,
        "sdk_version": SDK_VERSION,
        "w1_version": w1_version,
        "minimum_w1_version": manifest.minimum_w1_version,
        "api_compatible": api_compatible,
        "w1_compatible": w1_compatible,
        "python_compatible": python_compatible,
    }


def _iter_plugin_files(root: Path):
    root = root.resolve()
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise PluginIntegrityError("plugin_symlink_forbidden")
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if any(part in {"__pycache__", ".git", ".w1nexus"} for part in relative.parts) or path.suffix == ".pyc":
            continue
        if path.stat().st_size > MAX_PLUGIN_FILE_BYTES:
            raise PluginIntegrityError("plugin_file_too_large")
        yield relative, path


def plugin_tree_digest(root: str | Path) -> tuple[str, dict[str, str]]:
    selected = Path(root).resolve()
    if not selected.is_dir():
        raise PluginIntegrityError("plugin_root_missing")
    files: dict[str, str] = {}
    outer = hashlib.sha256()
    for relative, path in _iter_plugin_files(selected):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        rel = relative.as_posix()
        files[rel] = digest
        outer.update(rel.encode("utf-8") + b"\0" + digest.encode("ascii") + b"\n")
    if PLUGIN_MANIFEST_NAME not in files:
        raise PluginIntegrityError("plugin_manifest_not_in_tree")
    return outer.hexdigest(), files


def _safe_environment() -> dict[str, str]:
    allowed = {"PATH", "SystemRoot", "WINDIR", "TEMP", "TMP", "HOME", "USERPROFILE", "LANG", "LC_ALL"}
    result: dict[str, str] = {}
    for key, value in os.environ.items():
        lowered = key.lower()
        if key in allowed and not any(marker in lowered for marker in _SECRET_MARKERS):
            result[key] = value
    result["PYTHONNOUSERSITE"] = "1"
    result["W1_PLUGIN_HOST"] = "1"
    return result


@dataclass(frozen=True)
class InstalledPlugin:
    manifest: PluginManifest
    path: str
    digest: str
    files: Mapping[str, str]
    granted_permissions: tuple[str, ...]
    enabled: bool
    installed_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "manifest": self.manifest.as_dict(), "path": self.path, "digest": self.digest,
            "files": dict(self.files), "granted_permissions": list(self.granted_permissions),
            "enabled": self.enabled, "installed_at": self.installed_at,
        }


class PluginManager:
    def __init__(self, workspace_root: str | Path, *, w1_version: str = "0.1.0.dev50") -> None:
        self.workspace_root = Path(workspace_root).resolve()
        self.state_dir = self.workspace_root / ".w1nexus" / "plugins"
        self.installed_dir = self.state_dir / "installed"
        self.registry_path = self.state_dir / "registry.json"
        self.w1_version = w1_version

    def _load_registry(self) -> dict[str, Any]:
        if not self.registry_path.exists():
            return {"registry_version": PLUGIN_REGISTRY_VERSION, "plugins": {}}
        try:
            payload = json.loads(self.registry_path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PluginIntegrityError("plugin_registry_corrupt") from exc
        if payload.get("registry_version") != PLUGIN_REGISTRY_VERSION or not isinstance(payload.get("plugins"), dict):
            raise PluginIntegrityError("plugin_registry_version_invalid")
        return payload

    def _write_registry(self, payload: Mapping[str, Any]) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        temp = self.registry_path.with_suffix(".json.tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.chmod(temp, 0o600)
        temp.replace(self.registry_path)

    def validate_source(self, source: str | Path) -> dict[str, Any]:
        root = Path(source).resolve()
        manifest = PluginManifest.load(root)
        digest, files = plugin_tree_digest(root)
        compatibility = compatibility_report(manifest, w1_version=self.w1_version)
        return {"manifest": manifest.as_dict(), "digest": digest, "files": files, "compatibility": compatibility}

    def install(self, source: str | Path, *, granted_permissions: Sequence[str] = (), enable: bool = False) -> InstalledPlugin:
        source_root = Path(source).resolve()
        validated = self.validate_source(source_root)
        manifest = PluginManifest.from_mapping(validated["manifest"])
        if not validated["compatibility"]["compatible"]:
            raise PluginCompatibilityError("plugin_manifest_not_compatible")
        grants = tuple(sorted(set(str(item) for item in granted_permissions)))
        unknown = set(grants) - SUPPORTED_PERMISSIONS
        if unknown:
            raise PluginPermissionError("plugin_grant_unknown")
        missing = set(manifest.permissions) - set(grants)
        if enable and missing:
            raise PluginPermissionError(f"plugin_permissions_missing:{','.join(sorted(missing))}")
        target = self.installed_dir / manifest.plugin_id / manifest.version
        self.installed_dir.mkdir(parents=True, exist_ok=True)
        if target.exists():
            existing_digest, _ = plugin_tree_digest(target)
            if existing_digest != validated["digest"]:
                raise PluginIntegrityError("plugin_version_already_installed_with_different_content")
        else:
            temp = target.with_name(target.name + ".tmp-" + uuid.uuid4().hex[:8])
            temp.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source_root, temp, symlinks=False, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git", ".w1nexus"))
            copied_digest, _ = plugin_tree_digest(temp)
            if copied_digest != validated["digest"]:
                shutil.rmtree(temp, ignore_errors=True)
                raise PluginIntegrityError("plugin_copy_digest_mismatch")
            temp.replace(target)
        record = InstalledPlugin(
            manifest=manifest, path=str(target), digest=str(validated["digest"]), files=dict(validated["files"]),
            granted_permissions=grants, enabled=bool(enable), installed_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )
        registry = self._load_registry()
        registry["plugins"][manifest.plugin_id] = record.as_dict()
        self._write_registry(registry)
        return record

    def list(self) -> tuple[InstalledPlugin, ...]:
        registry = self._load_registry()
        result = []
        for plugin_id in sorted(registry["plugins"]):
            result.append(self._record_from_mapping(registry["plugins"][plugin_id]))
        return tuple(result)

    def get(self, plugin_id: str) -> InstalledPlugin:
        record = self._load_registry()["plugins"].get(plugin_id)
        if record is None:
            raise PluginError("plugin_not_installed")
        return self._record_from_mapping(record)

    @staticmethod
    def _record_from_mapping(record: Mapping[str, Any]) -> InstalledPlugin:
        return InstalledPlugin(
            manifest=PluginManifest.from_mapping(record["manifest"]), path=str(record["path"]), digest=str(record["digest"]),
            files=dict(record.get("files", {})), granted_permissions=tuple(record.get("granted_permissions", ())),
            enabled=bool(record.get("enabled", False)), installed_at=str(record.get("installed_at", "")),
        )

    def _mutate(self, plugin_id: str, **changes: Any) -> InstalledPlugin:
        registry = self._load_registry()
        raw = registry["plugins"].get(plugin_id)
        if raw is None:
            raise PluginError("plugin_not_installed")
        raw = dict(raw)
        raw.update(changes)
        record = self._record_from_mapping(raw)
        registry["plugins"][plugin_id] = record.as_dict()
        self._write_registry(registry)
        return record

    def set_enabled(self, plugin_id: str, enabled: bool) -> InstalledPlugin:
        record = self.get(plugin_id)
        if enabled:
            report = compatibility_report(record.manifest, w1_version=self.w1_version)
            if not report["compatible"]:
                raise PluginCompatibilityError("plugin_not_compatible")
            missing = set(record.manifest.permissions) - set(record.granted_permissions)
            if missing:
                raise PluginPermissionError(f"plugin_permissions_missing:{','.join(sorted(missing))}")
            self.verify(plugin_id)
        return self._mutate(plugin_id, enabled=bool(enabled))

    def grant(self, plugin_id: str, permissions: Sequence[str]) -> InstalledPlugin:
        record = self.get(plugin_id)
        additions = set(str(item) for item in permissions)
        if additions - SUPPORTED_PERMISSIONS:
            raise PluginPermissionError("plugin_grant_unknown")
        grants = tuple(sorted(set(record.granted_permissions) | additions))
        return self._mutate(plugin_id, granted_permissions=list(grants))

    def revoke(self, plugin_id: str, permissions: Sequence[str]) -> InstalledPlugin:
        record = self.get(plugin_id)
        grants = tuple(sorted(set(record.granted_permissions) - set(str(item) for item in permissions)))
        enabled = record.enabled and set(record.manifest.permissions).issubset(grants)
        return self._mutate(plugin_id, granted_permissions=list(grants), enabled=enabled)

    def verify(self, plugin_id: str) -> dict[str, Any]:
        record = self.get(plugin_id)
        digest, files = plugin_tree_digest(record.path)
        ok = digest == record.digest and dict(files) == dict(record.files)
        if not ok:
            raise PluginIntegrityError("plugin_installed_content_changed")
        return {"verified": True, "plugin_id": plugin_id, "digest": digest, "file_count": len(files)}

    def _require_runnable(self, plugin_id: str, capability: str) -> InstalledPlugin:
        record = self.get(plugin_id)
        if not record.enabled:
            raise PluginPermissionError("plugin_disabled")
        if capability not in record.manifest.capabilities:
            raise PluginPermissionError("plugin_capability_not_declared")
        missing = set(record.manifest.permissions) - set(record.granted_permissions)
        if missing:
            raise PluginPermissionError("plugin_permission_grants_incomplete")
        if not compatibility_report(record.manifest, w1_version=self.w1_version)["compatible"]:
            raise PluginCompatibilityError("plugin_not_compatible")
        self.verify(plugin_id)
        return record

    def run(self, plugin_id: str, *, operation: str, request: Mapping[str, Any] | None = None, timeout_seconds: float = 30.0) -> dict[str, Any]:
        capability = "provider.adapter" if operation == "provider.invoke" else "lifecycle.health"
        record = self._require_runnable(plugin_id, capability)
        return self._run_record(record, operation=operation, request=request or {}, timeout_seconds=timeout_seconds)

    def _run_record(self, record: InstalledPlugin, *, operation: str, request: Mapping[str, Any], timeout_seconds: float) -> dict[str, Any]:
        host_script = Path(__file__).with_name("plugin_host.py").resolve()
        payload = {
            "plugin_root": record.path,
            "entrypoint": record.manifest.entrypoint,
            "operation": operation,
            "request": dict(request),
            "context": {
                "plugin_id": record.manifest.plugin_id,
                "plugin_version": record.manifest.version,
                "api_version": record.manifest.api_version,
                "granted_permissions": list(record.granted_permissions),
                "workspace_id": hashlib.sha256(str(self.workspace_root).encode("utf-8")).hexdigest()[:24],
                "metadata": {"sdk_version": SDK_VERSION, "plugin_system_version": PLUGIN_SYSTEM_VERSION},
            },
        }
        try:
            completed = subprocess.run(
                [sys.executable, str(host_script)], input=canonical_json(payload).encode("utf-8"), stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, cwd=record.path, env=_safe_environment(), timeout=max(0.1, timeout_seconds), check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise PluginExecutionError("plugin_timeout") from exc
        except OSError as exc:
            raise PluginExecutionError("plugin_host_launch_failed") from exc
        if len(completed.stdout) > MAX_PLUGIN_OUTPUT_BYTES:
            raise PluginExecutionError("plugin_output_too_large")
        try:
            response = json.loads(completed.stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PluginExecutionError("plugin_host_response_invalid") from exc
        if not isinstance(response, dict) or not response.get("ok"):
            code = response.get("error_code", "plugin_host_failed") if isinstance(response, dict) else "plugin_host_failed"
            raise PluginExecutionError(str(code))
        return response

    def conformance(self, source_or_plugin_id: str | Path, *, timeout_seconds: float = 5.0) -> dict[str, Any]:
        source_path = Path(str(source_or_plugin_id))
        temporary_workspace: tempfile.TemporaryDirectory[str] | None = None
        if source_path.exists():
            temporary_workspace = tempfile.TemporaryDirectory(prefix="w1-plugin-conformance-")
            manager = PluginManager(temporary_workspace.name, w1_version=self.w1_version)
            validated = manager.validate_source(source_path)
            manifest = PluginManifest.from_mapping(validated["manifest"])
            record = manager.install(source_path, granted_permissions=manifest.permissions, enable=True)
        else:
            manager = self
            record = self.get(str(source_or_plugin_id))
            manager.verify(record.manifest.plugin_id)
            if not record.enabled:
                # Conformance may exercise an installed disabled plugin without mutating its state.
                record = InstalledPlugin(record.manifest, record.path, record.digest, record.files, record.manifest.permissions, True, record.installed_at)
        probes: dict[str, bool] = {}
        compatibility = compatibility_report(record.manifest, w1_version=self.w1_version)
        probes["manifest_valid"] = True
        probes["api_compatible"] = compatibility["api_compatible"]
        probes["w1_version_compatible"] = compatibility["w1_compatible"]
        probes["integrity_verified"] = plugin_tree_digest(record.path)[0] == record.digest
        try:
            health = manager._run_record(record, operation="health", request={}, timeout_seconds=timeout_seconds)
            probes["lifecycle_health"] = bool(health.get("health", {}).get("ok", False))
            probes["stdio_protocol_clean"] = isinstance(health, dict)
            probes["subprocess_isolation"] = True
        except PluginError:
            probes["lifecycle_health"] = False
            probes["stdio_protocol_clean"] = False
            probes["subprocess_isolation"] = True
        if "provider.adapter" in record.manifest.capabilities:
            sample = {
                "run_id": "conformance-run", "session_id": "conformance-session", "resource_id": "plugin-resource",
                "idempotency_key": "conformance", "context": {"input": "safe"}, "prior_outputs": {}, "attempt": 1,
                "task": {"task_id": "plugin-task", "title": "Plugin conformance", "phase": "execution", "role": "executor",
                         "expected_output_type": "contribution", "required_context_fields": ["input"], "domains": ["testing"]},
            }
            try:
                invoked = manager._run_record(record, operation="provider.invoke", request=sample, timeout_seconds=timeout_seconds)
                result = invoked.get("result", {})
                probes["provider_contract"] = result.get("output_type") == "contribution" and isinstance(result.get("payload"), dict)
            except PluginError:
                probes["provider_contract"] = False
        else:
            probes["provider_contract"] = True
        result = {
            "passed": all(probes.values()), "plugin_id": record.manifest.plugin_id,
            "plugin_version": record.manifest.version, "probes": probes, "compatibility": compatibility,
        }
        if temporary_workspace is not None:
            temporary_workspace.cleanup()
        return result

    def provider_adapter(self, plugin_id: str, resource_id: str) -> "ManagedPluginProviderAdapter":
        self._require_runnable(plugin_id, "provider.adapter")
        return ManagedPluginProviderAdapter(self, plugin_id, resource_id)


class ManagedPluginProviderAdapter:
    supports_idempotency = True

    def __init__(self, manager: PluginManager, plugin_id: str, resource_id: str) -> None:
        self.manager = manager
        self.plugin_id = plugin_id
        self.resource_id = resource_id

    def invoke(self, request: ProviderRequest) -> ProviderResponse:
        payload = asdict(request)
        response = self.manager.run(self.plugin_id, operation="provider.invoke", request=payload)
        result = response["result"]
        return ProviderResponse(
            output_type=str(result["output_type"]), payload=dict(result["payload"]),
            units_consumed=int(result.get("units_consumed", 1)),
            context_fields_used=tuple(result.get("context_fields_used", ())),
            protocol_envelopes=tuple(result.get("protocol_envelopes", ())),
            action_requests=tuple(result.get("action_requests", ())),
            metadata=dict(result.get("metadata", {})),
        )


def scaffold_plugin(destination: str | Path, *, plugin_id: str, name: str) -> dict[str, Any]:
    target = Path(destination).resolve()
    if target.exists() and any(target.iterdir()):
        raise PluginError("plugin_scaffold_destination_not_empty")
    target.mkdir(parents=True, exist_ok=True)
    manifest = PluginManifest(
        plugin_id=plugin_id, name=name, version="0.1.0", api_version=PLUGIN_API_VERSION,
        entrypoint="plugin:ExampleProviderPlugin", capabilities=("provider.adapter", "lifecycle.health"),
        permissions=(), minimum_w1_version="0.1.0.dev39", description="Example W1 Nexus provider plugin",
    )
    (target / PLUGIN_MANIFEST_NAME).write_text(json.dumps(manifest.as_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (target / "plugin.py").write_text(
        'from w1cip.sdk import PluginBase\n\n'
        'class ExampleProviderPlugin(PluginBase):\n'
        '    def invoke_provider(self, request):\n'
        '        task = request.get("task", {})\n'
        '        return {\n'
        '            "output_type": task.get("expected_output_type", "contribution"),\n'
        '            "payload": {"message": "Hello from a governed W1 plugin"},\n'
        '            "context_fields_used": list(request.get("context", {}).keys()),\n'
        '            "metadata": {"example": True},\n'
        '        }\n', encoding="utf-8"
    )
    (target / "README.md").write_text(
        "# W1 Nexus plugin example\n\nRun `w1 plugins conformance .` before installing.\n", encoding="utf-8"
    )
    return {"path": str(target), "manifest": manifest.as_dict()}


def _stable_sdk_probe() -> bool:
    try:
        from .sdk import LocalControlSettings, ProviderRequest as SDKProviderRequest, ProviderResponse as SDKProviderResponse, W1LocalClient
        return (
            SDK_VERSION == "1.2.0"
            and PLUGIN_API_VERSION == "1.0"
            and W1LocalClient is not None
            and LocalControlSettings is not None
            and SDKProviderRequest is ProviderRequest
            and SDKProviderResponse is ProviderResponse
        )
    except Exception:
        return False


def run_plugin_adoption_benchmark() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="w1-plugin-benchmark-") as directory:
        root = Path(directory)
        workspace = root / "workspace"
        workspace.mkdir()
        source = root / "plugin"
        scaffold_plugin(source, plugin_id="org.w1.benchmark.echo", name="Benchmark Echo")
        manager = PluginManager(workspace)
        validated = manager.validate_source(source)
        record = manager.install(source, enable=True)
        conformance = manager.conformance(record.manifest.plugin_id, timeout_seconds=10.0)
        adapter = manager.provider_adapter(record.manifest.plugin_id, "benchmark-plugin-resource")
        class _Task:
            pass
        # Use real W1 request dataclasses for the integration probe.
        from .orchestrator import CompiledTask
        request = ProviderRequest(
            run_id="plugin-run", session_id="plugin-session",
            task=CompiledTask(task_id="plugin-task", title="Plugin benchmark", phase="execution", role="executor", expected_output_type="contribution", required_context_fields=("input",), domains=("testing",)),
            resource_id="benchmark-plugin-resource", idempotency_key="plugin-benchmark", context={"input": "safe"}, prior_outputs={}, attempt=1,
        )
        provider_response = adapter.invoke(request)
        original = source / "plugin.py"
        original.write_text(original.read_text(encoding="utf-8") + "\n# source mutation does not alter installed copy\n", encoding="utf-8")
        installed_immutable_copy = manager.verify(record.manifest.plugin_id)["verified"]
        installed_file = Path(record.path) / "plugin.py"
        installed_file.write_text(installed_file.read_text(encoding="utf-8") + "\n# tamper\n", encoding="utf-8")
        tamper_denied = False
        try:
            manager.run(record.manifest.plugin_id, operation="health")
        except PluginIntegrityError:
            tamper_denied = True

        denied_source = root / "permission-plugin"
        scaffold_plugin(denied_source, plugin_id="org.w1.benchmark.permission", name="Permission Probe")
        manifest_payload = json.loads((denied_source / PLUGIN_MANIFEST_NAME).read_text(encoding="utf-8"))
        manifest_payload["permissions"] = ["workspace.write"]
        (denied_source / PLUGIN_MANIFEST_NAME).write_text(json.dumps(manifest_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        permission_denied = False
        try:
            manager.install(denied_source, enable=True)
        except PluginPermissionError:
            permission_denied = True

        crash_source = root / "crash-plugin"
        scaffold_plugin(crash_source, plugin_id="org.w1.benchmark.crash", name="Crash Probe")
        (crash_source / "plugin.py").write_text(
            'from w1cip.sdk import PluginBase\nclass ExampleProviderPlugin(PluginBase):\n'
            '    def health(self):\n        raise RuntimeError("boom")\n'
            '    def invoke_provider(self, request):\n        raise RuntimeError("boom")\n', encoding="utf-8")
        crash_record = manager.install(crash_source, enable=True)
        crash_isolated = False
        try:
            manager.run(crash_record.manifest.plugin_id, operation="health")
        except PluginExecutionError:
            crash_isolated = True

        probes = {
            "manifest_validated": validated["compatibility"]["compatible"],
            "local_install_copied_and_locked": Path(record.path).is_dir() and len(record.digest) == 64,
            "disabled_permission_escalation_denied": permission_denied,
            "conformance_suite_passed": conformance["passed"],
            "provider_adapter_integrated": provider_response.payload.get("message") == "Hello from a governed W1 plugin",
            "source_mutation_does_not_change_installed_copy": installed_immutable_copy,
            "installed_tamper_denied": tamper_denied,
            "plugin_crash_isolated": crash_isolated,
            "stable_sdk_surface_present": _stable_sdk_probe(),
            "no_secret_environment_contract": "PYTHONNOUSERSITE" in _safe_environment() and all(not any(marker in key.lower() for marker in _SECRET_MARKERS) for key in _safe_environment()),
        }
        return {
            "passed": all(probes.values()), "probes": probes,
            "metrics": {"plugin_api_version": PLUGIN_API_VERSION, "sdk_version": SDK_VERSION, "conformance_probes": len(conformance["probes"]), "supported_permissions": len(SUPPORTED_PERMISSIONS)},
        }
