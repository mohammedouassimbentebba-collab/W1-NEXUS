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
    ChallengeTargetState,
    ContributionState,
    RoleAssignmentAuthorityState,
    TaskState,
    ChallengeState,
    challenge_reopening_requires_reassessment,
    validate_challenge_against_task_raiser_target_and_resolution,
    validate_challenge_semantics,
    validate_challenge_transition,
    validate_envelope_role_assignment_authority,
    validate_protocol_envelope_semantics,
    validate_task_outputs_against_challenges,
)

SCHEMA_DIR = ROOT / "schemas" / "w1-cip" / "0.1"
CHALLENGE_PATH = SCHEMA_DIR / "challenge.schema.json"
VERSIONED_ENTITY_PATH = SCHEMA_DIR / "versioned-entity.schema.json"
SCHEMA_FILES = sorted(SCHEMA_DIR.glob("*.schema.json"))
EXAMPLES = ROOT / "examples" / "challenge"
VERSIONED = ROOT / "examples" / "versioned-entity" / "valid"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class ChallengeSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = load_json(CHALLENGE_PATH)
        Draft202012Validator.check_schema(cls.schema)
        registry = Registry()
        for path in SCHEMA_FILES:
            schema = load_json(path)
            registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))
        cls.registry = registry
        cls.validator = Draft202012Validator(
            cls.schema, registry=registry, format_checker=FormatChecker()
        )

    def structural_errors(self, instance: dict) -> list:
        return sorted(self.validator.iter_errors(instance), key=lambda error: list(error.path))

    def build_states(self):
        reviewer = load_json(VERSIONED / "role-assignment-reviewer-v1.json")
        executor = load_json(VERSIONED / "role-assignment-executor-v1.json")
        reviewer_card = load_json(VERSIONED / "agent-card-reviewer-v1.json")
        task = load_json(VERSIONED / "task-review-startup-v4.json")
        claim = load_json(VERSIONED / "contribution-claim-startup-v1.json")
        return (
            TaskState(
                tasks={("task", "task-review-startup-001", 4): task},
                latest_versions={("task", "task-review-startup-001"): 4},
            ),
            RoleAssignmentAuthorityState(
                assignments={
                    ("role_assignment", "role-assignment-reviewer-001", 1): reviewer,
                    ("role_assignment", "role-assignment-executor-001", 1): executor,
                },
                latest_versions={
                    ("role_assignment", "role-assignment-reviewer-001"): 1,
                    ("role_assignment", "role-assignment-executor-001"): 1,
                },
            ),
            AgentCardState(
                cards={("agent_card", "agent-reviewer-01", 1): reviewer_card},
                latest_versions={("agent_card", "agent-reviewer-01"): 1},
            ),
            ChallengeTargetState(
                records={("contribution", "claim-candidate-a-startup-001", 1): claim},
                latest_versions={("contribution", "claim-candidate-a-startup-001"): 1},
            ),
        )

    def test_valid_examples(self) -> None:
        paths = sorted((EXAMPLES / "valid").glob("*.json"))
        self.assertTrue(paths)
        for path in paths:
            with self.subTest(path=path.name):
                payload = load_json(path)
                self.assertEqual([], self.structural_errors(payload))
                self.assertEqual([], validate_challenge_semantics(payload))

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
                self.assertEqual(expected, validate_challenge_semantics(payload))

    def test_versioned_entity_resolves_challenge_schema(self) -> None:
        root_schema = load_json(VERSIONED_ENTITY_PATH)
        validator = Draft202012Validator(
            root_schema, registry=self.registry, format_checker=FormatChecker()
        )
        record = load_json(VERSIONED / "challenge-startup-current-v1.json")
        self.assertEqual([], list(validator.iter_errors(record)))
        wrong = deepcopy(record)
        wrong["entity"]["entity_type"] = "evidence"
        self.assertTrue(list(validator.iter_errors(wrong)))

    def test_protocol_envelope_runs_challenge_semantics_and_author_binding(self) -> None:
        envelope = load_json(ROOT / "examples" / "protocol-envelope" / "valid" / "challenge-create.json")
        self.assertEqual([], validate_protocol_envelope_semantics(envelope))
        wrong = deepcopy(envelope)
        wrong["authorized_by"]["entity_id"] = "role-assignment-verifier-001"
        self.assertIn("challenge_authority_ref_mismatch", validate_protocol_envelope_semantics(wrong))

    def test_valid_initial_challenge_binding(self) -> None:
        record = load_json(VERSIONED / "challenge-startup-current-v1.json")
        task_state, role_state, card_state, target_state = self.build_states()
        self.assertEqual(
            [],
            validate_challenge_against_task_raiser_target_and_resolution(
                record,
                task_state,
                role_state,
                target_state,
                evaluated_at="2026-08-04T15:21:01Z",
                agent_card_state=card_state,
            ),
        )

    def test_target_author_cannot_close_challenge_alone(self) -> None:
        record = load_json(VERSIONED / "challenge-startup-current-v4.json")
        task_state, role_state, _, target_state = self.build_states()
        bad = deepcopy(record)
        executor_ref = {
            "entity_type": "role_assignment",
            "entity_id": "role-assignment-executor-001",
            "entity_version": 1,
        }
        bad["legal_payload"]["last_transition_by_role_assignment"] = executor_ref
        bad["legal_payload"]["resolution"]["resolved_by_role_assignment"] = executor_ref
        errors = validate_challenge_against_task_raiser_target_and_resolution(
            bad,
            task_state,
            role_state,
            target_state,
            evaluated_at="2026-08-04T15:24:01Z",
        )
        self.assertIn("challenge_target_author_cannot_close_alone", errors)

    def test_rejection_requires_independent_authority(self) -> None:
        record = deepcopy(load_json(VERSIONED / "challenge-startup-current-v2.json"))
        payload = record["legal_payload"]
        payload["status"] = "rejected"
        payload.pop("status_reason", None)
        payload["resolution"] = {
            "resolved_by_role_assignment": payload["last_transition_by_role_assignment"],
            "resolved_at": payload["last_transition_at"],
            "rationale": "The challenger attempted to reject its own objection.",
            "basis_refs": [],
        }
        task_state, role_state, _, target_state = self.build_states()
        errors = validate_challenge_against_task_raiser_target_and_resolution(
            record,
            task_state,
            role_state,
            target_state,
            evaluated_at="2026-08-04T15:22:01Z",
        )
        self.assertIn("challenge_rejection_not_independent", errors)

    def test_stale_target_and_classification_floor_are_rejected(self) -> None:
        record = deepcopy(load_json(VERSIONED / "challenge-startup-current-v1.json"))
        task_state, role_state, card_state, target_state = self.build_states()
        target = deepcopy(next(iter(target_state.records.values())))
        target["legal_payload"]["classification"] = "confidential"
        stale = ChallengeTargetState(
            records={("contribution", "claim-candidate-a-startup-001", 1): target},
            latest_versions={("contribution", "claim-candidate-a-startup-001"): 2},
        )
        errors = validate_challenge_against_task_raiser_target_and_resolution(
            record,
            task_state,
            role_state,
            stale,
            evaluated_at="2026-08-04T15:21:01Z",
            agent_card_state=card_state,
        )
        self.assertIn("challenge_target_version_not_current", errors)
        self.assertIn("challenge_classification_below_target", errors)

    def test_lifecycle_and_critical_reopening_trigger(self) -> None:
        versions = [load_json(VERSIONED / f"challenge-startup-current-v{i}.json") for i in range(1, 6)]
        self.assertEqual([], validate_challenge_transition(versions[0], None))
        for previous, current in zip(versions, versions[1:]):
            self.assertEqual([], validate_challenge_transition(current, previous))
        self.assertTrue(challenge_reopening_requires_reassessment(versions[4], versions[3]))
        invalid = deepcopy(versions[1])
        invalid["legal_payload"]["status"] = "resolved"
        invalid["legal_payload"].pop("status_reason", None)
        invalid["legal_payload"]["resolution"] = {
            "resolved_by_role_assignment": invalid["legal_payload"]["last_transition_by_role_assignment"],
            "resolved_at": invalid["legal_payload"]["last_transition_at"],
            "rationale": "Invalid direct transition.",
            "basis_refs": [],
        }
        self.assertIn("challenge_status_transition_invalid", validate_challenge_transition(invalid, versions[0]))


    def test_escalation_can_return_to_higher_authority_processing(self) -> None:
        v1 = load_json(VERSIONED / "challenge-startup-current-v1.json")
        escalated = deepcopy(load_json(VERSIONED / "challenge-startup-current-v2.json"))
        escalated["legal_payload"]["status"] = "escalated"
        escalated["legal_payload"]["status_reason"] = "Escalated to a higher review authority."
        requested = deepcopy(load_json(VERSIONED / "challenge-startup-current-v3.json"))
        requested["previous_version"] = escalated["entity"]
        self.assertEqual([], validate_challenge_transition(escalated, v1))
        self.assertEqual([], validate_challenge_transition(requested, escalated))

    def test_completed_task_resolves_challenge_output(self) -> None:
        task = load_json(VERSIONED / "task-review-startup-v5.json")
        challenge = load_json(VERSIONED / "challenge-startup-current-v1.json")
        state = ChallengeState(
            challenges={("challenge", "challenge-startup-current-001", 1): challenge},
            latest_versions={("challenge", "challenge-startup-current-001"): 1},
        )
        self.assertEqual([], validate_task_outputs_against_challenges(task, state))
        wrong = deepcopy(challenge)
        wrong["legal_payload"]["fulfills_output_id"] = "other-output"
        bad_state = ChallengeState(
            challenges={("challenge", "challenge-startup-current-001", 1): wrong},
            latest_versions={("challenge", "challenge-startup-current-001"): 1},
        )
        self.assertIn(
            "task_challenge_output_binding_mismatch",
            validate_task_outputs_against_challenges(task, bad_state),
        )

    def test_challenge_authority_is_valid_for_reference_event(self) -> None:
        envelope = load_json(ROOT / "examples" / "protocol-envelope" / "valid" / "challenge-create.json")
        _, role_state, card_state, _ = self.build_states()
        self.assertEqual(
            [],
            validate_envelope_role_assignment_authority(
                envelope, role_state, agent_card_state=card_state
            ),
        )


if __name__ == "__main__":
    unittest.main()
