"""Local-first W1 Nexus Workspace and live operations console.

The console is deliberately dependency-light: a hardened loopback HTTP server,
static packaged assets, read-only SQLite snapshots, and a small opt-in operations
surface for integrity checks and deterministic demo execution.
"""

from __future__ import annotations

import hashlib
import html
import importlib.resources
import json
import os
import secrets
import sqlite3
import subprocess
import threading
import time
import urllib.parse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping, Sequence

from .cli_support import WorkspacePaths, load_json, trusted_recorders
from .reference_demo import run_reference_demo
from .session_store import SessionNotFoundError, SessionStore

CONSOLE_VERSION = "0.1.0-dev50"
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _json_loads(value: Any, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except (json.JSONDecodeError, TypeError, ValueError):
        return default


def _safe_text(value: Any, limit: int = 800) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _read_rows(path: Path, sql: str, parameters: Sequence[Any] = ()) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    connection: sqlite3.Connection | None = None
    try:
        uri = f"file:{path.as_posix()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=0.25)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        rows = connection.execute(sql, tuple(parameters)).fetchall()
        return [dict(row) for row in rows]
    except (sqlite3.Error, OSError):
        return []
    finally:
        if connection is not None:
            connection.close()


def _count(path: Path, table: str, where: str = "1=1", parameters: Sequence[Any] = ()) -> int:
    rows = _read_rows(path, f"SELECT COUNT(*) AS total FROM {table} WHERE {where}", parameters)
    return int(rows[0]["total"]) if rows else 0


def _asset_text(name: str) -> str:
    return importlib.resources.files("w1cip.workspace_assets").joinpath(name).read_text(encoding="utf-8")


@dataclass(frozen=True)
class ConsoleSettings:
    host: str = "127.0.0.1"
    port: int = 8765
    allow_operations: bool = False
    poll_seconds: float = 1.0

    def validate(self) -> None:
        if self.host not in LOOPBACK_HOSTS:
            raise ValueError("workspace_console_loopback_only")
        if not (0 <= self.port <= 65535):
            raise ValueError("workspace_console_invalid_port")
        if self.poll_seconds < 0.25:
            raise ValueError("workspace_console_poll_too_fast")


class WorkspaceSnapshotService:
    """Build redaction-safe operational snapshots from W1 workspace state."""

    def __init__(self, paths: WorkspacePaths) -> None:
        self.paths = paths

    @property
    def parallel_db(self) -> Path:
        return self.paths.state_dir / "parallel-runtime.sqlite3"

    @property
    def action_db(self) -> Path:
        return self.paths.state_dir / "action-runtime.sqlite3"

    @property
    def sandbox_db(self) -> Path:
        return self.paths.state_dir / "secure-execution.sqlite3"

    def snapshot(self) -> dict[str, Any]:
        config = load_json(self.paths.config) if self.paths.config.is_file() else {}
        sessions = self._sessions()
        runs = self._runs()
        agents = self._agents()
        providers = self._providers(runs)
        approvals = self._approvals()
        memory = self._memory()
        science = self._science()
        sandboxes = self._sandboxes()
        merges = self._merges()
        tools = self._tools()
        actions = self._actions()
        audit = self._audit_feed()
        git = self._git()
        final_result = next((item.get("result") for item in runs if item.get("result")), None)

        active_run_states = {"running", "blocked", "awaiting_user", "awaiting_reset", "awaiting_reconciliation"}
        active_agent_states = {"pending", "running", "blocked"}
        metrics = {
            "sessions": len(sessions),
            "active_runs": sum(1 for item in runs if item.get("status") in active_run_states),
            "active_agents": sum(1 for item in agents["jobs"] if item.get("status") in active_agent_states),
            "pending_approvals": len(approvals),
            "memory_records": memory["counts"].get("total", 0),
            "scientific_studies": len(science["studies"]),
            "sandbox_executions": len(sandboxes),
            "tool_calls": tools["total_calls"],
        }
        health = self._health(metrics)
        payload = {
            "console": {
                "version": CONSOLE_VERSION,
                "generated_at": utc_now(),
                "read_only": True,
            },
            "workspace": {
                "name": config.get("name", self.paths.root.name),
                "root": str(self.paths.root),
                "workspace_version": config.get("workspace_version"),
                "project_id": config.get("memory", {}).get("project_id", "default-project"),
                "namespace_id": config.get("memory", {}).get("namespace_id", "w1-local"),
            },
            "metrics": metrics,
            "health": health,
            "sessions": sessions,
            "runs": runs,
            "agents": agents,
            "providers": providers,
            "approvals": approvals,
            "memory": memory,
            "science": science,
            "sandboxes": sandboxes,
            "merges": merges,
            "tools": tools,
            "actions": actions,
            "git": git,
            "audit": audit,
            "final_result": final_result,
        }
        payload["snapshot_digest"] = hashlib.sha256(canonical_json(payload).encode()).hexdigest()
        return payload

    def _sessions(self) -> list[dict[str, Any]]:
        rows = _read_rows(
            self.paths.session_db,
            "SELECT session_id, created_at, last_sequence, last_recorded_at, last_event_hash "
            "FROM sessions ORDER BY COALESCE(last_recorded_at, created_at) DESC LIMIT 30",
        )
        for row in rows:
            row["event_count"] = int(row.get("last_sequence") or 0)
            row["last_event_hash"] = _safe_text(row.get("last_event_hash"), 18)
        return rows

    def _runs(self) -> list[dict[str, Any]]:
        rows = _read_rows(
            self.paths.journal_db,
            "SELECT run_id, session_id, status, resource_plan_json, created_at, updated_at, "
            "last_error_code, result_json FROM orchestrator_runs ORDER BY updated_at DESC LIMIT 25",
        )
        task_rows = _read_rows(
            self.paths.journal_db,
            "SELECT run_id, task_id, ordinal, status, attempts, resource_id, error_code, "
            "requires_extra_review FROM orchestrator_tasks ORDER BY run_id, ordinal",
        )
        tasks: dict[str, list[dict[str, Any]]] = {}
        for task in task_rows:
            task["requires_extra_review"] = bool(task.get("requires_extra_review"))
            tasks.setdefault(str(task["run_id"]), []).append(task)
        for row in rows:
            row["tasks"] = tasks.get(str(row["run_id"]), [])
            row["resource_plan"] = _json_loads(row.pop("resource_plan_json", None), {})
            row["result"] = _json_loads(row.pop("result_json", None), None)
        return rows

    def _agents(self) -> dict[str, Any]:
        runs = _read_rows(
            self.parallel_db,
            "SELECT run_id, status, base_commit, target_branch, created_at, completed_at, cancelled_at "
            "FROM parallel_runs ORDER BY created_at DESC LIMIT 20",
        )
        jobs = _read_rows(
            self.parallel_db,
            "SELECT run_id, job_id, agent_id, status, result_json, worktree_path, started_at, completed_at "
            "FROM agent_jobs ORDER BY COALESCE(started_at, completed_at) DESC LIMIT 100",
        )
        for row in jobs:
            result = _json_loads(row.pop("result_json", None), {}) or {}
            row["branch"] = result.get("branch")
            row["commit_sha"] = _safe_text(result.get("commit_sha"), 14)
            row["changed_files"] = result.get("changed_files", [])
            row["error_code"] = result.get("error_code")
        return {"runs": runs, "jobs": jobs}

    def _providers(self, runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        resources: dict[str, dict[str, Any]] = {}
        profile_rows = _read_rows(
            self.paths.model_access_db,
            "SELECT model_id, profile_json, updated_at FROM model_profiles ORDER BY model_id",
        )
        for row in profile_rows:
            profile = _json_loads(row.get("profile_json"), {}) or {}
            model_id = str(row.get("model_id"))
            resources[model_id] = {
                "resource_id": profile.get("connector_resource_id") or model_id,
                "model_id": model_id,
                "provider": profile.get("provider_id") or "user-managed",
                "model": profile.get("model_name") or model_id,
                "access_mode": profile.get("access_mode"),
                "privacy_mode": profile.get("privacy_mode"),
                "enabled": profile.get("enabled", True),
                "roles": profile.get("roles", []),
                "quota_status": "user-managed",
                "remaining_units": None,
                "reserved_units": 0,
                "limit_units": None,
                "reset_at": None,
                "fitness_score": None,
            }
        for run in runs:
            plan = run.get("resource_plan") or {}
            payload = plan.get("payload", plan) if isinstance(plan, dict) else {}
            for item in payload.get("resources", []) if isinstance(payload, dict) else []:
                if not isinstance(item, dict) or not item.get("resource_id"):
                    continue
                quota = item.get("quota", {}) or {}
                resources[str(item["resource_id"])] = {
                    "resource_id": item.get("resource_id"),
                    "provider": item.get("provider") or item.get("provider_id") or "provider",
                    "model": item.get("model") or item.get("model_id") or item.get("resource_id"),
                    "quota_status": quota.get("status", item.get("quota_status", "unknown")),
                    "remaining_units": quota.get("remaining_units"),
                    "reserved_units": quota.get("reserved_units", 0),
                    "limit_units": quota.get("limit_units"),
                    "reset_at": quota.get("reset_at"),
                    "fitness_score": item.get("fitness_score"),
                }
        observations = _read_rows(
            self.paths.journal_db,
            "SELECT resource_id, provider, observed_at, observation_json FROM provider_quota_observations "
            "ORDER BY observation_id DESC LIMIT 100",
        )
        for observation in observations:
            resource_id = str(observation.get("resource_id"))
            entry = resources.setdefault(resource_id, {
                "resource_id": resource_id,
                "provider": observation.get("provider"),
                "model": resource_id,
                "quota_status": "unknown",
                "remaining_units": None,
                "reserved_units": 0,
                "limit_units": None,
                "reset_at": None,
                "fitness_score": None,
            })
            if not entry.get("last_observed_at"):
                data = _json_loads(observation.get("observation_json"), {}) or {}
                entry["last_observed_at"] = observation.get("observed_at")
                entry["provider_observation"] = data
        return sorted(resources.values(), key=lambda item: str(item.get("resource_id")))

    def _approvals(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        action_rows = _read_rows(
            self.action_db,
            "SELECT approval_id, action_digest, approval_json FROM approvals WHERE consumed_by_action_id IS NULL",
        )
        for row in action_rows:
            data = _json_loads(row.get("approval_json"), {}) or {}
            result.append({
                "approval_id": row.get("approval_id"),
                "source": "action_runtime",
                "subject": data.get("summary") or _safe_text(row.get("action_digest"), 16),
                "issued_by": data.get("issued_by"),
                "expires_at": data.get("expires_at"),
                "risk": data.get("risk") or "sensitive_action",
            })
        tool_rows = _read_rows(
            self.paths.tool_registry_db,
            "SELECT approval_id, call_digest, issued_by, expires_at FROM tool_approvals "
            "WHERE consumed_by_call_id IS NULL ORDER BY issued_at DESC",
        )
        for row in tool_rows:
            result.append({
                "approval_id": row.get("approval_id"),
                "source": "tool_registry",
                "subject": f"Tool call {_safe_text(row.get('call_digest'), 16)}",
                "issued_by": row.get("issued_by"),
                "expires_at": row.get("expires_at"),
                "risk": "external_tool",
            })
        return result

    def _memory(self) -> dict[str, Any]:
        total = _count(self.paths.memory_db, "memories")
        conflicts = _count(self.paths.memory_db, "memory_conflicts", "status = 'open'")
        rows = _read_rows(
            self.paths.memory_db,
            "SELECT m.memory_id, m.project_id, m.canonical_key, m.current_status, m.updated_at, "
            "r.kind, r.subject, r.predicate, r.summary, r.confidence, r.sensitivity, r.visibility "
            "FROM memories m JOIN memory_revisions r ON r.memory_id=m.memory_id AND r.revision=m.current_revision "
            "ORDER BY m.updated_at DESC LIMIT 40",
        )
        return {"counts": {"total": total, "open_conflicts": conflicts}, "records": rows}

    def _science(self) -> dict[str, Any]:
        studies = _read_rows(
            self.paths.science_db,
            "SELECT study_id, title, status, project_id, created_at, preregistered_at, updated_at "
            "FROM studies ORDER BY updated_at DESC LIMIT 30",
        )
        for study in studies:
            study_id = study.get("study_id")
            study["observations"] = _count(self.paths.science_db, "observations", "study_id = ?", (study_id,))
            study["analyses"] = _count(self.paths.science_db, "analyses", "study_id = ?", (study_id,))
            reviews = _read_rows(
                self.paths.science_db,
                "SELECT outcome, reviewed_at, reviewer_id FROM scientific_reviews WHERE study_id=? ORDER BY reviewed_at DESC LIMIT 1",
                (study_id,),
            )
            study["review"] = reviews[0] if reviews else None
        return {"studies": studies}

    def _sandboxes(self) -> list[dict[str, Any]]:
        rows = _read_rows(
            self.sandbox_db,
            "SELECT execution_id, profile_id, status, backend, result_json, started_at, completed_at "
            "FROM sandbox_executions ORDER BY started_at DESC LIMIT 40",
        )
        for row in rows:
            result = _json_loads(row.pop("result_json", None), {}) or {}
            telemetry = result.get("telemetry", {}) if isinstance(result, dict) else {}
            row["wall_seconds"] = telemetry.get("wall_seconds")
            row["peak_rss_bytes"] = telemetry.get("peak_rss_bytes")
            row["exit_code"] = result.get("exit_code")
            row["artifact_count"] = len(result.get("artifacts", []) or [])
            row["error_code"] = result.get("error_code")
        return rows

    def _merges(self) -> list[dict[str, Any]]:
        rows = _read_rows(
            self.parallel_db,
            "SELECT proposal_id, run_id, proposal_json, review_json, applied_at, applied_by "
            "FROM merge_proposals ORDER BY rowid DESC LIMIT 30",
        )
        result: list[dict[str, Any]] = []
        for row in rows:
            proposal = _json_loads(row.get("proposal_json"), {}) or {}
            review = _json_loads(row.get("review_json"), None)
            result.append({
                "proposal_id": row.get("proposal_id"),
                "run_id": row.get("run_id"),
                "status": proposal.get("status"),
                "target_branch": proposal.get("target_branch"),
                "source_branches": proposal.get("source_branches", []),
                "potential_conflicts": proposal.get("potential_conflicts", []),
                "test_exit_code": proposal.get("test_exit_code"),
                "review": review,
                "applied_at": row.get("applied_at"),
                "applied_by": row.get("applied_by"),
            })
        return result

    def _tools(self) -> dict[str, Any]:
        calls = _read_rows(
            self.paths.tool_registry_db,
            "SELECT call_id, tool_name, status, started_at, completed_at, error_code FROM tool_calls "
            "ORDER BY started_at DESC LIMIT 40",
        )
        return {"total_calls": _count(self.paths.tool_registry_db, "tool_calls"), "calls": calls}

    def _actions(self) -> list[dict[str, Any]]:
        return _read_rows(
            self.action_db,
            "SELECT action_id, kind, status, requested_by, created_at, completed_at, undo_of_action_id "
            "FROM actions ORDER BY created_at DESC LIMIT 40",
        )

    def _audit_feed(self) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for row in _read_rows(
            self.paths.session_db,
            "SELECT session_id, sequence, message_type, entity_type, entity_id, recorded_at "
            "FROM events ORDER BY COALESCE(recorded_at, '') DESC LIMIT 60",
        ):
            events.append({
                "source": "protocol",
                "time": row.get("recorded_at"),
                "type": row.get("message_type"),
                "subject": f"{row.get('entity_type') or 'event'}:{row.get('entity_id') or row.get('session_id')}",
                "detail": f"sequence {row.get('sequence')}",
            })
        for row in _read_rows(
            self.parallel_db,
            "SELECT event_json, occurred_at FROM parallel_events ORDER BY sequence DESC LIMIT 40",
        ):
            data = _json_loads(row.get("event_json"), {}) or {}
            events.append({
                "source": "agents",
                "time": row.get("occurred_at"),
                "type": data.get("event_type"),
                "subject": data.get("agent_id") or data.get("job_id") or data.get("run_id"),
                "detail": _safe_text(data.get("details"), 180),
            })
        for row in _read_rows(
            self.paths.science_db,
            "SELECT event_type, study_id, actor_id, occurred_at FROM scientific_events ORDER BY sequence DESC LIMIT 30",
        ):
            events.append({
                "source": "science",
                "time": row.get("occurred_at"),
                "type": row.get("event_type"),
                "subject": row.get("study_id"),
                "detail": f"actor {row.get('actor_id')}",
            })
        for row in self._actions()[:20]:
            events.append({
                "source": "actions",
                "time": row.get("completed_at") or row.get("created_at"),
                "type": row.get("status"),
                "subject": row.get("action_id"),
                "detail": row.get("kind"),
            })
        events.sort(key=lambda item: str(item.get("time") or ""), reverse=True)
        return events[:100]

    def _git(self) -> dict[str, Any]:
        def run(*argv: str) -> tuple[int, str]:
            try:
                completed = subprocess.run(
                    ["git", *argv], cwd=self.paths.root, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, text=True, timeout=2, check=False,
                )
                return completed.returncode, completed.stdout.strip()
            except (OSError, subprocess.SubprocessError):
                return 1, ""

        code, branch = run("branch", "--show-current")
        if code != 0:
            return {"available": False, "branch": None, "dirty": False, "changes": [], "commits": []}
        _, status = run("status", "--porcelain=v1")
        _, log = run("log", "-6", "--pretty=format:%h%x09%s%x09%an%x09%aI")
        changes = [line for line in status.splitlines() if line and ".w1nexus/" not in line]
        commits = []
        for line in log.splitlines():
            parts = line.split("\t", 3)
            if len(parts) == 4:
                commits.append({"sha": parts[0], "subject": parts[1], "author": parts[2], "time": parts[3]})
        return {"available": True, "branch": branch or "detached", "dirty": bool(changes), "changes": changes[:40], "commits": commits}

    def _health(self, metrics: Mapping[str, int]) -> list[dict[str, Any]]:
        components = [
            ("Protocol ledger", self.paths.session_db, metrics.get("sessions", 0)),
            ("Orchestrator", self.paths.journal_db, metrics.get("active_runs", 0)),
            ("Memory fabric", self.paths.memory_db, metrics.get("memory_records", 0)),
            ("Scientific lab", self.paths.science_db, metrics.get("scientific_studies", 0)),
            ("Tool registry", self.paths.tool_registry_db, metrics.get("tool_calls", 0)),
            ("Parallel runtime", self.parallel_db, metrics.get("active_agents", 0)),
            ("Secure execution", self.sandbox_db, metrics.get("sandbox_executions", 0)),
            ("Action runtime", self.action_db, metrics.get("pending_approvals", 0)),
        ]
        return [
            {
                "component": name,
                "state": "online" if path.is_file() else "ready",
                "detail": f"{value} records" if path.is_file() else "initializes on first use",
            }
            for name, path, value in components
        ]


class WorkspaceConsoleApplication:
    def __init__(self, paths: WorkspacePaths, settings: ConsoleSettings) -> None:
        settings.validate()
        self.paths = paths
        self.settings = settings
        self.service = WorkspaceSnapshotService(paths)
        self.token_path = paths.state_dir / "workspace-console.token"
        self.token = self._load_or_create_token()
        self._operation_lock = threading.RLock()

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

    def snapshot(self) -> dict[str, Any]:
        payload = self.service.snapshot()
        payload["console"]["read_only"] = not self.settings.allow_operations
        return payload

    def bootstrap(self) -> dict[str, Any]:
        return {
            "token": self.token,
            "allowOperations": self.settings.allow_operations,
            "workspaceName": self.paths.root.name,
            "consoleVersion": CONSOLE_VERSION,
        }

    def operation(self, name: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        if name not in {"verify_session", "verify_all_sessions"} and not self.settings.allow_operations:
            raise PermissionError("workspace_console_operations_disabled")
        with self._operation_lock:
            if name == "verify_session":
                session_id = str(payload.get("session_id") or "")
                if not session_id:
                    raise ValueError("session_id_required")
                with SessionStore(self.paths.session_db, trusted_recorders=trusted_recorders(self.paths)) as store:
                    report = store.verify_integrity(session_id)
                return {"operation": name, "report": asdict(report)}
            if name == "verify_all_sessions":
                results = []
                with SessionStore(self.paths.session_db, trusted_recorders=trusted_recorders(self.paths)) as store:
                    for summary in store.list_sessions():
                        report = store.verify_integrity(summary.session_id)
                        results.append({"session_id": summary.session_id, "valid": report.valid, "errors": list(report.errors)})
                return {"operation": name, "results": results, "valid": all(item["valid"] for item in results)}
            if name == "rebuild_session":
                session_id = str(payload.get("session_id") or "")
                if not session_id:
                    raise ValueError("session_id_required")
                with SessionStore(self.paths.session_db, trusted_recorders=trusted_recorders(self.paths)) as store:
                    state = store.rebuild_projections(session_id)
                return {"operation": name, "session_id": session_id, "latest_entities": len(state.latest_versions)}
            if name == "run_reference_demo":
                result, audit = run_reference_demo(self.paths.root, reset=bool(payload.get("reset", False)))
                return {"operation": name, "status": result.status, "run_id": result.run_id, "audit": audit}
        raise ValueError("workspace_console_unknown_operation")


class WorkspaceConsoleServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], application: WorkspaceConsoleApplication) -> None:
        self.application = application
        super().__init__(address, WorkspaceConsoleHandler)


class WorkspaceConsoleHandler(BaseHTTPRequestHandler):
    server: WorkspaceConsoleServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
        )
        self.send_header("Cache-Control", "no-store")

    def _send_bytes(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self._security_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, value: Any) -> None:
        self._send_bytes(status, (canonical_json(value) + "\n").encode(), "application/json; charset=utf-8")

    def _authorised(self, query: Mapping[str, list[str]] | None = None) -> bool:
        header = self.headers.get("Authorization", "")
        if header.startswith("Bearer ") and secrets.compare_digest(header[7:], self.server.application.token):
            return True
        if query:
            candidate = query.get("token", [""])[0]
            if candidate and secrets.compare_digest(candidate, self.server.application.token):
                return True
        return False

    def _valid_host(self) -> bool:
        raw = self.headers.get("Host", "")
        host = raw.rsplit(":", 1)[0].strip("[]").lower()
        return host in LOOPBACK_HOSTS

    def do_GET(self) -> None:
        if not self._valid_host():
            self._send_json(HTTPStatus.MISDIRECTED_REQUEST, {"error": "workspace_console_invalid_host"})
            return
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        if parsed.path == "/":
            html_text = _asset_text("index.html").replace(
                "__W1_BOOTSTRAP__", html.escape(json.dumps(self.server.application.bootstrap(), ensure_ascii=False), quote=True)
            )
            self._send_bytes(HTTPStatus.OK, html_text.encode(), "text/html; charset=utf-8")
            return
        if parsed.path.startswith("/assets/"):
            name = Path(parsed.path).name
            if name not in {"styles.css", "app.js", "logo.svg"}:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "asset_not_found"})
                return
            content_type = {
                ".css": "text/css; charset=utf-8",
                ".js": "text/javascript; charset=utf-8",
                ".svg": "image/svg+xml; charset=utf-8",
            }[Path(name).suffix]
            self._send_bytes(HTTPStatus.OK, _asset_text(name).encode(), content_type)
            return
        if parsed.path.startswith("/api/") and not self._authorised(query):
            self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "workspace_console_unauthorized"})
            return
        if parsed.path == "/api/health":
            self._send_json(HTTPStatus.OK, {"ok": True, "version": CONSOLE_VERSION, "time": utc_now()})
            return
        if parsed.path == "/api/snapshot":
            self._send_json(HTTPStatus.OK, self.server.application.snapshot())
            return
        if parsed.path == "/api/stream":
            self._stream_snapshots()
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "route_not_found"})

    def do_POST(self) -> None:
        if not self._valid_host():
            self._send_json(HTTPStatus.MISDIRECTED_REQUEST, {"error": "workspace_console_invalid_host"})
            return
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        if not self._authorised(query):
            self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "workspace_console_unauthorized"})
            return
        if parsed.path != "/api/operation":
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "route_not_found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 1_000_000:
                raise ValueError("request_too_large")
            payload = json.loads(self.rfile.read(length).decode() or "{}")
            if not isinstance(payload, dict):
                raise ValueError("json_object_required")
            name = str(payload.pop("operation", ""))
            result = self.server.application.operation(name, payload)
            self._send_json(HTTPStatus.OK, {"ok": True, "result": result})
        except PermissionError as exc:
            self._send_json(HTTPStatus.FORBIDDEN, {"ok": False, "error": str(exc)})
        except SessionNotFoundError:
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "session_not_found"})
        except (ValueError, json.JSONDecodeError) as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
        except Exception as exc:
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": type(exc).__name__})

    def _stream_snapshots(self) -> None:
        self.send_response(HTTPStatus.OK)
        self._security_headers()
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        last_digest = ""
        heartbeat = 0
        try:
            while True:
                snapshot = self.server.application.snapshot()
                digest = str(snapshot.get("snapshot_digest"))
                if digest != last_digest:
                    data = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
                    self.wfile.write(f"event: snapshot\ndata: {data}\n\n".encode())
                    self.wfile.flush()
                    last_digest = digest
                    heartbeat = 0
                else:
                    heartbeat += 1
                    if heartbeat >= max(1, int(15 / self.server.application.settings.poll_seconds)):
                        self.wfile.write(b": heartbeat\n\n")
                        self.wfile.flush()
                        heartbeat = 0
                time.sleep(self.server.application.settings.poll_seconds)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            return


def create_console_server(paths: WorkspacePaths, settings: ConsoleSettings | None = None) -> WorkspaceConsoleServer:
    selected = settings or ConsoleSettings()
    selected.validate()
    application = WorkspaceConsoleApplication(paths, selected)
    return WorkspaceConsoleServer((selected.host, selected.port), application)


def serve_console(paths: WorkspacePaths, settings: ConsoleSettings | None = None) -> None:
    server = create_console_server(paths, settings)
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()
