"""Native desktop packaging contracts for W1 Nexus.

This module keeps packaging separate from the W1 runtime.  It provides:
- stable application identity and OS integration metadata,
- safe deep-link and project-descriptor parsing,
- a loopback desktop-service lifecycle with PID-reuse checks,
- integrity-first update manifests,
- deterministic Windows packaging sources and build diagnostics.

The module deliberately does *not* claim that an installer is signed merely
because signing metadata or hooks exist.  Platform signing must be performed by
native build infrastructure and verified independently.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.parse
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .brand_identity import verify_brand_identity

NATIVE_PACKAGING_VERSION = "0.1.0-dev50"
APP_ID = "com.w1.nexus"
PRODUCT_NAME = "W1 Nexus"
PUBLISHER_NAME = "W1 Nexus Project"
URL_SCHEME = "w1"
PROJECT_EXTENSION = ".w1nexus"
PROJECT_DESCRIPTOR_VERSION = 1
UPDATE_MANIFEST_VERSION = 1


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class NativePackagingError(RuntimeError):
    code = "native_packaging_error"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


class DeepLinkDenied(NativePackagingError):
    code = "native_deep_link_denied"


class ProjectDescriptorInvalid(NativePackagingError):
    code = "native_project_descriptor_invalid"


class UpdateVerificationError(NativePackagingError):
    code = "native_update_verification_failed"


class ServiceLifecycleError(NativePackagingError):
    code = "native_service_lifecycle_error"


@dataclass(frozen=True)
class AppIdentity:
    app_id: str = APP_ID
    product_name: str = PRODUCT_NAME
    publisher: str = PUBLISHER_NAME
    executable_name: str = "W1 Nexus.exe"
    url_scheme: str = URL_SCHEME
    project_extension: str = PROJECT_EXTENSION

    def validate(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9]*(?:\.[a-z0-9-]+)+", self.app_id):
            raise NativePackagingError("invalid_reverse_dns_app_id", code="native_app_identity_invalid")
        if not re.fullmatch(r"[a-z][a-z0-9+.-]{1,31}", self.url_scheme):
            raise NativePackagingError("invalid_url_scheme", code="native_app_identity_invalid")
        if not re.fullmatch(r"\.[a-z0-9][a-z0-9.-]{1,30}", self.project_extension):
            raise NativePackagingError("invalid_project_extension", code="native_app_identity_invalid")

    def as_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)


@dataclass(frozen=True)
class DeepLinkAction:
    area: str
    action: str
    parameters: Mapping[str, str]

    def as_dict(self) -> dict[str, Any]:
        return {"area": self.area, "action": self.action, "parameters": dict(self.parameters)}


_ALLOWED_DEEP_LINKS: dict[str, set[str]] = {
    "workspace": {"open"},
    "artifact": {"open"},
    "settings": {"open"},
    "account": {"open"},
}
_DENIED_QUERY_KEYS = {"command", "cmd", "argv", "shell", "powershell", "script", "token", "secret", "password", "credential", "api_key", "apikey", "access_token", "refresh_token", "bearer"}


def _safe_relative_ui_path(value: str) -> str:
    normalized = value.replace("\\", "/").strip()
    if not normalized or normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
        raise DeepLinkDenied("absolute_or_empty_workspace_path_denied")
    parts = [part for part in normalized.split("/") if part not in {"", "."}]
    if any(part == ".." for part in parts):
        raise DeepLinkDenied("workspace_path_traversal_denied")
    result = "/".join(parts)
    if re.search(r"%[0-9A-Fa-f]{2}", result):
        raise DeepLinkDenied("workspace_path_double_encoding_denied")
    return result


def parse_deep_link(uri: str) -> DeepLinkAction:
    if len(uri) > 4096:
        raise DeepLinkDenied("deep_link_too_long")
    parsed = urllib.parse.urlsplit(uri)
    if parsed.scheme.lower() != URL_SCHEME:
        raise DeepLinkDenied("deep_link_scheme_denied")
    if parsed.fragment:
        raise DeepLinkDenied("deep_link_fragment_denied")
    try:
        parsed_port = parsed.port
    except ValueError as exc:
        raise DeepLinkDenied("deep_link_port_invalid") from exc
    if parsed.username or parsed.password or parsed_port:
        raise DeepLinkDenied("deep_link_authority_credentials_denied")
    area = parsed.hostname or ""
    action = parsed.path.strip("/") or "open"
    if area not in _ALLOWED_DEEP_LINKS or action not in _ALLOWED_DEEP_LINKS[area]:
        raise DeepLinkDenied("deep_link_route_denied")
    try:
        query = urllib.parse.parse_qs(parsed.query, keep_blank_values=False, strict_parsing=False, max_num_fields=16)
    except ValueError as exc:
        raise DeepLinkDenied("deep_link_query_invalid") from exc
    if any(key.lower() in _DENIED_QUERY_KEYS for key in query):
        raise DeepLinkDenied("deep_link_sensitive_or_command_parameter_denied")
    if any(len(values) != 1 for values in query.values()):
        raise DeepLinkDenied("deep_link_duplicate_parameter_denied")
    parameters = {key: values[0] for key, values in query.items()}
    if area == "workspace" and "path" in parameters:
        parameters["path"] = _safe_relative_ui_path(parameters["path"])
    for key, value in parameters.items():
        if len(key) > 64 or len(value) > 2048 or "\x00" in value:
            raise DeepLinkDenied("deep_link_parameter_invalid")
    return DeepLinkAction(area=area, action=action, parameters=parameters)


@dataclass(frozen=True)
class ProjectDescriptor:
    name: str
    workspace: str = "."
    version: int = PROJECT_DESCRIPTOR_VERSION
    open_target: str | None = None

    def validate(self) -> None:
        if self.version != PROJECT_DESCRIPTOR_VERSION:
            raise ProjectDescriptorInvalid("unsupported_project_descriptor_version")
        if not self.name.strip() or len(self.name) > 160:
            raise ProjectDescriptorInvalid("project_name_invalid")
        selected = self.workspace.replace("\\", "/")
        if selected.startswith("/") or re.match(r"^[A-Za-z]:", selected):
            raise ProjectDescriptorInvalid("project_workspace_must_be_relative")
        parts = [part for part in selected.split("/") if part not in {"", "."}]
        if any(part == ".." for part in parts):
            raise ProjectDescriptorInvalid("project_workspace_traversal_denied")
        if self.open_target is not None:
            _safe_relative_ui_path(self.open_target)

    def as_dict(self) -> dict[str, Any]:
        self.validate()
        payload: dict[str, Any] = {
            "descriptor_version": self.version,
            "name": self.name,
            "workspace": self.workspace,
        }
        if self.open_target is not None:
            payload["open_target"] = self.open_target
        return payload

    def write(self, path: str | Path) -> Path:
        selected = Path(path)
        if selected.suffix.lower() != PROJECT_EXTENSION:
            raise ProjectDescriptorInvalid(f"project_descriptor_extension_required:{PROJECT_EXTENSION}")
        selected.parent.mkdir(parents=True, exist_ok=True)
        temporary = selected.with_suffix(selected.suffix + ".tmp")
        temporary.write_text(json.dumps(self.as_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(selected)
        return selected

    @classmethod
    def load(cls, path: str | Path) -> "ProjectDescriptor":
        selected = Path(path)
        if selected.stat().st_size > 64 * 1024:
            raise ProjectDescriptorInvalid("project_descriptor_too_large")
        try:
            payload = json.loads(selected.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ProjectDescriptorInvalid("project_descriptor_unreadable") from exc
        if not isinstance(payload, dict):
            raise ProjectDescriptorInvalid("project_descriptor_object_required")
        descriptor = cls(
            name=str(payload.get("name") or ""),
            workspace=str(payload.get("workspace") or "."),
            version=int(payload.get("descriptor_version", 0)),
            open_target=str(payload["open_target"]) if payload.get("open_target") is not None else None,
        )
        descriptor.validate()
        return descriptor

    def resolve_workspace(self, descriptor_path: str | Path) -> Path:
        self.validate()
        base = Path(descriptor_path).resolve().parent
        resolved = (base / self.workspace).resolve()
        try:
            resolved.relative_to(base)
        except ValueError as exc:
            raise ProjectDescriptorInvalid("project_workspace_escape_denied") from exc
        return resolved


@dataclass(frozen=True)
class UpdateArtifact:
    platform: str
    architecture: str
    version: str
    url: str
    size: int
    sha256: str
    signature_kind: str | None = None
    signer_identity: str | None = None

    def validate(self, *, require_signature_metadata: bool = True) -> None:
        parsed = urllib.parse.urlsplit(self.url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise UpdateVerificationError("update_url_must_be_https")
        if self.size < 1:
            raise UpdateVerificationError("update_artifact_size_invalid")
        if not re.fullmatch(r"[0-9a-f]{64}", self.sha256):
            raise UpdateVerificationError("update_sha256_invalid")
        if require_signature_metadata and (not self.signature_kind or not self.signer_identity):
            raise UpdateVerificationError("update_signature_metadata_required")
        if self.signature_kind not in {None, "authenticode", "codesign", "gpg", "minisign"}:
            raise UpdateVerificationError("update_signature_kind_unsupported")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class UpdateManifest:
    channel: str
    published_at: str
    artifacts: tuple[UpdateArtifact, ...]
    version: int = UPDATE_MANIFEST_VERSION

    def validate(self, *, require_signature_metadata: bool = True) -> None:
        if self.version != UPDATE_MANIFEST_VERSION:
            raise UpdateVerificationError("update_manifest_version_unsupported")
        if self.channel not in {"stable", "beta", "dev"}:
            raise UpdateVerificationError("update_channel_invalid")
        if not self.artifacts:
            raise UpdateVerificationError("update_artifacts_required")
        for artifact in self.artifacts:
            artifact.validate(require_signature_metadata=require_signature_metadata)

    def as_dict(self) -> dict[str, Any]:
        self.validate(require_signature_metadata=False)
        return {
            "manifest_version": self.version,
            "channel": self.channel,
            "published_at": self.published_at,
            "artifacts": [artifact.as_dict() for artifact in self.artifacts],
        }

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "UpdateManifest":
        raw_artifacts = payload.get("artifacts")
        if not isinstance(raw_artifacts, list):
            raise UpdateVerificationError("update_artifacts_array_required")
        artifacts = tuple(
            UpdateArtifact(
                platform=str(item["platform"]), architecture=str(item["architecture"]),
                version=str(item["version"]), url=str(item["url"]), size=int(item["size"]),
                sha256=str(item["sha256"]),
                signature_kind=str(item["signature_kind"]) if item.get("signature_kind") else None,
                signer_identity=str(item["signer_identity"]) if item.get("signer_identity") else None,
            )
            for item in raw_artifacts if isinstance(item, Mapping)
        )
        manifest = cls(
            channel=str(payload.get("channel") or ""),
            published_at=str(payload.get("published_at") or ""),
            artifacts=artifacts,
            version=int(payload.get("manifest_version", 0)),
        )
        manifest.validate(require_signature_metadata=False)
        return manifest

    def select(self, target_platform: str, architecture: str) -> UpdateArtifact:
        matches = [item for item in self.artifacts if item.platform == target_platform and item.architecture == architecture]
        if len(matches) != 1:
            raise UpdateVerificationError("update_artifact_selection_not_unique")
        return matches[0]


def verify_update_artifact(
    artifact: UpdateArtifact,
    path: str | Path,
    *,
    require_signature_metadata: bool = True,
) -> dict[str, Any]:
    artifact.validate(require_signature_metadata=require_signature_metadata)
    selected = Path(path)
    actual_size = selected.stat().st_size
    actual_hash = sha256_file(selected)
    if actual_size != artifact.size:
        raise UpdateVerificationError("update_size_mismatch")
    if actual_hash != artifact.sha256:
        raise UpdateVerificationError("update_sha256_mismatch")
    return {
        "integrity_verified": True,
        "sha256": actual_hash,
        "size": actual_size,
        "signature_metadata_present": bool(artifact.signature_kind and artifact.signer_identity),
        "native_signature_verification_required": bool(artifact.signature_kind),
        "native_signature_verified": False,
    }


@dataclass(frozen=True)
class ServiceState:
    pid: int
    process_marker: str
    port: int
    started_at: str
    command_sha256: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _windows_process_marker(pid: int) -> str | None:
    if os.name != "nt":
        return None
    from ctypes import wintypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetProcessTimes.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME),
    ]
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        creation = wintypes.FILETIME()
        exit_time = wintypes.FILETIME()
        kernel = wintypes.FILETIME()
        user = wintypes.FILETIME()
        if not kernel32.GetProcessTimes(handle, ctypes.byref(creation), ctypes.byref(exit_time), ctypes.byref(kernel), ctypes.byref(user)):
            return None
        exit_value = (int(exit_time.dwHighDateTime) << 32) | int(exit_time.dwLowDateTime)
        if exit_value != 0:
            return None
        exit_code = wintypes.DWORD()
        STILL_ACTIVE = 259
        if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)) and exit_code.value != STILL_ACTIVE:
            return None
        creation_value = (int(creation.dwHighDateTime) << 32) | int(creation.dwLowDateTime)
        return f"win-filetime:{creation_value}"
    finally:
        kernel32.CloseHandle(handle)


def process_marker(pid: int) -> str | None:
    if pid <= 0:
        return None
    if os.name == "nt":
        return _windows_process_marker(pid)
    proc = Path(f"/proc/{pid}")
    if proc.exists():
        try:
            raw_stat = (proc / "stat").read_text(encoding="utf-8", errors="replace")
            close_paren = raw_stat.rfind(")")
            if close_paren >= 0:
                fields_after_comm = raw_stat[close_paren + 1 :].strip().split()
                if fields_after_comm and fields_after_comm[0] == "Z":
                    return None
            stat = proc.stat()
            return f"proc-ctime:{stat.st_ctime_ns}"
        except OSError:
            return None
    try:
        os.kill(pid, 0)
    except OSError:
        return None
    return f"pid-only:{pid}"


class NativeServiceController:
    """Own the loopback Desktop service without storing command-line secrets."""

    def __init__(self, workspace_root: str | Path) -> None:
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        self.state_dir = self.workspace_root / ".w1nexus"
        self.state_path = self.state_dir / "native-service.json"
        self._owned_processes: dict[int, subprocess.Popen[Any]] = {}

    def _write_state(self, state: ServiceState) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        data = (json.dumps(state.as_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
        fd = os.open(self.state_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)
        try:
            os.chmod(self.state_path, 0o600)
        except OSError:
            pass

    def _read_state(self) -> ServiceState | None:
        if not self.state_path.is_file():
            return None
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
            return ServiceState(
                pid=int(payload["pid"]), process_marker=str(payload["process_marker"]),
                port=int(payload["port"]), started_at=str(payload["started_at"]),
                command_sha256=str(payload["command_sha256"]),
            )
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ServiceLifecycleError("native_service_state_invalid") from exc

    def status(self) -> dict[str, Any]:
        state = self._read_state()
        if state is None:
            return {"running": False, "state_path": str(self.state_path)}
        marker = process_marker(state.pid)
        running = marker is not None and marker == state.process_marker
        return {
            "running": running,
            "pid": state.pid,
            "port": state.port,
            "started_at": state.started_at,
            "process_identity_matches": marker == state.process_marker if marker is not None else False,
            "state_path": str(self.state_path),
        }

    def start(self, *, port: int = 8766, command: Sequence[str] | None = None) -> ServiceState:
        if not 1 <= int(port) <= 65535:
            raise ServiceLifecycleError("native_service_port_invalid")
        existing = self.status()
        if existing.get("running"):
            raise ServiceLifecycleError("native_service_already_running")
        # If there is a stale state file whose process is dead, clean it up before starting
        if self.state_path.is_file() and not existing.get("running"):
            self.state_path.unlink(missing_ok=True)
        selected_command = list(command) if command is not None else [
            sys.executable, "-m", "w1cip", "--workspace", str(self.workspace_root),
            "desktop", "serve", "--host", "127.0.0.1", "--port", str(port), "--no-browser",
        ]
        if not selected_command or any("\x00" in str(part) for part in selected_command):
            raise ServiceLifecycleError("native_service_command_invalid")
        popen_kwargs: dict[str, Any] = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "cwd": str(self.workspace_root),
            "close_fds": True,
        }
        if os.name == "nt":
            popen_kwargs["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        else:
            popen_kwargs["start_new_session"] = True
        process = subprocess.Popen(selected_command, **popen_kwargs)
        marker: str | None = None
        for _ in range(50):
            if process.poll() is not None:
                raise ServiceLifecycleError("native_service_exited_during_start")
            marker = process_marker(process.pid)
            if marker is not None:
                break
            time.sleep(0.02)
        if marker is None:
            try:
                process.terminate()
            except OSError:
                pass
            raise ServiceLifecycleError("native_service_identity_unavailable")
        state = ServiceState(
            pid=process.pid,
            process_marker=marker,
            port=int(port),
            started_at=utc_now(),
            command_sha256=sha256_bytes(canonical_json(selected_command).encode()),
        )
        self._owned_processes[process.pid] = process
        self._write_state(state)
        return state

    def stop(self, *, timeout_seconds: float = 5.0) -> dict[str, Any]:
        state = self._read_state()
        if state is None:
            return {"stopped": False, "reason": "not_running"}
        marker = process_marker(state.pid)
        if marker is None:
            self.state_path.unlink(missing_ok=True)
            return {"stopped": False, "reason": "stale_state_removed"}
        if marker != state.process_marker:
            raise ServiceLifecycleError("native_service_pid_reuse_or_identity_mismatch")
        owned = self._owned_processes.get(state.pid)
        try:
            if owned is not None:
                owned.terminate()
            else:
                os.kill(state.pid, signal.SIGTERM)
        except OSError as exc:
            raise ServiceLifecycleError("native_service_stop_failed") from exc
        deadline = time.monotonic() + max(timeout_seconds, 0.1)
        escalate_threshold = deadline - max(timeout_seconds * 0.3, 0.2)
        while time.monotonic() < deadline:
            if owned is not None:
                owned.poll()
            if process_marker(state.pid) is None:
                if owned is not None:
                    try:
                        owned.wait(timeout=0.2)
                    except (subprocess.TimeoutExpired, OSError):
                        pass
                    self._owned_processes.pop(state.pid, None)
                self.state_path.unlink(missing_ok=True)
                return {"stopped": True, "pid": state.pid}
            if time.monotonic() > escalate_threshold:
                if owned is not None:
                    try:
                        owned.kill()
                    except OSError:
                        pass
                elif os.name != "nt":
                    try:
                        os.kill(state.pid, signal.SIGKILL)
                    except OSError:
                        pass
            time.sleep(0.05)
        # Final check if process died during timeout
        if owned is not None:
            try:
                owned.kill()
                owned.wait(timeout=0.2)
            except (subprocess.TimeoutExpired, OSError):
                pass
        if process_marker(state.pid) is None:
            self._owned_processes.pop(state.pid, None)
            self.state_path.unlink(missing_ok=True)
            return {"stopped": True, "pid": state.pid}
        raise ServiceLifecycleError("native_service_stop_timeout")


@dataclass(frozen=True)
class NativeBuildPlan:
    target: str
    architecture: str
    version: str
    host_platform: str
    tools: Mapping[str, str | None]
    can_compile_native_installer_here: bool
    signing_configured: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "architecture": self.architecture,
            "version": self.version,
            "host_platform": self.host_platform,
            "tools": dict(self.tools),
            "can_compile_native_installer_here": self.can_compile_native_installer_here,
            "signing_configured": self.signing_configured,
        }


def native_build_plan(*, target: str = "windows", architecture: str = "x64", version: str = NATIVE_PACKAGING_VERSION) -> NativeBuildPlan:
    if target not in {"windows", "macos", "linux"}:
        raise NativePackagingError("native_build_target_invalid")
    tools = {
        "pyinstaller": shutil.which("pyinstaller"),
        "inno_setup": shutil.which("iscc") or shutil.which("ISCC.exe"),
        "signtool": shutil.which("signtool") or shutil.which("signtool.exe"),
        "codesign": shutil.which("codesign"),
        "notarytool": shutil.which("notarytool"),
        "dpkg_deb": shutil.which("dpkg-deb"),
    }
    host = platform.system().lower()
    can_compile = False
    if target == "windows":
        can_compile = host == "windows" and bool(tools["pyinstaller"] and tools["inno_setup"])
    elif target == "macos":
        can_compile = host == "darwin" and bool(tools["pyinstaller"] and tools["codesign"])
    elif target == "linux":
        can_compile = host == "linux" and bool(tools["pyinstaller"] and tools["dpkg_deb"])
    signing_configured = bool(os.environ.get("W1_WINDOWS_SIGN_CERT_SHA1") or os.environ.get("W1_MACOS_SIGN_IDENTITY"))
    return NativeBuildPlan(
        target=target, architecture=architecture, version=version,
        host_platform=host, tools=tools,
        can_compile_native_installer_here=can_compile,
        signing_configured=signing_configured,
    )


def _windows_guid(identity: AppIdentity) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"https://{identity.app_id}/installer")).upper()


def _windows_registry_script(identity: AppIdentity, *, unregister: bool = False) -> str:
    exe = identity.executable_name
    if unregister:
        return f'''$ErrorActionPreference = "Stop"\n$roots = @(\n  "HKCU:\\Software\\Classes\\{identity.url_scheme}",\n  "HKCU:\\Software\\Classes\\{identity.project_extension}",\n  "HKCU:\\Software\\Classes\\W1Nexus.Project"\n)\nforeach ($root in $roots) {{ if (Test-Path $root) {{ Remove-Item -Recurse -Force $root }} }}\n'''
    return f'''$ErrorActionPreference = "Stop"\n$exe = Join-Path $PSScriptRoot "{exe}"\nif (-not (Test-Path $exe)) {{ throw "W1 executable not found: $exe" }}\n$protocol = "HKCU:\\Software\\Classes\\{identity.url_scheme}"\nNew-Item -Force $protocol | Out-Null\nSet-ItemProperty $protocol "(default)" "URL:{identity.product_name} Protocol"\nSet-ItemProperty $protocol "URL Protocol" ""\nNew-Item -Force "$protocol\\shell\\open\\command" | Out-Null\nSet-ItemProperty "$protocol\\shell\\open\\command" "(default)" ('"' + $exe + '" --deep-link "%1"')\n$ext = "HKCU:\\Software\\Classes\\{identity.project_extension}"\nNew-Item -Force $ext | Out-Null\nSet-ItemProperty $ext "(default)" "W1Nexus.Project"\n$project = "HKCU:\\Software\\Classes\\W1Nexus.Project"\nNew-Item -Force "$project\\shell\\open\\command" | Out-Null\nSet-ItemProperty "$project\\shell\\open\\command" "(default)" ('"' + $exe + '" --project "%1"')\n'''


def _inno_setup_source(identity: AppIdentity, version: str, architecture: str) -> str:
    guid = _windows_guid(identity)
    arch_line = "ArchitecturesAllowed=x64compatible\nArchitecturesInstallIn64BitMode=x64compatible" if architecture == "x64" else ""
    app_id_line = "AppId={{" + guid + "}\n"
    return rf'''#define MyAppName "{identity.product_name}"
#define MyAppVersion "{version}"
#define MyAppPublisher "{identity.publisher}"
#define MyAppExeName "{identity.executable_name}"

[Setup]
{app_id_line}AppName={{#MyAppName}}
AppVersion={{#MyAppVersion}}
AppPublisher={{#MyAppPublisher}}
VersionInfoCompany={{#MyAppPublisher}}
VersionInfoDescription=W1 Nexus AI orchestration platform
VersionInfoProductName={{#MyAppName}}
VersionInfoProductVersion={{#MyAppVersion}}
DefaultDirName={{localappdata}}\Programs\W1 Nexus
DefaultGroupName=W1 Nexus
OutputDir=out
OutputBaseFilename=W1-Nexus-{version}-windows-{architecture}
SetupIconFile=..\..\brand\production\w1-nexus.ico
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DisableProgramGroupPage=yes
UninstallDisplayIcon={{app}}\{{#MyAppExeName}}
{arch_line}

[Files]
Source: "..\..\dist\W1 Nexus\*"; DestDir: "{{app}}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{{autoprograms}}\W1 Nexus"; Filename: "{{app}}\{{#MyAppExeName}}"

[Registry]
Root: HKCU; Subkey: "Software\Classes\{identity.url_scheme}"; ValueType: string; ValueName: ""; ValueData: "URL:{identity.product_name} Protocol"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\{identity.url_scheme}"; ValueType: string; ValueName: "URL Protocol"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\{identity.url_scheme}\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{{app}}\{{#MyAppExeName}}"" --deep-link ""%1"""
Root: HKCU; Subkey: "Software\Classes\{identity.project_extension}"; ValueType: string; ValueName: ""; ValueData: "W1Nexus.Project"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\W1Nexus.Project"; ValueType: string; ValueName: ""; ValueData: "W1 Nexus Project"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\W1Nexus.Project\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{{app}}\{{#MyAppExeName}}"" --project ""%1"""
'''


def _windows_version_info_source(identity: AppIdentity, version: str) -> str:
    numeric = [int(part) for part in re.findall(r"\d+", version)[:4]]
    while len(numeric) < 4:
        numeric.append(0)
    filevers = tuple(numeric[:4])
    filevers_text = ".".join(str(v) for v in filevers)
    return f'''# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(filevers={filevers}, prodvers={filevers}, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable("040904B0", [
      StringStruct("CompanyName", "{identity.publisher}"),
      StringStruct("FileDescription", "W1 Nexus AI orchestration platform"),
      StringStruct("FileVersion", "{filevers_text}"),
      StringStruct("InternalName", "W1 Nexus"),
      StringStruct("LegalCopyright", "Copyright 2026 Wassim and W1 Nexus contributors."),
      StringStruct("OriginalFilename", "{identity.executable_name}"),
      StringStruct("ProductName", "{identity.product_name}"),
      StringStruct("ProductVersion", "{version}"),
    ])]),
    VarFileInfo([VarStruct("Translation", [1033, 1200])])
  ]
)
'''


def _pyinstaller_spec() -> str:
    return '''# W1 Nexus deterministic PyInstaller entrypoint.
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files
repo = Path(SPECPATH).resolve().parents[1]
entrypoint = repo / "src" / "w1cip" / "native_entrypoint.py"
icon = repo / "brand" / "production" / "w1-nexus.ico"
version_info = repo / "packaging" / "windows" / "version-info.txt"
datas = collect_data_files("w1cip")
a = Analysis([str(entrypoint)], pathex=[str(repo)], binaries=[], datas=datas, hiddenimports=["w1cip"], hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="W1 Nexus", icon=str(icon), version=str(version_info), debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="W1 Nexus")
'''


def _windows_build_script(version: str, architecture: str) -> str:
    lines = [
        '$ErrorActionPreference = "Stop"',
        'python -m pip install --upgrade build pyinstaller',
        'python -m pip install ".[desktop,office]"',
        'pyinstaller --noconfirm packaging/windows/w1-nexus.spec',
        '$inno = (Get-Command iscc -ErrorAction SilentlyContinue).Source',
        'if (-not $inno) {',
        '  $candidate = Join-Path $env:ProgramFiles "Inno Setup 6\\ISCC.exe"',
        '  $candidateX86 = Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\\ISCC.exe"',
        '  if (Test-Path $candidate) { $inno = $candidate } elseif (Test-Path $candidateX86) { $inno = $candidateX86 } else { throw "Inno Setup 6 (ISCC.exe) is required." }',
        '}',
        '$exe = "dist\\W1 Nexus\\W1 Nexus.exe"',
        'if ($env:W1_WINDOWS_SIGN_CERT_SHA1) {',
        '  & signtool sign /sha1 $env:W1_WINDOWS_SIGN_CERT_SHA1 /fd SHA256 /tr https://timestamp.digicert.com /td SHA256 $exe',
        '  if ($LASTEXITCODE -ne 0) { throw "Executable Authenticode signing failed." }',
        '} else {',
        '  Write-Warning "Executable remains unsigned: W1_WINDOWS_SIGN_CERT_SHA1 is not configured."',
        '}',
        '& $inno packaging/windows/installer.iss',
        'if ($LASTEXITCODE -ne 0) { throw "Inno Setup compilation failed." }',
        f'$installer = "packaging\\windows\\out\\W1-Nexus-{version}-windows-{architecture}.exe"',
        'if ($env:W1_WINDOWS_SIGN_CERT_SHA1) {',
        '  & signtool sign /sha1 $env:W1_WINDOWS_SIGN_CERT_SHA1 /fd SHA256 /tr https://timestamp.digicert.com /td SHA256 $installer',
        '  if ($LASTEXITCODE -ne 0) { throw "Installer Authenticode signing failed." }',
        '  $exeStatus = (Get-AuthenticodeSignature -LiteralPath $exe).Status',
        '  $installerStatus = (Get-AuthenticodeSignature -LiteralPath $installer).Status',
        '  if ($exeStatus -ne "Valid" -or $installerStatus -ne "Valid") { throw "Authenticode verification failed after signing." }',
        '} else {',
        '  Write-Warning "Installer remains unsigned and MUST NOT be published as a trusted release."',
        '}',
        f'Write-Host "Built W1 Nexus {version}; publication still requires a verified signing identity."',
    ]
    return "\n".join(lines) + "\n"


def generate_windows_packaging_sources(
    output_dir: str | Path,
    *,
    version: str = NATIVE_PACKAGING_VERSION,
    architecture: str = "x64",
    identity: AppIdentity | None = None,
) -> dict[str, Any]:
    selected_identity = identity or AppIdentity()
    selected_identity.validate()
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    files = {
        "w1-nexus.spec": _pyinstaller_spec(),
        "version-info.txt": _windows_version_info_source(selected_identity, version),
        "installer.iss": _inno_setup_source(selected_identity, version, architecture),
        "register-user.ps1": _windows_registry_script(selected_identity),
        "unregister-user.ps1": _windows_registry_script(selected_identity, unregister=True),
        "build.ps1": _windows_build_script(version, architecture),
    }
    written: dict[str, str] = {}
    for name, content in files.items():
        path = root / name
        path.write_text(content, encoding="utf-8", newline="\n")
        written[name] = sha256_file(path)
    manifest = {
        "packaging_version": NATIVE_PACKAGING_VERSION,
        "target": "windows",
        "architecture": architecture,
        "application": selected_identity.as_dict(),
        "files": written,
        "constraints": {
            "per_user_registry": True,
            "installer_admin_required": False,
            "deep_links_execute_shell": False,
            "credentials_embedded": False,
            "native_signing_required_before_publication": True,
            "production_brand_icon_required": True,
            "windows_version_resource_required": True,
            "automatic_updates_require_hash_and_native_signature_verification": True,
        },
    }
    manifest_path = root / "packaging-manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {**manifest, "manifest_sha256": sha256_file(manifest_path), "output_dir": str(root.resolve())}


def verify_windows_release_candidate(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    brand = verify_brand_identity(root, require_production_assets=True)
    checked = root / "packaging" / "windows"
    required_files = ["w1-nexus.spec", "version-info.txt", "installer.iss", "build.ps1", "register-user.ps1", "unregister-user.ps1", "packaging-manifest.json"]
    checks: dict[str, bool] = {
        "brand_assets_production_ready": bool(brand.get("production_ready")),
        "windows_packaging_sources_present": all((checked / name).is_file() for name in required_files),
        "production_ico_present": (root / "brand" / "production" / "w1-nexus.ico").is_file(),
        "production_svg_present": (root / "brand" / "production" / "w1-nexus-symbol.svg").is_file(),
        "splash_present": (root / "brand" / "production" / "w1-nexus-splash.png").is_file(),
    }
    if checks["windows_packaging_sources_present"]:
        spec = (checked / "w1-nexus.spec").read_text(encoding="utf-8")
        inno = (checked / "installer.iss").read_text(encoding="utf-8")
        version_info = (checked / "version-info.txt").read_text(encoding="utf-8")
        checks.update({
            "pyinstaller_uses_production_icon": "w1-nexus.ico" in spec and "icon=str(icon)" in spec,
            "pyinstaller_embeds_version_resource": "version=str(version_info)" in spec,
            "installer_uses_production_icon": "SetupIconFile=..\\..\\brand\\production\\w1-nexus.ico" in inno,
            "installer_is_per_user": "PrivilegesRequired=lowest" in inno and "Root: HKCU" in inno and "HKLM" not in inno,
            "version_resource_has_project_identity": "W1 Nexus Project" in version_info and "W1 Nexus AI orchestration platform" in version_info,
        })
    else:
        for name in ("pyinstaller_uses_production_icon","pyinstaller_embeds_version_resource","installer_uses_production_icon","installer_is_per_user","version_resource_has_project_identity"): checks[name]=False
    deterministic=False
    import tempfile as _tempfile
    with _tempfile.TemporaryDirectory(prefix="w1-rc-packaging-") as directory:
        generated = generate_windows_packaging_sources(directory, version=NATIVE_PACKAGING_VERSION)
        deterministic=True
        for name,digest in generated["files"].items():
            source=checked/name
            if not source.is_file() or sha256_file(source)!=digest: deterministic=False; break
    checks["checked_in_sources_match_generator"] = deterministic
    blockers=[name for name,passed in checks.items() if not passed]
    plan=native_build_plan(target="windows")
    return {"rc_contract_version":"1.0","passed":not blockers,"checks":checks,"blockers":blockers,"brand":brand,"native_build":plan.as_dict(),"claims":{"windows_exe_compiled_here":False,"windows_installer_compiled_here":False,"authenticode_signed_here":False,"source_rc_ready":not blockers,"public_native_release_ready":bool(not blockers and plan.can_compile_native_installer_here and plan.signing_configured)}}


def run_native_packaging_benchmark() -> dict[str, Any]:
    probes: dict[str, bool] = {}
    with tempfile.TemporaryDirectory(prefix="w1-native-packaging-") as directory:
        root = Path(directory)
        workspace = root / "workspace"
        workspace.mkdir()
        (workspace / ".w1nexus").mkdir()

        identity = AppIdentity()
        identity.validate()
        probes["stable_app_identity"] = identity.app_id == APP_ID and identity.url_scheme == URL_SCHEME

        link = parse_deep_link("w1://workspace/open?path=docs%2Fplan.md")
        probes["safe_deep_link_parsed"] = link.parameters.get("path") == "docs/plan.md"
        try:
            parse_deep_link("w1://workspace/open?command=powershell")
            probes["command_deep_link_rejected"] = False
        except DeepLinkDenied:
            probes["command_deep_link_rejected"] = True
        try:
            parse_deep_link("w1://workspace/open?path=..%2Fsecret")
            probes["traversal_deep_link_rejected"] = False
        except DeepLinkDenied:
            probes["traversal_deep_link_rejected"] = True

        descriptor_path = root / "project.w1nexus"
        descriptor = ProjectDescriptor(name="Benchmark", workspace="workspace", open_target="README.md")
        descriptor.write(descriptor_path)
        loaded = ProjectDescriptor.load(descriptor_path)
        probes["project_descriptor_round_trip"] = loaded.resolve_workspace(descriptor_path) == workspace.resolve()

        payload = root / "update.bin"
        payload.write_bytes(b"w1-native-update-benchmark")
        artifact = UpdateArtifact(
            platform="windows", architecture="x64", version=NATIVE_PACKAGING_VERSION,
            url="https://updates.example.invalid/w1/update.bin", size=payload.stat().st_size,
            sha256=sha256_file(payload), signature_kind="authenticode", signer_identity="TEST-SIGNER-METADATA",
        )
        verified = verify_update_artifact(artifact, payload)
        probes["update_hash_and_size_verified"] = verified["integrity_verified"] and not verified["native_signature_verified"]
        bad = UpdateArtifact(**{**artifact.as_dict(), "sha256": "0" * 64})
        try:
            verify_update_artifact(bad, payload)
            probes["tampered_update_rejected"] = False
        except UpdateVerificationError:
            probes["tampered_update_rejected"] = True

        generated = generate_windows_packaging_sources(root / "windows")
        inno = (root / "windows" / "installer.iss").read_text(encoding="utf-8")
        register = (root / "windows" / "register-user.ps1").read_text(encoding="utf-8")
        probes["windows_installer_sources_generated"] = len(generated["files"]) == 6
        probes["per_user_associations_only"] = "Root: HKCU" in inno and "HKLM" not in inno and "HKCU:" in register
        probes["protocol_and_project_associations_present"] = URL_SCHEME in inno and PROJECT_EXTENSION in inno
        probes["installer_requires_native_signature_before_publication"] = generated["constraints"]["native_signing_required_before_publication"]

        controller = NativeServiceController(workspace)
        state = controller.start(command=[sys.executable, "-c", "import time; time.sleep(30)"], port=18766)
        status = controller.status()
        mode = controller.state_path.stat().st_mode & 0o777 if os.name != "nt" else 0o600
        probes["service_process_identity_bound"] = status["running"] and status["process_identity_matches"]
        probes["service_state_private"] = os.name == "nt" or mode == 0o600
        stopped = controller.stop(timeout_seconds=3)
        probes["service_lifecycle_controlled"] = bool(stopped.get("stopped")) and not controller.status()["running"]

        plan = native_build_plan(target="windows")
        probes["build_environment_truthful"] = isinstance(plan.can_compile_native_installer_here, bool) and "pyinstaller" in plan.tools

        combined = "\n".join(path.read_text(encoding="utf-8") for path in (root / "windows").glob("*.*") if path.suffix in {".ps1", ".iss", ".spec", ".json"})
        probes["generated_sources_have_no_embedded_secret"] = "API_KEY=" not in combined and "refresh_token" not in combined

    return {
        "passed": all(probes.values()),
        "probes": probes,
        "metrics": {
            "probe_count": len(probes),
            "windows_packaging_source_files": 6,
            "native_installer_compiled_in_benchmark": False,
            "native_signature_verified_in_benchmark": False,
        },
        "limitations": [
            "Windows EXE/installer compilation requires a Windows build host with PyInstaller and Inno Setup.",
            "Authenticode signing requires a user/company signing identity and native signtool verification.",
            "macOS notarization and Linux package generation share the contract but are not generated in this step.",
        ],
    }
