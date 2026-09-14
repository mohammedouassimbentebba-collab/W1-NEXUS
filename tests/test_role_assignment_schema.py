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
    RoleAssignmentAuthorityState,
    get_required_authority_operation,
    validate_envelope_role_assignment_authority,
    validate_role_assignment_capability,
    validate_role_assignment_semantics,
    validate_role_assignment_transition,
)

SCHEMA_PATH = ROOT / "schemas" / "w1-cip" / "0.1" / "role-assignment.schema.json"
PRINCIPAL_REF_PATH = ROOT / "schemas" / "w1-cip" / "0.1" / "principal-ref.schema.json"
ENTITY_REF_PATH = ROOT / "schemas" / "w1-cip" / "0.1" / "entity-ref.schema.json"
EXAMPLES = ROOT / "examples" / "role-assignment"
ENVELOPES = ROOT / "examples" / "protocol-envelope" / "valid"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class RoleAssignmentSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = load_json(SCHEMA_PATH)
        cls.principal_ref_schema = load_json(PRINCIPAL_REF_PATH)
        cls.entity_ref_schema = load_json(ENTITY_REF_PATH)
        for schema in (cls.schema, cls.principal_ref_schema, cls.entity_ref_schema):
            Draft202012Validator.check_schema(schema)
        registry = Registry()
        for schema in (cls.principal_ref_schema, cls.entity_ref_schema):
            registry = registry.with_resource(
                schema["$id"], Resource.from_contents(schema)
            )
        cls.validator = Draft202012Validator(
            cls.schema,
            registry=registry,
            format_checker=FormatChecker(),
        )

    def structural_errors(self, instance: dict) -> list:
        return sorted(self.validator.iter_errors(instance), key=lambda error: list(error.path))

    def test_valid_examples(self) -> None:
        paths = sorted((EXAMPLES / "valid").glob("*.json"))
        self.assertTrue(paths, "no valid RoleAssignment examples found")
        for path in paths:
            with self.subTest(path=path.name):
                instance = load_json(path)
                self.assertEqual([], self.structural_errors(instance))
                self.assertEqual([], validate_role_assignment_semantics(instance))

    def test_structurally_invalid_examples(self) -> None:
        paths = sorted((EXAMPLES / "invalid-structural").glob("*.json"))
        self.assertTrue(paths, "no structurally invalid RoleAssignment examples found")
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
                self.assertEqual(expected_codes, validate_role_assignment_semantics(instance))

    def test_required_operation_is_derived_from_target(self) -> None:
        command = load_json(ENVELOPES / "command-create.json")
        event = load_json(ENVELOPES / "protocol-event-next-version.json")
        self.assertEqual("claim.create", get_required_authority_operation(command))
        self.assertEqual("claim.next_version", get_required_authority_operation(event))

    def test_role_assignment_transition_keeps_subject_role_and_start(self) -> None:
        previous = self._versioned_assignment(version=1)
        current = self._versioned_assignment(version=2, previous=previous)
        current["legal_payload"]["subject"]["principal_id"] = "agent-other-01"
        current["legal_payload"]["role"] = "reviewer"
        current["legal_payload"]["valid_from"] = "2026-08-02T18:01:00Z"
        errors = validate_role_assignment_transition(current, previous)
        self.assertIn("role_assignment_subject_changed", errors)
        self.assertIn("role_assignment_role_changed", errors)
        self.assertIn("role_assignment_valid_from_changed", errors)

    def test_revocation_is_terminal(self) -> None:
        previous = self._versioned_assignment(version=1, status="revoked")
        current = self._versioned_assignment(version=2, previous=previous, status="active")
        self.assertIn(
            "role_assignment_revocation_final",
            validate_role_assignment_transition(current, previous),
        )

    def test_valid_authority_for_command_and_event(self) -> None:
        state = self._authority_state()
        command = load_json(ENVELOPES / "command-create.json")
        event = load_json(ENVELOPES / "protocol-event-next-version.json")
        self.assertEqual(
            [],
            validate_envelope_role_assignment_authority(
                command, state, acceptance_time="2026-08-02T18:20:00Z"
            ),
        )
        self.assertEqual([], validate_envelope_role_assignment_authority(event, state))

    def test_authority_matches_on_behalf_of_not_runtime_service(self) -> None:
        state = self._authority_state()
        event = load_json(ENVELOPES / "protocol-event-next-version.json")
        self.assertEqual([], validate_envelope_role_assignment_authority(event, state))
        event["on_behalf_of"]["principal_id"] = "agent-other-01"
        self.assertIn(
            "authority_subject_mismatch",
            validate_envelope_role_assignment_authority(event, state),
        )

    def test_stale_suspended_expired_and_scope_denied(self) -> None:
        command = load_json(ENVELOPES / "command-create.json")

        stale = self._authority_state(latest=3)
        self.assertIn(
            "authority_version_not_current",
            validate_envelope_role_assignment_authority(
                command, stale, acceptance_time="2026-08-02T18:20:00Z"
            ),
        )

        suspended = self._authority_state(status="suspended")
        self.assertIn(
            "authority_inactive",
            validate_envelope_role_assignment_authority(
                command, suspended, acceptance_time="2026-08-02T18:20:00Z"
            ),
        )

        expired = self._authority_state(valid_until="2026-08-02T18:20:00Z")
        self.assertIn(
            "authority_expired",
            validate_envelope_role_assignment_authority(
                command, expired, acceptance_time="2026-08-02T18:20:00Z"
            ),
        )

        denied = self._authority_state(operations=["contribution.create"])
        self.assertIn(
            "authority_scope_denied",
            validate_envelope_role_assignment_authority(
                command, denied, acceptance_time="2026-08-02T18:20:00Z"
            ),
        )

    def test_command_requires_explicit_acceptance_time(self) -> None:
        command = load_json(ENVELOPES / "command-create.json")
        self.assertIn(
            "authority_evaluation_time_required",
            validate_envelope_role_assignment_authority(command, self._authority_state()),
        )

    def test_human_owner_authorizes_role_assignment_creation(self) -> None:
        event = load_json(ENVELOPES / "role-assignment-create.json")
        owner_record = load_json(
            ROOT / "examples" / "versioned-entity" / "valid" / "role-assignment-owner-v1.json"
        )
        state = RoleAssignmentAuthorityState(
            assignments={("role_assignment", "role-assignment-owner-001", 1): owner_record},
            latest_versions={("role_assignment", "role-assignment-owner-001"): 1},
        )
        self.assertEqual([], validate_envelope_role_assignment_authority(event, state))

    def test_authority_not_found_and_not_yet_valid(self) -> None:
        command = load_json(ENVELOPES / "command-create.json")
        self.assertEqual(
            ["authority_role_assignment_not_found"],
            validate_envelope_role_assignment_authority(
                command,
                RoleAssignmentAuthorityState(),
                acceptance_time="2026-08-02T18:20:00Z",
            ),
        )
        future = self._authority_state()
        record = future.assignments[("role_assignment", "role-assignment-17", 2)]
        record["legal_payload"]["valid_from"] = "2026-08-02T18:21:00Z"
        self.assertIn(
            "authority_not_yet_valid",
            validate_envelope_role_assignment_authority(
                command, future, acceptance_time="2026-08-02T18:20:00Z"
            ),
        )

    def test_capability_check(self) -> None:
        state = self._authority_state(
            role="reviewer",
            capabilities=["classify_editorial_correction"],
            operations=["review.create"],
        )
        authority_ref = {
            "entity_type": "role_assignment",
            "entity_id": "role-assignment-17",
            "entity_version": 2,
        }
        principal = {
            "principal_type": "agent",
            "principal_id": "agent-electronics-01",
        }
        self.assertEqual(
            [],
            validate_role_assignment_capability(
                authority_ref,
                principal,
                "classify_editorial_correction",
                "2026-08-02T18:20:00Z",
                state,
            ),
        )
        self.assertIn(
            "authority_capability_denied",
            validate_role_assignment_capability(
                authority_ref,
                principal,
                "accept_risk",
                "2026-08-02T18:20:00Z",
                state,
            ),
        )

    def _authority_state(
        self,
        *,
        latest: int = 2,
        status: str = "active",
        valid_until: str | None = None,
        operations: list[str] | None = None,
        capabilities: list[str] | None = None,
        role: str = "executor",
    ) -> RoleAssignmentAuthorityState:
        scope: dict[str, list[str]] = {
            "operations": operations or ["claim.create", "claim.next_version"]
        }
        if capabilities:
            scope["capabilities"] = capabilities
        payload = {
            "subject": {
                "principal_type": "agent",
                "principal_id": "agent-electronics-01",
            },
            "role": role,
            "authority_scope": scope,
            "status": status,
            "valid_from": "2026-08-02T18:00:00Z",
            "assignment_reason": "test authority",
        }
        if status != "active":
            payload["status_reason"] = "test status"
        if valid_until:
            payload["valid_until"] = valid_until
        record = {
            "entity": {
                "entity_type": "role_assignment",
                "entity_id": "role-assignment-17",
                "entity_version": 2,
            },
            "created_by_event_id": "evt-role-assignment-17-v2",
            "legal_payload_schema": "urn:w1-cip:entity-payload:0.1:role-assignment",
            "legal_payload": payload,
        }
        return RoleAssignmentAuthorityState(
            assignments={("role_assignment", "role-assignment-17", 2): record},
            latest_versions={("role_assignment", "role-assignment-17"): latest},
        )

    def _versioned_assignment(
        self,
        *,
        version: int,
        previous: dict | None = None,
        status: str = "active",
    ) -> dict:
        payload = load_json(EXAMPLES / "valid" / "executor.json")
        payload["status"] = status
        if status == "active":
            payload.pop("status_reason", None)
        else:
            payload["status_reason"] = "status changed for test"
        result = {
            "entity": {
                "entity_type": "role_assignment",
                "entity_id": "role-assignment-executor-001",
                "entity_version": version,
            },
            "created_by_event_id": f"evt-role-assignment-executor-v{version}",
            "legal_payload_schema": "urn:w1-cip:entity-payload:0.1:role-assignment",
            "legal_payload": payload,
        }
        if version > 1:
            result["previous_version"] = deepcopy(previous["entity"])
            result["change_metadata"] = {
                "change_reason": "semantic",
                "substantive_effect": "may_affect_dependents",
            }
            # ensure a real payload change for the generic VersionedEntity transition
            result["legal_payload"]["assignment_reason"] = "updated assignment reason"
        return result


if __name__ == "__main__":
    unittest.main()
