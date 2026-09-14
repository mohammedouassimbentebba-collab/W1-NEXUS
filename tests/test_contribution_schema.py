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
    ContextGrantState,
    ContributionState,
    RoleAssignmentAuthorityState,
    TaskState,
    validate_contribution_against_task_author_and_context,
    validate_contribution_semantics,
    validate_contribution_transition,
    validate_envelope_role_assignment_authority,
    validate_protocol_envelope_semantics,
    validate_task_outputs_against_contributions,
)

SCHEMA_DIR = ROOT / "schemas" / "w1-cip" / "0.1"
CONTRIBUTION_PATH = SCHEMA_DIR / "contribution.schema.json"
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
    CONTRIBUTION_PATH,
    SCHEMA_DIR / "evidence.schema.json",
]
EXAMPLES = ROOT / "examples" / "contribution"
VERSIONED = ROOT / "examples" / "versioned-entity" / "valid"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class ContributionSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = load_json(CONTRIBUTION_PATH)
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
            ("task", "task-driver-selection-001", version): load_json(
                VERSIONED / f"task-driver-selection-v{version}.json"
            )
            for version in range(1, 5)
        }
        role = load_json(VERSIONED / "role-assignment-executor-v1.json")
        card = load_json(VERSIONED / "agent-card-electronics-v1.json")
        grant = load_json(VERSIONED / "context-grant-executor-v1.json")
        return (
            TaskState(
                tasks=tasks,
                latest_versions={("task", "task-driver-selection-001"): 4},
            ),
            RoleAssignmentAuthorityState(
                assignments={("role_assignment", "role-assignment-executor-001", 1): role},
                latest_versions={("role_assignment", "role-assignment-executor-001"): 1},
            ),
            AgentCardState(
                cards={("agent_card", "agent-electronics-01", 1): card},
                latest_versions={("agent_card", "agent-electronics-01"): 1},
            ),
            ContextGrantState(
                grants={("context_grant", "context-grant-executor-001", 1): grant},
                latest_versions={("context_grant", "context-grant-executor-001"): 1},
            ),
        )

    def test_valid_examples(self) -> None:
        paths = sorted((EXAMPLES / "valid").glob("*.json"))
        self.assertTrue(paths)
        for path in paths:
            with self.subTest(path=path.name):
                payload = load_json(path)
                self.assertEqual([], self.structural_errors(payload))
                self.assertEqual([], validate_contribution_semantics(payload))

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
                self.assertEqual(expected, validate_contribution_semantics(payload))

    def test_versioned_entity_resolves_contribution_schema(self) -> None:
        root_schema = load_json(VERSIONED_ENTITY_PATH)
        registry = Registry()
        for path in SCHEMA_FILES:
            schema = load_json(path)
            registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))
        validator = Draft202012Validator(
            root_schema, registry=registry, format_checker=FormatChecker()
        )
        record = load_json(VERSIONED / "contribution-proposal-v1.json")
        self.assertEqual([], list(validator.iter_errors(record)))
        wrong = deepcopy(record)
        wrong["entity"]["entity_type"] = "claim"
        self.assertTrue(list(validator.iter_errors(wrong)))

    def test_protocol_envelope_runs_contribution_semantics_and_author_binding(self) -> None:
        envelope = load_json(
            ROOT / "examples" / "protocol-envelope" / "valid" / "contribution-create.json"
        )
        self.assertEqual([], validate_protocol_envelope_semantics(envelope))
        duplicate = deepcopy(envelope)
        duplicate["payload"]["legal_payload"]["basis"] = [
            {"entity_type": "claim", "entity_id": "claim-21", "entity_version": 1},
            {"entity_type": "claim", "entity_id": "claim-21", "entity_version": 2},
        ]
        self.assertIn(
            "duplicate_contribution_basis_identity",
            validate_protocol_envelope_semantics(duplicate),
        )
        wrong_authority = deepcopy(envelope)
        wrong_authority["authorized_by"]["entity_id"] = "role-assignment-other-001"
        self.assertIn(
            "contribution_authority_ref_mismatch",
            validate_protocol_envelope_semantics(wrong_authority),
        )

    def test_valid_task_author_context_and_agent_policy_binding(self) -> None:
        current = load_json(VERSIONED / "contribution-proposal-v1.json")
        task_state, role_state, card_state, grant_state = self.build_states()
        self.assertEqual(
            [],
            validate_contribution_against_task_author_and_context(
                current,
                task_state,
                role_state,
                evaluated_at="2026-08-04T15:20:01Z",
                agent_card_state=card_state,
                context_grant_state=grant_state,
            ),
        )

    def test_contribution_rejects_wrong_author_phase_and_context_field(self) -> None:
        current = load_json(VERSIONED / "contribution-proposal-v1.json")
        task_state, role_state, card_state, grant_state = self.build_states()
        wrong_author = deepcopy(current)
        wrong_author["legal_payload"]["authored_by_role_assignment"]["entity_id"] = "role-assignment-reviewer-001"
        self.assertIn(
            "contribution_author_not_task_assignee",
            validate_contribution_against_task_author_and_context(
                wrong_author, task_state, role_state, evaluated_at="2026-08-04T15:20:01Z"
            ),
        )

        approval_task = deepcopy(task_state.tasks[("task", "task-driver-selection-001", 4)])
        approval_task["legal_payload"]["phase"] = "approval"
        approval_state = TaskState(
            tasks={("task", "task-driver-selection-001", 4): approval_task},
            latest_versions={("task", "task-driver-selection-001"): 4},
        )
        self.assertIn(
            "contribution_type_not_allowed_for_task_phase",
            validate_contribution_against_task_author_and_context(
                current, approval_state, role_state, evaluated_at="2026-08-04T15:20:01Z"
            ),
        )

        bad_context = deepcopy(current)
        bad_context["legal_payload"]["context_usage"][0]["fields_used"] = ["private_note"]
        self.assertIn(
            "contribution_context_grant_field_not_allowed",
            validate_contribution_against_task_author_and_context(
                bad_context,
                task_state,
                role_state,
                evaluated_at="2026-08-04T15:20:01Z",
                agent_card_state=card_state,
                context_grant_state=grant_state,
            ),
        )

        downgraded_output = deepcopy(current)
        downgraded_output["legal_payload"]["classification"] = "public"
        self.assertIn(
            "contribution_classification_below_context",
            validate_contribution_against_task_author_and_context(
                downgraded_output,
                task_state,
                role_state,
                evaluated_at="2026-08-04T15:20:01Z",
                agent_card_state=card_state,
                context_grant_state=grant_state,
            ),
        )

    def test_contribution_authority_is_valid_for_reference_event(self) -> None:
        envelope = load_json(
            ROOT / "examples" / "protocol-envelope" / "valid" / "contribution-create.json"
        )
        _, role_state, card_state, _ = self.build_states()
        self.assertEqual(
            [],
            validate_envelope_role_assignment_authority(
                envelope, role_state, agent_card_state=card_state
            ),
        )

    def test_transition_preserves_provenance_and_withdrawal_is_final(self) -> None:
        v1 = load_json(VERSIONED / "contribution-proposal-v1.json")
        v2 = load_json(VERSIONED / "contribution-proposal-v2-withdrawn.json")
        self.assertEqual([], validate_contribution_transition(v1, None))
        self.assertEqual([], validate_contribution_transition(v2, v1))
        changed = deepcopy(v2)
        changed["legal_payload"]["content"]["description"] += " Changed while withdrawing."
        self.assertIn(
            "contribution_withdrawal_changed_content",
            validate_contribution_transition(changed, v1),
        )
        downgraded = deepcopy(v2)
        downgraded["legal_payload"]["classification"] = "public"
        self.assertIn(
            "contribution_classification_downgraded",
            validate_contribution_transition(downgraded, v1),
        )
        reactivated = deepcopy(v2)
        reactivated["entity"]["entity_version"] = 3
        reactivated["previous_version"]["entity_version"] = 2
        reactivated["created_by_event_id"] = "evt-contribution-reactivated-003"
        reactivated["legal_payload"]["status"] = "active"
        reactivated["legal_payload"].pop("withdrawal_reason")
        self.assertIn(
            "contribution_withdrawal_final",
            validate_contribution_transition(reactivated, v2),
        )

    def test_completed_task_output_resolves_active_contribution(self) -> None:
        task = load_json(VERSIONED / "task-driver-selection-v5.json")
        contribution = load_json(VERSIONED / "contribution-proposal-v1.json")
        state = ContributionState(
            contributions={("contribution", "proposal-driver-001", 1): contribution},
            latest_versions={("contribution", "proposal-driver-001"): 1},
        )
        self.assertEqual([], validate_task_outputs_against_contributions(task, state))

        withdrawn = load_json(VERSIONED / "contribution-proposal-v2-withdrawn.json")
        state = ContributionState(
            contributions={
                ("contribution", "proposal-driver-001", 1): contribution,
                ("contribution", "proposal-driver-001", 2): withdrawn,
            },
            latest_versions={("contribution", "proposal-driver-001"): 2},
        )
        errors = validate_task_outputs_against_contributions(task, state)
        self.assertIn("task_contribution_output_version_not_current", errors)
        self.assertIn("task_contribution_output_inactive", errors)


if __name__ == "__main__":
    unittest.main()
