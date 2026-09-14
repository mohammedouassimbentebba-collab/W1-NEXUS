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
    DecisionState,
    ProtocolEventState,
    ProtocolEventTargetState,
    RoleAssignmentAuthorityState,
    active_protocol_effects_for_subject,
    protocol_event_payloads_for_decision_change,
    protocol_event_payloads_for_reopened_challenge,
    replay_protocol_log,
    validate_protocol_envelope_semantics,
    validate_protocol_event_against_authority_and_state,
    validate_protocol_event_semantics,
    validate_protocol_event_transition,
)

SCHEMA_DIR = ROOT / "schemas" / "w1-cip" / "0.1"
SCHEMA_PATH = SCHEMA_DIR / "protocol-event.schema.json"
VERSIONED_SCHEMA_PATH = SCHEMA_DIR / "versioned-entity.schema.json"
EXAMPLES = ROOT / "examples" / "protocol-event"
VERSIONED = ROOT / "examples" / "versioned-entity" / "valid"
ENVELOPES = ROOT / "examples" / "protocol-envelope" / "valid"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class ProtocolEventSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = load_json(SCHEMA_PATH)
        Draft202012Validator.check_schema(cls.schema)
        registry = Registry()
        for path in sorted(SCHEMA_DIR.glob("*.schema.json")):
            schema = load_json(path)
            registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))
        cls.registry = registry
        cls.validator = Draft202012Validator(
            cls.schema, registry=registry, format_checker=FormatChecker()
        )

    def structural_errors(self, instance: dict) -> list:
        return sorted(self.validator.iter_errors(instance), key=lambda error: list(error.path))

    def test_valid_examples(self) -> None:
        paths = sorted((EXAMPLES / "valid").glob("*.json"))
        self.assertTrue(paths)
        for path in paths:
            with self.subTest(path=path.name):
                payload = load_json(path)
                self.assertEqual([], self.structural_errors(payload))
                self.assertEqual([], validate_protocol_event_semantics(payload))

    def test_structurally_invalid_examples(self) -> None:
        paths = sorted((EXAMPLES / "invalid-structural").glob("*.json"))
        self.assertTrue(paths)
        for path in paths:
            with self.subTest(path=path.name):
                self.assertTrue(self.structural_errors(load_json(path)))

    def test_semantically_invalid_examples(self) -> None:
        directory = EXAMPLES / "invalid-semantic"
        manifest = load_json(directory / "manifest.json")
        for filename, expected in manifest.items():
            with self.subTest(path=filename):
                payload = load_json(directory / filename)
                self.assertEqual([], self.structural_errors(payload))
                self.assertIn(expected, validate_protocol_event_semantics(payload))

    def test_versioned_entity_binds_protocol_event_and_forbids_v2(self) -> None:
        schema = load_json(VERSIONED_SCHEMA_PATH)
        validator = Draft202012Validator(
            schema, registry=self.registry, format_checker=FormatChecker()
        )
        record = load_json(VERSIONED / "protocol-event-decision-reassessment-v1.json")
        self.assertEqual([], list(validator.iter_errors(record)))
        v2 = deepcopy(record)
        v2["entity"]["entity_version"] = 2
        v2["previous_version"] = deepcopy(record["entity"])
        v2["change_metadata"] = {
            "change_reason": "semantic",
            "substantive_effect": "may_affect_dependents",
        }
        self.assertTrue(list(validator.iter_errors(v2)))
        self.assertIn("protocol_event_version_must_be_one", validate_protocol_event_transition(v2, record))

    def test_envelope_binds_message_identity_type_and_causation(self) -> None:
        envelope = load_json(ENVELOPES / "protocol-event-reassessment-required.json")
        self.assertEqual([], validate_protocol_envelope_semantics(envelope))
        wrong = deepcopy(envelope)
        wrong["type"] = "protocol_event.invalidation_required"
        self.assertIn("protocol_event_envelope_type_mismatch", validate_protocol_envelope_semantics(wrong))
        wrong = deepcopy(envelope)
        wrong["payload"]["entity"]["entity_id"] = "different-event"
        self.assertIn("protocol_event_identity_must_match_message_id", validate_protocol_envelope_semantics(wrong))
        wrong = deepcopy(envelope)
        wrong.pop("causation_id")
        self.assertIn("protocol_event_causation_required", validate_protocol_envelope_semantics(wrong))

    def build_states(self):
        owner = load_json(VERSIONED / "role-assignment-owner-v4.json")
        decision = load_json(VERSIONED / "decision-motor-driver-v3.json")
        challenge = load_json(VERSIONED / "challenge-startup-current-v5.json")
        return (
            RoleAssignmentAuthorityState(
                assignments={("role_assignment", "role-assignment-owner-001", 4): owner},
                latest_versions={("role_assignment", "role-assignment-owner-001"): 4},
            ),
            ProtocolEventTargetState(
                records={
                    ("decision", "decision-motor-driver-001", 3): decision,
                    ("challenge", "challenge-startup-current-001", 5): challenge,
                },
                latest_versions={
                    ("decision", "decision-motor-driver-001"): 3,
                    ("challenge", "challenge-startup-current-001"): 5,
                },
            ),
        )

    def test_reassessment_event_validates_authority_subject_trigger_and_classification(self) -> None:
        role_state, target_state = self.build_states()
        envelope = load_json(ENVELOPES / "protocol-event-reassessment-required.json")
        self.assertEqual(
            [],
            validate_protocol_event_against_authority_and_state(
                envelope, target_state, ProtocolEventState(), role_state
            ),
        )
        wrong = deepcopy(envelope)
        wrong["causation_id"] = "evt-unrelated"
        self.assertIn(
            "protocol_event_causation_not_a_trigger_event",
            validate_protocol_event_against_authority_and_state(
                wrong, target_state, ProtocolEventState(), role_state
            ),
        )
        wrong = deepcopy(envelope)
        wrong["payload"]["legal_payload"]["classification"] = "public"
        self.assertIn(
            "protocol_event_classification_below_sources",
            validate_protocol_event_against_authority_and_state(
                wrong, target_state, ProtocolEventState(), role_state
            ),
        )

    def test_compensation_disables_effect_without_deleting_event(self) -> None:
        role_state, _ = self.build_states()
        effect = load_json(VERSIONED / "protocol-event-decision-reassessment-v1.json")
        state = ProtocolEventState(
            events={("protocol_event", "evt-decision-reassessment-required-001", 1): effect}
        )
        envelope = load_json(ENVELOPES / "protocol-event-compensation-recorded.json")
        self.assertEqual(
            [],
            validate_protocol_event_against_authority_and_state(
                envelope, ProtocolEventTargetState(), state, role_state
            ),
        )
        compensated = ProtocolEventState(
            events=state.events,
            compensated_events=frozenset({("protocol_event", "evt-decision-reassessment-required-001", 1)}),
        )
        self.assertIn(
            "protocol_event_effect_already_compensated",
            validate_protocol_event_against_authority_and_state(
                envelope, ProtocolEventTargetState(), compensated, role_state
            ),
        )
        subject = effect["legal_payload"]["subject"]
        self.assertEqual([effect], active_protocol_effects_for_subject(subject, state))
        self.assertEqual([], active_protocol_effects_for_subject(subject, compensated))

    @staticmethod
    def minimal_replay_log() -> list[dict]:
        session = "session-replay-001"
        runtime = {"principal_type": "runtime", "principal_id": "runtime-local-001"}
        owner = {"principal_type": "human", "principal_id": "human-owner-001"}
        v1 = {
            "entity": {"entity_type": "role_assignment", "entity_id": "owner", "entity_version": 1},
            "created_by_event_id": "evt-1",
            "legal_payload_schema": "urn:w1-cip:entity-payload:0.1:role-assignment",
            "legal_payload": {
                "subject": owner,
                "role": "human_owner",
                "authority_scope": {"operations": ["role_assignment.create", "role_assignment.next_version"]},
                "status": "active",
                "valid_from": "2026-08-05T15:00:00Z",
                "assignment_reason": "bootstrap owner",
            },
        }
        e1 = {
            "protocol": "w1-cip", "protocol_version": "0.1", "message_id": "evt-1", "session_id": session,
            "kind": "event", "event_class": "protocol_event", "type": "session.bootstrap.completed",
            "actor": runtime, "recorded_by": runtime, "sequence": 1,
            "occurred_at": "2026-08-05T15:00:00Z", "received_at": "2026-08-05T15:00:00Z", "recorded_at": "2026-08-05T15:00:00Z",
            "correlation_id": "corr-replay", "related_events": [],
            "entity": v1["entity"], "precondition": {"mode": "create", "expected_absent": True, "target_identity": {"entity_type": "role_assignment", "entity_id": "owner"}},
            "bootstrap": {"mode": "local_runtime", "bootstrap_id": "bootstrap-replay", "scope": "create_initial_role_assignment"},
            "payload_schema": "urn:w1-cip:schema:0.1:versioned-entity", "payload": v1,
        }
        v2 = deepcopy(v1)
        v2["entity"]["entity_version"] = 2
        v2["created_by_event_id"] = "evt-2"
        v2["previous_version"] = deepcopy(v1["entity"])
        v2["change_metadata"] = {"change_reason": "semantic", "substantive_effect": "may_affect_dependents"}
        v2["legal_payload"]["authority_scope"]["operations"] += ["artifact.create", "protocol_event.create"]
        e2 = {
            "protocol": "w1-cip", "protocol_version": "0.1", "message_id": "evt-2", "session_id": session,
            "kind": "event", "event_class": "protocol_event", "type": "entity.version.created",
            "actor": owner, "authorized_by": v1["entity"], "recorded_by": runtime, "sequence": 2,
            "occurred_at": "2026-08-05T15:00:01Z", "received_at": "2026-08-05T15:00:01Z", "recorded_at": "2026-08-05T15:00:01Z",
            "correlation_id": "corr-replay", "causation_id": "evt-1", "related_events": [],
            "entity": v2["entity"], "precondition": {"mode": "next_version", "expected_entity_version": 1, "target": v1["entity"]},
            "payload_schema": "urn:w1-cip:schema:0.1:versioned-entity", "payload": v2,
        }
        def artifact_event(mid: str, seq: int, entity_id: str) -> dict:
            record = {
                "entity": {"entity_type": "artifact", "entity_id": entity_id, "entity_version": 1},
                "created_by_event_id": mid,
                "legal_payload_schema": "urn:example:artifact",
                "legal_payload": {"classification": "internal", "label": entity_id},
            }
            return {
                "protocol": "w1-cip", "protocol_version": "0.1", "message_id": mid, "session_id": session,
                "kind": "event", "event_class": "protocol_event", "type": "entity.version.created",
                "actor": owner, "authorized_by": v2["entity"], "recorded_by": runtime, "sequence": seq,
                "occurred_at": f"2026-08-05T15:00:0{seq}Z", "received_at": f"2026-08-05T15:00:0{seq}Z", "recorded_at": f"2026-08-05T15:00:0{seq}Z",
                "correlation_id": "corr-replay", "causation_id": "evt-2", "related_events": [],
                "entity": record["entity"], "precondition": {"mode": "create", "expected_absent": True, "target_identity": {"entity_type": "artifact", "entity_id": entity_id}},
                "payload_schema": "urn:w1-cip:schema:0.1:versioned-entity", "payload": record,
            }
        subject = artifact_event("evt-3", 3, "subject")
        trigger = artifact_event("evt-4", 4, "trigger")
        effect_record = {
            "entity": {"entity_type": "protocol_event", "entity_id": "evt-5", "entity_version": 1},
            "created_by_event_id": "evt-5",
            "legal_payload_schema": "urn:w1-cip:entity-payload:0.1:protocol-event",
            "legal_payload": {
                "event_type": "reassessment_required",
                "subject": subject["entity"], "trigger_refs": [trigger["entity"]],
                "reason": {"code": "fixture_change", "description": "Fixture changed."},
                "classification": "internal", "propagation": "none", "blocking": True,
                "required_follow_up": {"operation": "artifact.next_version"},
            },
        }
        effect = {
            "protocol": "w1-cip", "protocol_version": "0.1", "message_id": "evt-5", "session_id": session,
            "kind": "event", "event_class": "protocol_event", "type": "protocol_event.reassessment_required",
            "actor": owner, "authorized_by": v2["entity"], "recorded_by": runtime, "sequence": 5,
            "occurred_at": "2026-08-05T15:00:05Z", "received_at": "2026-08-05T15:00:05Z", "recorded_at": "2026-08-05T15:00:05Z",
            "correlation_id": "corr-replay", "causation_id": "evt-4", "related_events": [],
            "entity": effect_record["entity"], "precondition": {"mode": "create", "expected_absent": True, "target_identity": {"entity_type": "protocol_event", "entity_id": "evt-5"}},
            "payload_schema": "urn:w1-cip:schema:0.1:versioned-entity", "payload": effect_record,
        }
        comp_record = {
            "entity": {"entity_type": "protocol_event", "entity_id": "evt-6", "entity_version": 1},
            "created_by_event_id": "evt-6",
            "legal_payload_schema": "urn:w1-cip:entity-payload:0.1:protocol-event",
            "legal_payload": {
                "event_type": "compensation_recorded", "subject": effect_record["entity"],
                "reason": {"code": "fixture_restored", "description": "Fixture was restored."},
                "classification": "internal", "propagation": "none", "blocking": False,
                "compensation": {"compensates_event": effect_record["entity"], "reason": "Disable the effect."},
            },
        }
        compensation = {
            "protocol": "w1-cip", "protocol_version": "0.1", "message_id": "evt-6", "session_id": session,
            "kind": "event", "event_class": "protocol_event", "type": "protocol_event.compensation_recorded",
            "actor": owner, "authorized_by": v2["entity"], "recorded_by": runtime, "sequence": 6,
            "occurred_at": "2026-08-05T15:00:06Z", "received_at": "2026-08-05T15:00:06Z", "recorded_at": "2026-08-05T15:00:06Z",
            "correlation_id": "corr-replay", "causation_id": "evt-5", "related_events": [],
            "entity": comp_record["entity"], "precondition": {"mode": "create", "expected_absent": True, "target_identity": {"entity_type": "protocol_event", "entity_id": "evt-6"}},
            "payload_schema": "urn:w1-cip:schema:0.1:versioned-entity", "payload": comp_record,
        }
        return [e1, e2, subject, trigger, effect, compensation]

    def test_replay_is_deterministic_and_compensation_removes_only_effect(self) -> None:
        log = self.minimal_replay_log()
        state, errors = replay_protocol_log(log)
        self.assertEqual([], errors)
        self.assertEqual(6, state.last_sequence)
        self.assertIn(("protocol_event", "evt-5", 1), state.compensated_protocol_events)
        self.assertNotIn(("protocol_event", "evt-5", 1), state.active_protocol_events)
        self.assertIn(("protocol_event", "evt-5", 1), state.records)
        second, second_errors = replay_protocol_log(deepcopy(log))
        self.assertEqual([], second_errors)
        self.assertEqual(state, second)

    def test_replay_rejects_sequence_gaps_future_causation_and_session_mix(self) -> None:
        log = self.minimal_replay_log()
        wrong = deepcopy(log)
        wrong[2]["sequence"] = 7
        _, errors = replay_protocol_log(wrong)
        self.assertIn("protocol_log_sequence_gap", errors)
        wrong = deepcopy(log)
        wrong[2]["causation_id"] = "evt-future"
        _, errors = replay_protocol_log(wrong)
        self.assertIn("protocol_log_causation_not_prior", errors)
        wrong = deepcopy(log)
        wrong[2]["session_id"] = "different-session"
        _, errors = replay_protocol_log(wrong)
        self.assertIn("protocol_log_session_mismatch", errors)

    def test_builders_emit_reassessment_and_dependency_effects(self) -> None:
        decision = load_json(VERSIONED / "decision-motor-driver-v3.json")
        challenge = load_json(VERSIONED / "challenge-startup-current-v5.json")
        decision_state = DecisionState(
            decisions={("decision", "decision-motor-driver-001", 3): decision},
            latest_versions={("decision", "decision-motor-driver-001"): 3},
        )
        payloads = protocol_event_payloads_for_reopened_challenge(challenge, decision_state)
        self.assertEqual(1, len(payloads))
        self.assertEqual("reassessment_required", payloads[0]["event_type"])
        self.assertEqual("decision.next_version", payloads[0]["required_follow_up"]["operation"])

        dependent = deepcopy(decision)
        dependent["entity"] = {"entity_type": "decision", "entity_id": "dependent-decision", "entity_version": 1}
        dependent["legal_payload"]["depends_on_decisions"] = [{
            "decision": decision["entity"], "critical": True,
            "on_revoked": "invalidate", "on_superseded": "reassess", "on_reassessment": "block",
        }]
        state = DecisionState(
            decisions={
                ("decision", "decision-motor-driver-001", 3): decision,
                ("decision", "dependent-decision", 1): dependent,
            },
            latest_versions={
                ("decision", "decision-motor-driver-001"): 3,
                ("decision", "dependent-decision"): 1,
            },
        )
        payloads = protocol_event_payloads_for_decision_change(decision, state, change="revoked")
        self.assertEqual("invalidation_required", payloads[0]["event_type"])
        self.assertEqual("dependent-decision", payloads[0]["subject"]["entity_id"])


if __name__ == "__main__":
    unittest.main()
