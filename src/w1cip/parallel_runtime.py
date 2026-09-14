"""Parallel multi-agent execution and governed merge coordination for W1 Nexus.

This module gives every agent an isolated Git worktree, schedules dependency-aware
jobs concurrently, serialises declared shared-resource access, persists progress in
SQLite, and creates a reviewed integration commit before the target branch can move.

It is a local reference runtime.  Git remains the source of code history; the SQLite
journal is the operational audit and resume layer.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import threading
import time
import uuid
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence

from .action_runtime import ActionPolicy, ActionRequest, ActionRuntime, canonical_json
from .secure_execution import SecureExecutionFabric, SandboxRequest

ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
TERMINAL_JOB_STATES = {"completed", "failed", "cancelled", "timed_out", "blocked"}
TERMINAL_RUN_STATES = {"completed", "failed", "cancelled", "merge_ready", "conflicted"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class ParallelRuntimeError(RuntimeError):
    code = "parallel_runtime_error"


class ParallelPlanError(ParallelRuntimeError):
    code = "parallel_plan_invalid"


class ParallelRunNotFound(ParallelRuntimeError):
    code = "parallel_run_not_found"


class MergeConflictError(ParallelRuntimeError):
    code = "merge_conflict"


class MergeReviewRequired(ParallelRuntimeError):
    code = "merge_review_required"


class MergeReviewInvalid(ParallelRuntimeError):
    code = "merge_review_invalid"


@dataclass(frozen=True)
class AgentJob:
    job_id: str
    agent_id: str
    argv: tuple[str, ...]
    branch: str
    base_ref: str = "HEAD"
    dependencies: tuple[str, ...] = ()
    resource_paths: tuple[str, ...] = ()
    timeout_seconds: int = 600
    expected_outputs: tuple[str, ...] = ()
    commit_message: str | None = None
    environment: Mapping[str, str] = field(default_factory=dict)
    sandbox_profile: str | None = None
    sandbox_artifact_globs: tuple[str, ...] = ()
    secret_env: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name, value in (("job_id", self.job_id), ("agent_id", self.agent_id)):
            if not ID_RE.fullmatch(value):
                raise ValueError(f"{name} must be a canonical lowercase identifier")
        if not self.argv:
            raise ValueError("argv cannot be empty")
        if self.timeout_seconds < 1:
            raise ValueError("timeout_seconds must be positive")
        if len(set(self.dependencies)) != len(self.dependencies):
            raise ValueError("dependencies must be unique")
        if self.job_id in self.dependencies:
            raise ValueError("job cannot depend on itself")


@dataclass(frozen=True)
class ParallelRunPolicy:
    max_workers: int = 4
    fail_fast: bool = False
    lock_wait_seconds: int = 300
    require_clean_repository: bool = True
    keep_worktrees: bool = True
    auto_commit: bool = True
    command_output_limit: int = 1_000_000

    def __post_init__(self) -> None:
        if self.max_workers < 1:
            raise ValueError("max_workers must be positive")
        if self.lock_wait_seconds < 1:
            raise ValueError("lock_wait_seconds must be positive")


@dataclass(frozen=True)
class AgentJobResult:
    run_id: str
    job_id: str
    agent_id: str
    status: str
    branch: str
    worktree_path: str
    started_at: str
    completed_at: str
    exit_code: int | None
    stdout: str
    stderr: str
    commit_sha: str | None
    changed_files: tuple[str, ...]
    error_code: str | None = None


@dataclass(frozen=True)
class ParallelEvent:
    event_id: str
    run_id: str
    event_type: str
    occurred_at: str
    job_id: str | None = None
    agent_id: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MergeProposal:
    proposal_id: str
    run_id: str
    target_branch: str
    base_commit: str
    source_branches: tuple[str, ...]
    source_commits: tuple[str, ...]
    potential_conflicts: tuple[str, ...]
    status: str
    integration_branch: str | None = None
    integration_commit: str | None = None
    integration_worktree: str | None = None
    test_argv: tuple[str, ...] = ()
    test_exit_code: int | None = None
    test_stdout: str = ""
    test_stderr: str = ""
    created_at: str = field(default_factory=utc_now)


@dataclass(frozen=True)
class MergeReview:
    review_id: str
    proposal_id: str
    reviewer_id: str
    outcome: str
    rationale: str
    reviewed_at: str
    source_snapshot: str


class AgentExecutor(Protocol):
    def execute(self, job: AgentJob, worktree: Path, cancel_event: threading.Event) -> tuple[int, str, str]: ...


class CommandAgentExecutor:
    """Direct argv executor with timeout, output caps, and secret stripping."""

    SECRET_PARTS = ("token", "secret", "password", "passwd", "api_key", "apikey", "private_key")

    def __init__(self, *, output_limit: int = 1_000_000) -> None:
        self.output_limit = output_limit

    def execute(self, job: AgentJob, worktree: Path, cancel_event: threading.Event) -> tuple[int, str, str]:
        env = {k: v for k, v in os.environ.items() if not any(p in k.lower() for p in self.SECRET_PARTS)}
        env.update({str(k): str(v) for k, v in job.environment.items()})
        process = subprocess.Popen(
            list(job.argv), cwd=str(worktree), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, shell=False,
            start_new_session=(os.name != "nt"),
        )
        deadline = time.monotonic() + job.timeout_seconds
        while process.poll() is None:
            if cancel_event.is_set() or time.monotonic() >= deadline:
                if os.name != "nt":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
                break
            time.sleep(0.05)
        stdout_b, stderr_b = process.communicate()
        stdout = stdout_b[: self.output_limit].decode("utf-8", errors="replace")
        stderr = stderr_b[: self.output_limit].decode("utf-8", errors="replace")
        if cancel_event.is_set():
            return 130, stdout, stderr
        if time.monotonic() >= deadline and process.returncode not in (0, None):
            return 124, stdout, stderr
        return int(process.returncode or 0), stdout, stderr


class SecureFabricAgentExecutor:
    """Execute jobs through SecureExecutionFabric when a profile is declared.

    Jobs without ``sandbox_profile`` preserve the established direct executor
    path for compatibility.  A hard-isolation profile can never silently fall
    back to direct execution because backend selection is enforced by the
    fabric itself.
    """

    def __init__(self, state_dir: str | Path, *, output_limit: int = 1_000_000) -> None:
        self.state_dir = Path(state_dir)
        self.direct = CommandAgentExecutor(output_limit=output_limit)

    def execute(self, job: AgentJob, worktree: Path, cancel_event: threading.Event) -> tuple[int, str, str]:
        if not job.sandbox_profile:
            return self.direct.execute(job, worktree, cancel_event)
        secret_values: dict[str, str] = {}
        for secret_name, environment_name in job.secret_env.items():
            if environment_name not in os.environ:
                return 78, "", f"missing secret environment reference: {environment_name}"
            secret_values[str(secret_name)] = os.environ[environment_name]
        digest = hashlib.sha256(str(worktree).encode()).hexdigest()[:12]
        request = SandboxRequest(
            execution_id=f"agent-{job.job_id}-{digest}",
            profile_id=job.sandbox_profile,
            argv=job.argv,
            cwd=".",
            environment=job.environment,
            secret_values=secret_values,
            artifact_globs=job.sandbox_artifact_globs,
            requested_by=job.agent_id,
            reason=f"parallel agent job {job.job_id}",
        )
        fabric = SecureExecutionFabric(worktree, state_dir=self.state_dir)
        try:
            result = fabric.execute(request, cancel_event=cancel_event)
        finally:
            fabric.close()
        if result.status == "timed_out":
            return 124, result.stdout, result.stderr
        if result.status == "cancelled":
            return 130, result.stdout, result.stderr
        return int(result.exit_code or 0), result.stdout, result.stderr


class ParallelJournal:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=FULL")
        self.lock = threading.RLock()
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS parallel_runs(
              run_id TEXT PRIMARY KEY, status TEXT NOT NULL, policy_json TEXT NOT NULL,
              base_commit TEXT NOT NULL, target_branch TEXT NOT NULL,
              created_at TEXT NOT NULL, completed_at TEXT, cancelled_at TEXT
            );
            CREATE TABLE IF NOT EXISTS agent_jobs(
              run_id TEXT NOT NULL, job_id TEXT NOT NULL, agent_id TEXT NOT NULL,
              spec_json TEXT NOT NULL, status TEXT NOT NULL, result_json TEXT,
              worktree_path TEXT, started_at TEXT, completed_at TEXT,
              PRIMARY KEY(run_id, job_id)
            );
            CREATE TABLE IF NOT EXISTS parallel_events(
              sequence INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE NOT NULL,
              run_id TEXT NOT NULL, event_json TEXT NOT NULL, occurred_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS resource_locks(
              run_id TEXT NOT NULL, resource_path TEXT NOT NULL, job_id TEXT NOT NULL,
              acquired_at TEXT NOT NULL, PRIMARY KEY(run_id, resource_path)
            );
            CREATE TABLE IF NOT EXISTS merge_proposals(
              proposal_id TEXT PRIMARY KEY, run_id TEXT NOT NULL,
              proposal_json TEXT NOT NULL, review_json TEXT, applied_at TEXT,
              applied_by TEXT
            );
            """
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def create_run(self, run_id: str, jobs: Sequence[AgentJob], policy: ParallelRunPolicy, base_commit: str, target_branch: str) -> None:
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT INTO parallel_runs(run_id,status,policy_json,base_commit,target_branch,created_at) VALUES (?,?,?,?,?,?)",
                (run_id, "pending", canonical_json(asdict(policy)), base_commit, target_branch, utc_now()),
            )
            for job in jobs:
                self.conn.execute(
                    "INSERT INTO agent_jobs(run_id,job_id,agent_id,spec_json,status) VALUES (?,?,?,?,?)",
                    (run_id, job.job_id, job.agent_id, canonical_json(asdict(job)), "pending"),
                )

    def emit(self, event: ParallelEvent) -> None:
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT INTO parallel_events(event_id,run_id,event_json,occurred_at) VALUES (?,?,?,?)",
                (event.event_id, event.run_id, canonical_json(asdict(event)), event.occurred_at),
            )

    def update_run(self, run_id: str, status: str) -> None:
        completed = utc_now() if status in TERMINAL_RUN_STATES else None
        with self.lock, self.conn:
            self.conn.execute("UPDATE parallel_runs SET status=?, completed_at=COALESCE(?,completed_at) WHERE run_id=?", (status, completed, run_id))

    def update_job(self, run_id: str, job_id: str, status: str, *, worktree_path: str | None = None, result: AgentJobResult | None = None) -> None:
        now = utc_now()
        with self.lock, self.conn:
            self.conn.execute(
                "UPDATE agent_jobs SET status=?, worktree_path=COALESCE(?,worktree_path), result_json=COALESCE(?,result_json), "
                "started_at=CASE WHEN ?='running' THEN COALESCE(started_at,?) ELSE started_at END, "
                "completed_at=CASE WHEN ? IN ('completed','failed','cancelled','timed_out','blocked') THEN ? ELSE completed_at END "
                "WHERE run_id=? AND job_id=?",
                (status, worktree_path, canonical_json(asdict(result)) if result else None, status, now, status, now, run_id, job_id),
            )

    def acquire_resources(self, run_id: str, job_id: str, resources: Sequence[str], timeout: int, cancel: threading.Event) -> bool:
        normalized = tuple(sorted({_normalise_resource(p) for p in resources}))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not cancel.is_set():
            with self.lock, self.conn:
                rows = self.conn.execute("SELECT resource_path,job_id FROM resource_locks WHERE run_id=?", (run_id,)).fetchall()
                conflicts = [r for r in rows if r["job_id"] != job_id and any(_paths_overlap(r["resource_path"], p) for p in normalized)]
                if not conflicts:
                    for path in normalized:
                        self.conn.execute("INSERT OR REPLACE INTO resource_locks(run_id,resource_path,job_id,acquired_at) VALUES (?,?,?,?)", (run_id, path, job_id, utc_now()))
                    return True
            time.sleep(0.05)
        return False

    def release_resources(self, run_id: str, job_id: str) -> None:
        with self.lock, self.conn:
            self.conn.execute("DELETE FROM resource_locks WHERE run_id=? AND job_id=?", (run_id, job_id))

    def save_proposal(self, proposal: MergeProposal) -> None:
        with self.lock, self.conn:
            self.conn.execute("INSERT OR REPLACE INTO merge_proposals(proposal_id,run_id,proposal_json) VALUES (?,?,?)", (proposal.proposal_id, proposal.run_id, canonical_json(asdict(proposal))))

    def save_review(self, review: MergeReview) -> None:
        with self.lock, self.conn:
            self.conn.execute("UPDATE merge_proposals SET review_json=? WHERE proposal_id=?", (canonical_json(asdict(review)), review.proposal_id))

    def get_proposal(self, proposal_id: str) -> tuple[MergeProposal, MergeReview | None]:
        row = self.conn.execute("SELECT proposal_json,review_json FROM merge_proposals WHERE proposal_id=?", (proposal_id,)).fetchone()
        if row is None:
            raise ParallelRunNotFound(proposal_id)
        p = json.loads(row["proposal_json"])
        for key in ("source_branches", "source_commits", "potential_conflicts", "test_argv"):
            p[key] = tuple(p.get(key, ()))
        proposal = MergeProposal(**p)
        review = MergeReview(**json.loads(row["review_json"])) if row["review_json"] else None
        return proposal, review

    def mark_applied(self, proposal_id: str, applied_by: str) -> None:
        with self.lock, self.conn:
            self.conn.execute("UPDATE merge_proposals SET applied_at=?, applied_by=? WHERE proposal_id=?", (utc_now(), applied_by, proposal_id))

    def status(self, run_id: str) -> dict[str, Any]:
        run = self.conn.execute("SELECT * FROM parallel_runs WHERE run_id=?", (run_id,)).fetchone()
        if run is None:
            raise ParallelRunNotFound(run_id)
        jobs = self.conn.execute("SELECT job_id,agent_id,status,worktree_path,result_json FROM agent_jobs WHERE run_id=? ORDER BY job_id", (run_id,)).fetchall()
        events = self.conn.execute("SELECT event_json FROM parallel_events WHERE run_id=? ORDER BY sequence", (run_id,)).fetchall()
        return {
            "run": dict(run),
            "jobs": [{**{k: row[k] for k in ("job_id","agent_id","status","worktree_path")}, "result": json.loads(row["result_json"]) if row["result_json"] else None} for row in jobs],
            "events": [json.loads(row["event_json"]) for row in events],
        }


class ParallelAgentRuntime:
    def __init__(self, workspace_root: str | Path, *, policy: ParallelRunPolicy | None = None, journal_path: str | Path | None = None, executor: AgentExecutor | None = None) -> None:
        self.root = Path(workspace_root).resolve()
        self.policy = policy or ParallelRunPolicy()
        self.state_dir = self.root / ".w1nexus"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.journal = ParallelJournal(journal_path or self.state_dir / "parallel-runtime.sqlite3")
        self.executor = executor or SecureFabricAgentExecutor(self.state_dir, output_limit=self.policy.command_output_limit)
        self.action_runtime = ActionRuntime(self.root)
        self._listeners: list[Callable[[ParallelEvent], None]] = []
        self._cancel: dict[str, threading.Event] = {}
        self._git_lock = threading.RLock()

    def close(self) -> None:
        self.action_runtime.close()
        self.journal.close()

    def subscribe(self, listener: Callable[[ParallelEvent], None]) -> None:
        self._listeners.append(listener)

    def _emit(self, run_id: str, event_type: str, *, job: AgentJob | None = None, **details: Any) -> None:
        event = ParallelEvent(event_id=f"pe-{uuid.uuid4().hex}", run_id=run_id, event_type=event_type, occurred_at=utc_now(), job_id=job.job_id if job else None, agent_id=job.agent_id if job else None, details=details)
        self.journal.emit(event)
        for listener in tuple(self._listeners):
            listener(event)

    def run(self, jobs: Sequence[AgentJob], *, run_id: str, approved_by: str, target_branch: str | None = None) -> dict[str, Any]:
        if not ID_RE.fullmatch(run_id):
            raise ParallelPlanError("run_id must be canonical")
        self._validate_jobs(jobs)
        self._ensure_repo_clean()
        target = target_branch or self._git(["branch", "--show-current"]).stdout.strip()
        base_commit = self._git(["rev-parse", target]).stdout.strip()
        self.journal.create_run(run_id, jobs, self.policy, base_commit, target)
        cancel = threading.Event(); self._cancel[run_id] = cancel
        self.journal.update_run(run_id, "running")
        self._emit(run_id, "parallel_run_started", job_count=len(jobs), base_commit=base_commit, target_branch=target)
        job_map = {j.job_id: j for j in jobs}
        states = {j.job_id: "pending" for j in jobs}
        futures: dict[Future[AgentJobResult], AgentJob] = {}
        with ThreadPoolExecutor(max_workers=self.policy.max_workers, thread_name_prefix="w1-agent") as pool:
            while True:
                progressed = False
                for job in jobs:
                    if states[job.job_id] != "pending":
                        continue
                    dep_states = [states[d] for d in job.dependencies]
                    if any(s in {"failed","cancelled","timed_out","blocked"} for s in dep_states):
                        states[job.job_id] = "blocked"; self.journal.update_job(run_id, job.job_id, "blocked")
                        self._emit(run_id, "agent_job_blocked", job=job, reason="dependency_failed"); progressed = True; continue
                    if all(s == "completed" for s in dep_states):
                        states[job.job_id] = "queued"
                        future = pool.submit(self._execute_job, run_id, job, approved_by, cancel, base_commit)
                        futures[future] = job; progressed = True
                if not futures:
                    if all(s in TERMINAL_JOB_STATES for s in states.values()): break
                    if not progressed: raise ParallelPlanError("scheduler deadlock")
                if futures:
                    done, _ = wait(tuple(futures), timeout=0.1, return_when=FIRST_COMPLETED)
                    for future in done:
                        job = futures.pop(future)
                        try: result = future.result()
                        except Exception as exc:
                            result = AgentJobResult(run_id,job.job_id,job.agent_id,"failed",job.branch,str(self.state_dir / "worktrees" / job.agent_id),utc_now(),utc_now(),None,"",str(exc),None,(),getattr(exc,"code",type(exc).__name__))
                        states[job.job_id] = result.status
                        if self.policy.fail_fast and result.status != "completed": cancel.set()
                if cancel.is_set() and not futures:
                    for job in jobs:
                        if states[job.job_id] in {"pending","queued"}:
                            states[job.job_id] = "cancelled"; self.journal.update_job(run_id,job.job_id,"cancelled")
                    break
        failed = [k for k,v in states.items() if v != "completed"]
        hard_failures = [k for k,v in states.items() if v in {"failed", "timed_out", "blocked"}]
        status = "failed" if hard_failures else ("cancelled" if failed else "completed")
        self.journal.update_run(run_id, status)
        self._emit(run_id, "parallel_run_finished", status=status, failed_jobs=failed)
        return self.journal.status(run_id)

    def cancel(self, run_id: str) -> None:
        event = self._cancel.get(run_id)
        if event is None: raise ParallelRunNotFound(run_id)
        event.set(); self._emit(run_id, "parallel_run_cancel_requested")

    def status(self, run_id: str) -> dict[str, Any]: return self.journal.status(run_id)

    def _execute_job(self, run_id: str, job: AgentJob, approved_by: str, cancel: threading.Event, base_commit: str) -> AgentJobResult:
        started = utc_now(); self.journal.update_job(run_id,job.job_id,"running"); self._emit(run_id,"agent_job_started",job=job)
        acquired = self.journal.acquire_resources(run_id,job.job_id,job.resource_paths,self.policy.lock_wait_seconds,cancel)
        if not acquired:
            status = "cancelled" if cancel.is_set() else "blocked"
            result = AgentJobResult(run_id,job.job_id,job.agent_id,status,job.branch,"",started,utc_now(),None,"","resource lock unavailable",None,(),"resource_lock_unavailable")
            self.journal.update_job(run_id,job.job_id,status,result=result); return result
        worktree = self.state_dir / "worktrees" / f"{run_id}-{job.agent_id}"
        try:
            with self._git_lock:
                self._create_worktree(run_id, job, worktree, approved_by, base_commit)
            self.journal.update_job(run_id,job.job_id,"running",worktree_path=str(worktree))
            code, stdout, stderr = self.executor.execute(job, worktree, cancel)
            if cancel.is_set(): status,error = "cancelled","cancelled"
            elif code == 124: status,error = "timed_out","agent_job_timed_out"
            elif code != 0: status,error = "failed","agent_command_failed"
            else: status,error = "completed",None
            commit = None; changed: tuple[str,...] = ()
            if status == "completed":
                missing = [p for p in job.expected_outputs if not (worktree / p).exists()]
                if missing:
                    status,error,stderr = "failed","expected_output_missing",stderr + "\nMissing: " + ", ".join(missing)
                elif self.policy.auto_commit:
                    commit, changed = self._commit_agent_work(job, worktree)
            result = AgentJobResult(run_id,job.job_id,job.agent_id,status,job.branch,str(worktree),started,utc_now(),code,stdout,stderr,commit,changed,error)
            self.journal.update_job(run_id,job.job_id,status,worktree_path=str(worktree),result=result)
            self._emit(run_id,"agent_job_finished",job=job,status=status,commit_sha=commit,changed_files=list(changed),error_code=error)
            return result
        finally:
            self.journal.release_resources(run_id,job.job_id)

    def _create_worktree(self, run_id: str, job: AgentJob, worktree: Path, approved_by: str, base_commit: str) -> None:
        if worktree.exists(): raise ParallelPlanError(f"worktree already exists: {worktree}")
        branch = job.branch
        ref = base_commit if job.base_ref == "HEAD" else job.base_ref
        request = ActionRequest(action_id=f"wt-{run_id}-{job.job_id}",kind="git.worktree.create",parameters={"agent_id":f"{run_id}-{job.agent_id}","branch":branch,"ref":ref},requested_by=approved_by)
        approval = self.action_runtime.issue_approval(self.action_runtime.plan(request),issued_by=approved_by)
        result = self.action_runtime.execute(request,approval=approval)
        actual = Path(str(result.metadata["path"]))
        if actual != worktree:
            worktree.parent.mkdir(parents=True,exist_ok=True)
            # ActionRuntime uses the same canonical path; this is defensive.
            worktree = actual

    def _commit_agent_work(self, job: AgentJob, worktree: Path) -> tuple[str, tuple[str,...]]:
        status = self._git(["-C",str(worktree),"status","--porcelain"]).stdout
        if not status.strip():
            commit = self._git(["-C",str(worktree),"rev-parse","HEAD"]).stdout.strip()
            return commit, ()
        self._git(["-C",str(worktree),"add","-A"])
        env = dict(os.environ); env.update({"GIT_AUTHOR_NAME":f"W1 Agent {job.agent_id}","GIT_AUTHOR_EMAIL":f"{job.agent_id}@w1.local","GIT_COMMITTER_NAME":"W1 Merge Runtime","GIT_COMMITTER_EMAIL":"runtime@w1.local"})
        message = job.commit_message or f"w1({job.agent_id}): complete {job.job_id}"
        subprocess.run(["git","-C",str(worktree),"commit","-m",message],check=True,capture_output=True,text=True,env=env)
        commit = self._git(["-C",str(worktree),"rev-parse","HEAD"]).stdout.strip()
        base = self._git(["rev-parse",job.base_ref]).stdout.strip() if job.base_ref != "HEAD" else self._git(["merge-base",commit,"HEAD"]).stdout.strip()
        changed = tuple(x for x in self._git(["-C",str(worktree),"diff","--name-only",f"{base}..{commit}"]).stdout.splitlines() if x)
        return commit, changed

    def _validate_jobs(self, jobs: Sequence[AgentJob]) -> None:
        if not jobs: raise ParallelPlanError("at least one job required")
        ids = [j.job_id for j in jobs]; agents=[j.agent_id for j in jobs]; branches=[j.branch for j in jobs]
        if len(ids)!=len(set(ids)): raise ParallelPlanError("duplicate job_id")
        if len(agents)!=len(set(agents)): raise ParallelPlanError("one active job per agent is required")
        if len(branches)!=len(set(branches)): raise ParallelPlanError("branches must be unique")
        known=set(ids)
        for j in jobs:
            unknown=set(j.dependencies)-known
            if unknown: raise ParallelPlanError(f"unknown dependencies for {j.job_id}: {sorted(unknown)}")
        visiting:set[str]=set(); visited:set[str]=set(); graph={j.job_id:j.dependencies for j in jobs}
        def dfs(node:str)->None:
            if node in visiting: raise ParallelPlanError("dependency cycle")
            if node in visited:return
            visiting.add(node)
            for dep in graph[node]: dfs(dep)
            visiting.remove(node);visited.add(node)
        for node in graph: dfs(node)

    def _ensure_repo_clean(self) -> None:
        top = Path(self._git(["rev-parse","--show-toplevel"]).stdout.strip()).resolve()
        if top != self.root: raise ParallelPlanError("workspace must be Git repository root")
        if self.policy.require_clean_repository:
            dirty = []
            for line in self._git(["status", "--porcelain", "--untracked-files=all"]).stdout.splitlines():
                path = line[3:] if len(line) > 3 else ""
                if path == ".w1nexus" or path.startswith(".w1nexus/"):
                    continue
                dirty.append(line)
            if dirty:
                raise ParallelPlanError("repository must be clean before a parallel run: " + "; ".join(dirty[:5]))

    def _git(self, args: Sequence[str], *, check: bool=True) -> subprocess.CompletedProcess[str]:
        result=subprocess.run(["git",*args],cwd=str(self.root),text=True,capture_output=True,check=False)
        if check and result.returncode!=0: raise ParallelPlanError(result.stderr.strip() or "git command failed")
        return result


class MergeCoordinator:
    def __init__(self, runtime: ParallelAgentRuntime) -> None:
        self.runtime=runtime; self.root=runtime.root; self.journal=runtime.journal

    def propose(self, run_id: str, *, test_argv: Sequence[str]=()) -> MergeProposal:
        status=self.journal.status(run_id); run=status["run"]
        completed=[j for j in status["jobs"] if j["status"]=="completed" and j["result"] and j["result"].get("commit_sha")]
        if len(completed)!=len(status["jobs"]): raise ParallelPlanError("all jobs must complete before merge proposal")
        branches=tuple(j["result"]["branch"] for j in completed); commits=tuple(j["result"]["commit_sha"] for j in completed)
        owners:dict[str,list[str]]={}
        for j in completed:
            for path in j["result"].get("changed_files",[]): owners.setdefault(path,[]).append(j["job_id"])
        potential=tuple(sorted(path for path,js in owners.items() if len(js)>1))
        proposal=MergeProposal(proposal_id=f"merge-{run_id}-{uuid.uuid4().hex[:8]}",run_id=run_id,target_branch=run["target_branch"],base_commit=run["base_commit"],source_branches=branches,source_commits=commits,potential_conflicts=potential,status="proposed",test_argv=tuple(test_argv))
        proposal=self._build_integration(proposal)
        self.journal.save_proposal(proposal); self.runtime._emit(run_id,"merge_proposal_created",proposal_id=proposal.proposal_id,status=proposal.status,potential_conflicts=list(potential))
        return proposal

    def _build_integration(self, proposal: MergeProposal) -> MergeProposal:
        branch=f"w1/integration/{proposal.run_id}-{uuid.uuid4().hex[:6]}"; agent=f"integration-{proposal.run_id}"
        req=ActionRequest(action_id=f"wt-integration-{uuid.uuid4().hex[:8]}",kind="git.worktree.create",parameters={"agent_id":agent,"branch":branch,"ref":proposal.base_commit},requested_by="merge-coordinator")
        approval=self.runtime.action_runtime.issue_approval(self.runtime.action_runtime.plan(req),issued_by="merge-coordinator")
        result=self.runtime.action_runtime.execute(req,approval=approval); path=Path(str(result.metadata["path"]))
        try:
            for source in proposal.source_branches:
                merge=subprocess.run(["git","-C",str(path),"merge","--no-ff","--no-edit",source],text=True,capture_output=True)
                if merge.returncode!=0:
                    subprocess.run(["git","-C",str(path),"merge","--abort"],capture_output=True)
                    return MergeProposal(**{**asdict(proposal),"status":"conflicted","integration_branch":branch,"integration_worktree":str(path),"test_stderr":merge.stderr})
            integration_commit=subprocess.run(["git","-C",str(path),"rev-parse","HEAD"],check=True,text=True,capture_output=True).stdout.strip()
            code=0; out=""; err=""
            if proposal.test_argv:
                test=subprocess.run(list(proposal.test_argv),cwd=str(path),text=True,capture_output=True,timeout=600)
                code=test.returncode; out=test.stdout; err=test.stderr
            status="tests_passed" if code==0 else "tests_failed"
            return MergeProposal(**{**asdict(proposal),"status":status,"integration_branch":branch,"integration_commit":integration_commit,"integration_worktree":str(path),"test_exit_code":code,"test_stdout":out,"test_stderr":err})
        except Exception:
            raise

    def review(self, proposal_id: str, *, reviewer_id: str, outcome: str, rationale: str) -> MergeReview:
        proposal,_=self.journal.get_proposal(proposal_id)
        if reviewer_id in {j["agent_id"] for j in self.journal.status(proposal.run_id)["jobs"]}: raise MergeReviewInvalid("reviewer must be independent from producing agents")
        if outcome not in {"approved","revision_required","rejected"}: raise MergeReviewInvalid("invalid outcome")
        if outcome=="approved" and proposal.status!="tests_passed": raise MergeReviewInvalid("only a conflict-free, tested proposal may be approved")
        snapshot=hashlib.sha256(canonical_json(asdict(proposal)).encode()).hexdigest()
        review=MergeReview(f"review-{uuid.uuid4().hex}",proposal_id,reviewer_id,outcome,rationale,utc_now(),snapshot)
        self.journal.save_review(review); self.runtime._emit(proposal.run_id,"merge_proposal_reviewed",proposal_id=proposal_id,outcome=outcome,reviewer_id=reviewer_id)
        return review

    def apply(self, proposal_id: str, *, approved_by: str) -> str:
        proposal,review=self.journal.get_proposal(proposal_id)
        if review is None or review.outcome!="approved": raise MergeReviewRequired(proposal_id)
        current=hashlib.sha256(canonical_json(asdict(proposal)).encode()).hexdigest()
        if current!=review.source_snapshot: raise MergeReviewInvalid("proposal changed after review")
        head=subprocess.run(["git","rev-parse",proposal.target_branch],cwd=str(self.root),check=True,text=True,capture_output=True).stdout.strip()
        if head!=proposal.base_commit: raise MergeReviewInvalid("target branch advanced after proposal creation")
        req=ActionRequest(action_id=f"merge-apply-{uuid.uuid4().hex[:12]}",kind="command.run",parameters={"argv":["git","merge","--ff-only",proposal.integration_commit],"cwd":"."},requested_by=approved_by,reason=f"Apply reviewed {proposal_id}")
        approval=self.runtime.action_runtime.issue_approval(self.runtime.action_runtime.plan(req),issued_by=approved_by)
        result=self.runtime.action_runtime.execute(req,approval=approval,raise_on_command_failure=True)
        commit=subprocess.run(["git","rev-parse","HEAD"],cwd=str(self.root),check=True,text=True,capture_output=True).stdout.strip()
        self.journal.mark_applied(proposal_id,approved_by); self.runtime._emit(proposal.run_id,"merge_proposal_applied",proposal_id=proposal_id,commit_sha=commit,approved_by=approved_by)
        return commit


def _normalise_resource(value: str) -> str:
    text=str(PurePosixPath(value.replace("\\","/")))
    if text.startswith("../") or text==".." or text.startswith("/"): raise ParallelPlanError(f"invalid resource path: {value}")
    return text.strip("./") or "."


def _paths_overlap(a: str,b: str)->bool:
    if a=="." or b==".": return True
    return a==b or a.startswith(b.rstrip("/")+"/") or b.startswith(a.rstrip("/")+"/")


__all__=["AgentJob","AgentJobResult","CommandAgentExecutor","MergeCoordinator","MergeProposal","MergeReview","ParallelAgentRuntime","ParallelEvent","ParallelPlanError","ParallelRunPolicy","ParallelRuntimeError"]
