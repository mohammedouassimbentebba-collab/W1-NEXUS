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
    ApprovalState,
    ChallengeState,
    ContributionState,
    DecisionState,
    EvidenceState,
    GoalContractState,
    ReviewState,
    RoleAssignmentAuthorityState,
    TaskState,
    TeamPlanState,
    VerificationState,
    decision_dependency_impacts,
    decisions_requiring_reassessment_for_reopened_challenge,
    validate_decision_against_task_authority_and_sources,
    validate_decision_dependency_graph,
    validate_decision_semantics,
    validate_decision_transition,
    validate_protocol_envelope_semantics,
    validate_task_outputs_against_decisions,
)

SCHEMA_DIR = ROOT / "schemas" / "w1-cip" / "0.1"
DECISION_PATH = SCHEMA_DIR / "decision.schema.json"
VERSIONED_ENTITY_PATH = SCHEMA_DIR / "versioned-entity.schema.json"
SCHEMA_FILES = sorted(SCHEMA_DIR.glob("*.schema.json"))
EXAMPLES = ROOT / "examples" / "decision"
VERSIONED = ROOT / "examples" / "versioned-entity" / "valid"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class DecisionSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = load_json(DECISION_PATH)
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
        task = load_json(VERSIONED / "task-select-driver-decision-v4.json")
        role = load_json(VERSIONED / "role-assignment-decision-v1.json")
        card = load_json(VERSIONED / "agent-card-decision-v1.json")
        goal = load_json(VERSIONED / "goal-contract-pump-v1.json")
        plan = load_json(VERSIONED / "team-plan-pump-v1.json")
        proposal = load_json(VERSIONED / "contribution-proposal-v1.json")
        claim_b = load_json(VERSIONED / "contribution-claim-candidate-b-v1.json")
        evidence_b = load_json(VERSIONED / "evidence-candidate-b-spec-v1.json")
        evidence_a = load_json(VERSIONED / "evidence-startup-current-v1.json")
        verification_b = load_json(VERSIONED / "verification-candidate-b-startup-v1.json")
        verification_a = load_json(VERSIONED / "verification-startup-current-v1.json")
        review = load_json(VERSIONED / "review-candidate-b-package-v1.json")
        challenge = load_json(VERSIONED / "challenge-startup-current-v4.json")
        return {
            "task": TaskState(
                tasks={("task", "task-select-driver-decision-001", 4): task},
                latest_versions={("task", "task-select-driver-decision-001"): 4},
            ),
            "role": RoleAssignmentAuthorityState(
                assignments={("role_assignment", "role-assignment-decision-001", 1): role},
                latest_versions={("role_assignment", "role-assignment-decision-001"): 1},
            ),
            "card": AgentCardState(
                cards={("agent_card", "agent-decision-01", 1): card},
                latest_versions={("agent_card", "agent-decision-01"): 1},
            ),
            "goal": GoalContractState(
                contracts={("goal_contract", "goal-pump-driver-001", 1): goal},
                latest_versions={("goal_contract", "goal-pump-driver-001"): 1},
            ),
            "plan": TeamPlanState(
                plans={("team_plan", "team-plan-pump-001", 1): plan},
                latest_versions={("team_plan", "team-plan-pump-001"): 1},
            ),
            "contribution": ContributionState(
                contributions={
                    ("contribution", "proposal-driver-001", 1): proposal,
                    ("contribution", "claim-candidate-b-startup-001", 1): claim_b,
                },
                latest_versions={
                    ("contribution", "proposal-driver-001"): 1,
                    ("contribution", "claim-candidate-b-startup-001"): 1,
                },
            ),
            "evidence": EvidenceState(
                evidence={
                    ("evidence", "candidate-b-peak-spec-001", 1): evidence_b,
                    ("evidence", "startup-current-capture-001", 1): evidence_a,
                },
                latest_versions={
                    ("evidence", "candidate-b-peak-spec-001"): 1,
                    ("evidence", "startup-current-capture-001"): 1,
                },
            ),
            "verification": VerificationState(
                verifications={
                    ("verification", "verification-candidate-b-startup-001", 1): verification_b,
                    ("verification", "verification-startup-current-001", 1): verification_a,
                },
                latest_versions={
                    ("verification", "verification-candidate-b-startup-001"): 1,
                    ("verification", "verification-startup-current-001"): 1,
                },
            ),
            "review": ReviewState(
                reviews={("review", "review-candidate-b-package-001", 1): review},
                latest_versions={("review", "review-candidate-b-package-001"): 1},
            ),
            "challenge": ChallengeState(
                challenges={("challenge", "challenge-startup-current-001", 4): challenge},
                latest_versions={("challenge", "challenge-startup-current-001"): 4},
            ),
            "approval": ApprovalState(),
            "decision": DecisionState(),
        }

    def test_valid_examples(self) -> None:
        paths = sorted((EXAMPLES / "valid").glob("*.json"))
        self.assertTrue(paths)
        for path in paths:
            with self.subTest(path=path.name):
                instance = load_json(path)
                self.assertEqual([], self.structural_errors(instance))
                self.assertEqual([], validate_decision_semantics(instance))

    def test_structurally_invalid_examples(self) -> None:
        paths = sorted((EXAMPLES / "invalid-structural").glob("*.json"))
        self.assertTrue(paths)
        for path in paths:
            with self.subTest(path=path.name):
                self.assertTrue(self.structural_errors(load_json(path)))

    def test_semantically_invalid_examples(self) -> None:
        directory = EXAMPLES / "invalid-semantic"
        manifest = load_json(directory / "manifest.json")
        for filename, expected_code in manifest.items():
            with self.subTest(path=filename):
                instance = load_json(directory / filename)
                self.assertEqual([], self.structural_errors(instance))
                self.assertIn(expected_code, validate_decision_semantics(instance))

    def test_versioned_entity_resolves_decision_schema(self) -> None:
        schema = load_json(VERSIONED_ENTITY_PATH)
        validator = Draft202012Validator(
            schema, registry=self.registry, format_checker=FormatChecker()
        )
        record = load_json(VERSIONED / "decision-motor-driver-v3.json")
        self.assertEqual([], list(validator.iter_errors(record)))
        wrong = deepcopy(record)
        wrong["entity"]["entity_type"] = "review"
        self.assertTrue(list(validator.iter_errors(wrong)))

    def test_protocol_envelope_runs_decision_semantics_and_author_binding(self) -> None:
        envelope = load_json(ROOT / "examples" / "protocol-envelope" / "valid" / "decision-accepted.json")
        self.assertEqual([], validate_protocol_envelope_semantics(envelope))
        wrong = deepcopy(envelope)
        wrong["authorized_by"]["entity_id"] = "role-assignment-reviewer-001"
        self.assertIn("decision_authority_ref_mismatch", validate_protocol_envelope_semantics(wrong))

    def test_lifecycle_proposed_under_review_accepted(self) -> None:
        v1 = load_json(VERSIONED / "decision-motor-driver-v1.json")
        v2 = load_json(VERSIONED / "decision-motor-driver-v2.json")
        v3 = load_json(VERSIONED / "decision-motor-driver-v3.json")
        self.assertEqual([], validate_decision_transition(v1, None))
        self.assertEqual([], validate_decision_transition(v2, v1))
        self.assertEqual([], validate_decision_transition(v3, v2))
        bad = deepcopy(v3)
        bad["legal_payload"]["selected_option"] = "candidate-a"
        self.assertIn("decision_selected_option_unknown", validate_decision_semantics({**bad["legal_payload"], "options_considered": []}))

    def test_accepted_decision_binds_authority_policy_and_sources(self) -> None:
        record = load_json(VERSIONED / "decision-motor-driver-v3.json")
        st = self.build_states()
        self.assertEqual(
            [],
            validate_decision_against_task_authority_and_sources(
                record, st["task"], st["role"], st["goal"], st["plan"],
                st["contribution"], st["evidence"], st["verification"],
                st["review"], st["challenge"], st["approval"], st["decision"],
                evaluated_at="2026-08-04T15:31:01Z", agent_card_state=st["card"],
            ),
        )

    def test_accepted_decision_rejects_unresolved_critical_and_unapproved_review(self) -> None:
        record = load_json(VERSIONED / "decision-motor-driver-v3.json")
        st = self.build_states()
        open_challenge = load_json(VERSIONED / "challenge-startup-current-v1.json")
        st["challenge"] = ChallengeState(
            challenges={("challenge", "challenge-startup-current-001", 1): open_challenge},
            latest_versions={("challenge", "challenge-startup-current-001"): 1},
        )
        record["legal_payload"]["addressed_challenges"] = []
        record["legal_payload"]["unresolved_challenges"] = [open_challenge["entity"]]
        review = deepcopy(st["review"].reviews[("review", "review-candidate-b-package-001", 1)])
        review["legal_payload"]["result"] = "revision_required"
        st["review"] = ReviewState(
            reviews={("review", "review-candidate-b-package-001", 1): review},
            latest_versions={("review", "review-candidate-b-package-001"): 1},
        )
        errors = validate_decision_against_task_authority_and_sources(
            record, st["task"], st["role"], st["goal"], st["plan"],
            st["contribution"], st["evidence"], st["verification"],
            st["review"], st["challenge"], st["approval"], st["decision"],
            evaluated_at="2026-08-04T15:31:01Z", agent_card_state=st["card"],
        )
        self.assertIn("decision_accepted_with_unresolved_critical_challenge", errors)
        self.assertIn("decision_accepted_without_approved_review", errors)

    def test_dependency_cycle_is_rejected(self) -> None:
        a = load_json(VERSIONED / "decision-motor-driver-v3.json")
        b = deepcopy(a)
        b["entity"] = {"entity_type": "decision", "entity_id": "decision-dependent-001", "entity_version": 1}
        b["legal_payload"]["depends_on_decisions"] = [{
            "decision": a["entity"], "critical": True,
            "on_revoked": "invalidate", "on_superseded": "reassess", "on_reassessment": "reassess",
        }]
        a["legal_payload"]["depends_on_decisions"] = [{
            "decision": b["entity"], "critical": True,
            "on_revoked": "invalidate", "on_superseded": "reassess", "on_reassessment": "reassess",
        }]
        state = DecisionState(
            decisions={("decision", "decision-dependent-001", 1): b},
            latest_versions={("decision", "decision-dependent-001"): 1},
        )
        self.assertIn("decision_dependency_cycle", validate_decision_dependency_graph(a, state))

    def test_dependency_impacts_and_reopened_challenge_propagate(self) -> None:
        base = load_json(VERSIONED / "decision-motor-driver-v3.json")
        dependent = deepcopy(base)
        dependent["entity"] = {"entity_type": "decision", "entity_id": "decision-dependent-001", "entity_version": 1}
        dependent["legal_payload"]["depends_on_decisions"] = [{
            "decision": base["entity"], "critical": True,
            "on_revoked": "invalidate", "on_superseded": "reassess", "on_reassessment": "reassess",
        }]
        state = DecisionState(
            decisions={
                ("decision", "decision-motor-driver-001", 3): base,
                ("decision", "decision-dependent-001", 1): dependent,
            },
            latest_versions={
                ("decision", "decision-motor-driver-001"): 3,
                ("decision", "decision-dependent-001"): 1,
            },
        )
        impacts = decision_dependency_impacts(base, state, change="revoked")
        self.assertEqual("invalidate", impacts[("decision", "decision-dependent-001")])
        reopened = load_json(VERSIONED / "challenge-startup-current-v5.json")
        affected = decisions_requiring_reassessment_for_reopened_challenge(reopened, state)
        self.assertIn(("decision", "decision-motor-driver-001"), affected)
        self.assertIn(("decision", "decision-dependent-001"), affected)

    def test_completed_task_resolves_accepted_decision_output(self) -> None:
        task = load_json(VERSIONED / "task-select-driver-decision-v5.json")
        decision = load_json(VERSIONED / "decision-motor-driver-v3.json")
        state = DecisionState(
            decisions={("decision", "decision-motor-driver-001", 3): decision},
            latest_versions={("decision", "decision-motor-driver-001"): 3},
        )
        self.assertEqual([], validate_task_outputs_against_decisions(task, state))
        wrong = deepcopy(decision)
        wrong["legal_payload"]["status"] = "reassessment_required"
        bad = DecisionState(decisions=state.decisions | {("decision", "decision-motor-driver-001", 3): wrong}, latest_versions=state.latest_versions)
        self.assertIn("task_decision_output_not_accepted", validate_task_outputs_against_decisions(task, bad))


if __name__ == "__main__":
    unittest.main()
