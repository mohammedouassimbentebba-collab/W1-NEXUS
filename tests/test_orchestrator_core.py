from __future__ import annotations

import json
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from test_protocol_event_schema import ProtocolEventSchemaTests  # noqa: E402
from w1cip.orchestrator import (  # noqa: E402
    ArtifactEnvelopeFactory,
    CompiledTask,
    CompiledWorkflow,
    GoalTaskCompiler,
    OrchestratorCore,
    OrchestratorJournal,
    ProviderOutcomeUncertain,
    ProviderQuotaExhausted,
    ProviderRequest,
    ProviderResponse,
    RunInputs,
    ScriptedProvider,
    TeamPlanner,
    WorkflowCompilationError,
)
from w1cip.orchestrator import _idempotency_key  # noqa: E402
from w1cip.provider_connectors import ProviderRateLimited  # noqa: E402
from w1cip.session_store import SessionStore  # noqa: E402

TRUSTED = {("runtime", "runtime-local-001")}


def goal_fixture() -> dict:
    return {
        "title": "Select a compatible driver",
        "objective": "Select a candidate and disclose verification.",
        "deliverables": [
            {
                "deliverable_id": "driver-decision",
                "description": "Decision",
                "kind": "decision",
                "required": True,
                "decision_subject": "driver-selection",
            }
        ],
        "constraints": [
            {
                "constraint_id": "startup-current",
                "statement": "Cover startup current.",
                "criticality": "critical",
                "verifiability": "deterministic",
            }
        ],
        "success_criteria": [
            {
                "criterion_id": "candidate-compatible",
                "statement": "Candidate passes.",
                "applies_to": ["driver-decision"],
                "verifiability": "deterministic",
                "acceptance_effect": "required_for_completion",
            }
        ],
        "risk_policy": {
            "risk_level": "medium",
            "error_cost": "material",
            "autonomy_mode": "human_approval_required",
        },
        "decision_policy": [
            {
                "decision_subject": "driver-selection",
                "human_approval_required": True,
            }
        ],
        "status": "active",
    }


def team_fixture(max_model_calls: int = 20) -> dict:
    roles = [
        "orchestrator",
        "executor",
        "verifier",
        "reviewer",
        "decision_authority",
        "synthesizer",
    ]
    return {
        "budget": {"max_model_calls": max_model_calls, "max_review_cycles": 2},
        "assignments": [
            {
                "slot_id": f"{role}-primary",
                "role": role,
                "role_assignment": {
                    "entity_type": "role_assignment",
                    "entity_id": f"role-{role}",
                    "entity_version": 1,
                },
            }
            for role in roles
        ],
    }


def resource(resource_id: str, remaining: int = 20) -> dict:
    return {
        "resource_id": resource_id,
        "role_assignment": {
            "entity_type": "role_assignment",
            "entity_id": f"role-{resource_id}",
            "entity_version": 1,
        },
        "agent_card": {
            "entity_type": "agent_card",
            "entity_id": f"agent-{resource_id}",
            "entity_version": 1,
        },
        "quota": {
            "accounting_scope": "user_account",
            "meter": "requests",
            "window": "day",
            "status": "available",
            "limit_units": 50,
            "remaining_units": remaining,
            "reserved_units": 0,
            "observed_at": "2026-08-05T15:00:00Z",
            "reset_at": "2026-08-06T15:00:00Z",
            "source": "local_meter",
        },
    }


def rule(
    rule_id: str,
    *,
    role: str,
    phase: str,
    primary: str,
    fallback: str | None = None,
    primary_score: int = 95,
    fallback_score: int = 90,
) -> dict:
    candidates = [
        {
            "resource_id": primary,
            "mode": "primary",
            "priority": 1,
            "fitness_score": primary_score,
            "assessment_source": "human_assessment",
            "assessment_confidence": "medium",
            "assessment_basis": "Fixture-local score.",
            "minimum_remaining_units": 1,
        }
    ]
    if fallback:
        candidates.append(
            {
                "resource_id": fallback,
                "mode": "fallback",
                "priority": 2,
                "fitness_score": fallback_score,
                "assessment_source": "human_assessment",
                "assessment_confidence": "medium",
                "assessment_basis": "Fixture-local fallback score.",
                "minimum_remaining_units": 1,
            }
        )
    return {
        "rule_id": rule_id,
        "applies_to": {
            "roles": [role],
            "phases": [phase],
            "risk_levels": ["medium"],
            "task_complexities": ["bounded"],
            "domains": [],
        },
        "selection_objective": "balanced",
        "minimum_fitness_score": 80,
        "active_resource_id": primary,
        "activation_reason": "initial_selection",
        "candidates": candidates,
        "on_primary_unavailable": "activate_fallback" if fallback else "block",
        "below_floor_policy": "allow_with_extra_review",
        "additional_review_required": False,
        "routing_status": "active",
    }


def full_resource_plan() -> dict:
    resources = [
        resource("opus-resource"),
        resource("sol-resource"),
        resource("verifier-resource"),
        resource("reviewer-resource"),
        resource("decision-resource"),
        resource("synth-resource"),
    ]
    return {
        "resources": resources,
        "routing_rules": [
            rule(
                "executor-route",
                role="executor",
                phase="execution",
                primary="opus-resource",
                fallback="sol-resource",
            ),
            rule(
                "verifier-route",
                role="verifier",
                phase="verification",
                primary="verifier-resource",
            ),
            rule(
                "reviewer-route",
                role="reviewer",
                phase="review",
                primary="reviewer-resource",
            ),
            rule(
                "decision-route",
                role="decision_authority",
                phase="approval",
                primary="decision-resource",
            ),
            rule(
                "synthesis-route",
                role="synthesizer",
                phase="synthesis",
                primary="synth-resource",
            ),
        ],
        "reserve_policy": {"protected_phases": ["review", "verification", "approval", "synthesis"]},
        "heterogeneity_policy": {
            "model_count_voting": "prohibited",
            "decision_basis": "authority_evidence_verification",
            "fitness_scope": "task_specific",
            "silent_downgrade": "prohibited",
        },
        "fallback_policy": {
            "all_candidates_unavailable": "await_reset",
            "cross_provider_allowed": True,
            "disclosure_required": True,
            "preserve_artifacts": True,
        },
        "status": "active",
    }


def output(output_type: str, name: str, *, fields: tuple[str, ...] = ()) -> ProviderResponse:
    return ProviderResponse(
        output_type=output_type,
        payload={"name": name},
        context_fields_used=fields,
        units_consumed=1,
    )


def seed_store(store: SessionStore) -> str:
    first, second, *_ = deepcopy(ProtocolEventSchemaTests.minimal_replay_log())
    first.pop("sequence", None)
    second.pop("sequence", None)
    store.append(first)
    store.append(second)
    return first["session_id"]


class OrchestratorCoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / "w1cip.sqlite3"
        self.journal_database = Path(self.temp.name) / "orchestrator.sqlite3"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_goal_compiler_builds_ordered_multi_role_workflow(self) -> None:
        compiler = GoalTaskCompiler(
            context_fields_by_deliverable={"driver-decision": ["supply-voltage"]},
            domains=["electronics"],
        )
        workflow = compiler.compile(goal_fixture(), team_fixture())
        phases = [task.phase for task in workflow.tasks]
        self.assertEqual(
            ["execution", "verification", "verification", "review", "approval", "approval", "synthesis"],
            phases,
        )
        self.assertEqual(("supply-voltage",), workflow.tasks[0].required_context_fields)
        self.assertIn("review-solution-package", workflow.task_by_id()["decide-driver-decision"].depends_on)
        self.assertEqual(("approve-final-decisions",), workflow.task_by_id()["synthesize-final-result"].depends_on)

    def test_team_planner_reports_missing_independent_roles(self) -> None:
        planner = TeamPlanner()
        draft = planner.build(
            goal_fixture(),
            [
                {"role": "orchestrator", "role_assignment": {"entity_id": "r-o"}},
                {"role": "executor", "role_assignment": {"entity_id": "r-e"}},
            ],
        )
        self.assertIn("verifier", draft.missing_roles)
        self.assertIn("reviewer", draft.missing_roles)
        self.assertIn("decision_authority", draft.missing_roles)
        self.assertIn("synthesizer", draft.missing_roles)

    def test_end_to_end_routes_after_quota_exhaustion_and_persists_trace(self) -> None:
        with SessionStore(self.database, trusted_recorders=TRUSTED) as store, OrchestratorJournal(
            self.journal_database
        ) as journal:
            session_id = seed_store(store)
            factory = ArtifactEnvelopeFactory(
                actor={"principal_type": "human", "principal_id": "human-owner-001"},
                authorized_by={
                    "entity_type": "role_assignment",
                    "entity_id": "owner",
                    "entity_version": 2,
                },
                recorded_by={"principal_type": "runtime", "principal_id": "runtime-local-001"},
                correlation_id="corr-orchestrator",
            )
            providers = {
                "opus-resource": ScriptedProvider(
                    "opus-resource", [ProviderQuotaExhausted("2026-08-06T00:00:00Z")]
                ),
                "sol-resource": ScriptedProvider(
                    "sol-resource", [output("contribution", "proposal", fields=("supply-voltage",))]
                ),
                "verifier-resource": ScriptedProvider(
                    "verifier-resource",
                    [output("evidence", "evidence"), output("verification", "verification")],
                ),
                "reviewer-resource": ScriptedProvider(
                    "reviewer-resource", [output("review", "approved-review")]
                ),
                "decision-resource": ScriptedProvider(
                    "decision-resource", [output("decision", "decision"), output("approval", "approval")]
                ),
                "synth-resource": ScriptedProvider(
                    "synth-resource", [output("final_result", "final-result")]
                ),
            }
            core = OrchestratorCore(
                session_store=store,
                journal=journal,
                providers=providers,
                envelope_factory=factory,
                compiler=GoalTaskCompiler(
                    context_fields_by_deliverable={"driver-decision": ["supply-voltage"]},
                    domains=["electronics"],
                ),
            )
            result = core.run(
                RunInputs(
                    run_id="pump-run-001",
                    session_id=session_id,
                    goal=goal_fixture(),
                    team_plan=team_fixture(),
                    resource_plan=full_resource_plan(),
                    context={"supply-voltage": 12, "secret-note": "must-not-leak"},
                    domains=frozenset({"electronics"}),
                )
            )
            self.assertEqual("completed", result.status)
            self.assertEqual({"name": "final-result"}, result.final_output)
            first_task = next(task for task in result.tasks if task.task_id == "execute-driver-decision")
            self.assertEqual("sol-resource", first_task.resource_id)
            self.assertTrue(first_task.requires_extra_review)
            self.assertEqual({"supply-voltage": 12}, providers["sol-resource"].calls[0].context)
            self.assertGreater(len(result.appended_message_ids), 5)
            self.assertTrue(store.verify_integrity(session_id).valid)
            opus = next(item for item in result.resource_plan["resources"] if item["resource_id"] == "opus-resource")
            self.assertEqual("exhausted", opus["quota"]["status"])
            self.assertTrue(any(item.get("reason") == "quota_exhausted" for item in result.disclosures))

            calls_before = sum(len(provider.calls) for provider in providers.values())
            resumed = core.run(
                RunInputs(
                    run_id="pump-run-001",
                    session_id=session_id,
                    goal=goal_fixture(),
                    team_plan=team_fixture(),
                    resource_plan=full_resource_plan(),
                    context={"supply-voltage": 12},
                    domains=frozenset({"electronics"}),
                )
            )
            self.assertEqual("completed", resumed.status)
            self.assertEqual(calls_before, sum(len(provider.calls) for provider in providers.values()))

    def test_all_resources_exhausted_enters_awaiting_reset(self) -> None:
        workflow = CompiledWorkflow(
            tasks=(
                CompiledTask(
                    task_id="execute-one",
                    title="Execute one",
                    phase="execution",
                    role="executor",
                    expected_output_type="contribution",
                ),
            ),
            risk_level="medium",
            error_cost="material",
            task_complexity="bounded",
        )
        plan = {
            "resources": [resource("primary"), resource("fallback")],
            "routing_rules": [
                rule(
                    "one-route",
                    role="executor",
                    phase="execution",
                    primary="primary",
                    fallback="fallback",
                )
            ],
            "reserve_policy": {"protected_phases": []},
            "heterogeneity_policy": {
                "model_count_voting": "prohibited",
                "decision_basis": "authority_evidence_verification",
                "fitness_scope": "task_specific",
                "silent_downgrade": "prohibited",
            },
            "fallback_policy": {
                "all_candidates_unavailable": "await_reset",
                "cross_provider_allowed": True,
                "disclosure_required": True,
                "preserve_artifacts": True,
            },
            "status": "active",
        }
        with SessionStore(self.database, trusted_recorders=TRUSTED) as store, OrchestratorJournal(
            self.journal_database
        ) as journal:
            session_id = seed_store(store)
            core = OrchestratorCore(
                session_store=store,
                journal=journal,
                providers={
                    "primary": ScriptedProvider("primary", [ProviderQuotaExhausted()]),
                    "fallback": ScriptedProvider("fallback", [ProviderQuotaExhausted()]),
                },
            )
            result = core.run(
                RunInputs(
                    run_id="quota-run-001",
                    session_id=session_id,
                    goal=goal_fixture(),
                    team_plan=team_fixture(),
                    resource_plan=plan,
                ),
                workflow=workflow,
            )
            self.assertEqual("awaiting_reset", result.status)
            self.assertEqual("execution_resource_all_candidates_unavailable", result.error_code)

            # A quota reset is a resumable pause, not a terminal run. Completed
            # work would be preserved; this pending task is returned to pending.
            resumed_core = OrchestratorCore(
                session_store=store,
                journal=journal,
                providers={
                    "primary": ScriptedProvider(
                        "primary", [output("contribution", "after-reset")]
                    ),
                    "fallback": ScriptedProvider(
                        "fallback", [output("contribution", "unused")]
                    ),
                },
            )
            resumed = resumed_core.run(
                RunInputs(
                    run_id="quota-run-001",
                    session_id=session_id,
                    goal=goal_fixture(),
                    team_plan=team_fixture(),
                    resource_plan=plan,
                ),
                workflow=workflow,
            )
            self.assertEqual("completed", resumed.status)
            self.assertEqual({"name": "after-reset"}, resumed.tasks[0].output)

    def test_resume_reuses_original_pending_call_idempotency_key(self) -> None:
        workflow = CompiledWorkflow(
            tasks=(
                CompiledTask(
                    task_id="execute-one",
                    title="Execute one",
                    phase="execution",
                    role="executor",
                    expected_output_type="contribution",
                ),
            ),
            risk_level="medium",
            error_cost="material",
            task_complexity="bounded",
        )
        plan = {
            "resources": [resource("primary")],
            "routing_rules": [
                rule("one-route", role="executor", phase="execution", primary="primary")
            ],
            "reserve_policy": {"protected_phases": []},
            "heterogeneity_policy": {
                "model_count_voting": "prohibited",
                "decision_basis": "authority_evidence_verification",
                "fitness_scope": "task_specific",
                "silent_downgrade": "prohibited",
            },
            "fallback_policy": {
                "all_candidates_unavailable": "await_user",
                "cross_provider_allowed": True,
                "disclosure_required": True,
                "preserve_artifacts": True,
            },
            "status": "active",
        }
        with SessionStore(self.database, trusted_recorders=TRUSTED) as store, OrchestratorJournal(
            self.journal_database
        ) as journal:
            session_id = seed_store(store)
            inputs = RunInputs(
                run_id="resume-run-001",
                session_id=session_id,
                goal=goal_fixture(),
                team_plan=team_fixture(max_model_calls=1),
                resource_plan=plan,
            )
            journal.initialize_run(inputs, workflow, plan)
            key = _idempotency_key(inputs.run_id, "execute-one", "primary", 1)
            pending = ProviderRequest(
                run_id=inputs.run_id,
                session_id=session_id,
                task=workflow.tasks[0],
                resource_id="primary",
                idempotency_key=key,
                context={},
                prior_outputs={},
                attempt=1,
            )
            journal.update_task(
                inputs.run_id,
                "execute-one",
                status="running",
                attempts=1,
                resource_id="primary",
            )
            journal.start_call("call-resume-pending", pending)
            provider = ScriptedProvider(
                "primary", [output("contribution", "recovered")], supports_idempotency=True
            )
            core = OrchestratorCore(
                session_store=store,
                journal=journal,
                providers={"primary": provider},
            )
            result = core.run(inputs, workflow=workflow)
            self.assertEqual("completed", result.status)
            self.assertEqual(1, len(provider.calls))
            self.assertEqual(key, provider.calls[0].idempotency_key)
            self.assertEqual(1, provider.calls[0].attempt)

    def test_non_idempotent_uncertain_call_requires_reconciliation(self) -> None:
        workflow = CompiledWorkflow(
            tasks=(
                CompiledTask(
                    task_id="execute-one",
                    title="Execute one",
                    phase="execution",
                    role="executor",
                    expected_output_type="contribution",
                ),
            ),
            risk_level="medium",
            error_cost="material",
            task_complexity="bounded",
        )
        plan = {
            "resources": [resource("primary")],
            "routing_rules": [
                rule("one-route", role="executor", phase="execution", primary="primary")
            ],
            "reserve_policy": {"protected_phases": []},
            "heterogeneity_policy": {
                "model_count_voting": "prohibited",
                "decision_basis": "authority_evidence_verification",
                "fitness_scope": "task_specific",
                "silent_downgrade": "prohibited",
            },
            "fallback_policy": {
                "all_candidates_unavailable": "await_user",
                "cross_provider_allowed": True,
                "disclosure_required": True,
                "preserve_artifacts": True,
            },
            "status": "active",
        }
        with SessionStore(self.database, trusted_recorders=TRUSTED) as store, OrchestratorJournal(
            self.journal_database
        ) as journal:
            session_id = seed_store(store)
            provider = ScriptedProvider(
                "primary", [ProviderOutcomeUncertain()], supports_idempotency=False
            )
            core = OrchestratorCore(
                session_store=store,
                journal=journal,
                providers={"primary": provider},
            )
            result = core.run(
                RunInputs(
                    run_id="uncertain-run-001",
                    session_id=session_id,
                    goal=goal_fixture(),
                    team_plan=team_fixture(),
                    resource_plan=plan,
                ),
                workflow=workflow,
            )
            self.assertEqual("awaiting_reconciliation", result.status)
            self.assertEqual("orchestrator_unsafe_provider_resume", result.error_code)

    def test_model_call_budget_blocks_before_excess_call(self) -> None:
        workflow = CompiledWorkflow(
            tasks=(
                CompiledTask("one", "One", "execution", "executor", "contribution"),
                CompiledTask("two", "Two", "execution", "executor", "contribution", depends_on=("one",)),
            ),
            risk_level="medium",
            error_cost="material",
            task_complexity="bounded",
        )
        plan = {
            "resources": [resource("primary")],
            "routing_rules": [rule("route", role="executor", phase="execution", primary="primary")],
            "reserve_policy": {"protected_phases": []},
            "heterogeneity_policy": {
                "model_count_voting": "prohibited",
                "decision_basis": "authority_evidence_verification",
                "fitness_scope": "task_specific",
                "silent_downgrade": "prohibited",
            },
            "fallback_policy": {
                "all_candidates_unavailable": "await_user",
                "cross_provider_allowed": True,
                "disclosure_required": True,
                "preserve_artifacts": True,
            },
            "status": "active",
        }
        with SessionStore(self.database, trusted_recorders=TRUSTED) as store, OrchestratorJournal(
            self.journal_database
        ) as journal:
            session_id = seed_store(store)
            provider = ScriptedProvider(
                "primary", [output("contribution", "one"), output("contribution", "two")]
            )
            core = OrchestratorCore(
                session_store=store,
                journal=journal,
                providers={"primary": provider},
            )
            result = core.run(
                RunInputs(
                    run_id="budget-run-001",
                    session_id=session_id,
                    goal=goal_fixture(),
                    team_plan=team_fixture(max_model_calls=1),
                    resource_plan=plan,
                ),
                workflow=workflow,
            )
            self.assertEqual("blocked", result.status)
            self.assertEqual("orchestrator_model_call_budget_exhausted", result.error_code)
            self.assertEqual(1, len(provider.calls))

    def test_compile_rejects_team_without_required_role(self) -> None:
        broken = team_fixture()
        broken["assignments"] = [
            item for item in broken["assignments"] if item["role"] != "reviewer"
        ]
        with self.assertRaises(WorkflowCompilationError) as caught:
            GoalTaskCompiler().compile(goal_fixture(), broken)
        self.assertIn("team_plan_missing_workflow_role", str(caught.exception))


    def test_rate_limited_primary_switches_to_fallback_without_repeating_primary(self) -> None:
        workflow = CompiledWorkflow(
            tasks=(
                CompiledTask(
                    task_id="execute-one",
                    title="Execute one",
                    phase="execution",
                    role="executor",
                    expected_output_type="contribution",
                ),
                CompiledTask(
                    task_id="review-one",
                    title="Review fallback output",
                    phase="review",
                    role="reviewer",
                    expected_output_type="review",
                    depends_on=("execute-one",),
                ),
            ),
            risk_level="medium",
            error_cost="material",
            task_complexity="bounded",
        )
        plan = {
            "resources": [
                resource("primary"),
                resource("fallback"),
                resource("reviewer"),
            ],
            "routing_rules": [
                rule(
                    "one-route",
                    role="executor",
                    phase="execution",
                    primary="primary",
                    fallback="fallback",
                ),
                rule(
                    "review-route",
                    role="reviewer",
                    phase="review",
                    primary="reviewer",
                ),
            ],
            "reserve_policy": {"protected_phases": []},
            "heterogeneity_policy": {
                "model_count_voting": "prohibited",
                "decision_basis": "authority_evidence_verification",
                "fitness_scope": "task_specific",
                "silent_downgrade": "prohibited",
            },
            "fallback_policy": {
                "all_candidates_unavailable": "await_reset",
                "cross_provider_allowed": True,
                "disclosure_required": True,
                "preserve_artifacts": True,
            },
            "status": "active",
        }
        primary = ScriptedProvider("primary", [ProviderRateLimited(3.0)])
        fallback = ScriptedProvider(
            "fallback", [output("contribution", "fallback-result")]
        )
        reviewer = ScriptedProvider(
            "reviewer", [output("review", "fallback-review")]
        )
        with SessionStore(self.database, trusted_recorders=TRUSTED) as store, OrchestratorJournal(
            self.journal_database
        ) as journal:
            session_id = seed_store(store)
            core = OrchestratorCore(
                session_store=store,
                journal=journal,
                providers={
                    "primary": primary,
                    "fallback": fallback,
                    "reviewer": reviewer,
                },
            )
            result = core.run(
                RunInputs(
                    run_id="rate-limit-run-001",
                    session_id=session_id,
                    goal=goal_fixture(),
                    team_plan=team_fixture(),
                    resource_plan=plan,
                    max_attempts_per_resource=3,
                ),
                workflow=workflow,
            )
            self.assertEqual("completed", result.status)
            self.assertEqual(1, len(primary.calls))
            self.assertEqual(1, len(fallback.calls))
            self.assertEqual(1, len(reviewer.calls))
            self.assertTrue(
                any(item.get("reason") == "rate_limited" for item in result.disclosures)
            )

    def test_consumed_resource_discards_reset_boundary_that_is_now_stale(self) -> None:
        from w1cip.orchestrator import _consume_resource_units
        from w1cip.validation import validate_execution_resource_plan_semantics

        plan = full_resource_plan()
        target = next(item for item in plan["resources"] if item["resource_id"] == "sol-resource")
        target["quota"]["reset_at"] = "2020-01-01T00:00:00Z"
        updated = _consume_resource_units(plan, "sol-resource", 1)
        updated_target = next(item for item in updated["resources"] if item["resource_id"] == "sol-resource")
        self.assertNotIn("reset_at", updated_target["quota"])
        self.assertTrue(updated_target["quota"]["reset_timestamp_stale"])
        self.assertNotIn("execution_resource_reset_not_after_observation", validate_execution_resource_plan_semantics(updated))

    def test_journal_persists_provider_quota_observation(self) -> None:
        with OrchestratorJournal(self.journal_database) as journal:
            inputs = RunInputs(
                run_id="quota-observation-run",
                session_id="session-provider-001",
                goal=goal_fixture(),
                team_plan=team_fixture(),
                resource_plan=full_resource_plan(),
            )
            workflow = CompiledWorkflow(
                tasks=(
                    CompiledTask(
                        task_id="execute-one",
                        title="Execute one",
                        phase="execution",
                        role="executor",
                        expected_output_type="contribution",
                    ),
                ),
                risk_level="medium",
                error_cost="material",
                task_complexity="bounded",
            )
            journal.initialize_run(inputs, workflow, inputs.resource_plan)
            observation = {
                "provider": "openai",
                "observed_at": "2026-08-05T17:00:00Z",
                "request_remaining": 99,
                "source": "response_headers",
            }
            journal.record_quota_observation(
                inputs.run_id, "execute-one", "opus-resource", observation
            )
            rows = journal.quota_observations(inputs.run_id)
            self.assertEqual(1, len(rows))
            self.assertEqual(99, rows[0]["observation"]["request_remaining"])
            self.assertEqual("opus-resource", rows[0]["resource_id"])


if __name__ == "__main__":
    unittest.main()


class OrchestratorActionRuntimeIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = SessionStore(self.root / "session.sqlite3", trusted_recorders=TRUSTED)
        self.journal = OrchestratorJournal(self.root / "orchestrator.sqlite3")
        from w1cip.action_runtime import ActionRuntime

        self.action_runtime = ActionRuntime(self.root)
        self.workflow = CompiledWorkflow(
            tasks=(
                CompiledTask(
                    task_id="execute-action-task",
                    title="Execute a governed action",
                    phase="execution",
                    role="executor",
                    expected_output_type="contribution",
                ),
            ),
            risk_level="medium",
            error_cost="material",
            task_complexity="bounded",
        )
        self.inputs = RunInputs(
            run_id="run-action-integration",
            session_id="session-action-integration",
            goal={},
            team_plan={"budget": {"max_model_calls": 5}},
            resource_plan=full_resource_plan(),
            context={},
        )

    def tearDown(self) -> None:
        self.action_runtime.close()
        self.journal.close()
        self.store.close()
        self.temp.cleanup()

    def test_safe_reversible_action_executes_and_is_attached_to_task_output(self) -> None:
        provider = ScriptedProvider(
            "opus-resource",
            [
                ProviderResponse(
                    output_type="contribution",
                    payload={"message": "file prepared"},
                    action_requests=(
                        {
                            "action_id": "write-agent-output",
                            "kind": "file.write",
                            "parameters": {"path": "agent-output.txt", "content": "hello"},
                        },
                    ),
                )
            ],
        )
        core = OrchestratorCore(
            session_store=self.store,
            journal=self.journal,
            providers={"opus-resource": provider},
            action_runtime=self.action_runtime,
        )
        result = core.run(self.inputs, workflow=self.workflow)
        self.assertEqual("completed", result.status)
        self.assertEqual("hello", (self.root / "agent-output.txt").read_text(encoding="utf-8"))
        task_output = result.tasks[0].output
        self.assertEqual("write-agent-output", task_output["_w1_action_results"][0]["action_id"])

    def test_sensitive_action_pauses_for_user_then_resumes_without_new_provider_call(self) -> None:
        target = self.root / "remove-after-approval.txt"
        target.write_text("sensitive", encoding="utf-8")
        action_item = {
            "action_id": "delete-after-human-approval",
            "kind": "file.delete",
            "parameters": {"path": "remove-after-approval.txt"},
        }
        provider = ScriptedProvider(
            "opus-resource",
            [
                ProviderResponse(
                    output_type="contribution",
                    payload={"message": "delete requested"},
                    action_requests=(action_item,),
                )
            ],
        )
        core = OrchestratorCore(
            session_store=self.store,
            journal=self.journal,
            providers={"opus-resource": provider},
            action_runtime=self.action_runtime,
        )
        first = core.run(self.inputs, workflow=self.workflow)
        self.assertEqual("awaiting_user", first.status)
        self.assertEqual("action_approval_required", first.error_code)
        self.assertEqual(1, len(provider.calls))
        self.assertTrue(target.exists())

        from w1cip.action_runtime import ActionRequest

        action_request = ActionRequest(
            action_id=action_item["action_id"],
            kind=action_item["kind"],
            parameters=action_item["parameters"],
            requested_by="opus-resource",
        )
        plan = self.action_runtime.plan(action_request)
        approval = self.action_runtime.issue_approval(plan, issued_by="human-owner")
        self.action_runtime.execute(action_request, approval=approval)
        self.assertFalse(target.exists())

        second = core.run(self.inputs, workflow=self.workflow)
        self.assertEqual("completed", second.status)
        self.assertEqual(1, len(provider.calls), "completed provider response must be replayed from journal")
        self.assertEqual(
            "delete-after-human-approval",
            second.tasks[0].output["_w1_action_results"][0]["action_id"],
        )
