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
    VersionedEntityRepositoryState,
    validate_versioned_entity_repository_semantics,
    validate_versioned_entity_semantics,
    validate_versioned_entity_transition,
)

SCHEMA_PATH = ROOT / "schemas" / "w1-cip" / "0.1" / "versioned-entity.schema.json"
ENTITY_REF_PATH = ROOT / "schemas" / "w1-cip" / "0.1" / "entity-ref.schema.json"
PRINCIPAL_REF_PATH = ROOT / "schemas" / "w1-cip" / "0.1" / "principal-ref.schema.json"
ROLE_ASSIGNMENT_PATH = ROOT / "schemas" / "w1-cip" / "0.1" / "role-assignment.schema.json"
AGENT_CARD_PATH = ROOT / "schemas" / "w1-cip" / "0.1" / "agent-card.schema.json"
GOAL_CONTRACT_PATH = ROOT / "schemas" / "w1-cip" / "0.1" / "goal-contract.schema.json"
TEAM_PLAN_PATH = ROOT / "schemas" / "w1-cip" / "0.1" / "team-plan.schema.json"
CONTEXT_GRANT_PATH = ROOT / "schemas" / "w1-cip" / "0.1" / "context-grant.schema.json"
TASK_PATH = ROOT / "schemas" / "w1-cip" / "0.1" / "task.schema.json"
CONTRIBUTION_PATH = ROOT / "schemas" / "w1-cip" / "0.1" / "contribution.schema.json"
EVIDENCE_PATH = ROOT / "schemas" / "w1-cip" / "0.1" / "evidence.schema.json"
VERIFICATION_PATH = ROOT / "schemas" / "w1-cip" / "0.1" / "verification.schema.json"
CHALLENGE_PATH = ROOT / "schemas" / "w1-cip" / "0.1" / "challenge.schema.json"
REVIEW_PATH = ROOT / "schemas" / "w1-cip" / "0.1" / "review.schema.json"
APPROVAL_PATH = ROOT / "schemas" / "w1-cip" / "0.1" / "approval.schema.json"
DECISION_PATH = ROOT / "schemas" / "w1-cip" / "0.1" / "decision.schema.json"
EXECUTION_RESOURCE_PLAN_PATH = ROOT / "schemas" / "w1-cip" / "0.1" / "execution-resource-plan.schema.json"
FINAL_RESULT_PATH = ROOT / "schemas" / "w1-cip" / "0.1" / "final-result.schema.json"
PROTOCOL_EVENT_PATH = ROOT / "schemas" / "w1-cip" / "0.1" / "protocol-event.schema.json"
EXAMPLES = ROOT / "examples" / "versioned-entity"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class VersionedEntitySchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = load_json(SCHEMA_PATH)
        cls.entity_ref_schema = load_json(ENTITY_REF_PATH)
        cls.principal_ref_schema = load_json(PRINCIPAL_REF_PATH)
        cls.role_assignment_schema = load_json(ROLE_ASSIGNMENT_PATH)
        cls.agent_card_schema = load_json(AGENT_CARD_PATH)
        cls.goal_contract_schema = load_json(GOAL_CONTRACT_PATH)
        cls.team_plan_schema = load_json(TEAM_PLAN_PATH)
        cls.context_grant_schema = load_json(CONTEXT_GRANT_PATH)
        cls.task_schema = load_json(TASK_PATH)
        cls.contribution_schema = load_json(CONTRIBUTION_PATH)
        cls.evidence_schema = load_json(EVIDENCE_PATH)
        cls.verification_schema = load_json(VERIFICATION_PATH)
        cls.challenge_schema = load_json(CHALLENGE_PATH)
        cls.review_schema = load_json(REVIEW_PATH)
        cls.approval_schema = load_json(APPROVAL_PATH)
        cls.decision_schema = load_json(DECISION_PATH)
        cls.execution_resource_plan_schema = load_json(EXECUTION_RESOURCE_PLAN_PATH)
        cls.final_result_schema = load_json(FINAL_RESULT_PATH)
        cls.protocol_event_schema = load_json(PROTOCOL_EVENT_PATH)
        Draft202012Validator.check_schema(cls.schema)
        Draft202012Validator.check_schema(cls.entity_ref_schema)
        Draft202012Validator.check_schema(cls.principal_ref_schema)
        Draft202012Validator.check_schema(cls.role_assignment_schema)
        Draft202012Validator.check_schema(cls.agent_card_schema)
        Draft202012Validator.check_schema(cls.goal_contract_schema)
        Draft202012Validator.check_schema(cls.team_plan_schema)
        Draft202012Validator.check_schema(cls.context_grant_schema)
        Draft202012Validator.check_schema(cls.task_schema)
        Draft202012Validator.check_schema(cls.contribution_schema)
        Draft202012Validator.check_schema(cls.evidence_schema)
        Draft202012Validator.check_schema(cls.verification_schema)
        Draft202012Validator.check_schema(cls.challenge_schema)
        Draft202012Validator.check_schema(cls.review_schema)
        Draft202012Validator.check_schema(cls.approval_schema)
        Draft202012Validator.check_schema(cls.decision_schema)
        Draft202012Validator.check_schema(cls.execution_resource_plan_schema)
        Draft202012Validator.check_schema(cls.final_result_schema)
        Draft202012Validator.check_schema(cls.protocol_event_schema)
        registry = Registry()
        for schema in (
            cls.entity_ref_schema,
            cls.principal_ref_schema,
            cls.role_assignment_schema,
            cls.agent_card_schema,
            cls.goal_contract_schema,
            cls.team_plan_schema,
            cls.context_grant_schema,
            cls.task_schema,
            cls.contribution_schema,
            cls.evidence_schema,
            cls.verification_schema,
            cls.challenge_schema,
            cls.review_schema,
            cls.approval_schema,
            cls.decision_schema,
            cls.execution_resource_plan_schema,
            cls.final_result_schema,
            cls.protocol_event_schema,
        ):
            registry = registry.with_resource(
                schema["$id"], Resource.from_contents(schema)
            )
        cls.validator = Draft202012Validator(
            cls.schema,
            registry=registry,
            format_checker=FormatChecker(),
        )

    def structural_errors(self, instance: dict) -> list:
        return sorted(self.validator.iter_errors(instance), key=lambda error: list(error.path))

    def test_valid_examples(self) -> None:
        paths = sorted((EXAMPLES / "valid").glob("*.json"))
        self.assertTrue(paths, "no valid VersionedEntity examples found")
        for path in paths:
            with self.subTest(path=path.name):
                instance = load_json(path)
                self.assertEqual([], self.structural_errors(instance))
                self.assertEqual([], validate_versioned_entity_semantics(instance))

    def test_structurally_invalid_examples(self) -> None:
        paths = sorted((EXAMPLES / "invalid-structural").glob("*.json"))
        self.assertTrue(paths, "no structurally invalid VersionedEntity examples found")
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
                self.assertEqual(expected_codes, validate_versioned_entity_semantics(instance))

    def test_valid_transitions(self) -> None:
        v1 = load_json(EXAMPLES / "valid" / "claim-v1.json")
        v2 = load_json(EXAMPLES / "valid" / "claim-v2-semantic.json")
        v3 = load_json(EXAMPLES / "valid" / "claim-v3-editorial-none.json")
        self.assertEqual([], validate_versioned_entity_transition(v1, None))
        self.assertEqual([], validate_versioned_entity_transition(v2, v1))
        self.assertEqual([], validate_versioned_entity_transition(v3, v2))

    def test_transition_rejects_unchanged_legal_payload(self) -> None:
        v1 = load_json(EXAMPLES / "valid" / "claim-v1.json")
        current = load_json(EXAMPLES / "valid" / "claim-v2-semantic.json")
        current["legal_payload"] = deepcopy(v1["legal_payload"])
        self.assertIn(
            "legal_payload_unchanged",
            validate_versioned_entity_transition(current, v1),
        )

    def test_editorial_none_cannot_change_payload_schema(self) -> None:
        previous = load_json(EXAMPLES / "valid" / "claim-v2-semantic.json")
        current = load_json(EXAMPLES / "valid" / "claim-v3-editorial-none.json")
        current["legal_payload_schema"] = "urn:w1-cip:entity-payload:0.2:claim"
        self.assertIn(
            "editorial_none_schema_changed",
            validate_versioned_entity_transition(current, previous),
        )

    def test_transition_requires_actual_previous_record(self) -> None:
        current = load_json(EXAMPLES / "valid" / "claim-v2-semantic.json")
        self.assertEqual(
            ["previous_record_required"],
            validate_versioned_entity_transition(current, None),
        )

    def test_transition_checks_declared_previous_record(self) -> None:
        current = load_json(EXAMPLES / "valid" / "claim-v2-semantic.json")
        wrong_previous = load_json(EXAMPLES / "valid" / "claim-v1.json")
        wrong_previous["entity"]["entity_id"] = "claim-other"
        errors = validate_versioned_entity_transition(current, wrong_previous)
        self.assertIn("previous_record_ref_mismatch", errors)
        self.assertIn("previous_record_identity_mismatch", errors)

    def test_repository_rejects_existing_identity_creation(self) -> None:
        v1 = load_json(EXAMPLES / "valid" / "claim-v1.json")
        state = VersionedEntityRepositoryState(
            latest_versions={("claim", "claim-21"): 2},
            accepted_versions=frozenset(),
        )
        self.assertEqual(
            ["entity_already_exists"],
            validate_versioned_entity_repository_semantics(v1, state),
        )

    def test_repository_rejects_stale_previous_version(self) -> None:
        v2 = load_json(EXAMPLES / "valid" / "claim-v2-semantic.json")
        state = VersionedEntityRepositoryState(
            latest_versions={("claim", "claim-21"): 2},
            accepted_versions=frozenset(),
        )
        self.assertEqual(
            ["version_conflict"],
            validate_versioned_entity_repository_semantics(v2, state),
        )

    def test_repository_rejects_reaccepted_version(self) -> None:
        v2 = load_json(EXAMPLES / "valid" / "claim-v2-semantic.json")
        state = VersionedEntityRepositoryState(
            latest_versions={("claim", "claim-21"): 2},
            accepted_versions=frozenset({("claim", "claim-21", 2)}),
        )
        self.assertEqual(
            ["immutable_version"],
            validate_versioned_entity_repository_semantics(v2, state),
        )

    def test_repository_accepts_next_version(self) -> None:
        v2 = load_json(EXAMPLES / "valid" / "claim-v2-semantic.json")
        state = VersionedEntityRepositoryState(
            latest_versions={("claim", "claim-21"): 1},
            accepted_versions=frozenset({("claim", "claim-21", 1)}),
        )
        self.assertEqual(
            [],
            validate_versioned_entity_repository_semantics(v2, state),
        )


if __name__ == "__main__":
    unittest.main()
