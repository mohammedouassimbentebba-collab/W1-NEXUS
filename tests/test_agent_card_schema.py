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
    AGENT_CARD_PAYLOAD_SCHEMA,
    AgentCardState,
    RoleAssignmentAuthorityState,
    validate_agent_card_transition,
    validate_envelope_role_assignment_authority,
    validate_role_assignment_agent_eligibility,
)

SCHEMA_PATH = ROOT / "schemas" / "w1-cip" / "0.1" / "agent-card.schema.json"
VERSIONED_ENTITY_PATH = ROOT / "schemas" / "w1-cip" / "0.1" / "versioned-entity.schema.json"
ENTITY_REF_PATH = ROOT / "schemas" / "w1-cip" / "0.1" / "entity-ref.schema.json"
PRINCIPAL_REF_PATH = ROOT / "schemas" / "w1-cip" / "0.1" / "principal-ref.schema.json"
ROLE_ASSIGNMENT_PATH = ROOT / "schemas" / "w1-cip" / "0.1" / "role-assignment.schema.json"
GOAL_CONTRACT_PATH = ROOT / "schemas" / "w1-cip" / "0.1" / "goal-contract.schema.json"
EXAMPLES = ROOT / "examples" / "agent-card"
VERSIONED = ROOT / "examples" / "versioned-entity" / "valid"
ROLE_EXAMPLES = ROOT / "examples" / "role-assignment" / "valid"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class AgentCardSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = load_json(SCHEMA_PATH)
        Draft202012Validator.check_schema(cls.schema)
        cls.validator = Draft202012Validator(
            cls.schema,
            format_checker=FormatChecker(),
        )

    def structural_errors(self, instance: dict) -> list:
        return sorted(self.validator.iter_errors(instance), key=lambda error: list(error.path))

    def test_valid_examples(self) -> None:
        paths = sorted((EXAMPLES / "valid").glob("*.json"))
        self.assertTrue(paths, "no valid AgentCard examples found")
        for path in paths:
            with self.subTest(path=path.name):
                self.assertEqual([], self.structural_errors(load_json(path)))

    def test_structurally_invalid_examples(self) -> None:
        paths = sorted((EXAMPLES / "invalid-structural").glob("*.json"))
        self.assertTrue(paths, "no invalid AgentCard examples found")
        for path in paths:
            with self.subTest(path=path.name):
                self.assertTrue(
                    self.structural_errors(load_json(path)),
                    f"{path.name} unexpectedly passed structural validation",
                )

    def test_versioned_entity_resolves_agent_card_schema(self) -> None:
        schemas = [
            load_json(VERSIONED_ENTITY_PATH),
            load_json(ENTITY_REF_PATH),
            load_json(PRINCIPAL_REF_PATH),
            load_json(ROLE_ASSIGNMENT_PATH),
            self.schema,
            load_json(GOAL_CONTRACT_PATH),
        ]
        for schema in schemas:
            Draft202012Validator.check_schema(schema)
        registry = Registry()
        for schema in schemas[1:]:
            registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))
        validator = Draft202012Validator(
            schemas[0], registry=registry, format_checker=FormatChecker()
        )
        record = load_json(VERSIONED / "agent-card-electronics-v1.json")
        self.assertEqual([], list(validator.iter_errors(record)))

    def test_retired_agent_card_is_terminal(self) -> None:
        previous = load_json(VERSIONED / "agent-card-electronics-v1.json")
        previous["legal_payload"]["status"] = "retired"
        previous["legal_payload"]["status_reason"] = "agent permanently withdrawn"
        current = load_json(VERSIONED / "agent-card-electronics-v2.json")
        self.assertIn(
            "agent_card_retired_final",
            validate_agent_card_transition(current, previous),
        )

    def test_agent_role_assignment_requires_current_active_eligible_card(self) -> None:
        assignment_payload = load_json(ROLE_EXAMPLES / "executor.json")
        assignment_record = {
            "entity": {
                "entity_type": "role_assignment",
                "entity_id": "role-assignment-executor-001",
                "entity_version": 1,
            },
            "created_by_event_id": "evt-role-assignment-executor-001",
            "legal_payload_schema": "urn:w1-cip:entity-payload:0.1:role-assignment",
            "legal_payload": assignment_payload,
        }
        card = load_json(VERSIONED / "agent-card-electronics-v1.json")
        state = AgentCardState(
            cards={("agent_card", "agent-electronics-01", 1): card},
            latest_versions={("agent_card", "agent-electronics-01"): 1},
        )
        self.assertEqual([], validate_role_assignment_agent_eligibility(assignment_record, state))

        semantic_v2 = load_json(VERSIONED / "agent-card-electronics-v2.json")
        stale = AgentCardState(
            cards={
                ("agent_card", "agent-electronics-01", 1): card,
                ("agent_card", "agent-electronics-01", 2): semantic_v2,
            },
            latest_versions={("agent_card", "agent-electronics-01"): 2},
        )
        self.assertIn(
            "agent_card_version_not_current",
            validate_role_assignment_agent_eligibility(assignment_record, stale),
        )

        editorial_v2 = deepcopy(semantic_v2)
        editorial_v2["change_metadata"] = {
            "change_reason": "editorial_correction",
            "substantive_effect": "none",
            "diff_artifact": {"uri": "artifact://diffs/agent-card-v1-v2", "artifact_version": 1},
            "classification_approval": {
                "entity_type": "approval",
                "entity_id": "approval-agent-card-editorial-001",
                "entity_version": 1,
            },
            "classification_check": {
                "entity_type": "review",
                "entity_id": "review-agent-card-editorial-001",
                "entity_version": 1,
            },
        }
        editorial_v2["legal_payload"] = deepcopy(card["legal_payload"])
        editorial_v2["legal_payload"]["display_name"] = "Electronics Agent v1"
        editorial_state = AgentCardState(
            cards={
                ("agent_card", "agent-electronics-01", 1): card,
                ("agent_card", "agent-electronics-01", 2): editorial_v2,
            },
            latest_versions={("agent_card", "agent-electronics-01"): 2},
        )
        self.assertEqual(
            [],
            validate_role_assignment_agent_eligibility(assignment_record, editorial_state),
        )

        suspended_card = deepcopy(card)
        suspended_card["legal_payload"]["status"] = "suspended"
        suspended_card["legal_payload"]["status_reason"] = "test suspension"
        suspended = AgentCardState(
            cards={("agent_card", "agent-electronics-01", 1): suspended_card},
            latest_versions={("agent_card", "agent-electronics-01"): 1},
        )
        self.assertIn(
            "agent_card_inactive",
            validate_role_assignment_agent_eligibility(assignment_record, suspended),
        )

        ineligible_card = deepcopy(card)
        ineligible_card["legal_payload"]["eligible_roles"] = ["reviewer"]
        ineligible = AgentCardState(
            cards={("agent_card", "agent-electronics-01", 1): ineligible_card},
            latest_versions={("agent_card", "agent-electronics-01"): 1},
        )
        self.assertIn(
            "agent_role_not_eligible",
            validate_role_assignment_agent_eligibility(assignment_record, ineligible),
        )

    def test_agent_assignment_card_reference_must_match_subject(self) -> None:
        payload = load_json(ROLE_EXAMPLES / "executor.json")
        payload["basis"][0]["entity_id"] = "agent-other-01"
        record = {
            "legal_payload_schema": "urn:w1-cip:entity-payload:0.1:role-assignment",
            "legal_payload": payload,
        }
        self.assertEqual(
            ["agent_card_subject_mismatch"],
            validate_role_assignment_agent_eligibility(record, AgentCardState()),
        )

    def test_authority_validation_can_enforce_agent_card_state(self) -> None:
        assignment = load_json(VERSIONED / "role-assignment-executor-v1.json")
        authority_state = RoleAssignmentAuthorityState(
            assignments={("role_assignment", "role-assignment-executor-001", 1): assignment},
            latest_versions={("role_assignment", "role-assignment-executor-001"): 1},
        )
        command = {
            "kind": "command",
            "type": "contribution.create",
            "actor": {"principal_type": "agent", "principal_id": "agent-electronics-01"},
            "authorized_by": {
                "entity_type": "role_assignment",
                "entity_id": "role-assignment-executor-001",
                "entity_version": 1,
            },
            "precondition": {
                "mode": "create",
                "target_identity": {"entity_type": "contribution", "entity_id": "proposal-01"},
            },
        }
        card = load_json(VERSIONED / "agent-card-electronics-v1.json")
        card["legal_payload"]["status"] = "suspended"
        card["legal_payload"]["status_reason"] = "test suspension"
        card_state = AgentCardState(
            cards={("agent_card", "agent-electronics-01", 1): card},
            latest_versions={("agent_card", "agent-electronics-01"): 1},
        )
        self.assertIn(
            "agent_card_inactive",
            validate_envelope_role_assignment_authority(
                command,
                authority_state,
                acceptance_time="2026-08-04T15:30:00Z",
                agent_card_state=card_state,
            ),
        )

    def test_missing_and_incomplete_agent_card_state_are_rejected(self) -> None:
        payload = load_json(ROLE_EXAMPLES / "executor.json")
        record = {
            "legal_payload_schema": "urn:w1-cip:entity-payload:0.1:role-assignment",
            "legal_payload": payload,
        }
        self.assertEqual(
            ["agent_card_not_found"],
            validate_role_assignment_agent_eligibility(record, AgentCardState()),
        )

        card = load_json(VERSIONED / "agent-card-electronics-v1.json")
        incomplete = AgentCardState(
            cards={("agent_card", "agent-electronics-01", 1): card},
            latest_versions={("agent_card", "agent-electronics-01"): 2},
        )
        self.assertEqual(
            ["agent_card_lineage_incomplete"],
            validate_role_assignment_agent_eligibility(record, incomplete),
        )

    def test_agent_card_payload_schema_must_match(self) -> None:
        payload = load_json(ROLE_EXAMPLES / "executor.json")
        record = {
            "legal_payload_schema": "urn:w1-cip:entity-payload:0.1:role-assignment",
            "legal_payload": payload,
        }
        card = load_json(VERSIONED / "agent-card-electronics-v1.json")
        card["legal_payload_schema"] = "urn:w1-cip:entity-payload:0.1:claim"
        state = AgentCardState(
            cards={("agent_card", "agent-electronics-01", 1): card},
            latest_versions={("agent_card", "agent-electronics-01"): 1},
        )
        self.assertEqual(
            ["agent_card_payload_schema_mismatch"],
            validate_role_assignment_agent_eligibility(record, state),
        )

    def test_agent_card_payload_schema_constant(self) -> None:
        self.assertEqual(
            "urn:w1-cip:entity-payload:0.1:agent-card",
            AGENT_CARD_PAYLOAD_SCHEMA,
        )


if __name__ == "__main__":
    unittest.main()
