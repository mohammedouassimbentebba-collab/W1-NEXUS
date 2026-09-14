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
    FINAL_RESULT_PAYLOAD_SCHEMA,
    ApprovalState,
    ChallengeState,
    ContributionState,
    DecisionState,
    EvidenceState,
    ExecutionResourcePlanState,
    GoalContractState,
    ReviewState,
    RoleAssignmentAuthorityState,
    TaskState,
    TeamPlanState,
    VerificationState,
    validate_final_result_against_state,
    validate_final_result_semantics,
    validate_final_result_transition,
    validate_protocol_envelope_semantics,
    validate_role_assignment_semantics,
)

SCHEMAS = ROOT / "schemas" / "w1-cip" / "0.1"
EXAMPLES = ROOT / "examples"
VALID = EXAMPLES / "final-result" / "valid"
VERSIONED = EXAMPLES / "versioned-entity" / "valid"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class FinalResultSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = load_json(SCHEMAS / "final-result.schema.json")
        Draft202012Validator.check_schema(cls.schema)
        entity_ref = load_json(SCHEMAS / "entity-ref.schema.json")
        registry = Registry().with_resource(entity_ref["$id"], Resource.from_contents(entity_ref))
        cls.validator = Draft202012Validator(
            cls.schema, registry=registry, format_checker=FormatChecker()
        )
        cls.result = load_json(VALID / "pump-success.json")

    def structural_errors(self, value: dict) -> list:
        return list(self.validator.iter_errors(value))

    def test_valid_published_result(self) -> None:
        self.assertEqual([], self.structural_errors(self.result))
        self.assertEqual([], validate_final_result_semantics(self.result))
        self.assertEqual("succeeded", self.result["result_status"])
        self.assertEqual(1, len(self.result["refuted_claims"]))

    def test_succeeded_result_cannot_hide_uncertainty(self) -> None:
        bad = deepcopy(self.result)
        bad["unverified_claims"] = [{
            "claim": {"entity_type": "contribution", "entity_id": "claim-unknown-001", "entity_version": 1},
            "reason_code": "missing_evidence",
            "description": "No evidence was supplied.",
        }]
        self.assertTrue(self.structural_errors(bad))

    def test_semantics_separates_verified_refuted_and_unverified_claims(self) -> None:
        bad = deepcopy(self.result)
        bad["refuted_claims"].append(deepcopy(bad["verified_claims"][0]))
        self.assertIn(
            "final_result_claim_both_verified_and_refuted",
            validate_final_result_semantics(bad),
        )

    def test_semantics_rejects_false_success_and_bad_log_anchor(self) -> None:
        bad = deepcopy(self.result)
        bad["deliverable_assessments"][0]["outcome"] = "partially_satisfied"
        bad["log_anchor"]["event_count"] = 38
        errors = validate_final_result_semantics(bad)
        self.assertIn("final_result_succeeded_with_incomplete_deliverable", errors)
        self.assertIn("final_result_log_count_sequence_mismatch", errors)

    def test_resource_fallback_must_be_disclosed_consistently(self) -> None:
        bad = deepcopy(self.result)
        disclosure = bad["resource_summary"]["routing_disclosures"][0]
        disclosure["fallback_from_resource_id"] = "sol-resource"
        disclosure["activation_reason"] = "initial_selection"
        errors = validate_final_result_semantics(bad)
        self.assertIn("final_result_invalid_fallback_origin", errors)
        self.assertIn("final_result_fallback_requires_noninitial_reason", errors)

    def test_transition_marks_reassessment_without_rewriting_result(self) -> None:
        previous = load_json(VERSIONED / "final-result-pump-v1.json")
        current = deepcopy(previous)
        current["entity"]["entity_version"] = 2
        current["created_by_event_id"] = "evt-final-result-reassessment-002"
        current["previous_version"] = {
            "entity_type": "final_result",
            "entity_id": "final-result-pump-001",
            "entity_version": 1,
        }
        current["change_metadata"] = {
            "change_reason": "semantic",
            "substantive_effect": "may_affect_dependents",
        }
        current["legal_payload"]["lifecycle_status"] = "reassessment_required"
        current["legal_payload"]["reassessment"] = {
            "reason": "A critical challenge was reopened after publication.",
            "trigger_refs": [{
                "entity_type": "protocol_event",
                "entity_id": "evt-decision-reassessment-required-001",
                "entity_version": 1,
            }],
            "marked_at": "2026-08-05T16:10:00Z",
            "marked_by_role_assignment": {
                "entity_type": "role_assignment",
                "entity_id": "role-assignment-synthesizer-001",
                "entity_version": 1,
            },
        }
        self.assertEqual([], validate_final_result_transition(current, previous))
        current["legal_payload"]["summary"] = "Silently rewritten summary."
        self.assertIn(
            "final_result_summary_changed",
            validate_final_result_transition(current, previous),
        )

    def build_states(self):
        def one(name: str) -> dict:
            return load_json(VERSIONED / name)

        goal = one("goal-contract-pump-v1.json")
        team = one("team-plan-pump-v2.json")
        task = one("task-final-result-v4.json")
        role = one("role-assignment-synthesizer-v1.json")
        decision = one("decision-motor-driver-v3.json")
        contributions = [
            one("contribution-claim-candidate-b-v1.json"),
            one("contribution-claim-startup-v1.json"),
        ]
        evidence = [one("evidence-candidate-b-spec-v1.json"), one("evidence-startup-current-v1.json")]
        verifications = [
            one("verification-candidate-b-startup-v1.json"),
            one("verification-startup-current-v1.json"),
        ]
        review = one("review-candidate-b-package-v1.json")
        challenge = one("challenge-startup-current-v4.json")
        resource = one("execution-resource-plan-pump-v2.json")

        def key(record: dict):
            e = record["entity"]
            return e["entity_type"], e["entity_id"], e["entity_version"]

        def latest(records: list[dict]):
            return {(r["entity"]["entity_type"], r["entity"]["entity_id"]): r["entity"]["entity_version"] for r in records}

        return (
            GoalContractState({key(goal): goal}, latest([goal])),
            TeamPlanState({key(team): team}, latest([team])),
            TaskState({key(task): task}, latest([task])),
            RoleAssignmentAuthorityState({key(role): role}, latest([role])),
            DecisionState({key(decision): decision}, latest([decision])),
            ContributionState({key(r): r for r in contributions}, latest(contributions)),
            EvidenceState({key(r): r for r in evidence}, latest(evidence)),
            VerificationState({key(r): r for r in verifications}, latest(verifications)),
            ReviewState({key(review): review}, latest([review])),
            ChallengeState({key(challenge): challenge}, latest([challenge])),
            ApprovalState(),
            ExecutionResourcePlanState({key(resource): resource}, latest([resource])),
        )

    def test_result_resolves_current_normative_sources(self) -> None:
        states = self.build_states()
        self.assertEqual(
            [],
            validate_final_result_against_state(
                self.result,
                *states,
                generated_at=self.result["generated_at"],
            ),
        )

    def test_state_rejects_unpassed_verified_claim_and_quota_mismatch(self) -> None:
        states = self.build_states()
        bad = deepcopy(self.result)
        bad["verified_claims"] = deepcopy(bad["refuted_claims"])
        bad["refuted_claims"] = []
        bad["resource_summary"]["resource_outcomes"][0]["final_quota_status"] = "available"
        errors = validate_final_result_against_state(
            bad, *states, generated_at=bad["generated_at"]
        )
        self.assertIn("final_result_verified_claim_not_conclusively_passed", errors)
        self.assertIn("final_result_resource_quota_status_mismatch", errors)

    def test_synthesizer_authority_is_required(self) -> None:
        role = load_json(VERSIONED / "role-assignment-synthesizer-v1.json")["legal_payload"]
        self.assertEqual([], validate_role_assignment_semantics(role))
        bad = deepcopy(role)
        bad["role"] = "executor"
        self.assertIn(
            "final_result_management_requires_synthesizer_or_owner",
            validate_role_assignment_semantics(bad),
        )

    def test_protocol_envelope_binds_authority_to_generator(self) -> None:
        envelope = load_json(EXAMPLES / "protocol-envelope" / "valid" / "final-result-created.json")
        self.assertEqual([], validate_protocol_envelope_semantics(envelope))
        bad = deepcopy(envelope)
        bad["authorized_by"]["entity_id"] = "role-assignment-decision-001"
        self.assertIn("final_result_authority_ref_mismatch", validate_protocol_envelope_semantics(bad))

    def test_versioned_record_uses_final_result_payload_schema(self) -> None:
        record = load_json(VERSIONED / "final-result-pump-v1.json")
        self.assertEqual(FINAL_RESULT_PAYLOAD_SCHEMA, record["legal_payload_schema"])


if __name__ == "__main__":
    unittest.main()
