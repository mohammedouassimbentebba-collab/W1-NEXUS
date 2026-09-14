from __future__ import annotations

import json
import sys
import unittest
from copy import deepcopy
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from w1cip.validation import (  # noqa: E402
    AgentCardState,
    GoalContractState,
    RoleAssignmentAuthorityState,
    TaskState,
    TeamPlanState,
    validate_envelope_role_assignment_authority,
    validate_context_grant_against_task,
    validate_protocol_envelope_semantics,
    validate_role_assignment_semantics,
    validate_task_against_goal_team_and_assignment,
    validate_task_budget_pool,
    validate_task_graph_and_dependencies,
    validate_task_semantics,
    validate_task_transition,
    validate_task_update_authority,
)

SCHEMA_DIR = ROOT / "schemas" / "w1-cip" / "0.1"
TASK_PATH = SCHEMA_DIR / "task.schema.json"
VERSIONED_ENTITY_PATH = SCHEMA_DIR / "versioned-entity.schema.json"
SCHEMA_FILES = [
    SCHEMA_DIR / "entity-ref.schema.json",
    SCHEMA_DIR / "principal-ref.schema.json",
    SCHEMA_DIR / "role-assignment.schema.json",
    SCHEMA_DIR / "agent-card.schema.json",
    SCHEMA_DIR / "goal-contract.schema.json",
    SCHEMA_DIR / "team-plan.schema.json",
    SCHEMA_DIR / "context-grant.schema.json",
    TASK_PATH,
]
EXAMPLES = ROOT / "examples" / "task"
VERSIONED = ROOT / "examples" / "versioned-entity" / "valid"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class TaskSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = load_json(TASK_PATH)
        Draft202012Validator.check_schema(cls.schema)
        registry = Registry()
        for path in SCHEMA_FILES:
            schema = load_json(path)
            Draft202012Validator.check_schema(schema)
            registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))
        cls.validator = Draft202012Validator(cls.schema, registry=registry, format_checker=FormatChecker())

    def structural_errors(self, instance: dict) -> list:
        return sorted(self.validator.iter_errors(instance), key=lambda error: list(error.path))

    def test_valid_examples(self) -> None:
        paths = sorted((EXAMPLES / "valid").glob("*.json"))
        self.assertTrue(paths)
        for path in paths:
            with self.subTest(path=path.name):
                payload = load_json(path)
                self.assertEqual([], self.structural_errors(payload))
                self.assertEqual([], validate_task_semantics(payload))

    def test_structurally_invalid_examples(self) -> None:
        paths = sorted((EXAMPLES / "invalid-structural").glob("*.json"))
        self.assertTrue(paths)
        for path in paths:
            with self.subTest(path=path.name):
                self.assertTrue(self.structural_errors(load_json(path)), path.name)

    def test_semantically_invalid_examples(self) -> None:
        directory = EXAMPLES / "invalid-semantic"
        manifest = load_json(directory / "manifest.json")
        for filename, expected in manifest.items():
            with self.subTest(path=filename):
                payload = load_json(directory / filename)
                self.assertEqual([], self.structural_errors(payload))
                self.assertEqual(expected, validate_task_semantics(payload))

    def test_versioned_entity_resolves_task_schema(self) -> None:
        root_schema = load_json(VERSIONED_ENTITY_PATH)
        registry = Registry()
        for path in SCHEMA_FILES:
            schema = load_json(path)
            registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))
        validator = Draft202012Validator(root_schema, registry=registry, format_checker=FormatChecker())
        record = load_json(VERSIONED / "task-driver-selection-v1.json")
        self.assertEqual([], list(validator.iter_errors(record)))
        wrong = deepcopy(record)
        wrong["entity"]["entity_type"] = "claim"
        self.assertTrue(list(validator.iter_errors(wrong)))

    def test_protocol_envelope_runs_task_semantics(self) -> None:
        envelope = load_json(ROOT / "examples" / "protocol-envelope" / "valid" / "task-create.json")
        self.assertEqual([], validate_protocol_envelope_semantics(envelope))
        invalid = deepcopy(envelope)
        invalid["payload"]["legal_payload"]["required_outputs"].append(
            deepcopy(invalid["payload"]["legal_payload"]["required_outputs"][0])
        )
        self.assertIn("duplicate_task_output_id", validate_protocol_envelope_semantics(invalid))
        invalid_status = deepcopy(envelope)
        invalid_status["payload"]["legal_payload"]["status"] = "ready"
        self.assertIn("task_initial_status_not_created", validate_protocol_envelope_semantics(invalid_status))

    def test_valid_lifecycle_and_fixed_phase(self) -> None:
        records = [load_json(VERSIONED / f"task-driver-selection-v{i}.json") for i in range(1, 6)]
        self.assertEqual([], validate_task_transition(records[0], None))
        for previous, current in zip(records, records[1:]):
            self.assertEqual([], validate_task_transition(current, previous))
        changed_phase = deepcopy(records[1])
        changed_phase["legal_payload"]["phase"] = "review"
        self.assertIn("task_phase_changed", validate_task_transition(changed_phase, records[0]))

    def test_invalid_status_jump_and_terminal_finality(self) -> None:
        v1 = load_json(VERSIONED / "task-driver-selection-v1.json")
        v4 = load_json(VERSIONED / "task-driver-selection-v5.json")
        jumped = deepcopy(v4)
        jumped["entity"]["entity_version"] = 2
        jumped["previous_version"]["entity_version"] = 1
        self.assertIn("task_status_transition_not_allowed", validate_task_transition(jumped, v1))
        v5 = deepcopy(v4)
        v5["entity"]["entity_version"] = 6
        v5["created_by_event_id"] = "evt-task-after-complete-005"
        v5["previous_version"]["entity_version"] = 5
        v5["legal_payload"]["description"] += " Changed."
        self.assertIn("task_terminal_status_final", validate_task_transition(v5, v4))

    def build_basis_states(self):
        goal = load_json(VERSIONED / "goal-contract-pump-v1.json")
        team = load_json(VERSIONED / "team-plan-pump-v1.json")
        executor = load_json(VERSIONED / "role-assignment-executor-v1.json")
        card = load_json(VERSIONED / "agent-card-electronics-v1.json")
        return (
            GoalContractState(contracts={("goal_contract", "goal-pump-driver-001", 1): goal}, latest_versions={("goal_contract", "goal-pump-driver-001"): 1}),
            TeamPlanState(plans={("team_plan", "team-plan-pump-001", 1): team}, latest_versions={("team_plan", "team-plan-pump-001"): 1}),
            RoleAssignmentAuthorityState(assignments={("role_assignment", "role-assignment-executor-001", 1): executor}, latest_versions={("role_assignment", "role-assignment-executor-001"): 1}),
            AgentCardState(cards={("agent_card", "agent-electronics-01", 1): card}, latest_versions={("agent_card", "agent-electronics-01"): 1}),
        )

    def test_task_matches_goal_team_budget_and_assignee(self) -> None:
        task = load_json(EXAMPLES / "valid" / "pump-executor-created.json")
        goal_state, team_state, role_state, card_state = self.build_basis_states()
        self.assertEqual([], validate_task_against_goal_team_and_assignment(task, goal_state, team_state, role_state, evaluated_at="2026-08-04T15:15:00Z", agent_card_state=card_state))
        over = deepcopy(task)
        over["budget_allocation"]["max_model_calls"] = 9
        self.assertIn("task_budget_exceeds_team_plan", validate_task_against_goal_team_and_assignment(over, goal_state, team_state, role_state, evaluated_at="2026-08-04T15:15:00Z", agent_card_state=card_state))
        wrong_phase = deepcopy(task)
        wrong_phase["phase"] = "review"
        self.assertIn("task_assignee_role_phase_mismatch", validate_task_against_goal_team_and_assignment(wrong_phase, goal_state, team_state, role_state, evaluated_at="2026-08-04T15:15:00Z", agent_card_state=card_state))

        unknown_deliverable = deepcopy(task)
        unknown_deliverable["required_outputs"][0]["goal_deliverable_id"] = "missing-deliverable"
        self.assertIn(
            "task_goal_deliverable_not_found",
            validate_task_against_goal_team_and_assignment(
                unknown_deliverable,
                goal_state,
                team_state,
                role_state,
                evaluated_at="2026-08-04T15:15:00Z",
                agent_card_state=card_state,
            ),
        )

    def test_dependencies_must_complete_and_remain_acyclic(self) -> None:
        current = load_json(VERSIONED / "task-driver-selection-v1.json")
        dependency = deepcopy(current)
        dependency["entity"]["entity_id"] = "task-input-fixture-001"
        dependency["created_by_event_id"] = "evt-task-input-completed-001"
        dependency["legal_payload"]["status"] = "completed"
        dependency["legal_payload"]["produced_outputs"] = [{"output_id":"candidate-proposal","entity":{"entity_type":"contribution","entity_id":"fixture-ready-001","entity_version":1}}]
        dependency["legal_payload"]["completion_summary"] = "Fixture prepared."
        candidate = deepcopy(current)
        candidate["legal_payload"]["status"] = "ready"
        candidate["legal_payload"]["dependencies"] = [{"task": dependency["entity"], "required_status":"completed"}]
        state = TaskState(tasks={("task", "task-input-fixture-001", 1): dependency}, latest_versions={("task", "task-input-fixture-001"): 1})
        self.assertEqual([], validate_task_graph_and_dependencies(candidate, state))
        dependency["legal_payload"]["status"] = "in_progress"
        self.assertIn("task_dependency_not_completed", validate_task_graph_and_dependencies(candidate, state))
        dependency["legal_payload"]["dependencies"] = [{"task": current["entity"], "required_status":"completed"}]
        self.assertIn("task_dependency_cycle", validate_task_graph_and_dependencies(candidate, state))


    def test_aggregate_task_allocations_do_not_exceed_team_budget(self) -> None:
        current = load_json(VERSIONED / "task-driver-selection-v1.json")
        other = deepcopy(current)
        other["entity"]["entity_id"] = "task-other-001"
        other["created_by_event_id"] = "evt-task-other-001"
        other["legal_payload"]["budget_allocation"]["max_model_calls"] = 7
        state = TaskState(
            tasks={("task", "task-other-001", 1): other},
            latest_versions={("task", "task-other-001"): 1},
        )
        team = load_json(VERSIONED / "team-plan-pump-v1.json")
        team_state = TeamPlanState(
            plans={("team_plan", "team-plan-pump-001", 1): team},
            latest_versions={("team_plan", "team-plan-pump-001"): 1},
        )
        self.assertEqual(
            ["task_budget_pool_exceeded"],
            validate_task_budget_pool(current, state, team_state),
        )

    def test_context_grant_is_limited_by_task_scope_and_lifecycle(self) -> None:
        grant = load_json(VERSIONED / "context-grant-executor-v1.json")["legal_payload"]
        task_records = {
            ("task", "task-driver-selection-001", version): load_json(
                VERSIONED / f"task-driver-selection-v{version}.json"
            )
            for version in range(1, 5)
        }
        state = TaskState(
            tasks=task_records,
            latest_versions={("task", "task-driver-selection-001"): 4},
        )
        self.assertEqual([], validate_context_grant_against_task(grant, state))

        excessive = deepcopy(grant)
        excessive["fields"].append("private_note")
        self.assertIn(
            "context_grant_field_not_required_by_task",
            validate_context_grant_against_task(excessive, state),
        )

        terminal_records = dict(task_records)
        terminal_records[("task", "task-driver-selection-001", 5)] = load_json(
            VERSIONED / "task-driver-selection-v5.json"
        )
        terminal_state = TaskState(
            tasks=terminal_records,
            latest_versions={("task", "task-driver-selection-001"): 5},
        )
        self.assertIn(
            "context_grant_task_terminal",
            validate_context_grant_against_task(grant, terminal_state),
        )

    def test_task_creation_and_update_authority(self) -> None:
        owner = load_json(VERSIONED / "role-assignment-owner-v3.json")
        owner_state = RoleAssignmentAuthorityState(assignments={("role_assignment", "role-assignment-owner-001", 3): owner}, latest_versions={("role_assignment", "role-assignment-owner-001"): 3})
        create_envelope = load_json(ROOT / "examples" / "protocol-envelope" / "valid" / "task-create.json")
        self.assertEqual([], validate_task_update_authority(create_envelope, owner_state, TaskState()))

        executor = load_json(VERSIONED / "role-assignment-executor-v1.json")
        executor = deepcopy(executor)
        executor["legal_payload"]["authority_scope"]["operations"].append("task.next_version")
        executor_state = RoleAssignmentAuthorityState(assignments={("role_assignment", "role-assignment-executor-001", 1): executor}, latest_versions={("role_assignment", "role-assignment-executor-001"): 1})
        task = load_json(VERSIONED / "task-driver-selection-v1.json")
        update = deepcopy(create_envelope)
        update["message_id"] = "evt-task-ready-002"
        update["authorized_by"] = {"entity_type":"role_assignment","entity_id":"role-assignment-executor-001","entity_version":1}
        update["on_behalf_of"] = {"principal_type":"agent","principal_id":"agent-electronics-01"}
        update["precondition"] = {"mode":"next_version","expected_entity_version":1,"target":task["entity"]}
        update["entity"]["entity_version"] = 2
        update["payload"] = load_json(VERSIONED / "task-driver-selection-v2.json")
        task_state = TaskState(tasks={("task", "task-driver-selection-001", 1): task}, latest_versions={("task", "task-driver-selection-001"): 1})
        self.assertEqual([], validate_task_update_authority(update, executor_state, task_state))

    def test_executor_cannot_create_tasks_even_with_named_operation(self) -> None:
        payload = load_json(VERSIONED / "role-assignment-executor-v1.json")["legal_payload"]
        payload = deepcopy(payload)
        payload["authority_scope"]["operations"].append("task.create")
        self.assertIn("task_creation_requires_orchestrator_or_owner", validate_role_assignment_semantics(payload))


if __name__ == "__main__":
    unittest.main()
