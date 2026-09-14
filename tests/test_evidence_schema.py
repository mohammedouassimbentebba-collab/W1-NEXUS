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
    RoleAssignmentAuthorityState,
    TaskState,
    validate_envelope_role_assignment_authority,
    validate_evidence_against_task_registrar_and_claims,
    validate_evidence_semantics,
    validate_evidence_transition,
    validate_protocol_envelope_semantics,
)

SCHEMA_DIR = ROOT / "schemas" / "w1-cip" / "0.1"
EVIDENCE_PATH = SCHEMA_DIR / "evidence.schema.json"
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
    EVIDENCE_PATH,
]
EXAMPLES = ROOT / "examples" / "evidence"
VERSIONED = ROOT / "examples" / "versioned-entity" / "valid"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class EvidenceSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = load_json(EVIDENCE_PATH)
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
            ("task", "task-startup-evidence-001", version): load_json(
                VERSIONED / f"task-startup-evidence-v{version}.json"
            )
            for version in range(1, 5)
        }
        role = load_json(VERSIONED / "role-assignment-verifier-v1.json")
        card = load_json(VERSIONED / "agent-card-verifier-v1.json")
        claim = load_json(VERSIONED / "contribution-claim-startup-v1.json")
        return (
            TaskState(
                tasks=tasks,
                latest_versions={("task", "task-startup-evidence-001"): 4},
            ),
            RoleAssignmentAuthorityState(
                assignments={("role_assignment", "role-assignment-verifier-001", 1): role},
                latest_versions={("role_assignment", "role-assignment-verifier-001"): 1},
            ),
            AgentCardState(
                cards={("agent_card", "agent-verifier-01", 1): card},
                latest_versions={("agent_card", "agent-verifier-01"): 1},
            ),
            ContributionState(
                contributions={("contribution", "claim-candidate-a-startup-001", 1): claim},
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
                self.assertEqual([], validate_evidence_semantics(payload))

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
                self.assertEqual(expected, validate_evidence_semantics(payload))

    def test_versioned_entity_resolves_evidence_schema(self) -> None:
        root_schema = load_json(VERSIONED_ENTITY_PATH)
        registry = Registry()
        for path in SCHEMA_FILES:
            schema = load_json(path)
            registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))
        validator = Draft202012Validator(
            root_schema, registry=registry, format_checker=FormatChecker()
        )
        record = load_json(VERSIONED / "evidence-startup-current-v1.json")
        self.assertEqual([], list(validator.iter_errors(record)))
        wrong = deepcopy(record)
        wrong["entity"]["entity_type"] = "contribution"
        self.assertTrue(list(validator.iter_errors(wrong)))

    def test_protocol_envelope_runs_evidence_semantics_and_author_binding(self) -> None:
        envelope = load_json(
            ROOT / "examples" / "protocol-envelope" / "valid" / "evidence-create.json"
        )
        self.assertEqual([], validate_protocol_envelope_semantics(envelope))
        wrong = deepcopy(envelope)
        wrong["authorized_by"]["entity_id"] = "role-assignment-other-001"
        self.assertIn("evidence_authority_ref_mismatch", validate_protocol_envelope_semantics(wrong))

    def test_valid_task_registrar_claim_and_agent_binding(self) -> None:
        record = load_json(VERSIONED / "evidence-startup-current-v1.json")
        task_state, role_state, card_state, contribution_state = self.build_states()
        self.assertEqual(
            [],
            validate_evidence_against_task_registrar_and_claims(
                record,
                task_state,
                role_state,
                contribution_state,
                evaluated_at="2026-08-04T15:20:01Z",
                agent_card_state=card_state,
            ),
        )

    def test_rejects_nonclaim_target_and_semantically_stale_claim(self) -> None:
        record = load_json(VERSIONED / "evidence-startup-current-v1.json")
        task_state, role_state, card_state, contribution_state = self.build_states()
        proposal = load_json(VERSIONED / "contribution-proposal-v1.json")
        nonclaim_state = ContributionState(
            contributions={("contribution", "proposal-driver-001", 1): proposal},
            latest_versions={("contribution", "proposal-driver-001"): 1},
        )
        nonclaim = deepcopy(record)
        nonclaim["legal_payload"]["targets"] = [proposal["entity"]]
        self.assertIn(
            "evidence_target_not_claim",
            validate_evidence_against_task_registrar_and_claims(
                nonclaim,
                task_state,
                role_state,
                nonclaim_state,
                evaluated_at="2026-08-04T15:20:01Z",
                agent_card_state=card_state,
            ),
        )

        claim_v1 = contribution_state.contributions[("contribution", "claim-candidate-a-startup-001", 1)]
        claim_v2 = deepcopy(claim_v1)
        claim_v2["entity"]["entity_version"] = 2
        claim_v2["created_by_event_id"] = "evt-contribution-claim-revised-003"
        claim_v2["previous_version"] = claim_v1["entity"]
        claim_v2["change_metadata"] = {
            "change_reason": "semantic",
            "substantive_effect": "may_affect_dependents",
        }
        claim_v2["legal_payload"]["content"]["statement"] = "candidate-a does not support the required startup current"
        stale_state = ContributionState(
            contributions={
                ("contribution", "claim-candidate-a-startup-001", 1): claim_v1,
                ("contribution", "claim-candidate-a-startup-001", 2): claim_v2,
            },
            latest_versions={("contribution", "claim-candidate-a-startup-001"): 2},
        )
        self.assertIn(
            "evidence_target_claim_version_not_current",
            validate_evidence_against_task_registrar_and_claims(
                record,
                task_state,
                role_state,
                stale_state,
                evaluated_at="2026-08-04T15:20:01Z",
                agent_card_state=card_state,
            ),
        )

    def test_rejects_wrong_registrar_scope_and_future_collection(self) -> None:
        record = load_json(VERSIONED / "evidence-startup-current-v1.json")
        task_state, role_state, card_state, contribution_state = self.build_states()
        role_record = deepcopy(next(iter(role_state.assignments.values())))
        role_record["legal_payload"]["authority_scope"]["operations"] = ["contribution.create"]
        bad_role_state = RoleAssignmentAuthorityState(
            assignments={("role_assignment", "role-assignment-verifier-001", 1): role_record},
            latest_versions={("role_assignment", "role-assignment-verifier-001"): 1},
        )
        errors = validate_evidence_against_task_registrar_and_claims(
            record,
            task_state,
            bad_role_state,
            contribution_state,
            evaluated_at="2026-08-04T15:19:59Z",
            agent_card_state=card_state,
        )
        self.assertIn("evidence_registrar_scope_denied", errors)
        self.assertIn("evidence_collected_in_future", errors)
        self.assertIn("evidence_integrity_assessor_scope_denied", errors)

    def test_evidence_authority_is_valid_for_reference_event(self) -> None:
        envelope = load_json(
            ROOT / "examples" / "protocol-envelope" / "valid" / "evidence-create.json"
        )
        _, role_state, card_state, _ = self.build_states()
        self.assertEqual(
            [],
            validate_envelope_role_assignment_authority(
                envelope, role_state, agent_card_state=card_state
            ),
        )

    def test_transition_preserves_source_and_withdrawal_is_final(self) -> None:
        v1 = load_json(VERSIONED / "evidence-startup-current-v1.json")
        v2 = load_json(VERSIONED / "evidence-startup-current-v2-withdrawn.json")
        self.assertEqual([], validate_evidence_transition(v1, None))
        self.assertEqual([], validate_evidence_transition(v2, v1))

        changed = deepcopy(v2)
        changed["legal_payload"]["source"]["source_version"] = 2
        self.assertIn("evidence_source_changed", validate_evidence_transition(changed, v1))
        self.assertIn("evidence_withdrawal_changed_content", validate_evidence_transition(changed, v1))

        downgraded = deepcopy(v2)
        downgraded["legal_payload"]["classification"] = "public"
        self.assertIn("evidence_classification_downgraded", validate_evidence_transition(downgraded, v1))

        reactivated = deepcopy(v2)
        reactivated["entity"]["entity_version"] = 3
        reactivated["created_by_event_id"] = "evt-evidence-reactivated-003"
        reactivated["previous_version"] = v2["entity"]
        reactivated["legal_payload"]["status"] = "active"
        reactivated["legal_payload"].pop("withdrawal_reason")
        self.assertIn("evidence_withdrawal_final", validate_evidence_transition(reactivated, v2))


if __name__ == "__main__":
    unittest.main()
