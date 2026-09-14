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
    RoleAssignmentAuthorityState,
    validate_envelope_role_assignment_authority,
    validate_goal_contract_decision_authorities,
    validate_goal_contract_semantics,
    validate_goal_contract_transition,
    validate_protocol_envelope_semantics,
    validate_role_assignment_semantics,
)

SCHEMA_PATH = ROOT / "schemas" / "w1-cip" / "0.1" / "goal-contract.schema.json"
VERSIONED_ENTITY_PATH = ROOT / "schemas" / "w1-cip" / "0.1" / "versioned-entity.schema.json"
ENTITY_REF_PATH = ROOT / "schemas" / "w1-cip" / "0.1" / "entity-ref.schema.json"
PRINCIPAL_REF_PATH = ROOT / "schemas" / "w1-cip" / "0.1" / "principal-ref.schema.json"
ROLE_ASSIGNMENT_PATH = ROOT / "schemas" / "w1-cip" / "0.1" / "role-assignment.schema.json"
AGENT_CARD_PATH = ROOT / "schemas" / "w1-cip" / "0.1" / "agent-card.schema.json"
TEAM_PLAN_PATH = ROOT / "schemas" / "w1-cip" / "0.1" / "team-plan.schema.json"
EXAMPLES = ROOT / "examples" / "goal-contract"
VERSIONED = ROOT / "examples" / "versioned-entity" / "valid"
ENVELOPES = ROOT / "examples" / "protocol-envelope" / "valid"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class GoalContractSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = load_json(SCHEMA_PATH)
        Draft202012Validator.check_schema(cls.schema)
        registry = Registry().with_resource(
            load_json(ENTITY_REF_PATH)["$id"],
            Resource.from_contents(load_json(ENTITY_REF_PATH)),
        )
        cls.validator = Draft202012Validator(
            cls.schema, registry=registry, format_checker=FormatChecker()
        )

    def structural_errors(self, instance: dict) -> list:
        return sorted(self.validator.iter_errors(instance), key=lambda error: list(error.path))

    def test_valid_examples(self) -> None:
        paths = sorted((EXAMPLES / "valid").glob("*.json"))
        self.assertTrue(paths, "no valid GoalContract examples found")
        for path in paths:
            with self.subTest(path=path.name):
                instance = load_json(path)
                self.assertEqual([], self.structural_errors(instance))
                self.assertEqual([], validate_goal_contract_semantics(instance))

    def test_structurally_invalid_examples(self) -> None:
        paths = sorted((EXAMPLES / "invalid-structural").glob("*.json"))
        self.assertTrue(paths, "no structurally invalid GoalContract examples found")
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
                self.assertEqual(expected_codes, validate_goal_contract_semantics(instance))

    def test_versioned_entity_resolves_goal_contract_schema(self) -> None:
        schemas = [
            load_json(VERSIONED_ENTITY_PATH),
            load_json(ENTITY_REF_PATH),
            load_json(PRINCIPAL_REF_PATH),
            load_json(ROLE_ASSIGNMENT_PATH),
            load_json(AGENT_CARD_PATH),
            self.schema,
            load_json(TEAM_PLAN_PATH),
        ]
        for schema in schemas:
            Draft202012Validator.check_schema(schema)
        registry = Registry()
        for schema in schemas[1:]:
            registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))
        validator = Draft202012Validator(
            schemas[0], registry=registry, format_checker=FormatChecker()
        )
        record = load_json(VERSIONED / "goal-contract-pump-v1.json")
        self.assertEqual([], list(validator.iter_errors(record)))

    def test_initial_goal_must_be_active_and_terminal_status_is_final(self) -> None:
        v1 = load_json(VERSIONED / "goal-contract-pump-v1.json")
        inactive = deepcopy(v1)
        inactive["legal_payload"]["status"] = "suspended"
        inactive["legal_payload"]["status_reason"] = "waiting for user"
        self.assertIn(
            "goal_contract_initial_status_not_active",
            validate_goal_contract_transition(inactive, None),
        )

        terminal = deepcopy(v1)
        terminal["legal_payload"]["status"] = "cancelled"
        terminal["legal_payload"]["status_reason"] = "user cancelled"
        v2 = load_json(VERSIONED / "goal-contract-pump-v2.json")
        self.assertIn(
            "goal_contract_terminal_status_final",
            validate_goal_contract_transition(v2, terminal),
        )

    def test_goal_contract_management_requires_human_owner(self) -> None:
        payload = {
            "subject": {"principal_type": "agent", "principal_id": "agent-orchestrator-01"},
            "role": "orchestrator",
            "authority_scope": {"operations": ["goal_contract.create"]},
            "status": "active",
            "valid_from": "2026-08-04T15:00:00Z",
            "assignment_reason": "attempt to manage the user goal",
            "basis": [
                {"entity_type": "agent_card", "entity_id": "agent-orchestrator-01", "entity_version": 1}
            ],
        }
        self.assertIn(
            "goal_contract_management_requires_human_owner",
            validate_role_assignment_semantics(payload),
        )

    def test_human_owner_can_authorize_goal_creation(self) -> None:
        event = load_json(ENVELOPES / "goal-contract-create.json")
        owner = load_json(VERSIONED / "role-assignment-owner-v1.json")
        owner["entity"]["entity_version"] = 2
        owner["created_by_event_id"] = "evt-role-assignment-owner-v2"
        owner["previous_version"] = {
            "entity_type": "role_assignment",
            "entity_id": "role-assignment-owner-001",
            "entity_version": 1,
        }
        owner["change_metadata"] = {
            "change_reason": "semantic",
            "substantive_effect": "may_affect_dependents",
        }
        owner["legal_payload"]["authority_scope"]["operations"].extend(
            ["goal_contract.create", "goal_contract.next_version"]
        )
        owner["legal_payload"]["assignment_reason"] = "manage roles and the session goal"
        state = RoleAssignmentAuthorityState(
            assignments={("role_assignment", "role-assignment-owner-001", 2): owner},
            latest_versions={("role_assignment", "role-assignment-owner-001"): 2},
        )
        self.assertEqual([], validate_envelope_role_assignment_authority(event, state))

    def test_pinned_decision_authority_must_cover_subject(self) -> None:
        goal = load_json(EXAMPLES / "valid" / "pump-driver-selection.json")
        goal["decision_policy"][0]["authority"] = {
            "mode": "pinned_assignment",
            "required_role": "decision_authority",
            "role_assignment": {
                "entity_type": "role_assignment",
                "entity_id": "role-assignment-decision-001",
                "entity_version": 1,
            },
        }
        assignment = {
            "entity": {
                "entity_type": "role_assignment",
                "entity_id": "role-assignment-decision-001",
                "entity_version": 1,
            },
            "created_by_event_id": "evt-role-assignment-decision-001",
            "legal_payload_schema": "urn:w1-cip:entity-payload:0.1:role-assignment",
            "legal_payload": {
                "subject": {"principal_type": "human", "principal_id": "human-reviewer-001"},
                "role": "decision_authority",
                "authority_scope": {
                    "operations": ["decision.create", "decision.next_version"],
                    "decision_subjects": ["motor_driver_selection"],
                },
                "status": "active",
                "valid_from": "2026-08-04T15:00:00Z",
                "assignment_reason": "approve the motor driver selection",
            },
        }
        state = RoleAssignmentAuthorityState(
            assignments={("role_assignment", "role-assignment-decision-001", 1): assignment},
            latest_versions={("role_assignment", "role-assignment-decision-001"): 1},
        )
        self.assertEqual(
            [],
            validate_goal_contract_decision_authorities(
                goal, state, evaluated_at="2026-08-04T15:30:00Z"
            ),
        )
        assignment["legal_payload"]["authority_scope"]["decision_subjects"] = ["other_subject"]
        self.assertIn(
            "goal_decision_subject_not_authorized",
            validate_goal_contract_decision_authorities(
                goal, state, evaluated_at="2026-08-04T15:30:00Z"
            ),
        )

    def test_prohibitions_have_no_waiver_field(self) -> None:
        goal = load_json(EXAMPLES / "valid" / "pump-driver-selection.json")
        goal["prohibitions"][0]["waivable"] = True
        self.assertTrue(self.structural_errors(goal))


    def test_protocol_envelope_runs_goal_contract_semantics(self) -> None:
        event = load_json(ENVELOPES / "goal-contract-create.json")
        event["payload"]["legal_payload"]["deliverables"].append(
            deepcopy(event["payload"]["legal_payload"]["deliverables"][0])
        )
        self.assertIn(
            "duplicate_deliverable_id",
            validate_protocol_envelope_semantics(event),
        )

    def test_non_owner_cannot_authorize_goal_change(self) -> None:
        event = load_json(ENVELOPES / "goal-contract-create.json")
        event["on_behalf_of"] = {
            "principal_type": "agent",
            "principal_id": "agent-orchestrator-01",
        }
        event["authorized_by"] = {
            "entity_type": "role_assignment",
            "entity_id": "role-assignment-orchestrator-001",
            "entity_version": 1,
        }
        assignment = {
            "entity": {
                "entity_type": "role_assignment",
                "entity_id": "role-assignment-orchestrator-001",
                "entity_version": 1,
            },
            "created_by_event_id": "evt-role-assignment-orchestrator-001",
            "legal_payload_schema": "urn:w1-cip:entity-payload:0.1:role-assignment",
            "legal_payload": {
                "subject": {
                    "principal_type": "agent",
                    "principal_id": "agent-orchestrator-01",
                },
                "role": "orchestrator",
                "authority_scope": {"operations": ["goal_contract.create"]},
                "status": "active",
                "valid_from": "2026-08-04T15:00:00Z",
                "assignment_reason": "invalid goal-management authority",
                "basis": [
                    {
                        "entity_type": "agent_card",
                        "entity_id": "agent-orchestrator-01",
                        "entity_version": 1,
                    }
                ],
            },
        }
        state = RoleAssignmentAuthorityState(
            assignments={("role_assignment", "role-assignment-orchestrator-001", 1): assignment},
            latest_versions={("role_assignment", "role-assignment-orchestrator-001"): 1},
        )
        self.assertIn(
            "goal_contract_management_requires_human_owner",
            validate_envelope_role_assignment_authority(event, state),
        )

    def test_schema_id_constant_is_stable(self) -> None:
        self.assertEqual(GOAL_CONTRACT_PAYLOAD_SCHEMA, self.schema["$id"])


if __name__ == "__main__":
    unittest.main()
