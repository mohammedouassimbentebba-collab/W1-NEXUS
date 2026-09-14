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
    GOAL_CONTRACT_PAYLOAD_SCHEMA,
    TEAM_PLAN_PAYLOAD_SCHEMA,
    GoalContractState,
    RoleAssignmentAuthorityState,
    validate_envelope_role_assignment_authority,
    validate_protocol_envelope_semantics,
    validate_role_assignment_semantics,
    validate_team_plan_against_goal_and_assignments,
    validate_team_plan_semantics,
    validate_team_plan_transition,
)

SCHEMA_PATH = ROOT / "schemas" / "w1-cip" / "0.1" / "team-plan.schema.json"
VERSIONED_ENTITY_PATH = ROOT / "schemas" / "w1-cip" / "0.1" / "versioned-entity.schema.json"
ENTITY_REF_PATH = ROOT / "schemas" / "w1-cip" / "0.1" / "entity-ref.schema.json"
PRINCIPAL_REF_PATH = ROOT / "schemas" / "w1-cip" / "0.1" / "principal-ref.schema.json"
ROLE_ASSIGNMENT_PATH = ROOT / "schemas" / "w1-cip" / "0.1" / "role-assignment.schema.json"
AGENT_CARD_PATH = ROOT / "schemas" / "w1-cip" / "0.1" / "agent-card.schema.json"
GOAL_CONTRACT_PATH = ROOT / "schemas" / "w1-cip" / "0.1" / "goal-contract.schema.json"
EXAMPLES = ROOT / "examples" / "team-plan"
VERSIONED = ROOT / "examples" / "versioned-entity" / "valid"
ENVELOPES = ROOT / "examples" / "protocol-envelope" / "valid"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def role_record(entity_id: str, role: str, principal_id: str, *, principal_type: str = "agent") -> dict:
    scope: dict = {"operations": [f"{role}.work"]}
    if role == "decision_authority":
        scope = {
            "operations": ["decision.create", "decision.next_version"],
            "decision_subjects": ["motor_driver_selection"],
        }
    basis = []
    if principal_type == "agent":
        basis = [
            {
                "entity_type": "agent_card",
                "entity_id": principal_id,
                "entity_version": 1,
            }
        ]
    payload = {
        "subject": {"principal_type": principal_type, "principal_id": principal_id},
        "role": role,
        "authority_scope": scope,
        "status": "active",
        "valid_from": "2026-08-04T15:00:00Z",
        "assignment_reason": f"fill the {role} team slot",
    }
    if basis:
        payload["basis"] = basis
    return {
        "entity": {
            "entity_type": "role_assignment",
            "entity_id": entity_id,
            "entity_version": 1,
        },
        "created_by_event_id": f"evt-{entity_id}",
        "legal_payload_schema": "urn:w1-cip:entity-payload:0.1:role-assignment",
        "legal_payload": payload,
    }


class TeamPlanSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = load_json(SCHEMA_PATH)
        Draft202012Validator.check_schema(cls.schema)
        entity_ref = load_json(ENTITY_REF_PATH)
        registry = Registry().with_resource(
            entity_ref["$id"], Resource.from_contents(entity_ref)
        )
        cls.validator = Draft202012Validator(
            cls.schema, registry=registry, format_checker=FormatChecker()
        )

    def structural_errors(self, instance: dict) -> list:
        return sorted(self.validator.iter_errors(instance), key=lambda error: list(error.path))

    def test_valid_examples(self) -> None:
        paths = sorted((EXAMPLES / "valid").glob("*.json"))
        self.assertTrue(paths, "no valid TeamPlan examples found")
        for path in paths:
            with self.subTest(path=path.name):
                instance = load_json(path)
                self.assertEqual([], self.structural_errors(instance))
                self.assertEqual([], validate_team_plan_semantics(instance))

    def test_structurally_invalid_examples(self) -> None:
        paths = sorted((EXAMPLES / "invalid-structural").glob("*.json"))
        self.assertTrue(paths, "no structurally invalid TeamPlan examples found")
        for path in paths:
            with self.subTest(path=path.name):
                self.assertTrue(
                    self.structural_errors(load_json(path)),
                    f"{path.name} unexpectedly passed structural validation",
                )

    def test_semantically_invalid_examples(self) -> None:
        directory = EXAMPLES / "invalid-semantic"
        manifest = load_json(directory / "manifest.json")
        for filename, expected_codes in manifest.items():
            with self.subTest(path=filename):
                instance = load_json(directory / filename)
                self.assertEqual([], self.structural_errors(instance))
                self.assertEqual(expected_codes, validate_team_plan_semantics(instance))

    def test_versioned_entity_resolves_team_plan_schema(self) -> None:
        schemas = [
            load_json(VERSIONED_ENTITY_PATH),
            load_json(ENTITY_REF_PATH),
            load_json(PRINCIPAL_REF_PATH),
            load_json(ROLE_ASSIGNMENT_PATH),
            load_json(AGENT_CARD_PATH),
            load_json(GOAL_CONTRACT_PATH),
            self.schema,
        ]
        for schema in schemas:
            Draft202012Validator.check_schema(schema)
        registry = Registry()
        for schema in schemas[1:]:
            registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))
        validator = Draft202012Validator(
            schemas[0], registry=registry, format_checker=FormatChecker()
        )
        record = load_json(VERSIONED / "team-plan-pump-v1.json")
        self.assertEqual([], list(validator.iter_errors(record)))

    def build_states(self) -> tuple[GoalContractState, RoleAssignmentAuthorityState]:
        goal = load_json(VERSIONED / "goal-contract-pump-v1.json")
        goal_state = GoalContractState(
            contracts={("goal_contract", "goal-pump-driver-001", 1): goal},
            latest_versions={("goal_contract", "goal-pump-driver-001"): 1},
        )
        records = [
            role_record("role-assignment-orchestrator-001", "orchestrator", "agent-orchestrator-01"),
            role_record("role-assignment-executor-001", "executor", "agent-electronics-01"),
            role_record("role-assignment-reviewer-001", "reviewer", "agent-reviewer-01"),
            role_record("role-assignment-verifier-001", "verifier", "agent-verifier-01"),
            role_record("role-assignment-decision-001", "decision_authority", "human-decision-001", principal_type="human"),
            role_record("role-assignment-synthesizer-001", "synthesizer", "agent-synthesizer-01"),
        ]
        role_state = RoleAssignmentAuthorityState(
            assignments={
                (
                    record["entity"]["entity_type"],
                    record["entity"]["entity_id"],
                    record["entity"]["entity_version"],
                ): record
                for record in records
            },
            latest_versions={
                (record["entity"]["entity_type"], record["entity"]["entity_id"]): 1
                for record in records
            },
        )
        return goal_state, role_state

    def test_versioned_entity_rejects_team_plan_type_schema_mismatch(self) -> None:
        schemas = [
            load_json(VERSIONED_ENTITY_PATH),
            load_json(ENTITY_REF_PATH),
            load_json(PRINCIPAL_REF_PATH),
            load_json(ROLE_ASSIGNMENT_PATH),
            load_json(AGENT_CARD_PATH),
            load_json(GOAL_CONTRACT_PATH),
            self.schema,
        ]
        registry = Registry()
        for schema in schemas[1:]:
            registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))
        validator = Draft202012Validator(
            schemas[0], registry=registry, format_checker=FormatChecker()
        )
        record = load_json(VERSIONED / "team-plan-pump-v1.json")
        wrong_type = deepcopy(record)
        wrong_type["entity"]["entity_type"] = "claim"
        self.assertTrue(list(validator.iter_errors(wrong_type)))
        wrong_schema = deepcopy(record)
        wrong_schema["legal_payload_schema"] = GOAL_CONTRACT_PAYLOAD_SCHEMA
        self.assertTrue(list(validator.iter_errors(wrong_schema)))

    def test_valid_plan_resolves_goal_roles_decision_and_independence(self) -> None:
        plan = load_json(EXAMPLES / "valid" / "pump-driver-team.json")
        goal_state, role_state = self.build_states()
        self.assertEqual(
            [],
            validate_team_plan_against_goal_and_assignments(
                plan, goal_state, role_state, evaluated_at="2026-08-04T15:30:00Z"
            ),
        )

    def test_plan_rejects_goal_risk_and_authority_mismatch(self) -> None:
        plan = load_json(EXAMPLES / "valid" / "pump-driver-team.json")
        plan["planning_assessment"]["risk_level"] = "low"
        plan["decision_authorities"][0]["decision_subject"] = "other_subject"
        goal_state, role_state = self.build_states()
        errors = validate_team_plan_against_goal_and_assignments(
            plan, goal_state, role_state, evaluated_at="2026-08-04T15:30:00Z"
        )
        self.assertIn("team_plan_risk_level_mismatch", errors)
        self.assertIn("team_plan_decision_authority_coverage_mismatch", errors)

    def test_decision_authority_must_cover_the_goal_subject(self) -> None:
        plan = load_json(EXAMPLES / "valid" / "pump-driver-team.json")
        goal_state, role_state = self.build_states()
        authority = role_state.assignments[("role_assignment", "role-assignment-decision-001", 1)]
        authority["legal_payload"]["authority_scope"]["decision_subjects"] = ["other_subject"]
        self.assertIn(
            "team_decision_subject_not_authorized",
            validate_team_plan_against_goal_and_assignments(
                plan, goal_state, role_state, evaluated_at="2026-08-04T15:30:00Z"
            ),
        )

    def test_role_separation_is_checked_against_actual_principals(self) -> None:
        plan = load_json(EXAMPLES / "valid" / "pump-driver-team.json")
        goal_state, role_state = self.build_states()
        reviewer = role_state.assignments[("role_assignment", "role-assignment-reviewer-001", 1)]
        reviewer["legal_payload"]["subject"] = {
            "principal_type": "agent",
            "principal_id": "agent-electronics-01",
        }
        errors = validate_team_plan_against_goal_and_assignments(
            plan, goal_state, role_state, evaluated_at="2026-08-04T15:30:00Z"
        )
        self.assertIn("team_independence_role_separation_violated", errors)

    def test_semantic_goal_change_requires_replanning_but_editorial_none_does_not(self) -> None:
        plan = load_json(EXAMPLES / "valid" / "pump-driver-team.json")
        goal_state, role_state = self.build_states()
        v1 = goal_state.contracts[("goal_contract", "goal-pump-driver-001", 1)]
        v2 = deepcopy(v1)
        v2["entity"]["entity_version"] = 2
        v2["created_by_event_id"] = "evt-goal-contract-created-002"
        v2["previous_version"] = deepcopy(v1["entity"])
        v2["change_metadata"] = {
            "change_reason": "editorial_correction",
            "substantive_effect": "none",
        }
        v2["legal_payload"]["title"] += "."
        editorial_state = GoalContractState(
            contracts={
                ("goal_contract", "goal-pump-driver-001", 1): v1,
                ("goal_contract", "goal-pump-driver-001", 2): v2,
            },
            latest_versions={("goal_contract", "goal-pump-driver-001"): 2},
        )
        self.assertNotIn(
            "team_plan_goal_version_not_current",
            validate_team_plan_against_goal_and_assignments(
                plan, editorial_state, role_state, evaluated_at="2026-08-04T15:30:00Z"
            ),
        )
        v2["change_metadata"] = {
            "change_reason": "semantic",
            "substantive_effect": "may_affect_dependents",
        }
        self.assertIn(
            "team_plan_goal_version_not_current",
            validate_team_plan_against_goal_and_assignments(
                plan, editorial_state, role_state, evaluated_at="2026-08-04T15:30:00Z"
            ),
        )

    def test_stale_or_wrong_role_assignment_is_rejected(self) -> None:
        plan = load_json(EXAMPLES / "valid" / "pump-driver-team.json")
        goal_state, role_state = self.build_states()
        role_state.latest_versions[("role_assignment", "role-assignment-reviewer-001")] = 2
        role_state.assignments[("role_assignment", "role-assignment-verifier-001", 1)][
            "legal_payload"
        ]["role"] = "reviewer"
        errors = validate_team_plan_against_goal_and_assignments(
            plan, goal_state, role_state, evaluated_at="2026-08-04T15:30:00Z"
        )
        self.assertIn("team_role_assignment_version_not_current", errors)
        self.assertIn("team_role_assignment_role_mismatch", errors)

    def test_goal_human_approval_requires_human_decision_authority(self) -> None:
        plan = load_json(EXAMPLES / "valid" / "pump-driver-team.json")
        goal_state, role_state = self.build_states()
        goal = goal_state.contracts[("goal_contract", "goal-pump-driver-001", 1)]
        goal["legal_payload"]["decision_policy"][0]["human_approval_required"] = True
        decision = role_state.assignments[("role_assignment", "role-assignment-decision-001", 1)]
        decision["legal_payload"]["subject"] = {
            "principal_type": "agent",
            "principal_id": "agent-decision-001",
        }
        decision["legal_payload"]["basis"] = [
            {
                "entity_type": "agent_card",
                "entity_id": "agent-decision-001",
                "entity_version": 1,
            }
        ]
        self.assertIn(
            "team_decision_authority_must_be_human",
            validate_team_plan_against_goal_and_assignments(
                plan, goal_state, role_state, evaluated_at="2026-08-04T15:30:00Z"
            ),
        )

    def test_goal_independence_is_enforced_even_without_relying_on_rule_labels(self) -> None:
        plan = load_json(EXAMPLES / "valid" / "pump-driver-team.json")
        goal_state, role_state = self.build_states()
        decision = role_state.assignments[("role_assignment", "role-assignment-decision-001", 1)]
        decision["legal_payload"]["subject"] = {
            "principal_type": "agent",
            "principal_id": "agent-electronics-01",
        }
        decision["legal_payload"]["basis"] = [
            {
                "entity_type": "agent_card",
                "entity_id": "agent-electronics-01",
                "entity_version": 1,
            }
        ]
        errors = validate_team_plan_against_goal_and_assignments(
            plan, goal_state, role_state, evaluated_at="2026-08-04T15:30:00Z"
        )
        self.assertIn("team_goal_independence_requirement_violated", errors)

    def test_team_plan_management_roles_are_restricted(self) -> None:
        executor = role_record(
            "role-assignment-executor-001", "executor", "agent-electronics-01"
        )["legal_payload"]
        executor["authority_scope"]["operations"] = ["team_plan.create"]
        self.assertIn(
            "team_plan_management_requires_orchestrator_or_owner",
            validate_role_assignment_semantics(executor),
        )

        orchestrator = role_record(
            "role-assignment-orchestrator-001", "orchestrator", "agent-orchestrator-01"
        )["legal_payload"]
        orchestrator["authority_scope"]["operations"] = ["team_plan.create"]
        self.assertNotIn(
            "team_plan_management_requires_orchestrator_or_owner",
            validate_role_assignment_semantics(orchestrator),
        )

    def test_orchestrator_can_authorize_team_plan_creation(self) -> None:
        event = load_json(ENVELOPES / "team-plan-create.json")
        record = role_record(
            "role-assignment-orchestrator-001", "orchestrator", "agent-orchestrator-01"
        )
        record["legal_payload"]["authority_scope"]["operations"] = [
            "team_plan.create",
            "team_plan.next_version",
        ]
        state = RoleAssignmentAuthorityState(
            assignments={("role_assignment", "role-assignment-orchestrator-001", 1): record},
            latest_versions={("role_assignment", "role-assignment-orchestrator-001"): 1},
        )
        self.assertEqual(
            [],
            validate_envelope_role_assignment_authority(
                event, state, acceptance_time="2026-08-04T15:10:01Z"
            ),
        )

    def test_initial_plan_must_be_active_and_terminal_status_is_final(self) -> None:
        v1 = load_json(VERSIONED / "team-plan-pump-v1.json")
        inactive = deepcopy(v1)
        inactive["legal_payload"]["status"] = "suspended"
        inactive["legal_payload"]["status_reason"] = "waiting for a verifier"
        self.assertIn(
            "team_plan_initial_status_not_active",
            validate_team_plan_transition(inactive, None),
        )
        terminal = deepcopy(v1)
        terminal["legal_payload"]["status"] = "cancelled"
        terminal["legal_payload"]["status_reason"] = "user cancelled the session"
        v2 = deepcopy(v1)
        v2["entity"]["entity_version"] = 2
        v2["created_by_event_id"] = "evt-team-plan-created-002"
        v2["previous_version"] = deepcopy(v1["entity"])
        v2["change_metadata"] = {
            "change_reason": "semantic",
            "substantive_effect": "may_affect_dependents",
        }
        v2["legal_payload"]["selection_rationale"] += " Updated."
        self.assertIn(
            "team_plan_terminal_status_final",
            validate_team_plan_transition(v2, terminal),
        )

    def test_protocol_envelope_runs_team_plan_semantics(self) -> None:
        event = load_json(ENVELOPES / "team-plan-create.json")
        event["payload"]["legal_payload"]["assignments"].append(
            deepcopy(event["payload"]["legal_payload"]["assignments"][0])
        )
        self.assertIn("duplicate_team_slot_id", validate_protocol_envelope_semantics(event))

    def test_schema_id_constant_is_stable(self) -> None:
        self.assertEqual(TEAM_PLAN_PAYLOAD_SCHEMA, self.schema["$id"])
        self.assertEqual(
            GOAL_CONTRACT_PAYLOAD_SCHEMA,
            load_json(GOAL_CONTRACT_PATH)["$id"],
        )


if __name__ == "__main__":
    unittest.main()
