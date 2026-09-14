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
    ApprovalTargetState,
    ChallengeState,
    GoalContractState,
    RoleAssignmentAuthorityState,
    TaskState,
    validate_approval_against_task_issuer_and_targets,
    validate_approval_semantics,
    validate_approval_transition,
    validate_envelope_approval_authority,
    validate_envelope_role_assignment_authority,
    validate_protocol_envelope_semantics,
    validate_role_assignment_semantics,
    validate_task_outputs_against_approvals,
    validate_versioned_entity_editorial_approval,
)

SCHEMA_DIR = ROOT / "schemas" / "w1-cip" / "0.1"
APPROVAL_PATH = SCHEMA_DIR / "approval.schema.json"
VERSIONED_ENTITY_PATH = SCHEMA_DIR / "versioned-entity.schema.json"
SCHEMA_FILES = sorted(SCHEMA_DIR.glob("*.schema.json"))
EXAMPLES = ROOT / "examples" / "approval"
VERSIONED = ROOT / "examples" / "versioned-entity" / "valid"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class ApprovalSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = load_json(APPROVAL_PATH)
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
        task = load_json(VERSIONED / "task-approve-revision-v4.json")
        decision = load_json(VERSIONED / "role-assignment-decision-v1.json")
        executor = load_json(VERSIONED / "role-assignment-executor-v1.json")
        card = load_json(VERSIONED / "agent-card-decision-v1.json")
        proposal = load_json(VERSIONED / "contribution-proposal-v1.json")
        review = load_json(VERSIONED / "review-startup-current-v1.json")
        challenge = load_json(VERSIONED / "challenge-startup-current-v1.json")
        goal = load_json(VERSIONED / "goal-contract-pump-v1.json")
        return (
            TaskState(
                tasks={("task", "task-approve-revision-001", 4): task},
                latest_versions={("task", "task-approve-revision-001"): 4},
            ),
            RoleAssignmentAuthorityState(
                assignments={
                    ("role_assignment", "role-assignment-decision-001", 1): decision,
                    ("role_assignment", "role-assignment-executor-001", 1): executor,
                },
                latest_versions={
                    ("role_assignment", "role-assignment-decision-001"): 1,
                    ("role_assignment", "role-assignment-executor-001"): 1,
                },
            ),
            AgentCardState(
                cards={("agent_card", "agent-decision-01", 1): card},
                latest_versions={("agent_card", "agent-decision-01"): 1},
            ),
            ApprovalTargetState(
                records={
                    ("contribution", "proposal-driver-selection-001", 1): proposal,
                    ("review", "review-startup-current-001", 1): review,
                    ("challenge", "challenge-startup-current-001", 1): challenge,
                },
                latest_versions={
                    ("contribution", "proposal-driver-selection-001"): 1,
                    ("review", "review-startup-current-001"): 1,
                    ("challenge", "challenge-startup-current-001"): 1,
                },
            ),
            GoalContractState(
                contracts={("goal_contract", "goal-pump-driver-001", 1): goal},
                latest_versions={("goal_contract", "goal-pump-driver-001"): 1},
            ),
            ChallengeState(
                challenges={("challenge", "challenge-startup-current-001", 1): challenge},
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
                self.assertEqual([], validate_approval_semantics(payload))

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
                self.assertEqual(expected, validate_approval_semantics(payload))

    def test_versioned_entity_resolves_approval_schema(self) -> None:
        root_schema = load_json(VERSIONED_ENTITY_PATH)
        validator = Draft202012Validator(
            root_schema, registry=self.registry, format_checker=FormatChecker()
        )
        record = load_json(VERSIONED / "approval-proposal-revision-v1.json")
        self.assertEqual([], list(validator.iter_errors(record)))
        wrong = deepcopy(record)
        wrong["entity"]["entity_type"] = "review"
        self.assertTrue(list(validator.iter_errors(wrong)))

    def test_protocol_envelope_runs_approval_semantics_and_author_binding(self) -> None:
        envelope = load_json(ROOT / "examples" / "protocol-envelope" / "valid" / "approval-create.json")
        self.assertEqual([], validate_protocol_envelope_semantics(envelope))
        wrong = deepcopy(envelope)
        wrong["authorized_by"]["entity_id"] = "role-assignment-reviewer-001"
        self.assertIn("approval_authority_ref_mismatch", validate_protocol_envelope_semantics(wrong))

    def test_valid_approval_binds_task_issuer_grantee_and_target(self) -> None:
        record = load_json(VERSIONED / "approval-proposal-revision-v1.json")
        states = self.build_states()
        self.assertEqual(
            [],
            validate_approval_against_task_issuer_and_targets(
                record, states[0], states[1], states[3],
                evaluated_at="2026-08-04T15:26:00Z",
                goal_state=states[4], challenge_state=states[5], agent_card_state=states[2],
            ),
        )

    def test_issuer_role_scope_and_agent_write_permission_are_enforced(self) -> None:
        record = load_json(VERSIONED / "approval-proposal-revision-v1.json")
        states = list(self.build_states())
        issuer = deepcopy(states[1].assignments[("role_assignment", "role-assignment-decision-001", 1)])
        issuer["legal_payload"]["authority_scope"]["operations"] = []
        states[1] = RoleAssignmentAuthorityState(
            assignments={
                ("role_assignment", "role-assignment-decision-001", 1): issuer,
                ("role_assignment", "role-assignment-executor-001", 1): states[1].assignments[("role_assignment", "role-assignment-executor-001", 1)],
            },
            latest_versions=states[1].latest_versions,
        )
        card = deepcopy(states[2].cards[("agent_card", "agent-decision-01", 1)])
        card["legal_payload"]["permissions"]["data_write"] = []
        states[2] = AgentCardState(
            cards={("agent_card", "agent-decision-01", 1): card},
            latest_versions={("agent_card", "agent-decision-01"): 1},
        )
        errors = validate_approval_against_task_issuer_and_targets(
            record, states[0], states[1], states[3],
            evaluated_at="2026-08-04T15:26:00Z", agent_card_state=states[2]
        )
        self.assertIn("approval_issuer_scope_denied", errors)
        self.assertIn("approval_agent_write_permission_missing", errors)

    def test_operation_approval_authorizes_only_exact_unconsumed_action(self) -> None:
        record = load_json(VERSIONED / "approval-proposal-revision-v1.json")
        cmd = load_json(ROOT / "examples" / "protocol-envelope" / "valid" / "command-approved-revision.json")
        states = self.build_states()
        approval_state = ApprovalState(
            approvals={("approval", "approval-proposal-revision-001", 1): record},
            latest_versions={("approval", "approval-proposal-revision-001"): 1},
        )
        self.assertEqual(
            [],
            validate_envelope_approval_authority(
                cmd, approval_state, states[1], acceptance_time="2026-08-04T15:27:00Z"
            ),
        )
        wrong = deepcopy(cmd)
        wrong["precondition"]["target"]["entity_id"] = "other-proposal"
        self.assertIn(
            "approval_authority_target_mismatch",
            validate_envelope_approval_authority(
                wrong, approval_state, states[1], acceptance_time="2026-08-04T15:27:00Z"
            ),
        )
        consumed = ApprovalState(
            approvals=approval_state.approvals,
            latest_versions=approval_state.latest_versions,
            consumed=frozenset({("approval", "approval-proposal-revision-001", 1)}),
        )
        self.assertIn(
            "approval_authority_already_consumed",
            validate_envelope_approval_authority(
                cmd, consumed, states[1], acceptance_time="2026-08-04T15:27:00Z"
            ),
        )
        self.assertIn(
            "approval_authority_expired",
            validate_envelope_approval_authority(
                cmd, approval_state, states[1], acceptance_time="2026-08-04T15:37:00Z"
            ),
        )

    def test_approval_cannot_authorize_different_principal(self) -> None:
        record = load_json(VERSIONED / "approval-proposal-revision-v1.json")
        cmd = load_json(ROOT / "examples" / "protocol-envelope" / "valid" / "command-approved-revision.json")
        cmd["actor"] = {"principal_type": "agent", "principal_id": "other-agent"}
        states = self.build_states()
        approval_state = ApprovalState(
            approvals={("approval", "approval-proposal-revision-001", 1): record},
            latest_versions={("approval", "approval-proposal-revision-001"): 1},
        )
        self.assertIn(
            "approval_authority_principal_mismatch",
            validate_envelope_approval_authority(
                cmd, approval_state, states[1], acceptance_time="2026-08-04T15:27:00Z"
            ),
        )

    def test_risk_acceptance_obeys_goal_policy_and_requires_owner_capability(self) -> None:
        base = load_json(VERSIONED / "approval-proposal-revision-v1.json")
        payload = base["legal_payload"]
        payload["approval_kind"] = "risk_acceptance"
        payload["targets"] = [{"entity_type": "challenge", "entity_id": "challenge-startup-current-001", "entity_version": 1}]
        payload.pop("granted_to_role_assignment")
        payload.pop("authorization")
        payload.pop("expires_at")
        payload["risk_acceptance"] = {
            "accepted_severity": "critical",
            "rationale": "Explicitly accept the unresolved synthetic risk.",
            "residual_risks": ["The selected candidate may remain incompatible."],
        }
        owner = load_json(VERSIONED / "role-assignment-owner-v3.json")
        owner["entity"]["entity_version"] = 4
        owner["legal_payload"]["authority_scope"]["operations"].extend(["approval.create", "approval.next_version"])
        owner["legal_payload"]["authority_scope"]["capabilities"] = ["accept_risk"]
        payload["approved_by_role_assignment"] = owner["entity"]
        task = load_json(VERSIONED / "task-approve-revision-v4.json")
        task["legal_payload"]["assigned_role_assignment"] = owner["entity"]
        states = self.build_states()
        task_state = TaskState(tasks={("task", "task-approve-revision-001", 4): task}, latest_versions={("task", "task-approve-revision-001"): 4})
        role_state = RoleAssignmentAuthorityState(assignments={("role_assignment", "role-assignment-owner-001", 4): owner}, latest_versions={("role_assignment", "role-assignment-owner-001"): 4})
        errors = validate_approval_against_task_issuer_and_targets(
            base, task_state, role_state, states[3],
            evaluated_at="2026-08-04T15:26:00Z", goal_state=states[4], challenge_state=states[5]
        )
        self.assertIn("approval_risk_acceptance_not_allowed", errors)
        self.assertIn("approval_risk_severity_exceeds_goal_policy", errors)

    def test_transition_keeps_decision_immutable_and_revocation_is_final(self) -> None:
        first = load_json(VERSIONED / "approval-proposal-revision-v1.json")
        second = load_json(VERSIONED / "approval-proposal-revision-v2.json")
        self.assertEqual([], validate_approval_transition(first, None))
        self.assertEqual([], validate_approval_transition(second, first))
        changed = deepcopy(second)
        changed["legal_payload"]["conditions"] = ["A different condition."]
        self.assertIn("approval_conditions_changed", validate_approval_transition(changed, first))
        reactivated = deepcopy(second)
        reactivated["entity"]["entity_version"] = 3
        reactivated["previous_version"] = second["entity"]
        reactivated["legal_payload"]["status"] = "active"
        reactivated["legal_payload"].pop("revocation_reason")
        self.assertIn("approval_revocation_final", validate_approval_transition(reactivated, second))

    def test_completed_task_resolves_approval_output(self) -> None:
        task = load_json(VERSIONED / "task-approve-revision-v5.json")
        approval = load_json(VERSIONED / "approval-proposal-revision-v1.json")
        state = ApprovalState(
            approvals={("approval", "approval-proposal-revision-001", 1): approval},
            latest_versions={("approval", "approval-proposal-revision-001"): 1},
        )
        self.assertEqual([], validate_task_outputs_against_approvals(task, state))
        wrong = deepcopy(approval)
        wrong["legal_payload"]["fulfills_output_id"] = "other-output"
        bad = ApprovalState(
            approvals={("approval", "approval-proposal-revision-001", 1): wrong},
            latest_versions=state.latest_versions,
        )
        self.assertIn("task_approval_output_binding_mismatch", validate_task_outputs_against_approvals(task, bad))

    def test_editorial_correction_approval_matches_version_diff_and_check(self) -> None:
        current = load_json(VERSIONED / "claim-v3-editorial-none.json")
        base = load_json(VERSIONED / "approval-proposal-revision-v1.json")
        base["entity"] = {"entity_type": "approval", "entity_id": "approval-editorial-003", "entity_version": 1}
        payload = base["legal_payload"]
        payload["approval_kind"] = "editorial_correction"
        payload["targets"] = [deepcopy(current["previous_version"])]
        payload.pop("granted_to_role_assignment")
        payload.pop("authorization")
        payload.pop("expires_at")
        payload["editorial_correction"] = {
            "diff_artifact": deepcopy(current["change_metadata"]["diff_artifact"]),
            "classification_check": deepcopy(current["change_metadata"]["classification_check"]),
        }
        state = ApprovalState(
            approvals={("approval", "approval-editorial-003", 1): base},
            latest_versions={("approval", "approval-editorial-003"): 1},
        )
        self.assertEqual([], validate_versioned_entity_editorial_approval(current, state))
        wrong = deepcopy(base)
        wrong["legal_payload"]["editorial_correction"]["diff_artifact"]["artifact_version"] = 2
        bad = ApprovalState(
            approvals={("approval", "approval-editorial-003", 1): wrong},
            latest_versions=state.latest_versions,
        )
        self.assertIn(
            "editorial_approval_diff_mismatch",
            validate_versioned_entity_editorial_approval(current, bad),
        )

    def test_decision_authority_role_can_issue_approval(self) -> None:
        role = load_json(VERSIONED / "role-assignment-decision-v1.json")
        self.assertEqual([], validate_role_assignment_semantics(role["legal_payload"]))
        envelope = load_json(ROOT / "examples" / "protocol-envelope" / "valid" / "approval-create.json")
        states = self.build_states()
        self.assertEqual(
            [],
            validate_envelope_role_assignment_authority(envelope, states[1], agent_card_state=states[2]),
        )


if __name__ == "__main__":
    unittest.main()
