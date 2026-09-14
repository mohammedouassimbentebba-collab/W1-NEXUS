"""Reference orchestration core for W1-CIP v0.1.

The core deliberately separates orchestration mechanics from domain-specific
ProtocolEnvelope construction.  It compiles an accepted GoalContract into a
DAG of work intents, routes each intent through an ExecutionResourcePlan,
minimises context, invokes provider adapters with idempotency keys, persists
checkpoints, and appends envelopes produced by a pluggable materializer to the
append-only SessionStore.

Live provider connectors are intentionally outside this module.  The included
``ScriptedProvider`` and ``ArtifactEnvelopeFactory`` support deterministic tests
and the reference demo.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence

from .session_store import SessionStore, canonical_json
from .action_runtime import ActionApprovalRequired, ActionRequest, ActionRuntime
from .validation import ResourceRoutingResult, route_execution_resource

CANONICAL_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
TERMINAL_RUN_STATUSES = {
    "completed",
    "cancelled",
    "failed",
    "awaiting_reconciliation",
}
RESUMABLE_RUN_STATUSES = {"blocked", "awaiting_reset", "awaiting_user"}
VERIFIABLE_KINDS = {"deterministic", "empirical", "source_based", "mixed"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _slug(value: str) -> str:
    lowered = value.lower().replace("_", "-").replace(".", "-")
    lowered = re.sub(r"[^a-z0-9-]+", "-", lowered)
    lowered = re.sub(r"-+", "-", lowered).strip("-")
    return lowered or "event"


def _entity_ref_key(ref: Mapping[str, Any]) -> tuple[str, str, int] | None:
    entity_type = ref.get("entity_type")
    entity_id = ref.get("entity_id")
    entity_version = ref.get("entity_version")
    if not isinstance(entity_type, str) or not isinstance(entity_id, str):
        return None
    if not isinstance(entity_version, int):
        return None
    return entity_type, entity_id, entity_version


class OrchestratorError(RuntimeError):
    """Base class for stable orchestration failures."""

    code = "orchestrator_error"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.code)


class WorkflowCompilationError(OrchestratorError):
    code = "orchestrator_workflow_invalid"


class ProviderError(OrchestratorError):
    code = "provider_error"


class ProviderQuotaExhausted(ProviderError):
    code = "provider_quota_exhausted"

    def __init__(self, reset_at: str | None = None) -> None:
        self.reset_at = reset_at
        super().__init__(self.code)


class ProviderTransientError(ProviderError):
    code = "provider_transient_error"


class ProviderPermanentError(ProviderError):
    code = "provider_permanent_error"


class ProviderOutcomeUncertain(ProviderError):
    """The caller cannot know whether the provider performed the request."""

    code = "provider_outcome_uncertain"


class UnsafeResumeError(OrchestratorError):
    code = "orchestrator_unsafe_provider_resume"


class OutputContractError(OrchestratorError):
    code = "orchestrator_provider_output_invalid"


class RunConflictError(OrchestratorError):
    code = "orchestrator_run_conflict"


@dataclass(frozen=True)
class CompiledTask:
    task_id: str
    title: str
    phase: str
    role: str
    expected_output_type: str
    depends_on: tuple[str, ...] = ()
    deliverable_id: str | None = None
    criterion_id: str | None = None
    required_context_fields: tuple[str, ...] = ()
    estimated_units: int = 1
    domains: tuple[str, ...] = ()


@dataclass(frozen=True)
class CompiledWorkflow:
    tasks: tuple[CompiledTask, ...]
    risk_level: str
    error_cost: str
    task_complexity: str

    def task_by_id(self) -> dict[str, CompiledTask]:
        return {task.task_id: task for task in self.tasks}


@dataclass(frozen=True)
class TeamFormationDraft:
    required_roles: tuple[str, ...]
    selected_assignments: Mapping[str, Mapping[str, Any]]
    missing_roles: tuple[str, ...]
    risk_level: str
    error_cost: str


@dataclass(frozen=True)
class ProviderRequest:
    run_id: str
    session_id: str
    task: CompiledTask
    resource_id: str
    idempotency_key: str
    context: Mapping[str, Any]
    prior_outputs: Mapping[str, Any]
    attempt: int


@dataclass(frozen=True)
class ProviderResponse:
    output_type: str
    payload: Mapping[str, Any]
    units_consumed: int = 1
    context_fields_used: tuple[str, ...] = ()
    protocol_envelopes: tuple[Mapping[str, Any], ...] = ()
    action_requests: tuple[Mapping[str, Any], ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)


class ProviderAdapter(Protocol):
    resource_id: str
    supports_idempotency: bool

    def invoke(self, request: ProviderRequest) -> ProviderResponse:
        ...


class ScriptedProvider:
    """Deterministic provider used by tests and the reference demonstration."""

    def __init__(
        self,
        resource_id: str,
        outcomes: Sequence[ProviderResponse | BaseException],
        *,
        supports_idempotency: bool = True,
    ) -> None:
        self.resource_id = resource_id
        self.supports_idempotency = supports_idempotency
        self._outcomes = list(outcomes)
        self._cache: dict[str, ProviderResponse] = {}
        self.calls: list[ProviderRequest] = []

    def invoke(self, request: ProviderRequest) -> ProviderResponse:
        self.calls.append(request)
        cached = self._cache.get(request.idempotency_key)
        if cached is not None:
            return deepcopy(cached)
        if not self._outcomes:
            raise ProviderPermanentError("scripted_provider_outcomes_exhausted")
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        response = deepcopy(outcome)
        if self.supports_idempotency:
            self._cache[request.idempotency_key] = deepcopy(response)
        return response


@dataclass(frozen=True)
class RuntimeEvent:
    event_id: str
    event_type: str
    run_id: str
    session_id: str
    occurred_at: str
    task_id: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)


class EnvelopeFactory(Protocol):
    def build(self, event: RuntimeEvent) -> Iterable[Mapping[str, Any]]:
        ...


class NullEnvelopeFactory:
    def build(self, event: RuntimeEvent) -> Iterable[Mapping[str, Any]]:
        return ()


class ArtifactEnvelopeFactory:
    """Materialize runtime trace events as generic immutable W1-CIP artifacts.

    This is a reference/demo materializer, not a replacement for normative
    Task, ContextGrant, Contribution, Verification, Review, Decision, or
    FinalResult entities.  Production domain packs should implement
    ``EnvelopeFactory`` and return those concrete envelopes.
    """

    def __init__(
        self,
        *,
        actor: Mapping[str, Any],
        authorized_by: Mapping[str, Any],
        recorded_by: Mapping[str, Any],
        correlation_id: str,
        classification: str = "internal",
    ) -> None:
        self.actor = deepcopy(dict(actor))
        self.authorized_by = deepcopy(dict(authorized_by))
        self.recorded_by = deepcopy(dict(recorded_by))
        self.correlation_id = correlation_id
        self.classification = classification

    def build(self, event: RuntimeEvent) -> Iterable[Mapping[str, Any]]:
        entity_id = _slug(event.event_id)
        message_id = f"evt-{entity_id}"
        return (
            {
                "protocol": "w1-cip",
                "protocol_version": "0.1",
                "message_id": message_id,
                "session_id": event.session_id,
                "kind": "event",
                "event_class": "protocol_event",
                "type": "entity.version.created",
                "actor": deepcopy(self.actor),
                "authorized_by": deepcopy(self.authorized_by),
                "recorded_by": deepcopy(self.recorded_by),
                "occurred_at": event.occurred_at,
                "received_at": event.occurred_at,
                "recorded_at": event.occurred_at,
                "correlation_id": self.correlation_id,
                "related_events": [],
                "entity": {
                    "entity_type": "artifact",
                    "entity_id": entity_id,
                    "entity_version": 1,
                },
                "precondition": {
                    "mode": "create",
                    "expected_absent": True,
                    "target_identity": {
                        "entity_type": "artifact",
                        "entity_id": entity_id,
                    },
                },
                "payload_schema": "urn:w1-cip:schema:0.1:versioned-entity",
                "payload": {
                    "entity": {
                        "entity_type": "artifact",
                        "entity_id": entity_id,
                        "entity_version": 1,
                    },
                    "created_by_event_id": message_id,
                    "legal_payload_schema": "urn:w1-cip:runtime-artifact:0.1",
                    "legal_payload": {
                        "classification": self.classification,
                        "label": event.event_type,
                        "run_id": event.run_id,
                        "task_id": event.task_id,
                        "details": deepcopy(dict(event.details)),
                    },
                },
            },
        )


@dataclass(frozen=True)
class TaskRunResult:
    task_id: str
    status: str
    resource_id: str | None = None
    output: Mapping[str, Any] | None = None
    attempts: int = 0
    error_code: str | None = None
    requires_extra_review: bool = False


@dataclass(frozen=True)
class OrchestratorRunResult:
    run_id: str
    session_id: str
    status: str
    tasks: tuple[TaskRunResult, ...]
    final_output: Mapping[str, Any] | None
    appended_message_ids: tuple[str, ...]
    resource_plan: Mapping[str, Any]
    disclosures: tuple[Mapping[str, Any], ...] = ()
    error_code: str | None = None


@dataclass(frozen=True)
class RunInputs:
    run_id: str
    session_id: str
    goal: Mapping[str, Any]
    team_plan: Mapping[str, Any]
    resource_plan: Mapping[str, Any]
    context: Mapping[str, Any] = field(default_factory=dict)
    domains: frozenset[str] = frozenset()
    max_attempts_per_resource: int = 2


class GoalTaskCompiler:
    """Compile GoalContract semantics into a deterministic abstract task DAG."""

    def __init__(
        self,
        *,
        context_fields_by_deliverable: Mapping[str, Sequence[str]] | None = None,
        domains: Sequence[str] = (),
    ) -> None:
        self.context_fields_by_deliverable = {
            key: tuple(values)
            for key, values in (context_fields_by_deliverable or {}).items()
        }
        self.domains = tuple(domains)

    @staticmethod
    def _risk(goal: Mapping[str, Any]) -> tuple[str, str]:
        policy = goal.get("risk_policy", {})
        return str(policy.get("risk_level", "low")), str(policy.get("error_cost", "minor"))

    @staticmethod
    def _complexity(goal: Mapping[str, Any]) -> str:
        deliverables = goal.get("deliverables", [])
        constraints = goal.get("constraints", [])
        if len(deliverables) <= 2 and len(constraints) <= 3:
            return "bounded"
        if len(deliverables) <= 6 and len(constraints) <= 10:
            return "multi_step"
        return "open_ended"

    def compile(self, goal: Mapping[str, Any], team_plan: Mapping[str, Any]) -> CompiledWorkflow:
        deliverables = [item for item in goal.get("deliverables", []) if item.get("required", False)]
        if not deliverables:
            raise WorkflowCompilationError("goal_has_no_required_deliverables")

        risk_level, error_cost = self._risk(goal)
        complexity = self._complexity(goal)
        tasks: list[CompiledTask] = []
        execution_by_deliverable: dict[str, str] = {}

        for item in deliverables:
            deliverable_id = item.get("deliverable_id")
            if not isinstance(deliverable_id, str) or not CANONICAL_ID.fullmatch(deliverable_id):
                raise WorkflowCompilationError("invalid_deliverable_id")
            task_id = f"execute-{deliverable_id}"
            execution_by_deliverable[deliverable_id] = task_id
            tasks.append(
                CompiledTask(
                    task_id=task_id,
                    title=f"Produce {deliverable_id}",
                    phase="execution",
                    role="executor",
                    expected_output_type="contribution",
                    deliverable_id=deliverable_id,
                    required_context_fields=self.context_fields_by_deliverable.get(deliverable_id, ()),
                    estimated_units=1,
                    domains=self.domains,
                )
            )

        verification_tasks: list[str] = []
        for criterion in goal.get("success_criteria", []):
            if criterion.get("acceptance_effect") != "required_for_completion":
                continue
            verifiability = criterion.get("verifiability")
            if verifiability not in VERIFIABLE_KINDS:
                continue
            criterion_id = criterion.get("criterion_id")
            if not isinstance(criterion_id, str) or not CANONICAL_ID.fullmatch(criterion_id):
                raise WorkflowCompilationError("invalid_criterion_id")
            applies_to = criterion.get("applies_to", [])
            upstream = tuple(
                execution_by_deliverable[item]
                for item in applies_to
                if item in execution_by_deliverable
            ) or tuple(execution_by_deliverable.values())
            evidence_id = f"collect-evidence-{criterion_id}"
            verification_id = f"verify-{criterion_id}"
            tasks.append(
                CompiledTask(
                    task_id=evidence_id,
                    title=f"Collect evidence for {criterion_id}",
                    phase="verification",
                    role="verifier",
                    expected_output_type="evidence",
                    depends_on=upstream,
                    criterion_id=criterion_id,
                    estimated_units=1,
                    domains=self.domains,
                )
            )
            tasks.append(
                CompiledTask(
                    task_id=verification_id,
                    title=f"Verify {criterion_id}",
                    phase="verification",
                    role="verifier",
                    expected_output_type="verification",
                    depends_on=(evidence_id,),
                    criterion_id=criterion_id,
                    estimated_units=1,
                    domains=self.domains,
                )
            )
            verification_tasks.append(verification_id)

        needs_review = (
            risk_level in {"medium", "high"}
            or error_cost in {"material", "severe"}
            or len(deliverables) > 1
            or any(item.get("kind") == "decision" for item in deliverables)
        )
        review_id: str | None = None
        if needs_review:
            review_id = "review-solution-package"
            review_dependencies = tuple(
                dict.fromkeys([*execution_by_deliverable.values(), *verification_tasks])
            )
            tasks.append(
                CompiledTask(
                    task_id=review_id,
                    title="Review the solution package",
                    phase="review",
                    role="reviewer",
                    expected_output_type="review",
                    depends_on=review_dependencies,
                    estimated_units=1,
                    domains=self.domains,
                )
            )

        decision_tasks: list[str] = []
        decision_dependencies = (review_id,) if review_id else tuple(
            dict.fromkeys([*execution_by_deliverable.values(), *verification_tasks])
        )
        for item in deliverables:
            if item.get("kind") != "decision":
                continue
            deliverable_id = str(item["deliverable_id"])
            task_id = f"decide-{deliverable_id}"
            tasks.append(
                CompiledTask(
                    task_id=task_id,
                    title=f"Decide {deliverable_id}",
                    phase="approval",
                    role="decision_authority",
                    expected_output_type="decision",
                    depends_on=decision_dependencies,
                    deliverable_id=deliverable_id,
                    estimated_units=1,
                    domains=self.domains,
                )
            )
            decision_tasks.append(task_id)

        human_approval_required = goal.get("risk_policy", {}).get("autonomy_mode") == "human_approval_required"
        human_approval_required = human_approval_required or any(
            item.get("human_approval_required") is True
            for item in goal.get("decision_policy", [])
        )
        approval_tasks: list[str] = []
        if human_approval_required:
            task_id = "approve-final-decisions"
            tasks.append(
                CompiledTask(
                    task_id=task_id,
                    title="Approve the final decisions",
                    phase="approval",
                    role="decision_authority",
                    expected_output_type="approval",
                    depends_on=tuple(decision_tasks or decision_dependencies),
                    estimated_units=1,
                    domains=self.domains,
                )
            )
            approval_tasks.append(task_id)

        synthesis_dependencies = tuple(
            approval_tasks
            or decision_tasks
            or ((review_id,) if review_id else tuple(verification_tasks or execution_by_deliverable.values()))
        )
        tasks.append(
            CompiledTask(
                task_id="synthesize-final-result",
                title="Synthesize the final result",
                phase="synthesis",
                role="synthesizer",
                expected_output_type="final_result",
                depends_on=synthesis_dependencies,
                estimated_units=1,
                domains=self.domains,
            )
        )

        workflow = CompiledWorkflow(tuple(tasks), risk_level, error_cost, complexity)
        _validate_workflow(workflow)
        _validate_team_can_cover(workflow, team_plan)
        return workflow


class TeamPlanner:
    """Derive required roles and choose available role assignments."""

    @staticmethod
    def required_roles(goal: Mapping[str, Any]) -> tuple[str, ...]:
        roles = ["orchestrator", "executor", "synthesizer"]
        risk = goal.get("risk_policy", {})
        if risk.get("risk_level") in {"medium", "high"} or risk.get("error_cost") in {"material", "severe"}:
            roles.append("reviewer")
        if any(item.get("verifiability") in VERIFIABLE_KINDS for item in goal.get("success_criteria", [])):
            roles.append("verifier")
        if any(item.get("kind") == "decision" for item in goal.get("deliverables", [])):
            roles.append("decision_authority")
        return tuple(dict.fromkeys(roles))

    def build(
        self,
        goal: Mapping[str, Any],
        available_assignments: Sequence[Mapping[str, Any]],
    ) -> TeamFormationDraft:
        required = self.required_roles(goal)
        selected: dict[str, Mapping[str, Any]] = {}
        for role in required:
            candidate = next((item for item in available_assignments if item.get("role") == role), None)
            if candidate is not None:
                selected[role] = deepcopy(dict(candidate))
        missing = tuple(role for role in required if role not in selected)
        risk = goal.get("risk_policy", {})
        return TeamFormationDraft(
            required_roles=required,
            selected_assignments=selected,
            missing_roles=missing,
            risk_level=str(risk.get("risk_level", "low")),
            error_cost=str(risk.get("error_cost", "minor")),
        )


class OrchestratorJournal:
    """Mutable operational checkpoint store, separate from the protocol ledger."""

    def __init__(self, database_path: str | Path) -> None:
        self.path = Path(database_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(
            str(self.path), isolation_level=None, check_same_thread=False, timeout=10
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.execute("PRAGMA synchronous = FULL")
        self._initialize()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def __enter__(self) -> "OrchestratorJournal":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def _initialize(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS orchestrator_runs (
                run_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                status TEXT NOT NULL,
                workflow_json TEXT NOT NULL,
                resource_plan_json TEXT NOT NULL,
                context_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_error_code TEXT,
                result_json TEXT
            );

            CREATE TABLE IF NOT EXISTS orchestrator_tasks (
                run_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                ordinal INTEGER NOT NULL,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                resource_id TEXT,
                output_json TEXT,
                error_code TEXT,
                requires_extra_review INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (run_id, task_id),
                FOREIGN KEY (run_id) REFERENCES orchestrator_runs(run_id)
            );

            CREATE TABLE IF NOT EXISTS orchestrator_calls (
                call_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                resource_id TEXT NOT NULL,
                idempotency_key TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL,
                request_json TEXT NOT NULL,
                response_json TEXT,
                error_code TEXT,
                units_consumed INTEGER,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                FOREIGN KEY (run_id, task_id) REFERENCES orchestrator_tasks(run_id, task_id)
            );


            CREATE TABLE IF NOT EXISTS provider_quota_observations (
                observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                resource_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                observation_json TEXT NOT NULL,
                FOREIGN KEY (run_id, task_id) REFERENCES orchestrator_tasks(run_id, task_id)
            );

            CREATE INDEX IF NOT EXISTS idx_provider_quota_observations_run
            ON provider_quota_observations(run_id, observation_id);
            """
        )

    def initialize_run(
        self,
        inputs: RunInputs,
        workflow: CompiledWorkflow,
        resource_plan: Mapping[str, Any],
    ) -> None:
        workflow_json = canonical_json(_workflow_to_dict(workflow))
        now = utc_now()
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._connection.execute(
                    "SELECT * FROM orchestrator_runs WHERE run_id = ?", (inputs.run_id,)
                ).fetchone()
                if row is None:
                    self._connection.execute(
                        """
                        INSERT INTO orchestrator_runs(
                            run_id, session_id, status, workflow_json,
                            resource_plan_json, context_json, created_at, updated_at
                        ) VALUES (?, ?, 'running', ?, ?, ?, ?, ?)
                        """,
                        (
                            inputs.run_id,
                            inputs.session_id,
                            workflow_json,
                            canonical_json(resource_plan),
                            canonical_json(inputs.context),
                            now,
                            now,
                        ),
                    )
                    for ordinal, task in enumerate(workflow.tasks):
                        self._connection.execute(
                            """
                            INSERT INTO orchestrator_tasks(
                                run_id, task_id, ordinal, status
                            ) VALUES (?, ?, ?, 'pending')
                            """,
                            (inputs.run_id, task.task_id, ordinal),
                        )
                else:
                    if row["session_id"] != inputs.session_id or row["workflow_json"] != workflow_json:
                        raise RunConflictError()
                    if row["status"] in RESUMABLE_RUN_STATUSES:
                        self._connection.execute(
                            """
                            UPDATE orchestrator_runs
                            SET status = 'running', resource_plan_json = ?, context_json = ?,
                                last_error_code = NULL, updated_at = ?
                            WHERE run_id = ?
                            """,
                            (
                                canonical_json(resource_plan),
                                canonical_json(inputs.context),
                                now,
                                inputs.run_id,
                            ),
                        )
                        self._connection.execute(
                            """
                            UPDATE orchestrator_tasks
                            SET status = 'pending', error_code = NULL
                            WHERE run_id = ?
                              AND status IN ('blocked', 'awaiting_reset', 'awaiting_user')
                            """,
                            (inputs.run_id,),
                        )
                self._connection.execute("COMMIT")
            except Exception:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                raise

    def load_run(self, run_id: str) -> dict[str, Any] | None:
        row = self._connection.execute(
            "SELECT * FROM orchestrator_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            return None
        return dict(row)

    def task_rows(self, run_id: str) -> list[dict[str, Any]]:
        rows = self._connection.execute(
            "SELECT * FROM orchestrator_tasks WHERE run_id = ? ORDER BY ordinal", (run_id,)
        ).fetchall()
        return [dict(row) for row in rows]

    def update_task(
        self,
        run_id: str,
        task_id: str,
        *,
        status: str,
        attempts: int | None = None,
        resource_id: str | None = None,
        output: Mapping[str, Any] | None = None,
        error_code: str | None = None,
        requires_extra_review: bool | None = None,
    ) -> None:
        fields = ["status = ?", "error_code = ?"]
        values: list[Any] = [status, error_code]
        if attempts is not None:
            fields.append("attempts = ?")
            values.append(attempts)
        if resource_id is not None:
            fields.append("resource_id = ?")
            values.append(resource_id)
        if output is not None:
            fields.append("output_json = ?")
            values.append(canonical_json(output))
        if requires_extra_review is not None:
            fields.append("requires_extra_review = ?")
            values.append(1 if requires_extra_review else 0)
        values.extend([run_id, task_id])
        with self._lock:
            self._connection.execute(
                f"UPDATE orchestrator_tasks SET {', '.join(fields)} WHERE run_id = ? AND task_id = ?",
                values,
            )

    def save_resource_plan(self, run_id: str, plan: Mapping[str, Any]) -> None:
        with self._lock:
            self._connection.execute(
                """
                UPDATE orchestrator_runs
                SET resource_plan_json = ?, updated_at = ?
                WHERE run_id = ?
                """,
                (canonical_json(plan), utc_now(), run_id),
            )

    def set_run_status(
        self,
        run_id: str,
        status: str,
        *,
        error_code: str | None = None,
        result: Mapping[str, Any] | None = None,
    ) -> None:
        with self._lock:
            self._connection.execute(
                """
                UPDATE orchestrator_runs
                SET status = ?, last_error_code = ?, result_json = ?, updated_at = ?
                WHERE run_id = ?
                """,
                (
                    status,
                    error_code,
                    canonical_json(result) if result is not None else None,
                    utc_now(),
                    run_id,
                ),
            )

    def call_by_key(self, idempotency_key: str) -> dict[str, Any] | None:
        row = self._connection.execute(
            "SELECT * FROM orchestrator_calls WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
        return dict(row) if row is not None else None

    def latest_call_for_task(self, run_id: str, task_id: str) -> dict[str, Any] | None:
        row = self._connection.execute(
            """
            SELECT rowid, * FROM orchestrator_calls
            WHERE run_id = ? AND task_id = ?
            ORDER BY rowid DESC LIMIT 1
            """,
            (run_id, task_id),
        ).fetchone()
        return dict(row) if row is not None else None

    def start_call(self, call_id: str, request: ProviderRequest) -> None:
        with self._lock:
            self._connection.execute(
                """
                INSERT OR IGNORE INTO orchestrator_calls(
                    call_id, run_id, task_id, resource_id, idempotency_key,
                    status, request_json, started_at
                ) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)
                """,
                (
                    call_id,
                    request.run_id,
                    request.task.task_id,
                    request.resource_id,
                    request.idempotency_key,
                    canonical_json(_request_to_dict(request)),
                    utc_now(),
                ),
            )

    def complete_call(self, idempotency_key: str, response: ProviderResponse) -> None:
        with self._lock:
            self._connection.execute(
                """
                UPDATE orchestrator_calls
                SET status = 'completed', response_json = ?, units_consumed = ?, completed_at = ?
                WHERE idempotency_key = ?
                """,
                (
                    canonical_json(_response_to_dict(response)),
                    response.units_consumed,
                    utc_now(),
                    idempotency_key,
                ),
            )

    def fail_call(self, idempotency_key: str, code: str, *, uncertain: bool = False) -> None:
        with self._lock:
            self._connection.execute(
                """
                UPDATE orchestrator_calls
                SET status = ?, error_code = ?, completed_at = ?
                WHERE idempotency_key = ?
                """,
                ("uncertain" if uncertain else "failed", code, utc_now(), idempotency_key),
            )

    def record_quota_observation(
        self,
        run_id: str,
        task_id: str,
        resource_id: str,
        observation: Mapping[str, Any],
    ) -> None:
        provider = observation.get("provider")
        observed_at = observation.get("observed_at")
        if not isinstance(provider, str) or not isinstance(observed_at, str):
            return
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO provider_quota_observations(
                    run_id, task_id, resource_id, provider, observed_at, observation_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    task_id,
                    resource_id,
                    provider,
                    observed_at,
                    canonical_json(observation),
                ),
            )

    def quota_observations(self, run_id: str) -> list[dict[str, Any]]:
        rows = self._connection.execute(
            """
            SELECT observation_id, run_id, task_id, resource_id, provider,
                   observed_at, observation_json
            FROM provider_quota_observations
            WHERE run_id = ?
            ORDER BY observation_id
            """,
            (run_id,),
        ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["observation"] = json.loads(item.pop("observation_json"))
            result.append(item)
        return result

    def call_rows(self, run_id: str) -> list[dict[str, Any]]:
        """Return redaction-safe persisted provider-call records for diagnostics."""

        rows = self._connection.execute(
            """
            SELECT call_id, run_id, task_id, resource_id, idempotency_key, status,
                   request_json, response_json, error_code, units_consumed,
                   started_at, completed_at
            FROM orchestrator_calls
            WHERE run_id = ?
            ORDER BY rowid
            """,
            (run_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def count_started_calls(self, run_id: str) -> int:
        row = self._connection.execute(
            "SELECT COUNT(*) AS n FROM orchestrator_calls WHERE run_id = ?", (run_id,)
        ).fetchone()
        return int(row["n"])


class OrchestratorCore:
    """Deterministic provider-routing and task-execution engine."""

    def __init__(
        self,
        *,
        session_store: SessionStore,
        journal: OrchestratorJournal,
        providers: Mapping[str, ProviderAdapter],
        envelope_factory: EnvelopeFactory | None = None,
        compiler: GoalTaskCompiler | None = None,
        event_listener: Callable[[RuntimeEvent], None] | None = None,
        action_runtime: ActionRuntime | None = None,
        memory_context_provider: Any | None = None,
    ) -> None:
        self.session_store = session_store
        self.journal = journal
        self.providers = dict(providers)
        self.envelope_factory = envelope_factory or NullEnvelopeFactory()
        self.compiler = compiler or GoalTaskCompiler()
        self.event_listener = event_listener
        self.action_runtime = action_runtime
        self.memory_context_provider = memory_context_provider

    def bootstrap_session(self, envelopes: Iterable[Mapping[str, Any]]) -> tuple[str, ...]:
        appended: list[str] = []
        for envelope in envelopes:
            result = self.session_store.append(deepcopy(dict(envelope)))
            appended.append(result.message_id)
        return tuple(appended)

    def run_from_session(
        self,
        *,
        run_id: str,
        session_id: str,
        goal_id: str,
        team_plan_id: str,
        resource_plan_id: str,
        context: Mapping[str, Any] | None = None,
        domains: frozenset[str] = frozenset(),
        max_attempts_per_resource: int = 2,
    ) -> OrchestratorRunResult:
        goal = self._load_latest_payload(session_id, "goal_contract", goal_id)
        team = self._load_latest_payload(session_id, "team_plan", team_plan_id)
        resource = self._load_latest_payload(session_id, "execution_resource_plan", resource_plan_id)
        return self.run(
            RunInputs(
                run_id=run_id,
                session_id=session_id,
                goal=goal,
                team_plan=team,
                resource_plan=resource,
                context=context or {},
                domains=domains,
                max_attempts_per_resource=max_attempts_per_resource,
            )
        )

    def _load_latest_payload(self, session_id: str, entity_type: str, entity_id: str) -> Mapping[str, Any]:
        record = self.session_store.get_entity(session_id, entity_type, entity_id)
        if record is None:
            raise WorkflowCompilationError(f"missing_{entity_type}")
        payload = record.get("legal_payload")
        if not isinstance(payload, dict):
            raise WorkflowCompilationError(f"invalid_{entity_type}_payload")
        return deepcopy(payload)

    def run(
        self,
        inputs: RunInputs,
        *,
        workflow: CompiledWorkflow | None = None,
    ) -> OrchestratorRunResult:
        if not CANONICAL_ID.fullmatch(inputs.run_id):
            raise WorkflowCompilationError("invalid_run_id")
        compiled = workflow or self.compiler.compile(inputs.goal, inputs.team_plan)
        _validate_workflow(compiled)
        self.journal.initialize_run(inputs, compiled, inputs.resource_plan)

        persisted = self.journal.load_run(inputs.run_id)
        assert persisted is not None
        resource_plan = json.loads(persisted["resource_plan_json"])
        existing_status = persisted["status"]
        if existing_status in TERMINAL_RUN_STATUSES:
            return self._result_from_journal(inputs, resource_plan)

        appended: list[str] = []
        disclosures: list[Mapping[str, Any]] = []
        self._emit(
            RuntimeEvent(
                event_id=f"orch-{inputs.run_id}-run-started",
                event_type="orchestrator.run.started",
                run_id=inputs.run_id,
                session_id=inputs.session_id,
                occurred_at=utc_now(),
                details={"task_count": len(compiled.tasks)},
            ),
            appended,
        )

        while True:
            rows = {row["task_id"]: row for row in self.journal.task_rows(inputs.run_id)}
            completed = {task_id for task_id, row in rows.items() if row["status"] == "completed"}
            if len(completed) == len(compiled.tasks):
                break
            ready = [
                task
                for task in compiled.tasks
                if rows[task.task_id]["status"] in {"pending", "running"}
                and set(task.depends_on).issubset(completed)
            ]
            if not ready:
                self.journal.set_run_status(
                    inputs.run_id,
                    "failed",
                    error_code="orchestrator_no_runnable_task",
                )
                return self._result_from_journal(
                    inputs,
                    resource_plan,
                    appended=appended,
                    disclosures=disclosures,
                )

            task = ready[0]
            result, resource_plan, new_disclosures = self._execute_task(
                inputs,
                compiled,
                task,
                rows,
                resource_plan,
                appended,
            )
            disclosures.extend(new_disclosures)
            if result.status != "completed":
                self.journal.set_run_status(
                    inputs.run_id,
                    result.status,
                    error_code=result.error_code,
                )
                self._emit(
                    RuntimeEvent(
                        event_id=f"orch-{inputs.run_id}-run-{result.status}",
                        event_type=f"orchestrator.run.{result.status}",
                        run_id=inputs.run_id,
                        session_id=inputs.session_id,
                        occurred_at=utc_now(),
                        task_id=task.task_id,
                        details={"error_code": result.error_code},
                    ),
                    appended,
                )
                return self._result_from_journal(
                    inputs,
                    resource_plan,
                    appended=appended,
                    disclosures=disclosures,
                )

        rows = {row["task_id"]: row for row in self.journal.task_rows(inputs.run_id)}
        _enforce_extra_review(compiled, rows)
        synthesis = rows.get("synthesize-final-result")
        final_output = json.loads(synthesis["output_json"]) if synthesis and synthesis["output_json"] else None
        self.journal.set_run_status(inputs.run_id, "completed", result=final_output)
        self._emit(
            RuntimeEvent(
                event_id=f"orch-{inputs.run_id}-run-completed",
                event_type="orchestrator.run.completed",
                run_id=inputs.run_id,
                session_id=inputs.session_id,
                occurred_at=utc_now(),
                details={"final_output_present": final_output is not None},
            ),
            appended,
        )
        return self._result_from_journal(
            inputs,
            resource_plan,
            appended=appended,
            disclosures=disclosures,
        )

    def _execute_task(
        self,
        inputs: RunInputs,
        workflow: CompiledWorkflow,
        task: CompiledTask,
        rows: Mapping[str, Mapping[str, Any]],
        resource_plan: dict[str, Any],
        appended: list[str],
    ) -> tuple[TaskRunResult, dict[str, Any], list[Mapping[str, Any]]]:
        attempts = int(rows[task.task_id]["attempts"])
        prior_outputs = {
            task_id: json.loads(row["output_json"])
            for task_id, row in rows.items()
            if row["status"] == "completed" and row.get("output_json")
        }
        disclosures: list[Mapping[str, Any]] = []
        unavailable_for_task: set[str] = set()

        # Recover a provider call that was persisted before the process stopped.
        # The original request and idempotency key are reused; a new attempt is
        # never invented merely because the runtime restarted.
        latest_call = self.journal.latest_call_for_task(inputs.run_id, task.task_id)
        if rows[task.task_id]["status"] in {"running", "pending"} and latest_call is not None:
            if latest_call["status"] in {"completed", "pending", "uncertain"}:
                request = _request_from_dict(json.loads(latest_call["request_json"]))
                attempts = max(attempts, request.attempt)
                provider = self.providers.get(request.resource_id)
                routing = _routing_metadata_for_resource(
                    resource_plan,
                    task=task,
                    workflow=workflow,
                    inputs=inputs,
                    resource_id=request.resource_id,
                )
                if latest_call["status"] == "completed":
                    response = _response_from_dict(json.loads(latest_call["response_json"]))
                    return self._accept_task_response(
                        inputs=inputs,
                        task=task,
                        request=request,
                        response=response,
                        routing=routing,
                        resource_plan=resource_plan,
                        appended=appended,
                        attempts=attempts,
                        disclosures=disclosures,
                    )
                if provider is None or not provider.supports_idempotency:
                    self.journal.update_task(
                        inputs.run_id,
                        task.task_id,
                        status="awaiting_reconciliation",
                        attempts=attempts,
                        resource_id=request.resource_id,
                        error_code=UnsafeResumeError.code,
                    )
                    return (
                        TaskRunResult(
                            task.task_id,
                            "awaiting_reconciliation",
                            request.resource_id,
                            attempts=attempts,
                            error_code=UnsafeResumeError.code,
                        ),
                        resource_plan,
                        disclosures,
                    )

                recovered_response: ProviderResponse | None = None
                for _ in range(max(1, inputs.max_attempts_per_resource)):
                    try:
                        recovered_response = provider.invoke(request)
                        break
                    except ProviderOutcomeUncertain as exc:
                        self.journal.fail_call(request.idempotency_key, exc.code, uncertain=True)
                        continue
                    except ProviderQuotaExhausted as exc:
                        self.journal.fail_call(request.idempotency_key, exc.code)
                        resource_plan = _mark_resource_quota_exhausted(
                            resource_plan, request.resource_id, exc.reset_at
                        )
                        self.journal.save_resource_plan(inputs.run_id, resource_plan)
                        unavailable_for_task.add(request.resource_id)
                        disclosures.append(
                            {
                                "task_id": task.task_id,
                                "resource_id": request.resource_id,
                                "reason": "quota_exhausted",
                                "reset_at": exc.reset_at,
                            }
                        )
                        break
                    except (ProviderTransientError, ProviderPermanentError) as exc:
                        self.journal.fail_call(request.idempotency_key, exc.code)
                        unavailable_for_task.add(request.resource_id)
                        if exc.code == "provider_rate_limited":
                            disclosures.append(
                                {
                                    "task_id": task.task_id,
                                    "resource_id": request.resource_id,
                                    "reason": "rate_limited",
                                    "retry_after_seconds": getattr(
                                        exc, "retry_after_seconds", None
                                    ),
                                }
                            )
                        break
                if recovered_response is not None:
                    self.journal.complete_call(request.idempotency_key, recovered_response)
                    return self._accept_task_response(
                        inputs=inputs,
                        task=task,
                        request=request,
                        response=recovered_response,
                        routing=routing,
                        resource_plan=resource_plan,
                        appended=appended,
                        attempts=attempts,
                        disclosures=disclosures,
                    )
                if latest_call["status"] == "uncertain" and not unavailable_for_task:
                    self.journal.update_task(
                        inputs.run_id,
                        task.task_id,
                        status="awaiting_reconciliation",
                        attempts=attempts,
                        resource_id=request.resource_id,
                        error_code=ProviderOutcomeUncertain.code,
                    )
                    return (
                        TaskRunResult(
                            task.task_id,
                            "awaiting_reconciliation",
                            request.resource_id,
                            attempts=attempts,
                            error_code=ProviderOutcomeUncertain.code,
                        ),
                        resource_plan,
                        disclosures,
                    )

        while True:
            max_calls = inputs.team_plan.get("budget", {}).get("max_model_calls")
            if isinstance(max_calls, int) and self.journal.count_started_calls(inputs.run_id) >= max_calls:
                result = TaskRunResult(
                    task.task_id,
                    "blocked",
                    attempts=attempts,
                    error_code="orchestrator_model_call_budget_exhausted",
                )
                self.journal.update_task(
                    inputs.run_id,
                    task.task_id,
                    status="blocked",
                    attempts=attempts,
                    error_code=result.error_code,
                )
                return result, resource_plan, disclosures

            routing_plan = _plan_with_unavailable_resources(resource_plan, unavailable_for_task)
            routing = route_execution_resource(
                routing_plan,
                role=task.role,
                phase=task.phase,
                risk_level=workflow.risk_level,
                task_complexity=workflow.task_complexity,
                domains=set(task.domains) | set(inputs.domains),
                estimated_units=task.estimated_units,
            )
            if routing.selected_resource_id is None:
                status = {
                    "await_reset": "awaiting_reset",
                    "await_user": "awaiting_user",
                    "cancelled": "cancelled",
                }.get(routing.action, "blocked")
                error = routing.error_codes[0] if routing.error_codes else "orchestrator_resource_unavailable"
                result = TaskRunResult(task.task_id, status, attempts=attempts, error_code=error)
                self.journal.update_task(
                    inputs.run_id,
                    task.task_id,
                    status=status,
                    attempts=attempts,
                    error_code=error,
                )
                return result, resource_plan, disclosures

            resource_id = routing.selected_resource_id
            provider = self.providers.get(resource_id)
            if provider is None:
                unavailable_for_task.add(resource_id)
                if len(unavailable_for_task) >= len(resource_plan.get("resources", [])):
                    error = "orchestrator_provider_adapter_missing"
                    result = TaskRunResult(task.task_id, "blocked", resource_id, attempts=attempts, error_code=error)
                    self.journal.update_task(
                        inputs.run_id,
                        task.task_id,
                        status="blocked",
                        attempts=attempts,
                        resource_id=resource_id,
                        error_code=error,
                    )
                    return result, resource_plan, disclosures
                continue

            resolved_context = dict(inputs.context)
            memory_citations: dict[str, str] = {}
            memory_conflicts: tuple[str, ...] = ()
            if self.memory_context_provider is not None:
                resolution = self.memory_context_provider.resolve_fields(
                    task=task,
                    required_fields=task.required_context_fields,
                    explicit_context=inputs.context,
                )
                for field_name, value in dict(getattr(resolution, "values", {})).items():
                    resolved_context.setdefault(field_name, deepcopy(value))
                memory_citations = dict(getattr(resolution, "citations", {}))
                memory_conflicts = tuple(getattr(resolution, "conflict_fields", ()))

            missing_context = [field for field in task.required_context_fields if field not in resolved_context]
            if missing_context:
                error = "orchestrator_memory_conflict" if any(field in memory_conflicts for field in missing_context) else "orchestrator_required_context_missing"
                result = TaskRunResult(task.task_id, "awaiting_user", resource_id, attempts=attempts, error_code=error)
                self.journal.update_task(
                    inputs.run_id,
                    task.task_id,
                    status="awaiting_user",
                    attempts=attempts,
                    resource_id=resource_id,
                    error_code=error,
                )
                return result, resource_plan, disclosures

            granted_context = {field: deepcopy(resolved_context[field]) for field in task.required_context_fields}
            attempts += 1
            idempotency_key = _idempotency_key(inputs.run_id, task.task_id, resource_id, attempts)
            request = ProviderRequest(
                run_id=inputs.run_id,
                session_id=inputs.session_id,
                task=task,
                resource_id=resource_id,
                idempotency_key=idempotency_key,
                context=granted_context,
                prior_outputs=prior_outputs,
                attempt=attempts,
            )
            self.journal.update_task(
                inputs.run_id,
                task.task_id,
                status="running",
                attempts=attempts,
                resource_id=resource_id,
            )
            self._emit(
                RuntimeEvent(
                    event_id=f"orch-{inputs.run_id}-{task.task_id}-attempt-{attempts}-started",
                    event_type="orchestrator.task.started",
                    run_id=inputs.run_id,
                    session_id=inputs.session_id,
                    occurred_at=utc_now(),
                    task_id=task.task_id,
                    details={"resource_id": resource_id, "phase": task.phase, "role": task.role},
                ),
                appended,
            )
            self._emit(
                RuntimeEvent(
                    event_id=f"orch-{inputs.run_id}-{task.task_id}-attempt-{attempts}-context",
                    event_type="orchestrator.context.granted",
                    run_id=inputs.run_id,
                    session_id=inputs.session_id,
                    occurred_at=utc_now(),
                    task_id=task.task_id,
                    details={
                        "resource_id": resource_id,
                        "fields": list(granted_context),
                        "memory_citations": memory_citations,
                    },
                ),
                appended,
            )

            call_id = f"call-{hashlib.sha256(idempotency_key.encode()).hexdigest()[:24]}"
            existing_call = self.journal.call_by_key(idempotency_key)
            if existing_call is not None and existing_call["status"] == "completed":
                response = _response_from_dict(json.loads(existing_call["response_json"]))
            else:
                if existing_call is not None and existing_call["status"] in {"pending", "uncertain"}:
                    if not provider.supports_idempotency:
                        self.journal.set_run_status(
                            inputs.run_id,
                            "awaiting_reconciliation",
                            error_code=UnsafeResumeError.code,
                        )
                        raise UnsafeResumeError()
                self.journal.start_call(call_id, request)
                try:
                    response = provider.invoke(request)
                except ProviderQuotaExhausted as exc:
                    self.journal.fail_call(idempotency_key, exc.code)
                    resource_plan = _mark_resource_quota_exhausted(resource_plan, resource_id, exc.reset_at)
                    self.journal.save_resource_plan(inputs.run_id, resource_plan)
                    unavailable_for_task.add(resource_id)
                    disclosure = {
                        "task_id": task.task_id,
                        "resource_id": resource_id,
                        "reason": "quota_exhausted",
                        "reset_at": exc.reset_at,
                    }
                    disclosures.append(disclosure)
                    self._emit(
                        RuntimeEvent(
                            event_id=f"orch-{inputs.run_id}-{task.task_id}-{resource_id}-quota-exhausted",
                            event_type="orchestrator.resource.quota_exhausted",
                            run_id=inputs.run_id,
                            session_id=inputs.session_id,
                            occurred_at=utc_now(),
                            task_id=task.task_id,
                            details=disclosure,
                        ),
                        appended,
                    )
                    continue
                except ProviderOutcomeUncertain as exc:
                    self.journal.fail_call(idempotency_key, exc.code, uncertain=True)
                    if not provider.supports_idempotency:
                        self.journal.update_task(
                            inputs.run_id,
                            task.task_id,
                            status="awaiting_reconciliation",
                            attempts=attempts,
                            resource_id=resource_id,
                            error_code=UnsafeResumeError.code,
                        )
                        return (
                            TaskRunResult(
                                task.task_id,
                                "awaiting_reconciliation",
                                resource_id,
                                attempts=attempts,
                                error_code=UnsafeResumeError.code,
                            ),
                            resource_plan,
                            disclosures,
                        )
                    if attempts >= inputs.max_attempts_per_resource:
                        unavailable_for_task.add(resource_id)
                    continue
                except ProviderTransientError as exc:
                    self.journal.fail_call(idempotency_key, exc.code)
                    if exc.code == "provider_rate_limited":
                        unavailable_for_task.add(resource_id)
                        disclosures.append(
                            {
                                "task_id": task.task_id,
                                "resource_id": resource_id,
                                "reason": "rate_limited",
                                "retry_after_seconds": getattr(
                                    exc, "retry_after_seconds", None
                                ),
                            }
                        )
                    elif attempts >= inputs.max_attempts_per_resource:
                        unavailable_for_task.add(resource_id)
                    continue
                except ProviderPermanentError as exc:
                    self.journal.fail_call(idempotency_key, exc.code)
                    unavailable_for_task.add(resource_id)
                    continue
                self.journal.complete_call(idempotency_key, response)

            return self._accept_task_response(
                inputs=inputs,
                task=task,
                request=request,
                response=response,
                routing=routing,
                resource_plan=resource_plan,
                appended=appended,
                attempts=attempts,
                disclosures=disclosures,
            )

    def _accept_task_response(
        self,
        *,
        inputs: RunInputs,
        task: CompiledTask,
        request: ProviderRequest,
        response: ProviderResponse,
        routing: ResourceRoutingResult,
        resource_plan: dict[str, Any],
        appended: list[str],
        attempts: int,
        disclosures: list[Mapping[str, Any]],
    ) -> tuple[TaskRunResult, dict[str, Any], list[Mapping[str, Any]]]:
        resource_id = request.resource_id
        validation_error = _validate_response(task, request.context, response)
        if validation_error is not None:
            self.journal.fail_call(request.idempotency_key, validation_error)
            self.journal.update_task(
                inputs.run_id,
                task.task_id,
                status="failed",
                attempts=attempts,
                resource_id=resource_id,
                error_code=validation_error,
            )
            return (
                TaskRunResult(
                    task.task_id,
                    "failed",
                    resource_id,
                    attempts=attempts,
                    error_code=validation_error,
                ),
                resource_plan,
                disclosures,
            )

        action_results: list[Mapping[str, Any]] = []
        if response.action_requests:
            if self.action_runtime is None:
                self.journal.update_task(
                    inputs.run_id,
                    task.task_id,
                    status="blocked",
                    attempts=attempts,
                    resource_id=resource_id,
                    error_code="orchestrator_action_runtime_unavailable",
                )
                return (
                    TaskRunResult(
                        task.task_id,
                        "blocked",
                        resource_id,
                        attempts=attempts,
                        error_code="orchestrator_action_runtime_unavailable",
                    ),
                    resource_plan,
                    disclosures,
                )
            for item in response.action_requests:
                if not isinstance(item, Mapping):
                    raise OutputContractError("provider_action_request_invalid")
                try:
                    action_request = ActionRequest(
                        action_id=str(item["action_id"]),
                        kind=str(item["kind"]),
                        parameters=deepcopy(dict(item.get("parameters", {}))),
                        requested_by=str(item.get("requested_by", resource_id)),
                        reason=item.get("reason") if isinstance(item.get("reason"), str) else None,
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    raise OutputContractError("provider_action_request_invalid") from exc
                existing = self.action_runtime.get_result(action_request.action_id)
                if existing is None:
                    try:
                        existing = self.action_runtime.execute(action_request)
                    except ActionApprovalRequired as exc:
                        pending = {
                            "action_id": action_request.action_id,
                            "kind": action_request.kind,
                            "plan": asdict(exc.plan),
                        }
                        self.journal.update_task(
                            inputs.run_id,
                            task.task_id,
                            status="awaiting_user",
                            attempts=attempts,
                            resource_id=resource_id,
                            output={"pending_action": pending},
                            error_code="action_approval_required",
                        )
                        self._emit(
                            RuntimeEvent(
                                event_id=f"orch-{inputs.run_id}-{task.task_id}-{action_request.action_id}-approval-required",
                                event_type="orchestrator.action.approval_required",
                                run_id=inputs.run_id,
                                session_id=inputs.session_id,
                                occurred_at=utc_now(),
                                task_id=task.task_id,
                                details=pending,
                            ),
                            appended,
                        )
                        return (
                            TaskRunResult(
                                task.task_id,
                                "awaiting_user",
                                resource_id,
                                output={"pending_action": pending},
                                attempts=attempts,
                                error_code="action_approval_required",
                            ),
                            resource_plan,
                            disclosures,
                        )
                action_payload = asdict(existing)
                action_results.append(action_payload)
                if existing.status != "completed":
                    self.journal.update_task(
                        inputs.run_id,
                        task.task_id,
                        status="failed",
                        attempts=attempts,
                        resource_id=resource_id,
                        output={"action_results": action_results},
                        error_code=existing.error_code or "orchestrator_action_failed",
                    )
                    return (
                        TaskRunResult(
                            task.task_id,
                            "failed",
                            resource_id,
                            output={"action_results": action_results},
                            attempts=attempts,
                            error_code=existing.error_code or "orchestrator_action_failed",
                        ),
                        resource_plan,
                        disclosures,
                    )
                self._emit(
                    RuntimeEvent(
                        event_id=f"orch-{inputs.run_id}-{task.task_id}-{action_request.action_id}-completed",
                        event_type="orchestrator.action.completed",
                        run_id=inputs.run_id,
                        session_id=inputs.session_id,
                        occurred_at=utc_now(),
                        task_id=task.task_id,
                        details={
                            "action_id": action_request.action_id,
                            "kind": action_request.kind,
                            "changed_paths": list(existing.changed_paths),
                        },
                    ),
                    appended,
                )

        for envelope in response.protocol_envelopes:
            if envelope.get("session_id") != inputs.session_id:
                raise OutputContractError("provider_envelope_session_mismatch")
            appended_result = self.session_store.append(deepcopy(dict(envelope)))
            appended.append(appended_result.message_id)

        quota_observation = response.metadata.get("quota_observation")
        if isinstance(quota_observation, Mapping):
            self.journal.record_quota_observation(
                inputs.run_id,
                task.task_id,
                resource_id,
                quota_observation,
            )

        resource_plan = _consume_resource_units(resource_plan, resource_id, response.units_consumed)
        self.journal.save_resource_plan(inputs.run_id, resource_plan)
        self._emit(
            RuntimeEvent(
                event_id=f"orch-{inputs.run_id}-{task.task_id}-attempt-{attempts}-response",
                event_type="orchestrator.provider.response_accepted",
                run_id=inputs.run_id,
                session_id=inputs.session_id,
                occurred_at=utc_now(),
                task_id=task.task_id,
                details={
                    "resource_id": resource_id,
                    "output_type": response.output_type,
                    "units_consumed": response.units_consumed,
                    "used_fallback": routing.used_fallback,
                    "requires_extra_review": routing.requires_extra_review,
                },
            ),
            appended,
        )
        accepted_output = deepcopy(dict(response.payload))
        if action_results:
            accepted_output["_w1_action_results"] = action_results
        self.journal.update_task(
            inputs.run_id,
            task.task_id,
            status="completed",
            attempts=attempts,
            resource_id=resource_id,
            output=accepted_output,
            requires_extra_review=routing.requires_extra_review,
        )
        self._emit(
            RuntimeEvent(
                event_id=f"orch-{inputs.run_id}-{task.task_id}-completed",
                event_type="orchestrator.task.completed",
                run_id=inputs.run_id,
                session_id=inputs.session_id,
                occurred_at=utc_now(),
                task_id=task.task_id,
                details={"resource_id": resource_id, "output_type": response.output_type},
            ),
            appended,
        )
        if routing.used_fallback:
            disclosures.append(
                {
                    "task_id": task.task_id,
                    "selected_resource_id": resource_id,
                    "activation_reason": "primary_unavailable",
                    "additional_review_required": routing.requires_extra_review,
                }
            )
        return (
            TaskRunResult(
                task.task_id,
                "completed",
                resource_id,
                accepted_output,
                attempts,
                requires_extra_review=routing.requires_extra_review,
            ),
            resource_plan,
            disclosures,
        )

    def _emit(self, event: RuntimeEvent, appended: list[str]) -> None:
        if self.event_listener is not None:
            try:
                self.event_listener(event)
            except Exception:
                # Progress rendering and telemetry are non-authoritative. A broken
                # listener must not corrupt or abort the governed run.
                pass
        for envelope in self.envelope_factory.build(event):
            result = self.session_store.append(deepcopy(dict(envelope)))
            appended.append(result.message_id)

    def _result_from_journal(
        self,
        inputs: RunInputs,
        resource_plan: Mapping[str, Any],
        *,
        appended: Sequence[str] = (),
        disclosures: Sequence[Mapping[str, Any]] = (),
    ) -> OrchestratorRunResult:
        run = self.journal.load_run(inputs.run_id)
        assert run is not None
        task_results: list[TaskRunResult] = []
        for row in self.journal.task_rows(inputs.run_id):
            output = json.loads(row["output_json"]) if row["output_json"] else None
            task_results.append(
                TaskRunResult(
                    task_id=row["task_id"],
                    status=row["status"],
                    resource_id=row["resource_id"],
                    output=output,
                    attempts=int(row["attempts"]),
                    error_code=row["error_code"],
                    requires_extra_review=bool(row["requires_extra_review"]),
                )
            )
        final_output = json.loads(run["result_json"]) if run["result_json"] else None
        return OrchestratorRunResult(
            run_id=inputs.run_id,
            session_id=inputs.session_id,
            status=run["status"],
            tasks=tuple(task_results),
            final_output=final_output,
            appended_message_ids=tuple(appended),
            resource_plan=deepcopy(dict(resource_plan)),
            disclosures=tuple(deepcopy(list(disclosures))),
            error_code=run["last_error_code"],
        )


def _validate_workflow(workflow: CompiledWorkflow) -> None:
    task_ids = [task.task_id for task in workflow.tasks]
    if len(task_ids) != len(set(task_ids)):
        raise WorkflowCompilationError("duplicate_task_id")
    known = set(task_ids)
    for task in workflow.tasks:
        if not CANONICAL_ID.fullmatch(task.task_id):
            raise WorkflowCompilationError("invalid_task_id")
        if task.task_id in task.depends_on:
            raise WorkflowCompilationError("task_self_dependency")
        if not set(task.depends_on).issubset(known):
            raise WorkflowCompilationError("unknown_task_dependency")
    visiting: set[str] = set()
    visited: set[str] = set()
    by_id = workflow.task_by_id()

    def visit(task_id: str) -> None:
        if task_id in visiting:
            raise WorkflowCompilationError("task_dependency_cycle")
        if task_id in visited:
            return
        visiting.add(task_id)
        for dependency in by_id[task_id].depends_on:
            visit(dependency)
        visiting.remove(task_id)
        visited.add(task_id)

    for task_id in task_ids:
        visit(task_id)


def _validate_team_can_cover(workflow: CompiledWorkflow, team_plan: Mapping[str, Any]) -> None:
    declared_roles = {item.get("role") for item in team_plan.get("assignments", [])}
    missing = sorted({task.role for task in workflow.tasks} - declared_roles)
    if missing:
        raise WorkflowCompilationError("team_plan_missing_workflow_role:" + ",".join(missing))


def _workflow_to_dict(workflow: CompiledWorkflow) -> dict[str, Any]:
    return {
        "tasks": [asdict(task) for task in workflow.tasks],
        "risk_level": workflow.risk_level,
        "error_cost": workflow.error_cost,
        "task_complexity": workflow.task_complexity,
    }


def _request_to_dict(request: ProviderRequest) -> dict[str, Any]:
    return {
        "run_id": request.run_id,
        "session_id": request.session_id,
        "task": asdict(request.task),
        "resource_id": request.resource_id,
        "idempotency_key": request.idempotency_key,
        "context": deepcopy(dict(request.context)),
        "prior_outputs": deepcopy(dict(request.prior_outputs)),
        "attempt": request.attempt,
    }


def _request_from_dict(value: Mapping[str, Any]) -> ProviderRequest:
    task_value = value["task"]
    task = CompiledTask(
        task_id=str(task_value["task_id"]),
        title=str(task_value["title"]),
        phase=str(task_value["phase"]),
        role=str(task_value["role"]),
        expected_output_type=str(task_value["expected_output_type"]),
        depends_on=tuple(task_value.get("depends_on", [])),
        deliverable_id=task_value.get("deliverable_id"),
        criterion_id=task_value.get("criterion_id"),
        required_context_fields=tuple(task_value.get("required_context_fields", [])),
        estimated_units=int(task_value.get("estimated_units", 1)),
        domains=tuple(task_value.get("domains", [])),
    )
    return ProviderRequest(
        run_id=str(value["run_id"]),
        session_id=str(value["session_id"]),
        task=task,
        resource_id=str(value["resource_id"]),
        idempotency_key=str(value["idempotency_key"]),
        context=deepcopy(dict(value.get("context", {}))),
        prior_outputs=deepcopy(dict(value.get("prior_outputs", {}))),
        attempt=int(value.get("attempt", 1)),
    )


def _response_to_dict(response: ProviderResponse) -> dict[str, Any]:
    return {
        "output_type": response.output_type,
        "payload": deepcopy(dict(response.payload)),
        "units_consumed": response.units_consumed,
        "context_fields_used": list(response.context_fields_used),
        "protocol_envelopes": [deepcopy(dict(item)) for item in response.protocol_envelopes],
        "action_requests": [deepcopy(dict(item)) for item in response.action_requests],
        "metadata": deepcopy(dict(response.metadata)),
    }


def _response_from_dict(value: Mapping[str, Any]) -> ProviderResponse:
    return ProviderResponse(
        output_type=str(value["output_type"]),
        payload=deepcopy(dict(value.get("payload", {}))),
        units_consumed=int(value.get("units_consumed", 1)),
        context_fields_used=tuple(value.get("context_fields_used", [])),
        protocol_envelopes=tuple(deepcopy(value.get("protocol_envelopes", []))),
        action_requests=tuple(deepcopy(value.get("action_requests", []))),
        metadata=deepcopy(dict(value.get("metadata", {}))),
    )


def _idempotency_key(run_id: str, task_id: str, resource_id: str, attempt: int) -> str:
    raw = f"w1-cip|{run_id}|{task_id}|{resource_id}|{attempt}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _routing_metadata_for_resource(
    plan: Mapping[str, Any],
    *,
    task: CompiledTask,
    workflow: CompiledWorkflow,
    inputs: RunInputs,
    resource_id: str,
) -> ResourceRoutingResult:
    routed = route_execution_resource(
        dict(plan),
        role=task.role,
        phase=task.phase,
        risk_level=workflow.risk_level,
        task_complexity=workflow.task_complexity,
        domains=set(task.domains) | set(inputs.domains),
        estimated_units=task.estimated_units,
    )
    if routed.selected_resource_id == resource_id:
        return routed

    domains = set(task.domains) | set(inputs.domains)
    for rule in plan.get("routing_rules", []):
        selector = rule.get("applies_to", {})
        if task.role not in set(selector.get("roles", [])):
            continue
        if task.phase not in set(selector.get("phases", [])):
            continue
        if selector.get("risk_levels") and workflow.risk_level not in selector["risk_levels"]:
            continue
        if selector.get("task_complexities") and workflow.task_complexity not in selector["task_complexities"]:
            continue
        required_domains = set(selector.get("domains", []))
        if required_domains and not required_domains.issubset(domains):
            continue
        candidate = next(
            (item for item in rule.get("candidates", []) if item.get("resource_id") == resource_id),
            None,
        )
        if candidate is None:
            continue
        primary = next(
            (item for item in rule.get("candidates", []) if item.get("mode") == "primary"),
            None,
        )
        used_fallback = candidate.get("mode") == "fallback"
        requires_review = bool(
            used_fallback
            and primary
            and candidate.get("fitness_score", 0) < primary.get("fitness_score", 0)
        ) or bool(rule.get("additional_review_required"))
        return ResourceRoutingResult(
            selected_resource_id=resource_id,
            action="selected",
            used_fallback=used_fallback,
            requires_extra_review=requires_review,
            disclosure_required=used_fallback,
            plan_update_required=False,
        )
    return ResourceRoutingResult(
        selected_resource_id=resource_id,
        action="selected",
        error_codes=("orchestrator_resumed_resource_not_in_routing_rule",),
    )


def _validate_response(
    task: CompiledTask,
    granted_context: Mapping[str, Any],
    response: ProviderResponse,
) -> str | None:
    if response.output_type != task.expected_output_type:
        return "orchestrator_output_type_mismatch"
    if response.units_consumed < 0:
        return "orchestrator_negative_units_consumed"
    if not set(response.context_fields_used).issubset(granted_context):
        return "orchestrator_undeclared_context_used"
    return None


def _resource_by_id(plan: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(item.get("resource_id")): item for item in plan.get("resources", [])}


def _plan_with_unavailable_resources(
    plan: Mapping[str, Any], unavailable: set[str]
) -> dict[str, Any]:
    value = deepcopy(dict(plan))
    for resource in value.get("resources", []):
        if resource.get("resource_id") in unavailable:
            resource.setdefault("quota", {})["status"] = "unknown"
    return value


def _mark_resource_quota_exhausted(
    plan: Mapping[str, Any], resource_id: str, reset_at: str | None
) -> dict[str, Any]:
    value = deepcopy(dict(plan))
    resources = _resource_by_id(value)
    resource = resources.get(resource_id)
    if resource is None:
        return value
    quota = resource.setdefault("quota", {})
    quota["status"] = "exhausted"
    quota["remaining_units"] = 0
    observed_at = utc_now()
    quota["observed_at"] = observed_at
    candidate_reset = reset_at if reset_at is not None else quota.get("reset_at")
    if isinstance(candidate_reset, str):
        try:
            parsed_reset = datetime.fromisoformat(candidate_reset.replace("Z", "+00:00"))
            parsed_observed = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
        except ValueError:
            parsed_reset = None
            parsed_observed = None
        if parsed_reset is not None and parsed_observed is not None and parsed_reset > parsed_observed:
            quota["reset_at"] = candidate_reset
        else:
            # Providers occasionally omit a new reset boundary while the prior
            # quota snapshot still contains an expired timestamp.  A stale
            # boundary is preserved only in the disclosure, never in the active
            # plan where it would invalidate safe fallback routing.
            quota.pop("reset_at", None)
            quota["reset_timestamp_stale"] = True
    elif reset_at is not None:
        quota.pop("reset_at", None)
        quota["reset_timestamp_stale"] = True
    quota["status_reason"] = "Provider reported quota exhaustion during orchestration."

    # Keep the persisted plan semantically usable.  An active rule may not point
    # at an exhausted resource, so advance that rule to the first declared usable
    # fallback or represent the absence of a safe route explicitly.
    for routing_rule in value.get("routing_rules", []):
        if routing_rule.get("active_resource_id") != resource_id:
            continue
        candidates = sorted(
            routing_rule.get("candidates", []), key=lambda item: item.get("priority", 10**9)
        )
        replacement = None
        for candidate in candidates:
            candidate_id = candidate.get("resource_id")
            if candidate_id == resource_id:
                continue
            candidate_resource = resources.get(candidate_id, {})
            candidate_quota = candidate_resource.get("quota", {})
            if candidate_quota.get("status") in {"exhausted", "unknown"}:
                continue
            remaining = candidate_quota.get("remaining_units")
            required = candidate.get("minimum_remaining_units", 0)
            if isinstance(remaining, int) and remaining < required:
                continue
            replacement = candidate
            break

        if replacement is None:
            routing_rule.pop("active_resource_id", None)
            routing_rule.pop("fallback_from_resource_id", None)
            routing_rule["routing_status"] = "awaiting_reset"
            routing_rule["activation_reason"] = "quota_exhausted"
            routing_rule["additional_review_required"] = False
            continue

        replacement_id = replacement.get("resource_id")
        primary = next(
            (item for item in candidates if item.get("mode") == "primary"), None
        )
        routing_rule["active_resource_id"] = replacement_id
        routing_rule["fallback_from_resource_id"] = resource_id
        routing_rule["routing_status"] = "active"
        routing_rule["activation_reason"] = "quota_exhausted"
        routing_rule["additional_review_required"] = bool(
            primary
            and replacement.get("fitness_score", 0) < primary.get("fitness_score", 0)
        )
    return value


def _consume_resource_units(
    plan: Mapping[str, Any], resource_id: str, units: int
) -> dict[str, Any]:
    value = deepcopy(dict(plan))
    resource = _resource_by_id(value).get(resource_id)
    if resource is None:
        return value
    quota = resource.get("quota", {})
    remaining = quota.get("remaining_units")
    if isinstance(remaining, int):
        remaining = max(0, remaining - max(0, units))
        quota["remaining_units"] = remaining
        if remaining == 0:
            quota["status"] = "exhausted"
        elif remaining <= quota.get("reserved_units", 0):
            quota["status"] = "low"
        else:
            quota["status"] = "available"
        observed_at = utc_now()
        quota["observed_at"] = observed_at
        reset_at = quota.get("reset_at")
        if isinstance(reset_at, str):
            try:
                parsed_reset = datetime.fromisoformat(reset_at.replace("Z", "+00:00"))
                parsed_observed = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
            except ValueError:
                parsed_reset = None
                parsed_observed = None
            if parsed_reset is None or parsed_observed is None or parsed_reset <= parsed_observed:
                # A quota observation taken after the advertised reset boundary
                # must not retain that old boundary.  Keeping it would make the
                # otherwise valid resource plan fail semantic validation on the
                # next task and incorrectly block fallback execution.
                quota.pop("reset_at", None)
                quota["reset_timestamp_stale"] = True
    return value


def _depends_transitively(
    workflow: CompiledWorkflow, task_id: str, target_id: str
) -> bool:
    by_id = workflow.task_by_id()
    seen: set[str] = set()

    def visit(current: str) -> bool:
        if current in seen:
            return False
        seen.add(current)
        task = by_id[current]
        if target_id in task.depends_on:
            return True
        return any(visit(dep) for dep in task.depends_on)

    return visit(task_id)


def _enforce_extra_review(
    workflow: CompiledWorkflow, rows: Mapping[str, Mapping[str, Any]]
) -> None:
    for task_id, row in rows.items():
        if not row.get("requires_extra_review"):
            continue
        covered = any(
            task.phase == "review"
            and rows.get(task.task_id, {}).get("status") == "completed"
            and _depends_transitively(workflow, task.task_id, task_id)
            for task in workflow.tasks
        )
        if not covered:
            raise OutputContractError("orchestrator_required_extra_review_missing")
