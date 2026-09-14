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
    AGENT_CARD_PAYLOAD_SCHEMA,
    CONTEXT_GRANT_PAYLOAD_SCHEMA,
    AgentCardState,
    ContextGrantState,
    RoleAssignmentAuthorityState,
    TeamPlanState,
    validate_context_grant_against_team_and_recipient,
    validate_context_grant_semantics,
    validate_context_grant_transition,
    validate_context_grant_use,
    validate_envelope_role_assignment_authority,
    validate_protocol_envelope_semantics,
    validate_role_assignment_semantics,
)

SCHEMA_PATH = ROOT / "schemas" / "w1-cip" / "0.1" / "context-grant.schema.json"
VERSIONED_ENTITY_PATH = ROOT / "schemas" / "w1-cip" / "0.1" / "versioned-entity.schema.json"
ENTITY_REF_PATH = ROOT / "schemas" / "w1-cip" / "0.1" / "entity-ref.schema.json"
PRINCIPAL_REF_PATH = ROOT / "schemas" / "w1-cip" / "0.1" / "principal-ref.schema.json"
ROLE_ASSIGNMENT_PATH = ROOT / "schemas" / "w1-cip" / "0.1" / "role-assignment.schema.json"
AGENT_CARD_PATH = ROOT / "schemas" / "w1-cip" / "0.1" / "agent-card.schema.json"
GOAL_CONTRACT_PATH = ROOT / "schemas" / "w1-cip" / "0.1" / "goal-contract.schema.json"
TEAM_PLAN_PATH = ROOT / "schemas" / "w1-cip" / "0.1" / "team-plan.schema.json"
EXAMPLES = ROOT / "examples" / "context-grant"
VERSIONED = ROOT / "examples" / "versioned-entity" / "valid"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class ContextGrantSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = load_json(SCHEMA_PATH)
        cls.entity_ref = load_json(ENTITY_REF_PATH)
        Draft202012Validator.check_schema(cls.schema)
        registry = Registry().with_resource(
            cls.entity_ref["$id"], Resource.from_contents(cls.entity_ref)
        )
        cls.validator = Draft202012Validator(
            cls.schema, registry=registry, format_checker=FormatChecker()
        )

    def structural_errors(self, instance: dict) -> list:
        return sorted(self.validator.iter_errors(instance), key=lambda error: list(error.path))

    def build_states(self) -> tuple[TeamPlanState, RoleAssignmentAuthorityState, AgentCardState]:
        team = load_json(VERSIONED / "team-plan-pump-v1.json")
        role = load_json(VERSIONED / "role-assignment-executor-v1.json")
        card = load_json(VERSIONED / "agent-card-electronics-v1.json")
        return (
            TeamPlanState(
                plans={("team_plan", "team-plan-pump-001", 1): team},
                latest_versions={("team_plan", "team-plan-pump-001"): 1},
            ),
            RoleAssignmentAuthorityState(
                assignments={("role_assignment", "role-assignment-executor-001", 1): role},
                latest_versions={("role_assignment", "role-assignment-executor-001"): 1},
            ),
            AgentCardState(
                cards={("agent_card", "agent-electronics-01", 1): card},
                latest_versions={("agent_card", "agent-electronics-01"): 1},
            ),
        )

    def test_valid_examples(self) -> None:
        paths = sorted((EXAMPLES / "valid").glob("*.json"))
        self.assertTrue(paths, "no valid ContextGrant examples found")
        for path in paths:
            with self.subTest(path=path.name):
                instance = load_json(path)
                self.assertEqual([], self.structural_errors(instance))
                self.assertEqual([], validate_context_grant_semantics(instance))

    def test_structurally_invalid_examples(self) -> None:
        paths = sorted((EXAMPLES / "invalid-structural").glob("*.json"))
        self.assertTrue(paths, "no structurally invalid ContextGrant examples found")
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
                self.assertEqual(expected_codes, validate_context_grant_semantics(instance))

    def test_versioned_entity_resolves_context_grant_schema(self) -> None:
        schemas = [
            load_json(VERSIONED_ENTITY_PATH),
            load_json(ENTITY_REF_PATH),
            load_json(PRINCIPAL_REF_PATH),
            load_json(ROLE_ASSIGNMENT_PATH),
            load_json(AGENT_CARD_PATH),
            load_json(GOAL_CONTRACT_PATH),
            load_json(TEAM_PLAN_PATH),
            self.schema,
        ]
        for schema in schemas:
            Draft202012Validator.check_schema(schema)
        registry = Registry()
        for schema in schemas[1:]:
            registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))
        validator = Draft202012Validator(
            schemas[0], registry=registry, format_checker=FormatChecker()
        )
        record = load_json(VERSIONED / "context-grant-executor-v1.json")
        self.assertEqual([], list(validator.iter_errors(record)))

    def test_versioned_entity_rejects_context_grant_type_schema_mismatch(self) -> None:
        schemas = [
            load_json(VERSIONED_ENTITY_PATH),
            load_json(ENTITY_REF_PATH),
            load_json(PRINCIPAL_REF_PATH),
            load_json(ROLE_ASSIGNMENT_PATH),
            load_json(AGENT_CARD_PATH),
            load_json(GOAL_CONTRACT_PATH),
            load_json(TEAM_PLAN_PATH),
            self.schema,
        ]
        registry = Registry()
        for schema in schemas[1:]:
            registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))
        validator = Draft202012Validator(schemas[0], registry=registry, format_checker=FormatChecker())
        record = load_json(VERSIONED / "context-grant-executor-v1.json")
        wrong_type = deepcopy(record)
        wrong_type["entity"]["entity_type"] = "claim"
        self.assertTrue(list(validator.iter_errors(wrong_type)))
        wrong_schema = deepcopy(record)
        wrong_schema["legal_payload_schema"] = AGENT_CARD_PAYLOAD_SCHEMA
        self.assertTrue(list(validator.iter_errors(wrong_schema)))

    def test_protocol_envelope_runs_context_grant_semantics(self) -> None:
        envelope = load_json(ROOT / "examples" / "protocol-envelope" / "valid" / "context-grant-create.json")
        invalid = deepcopy(envelope)
        invalid["payload"]["legal_payload"]["expires_at"] = "2026-08-04T15:00:00Z"
        self.assertIn("context_grant_invalid_validity_interval", validate_protocol_envelope_semantics(invalid))
        backdated = deepcopy(envelope)
        backdated["payload"]["legal_payload"]["valid_from"] = "2026-08-04T15:11:59Z"
        self.assertIn("context_grant_backdated", validate_protocol_envelope_semantics(backdated))

    def test_valid_grant_resolves_team_recipient_and_card_policy(self) -> None:
        grant = load_json(EXAMPLES / "valid" / "pump-executor-context.json")
        team_state, role_state, card_state = self.build_states()
        self.assertEqual(
            [],
            validate_context_grant_against_team_and_recipient(
                grant,
                team_state,
                role_state,
                evaluated_at="2026-08-04T15:15:00Z",
                agent_card_state=card_state,
            ),
        )

    def test_grant_rejects_recipient_not_in_team(self) -> None:
        grant = load_json(EXAMPLES / "valid" / "pump-executor-context.json")
        team_state, role_state, card_state = self.build_states()
        team = deepcopy(next(iter(team_state.plans.values())))
        team["legal_payload"]["assignments"] = [
            item
            for item in team["legal_payload"]["assignments"]
            if item["role_assignment"]["entity_id"] != "role-assignment-executor-001"
        ]
        team_state = TeamPlanState(
            plans={("team_plan", "team-plan-pump-001", 1): team},
            latest_versions={("team_plan", "team-plan-pump-001"): 1},
        )
        self.assertIn(
            "context_grant_recipient_not_in_team",
            validate_context_grant_against_team_and_recipient(
                grant,
                team_state,
                role_state,
                evaluated_at="2026-08-04T15:15:00Z",
                agent_card_state=card_state,
            ),
        )

    def test_grant_rejects_agent_policy_mismatch(self) -> None:
        grant = load_json(EXAMPLES / "valid" / "pump-executor-context.json")
        grant["classification"] = "restricted"
        grant["processing_mode"] = "local_only"
        team_state, role_state, card_state = self.build_states()
        errors = validate_context_grant_against_team_and_recipient(
            grant,
            team_state,
            role_state,
            evaluated_at="2026-08-04T15:15:00Z",
            agent_card_state=card_state,
        )
        self.assertIn("context_grant_classification_not_accepted", errors)
        self.assertIn("context_grant_external_processing_not_allowed", errors)

    def test_grant_cannot_outlive_recipient_assignment(self) -> None:
        grant = load_json(EXAMPLES / "valid" / "pump-executor-context.json")
        grant["expires_at"] = "2026-08-04T18:00:00Z"
        team_state, role_state, card_state = self.build_states()
        self.assertIn(
            "context_grant_outlives_recipient",
            validate_context_grant_against_team_and_recipient(
                grant,
                team_state,
                role_state,
                evaluated_at="2026-08-04T15:15:00Z",
                agent_card_state=card_state,
            ),
        )

    def test_transition_keeps_identity_scope_and_revocation_final(self) -> None:
        v1 = load_json(VERSIONED / "context-grant-executor-v1.json")
        v2 = load_json(VERSIONED / "context-grant-executor-v2.json")
        self.assertEqual([], validate_context_grant_transition(v1, None))
        self.assertEqual([], validate_context_grant_transition(v2, v1))

        changed = deepcopy(v2)
        changed["legal_payload"]["task"]["entity_id"] = "task-other-001"
        self.assertIn(
            "context_grant_task_changed",
            validate_context_grant_transition(changed, v1),
        )

        v3 = deepcopy(v2)
        v3["entity"]["entity_version"] = 3
        v3["created_by_event_id"] = "evt-context-grant-reopened-003"
        v3["previous_version"]["entity_version"] = 2
        v3["legal_payload"]["status"] = "active"
        v3["legal_payload"].pop("status_reason")
        self.assertIn(
            "context_grant_revocation_final",
            validate_context_grant_transition(v3, v2),
        )

    def test_context_input_is_field_task_recipient_purpose_and_time_bounded(self) -> None:
        record = load_json(VERSIONED / "context-grant-executor-v1.json")
        state = ContextGrantState(
            grants={("context_grant", "context-grant-executor-001", 1): record},
            latest_versions={("context_grant", "context-grant-executor-001"): 1},
        )
        ref = record["entity"]
        role_ref = record["legal_payload"]["recipient_role_assignment"]
        task_ref = record["legal_payload"]["task"]
        self.assertEqual(
            [],
            validate_context_grant_use(
                ref,
                recipient_role_assignment=role_ref,
                task=task_ref,
                requested_fields=["available_supply_voltage"],
                purpose_code="adapt_component_explanation",
                used_at="2026-08-04T15:30:00Z",
                state=state,
            ),
        )
        errors = validate_context_grant_use(
            ref,
            recipient_role_assignment=role_ref,
            task={"entity_type": "task", "entity_id": "task-other-001", "entity_version": 1},
            requested_fields=["private_note"],
            purpose_code="other_purpose",
            used_at="2026-08-04T16:00:00Z",
            state=state,
        )
        self.assertIn("context_grant_task_mismatch", errors)
        self.assertIn("context_grant_field_not_allowed", errors)
        self.assertIn("context_grant_purpose_mismatch", errors)
        self.assertIn("context_grant_expired", errors)


    def test_human_owner_authorizes_context_grant_creation(self) -> None:
        owner = load_json(VERSIONED / "role-assignment-owner-v2.json")
        state = RoleAssignmentAuthorityState(
            assignments={("role_assignment", "role-assignment-owner-001", 2): owner},
            latest_versions={("role_assignment", "role-assignment-owner-001"): 2},
        )
        envelope = load_json(ROOT / "examples" / "protocol-envelope" / "valid" / "context-grant-create.json")
        self.assertEqual([], validate_envelope_role_assignment_authority(envelope, state))

    def test_only_human_owner_may_manage_context_grants(self) -> None:
        payload = {
            "subject": {"principal_type": "agent", "principal_id": "agent-orchestrator-01"},
            "role": "orchestrator",
            "authority_scope": {"operations": ["context_grant.create"]},
            "basis": [{"entity_type": "agent_card", "entity_id": "agent-orchestrator-01", "entity_version": 1}],
            "status": "active",
            "valid_from": "2026-08-04T15:00:00Z",
            "assignment_reason": "manage context",
        }
        self.assertIn(
            "context_grant_management_requires_human_owner",
            validate_role_assignment_semantics(payload),
        )


if __name__ == "__main__":
    unittest.main()
