from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from w1cip.validation import validate_protocol_envelope_semantics  # noqa: E402

SCHEMA_PATH = ROOT / "schemas" / "w1-cip" / "0.1" / "protocol-envelope.schema.json"
PRINCIPAL_REF_PATH = ROOT / "schemas" / "w1-cip" / "0.1" / "principal-ref.schema.json"
ENTITY_REF_PATH = ROOT / "schemas" / "w1-cip" / "0.1" / "entity-ref.schema.json"
VERSIONED_ENTITY_PATH = ROOT / "schemas" / "w1-cip" / "0.1" / "versioned-entity.schema.json"
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
EXAMPLES = ROOT / "examples" / "protocol-envelope"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class ProtocolEnvelopeSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = load_json(SCHEMA_PATH)
        cls.principal_ref_schema = load_json(PRINCIPAL_REF_PATH)
        cls.entity_ref_schema = load_json(ENTITY_REF_PATH)
        cls.versioned_entity_schema = load_json(VERSIONED_ENTITY_PATH)
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
        Draft202012Validator.check_schema(cls.principal_ref_schema)
        Draft202012Validator.check_schema(cls.entity_ref_schema)
        Draft202012Validator.check_schema(cls.versioned_entity_schema)
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
        registry = registry.with_resource(
            cls.principal_ref_schema["$id"],
            Resource.from_contents(cls.principal_ref_schema),
        )
        registry = registry.with_resource(
            cls.entity_ref_schema["$id"],
            Resource.from_contents(cls.entity_ref_schema),
        )
        registry = registry.with_resource(
            cls.versioned_entity_schema["$id"],
            Resource.from_contents(cls.versioned_entity_schema),
        )
        registry = registry.with_resource(
            cls.role_assignment_schema["$id"],
            Resource.from_contents(cls.role_assignment_schema),
        )
        registry = registry.with_resource(
            cls.agent_card_schema["$id"],
            Resource.from_contents(cls.agent_card_schema),
        )
        registry = registry.with_resource(
            cls.goal_contract_schema["$id"],
            Resource.from_contents(cls.goal_contract_schema),
        )
        registry = registry.with_resource(
            cls.team_plan_schema["$id"],
            Resource.from_contents(cls.team_plan_schema),
        )
        registry = registry.with_resource(
            cls.context_grant_schema["$id"],
            Resource.from_contents(cls.context_grant_schema),
        )
        registry = registry.with_resource(
            cls.task_schema["$id"],
            Resource.from_contents(cls.task_schema),
        )
        registry = registry.with_resource(
            cls.contribution_schema["$id"],
            Resource.from_contents(cls.contribution_schema),
        )
        registry = registry.with_resource(
            cls.evidence_schema["$id"],
            Resource.from_contents(cls.evidence_schema),
        )
        registry = registry.with_resource(
            cls.verification_schema["$id"],
            Resource.from_contents(cls.verification_schema),
        )
        registry = registry.with_resource(
            cls.challenge_schema["$id"],
            Resource.from_contents(cls.challenge_schema),
        )
        registry = registry.with_resource(
            cls.review_schema["$id"],
            Resource.from_contents(cls.review_schema),
        )
        registry = registry.with_resource(
            cls.approval_schema["$id"],
            Resource.from_contents(cls.approval_schema),
        )
        registry = registry.with_resource(
            cls.decision_schema["$id"],
            Resource.from_contents(cls.decision_schema),
        )
        registry = registry.with_resource(
            cls.execution_resource_plan_schema["$id"],
            Resource.from_contents(cls.execution_resource_plan_schema),
        )
        registry = registry.with_resource(
            cls.final_result_schema["$id"],
            Resource.from_contents(cls.final_result_schema),
        )
        registry = registry.with_resource(
            cls.protocol_event_schema["$id"],
            Resource.from_contents(cls.protocol_event_schema),
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
        self.assertTrue(paths, "no valid ProtocolEnvelope examples found")
        for path in paths:
            with self.subTest(path=path.name):
                instance = load_json(path)
                self.assertEqual([], self.structural_errors(instance))
                self.assertEqual([], validate_protocol_envelope_semantics(instance))

    def test_valid_event_sequences_are_unique_within_each_session(self) -> None:
        seen: set[tuple[str, int]] = set()
        for path in sorted((EXAMPLES / "valid").glob("*.json")):
            instance = load_json(path)
            if instance.get("kind") != "event":
                continue
            key = (instance["session_id"], instance["sequence"])
            self.assertNotIn(key, seen, f"duplicate sample event sequence: {key}")
            seen.add(key)

    def test_structurally_invalid_examples(self) -> None:
        paths = sorted((EXAMPLES / "invalid-structural").glob("*.json"))
        self.assertTrue(paths, "no structurally invalid examples found")
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
                self.assertEqual(expected_codes, validate_protocol_envelope_semantics(instance))


if __name__ == "__main__":
    unittest.main()
