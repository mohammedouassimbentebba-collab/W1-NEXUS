"""Secure execution fabric for W1 Nexus.

The fabric offers two execution classes:

* ``local``: a dependency-free, process-group isolated backend with POSIX
  ``setrlimit`` enforcement where available.  It is appropriate for trusted or
  semi-trusted code, but it is not a kernel isolation boundary on every OS.
* ``docker`` / ``podman``: an OCI-backed backend with read-only roots,
  network policy, cgroup resource limits, pids limits, tmpfs, non-root users,
  ephemeral secret injection, and explicit mounts.

Every execution is journalled in SQLite.  Artifacts are copied through a
symlink-safe extractor, hashed, and bound to a local HMAC attestation.  The
attestation proves what this W1 installation observed; it is not a remote TEE
attestation or a substitute for externally protected signing keys.
"""
from __future__ import annotations

import fnmatch
import hashlib
import hmac
import json
import os
import platform
import re
import secrets
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence

try:  # POSIX only
    import resource  # type: ignore
except ImportError:  # pragma: no cover - Windows
    resource = None  # type: ignore

from .action_runtime import canonical_json


ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SECRET_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,127}$")
NETWORK_MODES = {"none", "loopback", "inherit", "allowlist"}
BACKENDS = {"auto", "local", "docker", "podman"}
TERMINAL_STATES = {"completed", "failed", "timed_out", "cancelled", "policy_denied"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class SecureExecutionError(RuntimeError):
    code = "secure_execution_error"


class SandboxPolicyDenied(SecureExecutionError):
    code = "sandbox_policy_denied"


class SandboxBackendUnavailable(SecureExecutionError):
    code = "sandbox_backend_unavailable"


class SandboxExecutionNotFound(SecureExecutionError):
    code = "sandbox_execution_not_found"


class SandboxAttestationInvalid(SecureExecutionError):
    code = "sandbox_attestation_invalid"


@dataclass(frozen=True)
class SandboxLimits:
    wall_seconds: int = 300
    cpu_seconds: int = 120
    cpu_cores: float = 1.0
    memory_mb: int = 1024
    pids: int = 128
    open_files: int = 512
    file_size_mb: int = 256
    output_bytes: int = 1_000_000
    tmpfs_mb: int = 128
    artifact_bytes: int = 100_000_000

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            if float(value) <= 0:
                raise ValueError(f"{name} must be positive")


@dataclass(frozen=True)
class NetworkPolicy:
    mode: str = "none"
    allow_hosts: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.mode not in NETWORK_MODES:
            raise ValueError(f"unknown network mode: {self.mode}")
        if self.mode != "allowlist" and self.allow_hosts:
            raise ValueError("allow_hosts requires network mode allowlist")


@dataclass(frozen=True)
class MountSpec:
    source: str
    target: str
    mode: str = "ro"

    def __post_init__(self) -> None:
        if self.mode not in {"ro", "rw"}:
            raise ValueError("mount mode must be ro or rw")
        target = PurePosixPath(self.target)
        if not target.is_absolute() or ".." in target.parts:
            raise ValueError("mount target must be an absolute container path")


@dataclass(frozen=True)
class SandboxProfile:
    profile_id: str
    backend: str = "auto"
    image: str | None = None
    image_digest: str | None = None
    root_read_only: bool = True
    workspace_mode: str = "rw"
    run_as_user: str = "host"
    network: NetworkPolicy = field(default_factory=NetworkPolicy)
    limits: SandboxLimits = field(default_factory=SandboxLimits)
    allowed_commands: tuple[str, ...] = ("python", "python3", "pytest", "git", "node", "npm")
    environment_allowlist: tuple[str, ...] = ("PATH", "LANG", "LC_ALL", "TZ", "PYTHONPATH")
    artifact_globs: tuple[str, ...] = ("artifacts/**", "reports/**")
    mount_source_roots: tuple[str, ...] = (".",)
    secret_names: tuple[str, ...] = ()
    require_hard_isolation: bool = True
    seccomp_profile: str | None = None
    capabilities_drop: tuple[str, ...] = ("ALL",)

    def __post_init__(self) -> None:
        if not ID_RE.fullmatch(self.profile_id):
            raise ValueError("profile_id must be canonical lowercase identifier")
        if self.backend not in BACKENDS:
            raise ValueError(f"unknown backend: {self.backend}")
        if self.workspace_mode not in {"ro", "rw"}:
            raise ValueError("workspace_mode must be ro or rw")
        for name in self.secret_names:
            if not SECRET_NAME_RE.fullmatch(name):
                raise ValueError(f"invalid secret name: {name}")
        if self.backend in {"docker", "podman"} and not self.image:
            raise ValueError("OCI profiles require an image")
        if self.image_digest and not self.image:
            raise ValueError("image_digest requires image")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "SandboxProfile":
        data = dict(value)
        data["limits"] = SandboxLimits(**dict(data.get("limits", {})))
        network = dict(data.get("network", {}))
        network["allow_hosts"] = tuple(network.get("allow_hosts", ()))
        data["network"] = NetworkPolicy(**network)
        tuple_defaults = {
            "allowed_commands": ("python", "python3", "pytest", "git", "node", "npm"),
            "environment_allowlist": ("PATH", "LANG", "LC_ALL", "TZ", "PYTHONPATH"),
            "artifact_globs": ("artifacts/**", "reports/**"),
            "mount_source_roots": (".",),
            "secret_names": (),
            "capabilities_drop": ("ALL",),
        }
        for key, default in tuple_defaults.items():
            data[key] = tuple(data.get(key, default))
        return cls(**data)


@dataclass(frozen=True)
class SandboxRequest:
    execution_id: str
    profile_id: str
    argv: tuple[str, ...]
    cwd: str = "."
    environment: Mapping[str, str] = field(default_factory=dict)
    mounts: tuple[MountSpec, ...] = ()
    secret_values: Mapping[str, str] = field(default_factory=dict, repr=False)
    artifact_globs: tuple[str, ...] = ()
    requested_by: str = "w1-orchestrator"
    reason: str | None = None

    def __post_init__(self) -> None:
        if not ID_RE.fullmatch(self.execution_id):
            raise ValueError("execution_id must be canonical lowercase identifier")
        if not ID_RE.fullmatch(self.profile_id):
            raise ValueError("profile_id must be canonical lowercase identifier")
        if not self.argv or not all(isinstance(x, str) and x for x in self.argv):
            raise ValueError("argv must contain non-empty strings")
        cwd = PurePosixPath(self.cwd)
        if cwd.is_absolute() or ".." in cwd.parts:
            raise ValueError("cwd must be workspace relative")
        for name, value in self.secret_values.items():
            if not SECRET_NAME_RE.fullmatch(name):
                raise ValueError(f"invalid secret name: {name}")
            if not isinstance(value, str) or not value:
                raise ValueError(f"secret {name} must be a non-empty string")

    def redacted_mapping(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "profile_id": self.profile_id,
            "argv": list(self.argv),
            "cwd": self.cwd,
            "environment": dict(self.environment),
            "mounts": [asdict(x) for x in self.mounts],
            "secret_names": sorted(self.secret_values),
            "artifact_globs": list(self.artifact_globs),
            "requested_by": self.requested_by,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ArtifactRecord:
    relative_path: str
    stored_path: str
    size_bytes: int
    sha256: str
    media_type: str = "application/octet-stream"


@dataclass(frozen=True)
class ResourceTelemetry:
    wall_seconds: float
    cpu_user_seconds: float | None
    cpu_system_seconds: float | None
    peak_rss_bytes: int | None
    stdout_bytes: int
    stderr_bytes: int
    output_truncated: bool
    enforcement: Mapping[str, str]


@dataclass(frozen=True)
class SandboxAttestation:
    attestation_id: str
    execution_id: str
    request_digest: str
    profile_digest: str
    result_digest: str
    artifact_root_digest: str
    backend: str
    runtime_version: str
    host_platform: str
    created_at: str
    claims: Mapping[str, Any]
    signature: str


@dataclass(frozen=True)
class SandboxResult:
    execution_id: str
    status: str
    backend: str
    started_at: str
    completed_at: str
    exit_code: int | None
    stdout: str
    stderr: str
    telemetry: ResourceTelemetry
    artifacts: tuple[ArtifactRecord, ...]
    attestation: SandboxAttestation | None
    error_code: str | None = None
    backend_metadata: Mapping[str, Any] = field(default_factory=dict)


class ProcessRunner(Protocol):
    def __call__(self, argv: Sequence[str], **kwargs: Any) -> subprocess.Popen[bytes]: ...


class SandboxJournal:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=FULL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self._lock = threading.RLock()
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sandbox_profiles(
              profile_id TEXT PRIMARY KEY,
              profile_json TEXT NOT NULL,
              profile_digest TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sandbox_executions(
              execution_id TEXT PRIMARY KEY,
              profile_id TEXT NOT NULL,
              request_json TEXT NOT NULL,
              request_digest TEXT NOT NULL,
              status TEXT NOT NULL,
              backend TEXT,
              result_json TEXT,
              started_at TEXT NOT NULL,
              completed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS sandbox_artifacts(
              execution_id TEXT NOT NULL,
              relative_path TEXT NOT NULL,
              artifact_json TEXT NOT NULL,
              PRIMARY KEY(execution_id, relative_path)
            );
            CREATE TABLE IF NOT EXISTS sandbox_attestations(
              execution_id TEXT PRIMARY KEY,
              attestation_json TEXT NOT NULL
            );
            """
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def save_profile(self, profile: SandboxProfile) -> str:
        payload = asdict(profile)
        digest = _sha256_bytes(canonical_json(payload).encode())
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO sandbox_profiles(profile_id,profile_json,profile_digest,updated_at) VALUES (?,?,?,?)",
                (profile.profile_id, canonical_json(payload), digest, utc_now()),
            )
        return digest

    def load_profile(self, profile_id: str) -> SandboxProfile:
        row = self.conn.execute("SELECT profile_json FROM sandbox_profiles WHERE profile_id=?", (profile_id,)).fetchone()
        if row is None:
            raise SandboxPolicyDenied(f"unknown sandbox profile: {profile_id}")
        return SandboxProfile.from_mapping(json.loads(row["profile_json"]))

    def list_profiles(self) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT profile_id,profile_json,profile_digest,updated_at FROM sandbox_profiles ORDER BY profile_id").fetchall()
        return [
            {
                "profile_id": row["profile_id"],
                "profile": json.loads(row["profile_json"]),
                "profile_digest": row["profile_digest"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def start(self, request: SandboxRequest, request_digest: str) -> None:
        with self._lock, self.conn:
            existing = self.conn.execute("SELECT request_digest FROM sandbox_executions WHERE execution_id=?", (request.execution_id,)).fetchone()
            if existing:
                if existing["request_digest"] != request_digest:
                    raise SandboxPolicyDenied("execution_id already exists with different request")
                raise SandboxPolicyDenied("execution_id already exists")
            self.conn.execute(
                "INSERT INTO sandbox_executions(execution_id,profile_id,request_json,request_digest,status,started_at) VALUES (?,?,?,?,?,?)",
                (request.execution_id, request.profile_id, canonical_json(request.redacted_mapping()), request_digest, "running", utc_now()),
            )

    def finish(self, result: SandboxResult) -> None:
        result_json = canonical_json(_result_to_mapping(result))
        with self._lock, self.conn:
            self.conn.execute(
                "UPDATE sandbox_executions SET status=?,backend=?,result_json=?,completed_at=? WHERE execution_id=?",
                (result.status, result.backend, result_json, result.completed_at, result.execution_id),
            )
            for artifact in result.artifacts:
                self.conn.execute(
                    "INSERT OR REPLACE INTO sandbox_artifacts(execution_id,relative_path,artifact_json) VALUES (?,?,?)",
                    (result.execution_id, artifact.relative_path, canonical_json(asdict(artifact))),
                )
            if result.attestation:
                self.conn.execute(
                    "INSERT OR REPLACE INTO sandbox_attestations(execution_id,attestation_json) VALUES (?,?)",
                    (result.execution_id, canonical_json(asdict(result.attestation))),
                )

    def get(self, execution_id: str) -> dict[str, Any]:
        row = self.conn.execute("SELECT * FROM sandbox_executions WHERE execution_id=?", (execution_id,)).fetchone()
        if row is None:
            raise SandboxExecutionNotFound(execution_id)
        return {
            "execution_id": row["execution_id"],
            "profile_id": row["profile_id"],
            "request": json.loads(row["request_json"]),
            "request_digest": row["request_digest"],
            "status": row["status"],
            "backend": row["backend"],
            "result": json.loads(row["result_json"]) if row["result_json"] else None,
            "started_at": row["started_at"],
            "completed_at": row["completed_at"],
        }

    def list_executions(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT execution_id,profile_id,status,backend,started_at,completed_at FROM sandbox_executions ORDER BY started_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]


def _result_to_mapping(result: SandboxResult) -> dict[str, Any]:
    value = asdict(result)
    return value


class SecureExecutionFabric:
    RUNTIME_VERSION = "0.1.0-dev50"

    def __init__(
        self,
        workspace_root: str | Path,
        *,
        state_dir: str | Path | None = None,
        process_factory: ProcessRunner = subprocess.Popen,
    ) -> None:
        self.root = Path(workspace_root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.state_dir = Path(state_dir).expanduser().resolve() if state_dir else self.root / ".w1nexus"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.artifact_root = self.state_dir / "sandbox-artifacts"
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        self.secret_key_path = self.state_dir / "sandbox-attestation.key"
        self.secret_key = self._load_or_create_secret()
        self.journal = SandboxJournal(self.state_dir / "secure-execution.sqlite3")
        self.process_factory = process_factory
        self._cancel: dict[str, threading.Event] = {}
        self._lock = threading.RLock()
        self._ensure_builtin_profiles()

    def close(self) -> None:
        self.journal.close()

    def _load_or_create_secret(self) -> bytes:
        if self.secret_key_path.exists():
            return self.secret_key_path.read_bytes()
        key = secrets.token_bytes(32)
        try:
            fd = os.open(str(self.secret_key_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return self.secret_key_path.read_bytes()
        try:
            os.write(fd, key)
        finally:
            os.close(fd)
        return key

    def _ensure_builtin_profiles(self) -> None:
        builtins = [
            SandboxProfile(
                profile_id="trusted-local",
                backend="local",
                require_hard_isolation=False,
                root_read_only=False,
                network=NetworkPolicy(mode="inherit"),
                limits=SandboxLimits(wall_seconds=120, cpu_seconds=60, memory_mb=1024, pids=64),
            ),
            SandboxProfile(
                profile_id="oci-python",
                backend="auto",
                image="python:3.13-slim",
                root_read_only=True,
                network=NetworkPolicy(mode="none"),
                limits=SandboxLimits(wall_seconds=300, cpu_seconds=120, memory_mb=1024, pids=128),
                allowed_commands=("python", "python3", "pytest"),
                require_hard_isolation=True,
            ),
        ]
        for profile in builtins:
            existing = {x["profile_id"] for x in self.journal.list_profiles()}
            if profile.profile_id not in existing:
                self.journal.save_profile(profile)

    def save_profile(self, profile: SandboxProfile) -> str:
        return self.journal.save_profile(profile)

    def load_profile(self, profile_id: str) -> SandboxProfile:
        return self.journal.load_profile(profile_id)

    def doctor(self) -> dict[str, Any]:
        engines: dict[str, Any] = {}
        for name in ("docker", "podman"):
            path = shutil.which(name)
            entry: dict[str, Any] = {"available": bool(path), "path": path}
            if path:
                result = subprocess.run([path, "version", "--format", "{{.Client.Version}}"], capture_output=True, text=True, timeout=5, check=False)
                entry.update({"usable": result.returncode == 0, "version": result.stdout.strip() or None, "error": result.stderr.strip() or None})
            else:
                entry["usable"] = False
            engines[name] = entry
        local_limits = {
            "posix_rlimits": bool(resource is not None and os.name != "nt"),
            "process_group_termination": True,
            "hard_network_isolation": False,
            "hard_filesystem_isolation": False,
        }
        return {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "engines": engines,
            "local_backend": local_limits,
            "profiles": self.journal.list_profiles(),
        }

    def plan(self, request: SandboxRequest) -> dict[str, Any]:
        profile = self.load_profile(request.profile_id)
        backend = self._select_backend(profile)
        self._validate_request(request, profile, backend)
        return {
            "execution_id": request.execution_id,
            "profile_id": profile.profile_id,
            "backend": backend,
            "hard_isolation": backend in {"docker", "podman"},
            "network_enforcement": "kernel" if backend in {"docker", "podman"} else "not_enforced",
            "filesystem_enforcement": "mount_namespace" if backend in {"docker", "podman"} else "workspace_convention",
            "limits": asdict(profile.limits),
            "warnings": self._warnings(profile, backend),
        }

    def execute(self, request: SandboxRequest, *, cancel_event: threading.Event | None = None) -> SandboxResult:
        profile = self.load_profile(request.profile_id)
        backend = self._select_backend(profile)
        self._validate_request(request, profile, backend)
        request_digest = _sha256_bytes(canonical_json(request.redacted_mapping()).encode())
        try:
            existing = self.journal.get(request.execution_id)
        except SandboxExecutionNotFound:
            existing = None
        if existing is not None:
            if existing["request_digest"] != request_digest:
                raise SandboxPolicyDenied("execution_id already exists with different request")
            if existing.get("result") is not None:
                return _result_from_mapping(existing["result"])
            raise SandboxPolicyDenied("execution already started and requires reconciliation")
        self.journal.start(request, request_digest)
        cancel = cancel_event or threading.Event()
        self._cancel[request.execution_id] = cancel
        try:
            if backend == "local":
                raw = self._run_local(request, profile, cancel)
            else:
                raw = self._run_oci(request, profile, backend, cancel)
            raw = dict(raw)
            raw["stdout"] = self._redact_secrets(str(raw.get("stdout", "")), request.secret_values.values())
            raw["stderr"] = self._redact_secrets(str(raw.get("stderr", "")), request.secret_values.values())
            artifacts = self._extract_artifacts(request, profile)
            result = self._finalise_result(request, profile, backend, request_digest, raw, artifacts)
            self.journal.finish(result)
            return result
        except SandboxPolicyDenied as exc:
            now = utc_now()
            telemetry = ResourceTelemetry(0.0, None, None, None, 0, 0, False, {})
            result = SandboxResult(
                request.execution_id, "policy_denied", backend, now, now, None, "", str(exc), telemetry, (), None,
                exc.code, {},
            )
            self.journal.finish(result)
            return result
        except Exception as exc:
            now = utc_now()
            telemetry = ResourceTelemetry(0.0, None, None, None, 0, 0, False, {})
            result = SandboxResult(
                request.execution_id, "failed", backend, now, now, None, "", str(exc), telemetry, (), None,
                getattr(exc, "code", type(exc).__name__), {},
            )
            self.journal.finish(result)
            return result
        finally:
            self._cancel.pop(request.execution_id, None)

    def cancel(self, execution_id: str) -> None:
        event = self._cancel.get(execution_id)
        if event is None:
            raise SandboxExecutionNotFound(execution_id)
        event.set()

    def verify_attestation(self, attestation: SandboxAttestation | Mapping[str, Any]) -> bool:
        data = dict(attestation) if isinstance(attestation, Mapping) else asdict(attestation)
        signature = str(data.pop("signature"))
        expected = hmac.new(self.secret_key, canonical_json(data).encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise SandboxAttestationInvalid("attestation signature mismatch")
        return True

    @staticmethod
    def _redact_secrets(text: str, values: Iterable[str]) -> str:
        result = text
        for value in sorted({v for v in values if v}, key=len, reverse=True):
            result = result.replace(value, "[REDACTED_SECRET]")
        return result

    def _select_backend(self, profile: SandboxProfile) -> str:
        if profile.backend in {"local", "docker", "podman"}:
            if profile.backend in {"docker", "podman"} and not self._engine_usable(profile.backend):
                raise SandboxBackendUnavailable(profile.backend)
            return profile.backend
        for engine in ("docker", "podman"):
            if self._engine_usable(engine):
                return engine
        if profile.require_hard_isolation:
            raise SandboxBackendUnavailable("no OCI engine available for hard-isolation profile")
        return "local"

    @staticmethod
    def _engine_usable(name: str) -> bool:
        executable = shutil.which(name)
        if not executable:
            return False
        try:
            result = subprocess.run([executable, "version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3, check=False)
            return result.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False

    def _validate_request(self, request: SandboxRequest, profile: SandboxProfile, backend: str) -> None:
        command = Path(request.argv[0]).name.lower()
        command_stem = Path(request.argv[0]).stem.lower()
        allowed = {x.lower() for x in profile.allowed_commands}
        if command not in allowed and command_stem not in allowed:
            raise SandboxPolicyDenied(f"command not allowed by profile: {command}")
        work_cwd = (self.root / request.cwd).resolve()
        if work_cwd != self.root and self.root not in work_cwd.parents:
            raise SandboxPolicyDenied("cwd escapes workspace")
        if not work_cwd.exists() or not work_cwd.is_dir():
            raise SandboxPolicyDenied("cwd does not exist")
        unknown_secrets = set(request.secret_values) - set(profile.secret_names)
        if unknown_secrets:
            raise SandboxPolicyDenied(f"secrets not permitted by profile: {sorted(unknown_secrets)}")
        unknown_env = set(request.environment) - set(profile.environment_allowlist)
        if unknown_env:
            raise SandboxPolicyDenied(f"environment keys not permitted: {sorted(unknown_env)}")
        allowed_mount_roots = [self._resolve_profile_root(value) for value in profile.mount_source_roots]
        for mount in request.mounts:
            source = self._resolve_mount_source(mount.source)
            if not source.exists():
                raise SandboxPolicyDenied(f"mount source missing: {source}")
            if source.is_symlink():
                raise SandboxPolicyDenied("symlink mount sources are prohibited")
            if not any(source == root or root in source.parents for root in allowed_mount_roots):
                raise SandboxPolicyDenied(f"mount source outside profile roots: {source}")
        if backend == "local":
            if profile.require_hard_isolation:
                raise SandboxBackendUnavailable("local backend cannot satisfy hard isolation")
            if profile.network.mode not in {"inherit"}:
                raise SandboxPolicyDenied("local backend cannot enforce requested network policy")
            if request.mounts:
                raise SandboxPolicyDenied("local backend does not provide mount namespaces")

    def _resolve_profile_root(self, value: str) -> Path:
        path = Path(value).expanduser()
        return (self.root / path).resolve() if not path.is_absolute() else path.resolve()

    def _resolve_mount_source(self, value: str) -> Path:
        path = Path(value).expanduser()
        return (self.root / path).resolve() if not path.is_absolute() else path.resolve()

    @staticmethod
    def _container_name(execution_id: str) -> str:
        digest = hashlib.sha256(execution_id.encode()).hexdigest()[:12]
        return f"w1-{execution_id[:36]}-{digest}"

    def _warnings(self, profile: SandboxProfile, backend: str) -> list[str]:
        warnings: list[str] = []
        if backend == "local":
            warnings.extend([
                "local backend is not a filesystem or network isolation boundary",
                "memory and CPU limits are enforced only where POSIX rlimits are available",
            ])
        if profile.network.mode == "allowlist":
            warnings.append("OCI allowlist requires an external proxy/firewall; direct egress remains disabled")
        if not profile.image_digest and backend in {"docker", "podman"}:
            warnings.append("image tag is not pinned by digest")
        return warnings

    def _clean_environment(self, profile: SandboxProfile, request: SandboxRequest) -> dict[str, str]:
        env: dict[str, str] = {}
        for name in profile.environment_allowlist:
            if name in os.environ:
                env[name] = os.environ[name]
        env.update({str(k): str(v) for k, v in request.environment.items()})
        env["W1_EXECUTION_ID"] = request.execution_id
        return env

    def _run_process(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        limits: SandboxLimits,
        cancel: threading.Event,
        terminate_hook: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        before = resource.getrusage(resource.RUSAGE_CHILDREN) if resource is not None and os.name != "nt" else None
        started_mono = time.monotonic()
        started_at = utc_now()
        process = self.process_factory(
            list(argv), cwd=str(cwd), env=dict(env), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False,
            start_new_session=True,
        )
        deadline = started_mono + limits.wall_seconds
        timed_out = False
        cancelled = False
        stdout_chunks: list[bytes] = []
        stderr_chunks: list[bytes] = []
        byte_counts = {"stdout": 0, "stderr": 0}

        def drain(stream: Any, chunks: list[bytes], name: str) -> None:
            while True:
                chunk = stream.read(65536)
                if not chunk:
                    break
                byte_counts[name] += len(chunk)
                captured = sum(len(item) for item in chunks)
                if captured < limits.output_bytes:
                    chunks.append(chunk[: max(0, limits.output_bytes - captured)])

        stdout_thread = threading.Thread(target=drain, args=(process.stdout, stdout_chunks, "stdout"), daemon=True)
        stderr_thread = threading.Thread(target=drain, args=(process.stderr, stderr_chunks, "stderr"), daemon=True)
        stdout_thread.start()
        stderr_thread.start()
        while process.poll() is None:
            if cancel.is_set():
                cancelled = True
                self._kill_process(process)
                if terminate_hook:
                    terminate_hook()
                break
            if time.monotonic() >= deadline:
                timed_out = True
                self._kill_process(process)
                if terminate_hook:
                    terminate_hook()
                break
            time.sleep(0.05)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._kill_process(process)
            process.wait(timeout=5)
        stdout_thread.join(timeout=5)
        stderr_thread.join(timeout=5)
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()
        stdout_b = b"".join(stdout_chunks)
        stderr_b = b"".join(stderr_chunks)
        wall = time.monotonic() - started_mono
        total = byte_counts["stdout"] + byte_counts["stderr"]
        output_truncated = total > limits.output_bytes
        remaining = limits.output_bytes
        out = stdout_b[:remaining]
        remaining -= len(out)
        err = stderr_b[:max(0, remaining)]
        after = resource.getrusage(resource.RUSAGE_CHILDREN) if resource is not None and os.name != "nt" else None
        cpu_user = cpu_system = None
        peak_rss = None
        if before is not None and after is not None:
            cpu_user = max(0.0, after.ru_utime - before.ru_utime)
            cpu_system = max(0.0, after.ru_stime - before.ru_stime)
            peak_rss = int(after.ru_maxrss * (1024 if platform.system() != "Darwin" else 1))
        status = "cancelled" if cancelled else "timed_out" if timed_out else "completed" if process.returncode == 0 else "failed"
        return {
            "status": status,
            "started_at": started_at,
            "completed_at": utc_now(),
            "exit_code": int(process.returncode) if process.returncode is not None else None,
            "stdout": out.decode("utf-8", errors="replace"),
            "stderr": err.decode("utf-8", errors="replace"),
            "telemetry": ResourceTelemetry(
                wall, cpu_user, cpu_system, peak_rss, byte_counts["stdout"], byte_counts["stderr"], output_truncated,
                {},
            ),
        }

    @staticmethod
    def _kill_process(process: subprocess.Popen[bytes]) -> None:
        try:
            if os.name != "nt":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except ProcessLookupError:
            pass

    def _run_local(self, request: SandboxRequest, profile: SandboxProfile, cancel: threading.Event) -> dict[str, Any]:
        env = self._clean_environment(profile, request)
        # Secrets are available only in child memory and are never journalled.
        env.update(request.secret_values)
        argv: Sequence[str] = request.argv
        limits_file: Path | None = None
        temporary: tempfile.TemporaryDirectory[str] | None = None
        if resource is not None and os.name != "nt":
            temporary = tempfile.TemporaryDirectory(prefix="w1-limits-", dir=str(self.state_dir))
            limits_file = Path(temporary.name) / "limits.json"
            limits_file.write_text(canonical_json(asdict(profile.limits)), encoding="utf-8")
            os.chmod(limits_file, 0o600)
            argv = (sys.executable, str(Path(__file__).with_name("sandbox_child.py")), "--limits-file", str(limits_file), "--", *request.argv)
        try:
            raw = self._run_process(
                argv,
                cwd=(self.root / request.cwd).resolve(),
                env=env,
                limits=profile.limits,
                cancel=cancel,
            )
        finally:
            if temporary is not None:
                temporary.cleanup()
        enforcement = {
            "wall": "process_watchdog",
            "cpu_time": "posix_rlimit" if resource is not None and os.name != "nt" else "not_enforced",
            "cpu_cores": "not_enforced",
            "memory": "posix_rlimit" if resource is not None and os.name != "nt" else "not_enforced",
            "pids": "posix_rlimit" if resource is not None and os.name != "nt" else "not_enforced",
            "network": "not_enforced",
            "filesystem": "workspace_convention",
        }
        raw["telemetry"] = ResourceTelemetry(**{**asdict(raw["telemetry"]), "enforcement": enforcement})
        raw["metadata"] = {"hard_isolation": False}
        return raw

    def _oci_command(self, request: SandboxRequest, profile: SandboxProfile, backend: str, secret_file: Path | None) -> list[str]:
        assert profile.image
        limits = profile.limits
        command = [backend, "run", "--rm", "--name", self._container_name(request.execution_id)]
        command += ["--cpus", str(limits.cpu_cores), "--memory", f"{limits.memory_mb}m", "--memory-swap", f"{limits.memory_mb}m"]
        command += ["--pids-limit", str(limits.pids), "--ulimit", f"nofile={limits.open_files}:{limits.open_files}"]
        command += ["--ulimit", f"fsize={limits.file_size_mb * 1024}:{limits.file_size_mb * 1024}"]
        run_user = profile.run_as_user
        if run_user == "host":
            run_user = f"{os.getuid()}:{os.getgid()}" if hasattr(os, "getuid") else "1000:1000"
        command += ["--security-opt", "no-new-privileges:true", "--user", run_user]
        for cap in profile.capabilities_drop:
            command += ["--cap-drop", cap]
        if profile.seccomp_profile:
            command += ["--security-opt", f"seccomp={profile.seccomp_profile}"]
        if profile.root_read_only:
            command.append("--read-only")
        command += ["--tmpfs", f"/tmp:rw,noexec,nosuid,nodev,size={limits.tmpfs_mb}m"]
        if profile.network.mode in {"none", "allowlist"}:
            command += ["--network", "none"]
        elif profile.network.mode == "loopback":
            command += ["--network", "none"]
        workspace_opts = "rw" if profile.workspace_mode == "rw" else "ro"
        command += ["--mount", f"type=bind,src={self.root},dst=/workspace,{workspace_opts}"]
        for mount in request.mounts:
            command += ["--mount", f"type=bind,src={self._resolve_mount_source(mount.source)},dst={mount.target},{mount.mode}"]
        if request.secret_values and secret_file is not None:
            command += ["--env-file", str(secret_file)]
        command += ["--workdir", f"/workspace/{request.cwd}".rstrip("/")]
        for key, value in self._clean_environment(profile, request).items():
            command += ["--env", f"{key}={value}"]
        image = f"{profile.image}@{profile.image_digest}" if profile.image_digest else profile.image
        command.append(image)
        command += list(request.argv)
        return command

    def _run_oci(self, request: SandboxRequest, profile: SandboxProfile, backend: str, cancel: threading.Event) -> dict[str, Any]:
        with tempfile.TemporaryDirectory(prefix="w1-secrets-", dir=str(self.state_dir)) as temp:
            secret_file: Path | None = None
            if request.secret_values:
                secret_file = Path(temp) / "secrets.env"
                secret_file.write_text(
                    "".join(f"{name}={value.replace(chr(10), '').replace(chr(13), '')}\n" for name, value in sorted(request.secret_values.items())),
                    encoding="utf-8",
                )
                os.chmod(secret_file, 0o600)
            command = self._oci_command(request, profile, backend, secret_file)
            container_name = self._container_name(request.execution_id)
            def terminate_container() -> None:
                try:
                    subprocess.run([backend, "kill", container_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5, check=False)
                except (OSError, subprocess.TimeoutExpired):
                    pass
            raw = self._run_process(
                command, cwd=self.root, env={"PATH": os.environ.get("PATH", "")},
                limits=profile.limits, cancel=cancel, terminate_hook=terminate_container,
            )
        enforcement = {
            "wall": "process_watchdog",
            "cpu_time": "container_process",
            "cpu_cores": "oci_cgroup",
            "memory": "oci_cgroup",
            "pids": "oci_pids_limit",
            "network": "oci_network_namespace",
            "filesystem": "oci_mount_namespace",
            "rootfs": "read_only" if profile.root_read_only else "writable",
        }
        raw["telemetry"] = ResourceTelemetry(**{**asdict(raw["telemetry"]), "enforcement": enforcement})
        raw["metadata"] = {
            "hard_isolation": True,
            "engine": backend,
            "image": profile.image,
            "image_digest": profile.image_digest,
            "command_digest": _sha256_bytes(canonical_json(command).encode()),
        }
        return raw

    def _extract_artifacts(self, request: SandboxRequest, profile: SandboxProfile) -> tuple[ArtifactRecord, ...]:
        globs = request.artifact_globs or profile.artifact_globs
        if not globs:
            return ()
        destination = self.artifact_root / request.execution_id
        destination.mkdir(parents=True, exist_ok=True)
        records: list[ArtifactRecord] = []
        total = 0
        for path in sorted(self.root.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            try:
                relative = path.relative_to(self.root).as_posix()
            except ValueError:
                continue
            if relative.startswith(".w1nexus/"):
                continue
            if not any(fnmatch.fnmatch(relative, pattern) for pattern in globs):
                continue
            resolved = path.resolve()
            if resolved != self.root and self.root not in resolved.parents:
                raise SandboxPolicyDenied("artifact escapes workspace")
            size = path.stat().st_size
            total += size
            if total > profile.limits.artifact_bytes:
                raise SandboxPolicyDenied("artifact extraction byte limit exceeded")
            stored = destination / relative
            stored.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, stored)
            records.append(ArtifactRecord(relative, str(stored), size, _sha256_file(stored)))
        return tuple(records)

    def _finalise_result(
        self,
        request: SandboxRequest,
        profile: SandboxProfile,
        backend: str,
        request_digest: str,
        raw: Mapping[str, Any],
        artifacts: tuple[ArtifactRecord, ...],
    ) -> SandboxResult:
        result_payload = {
            "execution_id": request.execution_id,
            "status": raw["status"],
            "backend": backend,
            "exit_code": raw["exit_code"],
            "stdout_sha256": _sha256_bytes(str(raw["stdout"]).encode()),
            "stderr_sha256": _sha256_bytes(str(raw["stderr"]).encode()),
            "telemetry": asdict(raw["telemetry"]),
            "artifacts": [asdict(x) for x in artifacts],
            "backend_metadata": dict(raw.get("metadata", {})),
        }
        profile_payload = asdict(profile)
        profile_digest = _sha256_bytes(canonical_json(profile_payload).encode())
        result_digest = _sha256_bytes(canonical_json(result_payload).encode())
        artifact_root_digest = _sha256_bytes(canonical_json([asdict(x) for x in artifacts]).encode())
        claims = {
            "hard_isolation": backend in {"docker", "podman"},
            "network_mode": profile.network.mode,
            "root_read_only": profile.root_read_only if backend in {"docker", "podman"} else False,
            "limits": asdict(profile.limits),
            "secret_names": sorted(request.secret_values),
            "secret_values_persisted_by_w1_control_plane": False,
            "attestation_scope": "local_w1_observation",
        }
        unsigned = {
            "attestation_id": f"att-{request.execution_id}-{uuid.uuid4().hex[:12]}",
            "execution_id": request.execution_id,
            "request_digest": request_digest,
            "profile_digest": profile_digest,
            "result_digest": result_digest,
            "artifact_root_digest": artifact_root_digest,
            "backend": backend,
            "runtime_version": self.RUNTIME_VERSION,
            "host_platform": platform.platform(),
            "created_at": utc_now(),
            "claims": claims,
        }
        signature = hmac.new(self.secret_key, canonical_json(unsigned).encode(), hashlib.sha256).hexdigest()
        attestation = SandboxAttestation(**unsigned, signature=signature)
        return SandboxResult(
            request.execution_id, str(raw["status"]), backend, str(raw["started_at"]), str(raw["completed_at"]),
            raw["exit_code"], str(raw["stdout"]), str(raw["stderr"]), raw["telemetry"], artifacts,
            attestation, None if raw["status"] == "completed" else f"sandbox_{raw['status']}", dict(raw.get("metadata", {})),
        )


def _result_from_mapping(value: Mapping[str, Any]) -> SandboxResult:
    data = dict(value)
    data["telemetry"] = ResourceTelemetry(**dict(data["telemetry"]))
    data["artifacts"] = tuple(ArtifactRecord(**item) for item in data.get("artifacts", ()))
    attestation = data.get("attestation")
    data["attestation"] = SandboxAttestation(**attestation) if attestation else None
    return SandboxResult(**data)


def request_from_mapping(value: Mapping[str, Any]) -> SandboxRequest:
    data = dict(value)
    data["argv"] = tuple(data.get("argv", ()))
    data["mounts"] = tuple(MountSpec(**m) for m in data.get("mounts", ()))
    data["artifact_globs"] = tuple(data.get("artifact_globs", ()))
    return SandboxRequest(**data)
