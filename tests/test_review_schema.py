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
    ChallengeState,
    EvidenceState,
    ReviewState,
    ReviewTargetState,
    RoleAssignmentAuthorityState,
    TaskState,
    VerificationState,
    validate_envelope_role_assignment_authority,
    validate_protocol_envelope_semantics,
    validate_review_against_task_reviewer_and_sources,
    validate_review_semantics,
    validate_review_transition,
    validate_task_outputs_against_reviews,
)

SCHEMA_DIR = ROOT / "schemas" / "w1-cip" / "0.1"
REVIEW_PATH = SCHEMA_DIR / "review.schema.json"
VERSIONED_ENTITY_PATH = SCHEMA_DIR / "versioned-entity.schema.json"
SCHEMA_FILES = sorted(SCHEMA_DIR.glob("*.schema.json"))
EXAMPLES = ROOT / "examples" / "review"
VERSIONED = ROOT / "examples" / "versioned-entity" / "valid"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class ReviewSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = load_json(REVIEW_PATH)
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
        proposal = load_json(VERSIONED / "contribution-proposal-v1.json")
        claim = load_json(VERSIONED / "contribution-claim-startup-v1.json")
        evidence = load_json(VERSIONED / "evidence-startup-current-v1.json")
        verification = load_json(VERSIONED / "verification-startup-current-v1.json")
        challenge = load_json(VERSIONED / "challenge-startup-current-v1.json")
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
            ReviewTargetState(
                records={
                    ("contribution", "proposal-driver-selection-001", 1): proposal,
                    ("contribution", "claim-candidate-a-startup-001", 1): claim,
                },
                latest_versions={
                    ("contribution", "proposal-driver-selection-001"): 1,
                    ("contribution", "claim-candidate-a-startup-001"): 1,
                },
            ),
            EvidenceState(
                evidence={("evidence", "startup-current-capture-001", 1): evidence},
                latest_versions={("evidence", "startup-current-capture-001"): 1},
            ),
            VerificationState(
                verifications={
                    ("verification", "verification-startup-current-001", 1): verification
                },
                latest_versions={("verification", "verification-startup-current-001"): 1},
            ),
            ChallengeState(
                challenges={
                    ("challenge", "challenge-startup-current-001", 1): challenge
                },
                latest_versions={("challenge", "challenge-startup-current-001"): 1},
            ),
        )

    def test_valid_examples(self) -> None:
        paths = sorted((EXAMPLES / "valid").glob("*.json"))
        self.assertTrue(paths)
        for path in paths:
            with self.subTest(path=path.name):
                payload = load_json(path)
                self.assertEqual([], self.structural_errors(payload))
                self.assertEqual([], validate_review_semantics(payload))

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
                self.assertEqual(expected, validate_review_semantics(payload))

    def test_versioned_entity_resolves_review_schema(self) -> None:
        root_schema = load_json(VERSIONED_ENTITY_PATH)
        validator = Draft202012Validator(
            root_schema, registry=self.registry, format_checker=FormatChecker()
        )
        record = load_json(VERSIONED / "review-startup-current-v1.json")
        self.assertEqual([], list(validator.iter_errors(record)))
        wrong = deepcopy(record)
        wrong["entity"]["entity_type"] = "challenge"
        self.assertTrue(list(validator.iter_errors(wrong)))

    def test_protocol_envelope_runs_review_semantics_and_author_binding(self) -> None:
        envelope = load_json(ROOT / "examples" / "protocol-envelope" / "valid" / "review-create.json")
        self.assertEqual([], validate_protocol_envelope_semantics(envelope))
        wrong = deepcopy(envelope)
        wrong["authorized_by"]["entity_id"] = "role-assignment-executor-001"
        self.assertIn("review_authority_ref_mismatch", validate_protocol_envelope_semantics(wrong))

    def test_valid_review_binds_task_reviewer_and_sources(self) -> None:
        record = load_json(VERSIONED / "review-startup-current-v1.json")
        states = self.build_states()
        self.assertEqual(
            [],
            validate_review_against_task_reviewer_and_sources(
                record,
                states[0], states[1], states[3], states[4], states[5], states[6],
                evaluated_at="2026-08-04T15:21:31Z",
                agent_card_state=states[2],
            ),
        )

    def test_review_must_be_independent_from_target_author(self) -> None:
        record = load_json(VERSIONED / "review-startup-current-v1.json")
        states = list(self.build_states())
        executor = states[1].assignments[("role_assignment", "role-assignment-executor-001", 1)]
        bad_reviewer = deepcopy(
            states[1].assignments[("role_assignment", "role-assignment-reviewer-001", 1)]
        )
        bad_reviewer["legal_payload"]["subject"] = deepcopy(
            executor["legal_payload"]["subject"]
        )
        states[1] = RoleAssignmentAuthorityState(
            assignments={
                ("role_assignment", "role-assignment-reviewer-001", 1): bad_reviewer,
                ("role_assignment", "role-assignment-executor-001", 1): executor,
            },
            latest_versions={
                ("role_assignment", "role-assignment-reviewer-001"): 1,
                ("role_assignment", "role-assignment-executor-001"): 1,
            },
        )
        errors = validate_review_against_task_reviewer_and_sources(
            record,
            states[0], states[1], states[3], states[4], states[5], states[6],
            evaluated_at="2026-08-04T15:21:31Z",
        )
        self.assertIn("review_not_independent_from_target_author", errors)

    def test_approval_rejects_failed_verification_and_open_critical_challenge(self) -> None:
        record = deepcopy(load_json(VERSIONED / "review-startup-current-v1.json"))
        payload = record["legal_payload"]
        payload["result"] = "approved"
        payload["required_actions"] = []
        for item in payload["criteria_assessments"]:
            item["outcome"] = "satisfied"
            item["severity"] = "informational"
        states = self.build_states()
        errors = validate_review_against_task_reviewer_and_sources(
            record,
            states[0], states[1], states[3], states[4], states[5], states[6],
            evaluated_at="2026-08-04T15:21:31Z",
        )
        self.assertIn("review_approved_with_unresolved_critical_challenge", errors)
        self.assertIn("review_approved_with_nonpassing_verification", errors)
        self.assertIn("review_approved_claim_without_passing_verification", errors)


    def test_stale_target_and_classification_floor_are_rejected(self) -> None:
        record = load_json(VERSIONED / "review-startup-current-v1.json")
        states = list(self.build_states())
        proposal = deepcopy(
            states[3].records[("contribution", "proposal-driver-selection-001", 1)]
        )
        proposal["legal_payload"]["classification"] = "confidential"
        successor = deepcopy(proposal)
        successor["entity"]["entity_version"] = 2
        successor["previous_version"] = {
            "entity_type": "contribution",
            "entity_id": "proposal-driver-selection-001",
            "entity_version": 1,
        }
        successor["change_metadata"] = {
            "change_reason": "semantic",
            "substantive_effect": "may_affect_dependents",
        }
        successor["legal_payload"]["content"]["description"] = (
            "A semantically revised proposal requiring a new review."
        )
        claim = states[3].records[("contribution", "claim-candidate-a-startup-001", 1)]
        states[3] = ReviewTargetState(
            records={
                ("contribution", "proposal-driver-selection-001", 1): proposal,
                ("contribution", "proposal-driver-selection-001", 2): successor,
                ("contribution", "claim-candidate-a-startup-001", 1): claim,
            },
            latest_versions={
                ("contribution", "proposal-driver-selection-001"): 2,
                ("contribution", "claim-candidate-a-startup-001"): 1,
            },
        )
        errors = validate_review_against_task_reviewer_and_sources(
            record,
            states[0], states[1], states[3], states[4], states[5], states[6],
            evaluated_at="2026-08-04T15:21:31Z",
        )
        self.assertIn("review_target_version_not_current", errors)
        self.assertIn("review_classification_below_sources", errors)

    def test_transition_preserves_run_and_withdrawal_is_final(self) -> None:
        first = load_json(VERSIONED / "review-startup-current-v1.json")
        second = load_json(VERSIONED / "review-startup-current-v2-withdrawn.json")
        self.assertEqual([], validate_review_transition(first, None))
        self.assertEqual([], validate_review_transition(second, first))
        changed = deepcopy(second)
        changed["legal_payload"]["result"] = "rejected"
        self.assertIn("review_result_changed", validate_review_transition(changed, first))
        reactivated = deepcopy(second)
        reactivated["entity"]["entity_version"] = 3
        reactivated["previous_version"] = second["entity"]
        reactivated["legal_payload"]["status"] = "active"
        reactivated["legal_payload"].pop("withdrawal_reason", None)
        self.assertIn("review_withdrawal_final", validate_review_transition(reactivated, second))

    def test_completed_task_resolves_review_output(self) -> None:
        task = load_json(VERSIONED / "task-review-startup-v5.json")
        review = load_json(VERSIONED / "review-startup-current-v1.json")
        state = ReviewState(
            reviews={("review", "review-startup-current-001", 1): review},
            latest_versions={("review", "review-startup-current-001"): 1},
        )
        self.assertEqual([], validate_task_outputs_against_reviews(task, state))
        wrong = deepcopy(review)
        wrong["legal_payload"]["fulfills_output_id"] = "other-output"
        bad_state = ReviewState(
            reviews={("review", "review-startup-current-001", 1): wrong},
            latest_versions={("review", "review-startup-current-001"): 1},
        )
        self.assertIn(
            "task_review_output_binding_mismatch",
            validate_task_outputs_against_reviews(task, bad_state),
        )

    def test_review_authority_is_valid_for_reference_event(self) -> None:
        envelope = load_json(ROOT / "examples" / "protocol-envelope" / "valid" / "review-create.json")
        states = self.build_states()
        self.assertEqual(
            [],
            validate_envelope_role_assignment_authority(
                envelope, states[1], agent_card_state=states[2]
            ),
        )


if __name__ == "__main__":
    unittest.main()
