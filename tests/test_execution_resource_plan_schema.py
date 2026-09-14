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
    EXECUTION_RESOURCE_PLAN_PAYLOAD_SCHEMA,
    AgentCardState,
    RoleAssignmentAuthorityState,
    TeamPlanState,
    route_execution_resource,
    validate_execution_resource_plan_against_state,
    validate_execution_resource_plan_semantics,
    validate_execution_resource_plan_transition,
    validate_protocol_envelope_semantics,
    validate_role_assignment_semantics,
)

SCHEMAS = ROOT / "schemas" / "w1-cip" / "0.1"
EXAMPLES = ROOT / "examples"
VALID = EXAMPLES / "execution-resource-plan" / "valid"
VERSIONED = EXAMPLES / "versioned-entity" / "valid"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class ExecutionResourcePlanSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = load_json(SCHEMAS / "execution-resource-plan.schema.json")
        Draft202012Validator.check_schema(cls.schema)
        entity_ref = load_json(SCHEMAS / "entity-ref.schema.json")
        registry = Registry().with_resource(entity_ref["$id"], Resource.from_contents(entity_ref))
        cls.validator = Draft202012Validator(
            cls.schema, registry=registry, format_checker=FormatChecker()
        )
        cls.plan = load_json(VALID / "heterogeneous-model-routing.json")

    def structural_errors(self, value: dict) -> list:
        return list(self.validator.iter_errors(value))

    def test_valid_heterogeneous_plan(self) -> None:
        self.assertEqual([], self.structural_errors(self.plan))
        self.assertEqual([], validate_execution_resource_plan_semantics(self.plan))
        self.assertEqual("prohibited", self.plan["heterogeneity_policy"]["model_count_voting"])
        self.assertEqual(
            "authority_evidence_verification",
            self.plan["heterogeneity_policy"]["decision_basis"],
        )

    def test_versioned_entity_resolves_resource_plan_schema(self) -> None:
        names = [
            "versioned-entity.schema.json",
            "entity-ref.schema.json",
            "principal-ref.schema.json",
            "role-assignment.schema.json",
            "agent-card.schema.json",
            "goal-contract.schema.json",
            "team-plan.schema.json",
            "execution-resource-plan.schema.json",
        ]
        schemas = [load_json(SCHEMAS / name) for name in names]
        for schema in schemas:
            Draft202012Validator.check_schema(schema)
        registry = Registry()
        for schema in schemas[1:]:
            registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))
        validator = Draft202012Validator(
            schemas[0], registry=registry, format_checker=FormatChecker()
        )
        record = load_json(VERSIONED / "execution-resource-plan-pump-v1.json")
        self.assertEqual([], list(validator.iter_errors(record)))
        self.assertEqual(EXECUTION_RESOURCE_PLAN_PAYLOAD_SCHEMA, record["legal_payload_schema"])

    def test_semantics_reject_duplicate_and_unsafe_routing(self) -> None:
        bad = deepcopy(self.plan)
        bad["resources"].append(deepcopy(bad["resources"][0]))
        bad["routing_rules"][0]["candidates"][1]["priority"] = 1
        bad["routing_rules"][0]["active_resource_id"] = "lite-resource"
        errors = validate_execution_resource_plan_semantics(bad)
        self.assertIn("execution_resource_duplicate_resource_id", errors)
        self.assertIn("execution_resource_duplicate_candidate_priority", errors)
        self.assertIn("execution_resource_active_candidate_below_floor", errors)

    def test_capability_first_primary_must_be_strongest(self) -> None:
        bad = deepcopy(self.plan)
        bad["routing_rules"][0]["candidates"][0]["fitness_score"] = 80
        bad["routing_rules"][0]["candidates"][1]["fitness_score"] = 91
        self.assertIn(
            "execution_resource_capability_first_primary_not_strongest",
            validate_execution_resource_plan_semantics(bad),
        )

    def test_quota_reserve_routes_to_strong_fallback(self) -> None:
        result = route_execution_resource(
            self.plan,
            role="executor",
            phase="execution",
            risk_level="medium",
            task_complexity="bounded",
            domains={"electronics"},
            estimated_units=3,
        )
        self.assertEqual("selected", result.action)
        self.assertEqual("sol-resource", result.selected_resource_id)
        self.assertTrue(result.used_fallback)
        self.assertTrue(result.requires_extra_review)
        self.assertTrue(result.disclosure_required)
        self.assertTrue(result.plan_update_required)

    def test_small_request_uses_primary_without_spending_reserve(self) -> None:
        result = route_execution_resource(
            self.plan,
            role="executor",
            phase="execution",
            risk_level="medium",
            task_complexity="bounded",
            domains={"electronics"},
            estimated_units=1,
        )
        self.assertEqual("opus-resource", result.selected_resource_id)
        self.assertFalse(result.used_fallback)
        self.assertFalse(result.disclosure_required)

    def test_lite_model_can_lead_only_for_matching_low_risk_task(self) -> None:
        result = route_execution_resource(
            self.plan,
            role="executor",
            phase="execution",
            risk_level="low",
            task_complexity="bounded",
            domains={"electronics"},
            estimated_units=1,
        )
        self.assertEqual("lite-resource", result.selected_resource_id)
        self.assertFalse(result.used_fallback)

    def test_unsafe_low_tier_fallback_is_blocked_for_material_work(self) -> None:
        bad = deepcopy(self.plan)
        bad["resources"][0]["quota"].update(
            {"status": "exhausted", "remaining_units": 0, "status_reason": "quota exhausted"}
        )
        bad["resources"][1]["quota"].update(
            {"status": "exhausted", "remaining_units": 0, "status_reason": "quota exhausted"}
        )
        # Make the recorded active candidate structurally coherent before routing.
        bad["routing_rules"][0]["active_resource_id"] = "lite-resource"
        bad["routing_rules"][0]["activation_reason"] = "quota_exhausted"
        bad["routing_rules"][0]["fallback_from_resource_id"] = "opus-resource"
        # Local semantic validation correctly rejects the active below-floor fallback.
        self.assertIn(
            "execution_resource_active_candidate_below_floor",
            validate_execution_resource_plan_semantics(bad),
        )

    def test_all_candidates_exhausted_waits_for_reset(self) -> None:
        bad = deepcopy(self.plan)
        for resource in bad["resources"]:
            resource["quota"].update(
                {"status": "exhausted", "remaining_units": 0, "status_reason": "quota exhausted"}
            )
        for rule in bad["routing_rules"]:
            rule["routing_status"] = "awaiting_reset"
            rule.pop("active_resource_id")
            rule["activation_reason"] = "quota_exhausted"
            rule["additional_review_required"] = False
            rule.pop("fallback_from_resource_id", None)
        result = route_execution_resource(
            bad,
            role="executor",
            phase="execution",
            risk_level="medium",
            task_complexity="bounded",
            domains={"electronics"},
            estimated_units=1,
        )
        self.assertEqual("await_reset", result.action)
        self.assertIn("execution_resource_all_candidates_unavailable", result.error_codes)

    def build_states(self) -> tuple[TeamPlanState, RoleAssignmentAuthorityState, AgentCardState]:
        team1 = load_json(VERSIONED / "team-plan-pump-v1.json")
        team2 = load_json(VERSIONED / "team-plan-pump-v2.json")
        team_state = TeamPlanState(
            plans={
                ("team_plan", "team-plan-pump-001", 1): team1,
                ("team_plan", "team-plan-pump-001", 2): team2,
            },
            latest_versions={("team_plan", "team-plan-pump-001"): 2},
        )
        roles = [
            load_json(VERSIONED / "role-assignment-executor-opus-v1.json"),
            load_json(VERSIONED / "role-assignment-executor-sol-v1.json"),
            load_json(VERSIONED / "role-assignment-executor-lite-v1.json"),
        ]
        role_state = RoleAssignmentAuthorityState(
            assignments={
                (r["entity"]["entity_type"], r["entity"]["entity_id"], r["entity"]["entity_version"]): r
                for r in roles
            },
            latest_versions={
                (r["entity"]["entity_type"], r["entity"]["entity_id"]): 1 for r in roles
            },
        )
        cards = [
            load_json(VERSIONED / "agent-card-opus-v1.json"),
            load_json(VERSIONED / "agent-card-sol-v1.json"),
            load_json(VERSIONED / "agent-card-lite-v1.json"),
        ]
        card_state = AgentCardState(
            cards={
                (c["entity"]["entity_type"], c["entity"]["entity_id"], c["entity"]["entity_version"]): c
                for c in cards
            },
            latest_versions={
                (c["entity"]["entity_type"], c["entity"]["entity_id"]): 1 for c in cards
            },
        )
        return team_state, role_state, card_state

    def test_plan_resolves_team_roles_cards_and_quota_time(self) -> None:
        team_state, role_state, card_state = self.build_states()
        self.assertEqual(
            [],
            validate_execution_resource_plan_against_state(
                self.plan,
                team_state,
                role_state,
                card_state,
                evaluated_at="2026-08-05T15:40:00Z",
            ),
        )

    def test_plan_rejects_card_and_team_mismatch(self) -> None:
        team_state, role_state, card_state = self.build_states()
        bad = deepcopy(self.plan)
        bad["resources"][0]["agent_card"]["entity_id"] = "agent-lite-01"
        bad["resources"][0]["quota"]["observed_at"] = "2026-08-05T16:00:00Z"
        errors = validate_execution_resource_plan_against_state(
            bad, team_state, role_state, card_state, evaluated_at="2026-08-05T15:40:00Z"
        )
        self.assertIn("execution_resource_agent_card_ref_mismatch", errors)
        self.assertIn("execution_resource_quota_observation_in_future", errors)

    def test_transition_records_exhaustion_and_fallback(self) -> None:
        v1 = load_json(VERSIONED / "execution-resource-plan-pump-v1.json")
        v2 = load_json(VERSIONED / "execution-resource-plan-pump-v2.json")
        self.assertEqual([], validate_execution_resource_plan_transition(v2, v1))
        current_rule = v2["legal_payload"]["routing_rules"][0]
        self.assertEqual("sol-resource", current_rule["active_resource_id"])
        self.assertEqual("quota_exhausted", current_rule["activation_reason"])
        self.assertTrue(current_rule["additional_review_required"])

    def test_scope_and_terminal_state_are_immutable(self) -> None:
        v1 = load_json(VERSIONED / "execution-resource-plan-pump-v1.json")
        bad = deepcopy(v1)
        bad["entity"]["entity_version"] = 2
        bad["previous_version"] = deepcopy(v1["entity"])
        bad["created_by_event_id"] = "evt-bad-plan-v2"
        bad["change_metadata"] = {"change_reason": "semantic", "substantive_effect": "may_affect_dependents"}
        bad["legal_payload"]["goal"]["entity_id"] = "other-goal"
        self.assertIn(
            "execution_resource_plan_goal_changed",
            validate_execution_resource_plan_transition(bad, v1),
        )

    def test_management_operations_are_not_available_to_executor(self) -> None:
        role = load_json(EXAMPLES / "role-assignment" / "valid" / "executor-lite.json")
        role["authority_scope"]["operations"].append("execution_resource_plan.create")
        self.assertIn(
            "execution_resource_plan_management_requires_orchestrator_or_owner",
            validate_role_assignment_semantics(role),
        )

    def test_envelope_dispatch_checks_initial_status(self) -> None:
        record = load_json(VERSIONED / "execution-resource-plan-pump-v1.json")
        record["legal_payload"]["status"] = "suspended"
        record["legal_payload"]["status_reason"] = "test"
        envelope = {
            "kind": "event",
            "event_class": "protocol_event",
            "type": "entity.version.created",
            "message_id": record["created_by_event_id"],
            "actor": {"principal_type": "human", "principal_id": "human-owner-001"},
            "authorized_by": {"entity_type": "role_assignment", "entity_id": "role-assignment-owner-001", "entity_version": 5},
            "recorded_by": {"principal_type": "runtime", "principal_id": "runtime-01"},
            "received_at": "2026-08-05T15:40:00Z",
            "recorded_at": "2026-08-05T15:40:01Z",
            "entity": deepcopy(record["entity"]),
            "precondition": {"mode": "create", "expected_absent": True, "target_identity": {"entity_type": "execution_resource_plan", "entity_id": "execution-resource-plan-pump-001"}},
            "payload_schema": "urn:w1-cip:schema:0.1:versioned-entity",
            "payload": record,
        }
        self.assertIn(
            "execution_resource_plan_initial_status_not_active",
            validate_protocol_envelope_semantics(envelope),
        )


if __name__ == "__main__":
    unittest.main()
