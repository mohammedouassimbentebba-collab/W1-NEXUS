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
    ContributionState,
    EvidenceState,
    RoleAssignmentAuthorityState,
    TaskState,
    TeamPlanState,
    validate_envelope_role_assignment_authority,
    validate_protocol_envelope_semantics,
    validate_verification_against_task_method_claims_and_evidence,
    validate_verification_semantics,
    validate_verification_transition,
)

SCHEMA_DIR = ROOT / "schemas" / "w1-cip" / "0.1"
VERIFICATION_PATH = SCHEMA_DIR / "verification.schema.json"
VERSIONED_ENTITY_PATH = SCHEMA_DIR / "versioned-entity.schema.json"
SCHEMA_FILES = [
    SCHEMA_DIR / "entity-ref.schema.json",
    SCHEMA_DIR / "principal-ref.schema.json",
    SCHEMA_DIR / "role-assignment.schema.json",
    SCHEMA_DIR / "agent-card.schema.json",
    SCHEMA_DIR / "goal-contract.schema.json",
    SCHEMA_DIR / "team-plan.schema.json",
    SCHEMA_DIR / "context-grant.schema.json",
    SCHEMA_DIR / "task.schema.json",
    SCHEMA_DIR / "contribution.schema.json",
    SCHEMA_DIR / "evidence.schema.json",
    VERIFICATION_PATH,
]
EXAMPLES = ROOT / "examples" / "verification"
VERSIONED = ROOT / "examples" / "versioned-entity" / "valid"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class VerificationSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = load_json(VERIFICATION_PATH)
        Draft202012Validator.check_schema(cls.schema)
        registry = Registry()
        for path in SCHEMA_FILES[:-1]:
            schema = load_json(path)
            Draft202012Validator.check_schema(schema)
            registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))
        cls.validator = Draft202012Validator(
            cls.schema, registry=registry, format_checker=FormatChecker()
        )

    def structural_errors(self, instance: dict) -> list:
        return sorted(self.validator.iter_errors(instance), key=lambda error: list(error.path))

    def build_states(self):
        tasks = {
            ("task", "task-verify-startup-001", version): load_json(
                VERSIONED / f"task-verify-startup-v{version}.json"
            )
            for version in range(1, 5)
        }
        role = load_json(VERSIONED / "role-assignment-verifier-v1.json")
        executor_role = load_json(VERSIONED / "role-assignment-executor-v1.json")
        card = load_json(VERSIONED / "agent-card-verifier-v1.json")
        claim = load_json(VERSIONED / "contribution-claim-startup-v1.json")
        evidence = load_json(VERSIONED / "evidence-startup-current-v1.json")
        plan = load_json(VERSIONED / "team-plan-pump-v1.json")
        return (
            TaskState(tasks=tasks, latest_versions={("task", "task-verify-startup-001"): 4}),
            RoleAssignmentAuthorityState(
                assignments={
                    ("role_assignment", "role-assignment-verifier-001", 1): role,
                    ("role_assignment", "role-assignment-executor-001", 1): executor_role,
                },
                latest_versions={
                    ("role_assignment", "role-assignment-verifier-001"): 1,
                    ("role_assignment", "role-assignment-executor-001"): 1,
                },
            ),
            AgentCardState(
                cards={("agent_card", "agent-verifier-01", 1): card},
                latest_versions={("agent_card", "agent-verifier-01"): 1},
            ),
            ContributionState(
                contributions={("contribution", "claim-candidate-a-startup-001", 1): claim},
                latest_versions={("contribution", "claim-candidate-a-startup-001"): 1},
            ),
            EvidenceState(
                evidence={("evidence", "startup-current-capture-001", 1): evidence},
                latest_versions={("evidence", "startup-current-capture-001"): 1},
            ),
            TeamPlanState(
                plans={("team_plan", "team-plan-pump-001", 1): plan},
                latest_versions={("team_plan", "team-plan-pump-001"): 1},
            ),
        )

    def test_valid_examples(self) -> None:
        paths = sorted((EXAMPLES / "valid").glob("*.json"))
        self.assertTrue(paths)
        for path in paths:
            with self.subTest(path=path.name):
                payload = load_json(path)
                self.assertEqual([], self.structural_errors(payload))
                self.assertEqual([], validate_verification_semantics(payload))

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
                self.assertEqual(expected, validate_verification_semantics(payload))

    def test_versioned_entity_resolves_verification_schema(self) -> None:
        root_schema = load_json(VERSIONED_ENTITY_PATH)
        registry = Registry()
        for path in SCHEMA_FILES:
            schema = load_json(path)
            registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))
        validator = Draft202012Validator(root_schema, registry=registry, format_checker=FormatChecker())
        record = load_json(VERSIONED / "verification-startup-current-v1.json")
        self.assertEqual([], list(validator.iter_errors(record)))
        wrong = deepcopy(record)
        wrong["entity"]["entity_type"] = "evidence"
        self.assertTrue(list(validator.iter_errors(wrong)))

    def test_protocol_envelope_runs_verification_semantics_and_author_binding(self) -> None:
        envelope = load_json(ROOT / "examples" / "protocol-envelope" / "valid" / "verification-create.json")
        self.assertEqual([], validate_protocol_envelope_semantics(envelope))
        wrong = deepcopy(envelope)
        wrong["authorized_by"]["entity_id"] = "role-assignment-other-001"
        self.assertIn("verification_authority_ref_mismatch", validate_protocol_envelope_semantics(wrong))

    def test_valid_task_verifier_claim_evidence_and_method_binding(self) -> None:
        record = load_json(VERSIONED / "verification-startup-current-v1.json")
        task_state, role_state, card_state, contribution_state, evidence_state, plan_state = self.build_states()
        self.assertEqual(
            [],
            validate_verification_against_task_method_claims_and_evidence(
                record, task_state, role_state, contribution_state, evidence_state,
                evaluated_at="2026-08-04T15:20:04Z", agent_card_state=card_state,
                team_plan_state=plan_state,
            ),
        )

    def test_rejects_stale_or_unrelated_evidence_and_wrong_method(self) -> None:
        record = load_json(VERSIONED / "verification-startup-current-v1.json")
        task_state, role_state, card_state, contribution_state, evidence_state, plan_state = self.build_states()
        bad = deepcopy(record)
        bad["legal_payload"]["method"]["type"] = "source_cross_check"
        unrelated = deepcopy(next(iter(evidence_state.evidence.values())))
        unrelated["legal_payload"]["targets"] = [{"entity_type":"contribution","entity_id":"claim-other","entity_version":1}]
        unrelated_state = EvidenceState(
            evidence={("evidence", "startup-current-capture-001", 1): unrelated},
            latest_versions={("evidence", "startup-current-capture-001"): 1},
        )
        errors = validate_verification_against_task_method_claims_and_evidence(
            bad, task_state, role_state, contribution_state, unrelated_state,
            evaluated_at="2026-08-04T15:20:04Z", agent_card_state=card_state,
            team_plan_state=plan_state,
        )
        self.assertIn("verification_method_incompatible_with_claim", errors)
        self.assertIn("verification_evidence_not_relevant_to_targets", errors)
        self.assertIn("verification_target_not_covered_by_evidence", errors)

    def test_conclusive_requires_verified_evidence_and_classification_floor(self) -> None:
        record = load_json(VERSIONED / "verification-startup-current-v1.json")
        task_state, role_state, card_state, contribution_state, evidence_state, plan_state = self.build_states()
        ev = deepcopy(next(iter(evidence_state.evidence.values())))
        ev["legal_payload"]["integrity"] = {"status":"unverified"}
        ev["legal_payload"]["classification"] = "confidential"
        state = EvidenceState(
            evidence={("evidence", "startup-current-capture-001", 1): ev},
            latest_versions={("evidence", "startup-current-capture-001"): 1},
        )
        errors = validate_verification_against_task_method_claims_and_evidence(
            record, task_state, role_state, contribution_state, state,
            evaluated_at="2026-08-04T15:20:04Z", agent_card_state=card_state,
            team_plan_state=plan_state,
        )
        self.assertIn("verification_conclusive_evidence_not_verified", errors)
        self.assertIn("verification_classification_below_evidence", errors)

    def test_rejects_nonindependent_verifier(self) -> None:
        record = load_json(VERSIONED / "verification-startup-current-v1.json")
        task_state, role_state, card_state, contribution_state, evidence_state, plan_state = self.build_states()
        verifier = deepcopy(role_state.assignments[("role_assignment", "role-assignment-verifier-001", 1)])
        executor = role_state.assignments[("role_assignment", "role-assignment-executor-001", 1)]
        verifier["legal_payload"]["subject"] = executor["legal_payload"]["subject"]
        bad_roles = RoleAssignmentAuthorityState(
            assignments={**role_state.assignments, ("role_assignment", "role-assignment-verifier-001", 1): verifier},
            latest_versions=role_state.latest_versions,
        )
        errors = validate_verification_against_task_method_claims_and_evidence(
            record, task_state, bad_roles, contribution_state, evidence_state,
            evaluated_at="2026-08-04T15:20:04Z", agent_card_state=None,
            team_plan_state=plan_state,
        )
        self.assertIn("verification_performer_not_independent_from_claim_author", errors)

    def test_transition_preserves_run_and_withdrawal_is_final(self) -> None:
        v1 = load_json(VERSIONED / "verification-startup-current-v1.json")
        v2 = load_json(VERSIONED / "verification-startup-current-v2-withdrawn.json")
        self.assertEqual([], validate_verification_transition(v1, None))
        self.assertEqual([], validate_verification_transition(v2, v1))
        changed = deepcopy(v2)
        changed["legal_payload"]["result"] = "passed"
        self.assertIn("verification_result_changed", validate_verification_transition(changed, v1))
        self.assertIn("verification_withdrawal_changed_content", validate_verification_transition(changed, v1))
        reactivated = deepcopy(v2)
        reactivated["entity"]["entity_version"] = 3
        reactivated["previous_version"] = v2["entity"]
        reactivated["legal_payload"]["status"] = "active"
        reactivated["legal_payload"].pop("withdrawal_reason", None)
        self.assertIn("verification_withdrawal_final", validate_verification_transition(reactivated, v2))

    def test_verification_authority_is_valid_for_reference_event(self) -> None:
        envelope = load_json(ROOT / "examples" / "protocol-envelope" / "valid" / "verification-create.json")
        _, role_state, card_state, _, _, _ = self.build_states()
        self.assertEqual([], validate_envelope_role_assignment_authority(envelope, role_state, agent_card_state=card_state))


if __name__ == "__main__":
    unittest.main()
