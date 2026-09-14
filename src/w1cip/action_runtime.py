"""Governed local action runtime for W1 Nexus.

The runtime is deliberately local-first and dependency-light.  It confines file
operations to a workspace, executes commands without a shell, strips secrets
from child environments, records every attempt in SQLite, and requires a
one-time HMAC-bound approval for sensitive actions.  Reversible file actions
create backups that can be restored with ``undo``.

This is not an operating-system security boundary.  It is a reference policy
and audit layer intended to be combined with containers/VMs for hostile code.
"""

from __future__ import annotations

import fnmatch
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import signal
import sqlite3
import stat
import subprocess
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .computer_use import (
    NAVIGATION_KEYS,
    POINTER_BUTTONS,
    ComputerBackendUnavailable,
    ComputerDriver,
    ComputerElement,
    ComputerSelectorError,
    EphemeralInputRef,
    EphemeralInputVault,
    create_native_computer_driver,
    element_matches,
    resolve_unique_element,
)


ACTION_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SENSITIVE_ENV_RE = re.compile(
    r"(?:api[_-]?key|token|secret|password|passwd|credential|private[_-]?key|authorization)",
    re.IGNORECASE,
)
SHELL_METACHARACTERS = re.compile(r"[;&|`$<>\n\r]")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def action_digest(kind: str, parameters: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        canonical_json({"kind": kind, "parameters": dict(parameters)}).encode("utf-8")
    ).hexdigest()


class ActionRuntimeError(RuntimeError):
    code = "action_runtime_error"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.code)


class ActionPolicyDenied(ActionRuntimeError):
    code = "action_policy_denied"


class ActionApprovalRequired(ActionRuntimeError):
    code = "action_approval_required"

    def __init__(self, plan: "ActionPlan") -> None:
        self.plan = plan
        super().__init__(f"Approval required for {plan.kind} ({plan.action_digest})")


class ActionApprovalInvalid(ActionRuntimeError):
    code = "action_approval_invalid"


class ActionNotFound(ActionRuntimeError):
    code = "action_not_found"


class ActionNotReversible(ActionRuntimeError):
    code = "action_not_reversible"


class ActionCommandFailed(ActionRuntimeError):
    code = "action_command_failed"

    def __init__(self, result: "ActionResult") -> None:
        self.result = result
        super().__init__(f"Command exited with {result.exit_code}")


class ActionCommandTimedOut(ActionRuntimeError):
    code = "action_command_timed_out"

    def __init__(self, result: "ActionResult") -> None:
        self.result = result
        super().__init__(self.code)


@dataclass(frozen=True)
class ActionPolicy:
    allowed_commands: tuple[str, ...] = ("git", "python", "python3", "pytest")
    denied_commands: tuple[str, ...] = (
        "bash",
        "sh",
        "zsh",
        "fish",
        "cmd",
        "cmd.exe",
        "powershell",
        "pwsh",
        "curl",
        "wget",
        "ssh",
        "scp",
        "sftp",
        "ftp",
        "telnet",
        "nc",
        "ncat",
        "netcat",
    )
    denied_path_globs: tuple[str, ...] = (
        ".git",
        ".git/**",
        ".w1nexus",
        ".w1nexus/**",
    )
    max_command_seconds: int = 60
    max_output_bytes: int = 1_000_000
    max_backup_bytes: int = 25_000_000
    require_approval_for_writes: bool = False
    require_approval_for_delete: bool = True
    require_approval_for_commands: bool = True
    require_approval_for_worktrees: bool = True
    require_approval_for_computer_input: bool = True
    require_approval_for_computer_perception: bool = True
    max_computer_clicks: int = 3
    max_computer_scroll_delta: int = 1200
    max_computer_text_chars: int = 4096
    max_computer_elements: int = 2048
    computer_capture_ttl_seconds: int = 300
    computer_window_allowlist: tuple[str, ...] = ()
    computer_window_denylist: tuple[str, ...] = ()
    allow_symlinks: bool = False
    allow_network: bool = False
    environment_allowlist: tuple[str, ...] = (
        "PATH",
        "HOME",
        "USERPROFILE",
        "TMP",
        "TEMP",
        "LANG",
        "LC_ALL",
        "SYSTEMROOT",
        "COMSPEC",
        "PATHEXT",
    )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "ActionPolicy":
        if not value:
            return cls()
        allowed = {field.name for field in __import__("dataclasses").fields(cls)}
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"Unknown action policy fields: {', '.join(sorted(unknown))}")
        payload: dict[str, Any] = dict(value)
        for name in (
            "allowed_commands",
            "denied_commands",
            "denied_path_globs",
            "environment_allowlist",
            "computer_window_allowlist",
            "computer_window_denylist",
        ):
            if name in payload:
                payload[name] = tuple(payload[name])
        return cls(**payload)


@dataclass(frozen=True)
class ActionRequest:
    action_id: str
    kind: str
    parameters: Mapping[str, Any]
    requested_by: str = "local-user"
    reason: str | None = None

    def __post_init__(self) -> None:
        if not ACTION_ID_RE.fullmatch(self.action_id):
            raise ValueError("action_id must be a canonical lowercase identifier")


@dataclass(frozen=True)
class ActionPlan:
    action_id: str
    kind: str
    risk: str
    reversible: bool
    requires_approval: bool
    action_digest: str
    summary: str
    resolved_paths: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class ActionApproval:
    approval_id: str
    action_digest: str
    issued_by: str
    issued_at: str
    expires_at: str
    one_time: bool
    signature: str


@dataclass(frozen=True)
class ActionResult:
    action_id: str
    kind: str
    status: str
    started_at: str
    completed_at: str
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    output_truncated: bool = False
    changed_paths: tuple[str, ...] = ()
    reversible: bool = False
    undo_action_id: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    error_code: str | None = None


class ActionRuntime:
    """Execute governed local actions in one workspace."""

    def __init__(
        self,
        workspace_root: str | Path,
        *,
        state_dir: str | Path | None = None,
        policy: ActionPolicy | None = None,
        computer_driver: ComputerDriver | None = None,
        ephemeral_input_vault: EphemeralInputVault | None = None,
    ) -> None:
        self.root = Path(workspace_root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.state_dir = (
            Path(state_dir).expanduser().resolve()
            if state_dir is not None
            else self.root / ".w1nexus"
        )
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.policy = policy or ActionPolicy()
        self.computer_driver = computer_driver or create_native_computer_driver()
        self.ephemeral_input_vault = ephemeral_input_vault or EphemeralInputVault()
        self.computer_capture_root = self.state_dir / "computer-captures"
        self.computer_capture_root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.state_dir / "action-runtime.sqlite3"
        self.backup_root = self.state_dir / "action-backups"
        self.backup_root.mkdir(parents=True, exist_ok=True)
        self.secret_path = self.state_dir / "action-secret.key"
        self._lock = threading.RLock()
        self._secret = self._load_or_create_secret()
        self._connection = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._initialize_schema()

    def __enter__(self) -> "ActionRuntime":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def _load_or_create_secret(self) -> bytes:
        if self.secret_path.exists():
            return self.secret_path.read_bytes()
        value = secrets.token_bytes(32)
        fd = os.open(self.secret_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, value)
        finally:
            os.close(fd)
        try:
            os.chmod(self.secret_path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass
        return value

    def _initialize_schema(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS actions (
                action_id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                request_json TEXT NOT NULL,
                plan_json TEXT NOT NULL,
                result_json TEXT,
                status TEXT NOT NULL,
                requested_by TEXT NOT NULL,
                approval_id TEXT,
                created_at TEXT NOT NULL,
                completed_at TEXT,
                undo_of_action_id TEXT,
                FOREIGN KEY (undo_of_action_id) REFERENCES actions(action_id)
            );
            CREATE TABLE IF NOT EXISTS approvals (
                approval_id TEXT PRIMARY KEY,
                action_digest TEXT NOT NULL,
                approval_json TEXT NOT NULL,
                consumed_by_action_id TEXT,
                consumed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS backups (
                action_id TEXT NOT NULL,
                ordinal INTEGER NOT NULL,
                relative_path TEXT NOT NULL,
                backup_path TEXT,
                existed_before INTEGER NOT NULL,
                was_directory INTEGER NOT NULL,
                PRIMARY KEY (action_id, ordinal),
                FOREIGN KEY (action_id) REFERENCES actions(action_id)
            );
            CREATE TABLE IF NOT EXISTS worktrees (
                agent_id TEXT PRIMARY KEY,
                path TEXT NOT NULL,
                branch TEXT NOT NULL,
                created_by_action_id TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        self._connection.commit()

    # ---------- planning and policy ----------

    def plan(self, request: ActionRequest) -> ActionPlan:
        kind = request.kind
        params = dict(request.parameters)
        resolved: list[str] = []
        warnings: list[str] = []
        risk = "read_only"
        reversible = False
        requires_approval = False
        summary = kind

        if kind == "file.read":
            path = self._resolve_path(self._required_str(params, "path"), must_exist=True)
            resolved.append(self._relative(path))
            summary = f"Read {self._relative(path)}"
        elif kind == "file.write":
            path = self._resolve_path(self._required_str(params, "path"), must_exist=False)
            self._deny_protected_path(path)
            content = params.get("content")
            if not isinstance(content, str):
                raise ActionPolicyDenied("file.write requires string content")
            resolved.append(self._relative(path))
            risk, reversible = "reversible_write", True
            requires_approval = self.policy.require_approval_for_writes
            summary = f"Write {self._relative(path)}"
        elif kind == "file.publish_staged":
            path = self._resolve_path(self._required_str(params, "path"), must_exist=False)
            self._deny_protected_path(path)
            staging = self._resolve_staging_path(self._required_str(params, "staging_path"))
            expected_hash = self._required_str(params, "sha256")
            if not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
                raise ActionPolicyDenied("file.publish_staged requires a lowercase SHA-256 digest")
            if staging.stat().st_size > self.policy.max_backup_bytes:
                raise ActionPolicyDenied("staged file exceeds policy max_backup_bytes")
            resolved.extend((self._relative(path), f"state:{staging.relative_to(self.state_dir).as_posix()}"))
            risk, reversible = "reversible_write", True
            requires_approval = self.policy.require_approval_for_writes
            summary = f"Publish staged binary to {self._relative(path)}"
        elif kind == "file.mkdir":
            path = self._resolve_path(self._required_str(params, "path"), must_exist=False)
            self._deny_protected_path(path)
            resolved.append(self._relative(path))
            risk, reversible = "reversible_write", True
            requires_approval = self.policy.require_approval_for_writes
            summary = f"Create directory {self._relative(path)}"
        elif kind == "file.move":
            source = self._resolve_path(self._required_str(params, "source"), must_exist=True)
            target = self._resolve_path(self._required_str(params, "target"), must_exist=False)
            self._deny_protected_path(source)
            self._deny_protected_path(target)
            resolved.extend((self._relative(source), self._relative(target)))
            risk, reversible = "reversible_write", True
            requires_approval = self.policy.require_approval_for_writes
            summary = f"Move {self._relative(source)} to {self._relative(target)}"
        elif kind == "file.delete":
            path = self._resolve_path(self._required_str(params, "path"), must_exist=True)
            self._deny_protected_path(path)
            resolved.append(self._relative(path))
            risk, reversible = "destructive", True
            requires_approval = self.policy.require_approval_for_delete
            summary = f"Delete {self._relative(path)} with backup"
        elif kind == "command.run":
            argv = self._validated_argv(params.get("argv"))
            cwd = self._resolve_path(str(params.get("cwd", ".")), must_exist=True)
            if not cwd.is_dir():
                raise ActionPolicyDenied("command cwd must be a directory")
            self._deny_protected_path(cwd)
            resolved.append(self._relative(cwd))
            read_only = self._command_is_read_only(argv)
            risk = "read_only" if read_only else "external_effect"
            reversible = False
            requires_approval = self.policy.require_approval_for_commands and not read_only
            summary = "Run: " + " ".join(argv)
            if not self.policy.allow_network:
                warnings.append(
                    "Network is denied by policy on a best-effort basis; use an OS sandbox for hostile code."
                )
        elif kind == "computer.observe":
            risk = "read_only"
            reversible = False
            requires_approval = False
            summary = "Observe desktop geometry, cursor, and active-window title"
        elif kind == "computer.screenshot":
            risk = "sensitive_read"
            reversible = False
            requires_approval = self.policy.require_approval_for_computer_perception
            summary = "Capture a governed local screenshot into ephemeral W1 state"
        elif kind == "computer.elements.list":
            selector = params.get("selector", {})
            if not isinstance(selector, Mapping):
                raise ActionPolicyDenied("computer selector must be an object")
            if selector:
                self._validate_computer_selector(selector)
            risk = "sensitive_read"
            reversible = False
            requires_approval = self.policy.require_approval_for_computer_perception
            summary = "Inspect local UI/accessibility elements" + (" matching a selector" if selector else "")
        elif kind == "computer.element.click":
            selector = self._validate_computer_selector(params.get("selector"))
            fingerprint = self._validate_computer_fingerprint(params.get("expected_fingerprint"))
            button = str(params.get("button", "left")).lower()
            if button not in POINTER_BUTTONS:
                raise ActionPolicyDenied("unsupported pointer button")
            clicks = self._bounded_int(params, "clicks", 1, self.policy.max_computer_clicks, default=1)
            risk, reversible = "external_effect", False
            requires_approval = self.policy.require_approval_for_computer_input
            summary = f"{button.title()} click x{clicks} on selector-bound UI element ({fingerprint[:12]}…)"
        elif kind == "computer.element.type":
            self._validate_computer_selector(params.get("selector"))
            fingerprint = self._validate_computer_fingerprint(params.get("expected_fingerprint"))
            self._validate_ephemeral_input_reference(params)
            length = int(params["text_length"])
            risk, reversible = "external_effect", False
            requires_approval = self.policy.require_approval_for_computer_input
            summary = f"Type {length} ephemeral characters into selector-bound UI element ({fingerprint[:12]}…)"
            warnings.append("Text content is process-local and is never persisted in the action request/result journal.")
        elif kind == "computer.file.choose":
            self._validate_computer_selector(params.get("selector"))
            fingerprint = self._validate_computer_fingerprint(params.get("expected_fingerprint"))
            path = self._resolve_path(self._required_str(params, "path"), must_exist=True)
            if not path.is_file():
                raise ActionPolicyDenied("computer.file.choose path must be an existing file")
            self._deny_protected_path(path)
            resolved.append(self._relative(path))
            risk, reversible = "external_effect", False
            requires_approval = self.policy.require_approval_for_computer_input
            summary = f"Choose workspace file {self._relative(path)} in selector-bound file field ({fingerprint[:12]}…)"
        elif kind == "computer.pointer.move":
            x = self._bounded_int(params, "x", 0, 100_000)
            y = self._bounded_int(params, "y", 0, 100_000)
            risk, reversible = "external_effect", False
            requires_approval = self.policy.require_approval_for_computer_input
            summary = f"Move pointer to ({x}, {y})"
        elif kind == "computer.pointer.click":
            x = self._bounded_int(params, "x", 0, 100_000)
            y = self._bounded_int(params, "y", 0, 100_000)
            button = str(params.get("button", "left")).lower()
            if button not in POINTER_BUTTONS:
                raise ActionPolicyDenied("unsupported pointer button")
            clicks = self._bounded_int(params, "clicks", 1, self.policy.max_computer_clicks, default=1)
            risk, reversible = "external_effect", False
            requires_approval = self.policy.require_approval_for_computer_input
            summary = f"{button.title()} click x{clicks} at ({x}, {y})"
        elif kind == "computer.scroll":
            x = self._bounded_int(params, "x", 0, 100_000)
            y = self._bounded_int(params, "y", 0, 100_000)
            delta = self._bounded_int(
                params, "delta", -self.policy.max_computer_scroll_delta, self.policy.max_computer_scroll_delta
            )
            if delta == 0:
                raise ActionPolicyDenied("computer.scroll delta must be non-zero")
            risk, reversible = "external_effect", False
            requires_approval = self.policy.require_approval_for_computer_input
            summary = f"Scroll {delta} at ({x}, {y})"
        elif kind == "computer.key.press":
            key = self._required_str(params, "key").lower()
            if key not in NAVIGATION_KEYS:
                raise ActionPolicyDenied("only bounded navigation keys are permitted; free-form typing is disabled")
            risk, reversible = "external_effect", False
            requires_approval = self.policy.require_approval_for_computer_input
            summary = f"Press navigation key {key}"
        elif kind == "git.worktree.create":
            agent_id = self._canonical_token(self._required_str(params, "agent_id"), "agent_id")
            branch = self._branch_name(self._required_str(params, "branch"))
            self._ensure_git_repository()
            path = self.state_dir / "worktrees" / agent_id
            resolved.append(self._relative(path))
            risk, reversible = "repository_mutation", True
            requires_approval = self.policy.require_approval_for_worktrees
            summary = f"Create worktree {agent_id} on branch {branch}"
        elif kind == "git.worktree.remove":
            agent_id = self._canonical_token(self._required_str(params, "agent_id"), "agent_id")
            row = self._connection.execute(
                "SELECT path, branch FROM worktrees WHERE agent_id=?", (agent_id,)
            ).fetchone()
            if row is None:
                raise ActionNotFound(f"Unknown worktree: {agent_id}")
            resolved.append(self._relative(Path(row["path"])))
            risk, reversible = "repository_mutation", False
            requires_approval = self.policy.require_approval_for_worktrees
            summary = f"Remove worktree {agent_id}"
        else:
            raise ActionPolicyDenied(f"Unsupported action kind: {kind}")

        return ActionPlan(
            action_id=request.action_id,
            kind=kind,
            risk=risk,
            reversible=reversible,
            requires_approval=requires_approval,
            action_digest=action_digest(kind, params),
            summary=summary,
            resolved_paths=tuple(resolved),
            warnings=tuple(warnings),
        )

    def issue_approval(
        self,
        plan: ActionPlan,
        *,
        issued_by: str,
        ttl_seconds: int = 600,
        one_time: bool = True,
    ) -> ActionApproval:
        if ttl_seconds < 1 or ttl_seconds > 86_400:
            raise ValueError("ttl_seconds must be between 1 and 86400")
        issued_at = datetime.now(timezone.utc)
        expires_at = issued_at + timedelta(seconds=ttl_seconds)
        approval_id = "approval-" + secrets.token_hex(12)
        unsigned = {
            "approval_id": approval_id,
            "action_digest": plan.action_digest,
            "issued_by": issued_by,
            "issued_at": issued_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
            "expires_at": expires_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
            "one_time": bool(one_time),
        }
        signature = hmac.new(
            self._secret, canonical_json(unsigned).encode("utf-8"), hashlib.sha256
        ).hexdigest()
        approval = ActionApproval(signature=signature, **unsigned)
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO approvals(approval_id, action_digest, approval_json) VALUES (?, ?, ?)",
                (approval_id, plan.action_digest, canonical_json(asdict(approval))),
            )
        return approval

    def register_ephemeral_input(self, text: str, *, ttl_seconds: int = 120) -> EphemeralInputRef:
        """Register one-shot text without writing its contents to SQLite or action JSON."""

        return self.ephemeral_input_vault.put(
            text, ttl_seconds=ttl_seconds, max_chars=self.policy.max_computer_text_chars
        )

    # ---------- execution ----------

    def execute(
        self,
        request: ActionRequest,
        *,
        approval: ActionApproval | Mapping[str, Any] | None = None,
        raise_on_command_failure: bool = False,
    ) -> ActionResult:
        # Resolve idempotent retries before planning.  Some actions consume
        # ephemeral inputs (for example a staged binary file), so a successful
        # prior execution must remain replayable after that input is removed.
        prior = self.get_result(request.action_id)
        if prior is not None:
            expected_request = canonical_json(asdict(request))
            row = self._connection.execute(
                "SELECT request_json FROM actions WHERE action_id=?", (request.action_id,)
            ).fetchone()
            if row is not None and row["request_json"] == expected_request:
                return prior
            raise ActionPolicyDenied("action_id was already used for different content")

        plan = self.plan(request)
        selected_approval = self._coerce_approval(approval)
        if plan.requires_approval:
            if selected_approval is None:
                raise ActionApprovalRequired(plan)
            self._validate_approval(selected_approval, plan, request.action_id)

        started = utc_now()
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO actions(action_id, kind, request_json, plan_json, status, requested_by, approval_id, created_at) "
                "VALUES (?, ?, ?, ?, 'running', ?, ?, ?)",
                (
                    request.action_id,
                    request.kind,
                    canonical_json(asdict(request)),
                    canonical_json(asdict(plan)),
                    request.requested_by,
                    selected_approval.approval_id if selected_approval else None,
                    started,
                ),
            )
            if selected_approval is not None and selected_approval.one_time:
                updated = self._connection.execute(
                    "UPDATE approvals SET consumed_by_action_id=?, consumed_at=? "
                    "WHERE approval_id=? AND consumed_by_action_id IS NULL",
                    (request.action_id, started, selected_approval.approval_id),
                ).rowcount
                if updated != 1:
                    raise ActionApprovalInvalid("approval has already been consumed")

        try:
            result = self._dispatch(request, plan, started)
        except Exception as exc:
            completed = utc_now()
            code = getattr(exc, "code", "action_execution_failed")
            result = ActionResult(
                action_id=request.action_id,
                kind=request.kind,
                status="failed",
                started_at=started,
                completed_at=completed,
                error_code=str(code),
                stderr=str(exc),
                reversible=plan.reversible,
            )
            self._persist_result(result)
            raise
        self._persist_result(result)
        if raise_on_command_failure and request.kind == "command.run" and result.exit_code not in (0, None):
            if result.error_code == ActionCommandTimedOut.code:
                raise ActionCommandTimedOut(result)
            raise ActionCommandFailed(result)
        return result

    def undo(
        self,
        action_id: str,
        *,
        undo_action_id: str | None = None,
        requested_by: str = "local-user",
    ) -> ActionResult:
        original = self.get_result(action_id)
        if original is None:
            raise ActionNotFound(action_id)
        if not original.reversible:
            raise ActionNotReversible(action_id)
        undo_id = undo_action_id or f"undo-{action_id}"
        if not ACTION_ID_RE.fullmatch(undo_id):
            raise ValueError("undo_action_id must be canonical")
        existing = self.get_result(undo_id)
        if existing is not None:
            return existing
        started = utc_now()
        row = self._connection.execute(
            "SELECT kind FROM actions WHERE action_id=?", (action_id,)
        ).fetchone()
        if row is None:
            raise ActionNotFound(action_id)
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO actions(action_id, kind, request_json, plan_json, status, requested_by, created_at, undo_of_action_id) "
                "VALUES (?, 'action.undo', ?, ?, 'running', ?, ?, ?)",
                (
                    undo_id,
                    canonical_json({"action_id": undo_id, "kind": "action.undo", "parameters": {"target": action_id}}),
                    canonical_json({"reversible": False, "target": action_id}),
                    requested_by,
                    started,
                    action_id,
                ),
            )
        if row["kind"] == "git.worktree.create":
            changed: list[str] = []
            created = self._connection.execute(
                "SELECT agent_id, path FROM worktrees WHERE created_by_action_id=?", (action_id,)
            ).fetchone()
            if created is not None:
                self._git(["worktree", "remove", "--force", created["path"]])
                with self._connection:
                    self._connection.execute("DELETE FROM worktrees WHERE agent_id=?", (created["agent_id"],))
                changed.append(self._relative(Path(created["path"])))
        else:
            changed = self._restore_backups(action_id)
        completed = utc_now()
        result = ActionResult(
            action_id=undo_id,
            kind="action.undo",
            status="completed",
            started_at=started,
            completed_at=completed,
            changed_paths=tuple(sorted(set(changed))),
            reversible=False,
            metadata={"undid_action_id": action_id},
        )
        self._persist_result(result)
        return result

    def get_result(self, action_id: str) -> ActionResult | None:
        row = self._connection.execute(
            "SELECT result_json FROM actions WHERE action_id=?", (action_id,)
        ).fetchone()
        if row is None or row["result_json"] is None:
            return None
        value = json.loads(row["result_json"])
        value["changed_paths"] = tuple(value.get("changed_paths", ()))
        return ActionResult(**value)

    def list_actions(self, *, limit: int = 100) -> list[dict[str, Any]]:
        rows = self._connection.execute(
            "SELECT action_id, kind, status, requested_by, approval_id, created_at, completed_at, undo_of_action_id "
            "FROM actions ORDER BY created_at DESC, action_id DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
        return [dict(row) for row in rows]

    def list_worktrees(self) -> list[dict[str, Any]]:
        rows = self._connection.execute(
            "SELECT agent_id, path, branch, created_by_action_id, created_at FROM worktrees ORDER BY agent_id"
        ).fetchall()
        return [dict(row) for row in rows]

    # ---------- dispatch ----------

    def _dispatch(self, request: ActionRequest, plan: ActionPlan, started: str) -> ActionResult:
        params = dict(request.parameters)
        if request.kind == "file.read":
            path = self._resolve_path(self._required_str(params, "path"), must_exist=True)
            max_bytes = int(params.get("max_bytes", self.policy.max_output_bytes))
            if max_bytes < 1 or max_bytes > self.policy.max_output_bytes:
                raise ActionPolicyDenied("max_bytes outside policy")
            data = path.read_bytes()
            truncated = len(data) > max_bytes
            data = data[:max_bytes]
            text = data.decode(str(params.get("encoding", "utf-8")), errors="replace")
            return self._completed(request, started, stdout=text, truncated=truncated, metadata={"bytes_read": len(data)})
        if request.kind == "file.write":
            path = self._resolve_path(self._required_str(params, "path"), must_exist=False)
            self._backup_path(request.action_id, path, 0)
            path.parent.mkdir(parents=True, exist_ok=True)
            encoding = str(params.get("encoding", "utf-8"))
            content = str(params["content"])
            fd, temp_name = tempfile.mkstemp(prefix=".w1-write-", dir=str(path.parent))
            try:
                with os.fdopen(fd, "w", encoding=encoding, newline="") as handle:
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp_name, path)
            finally:
                if os.path.exists(temp_name):
                    os.unlink(temp_name)
            return self._completed(request, started, changed=(self._relative(path),), reversible=True, metadata={"bytes_written": len(content.encode(encoding))})
        if request.kind == "file.publish_staged":
            path = self._resolve_path(self._required_str(params, "path"), must_exist=False)
            staging = self._resolve_staging_path(self._required_str(params, "staging_path"))
            data = staging.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            if digest != self._required_str(params, "sha256"):
                raise ActionPolicyDenied("staged file digest mismatch")
            self._backup_path(request.action_id, path, 0)
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, temp_name = tempfile.mkstemp(prefix=".w1-publish-", dir=str(path.parent))
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp_name, path)
                staging.unlink(missing_ok=True)
            finally:
                if os.path.exists(temp_name):
                    os.unlink(temp_name)
            return self._completed(
                request, started, changed=(self._relative(path),), reversible=True,
                metadata={"bytes_written": len(data), "binary": True, "sha256": digest},
            )
        if request.kind == "file.mkdir":
            path = self._resolve_path(self._required_str(params, "path"), must_exist=False)
            self._backup_path(request.action_id, path, 0)
            path.mkdir(parents=bool(params.get("parents", True)), exist_ok=bool(params.get("exist_ok", False)))
            return self._completed(request, started, changed=(self._relative(path),), reversible=True)
        if request.kind == "file.move":
            source = self._resolve_path(self._required_str(params, "source"), must_exist=True)
            target = self._resolve_path(self._required_str(params, "target"), must_exist=False)
            self._backup_path(request.action_id, source, 0)
            self._backup_path(request.action_id, target, 1)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(target))
            return self._completed(request, started, changed=(self._relative(source), self._relative(target)), reversible=True)
        if request.kind == "file.delete":
            path = self._resolve_path(self._required_str(params, "path"), must_exist=True)
            self._backup_path(request.action_id, path, 0)
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink()
            return self._completed(request, started, changed=(self._relative(path),), reversible=True)
        if request.kind == "computer.observe":
            self._require_computer_backend()
            observation = self.computer_driver.observe()
            payload = observation.as_dict()
            return self._completed(request, started, stdout=canonical_json(payload), metadata=payload)
        if request.kind == "computer.screenshot":
            self._require_computer_backend()
            self._cleanup_computer_captures()
            screenshot = self.computer_driver.capture_screenshot()
            target = self.computer_capture_root / f"{request.action_id}.png"
            target.write_bytes(screenshot.png_bytes)
            try:
                os.chmod(target, stat.S_IRUSR | stat.S_IWUSR)
            except OSError:
                pass
            metadata = screenshot.audit_dict()
            metadata.update({
                "capture_path": str(target),
                "capture_ttl_seconds": self.policy.computer_capture_ttl_seconds,
                "sensitive": True,
            })
            return self._completed(request, started, metadata=metadata)
        if request.kind == "computer.elements.list":
            self._require_computer_backend()
            selector = params.get("selector", {})
            elements = self.computer_driver.list_elements(limit=self.policy.max_computer_elements)
            if selector:
                elements = [element for element in elements if element_matches(element, selector)]
            element_payloads = [element.as_dict() for element in elements]
            tree_hash = hashlib.sha256(canonical_json(element_payloads).encode("utf-8")).hexdigest()
            return self._completed(
                request, started,
                metadata={
                    "backend": self.computer_driver.backend_name,
                    "element_count": len(element_payloads),
                    "tree_hash": tree_hash,
                    "elements": element_payloads,
                    "elements_ephemeral": True,
                },
            )
        if request.kind == "computer.element.click":
            self._require_computer_backend()
            element = self._resolve_computer_element(params)
            self._enforce_computer_window_policy(element)
            button, clicks = str(params.get("button", "left")).lower(), int(params.get("clicks", 1))
            x, y = element.center
            self.computer_driver.click(x, y, button=button, clicks=clicks)
            return self._completed(
                request, started,
                metadata={
                    "element_id": element.element_id, "fingerprint": element.fingerprint, "role": element.role,
                    "window_title": element.window_title, "center": [x, y], "button": button, "clicks": clicks,
                    "backend": self.computer_driver.backend_name,
                },
            )
        if request.kind == "computer.element.type":
            self._require_computer_backend()
            element = self._resolve_computer_element(params)
            self._enforce_computer_window_policy(element)
            text = self.ephemeral_input_vault.consume(
                str(params["input_handle"]), binding=str(params["input_binding"]), expected_length=int(params["text_length"])
            )
            x, y = element.center
            self.computer_driver.click(x, y, button="left", clicks=1)
            self.computer_driver.type_text(text)
            return self._completed(
                request, started,
                metadata={
                    "element_id": element.element_id, "fingerprint": element.fingerprint, "role": element.role,
                    "window_title": element.window_title, "text_length": len(text), "text_persisted": False,
                    "input_handle_consumed": True, "backend": self.computer_driver.backend_name,
                },
            )
        if request.kind == "computer.file.choose":
            self._require_computer_backend()
            element = self._resolve_computer_element(params)
            self._enforce_computer_window_policy(element)
            path = self._resolve_path(self._required_str(params, "path"), must_exist=True)
            x, y = element.center
            self.computer_driver.click(x, y, button="left", clicks=1)
            self.computer_driver.type_text(str(path))
            if bool(params.get("submit", True)):
                self.computer_driver.press_key("enter")
            return self._completed(
                request, started,
                metadata={
                    "element_id": element.element_id, "fingerprint": element.fingerprint,
                    "workspace_path": self._relative(path), "submitted": bool(params.get("submit", True)),
                    "backend": self.computer_driver.backend_name,
                },
            )
        if request.kind == "computer.pointer.move":
            self._require_computer_backend()
            x, y = int(params["x"]), int(params["y"])
            self.computer_driver.move_pointer(x, y)
            return self._completed(request, started, metadata={"x": x, "y": y, "backend": self.computer_driver.backend_name})
        if request.kind == "computer.pointer.click":
            self._require_computer_backend()
            x, y = int(params["x"]), int(params["y"])
            button, clicks = str(params.get("button", "left")).lower(), int(params.get("clicks", 1))
            self.computer_driver.click(x, y, button=button, clicks=clicks)
            return self._completed(
                request, started, metadata={"x": x, "y": y, "button": button, "clicks": clicks, "backend": self.computer_driver.backend_name}
            )
        if request.kind == "computer.scroll":
            self._require_computer_backend()
            x, y, delta = int(params["x"]), int(params["y"]), int(params["delta"])
            self.computer_driver.scroll(x, y, delta=delta)
            return self._completed(request, started, metadata={"x": x, "y": y, "delta": delta, "backend": self.computer_driver.backend_name})
        if request.kind == "computer.key.press":
            self._require_computer_backend()
            key = str(params["key"]).lower()
            self.computer_driver.press_key(key)
            return self._completed(request, started, metadata={"key": key, "backend": self.computer_driver.backend_name})
        if request.kind == "command.run":
            return self._run_command(request, started)
        if request.kind == "git.worktree.create":
            return self._create_worktree(request, started)
        if request.kind == "git.worktree.remove":
            return self._remove_worktree(request, started)
        raise ActionPolicyDenied(request.kind)

    def _require_computer_backend(self) -> None:
        if not self.computer_driver.available:
            reason = getattr(self.computer_driver, "reason", "computer-use backend unavailable")
            raise ComputerBackendUnavailable(str(reason))

    @staticmethod
    def _bounded_int(
        params: Mapping[str, Any], name: str, minimum: int, maximum: int, *, default: int | None = None
    ) -> int:
        value = params.get(name, default)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ActionPolicyDenied(f"{name} must be an integer")
        if not minimum <= value <= maximum:
            raise ActionPolicyDenied(f"{name} must be between {minimum} and {maximum}")
        return value

    def _completed(
        self,
        request: ActionRequest,
        started: str,
        *,
        stdout: str = "",
        stderr: str = "",
        truncated: bool = False,
        changed: Sequence[str] = (),
        reversible: bool = False,
        metadata: Mapping[str, Any] | None = None,
    ) -> ActionResult:
        return ActionResult(
            action_id=request.action_id,
            kind=request.kind,
            status="completed",
            started_at=started,
            completed_at=utc_now(),
            exit_code=0,
            stdout=stdout,
            stderr=stderr,
            output_truncated=truncated,
            changed_paths=tuple(changed),
            reversible=reversible,
            metadata=dict(metadata or {}),
        )

    def _run_command(self, request: ActionRequest, started: str) -> ActionResult:
        params = dict(request.parameters)
        argv = self._validated_argv(params.get("argv"))
        cwd = self._resolve_path(str(params.get("cwd", ".")), must_exist=True)
        timeout = min(int(params.get("timeout_seconds", self.policy.max_command_seconds)), self.policy.max_command_seconds)
        if timeout < 1:
            raise ActionPolicyDenied("timeout must be positive")
        env = self._child_environment(params.get("env", {}))
        process = subprocess.Popen(
            argv,
            cwd=str(cwd),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
            env=env,
            shell=False,
            start_new_session=(os.name != "nt"),
        )
        timed_out = False
        try:
            stdout_b, stderr_b = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            if os.name != "nt":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
            stdout_b, stderr_b = process.communicate()
        stdout, out_truncated = self._decode_limited(stdout_b)
        stderr, err_truncated = self._decode_limited(stderr_b)
        status = "failed" if timed_out or process.returncode != 0 else "completed"
        return ActionResult(
            action_id=request.action_id,
            kind=request.kind,
            status=status,
            started_at=started,
            completed_at=utc_now(),
            exit_code=process.returncode,
            stdout=stdout,
            stderr=stderr,
            output_truncated=out_truncated or err_truncated,
            reversible=False,
            metadata={"argv": argv, "cwd": self._relative(cwd), "timeout_seconds": timeout},
            error_code=ActionCommandTimedOut.code if timed_out else (ActionCommandFailed.code if process.returncode != 0 else None),
        )

    def _create_worktree(self, request: ActionRequest, started: str) -> ActionResult:
        params = dict(request.parameters)
        agent_id = self._canonical_token(self._required_str(params, "agent_id"), "agent_id")
        branch = self._branch_name(self._required_str(params, "branch"))
        ref = str(params.get("ref", "HEAD"))
        path = self.state_dir / "worktrees" / agent_id
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            raise ActionPolicyDenied(f"worktree path already exists: {path}")
        if self._connection.execute("SELECT 1 FROM worktrees WHERE agent_id=?", (agent_id,)).fetchone():
            raise ActionPolicyDenied(f"worktree already registered: {agent_id}")
        self._git(["worktree", "add", "-b", branch, str(path), ref])
        with self._connection:
            self._connection.execute(
                "INSERT INTO worktrees(agent_id, path, branch, created_by_action_id, created_at) VALUES (?, ?, ?, ?, ?)",
                (agent_id, str(path), branch, request.action_id, utc_now()),
            )
        return self._completed(
            request,
            started,
            changed=(self._relative(path),),
            reversible=True,
            metadata={"agent_id": agent_id, "branch": branch, "path": str(path)},
        )

    def _remove_worktree(self, request: ActionRequest, started: str) -> ActionResult:
        agent_id = self._canonical_token(self._required_str(request.parameters, "agent_id"), "agent_id")
        row = self._connection.execute(
            "SELECT path, branch FROM worktrees WHERE agent_id=?", (agent_id,)
        ).fetchone()
        if row is None:
            raise ActionNotFound(agent_id)
        force = bool(request.parameters.get("force", False))
        args = ["worktree", "remove"]
        if force:
            args.append("--force")
        args.append(row["path"])
        self._git(args)
        with self._connection:
            self._connection.execute("DELETE FROM worktrees WHERE agent_id=?", (agent_id,))
        return self._completed(
            request,
            started,
            changed=(self._relative(Path(row["path"])),),
            reversible=False,
            metadata={"agent_id": agent_id, "branch": row["branch"]},
        )

    # ---------- helpers ----------

    def _persist_result(self, result: ActionResult) -> None:
        persisted = result
        if result.kind == "computer.elements.list" and "elements" in result.metadata:
            metadata = dict(result.metadata)
            metadata.pop("elements", None)
            metadata["elements_persisted"] = False
            persisted = ActionResult(**{**asdict(result), "metadata": metadata})
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE actions SET result_json=?, status=?, completed_at=? WHERE action_id=?",
                (canonical_json(asdict(persisted)), result.status, result.completed_at, result.action_id),
            )

    def _validate_computer_selector(self, selector: Any) -> Mapping[str, Any]:
        if not isinstance(selector, Mapping) or not selector:
            raise ActionPolicyDenied("computer selector must be a non-empty object")
        # Reuse the matcher to validate supported fields without requiring a live backend.
        probe = ComputerElement(
            element_id="probe", backend="probe", role="probe", name="", class_name="", bounds=(0, 0, 1, 1)
        )
        try:
            element_matches(probe, selector)
        except ComputerSelectorError as exc:
            raise ActionPolicyDenied(str(exc)) from exc
        return selector

    @staticmethod
    def _validate_computer_fingerprint(value: Any) -> str:
        fingerprint = str(value or "").lower()
        if not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
            raise ActionPolicyDenied("expected_fingerprint must be a 64-character SHA-256 hex value")
        return fingerprint

    def _validate_ephemeral_input_reference(self, params: Mapping[str, Any]) -> None:
        handle = str(params.get("input_handle", ""))
        binding = str(params.get("input_binding", "")).lower()
        if not re.fullmatch(r"input-[0-9a-f]{32}", handle):
            raise ActionPolicyDenied("invalid ephemeral input handle")
        if not re.fullmatch(r"[0-9a-f]{64}", binding):
            raise ActionPolicyDenied("invalid ephemeral input binding")
        self._bounded_int(params, "text_length", 1, self.policy.max_computer_text_chars)

    def _resolve_computer_element(self, params: Mapping[str, Any]) -> ComputerElement:
        selector = self._validate_computer_selector(params.get("selector"))
        fingerprint = self._validate_computer_fingerprint(params.get("expected_fingerprint"))
        return resolve_unique_element(
            self.computer_driver, selector, expected_fingerprint=fingerprint, limit=self.policy.max_computer_elements
        )

    def _enforce_computer_window_policy(self, element: ComputerElement) -> None:
        title = element.window_title or ""
        if self.policy.computer_window_denylist and any(
            fnmatch.fnmatchcase(title, pattern) for pattern in self.policy.computer_window_denylist
        ):
            raise ActionPolicyDenied("target window is denied by computer_window_denylist")
        if self.policy.computer_window_allowlist and not any(
            fnmatch.fnmatchcase(title, pattern) for pattern in self.policy.computer_window_allowlist
        ):
            raise ActionPolicyDenied("target window is outside computer_window_allowlist")

    def _cleanup_computer_captures(self) -> None:
        cutoff = time.time() - max(1, int(self.policy.computer_capture_ttl_seconds))
        for path in self.computer_capture_root.glob("*.png"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink(missing_ok=True)
            except OSError:
                continue

    def _coerce_approval(self, value: ActionApproval | Mapping[str, Any] | None) -> ActionApproval | None:
        if value is None or isinstance(value, ActionApproval):
            return value
        try:
            return ActionApproval(**dict(value))
        except TypeError as exc:
            raise ActionApprovalInvalid("invalid approval structure") from exc

    def _validate_approval(self, approval: ActionApproval, plan: ActionPlan, action_id: str) -> None:
        unsigned = {
            "approval_id": approval.approval_id,
            "action_digest": approval.action_digest,
            "issued_by": approval.issued_by,
            "issued_at": approval.issued_at,
            "expires_at": approval.expires_at,
            "one_time": approval.one_time,
        }
        expected = hmac.new(
            self._secret, canonical_json(unsigned).encode("utf-8"), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected, approval.signature):
            raise ActionApprovalInvalid("approval signature mismatch")
        if approval.action_digest != plan.action_digest:
            raise ActionApprovalInvalid("approval does not match this action")
        try:
            expires = datetime.fromisoformat(approval.expires_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ActionApprovalInvalid("approval expiry invalid") from exc
        if datetime.now(timezone.utc) >= expires:
            raise ActionApprovalInvalid("approval expired")
        row = self._connection.execute(
            "SELECT approval_json, consumed_by_action_id FROM approvals WHERE approval_id=?",
            (approval.approval_id,),
        ).fetchone()
        if row is None or row["approval_json"] != canonical_json(asdict(approval)):
            raise ActionApprovalInvalid("approval is not registered")
        if approval.one_time and row["consumed_by_action_id"] not in (None, action_id):
            raise ActionApprovalInvalid("approval already consumed")

    def _resolve_path(self, value: str, *, must_exist: bool) -> Path:
        raw = Path(value).expanduser()
        candidate = raw if raw.is_absolute() else self.root / raw
        if must_exist:
            try:
                resolved = candidate.resolve(strict=True)
            except FileNotFoundError as exc:
                raise ActionNotFound(str(value)) from exc
        else:
            parent = candidate.parent.resolve(strict=True) if candidate.parent.exists() else self._nearest_existing_parent(candidate.parent)
            resolved = (parent / candidate.name).resolve(strict=False)
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise ActionPolicyDenied(f"Path escapes workspace: {value}") from exc
        if not self.policy.allow_symlinks:
            self._reject_symlink_components(candidate)
        return resolved

    def _nearest_existing_parent(self, path: Path) -> Path:
        current = path
        missing: list[str] = []
        while not current.exists():
            missing.append(current.name)
            if current == current.parent:
                raise ActionPolicyDenied("No existing parent for path")
            current = current.parent
        resolved = current.resolve(strict=True)
        for part in reversed(missing):
            resolved = resolved / part
        return resolved

    def _reject_symlink_components(self, path: Path) -> None:
        current = path if path.is_absolute() else self.root / path
        try:
            relative = current.relative_to(self.root)
        except ValueError:
            relative = current
        probe = self.root
        for part in relative.parts:
            probe = probe / part
            if probe.exists():
                is_link = probe.is_symlink()
                if not is_link and os.name == "nt":
                    try:
                        os.readlink(probe)
                        is_link = True
                    except (OSError, ValueError, AttributeError):
                        pass
                if is_link:
                    raise ActionPolicyDenied(f"Symlink paths are disabled: {probe}")

    def _resolve_staging_path(self, relative_path: str) -> Path:
        raw = Path(str(relative_path).replace("\\", "/"))
        if raw.is_absolute() or any(part in {"", ".", ".."} for part in raw.parts):
            raise ActionPolicyDenied("staging_path must be canonical and state-relative")
        staging_root = (self.state_dir / "office-staging").resolve()
        staging_root.mkdir(parents=True, exist_ok=True)
        candidate = (self.state_dir / raw).resolve()
        if candidate != staging_root and staging_root not in candidate.parents:
            raise ActionPolicyDenied("staging_path must be inside office-staging")
        if not candidate.is_file() or candidate.is_symlink():
            raise ActionPolicyDenied("staged file does not exist")
        return candidate

    def _deny_protected_path(self, path: Path) -> None:
        relative = self._relative(path)
        for pattern in self.policy.denied_path_globs:
            base = pattern[:-3] if pattern.endswith("/**") else pattern
            if relative == base or relative.startswith(base + "/") or fnmatch.fnmatch(relative, pattern):
                raise ActionPolicyDenied(f"Protected path: {relative}")

    def _relative(self, path: Path) -> str:
        return path.resolve(strict=False).relative_to(self.root).as_posix()

    def _required_str(self, params: Mapping[str, Any], name: str) -> str:
        value = params.get(name)
        if not isinstance(value, str) or not value:
            raise ActionPolicyDenied(f"{name} must be a non-empty string")
        return value

    def _canonical_token(self, value: str, name: str) -> str:
        if not ACTION_ID_RE.fullmatch(value):
            raise ActionPolicyDenied(f"{name} must be canonical lowercase identifier")
        return value

    def _branch_name(self, value: str) -> str:
        if not value or value.startswith("-") or ".." in value or value.endswith("."):
            raise ActionPolicyDenied("invalid branch name")
        if any(char.isspace() for char in value) or any(char in value for char in "~^:?*[\\"):
            raise ActionPolicyDenied("invalid branch name")
        return value

    def _is_denied_executable(self, raw_executable: str) -> bool:
        name = Path(raw_executable).name.lower()
        denied_lower = {item.lower() for item in self.policy.denied_commands}
        if name in denied_lower:
            return True
        if name.endswith(".exe") and name[:-4] in denied_lower:
            return True
        return False

    def _canonical_executable(self, raw_executable: str) -> str:
        name = Path(raw_executable).name.lower()
        if name.endswith(".exe"):
            base = name[:-4]
            allowed_lower = {item.lower() for item in self.policy.allowed_commands}
            if base in allowed_lower:
                return base
        return name

    def _is_allowed_executable(self, raw_executable: str) -> tuple[bool, str]:
        canonical = self._canonical_executable(raw_executable)
        allowed_lower = {item.lower() for item in self.policy.allowed_commands}
        if canonical in allowed_lower:
            return True, canonical
        raw_name = Path(raw_executable).name.lower()
        if raw_name in allowed_lower:
            return True, raw_name
        return False, raw_name

    def _validated_argv(self, value: Any) -> list[str]:
        if not isinstance(value, list) or not value or not all(isinstance(item, str) and item for item in value):
            raise ActionPolicyDenied("argv must be a non-empty string array")
        if len(value) > 128:
            raise ActionPolicyDenied("argv too long")
        raw_name = Path(value[0]).name.lower()
        if self._is_denied_executable(raw_name):
            raise ActionPolicyDenied(f"Command denied: {raw_name}")
        allowed, canonical = self._is_allowed_executable(raw_name)
        if not allowed:
            raise ActionPolicyDenied(f"Command not allowlisted: {raw_name}")
        for argument in value:
            if "\x00" in argument or SHELL_METACHARACTERS.search(argument):
                raise ActionPolicyDenied("shell syntax is not accepted; pass direct argv only")
        if canonical == "git":
            self._validate_git_argv(value)
        return list(value)

    def _validate_git_argv(self, argv: Sequence[str]) -> None:
        if len(argv) < 2:
            raise ActionPolicyDenied("git subcommand required")
        subcommand = argv[1]
        denied = {"push", "fetch", "pull", "clone", "remote", "clean", "reset", "rebase", "gc", "prune"}
        if subcommand in denied:
            raise ActionPolicyDenied(f"git {subcommand} is denied in generic command mode")

    def _command_is_read_only(self, argv: Sequence[str]) -> bool:
        canonical = self._canonical_executable(argv[0])
        if canonical == "git" and len(argv) > 1:
            return argv[1] in {"status", "diff", "log", "show", "rev-parse", "ls-files", "branch"}
        if canonical in {"python", "python3"} and len(argv) == 2 and argv[1] in {"--version", "-V"}:
            return True
        return False

    def _child_environment(self, supplied: Any) -> dict[str, str]:
        if not isinstance(supplied, Mapping):
            raise ActionPolicyDenied("env must be an object")
        env: dict[str, str] = {}
        for name in self.policy.environment_allowlist:
            value = os.environ.get(name)
            if value is not None and not SENSITIVE_ENV_RE.search(name):
                env[name] = value
        for name, value in supplied.items():
            if not isinstance(name, str) or not isinstance(value, str):
                raise ActionPolicyDenied("env values must be strings")
            if SENSITIVE_ENV_RE.search(name):
                raise ActionPolicyDenied(f"Sensitive environment variable denied: {name}")
            env[name] = value
        for proxy in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
            env.pop(proxy, None)
        if not self.policy.allow_network:
            env["NO_PROXY"] = "*"
            env["W1_NETWORK_POLICY"] = "denied-best-effort"
        return env

    def _decode_limited(self, value: bytes) -> tuple[str, bool]:
        truncated = len(value) > self.policy.max_output_bytes
        selected = value[: self.policy.max_output_bytes]
        return selected.decode("utf-8", errors="replace"), truncated

    def _backup_path(self, action_id: str, path: Path, ordinal: int) -> None:
        existed = path.exists() or path.is_symlink()
        was_directory = bool(existed and path.is_dir() and not path.is_symlink())
        destination: Path | None = None
        if existed:
            size = self._path_size(path)
            if size > self.policy.max_backup_bytes:
                raise ActionPolicyDenied(
                    f"Backup would exceed {self.policy.max_backup_bytes} bytes: {self._relative(path)}"
                )
            destination = self.backup_root / action_id / f"{ordinal}"
            destination.parent.mkdir(parents=True, exist_ok=True)
            if was_directory:
                shutil.copytree(path, destination, symlinks=False)
            else:
                shutil.copy2(path, destination, follow_symlinks=False)
        with self._connection:
            self._connection.execute(
                "INSERT INTO backups(action_id, ordinal, relative_path, backup_path, existed_before, was_directory) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    action_id,
                    ordinal,
                    self._relative(path),
                    str(destination) if destination else None,
                    1 if existed else 0,
                    1 if was_directory else 0,
                ),
            )

    def _restore_backups(self, action_id: str) -> list[str]:
        rows = self._connection.execute(
            "SELECT ordinal, relative_path, backup_path, existed_before, was_directory "
            "FROM backups WHERE action_id=? ORDER BY ordinal DESC",
            (action_id,),
        ).fetchall()
        if not rows:
            raise ActionNotReversible(f"No backup for {action_id}")
        changed: list[str] = []
        for row in rows:
            target = self._resolve_path(row["relative_path"], must_exist=False)
            if target.exists() or target.is_symlink():
                if target.is_dir() and not target.is_symlink():
                    shutil.rmtree(target)
                else:
                    target.unlink()
            if row["existed_before"]:
                source = Path(row["backup_path"])
                target.parent.mkdir(parents=True, exist_ok=True)
                if row["was_directory"]:
                    shutil.copytree(source, target)
                else:
                    shutil.copy2(source, target)
            changed.append(row["relative_path"])
        return changed

    def _path_size(self, path: Path) -> int:
        if path.is_file() or path.is_symlink():
            return path.stat().st_size
        total = 0
        for item in path.rglob("*"):
            if item.is_file() and not item.is_symlink():
                total += item.stat().st_size
                if total > self.policy.max_backup_bytes:
                    break
        return total

    def _ensure_git_repository(self) -> None:
        result = self._git(["rev-parse", "--show-toplevel"], check=False)
        if result.returncode != 0:
            raise ActionPolicyDenied("workspace is not a Git repository")
        root = Path(result.stdout.strip()).resolve()
        if root != self.root:
            raise ActionPolicyDenied("workspace root must equal Git repository root")

    def _git(self, arguments: Sequence[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            ["git", *arguments],
            cwd=str(self.root),
            stdin=subprocess.DEVNULL,
            text=True,
            capture_output=True,
            timeout=self.policy.max_command_seconds,
            env=self._child_environment({}),
            check=False,
        )
        if check and result.returncode != 0:
            raise ActionPolicyDenied(result.stderr.strip() or f"git {' '.join(arguments)} failed")
        return result


__all__ = [
    "ActionApproval",
    "ActionApprovalInvalid",
    "ActionApprovalRequired",
    "ActionCommandFailed",
    "ActionCommandTimedOut",
    "ActionNotFound",
    "ActionNotReversible",
    "ActionPlan",
    "ActionPolicy",
    "ActionPolicyDenied",
    "ActionRequest",
    "ActionResult",
    "ActionRuntime",
    "ActionRuntimeError",
    "action_digest",
]
