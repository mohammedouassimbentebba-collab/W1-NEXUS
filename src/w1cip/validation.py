"""Semantic checks for W1-CIP schemas that JSON Schema cannot express."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

PrincipalKey = tuple[str, str]


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _same_identity(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return (
        left.get("entity_type") == right.get("entity_type")
        and left.get("entity_id") == right.get("entity_id")
    )


def _principal_key(principal: dict[str, Any] | None) -> PrincipalKey | None:
    if principal is None:
        return None
    principal_type = principal.get("principal_type")
    principal_id = principal.get("principal_id")
    if not isinstance(principal_type, str) or not isinstance(principal_id, str):
        return None
    return principal_type, principal_id


def get_effective_principal(envelope: dict[str, Any]) -> PrincipalKey | None:
    """Return the principal whose authority must cover the envelope action."""

    represented = _principal_key(envelope.get("on_behalf_of"))
    return represented if represented is not None else _principal_key(envelope.get("actor"))


def get_required_authority_operation(envelope: dict[str, Any]) -> str | None:
    """Derive the exact operation a RoleAssignment must authorize.

    Commands and state-changing protocol events derive their operation from the
    target entity and precondition, so a generic event such as
    ``entity.version.created`` does not grant cross-entity authority. Other
    envelopes use their explicit message type. Bootstrap has no derived
    authority because it is the single root exception.
    """

    if envelope.get("type") == "session.bootstrap.completed":
        return None

    precondition = envelope.get("precondition")
    if isinstance(precondition, dict):
        mode = precondition.get("mode")
        target = (
            precondition.get("target_identity")
            if mode == "create"
            else precondition.get("target")
        )
        if isinstance(target, dict):
            entity_type = target.get("entity_type")
            if isinstance(entity_type, str) and mode in {"create", "next_version"}:
                return f"{entity_type}.{mode}"

    message_type = envelope.get("type")
    return message_type if isinstance(message_type, str) else None


def validate_protocol_envelope_semantics(envelope: dict[str, Any]) -> list[str]:
    """Return stable error codes for local cross-field envelope violations.

    Call this only after structural JSON Schema validation succeeds. Checks that
    require repository state—such as identifier uniqueness, authority-subject
    matching, and recorder trust—belong to the session validator or event store.
    """

    errors: list[str] = []

    actor_key = _principal_key(envelope.get("actor"))
    represented_key = _principal_key(envelope.get("on_behalf_of"))
    if represented_key is not None and represented_key == actor_key:
        errors.append("actor_same_as_on_behalf_of")

    if envelope.get("type") == "session.bootstrap.completed":
        if actor_key != _principal_key(envelope.get("recorded_by")):
            errors.append("bootstrap_actor_recorder_mismatch")

    causation_id = envelope.get("causation_id")
    if causation_id is not None and causation_id in envelope.get("related_events", []):
        errors.append("causation_listed_as_related")

    required_operation = get_required_authority_operation(envelope)
    if (
        envelope.get("kind") == "command"
        and required_operation is not None
        and envelope.get("type") != required_operation
    ):
        errors.append("command_type_precondition_mismatch")

    if envelope.get("type") == "session.bootstrap.completed":
        legal_payload = envelope.get("payload", {}).get("legal_payload", {})
        operations = legal_payload.get("authority_scope", {}).get("operations", [])
        if set(operations) != {
            "role_assignment.create",
            "role_assignment.next_version",
        }:
            errors.append("bootstrap_authority_scope_not_minimal")
        if legal_payload.get("valid_from") != envelope.get("recorded_at"):
            errors.append("bootstrap_authority_start_mismatch")
        if "valid_until" in legal_payload:
            errors.append("bootstrap_authority_must_not_expire")

    if envelope.get("kind") != "event":
        return errors

    received_at = envelope.get("received_at")
    recorded_at = envelope.get("recorded_at")
    if received_at and recorded_at:
        if _parse_datetime(recorded_at) < _parse_datetime(received_at):
            errors.append("recorded_before_received")

    if envelope.get("event_class") != "protocol_event":
        return errors

    precondition = envelope["precondition"]
    entity = envelope["entity"]
    mode = precondition["mode"]

    if mode == "create":
        target = precondition["target_identity"]
        if not _same_identity(entity, target):
            errors.append("result_identity_mismatch")
        if entity.get("entity_version") != 1:
            errors.append("created_entity_version_not_one")
    elif mode == "next_version":
        target = precondition["target"]
        expected = precondition["expected_entity_version"]
        if target.get("entity_version") != expected:
            errors.append("precondition_target_version_mismatch")
        if not _same_identity(entity, target):
            errors.append("result_identity_mismatch")
        if entity.get("entity_version") != expected + 1:
            errors.append("result_version_not_next")

    if envelope.get("payload_schema") == "urn:w1-cip:schema:0.1:versioned-entity":
        versioned_payload = envelope.get("payload", {})
        if not _same_entity_ref(entity, versioned_payload.get("entity")):
            errors.append("versioned_entity_result_mismatch")
        if versioned_payload.get("created_by_event_id") != envelope.get("message_id"):
            errors.append("versioned_entity_creator_event_mismatch")
        if mode == "next_version" and not _same_entity_ref(
            precondition.get("target"), versioned_payload.get("previous_version")
        ):
            errors.append("versioned_entity_previous_mismatch")
        errors.extend(validate_versioned_entity_semantics(versioned_payload))
        if versioned_payload.get("legal_payload_schema") == GOAL_CONTRACT_PAYLOAD_SCHEMA:
            errors.extend(
                validate_goal_contract_semantics(versioned_payload.get("legal_payload", {}))
            )
            if (
                versioned_payload.get("entity", {}).get("entity_version") == 1
                and versioned_payload.get("legal_payload", {}).get("status") != "active"
            ):
                errors.append("goal_contract_initial_status_not_active")

        if versioned_payload.get("legal_payload_schema") == TEAM_PLAN_PAYLOAD_SCHEMA:
            errors.extend(
                validate_team_plan_semantics(versioned_payload.get("legal_payload", {}))
            )
            if (
                versioned_payload.get("entity", {}).get("entity_version") == 1
                and versioned_payload.get("legal_payload", {}).get("status") != "active"
            ):
                errors.append("team_plan_initial_status_not_active")

        if versioned_payload.get("legal_payload_schema") == CONTEXT_GRANT_PAYLOAD_SCHEMA:
            grant_payload = versioned_payload.get("legal_payload", {})
            errors.extend(validate_context_grant_semantics(grant_payload))
            if (
                versioned_payload.get("entity", {}).get("entity_version") == 1
                and grant_payload.get("status") != "active"
            ):
                errors.append("context_grant_initial_status_not_active")
            valid_from = grant_payload.get("valid_from")
            if (
                versioned_payload.get("entity", {}).get("entity_version") == 1
                and isinstance(valid_from, str)
                and isinstance(recorded_at, str)
                and _parse_datetime(valid_from) < _parse_datetime(recorded_at)
            ):
                errors.append("context_grant_backdated")

        if versioned_payload.get("legal_payload_schema") == TASK_PAYLOAD_SCHEMA:
            task_payload = versioned_payload.get("legal_payload", {})
            errors.extend(validate_task_semantics(task_payload))
            if (
                versioned_payload.get("entity", {}).get("entity_version") == 1
                and task_payload.get("status") != "created"
            ):
                errors.append("task_initial_status_not_created")

        if versioned_payload.get("legal_payload_schema") == CONTRIBUTION_PAYLOAD_SCHEMA:
            contribution_payload = versioned_payload.get("legal_payload", {})
            errors.extend(validate_contribution_semantics(contribution_payload))
            if (
                versioned_payload.get("entity", {}).get("entity_version") == 1
                and contribution_payload.get("status") != "active"
            ):
                errors.append("contribution_initial_status_not_active")
            authority_ref = envelope.get("authorized_by", {})
            if authority_ref.get("entity_type") != "role_assignment":
                errors.append("contribution_requires_role_assignment_authority")
            elif not _same_entity_ref(
                authority_ref, contribution_payload.get("authored_by_role_assignment")
            ):
                errors.append("contribution_authority_ref_mismatch")

        if versioned_payload.get("legal_payload_schema") == EVIDENCE_PAYLOAD_SCHEMA:
            evidence_payload = versioned_payload.get("legal_payload", {})
            errors.extend(validate_evidence_semantics(evidence_payload))
            if (
                versioned_payload.get("entity", {}).get("entity_version") == 1
                and evidence_payload.get("status") != "active"
            ):
                errors.append("evidence_initial_status_not_active")
            authority_ref = envelope.get("authorized_by", {})
            if authority_ref.get("entity_type") != "role_assignment":
                errors.append("evidence_requires_role_assignment_authority")
            elif not _same_entity_ref(
                authority_ref, evidence_payload.get("registered_by_role_assignment")
            ):
                errors.append("evidence_authority_ref_mismatch")

        if versioned_payload.get("legal_payload_schema") == VERIFICATION_PAYLOAD_SCHEMA:
            verification_payload = versioned_payload.get("legal_payload", {})
            errors.extend(validate_verification_semantics(verification_payload))
            if (
                versioned_payload.get("entity", {}).get("entity_version") == 1
                and verification_payload.get("status") != "active"
            ):
                errors.append("verification_initial_status_not_active")
            authority_ref = envelope.get("authorized_by", {})
            if authority_ref.get("entity_type") != "role_assignment":
                errors.append("verification_requires_role_assignment_authority")
            elif not _same_entity_ref(
                authority_ref, verification_payload.get("performed_by_role_assignment")
            ):
                errors.append("verification_authority_ref_mismatch")

        if versioned_payload.get("legal_payload_schema") == CHALLENGE_PAYLOAD_SCHEMA:
            challenge_payload = versioned_payload.get("legal_payload", {})
            errors.extend(validate_challenge_semantics(challenge_payload))
            if (
                versioned_payload.get("entity", {}).get("entity_version") == 1
                and challenge_payload.get("status") != "open"
            ):
                errors.append("challenge_initial_status_not_open")
            authority_ref = envelope.get("authorized_by", {})
            if authority_ref.get("entity_type") != "role_assignment":
                errors.append("challenge_requires_role_assignment_authority")
            elif not _same_entity_ref(
                authority_ref, challenge_payload.get("last_transition_by_role_assignment")
            ):
                errors.append("challenge_authority_ref_mismatch")

        if versioned_payload.get("legal_payload_schema") == REVIEW_PAYLOAD_SCHEMA:
            review_payload = versioned_payload.get("legal_payload", {})
            errors.extend(validate_review_semantics(review_payload))
            if (
                versioned_payload.get("entity", {}).get("entity_version") == 1
                and review_payload.get("status") != "active"
            ):
                errors.append("review_initial_status_not_active")
            authority_ref = envelope.get("authorized_by", {})
            if authority_ref.get("entity_type") != "role_assignment":
                errors.append("review_requires_role_assignment_authority")
            elif not _same_entity_ref(
                authority_ref, review_payload.get("reviewed_by_role_assignment")
            ):
                errors.append("review_authority_ref_mismatch")

        if versioned_payload.get("legal_payload_schema") == APPROVAL_PAYLOAD_SCHEMA:
            approval_payload = versioned_payload.get("legal_payload", {})
            errors.extend(validate_approval_semantics(approval_payload))
            if (
                versioned_payload.get("entity", {}).get("entity_version") == 1
                and approval_payload.get("status") != "active"
            ):
                errors.append("approval_initial_status_not_active")
            authority_ref = envelope.get("authorized_by", {})
            if authority_ref.get("entity_type") != "role_assignment":
                errors.append("approval_requires_role_assignment_authority")
            elif not _same_entity_ref(
                authority_ref, approval_payload.get("approved_by_role_assignment")
            ):
                errors.append("approval_authority_ref_mismatch")


        if versioned_payload.get("legal_payload_schema") == DECISION_PAYLOAD_SCHEMA:
            decision_payload = versioned_payload.get("legal_payload", {})
            errors.extend(validate_decision_semantics(decision_payload))
            if (
                versioned_payload.get("entity", {}).get("entity_version") == 1
                and decision_payload.get("status") != "proposed"
            ):
                errors.append("decision_initial_status_not_proposed")
            authority_ref = envelope.get("authorized_by", {})
            if authority_ref.get("entity_type") != "role_assignment":
                errors.append("decision_requires_role_assignment_authority")
            elif not _same_entity_ref(
                authority_ref, decision_payload.get("last_transition_by_role_assignment")
            ):
                errors.append("decision_authority_ref_mismatch")


        if versioned_payload.get("legal_payload_schema") == EXECUTION_RESOURCE_PLAN_PAYLOAD_SCHEMA:
            resource_payload = versioned_payload.get("legal_payload", {})
            errors.extend(validate_execution_resource_plan_semantics(resource_payload))
            if (
                versioned_payload.get("entity", {}).get("entity_version") == 1
                and resource_payload.get("status") != "active"
            ):
                errors.append("execution_resource_plan_initial_status_not_active")

        if versioned_payload.get("legal_payload_schema") == FINAL_RESULT_PAYLOAD_SCHEMA:
            final_payload = versioned_payload.get("legal_payload", {})
            errors.extend(validate_final_result_semantics(final_payload))
            if (
                versioned_payload.get("entity", {}).get("entity_version") == 1
                and final_payload.get("lifecycle_status") != "published"
            ):
                errors.append("final_result_initial_lifecycle_not_published")
            authority_ref = envelope.get("authorized_by", {})
            if authority_ref.get("entity_type") != "role_assignment":
                errors.append("final_result_requires_role_assignment_authority")
            elif not _same_entity_ref(
                authority_ref, final_payload.get("generated_by_role_assignment")
            ):
                errors.append("final_result_authority_ref_mismatch")

        if versioned_payload.get("legal_payload_schema") == PROTOCOL_EVENT_PAYLOAD_SCHEMA:
            protocol_event_payload = versioned_payload.get("legal_payload", {})
            errors.extend(validate_protocol_event_semantics(protocol_event_payload))
            event_ref = versioned_payload.get("entity", {})
            if event_ref.get("entity_version") != 1:
                errors.append("protocol_event_version_must_be_one")
            if event_ref.get("entity_id") != envelope.get("message_id"):
                errors.append("protocol_event_identity_must_match_message_id")
            expected_type = f"protocol_event.{protocol_event_payload.get('event_type')}"
            if envelope.get("type") != expected_type:
                errors.append("protocol_event_envelope_type_mismatch")
            if envelope.get("authorized_by", {}).get("entity_type") != "role_assignment":
                errors.append("protocol_event_requires_role_assignment_authority")
            if not envelope.get("causation_id"):
                errors.append("protocol_event_causation_required")

        versioned_ref = versioned_payload.get("entity", {})
        if (
            versioned_ref.get("entity_type") == "role_assignment"
            and versioned_ref.get("entity_version") == 1
        ):
            valid_from = versioned_payload.get("legal_payload", {}).get("valid_from")
            if (
                isinstance(valid_from, str)
                and isinstance(recorded_at, str)
                and _parse_datetime(valid_from) < _parse_datetime(recorded_at)
            ):
                errors.append("role_assignment_backdated")

    return errors


@dataclass(frozen=True)
class SessionValidationState:
    """Minimal session facts needed by the current envelope slice.

    This is intentionally not a full event store. It enforces the bootstrap and
    recorder-trust boundary while repository work enforces message and sequence uniqueness, entity
    existence, RoleAssignment authority is validated by a separate state view.
    """

    event_count: int = 0
    bootstrap_completed: bool = False
    used_bootstrap_ids: frozenset[str] = field(default_factory=frozenset)
    trusted_recorders: frozenset[PrincipalKey] = field(default_factory=frozenset)


def validate_protocol_envelope_session_semantics(
    envelope: dict[str, Any], state: SessionValidationState
) -> list[str]:
    """Return stable error codes for stateful bootstrap/recorder violations.

    Call this after structural and local semantic validation. Authority validity
    and matching ``authorized_by`` to the effective principal are handled by
    ``validate_envelope_role_assignment_authority``. Approval-based authority is validated by ``validate_envelope_approval_authority``.
    """

    errors: list[str] = []
    is_bootstrap = envelope.get("type") == "session.bootstrap.completed"

    if envelope.get("kind") == "event":
        recorder_key = _principal_key(envelope.get("recorded_by"))
        if recorder_key not in state.trusted_recorders:
            errors.append(
                "bootstrap_recorder_untrusted"
                if is_bootstrap
                else "event_recorder_untrusted"
            )

    if is_bootstrap:
        if state.bootstrap_completed:
            errors.append("bootstrap_already_completed")
        elif state.event_count != 0:
            errors.append("bootstrap_not_first_event")

        bootstrap = envelope.get("bootstrap", {})
        bootstrap_id = bootstrap.get("bootstrap_id")
        if bootstrap_id in state.used_bootstrap_ids:
            errors.append("bootstrap_id_reused")

        return errors

    if not state.bootstrap_completed:
        errors.append("session_not_bootstrapped")

    return errors


def _same_entity_ref(left: dict[str, Any] | None, right: dict[str, Any] | None) -> bool:
    if left is None or right is None:
        return False
    return (
        _same_identity(left, right)
        and left.get("entity_version") == right.get("entity_version")
    )


def validate_versioned_entity_semantics(versioned_entity: dict[str, Any]) -> list[str]:
    """Return stable local error codes for VersionedEntity lineage rules.

    Call this after structural validation. Checks involving the stored prior
    version or referenced approval/check records belong to transition or
    repository validation.
    """

    errors: list[str] = []
    entity = versioned_entity.get("entity", {})
    version = entity.get("entity_version")

    if isinstance(version, int) and version > 1:
        previous = versioned_entity.get("previous_version", {})
        if not _same_identity(entity, previous):
            errors.append("previous_identity_mismatch")
        previous_number = previous.get("entity_version")
        if isinstance(previous_number, int) and previous_number != version - 1:
            errors.append("previous_version_not_immediate")

    return errors


def validate_versioned_entity_transition(
    current: dict[str, Any], previous: dict[str, Any] | None
) -> list[str]:
    """Validate a new accepted version against the stored previous record.

    This function does not validate artifact contents or authority scopes. The
    event store must resolve the referenced diff, approval, and check entities.
    """

    errors = validate_versioned_entity_semantics(current)
    current_ref = current.get("entity", {})
    current_version = current_ref.get("entity_version")

    if current_version == 1:
        if previous is not None:
            errors.append("initial_version_has_previous_record")
        return errors

    if previous is None:
        errors.append("previous_record_required")
        return errors

    previous_ref = previous.get("entity", {})
    declared_previous = current.get("previous_version", {})

    if not _same_entity_ref(declared_previous, previous_ref):
        errors.append("previous_record_ref_mismatch")
    if not _same_identity(current_ref, previous_ref):
        errors.append("previous_record_identity_mismatch")
    if (
        isinstance(current_version, int)
        and isinstance(previous_ref.get("entity_version"), int)
        and current_version != previous_ref["entity_version"] + 1
    ):
        errors.append("current_version_not_next")

    same_schema = current.get("legal_payload_schema") == previous.get(
        "legal_payload_schema"
    )
    same_payload = current.get("legal_payload") == previous.get("legal_payload")
    if same_schema and same_payload:
        errors.append("legal_payload_unchanged")

    metadata = current.get("change_metadata", {})
    if metadata.get("substantive_effect") == "none" and not same_schema:
        errors.append("editorial_none_schema_changed")

    return errors


ROLE_ASSIGNMENT_PAYLOAD_SCHEMA = "urn:w1-cip:entity-payload:0.1:role-assignment"
ROLE_MANAGEMENT_OPERATIONS = frozenset(
    {"role_assignment.create", "role_assignment.next_version"}
)
EDITORIAL_CLASSIFICATION_ROLES = frozenset(
    {"reviewer", "verifier", "human_owner"}
)
AGENT_CARD_PAYLOAD_SCHEMA = "urn:w1-cip:entity-payload:0.1:agent-card"
GOAL_CONTRACT_PAYLOAD_SCHEMA = "urn:w1-cip:entity-payload:0.1:goal-contract"
GOAL_CONTRACT_MANAGEMENT_OPERATIONS = frozenset(
    {"goal_contract.create", "goal_contract.next_version"}
)
TEAM_PLAN_PAYLOAD_SCHEMA = "urn:w1-cip:entity-payload:0.1:team-plan"
TEAM_PLAN_MANAGEMENT_OPERATIONS = frozenset(
    {"team_plan.create", "team_plan.next_version"}
)
TEAM_PLAN_MANAGEMENT_ROLES = frozenset({"orchestrator", "human_owner"})
CONTEXT_GRANT_PAYLOAD_SCHEMA = "urn:w1-cip:entity-payload:0.1:context-grant"
CONTEXT_GRANT_MANAGEMENT_OPERATIONS = frozenset(
    {"context_grant.create", "context_grant.next_version"}
)
CONTEXT_GRANT_MANAGEMENT_ROLES = frozenset({"human_owner"})
CONTEXT_GRANT_TERMINAL_STATUSES = frozenset({"revoked"})
TASK_PAYLOAD_SCHEMA = "urn:w1-cip:entity-payload:0.1:task"
TASK_CREATE_OPERATION = "task.create"
TASK_UPDATE_OPERATION = "task.next_version"
TASK_CREATE_ROLES = frozenset({"orchestrator", "human_owner"})
TASK_PHASE_ROLES = {
    "planning": frozenset({"orchestrator", "human_owner"}),
    "execution": frozenset({"executor"}),
    "review": frozenset({"reviewer"}),
    "verification": frozenset({"verifier"}),
    "revision": frozenset({"executor"}),
    "approval": frozenset({"decision_authority", "human_owner"}),
    "synthesis": frozenset({"synthesizer", "human_owner"}),
}
TASK_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
CONTRIBUTION_PAYLOAD_SCHEMA = "urn:w1-cip:entity-payload:0.1:contribution"
CONTRIBUTION_TERMINAL_STATUSES = frozenset({"withdrawn"})
EVIDENCE_PAYLOAD_SCHEMA = "urn:w1-cip:entity-payload:0.1:evidence"

VERIFICATION_PAYLOAD_SCHEMA = "urn:w1-cip:entity-payload:0.1:verification"
CHALLENGE_PAYLOAD_SCHEMA = "urn:w1-cip:entity-payload:0.1:challenge"
REVIEW_PAYLOAD_SCHEMA = "urn:w1-cip:entity-payload:0.1:review"
APPROVAL_PAYLOAD_SCHEMA = "urn:w1-cip:entity-payload:0.1:approval"
DECISION_PAYLOAD_SCHEMA = "urn:w1-cip:entity-payload:0.1:decision"
EXECUTION_RESOURCE_PLAN_PAYLOAD_SCHEMA = "urn:w1-cip:entity-payload:0.1:execution-resource-plan"
EXECUTION_RESOURCE_PLAN_MANAGEMENT_OPERATIONS = frozenset({"execution_resource_plan.create", "execution_resource_plan.next_version"})
EXECUTION_RESOURCE_PLAN_MANAGEMENT_ROLES = frozenset({"orchestrator", "human_owner"})
EXECUTION_RESOURCE_PLAN_TERMINAL_STATUSES = frozenset({"completed", "cancelled"})
FINAL_RESULT_PAYLOAD_SCHEMA = "urn:w1-cip:entity-payload:0.1:final-result"
FINAL_RESULT_MANAGEMENT_OPERATIONS = frozenset({"final_result.create", "final_result.next_version"})
FINAL_RESULT_MANAGEMENT_ROLES = frozenset({"synthesizer", "human_owner"})
FINAL_RESULT_TERMINAL_LIFECYCLE = frozenset({"superseded", "withdrawn"})
PROTOCOL_EVENT_PAYLOAD_SCHEMA = "urn:w1-cip:entity-payload:0.1:protocol-event"
PROTOCOL_EVENT_CREATE_OPERATION = "protocol_event.create"
PROTOCOL_EVENT_MANAGEMENT_ROLES = frozenset({"orchestrator", "human_owner"})
APPROVAL_MANAGEMENT_OPERATIONS = frozenset({"approval.create", "approval.next_version"})
APPROVAL_ISSUER_ROLES = frozenset({"reviewer", "verifier", "decision_authority", "human_owner"})
APPROVAL_GOVERNANCE_DELEGATION_FORBIDDEN = frozenset({
    "role_assignment.create", "role_assignment.next_version",
    "goal_contract.create", "goal_contract.next_version",
    "team_plan.create", "team_plan.next_version",
    "context_grant.create", "context_grant.next_version",
    "execution_resource_plan.create", "execution_resource_plan.next_version",
    "final_result.create", "final_result.next_version",
    "approval.create", "approval.next_version",
})
EVIDENCE_TERMINAL_STATUSES = frozenset({"withdrawn"})
EVIDENCE_TASK_PHASES = frozenset({"planning", "execution", "review", "verification", "revision"})
CONTRIBUTION_TYPES_BY_TASK_PHASE = {
    "planning": frozenset({"proposal", "assumption", "question", "recommendation"}),
    "execution": frozenset({
        "proposal", "claim", "assumption", "measured_result",
        "tool_result", "question", "recommendation"
    }),
    "review": frozenset({"claim", "assumption", "question", "recommendation"}),
    "verification": frozenset({
        "claim", "measured_result", "tool_result", "question", "recommendation"
    }),
    "revision": frozenset({
        "proposal", "claim", "assumption", "measured_result",
        "tool_result", "question", "recommendation"
    }),
    "approval": frozenset({"question", "recommendation"}),
    "synthesis": frozenset({"proposal", "question", "recommendation"}),
}

CONTRIBUTION_CLASSIFICATION_RANK = {
    "public": 0,
    "internal": 1,
    "confidential": 2,
    "restricted": 3,
}


def _agent_card_basis_refs(role_assignment: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        ref
        for ref in role_assignment.get("basis", [])
        if isinstance(ref, dict) and ref.get("entity_type") == "agent_card"
    ]


def validate_role_assignment_semantics(role_assignment: dict[str, Any]) -> list[str]:
    """Return stable cross-field errors for a RoleAssignment legal payload."""

    errors: list[str] = []
    role = role_assignment.get("role")
    scope = role_assignment.get("authority_scope", {})
    operations = set(scope.get("operations", []))
    capabilities = set(scope.get("capabilities", []))

    valid_from = role_assignment.get("valid_from")
    valid_until = role_assignment.get("valid_until")
    if isinstance(valid_from, str) and isinstance(valid_until, str):
        if _parse_datetime(valid_until) <= _parse_datetime(valid_from):
            errors.append("role_assignment_invalid_validity_interval")

    if operations & ROLE_MANAGEMENT_OPERATIONS and role != "human_owner":
        errors.append("role_assignment_management_requires_human_owner")

    if operations & GOAL_CONTRACT_MANAGEMENT_OPERATIONS and role != "human_owner":
        errors.append("goal_contract_management_requires_human_owner")

    if operations & TEAM_PLAN_MANAGEMENT_OPERATIONS and role not in TEAM_PLAN_MANAGEMENT_ROLES:
        errors.append("team_plan_management_requires_orchestrator_or_owner")

    if operations & CONTEXT_GRANT_MANAGEMENT_OPERATIONS and role not in CONTEXT_GRANT_MANAGEMENT_ROLES:
        errors.append("context_grant_management_requires_human_owner")

    if operations & EXECUTION_RESOURCE_PLAN_MANAGEMENT_OPERATIONS and role not in EXECUTION_RESOURCE_PLAN_MANAGEMENT_ROLES:
        errors.append("execution_resource_plan_management_requires_orchestrator_or_owner")

    if operations & FINAL_RESULT_MANAGEMENT_OPERATIONS and role not in FINAL_RESULT_MANAGEMENT_ROLES:
        errors.append("final_result_management_requires_synthesizer_or_owner")

    if TASK_CREATE_OPERATION in operations and role not in TASK_CREATE_ROLES:
        errors.append("task_creation_requires_orchestrator_or_owner")

    if operations & APPROVAL_MANAGEMENT_OPERATIONS and role not in APPROVAL_ISSUER_ROLES:
        errors.append("approval_management_role_not_allowed")

    if PROTOCOL_EVENT_CREATE_OPERATION in operations and role not in PROTOCOL_EVENT_MANAGEMENT_ROLES:
        errors.append("protocol_event_creation_requires_orchestrator_or_owner")

    if "protocol_event.next_version" in operations:
        errors.append("protocol_event_next_version_forbidden")

    if (
        "classify_editorial_correction" in capabilities
        and role not in EDITORIAL_CLASSIFICATION_ROLES
    ):
        errors.append("editorial_classification_role_not_allowed")

    if "accept_risk" in capabilities and role != "human_owner":
        errors.append("risk_acceptance_requires_human_owner")

    subject = role_assignment.get("subject", {})
    card_refs = _agent_card_basis_refs(role_assignment)
    if subject.get("principal_type") == "agent":
        if not card_refs:
            errors.append("agent_card_basis_required")
        elif len(card_refs) > 1:
            errors.append("agent_card_basis_ambiguous")
        elif card_refs[0].get("entity_id") != subject.get("principal_id"):
            errors.append("agent_card_subject_mismatch")
    elif card_refs:
        errors.append("agent_card_basis_for_non_agent")

    return errors


def validate_role_assignment_transition(
    current: dict[str, Any], previous: dict[str, Any] | None
) -> list[str]:
    """Validate RoleAssignment invariants across VersionedEntity versions."""

    errors = validate_versioned_entity_transition(current, previous)
    if current.get("legal_payload_schema") != ROLE_ASSIGNMENT_PAYLOAD_SCHEMA:
        return errors

    current_payload = current.get("legal_payload", {})
    errors.extend(validate_role_assignment_semantics(current_payload))
    if (
        previous is None
        or previous.get("legal_payload_schema") != ROLE_ASSIGNMENT_PAYLOAD_SCHEMA
    ):
        return errors

    previous_payload = previous.get("legal_payload", {})
    for field_name, error_code in (
        ("subject", "role_assignment_subject_changed"),
        ("role", "role_assignment_role_changed"),
        ("valid_from", "role_assignment_valid_from_changed"),
    ):
        if current_payload.get(field_name) != previous_payload.get(field_name):
            errors.append(error_code)

    if (
        previous_payload.get("status") == "revoked"
        and current_payload.get("status") != "revoked"
    ):
        errors.append("role_assignment_revocation_final")

    return errors


EntityKey = tuple[str, str]
EntityVersionKey = tuple[str, str, int]


@dataclass(frozen=True)
class VersionedEntityRepositoryState:
    """Minimal repository facts for immutable version acceptance checks."""

    latest_versions: dict[EntityKey, int] = field(default_factory=dict)
    accepted_versions: frozenset[EntityVersionKey] = field(default_factory=frozenset)


def validate_versioned_entity_repository_semantics(
    current: dict[str, Any], state: VersionedEntityRepositoryState
) -> list[str]:
    """Validate identity creation, optimistic concurrency, and immutability."""

    errors: list[str] = []
    entity = current.get("entity", {})
    entity_type = entity.get("entity_type")
    entity_id = entity.get("entity_id")
    version = entity.get("entity_version")
    if not (
        isinstance(entity_type, str)
        and isinstance(entity_id, str)
        and isinstance(version, int)
    ):
        return errors

    identity: EntityKey = (entity_type, entity_id)
    version_key: EntityVersionKey = (entity_type, entity_id, version)
    latest = state.latest_versions.get(identity)

    if version_key in state.accepted_versions:
        return ["immutable_version"]

    if version == 1:
        if latest is not None:
            errors.append("entity_already_exists")
        return errors

    if latest is None:
        errors.append("entity_not_found")
        return errors

    declared_previous = current.get("previous_version", {}).get("entity_version")
    if declared_previous != latest:
        errors.append("version_conflict")

    return errors


@dataclass(frozen=True)
class RoleAssignmentAuthorityState:
    """Accepted RoleAssignment records needed to validate ``authorized_by``."""

    assignments: dict[EntityVersionKey, dict[str, Any]] = field(default_factory=dict)
    latest_versions: dict[EntityKey, int] = field(default_factory=dict)


def validate_envelope_role_assignment_authority(
    envelope: dict[str, Any],
    state: RoleAssignmentAuthorityState,
    *,
    acceptance_time: str | None = None,
    agent_card_state: AgentCardState | None = None,
) -> list[str]:
    """Validate a RoleAssignment-backed authority reference.

    Events are evaluated at ``recorded_at``. Commands require the caller to
    supply the proposed acceptance time so validation remains deterministic.
    Approval-backed authority is validated separately against the Approval repository.
    """

    if envelope.get("type") == "session.bootstrap.completed":
        return []

    authority_ref = envelope.get("authorized_by", {})
    if authority_ref.get("entity_type") != "role_assignment":
        return []

    entity_id = authority_ref.get("entity_id")
    entity_version = authority_ref.get("entity_version")
    if not isinstance(entity_id, str) or not isinstance(entity_version, int):
        return []

    key: EntityVersionKey = ("role_assignment", entity_id, entity_version)
    record = state.assignments.get(key)
    if record is None:
        return ["authority_role_assignment_not_found"]

    errors: list[str] = []
    latest = state.latest_versions.get(("role_assignment", entity_id))
    if latest != entity_version:
        errors.append("authority_version_not_current")

    if record.get("legal_payload_schema") != ROLE_ASSIGNMENT_PAYLOAD_SCHEMA:
        errors.append("authority_payload_schema_mismatch")
        return errors

    payload = record.get("legal_payload", {})
    effective_principal = get_effective_principal(envelope)
    if _principal_key(payload.get("subject")) != effective_principal:
        errors.append("authority_subject_mismatch")

    if payload.get("status") != "active":
        errors.append("authority_inactive")

    evaluation_value = (
        envelope.get("recorded_at")
        if envelope.get("kind") == "event"
        else acceptance_time
    )
    if not isinstance(evaluation_value, str):
        errors.append("authority_evaluation_time_required")
    else:
        evaluation_time = _parse_datetime(evaluation_value)
        valid_from = payload.get("valid_from")
        valid_until = payload.get("valid_until")
        if isinstance(valid_from, str) and evaluation_time < _parse_datetime(valid_from):
            errors.append("authority_not_yet_valid")
        if isinstance(valid_until, str) and evaluation_time >= _parse_datetime(valid_until):
            errors.append("authority_expired")

    required_operation = get_required_authority_operation(envelope)
    allowed_operations = set(payload.get("authority_scope", {}).get("operations", []))
    if required_operation is not None and required_operation not in allowed_operations:
        errors.append("authority_scope_denied")

    if (
        required_operation in ROLE_MANAGEMENT_OPERATIONS
        and payload.get("role") != "human_owner"
    ):
        errors.append("role_assignment_management_requires_human_owner")

    if (
        required_operation in GOAL_CONTRACT_MANAGEMENT_OPERATIONS
        and payload.get("role") != "human_owner"
    ):
        errors.append("goal_contract_management_requires_human_owner")

    if (
        required_operation in TEAM_PLAN_MANAGEMENT_OPERATIONS
        and payload.get("role") not in TEAM_PLAN_MANAGEMENT_ROLES
    ):
        errors.append("team_plan_management_requires_orchestrator_or_owner")

    if (
        required_operation in CONTEXT_GRANT_MANAGEMENT_OPERATIONS
        and payload.get("role") not in CONTEXT_GRANT_MANAGEMENT_ROLES
    ):
        errors.append("context_grant_management_requires_human_owner")

    if (
        required_operation in EXECUTION_RESOURCE_PLAN_MANAGEMENT_OPERATIONS
        and payload.get("role") not in EXECUTION_RESOURCE_PLAN_MANAGEMENT_ROLES
    ):
        errors.append("execution_resource_plan_management_requires_orchestrator_or_owner")

    if (
        required_operation in FINAL_RESULT_MANAGEMENT_OPERATIONS
        and payload.get("role") not in FINAL_RESULT_MANAGEMENT_ROLES
    ):
        errors.append("final_result_management_requires_synthesizer_or_owner")

    if (
        required_operation == TASK_CREATE_OPERATION
        and payload.get("role") not in TASK_CREATE_ROLES
    ):
        errors.append("task_creation_requires_orchestrator_or_owner")


    if (
        required_operation in APPROVAL_MANAGEMENT_OPERATIONS
        and payload.get("role") not in APPROVAL_ISSUER_ROLES
    ):
        errors.append("approval_management_role_not_allowed")

    if (
        required_operation == PROTOCOL_EVENT_CREATE_OPERATION
        and payload.get("role") not in PROTOCOL_EVENT_MANAGEMENT_ROLES
    ):
        errors.append("protocol_event_creation_requires_orchestrator_or_owner")

    if agent_card_state is not None:
        errors.extend(validate_role_assignment_agent_eligibility(record, agent_card_state))

    return errors


def validate_role_assignment_capability(
    authority_ref: dict[str, Any],
    principal: dict[str, Any],
    capability: str,
    evaluated_at: str,
    state: RoleAssignmentAuthorityState,
    *,
    agent_card_state: AgentCardState | None = None,
) -> list[str]:
    """Validate a non-operation capability on a pinned RoleAssignment."""

    synthetic_envelope = {
        "kind": "event",
        "type": "authority.capability_check",
        "actor": principal,
        "authorized_by": authority_ref,
        "recorded_at": evaluated_at,
    }
    errors = validate_envelope_role_assignment_authority(
        synthetic_envelope, state, agent_card_state=agent_card_state
    )
    errors = [error for error in errors if error != "authority_scope_denied"]

    entity_id = authority_ref.get("entity_id")
    entity_version = authority_ref.get("entity_version")
    record = state.assignments.get(
        ("role_assignment", entity_id, entity_version)
    )
    if record is None:
        return errors
    capabilities = set(
        record.get("legal_payload", {})
        .get("authority_scope", {})
        .get("capabilities", [])
    )
    if capability not in capabilities:
        errors.append("authority_capability_denied")
    return errors

@dataclass(frozen=True)
class AgentCardState:
    """Accepted AgentCard records used when assigning roles to agents."""

    cards: dict[EntityVersionKey, dict[str, Any]] = field(default_factory=dict)
    latest_versions: dict[EntityKey, int] = field(default_factory=dict)


def validate_agent_card_transition(
    current: dict[str, Any], previous: dict[str, Any] | None
) -> list[str]:
    """Validate AgentCard lifecycle rules across immutable versions."""

    errors = validate_versioned_entity_transition(current, previous)
    if current.get("legal_payload_schema") != AGENT_CARD_PAYLOAD_SCHEMA:
        return errors
    if (
        previous is not None
        and previous.get("legal_payload_schema") == AGENT_CARD_PAYLOAD_SCHEMA
        and previous.get("legal_payload", {}).get("status") == "retired"
    ):
        errors.append("agent_card_retired_final")
    return errors


def validate_role_assignment_agent_eligibility(
    role_assignment_record: dict[str, Any], state: AgentCardState
) -> list[str]:
    """Validate the pinned AgentCard supporting an agent RoleAssignment.

    The role assignment must pin exactly one current AgentCard whose entity id
    matches the agent principal id. The card must be active and explicitly list
    the assigned role as eligible. Non-agent assignments do not require a card.
    """

    if role_assignment_record.get("legal_payload_schema") != ROLE_ASSIGNMENT_PAYLOAD_SCHEMA:
        return []

    payload = role_assignment_record.get("legal_payload", {})
    subject = payload.get("subject", {})
    local_errors = [
        error
        for error in validate_role_assignment_semantics(payload)
        if error.startswith("agent_card_")
    ]
    if local_errors or subject.get("principal_type") != "agent":
        return local_errors

    card_ref = _agent_card_basis_refs(payload)[0]
    entity_id = card_ref.get("entity_id")
    entity_version = card_ref.get("entity_version")
    if not isinstance(entity_id, str) or not isinstance(entity_version, int):
        return local_errors

    key: EntityVersionKey = ("agent_card", entity_id, entity_version)
    card_record = state.cards.get(key)
    if card_record is None:
        return ["agent_card_not_found"]

    errors: list[str] = []
    latest = state.latest_versions.get(("agent_card", entity_id))
    effective_record = card_record
    if isinstance(latest, int) and latest > entity_version:
        for successor_version in range(entity_version + 1, latest + 1):
            successor = state.cards.get(("agent_card", entity_id, successor_version))
            if successor is None:
                errors.append("agent_card_lineage_incomplete")
                return errors
            effective_record = successor
            metadata = successor.get("change_metadata", {})
            if metadata.get("substantive_effect") != "none":
                errors.append("agent_card_version_not_current")
                break
    elif latest != entity_version:
        errors.append("agent_card_version_not_current")

    if effective_record.get("legal_payload_schema") != AGENT_CARD_PAYLOAD_SCHEMA:
        errors.append("agent_card_payload_schema_mismatch")
        return errors

    card_payload = effective_record.get("legal_payload", {})
    if card_payload.get("status") != "active":
        errors.append("agent_card_inactive")
    if payload.get("role") not in set(card_payload.get("eligible_roles", [])):
        errors.append("agent_role_not_eligible")
    return errors



GOAL_CONTRACT_TERMINAL_STATUSES = frozenset({"fulfilled", "cancelled"})


def _duplicate_value_errors(
    items: list[dict[str, Any]], field_name: str, error_code: str
) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for item in items:
        value = item.get(field_name)
        if not isinstance(value, str):
            continue
        if value in seen:
            duplicates.add(value)
        else:
            seen.add(value)
    return [error_code] if duplicates else []


def validate_goal_contract_semantics(goal_contract: dict[str, Any]) -> list[str]:
    """Return stable local cross-field errors for a GoalContract payload.

    The schema validates shapes and enumerations. This function enforces
    identifier uniqueness, deliverable coverage, cross-references, and the
    conservative high-risk autonomy rule.
    """

    errors: list[str] = []
    deliverables = goal_contract.get("deliverables", [])
    constraints = goal_contract.get("constraints", [])
    criteria = goal_contract.get("success_criteria", [])
    prohibitions = goal_contract.get("prohibitions", [])
    decisions = goal_contract.get("decision_policy", [])

    errors.extend(
        _duplicate_value_errors(deliverables, "deliverable_id", "duplicate_deliverable_id")
    )
    errors.extend(
        _duplicate_value_errors(constraints, "constraint_id", "duplicate_constraint_id")
    )
    errors.extend(
        _duplicate_value_errors(criteria, "criterion_id", "duplicate_success_criterion_id")
    )
    errors.extend(
        _duplicate_value_errors(prohibitions, "prohibition_id", "duplicate_prohibition_id")
    )
    errors.extend(
        _duplicate_value_errors(decisions, "decision_subject", "duplicate_decision_subject")
    )

    deliverable_ids = {
        item.get("deliverable_id")
        for item in deliverables
        if isinstance(item.get("deliverable_id"), str)
    }
    required_criteria = [
        criterion
        for criterion in criteria
        if criterion.get("acceptance_effect") == "required_for_completion"
    ]
    if not required_criteria:
        errors.append("required_success_criterion_missing")

    constraint_ids = {
        item.get("constraint_id")
        for item in constraints
        if isinstance(item.get("constraint_id"), str)
    }
    for criterion in criteria:
        targets = criterion.get("applies_to", [])
        if any(target not in deliverable_ids for target in targets):
            errors.append("success_criterion_unknown_deliverable")
        verified_constraints = criterion.get("verifies_constraints", [])
        if any(target not in constraint_ids for target in verified_constraints):
            errors.append("success_criterion_unknown_constraint")

    covered_by_required = {
        target
        for criterion in required_criteria
        for target in criterion.get("applies_to", [])
        if target in deliverable_ids
    }
    for deliverable in deliverables:
        if (
            deliverable.get("required") is True
            and deliverable.get("deliverable_id") not in covered_by_required
        ):
            errors.append("required_deliverable_without_required_criterion")

    constraints_covered_by_required = {
        target
        for criterion in required_criteria
        for target in criterion.get("verifies_constraints", [])
        if target in constraint_ids
    }
    for constraint in constraints:
        if (
            constraint.get("criticality") == "critical"
            and constraint.get("constraint_id") not in constraints_covered_by_required
        ):
            errors.append("critical_constraint_without_required_criterion")

    decision_subjects = {
        item.get("decision_subject")
        for item in decisions
        if isinstance(item.get("decision_subject"), str)
    }
    for deliverable in deliverables:
        if (
            deliverable.get("kind") == "decision"
            and deliverable.get("decision_subject") not in decision_subjects
        ):
            errors.append("decision_deliverable_unknown_subject")

    risk_policy = goal_contract.get("risk_policy", {})
    if risk_policy.get("risk_level") == "high":
        if risk_policy.get("autonomy_mode") != "advisory_only":
            errors.append("high_risk_requires_advisory_only")
        if any(
            decision.get("human_approval_required") is not True
            for decision in decisions
        ):
            errors.append("high_risk_requires_human_approval")

    return errors


def validate_goal_contract_transition(
    current: dict[str, Any], previous: dict[str, Any] | None
) -> list[str]:
    """Validate GoalContract lifecycle rules across immutable versions."""

    errors = validate_versioned_entity_transition(current, previous)
    if current.get("legal_payload_schema") != GOAL_CONTRACT_PAYLOAD_SCHEMA:
        return errors

    current_payload = current.get("legal_payload", {})
    errors.extend(validate_goal_contract_semantics(current_payload))
    current_version = current.get("entity", {}).get("entity_version")
    if current_version == 1 and current_payload.get("status") != "active":
        errors.append("goal_contract_initial_status_not_active")

    if (
        previous is not None
        and previous.get("legal_payload_schema") == GOAL_CONTRACT_PAYLOAD_SCHEMA
        and previous.get("legal_payload", {}).get("status")
        in GOAL_CONTRACT_TERMINAL_STATUSES
    ):
        errors.append("goal_contract_terminal_status_final")
    return errors


def validate_goal_contract_decision_authorities(
    goal_contract: dict[str, Any],
    state: RoleAssignmentAuthorityState,
    *,
    evaluated_at: str,
    agent_card_state: AgentCardState | None = None,
) -> list[str]:
    """Validate pinned decision authorities declared by a GoalContract.

    TeamPlan-resolved entries are intentionally deferred. A pinned assignment
    must be current, active, valid at the evaluation time, carry the
    ``decision_authority`` role, and include the matching decision subject.
    """

    errors: list[str] = []
    evaluation_time = _parse_datetime(evaluated_at)
    for decision_class in goal_contract.get("decision_policy", []):
        authority = decision_class.get("authority", {})
        if authority.get("mode") != "pinned_assignment":
            continue
        ref = authority.get("role_assignment", {})
        entity_id = ref.get("entity_id")
        version = ref.get("entity_version")
        subject = decision_class.get("decision_subject")
        if not isinstance(entity_id, str) or not isinstance(version, int):
            continue

        record = state.assignments.get(("role_assignment", entity_id, version))
        if record is None:
            errors.append("goal_decision_authority_not_found")
            continue
        latest = state.latest_versions.get(("role_assignment", entity_id))
        if latest != version:
            errors.append("goal_decision_authority_version_not_current")
        if record.get("legal_payload_schema") != ROLE_ASSIGNMENT_PAYLOAD_SCHEMA:
            errors.append("goal_decision_authority_payload_schema_mismatch")
            continue

        payload = record.get("legal_payload", {})
        if payload.get("role") != "decision_authority":
            errors.append("goal_decision_authority_role_mismatch")
        if payload.get("status") != "active":
            errors.append("goal_decision_authority_inactive")
        valid_from = payload.get("valid_from")
        valid_until = payload.get("valid_until")
        if isinstance(valid_from, str) and evaluation_time < _parse_datetime(valid_from):
            errors.append("goal_decision_authority_not_yet_valid")
        if isinstance(valid_until, str) and evaluation_time >= _parse_datetime(valid_until):
            errors.append("goal_decision_authority_expired")
        decision_subjects = set(
            payload.get("authority_scope", {}).get("decision_subjects", [])
        )
        if subject not in decision_subjects:
            errors.append("goal_decision_subject_not_authorized")
        if agent_card_state is not None:
            for error in validate_role_assignment_agent_eligibility(
                record, agent_card_state
            ):
                errors.append(f"goal_decision_{error}")
    return errors


TEAM_PLAN_TERMINAL_STATUSES = frozenset({"completed", "cancelled"})


@dataclass(frozen=True)
class GoalContractState:
    """Accepted GoalContract records used to validate a TeamPlan basis."""

    contracts: dict[EntityVersionKey, dict[str, Any]] = field(default_factory=dict)
    latest_versions: dict[EntityKey, int] = field(default_factory=dict)


def _entity_ref_key(ref: dict[str, Any]) -> EntityVersionKey | None:
    entity_type = ref.get("entity_type")
    entity_id = ref.get("entity_id")
    entity_version = ref.get("entity_version")
    if not (
        isinstance(entity_type, str)
        and isinstance(entity_id, str)
        and isinstance(entity_version, int)
    ):
        return None
    return entity_type, entity_id, entity_version


def validate_team_plan_semantics(team_plan: dict[str, Any]) -> list[str]:
    """Return stable local cross-field errors for a TeamPlan payload."""

    errors: list[str] = []
    requirements = team_plan.get("role_requirements", [])
    assignments = team_plan.get("assignments", [])
    independence_rules = team_plan.get("independence_rules", [])
    authorities = team_plan.get("decision_authorities", [])

    errors.extend(
        _duplicate_value_errors(requirements, "role", "duplicate_role_requirement")
    )
    errors.extend(
        _duplicate_value_errors(assignments, "slot_id", "duplicate_team_slot_id")
    )
    errors.extend(
        _duplicate_value_errors(independence_rules, "rule_id", "duplicate_independence_rule_id")
    )
    errors.extend(
        _duplicate_value_errors(authorities, "decision_subject", "duplicate_team_decision_subject")
    )

    assignment_refs: list[tuple[str, str, int]] = []
    for assignment in assignments:
        key = _entity_ref_key(assignment.get("role_assignment", {}))
        if key is not None:
            assignment_refs.append(key)
    if len(assignment_refs) != len(set(assignment_refs)):
        errors.append("duplicate_team_role_assignment")

    requirement_counts = {
        item.get("role"): item.get("minimum_count")
        for item in requirements
        if isinstance(item.get("role"), str)
        and isinstance(item.get("minimum_count"), int)
    }
    actual_counts: dict[str, int] = {}
    for assignment in assignments:
        role = assignment.get("role")
        if isinstance(role, str):
            actual_counts[role] = actual_counts.get(role, 0) + 1

    for role, minimum in requirement_counts.items():
        if actual_counts.get(role, 0) < minimum:
            errors.append("required_role_count_unmet")
            break

    if requirement_counts.get("executor", 0) < 1:
        errors.append("team_plan_executor_required")

    assessment = team_plan.get("planning_assessment", {})
    risk_level = assessment.get("risk_level")
    error_cost = assessment.get("error_cost")
    verifiability = assessment.get("verifiability")
    if risk_level in {"medium", "high"} and requirement_counts.get("reviewer", 0) < 1:
        errors.append("team_plan_reviewer_required")
    if (
        error_cost in {"material", "severe"}
        and verifiability in {"deterministic", "empirical", "source_based", "mixed"}
        and requirement_counts.get("verifier", 0) < 1
    ):
        errors.append("team_plan_verifier_required")

    if requirement_counts.get("reviewer", 0) > 0 and team_plan.get("budget", {}).get(
        "max_review_cycles"
    ) == 0:
        errors.append("team_plan_review_budget_missing")

    reviewer_executor_separation = any(
        rule.get("type") == "role_separation"
        and {rule.get("role_a"), rule.get("role_b")} == {"reviewer", "executor"}
        for rule in independence_rules
    )
    if requirement_counts.get("reviewer", 0) > 0 and not reviewer_executor_separation:
        errors.append("team_plan_reviewer_independence_required")

    verifier_author_separation = any(
        rule.get("type") == "author_separation"
        and rule.get("reviewing_role") == "verifier"
        and "contribution" in set(rule.get("target_entity_types", []))
        and "claim" in set(rule.get("target_contribution_types", []))
        for rule in independence_rules
    )
    if requirement_counts.get("verifier", 0) > 0 and not verifier_author_separation:
        errors.append("team_plan_verifier_author_independence_required")

    roles_in_requirements = set(requirement_counts)
    for rule in independence_rules:
        if rule.get("type") == "role_separation":
            role_a = rule.get("role_a")
            role_b = rule.get("role_b")
            if role_a == role_b:
                errors.append("independence_rule_same_role")
            if role_a not in roles_in_requirements or role_b not in roles_in_requirements:
                errors.append("independence_rule_unknown_role")
        elif rule.get("type") == "author_separation":
            if rule.get("reviewing_role") not in roles_in_requirements:
                errors.append("independence_rule_unknown_role")

    assignment_keys = set(assignment_refs)
    for authority in authorities:
        key = _entity_ref_key(authority.get("role_assignment", {}))
        if key is not None and key not in assignment_keys:
            errors.append("team_decision_authority_not_in_assignments")

    return errors


def validate_team_plan_transition(
    current: dict[str, Any], previous: dict[str, Any] | None
) -> list[str]:
    """Validate TeamPlan lifecycle rules across immutable versions."""

    errors = validate_versioned_entity_transition(current, previous)
    if current.get("legal_payload_schema") != TEAM_PLAN_PAYLOAD_SCHEMA:
        return errors

    payload = current.get("legal_payload", {})
    errors.extend(validate_team_plan_semantics(payload))
    if current.get("entity", {}).get("entity_version") == 1 and payload.get("status") != "active":
        errors.append("team_plan_initial_status_not_active")
    if (
        previous is not None
        and previous.get("legal_payload_schema") == TEAM_PLAN_PAYLOAD_SCHEMA
        and previous.get("legal_payload", {}).get("status") in TEAM_PLAN_TERMINAL_STATUSES
    ):
        errors.append("team_plan_terminal_status_final")
    return errors


def _resolve_effective_goal_contract(
    goal_ref: dict[str, Any], state: GoalContractState
) -> tuple[dict[str, Any] | None, list[str]]:
    key = _entity_ref_key(goal_ref)
    if key is None:
        return None, []
    record = state.contracts.get(key)
    if record is None:
        return None, ["team_plan_goal_not_found"]

    errors: list[str] = []
    _, entity_id, entity_version = key
    latest = state.latest_versions.get(("goal_contract", entity_id))
    effective = record
    if isinstance(latest, int) and latest > entity_version:
        for successor_version in range(entity_version + 1, latest + 1):
            successor = state.contracts.get(("goal_contract", entity_id, successor_version))
            if successor is None:
                return None, ["team_plan_goal_lineage_incomplete"]
            effective = successor
            if successor.get("change_metadata", {}).get("substantive_effect") != "none":
                errors.append("team_plan_goal_version_not_current")
                break
    elif latest != entity_version:
        errors.append("team_plan_goal_version_not_current")

    if effective.get("legal_payload_schema") != GOAL_CONTRACT_PAYLOAD_SCHEMA:
        errors.append("team_plan_goal_payload_schema_mismatch")
        return None, errors
    if effective.get("legal_payload", {}).get("status") != "active":
        errors.append("team_plan_goal_inactive")
    return effective, errors


def _resolve_team_assignment_records(
    team_plan: dict[str, Any],
    state: RoleAssignmentAuthorityState,
    *,
    evaluated_at: str,
    agent_card_state: AgentCardState | None,
) -> tuple[dict[EntityVersionKey, dict[str, Any]], list[str]]:
    records: dict[EntityVersionKey, dict[str, Any]] = {}
    errors: list[str] = []
    evaluation_time = _parse_datetime(evaluated_at)

    for assignment in team_plan.get("assignments", []):
        ref = assignment.get("role_assignment", {})
        key = _entity_ref_key(ref)
        if key is None:
            continue
        record = state.assignments.get(key)
        if record is None:
            errors.append("team_role_assignment_not_found")
            continue
        records[key] = record
        _, entity_id, entity_version = key
        if state.latest_versions.get(("role_assignment", entity_id)) != entity_version:
            errors.append("team_role_assignment_version_not_current")
        if record.get("legal_payload_schema") != ROLE_ASSIGNMENT_PAYLOAD_SCHEMA:
            errors.append("team_role_assignment_payload_schema_mismatch")
            continue
        payload = record.get("legal_payload", {})
        if payload.get("role") != assignment.get("role"):
            errors.append("team_role_assignment_role_mismatch")
        if payload.get("status") != "active":
            errors.append("team_role_assignment_inactive")
        valid_from = payload.get("valid_from")
        valid_until = payload.get("valid_until")
        if isinstance(valid_from, str) and evaluation_time < _parse_datetime(valid_from):
            errors.append("team_role_assignment_not_yet_valid")
        if isinstance(valid_until, str) and evaluation_time >= _parse_datetime(valid_until):
            errors.append("team_role_assignment_expired")
        if agent_card_state is not None:
            for error in validate_role_assignment_agent_eligibility(record, agent_card_state):
                errors.append(f"team_{error}")
    return records, errors


def validate_team_plan_against_goal_and_assignments(
    team_plan: dict[str, Any],
    goal_state: GoalContractState,
    role_state: RoleAssignmentAuthorityState,
    *,
    evaluated_at: str,
    agent_card_state: AgentCardState | None = None,
) -> list[str]:
    """Validate a TeamPlan against its pinned goal and assigned roles."""

    errors = validate_team_plan_semantics(team_plan)
    goal_record, goal_errors = _resolve_effective_goal_contract(
        team_plan.get("goal", {}), goal_state
    )
    errors.extend(goal_errors)
    assignment_records, assignment_errors = _resolve_team_assignment_records(
        team_plan,
        role_state,
        evaluated_at=evaluated_at,
        agent_card_state=agent_card_state,
    )
    errors.extend(assignment_errors)
    if goal_record is None:
        return errors

    goal = goal_record.get("legal_payload", {})
    assessment = team_plan.get("planning_assessment", {})
    risk = goal.get("risk_policy", {})
    requirement_roles = {
        item.get("role")
        for item in team_plan.get("role_requirements", [])
        if isinstance(item.get("role"), str)
    }
    if len(goal.get("constraints", [])) > 1 and "reviewer" not in requirement_roles:
        if "team_plan_reviewer_required" not in errors:
            errors.append("team_plan_reviewer_required")
    if assessment.get("risk_level") != risk.get("risk_level"):
        errors.append("team_plan_risk_level_mismatch")
    if assessment.get("error_cost") != risk.get("error_cost"):
        errors.append("team_plan_error_cost_mismatch")

    verifiability_types = {
        item.get("verifiability")
        for item in goal.get("constraints", []) + goal.get("success_criteria", [])
        if isinstance(item.get("verifiability"), str)
    }
    expected_verifiability = (
        next(iter(verifiability_types)) if len(verifiability_types) == 1 else "mixed"
    )
    if assessment.get("verifiability") != expected_verifiability:
        errors.append("team_plan_verifiability_mismatch")

    deferred = {
        item.get("decision_subject"): item
        for item in goal.get("decision_policy", [])
        if item.get("authority", {}).get("mode") == "team_plan"
    }
    declared = {
        item.get("decision_subject"): item
        for item in team_plan.get("decision_authorities", [])
        if isinstance(item.get("decision_subject"), str)
    }
    if set(declared) != set(deferred):
        errors.append("team_plan_decision_authority_coverage_mismatch")

    records_by_ref = assignment_records
    assignment_principals_by_role: dict[str, set[PrincipalKey]] = {}
    for assignment in team_plan.get("assignments", []):
        key = _entity_ref_key(assignment.get("role_assignment", {}))
        record = records_by_ref.get(key) if key is not None else None
        if record is None:
            continue
        principal = _principal_key(record.get("legal_payload", {}).get("subject"))
        role = assignment.get("role")
        if principal is not None and isinstance(role, str):
            assignment_principals_by_role.setdefault(role, set()).add(principal)

    for rule in team_plan.get("independence_rules", []):
        if rule.get("type") != "role_separation":
            continue
        left = assignment_principals_by_role.get(rule.get("role_a"), set())
        right = assignment_principals_by_role.get(rule.get("role_b"), set())
        if left & right:
            errors.append("team_independence_role_separation_violated")

    for subject, plan_authority in declared.items():
        key = _entity_ref_key(plan_authority.get("role_assignment", {}))
        record = records_by_ref.get(key) if key is not None else None
        if record is None:
            continue
        payload = record.get("legal_payload", {})
        if payload.get("role") != "decision_authority":
            errors.append("team_decision_authority_role_mismatch")
        if subject not in set(payload.get("authority_scope", {}).get("decision_subjects", [])):
            errors.append("team_decision_subject_not_authorized")
        goal_policy = deferred.get(subject, {})
        if (
            goal_policy.get("human_approval_required") is True
            and payload.get("subject", {}).get("principal_type") != "human"
        ):
            errors.append("team_decision_authority_must_be_human")
        authority_principal = _principal_key(payload.get("subject"))
        for independent_role in goal_policy.get("independent_from_roles", []):
            if authority_principal in assignment_principals_by_role.get(independent_role, set()):
                errors.append("team_goal_independence_requirement_violated")

    return errors


@dataclass(frozen=True)
class TeamPlanState:
    """Accepted TeamPlan records used to validate ContextGrant membership."""

    plans: dict[EntityVersionKey, dict[str, Any]] = field(default_factory=dict)
    latest_versions: dict[EntityKey, int] = field(default_factory=dict)


@dataclass(frozen=True)
class ContextGrantState:
    """Accepted ContextGrant records used when constructing an agent input."""

    grants: dict[EntityVersionKey, dict[str, Any]] = field(default_factory=dict)
    latest_versions: dict[EntityKey, int] = field(default_factory=dict)


def validate_context_grant_semantics(context_grant: dict[str, Any]) -> list[str]:
    """Return stable local cross-field errors for a ContextGrant payload."""

    errors: list[str] = []
    valid_from = context_grant.get("valid_from")
    expires_at = context_grant.get("expires_at")
    if isinstance(valid_from, str) and isinstance(expires_at, str):
        if _parse_datetime(expires_at) <= _parse_datetime(valid_from):
            errors.append("context_grant_invalid_validity_interval")

    fields = context_grant.get("fields", [])
    if len(fields) != len(set(fields)):
        errors.append("context_grant_duplicate_field")

    return errors


def validate_context_grant_transition(
    current: dict[str, Any], previous: dict[str, Any] | None
) -> list[str]:
    """Validate ContextGrant lineage and terminal revocation rules."""

    errors = validate_versioned_entity_transition(current, previous)
    if current.get("legal_payload_schema") != CONTEXT_GRANT_PAYLOAD_SCHEMA:
        return errors

    payload = current.get("legal_payload", {})
    errors.extend(validate_context_grant_semantics(payload))
    if current.get("entity", {}).get("entity_version") == 1 and payload.get("status") != "active":
        errors.append("context_grant_initial_status_not_active")

    if previous is None or previous.get("legal_payload_schema") != CONTEXT_GRANT_PAYLOAD_SCHEMA:
        return errors

    previous_payload = previous.get("legal_payload", {})
    for field_name, error_code in (
        ("recipient_role_assignment", "context_grant_recipient_changed"),
        ("team_plan", "context_grant_team_plan_changed"),
        ("task", "context_grant_task_changed"),
        ("purpose", "context_grant_purpose_changed"),
        ("valid_from", "context_grant_valid_from_changed"),
    ):
        if payload.get(field_name) != previous_payload.get(field_name):
            errors.append(error_code)

    if previous_payload.get("status") == "revoked" and payload.get("status") != "revoked":
        errors.append("context_grant_revocation_final")
    return errors


def _resolve_effective_team_plan(
    team_ref: dict[str, Any], state: TeamPlanState
) -> tuple[dict[str, Any] | None, list[str]]:
    key = _entity_ref_key(team_ref)
    if key is None:
        return None, []
    record = state.plans.get(key)
    if record is None:
        return None, ["context_grant_team_plan_not_found"]

    errors: list[str] = []
    _, entity_id, entity_version = key
    latest = state.latest_versions.get(("team_plan", entity_id))
    effective = record
    if isinstance(latest, int) and latest > entity_version:
        for successor_version in range(entity_version + 1, latest + 1):
            successor = state.plans.get(("team_plan", entity_id, successor_version))
            if successor is None:
                return None, ["context_grant_team_plan_lineage_incomplete"]
            effective = successor
            if successor.get("change_metadata", {}).get("substantive_effect") != "none":
                errors.append("context_grant_team_plan_version_not_current")
                break
    elif latest != entity_version:
        errors.append("context_grant_team_plan_version_not_current")

    if effective.get("legal_payload_schema") != TEAM_PLAN_PAYLOAD_SCHEMA:
        errors.append("context_grant_team_plan_payload_schema_mismatch")
        return None, errors
    if effective.get("legal_payload", {}).get("status") != "active":
        errors.append("context_grant_team_plan_inactive")
    return effective, errors


def validate_context_grant_against_team_and_recipient(
    context_grant: dict[str, Any],
    team_state: TeamPlanState,
    role_state: RoleAssignmentAuthorityState,
    *,
    evaluated_at: str,
    agent_card_state: AgentCardState | None = None,
    task_state: TaskState | None = None,
) -> list[str]:
    """Validate a grant against its team membership, task, and recipient policy."""

    errors = validate_context_grant_semantics(context_grant)
    if task_state is not None:
        errors.extend(validate_context_grant_against_task(context_grant, task_state))
    team_record, team_errors = _resolve_effective_team_plan(
        context_grant.get("team_plan", {}), team_state
    )
    errors.extend(team_errors)

    recipient_ref = context_grant.get("recipient_role_assignment", {})
    recipient_key = _entity_ref_key(recipient_ref)
    role_record = role_state.assignments.get(recipient_key) if recipient_key else None
    if role_record is None:
        errors.append("context_grant_recipient_not_found")
        return errors

    _, role_id, role_version = recipient_key
    if role_state.latest_versions.get(("role_assignment", role_id)) != role_version:
        errors.append("context_grant_recipient_version_not_current")
    if role_record.get("legal_payload_schema") != ROLE_ASSIGNMENT_PAYLOAD_SCHEMA:
        errors.append("context_grant_recipient_payload_schema_mismatch")
        return errors

    role_payload = role_record.get("legal_payload", {})
    if role_payload.get("status") != "active":
        errors.append("context_grant_recipient_inactive")
    evaluation_time = _parse_datetime(evaluated_at)
    role_start = role_payload.get("valid_from")
    role_end = role_payload.get("valid_until")
    if isinstance(role_start, str) and evaluation_time < _parse_datetime(role_start):
        errors.append("context_grant_recipient_not_yet_valid")
    if isinstance(role_end, str) and evaluation_time >= _parse_datetime(role_end):
        errors.append("context_grant_recipient_expired")

    grant_start = context_grant.get("valid_from")
    grant_end = context_grant.get("expires_at")
    if isinstance(role_start, str) and isinstance(grant_start, str):
        if _parse_datetime(grant_start) < _parse_datetime(role_start):
            errors.append("context_grant_starts_before_recipient")
    if isinstance(role_end, str) and isinstance(grant_end, str):
        if _parse_datetime(grant_end) > _parse_datetime(role_end):
            errors.append("context_grant_outlives_recipient")

    if team_record is not None:
        assignments = team_record.get("legal_payload", {}).get("assignments", [])
        assignment_refs = {
            _entity_ref_key(item.get("role_assignment", {})) for item in assignments
        }
        if recipient_key not in assignment_refs:
            errors.append("context_grant_recipient_not_in_team")

    if agent_card_state is not None:
        eligibility_errors = validate_role_assignment_agent_eligibility(
            role_record, agent_card_state
        )
        errors.extend(f"context_grant_{error}" for error in eligibility_errors)

        subject = role_payload.get("subject", {})
        if subject.get("principal_type") == "agent":
            refs = _agent_card_basis_refs(role_payload)
            if len(refs) == 1:
                card_key = _entity_ref_key(refs[0])
                card_record = agent_card_state.cards.get(card_key) if card_key else None
                if card_record is not None:
                    card = card_record.get("legal_payload", {})
                    policy = card.get("data_policy", {})
                    permissions = card.get("permissions", {})
                    if context_grant.get("classification") not in set(
                        policy.get("accepted_classifications", [])
                    ):
                        errors.append("context_grant_classification_not_accepted")
                    if "granted_context" not in set(permissions.get("data_read", [])):
                        errors.append("context_grant_read_permission_missing")
                    if (
                        policy.get("external_processing") is True
                        and context_grant.get("processing_mode") != "external_allowed"
                    ):
                        errors.append("context_grant_external_processing_not_allowed")

    return errors


def validate_context_grant_use(
    grant_ref: dict[str, Any],
    *,
    recipient_role_assignment: dict[str, Any],
    task: dict[str, Any],
    requested_fields: list[str],
    purpose_code: str,
    used_at: str,
    state: ContextGrantState,
    task_state: TaskState | None = None,
) -> list[str]:
    """Authorize one context-input construction against a pinned grant."""

    key = _entity_ref_key(grant_ref)
    if key is None:
        return []
    record = state.grants.get(key)
    if record is None:
        return ["context_grant_not_found"]

    errors: list[str] = []
    _, entity_id, entity_version = key
    if state.latest_versions.get(("context_grant", entity_id)) != entity_version:
        errors.append("context_grant_version_not_current")
    if record.get("legal_payload_schema") != CONTEXT_GRANT_PAYLOAD_SCHEMA:
        errors.append("context_grant_payload_schema_mismatch")
        return errors

    payload = record.get("legal_payload", {})
    if payload.get("status") != "active":
        errors.append("context_grant_inactive")
    when = _parse_datetime(used_at)
    valid_from = payload.get("valid_from")
    expires_at = payload.get("expires_at")
    if isinstance(valid_from, str) and when < _parse_datetime(valid_from):
        errors.append("context_grant_not_yet_valid")
    if isinstance(expires_at, str) and when >= _parse_datetime(expires_at):
        errors.append("context_grant_expired")
    if not _same_entity_ref(payload.get("recipient_role_assignment"), recipient_role_assignment):
        errors.append("context_grant_recipient_mismatch")
    if not _same_entity_ref(payload.get("task"), task):
        errors.append("context_grant_task_mismatch")
    if payload.get("purpose", {}).get("code") != purpose_code:
        errors.append("context_grant_purpose_mismatch")
    allowed_fields = set(payload.get("fields", []))
    if not set(requested_fields).issubset(allowed_fields):
        errors.append("context_grant_field_not_allowed")
    if task_state is not None:
        errors.extend(validate_context_grant_against_task(payload, task_state))
    return errors


TASK_ALLOWED_STATUS_TRANSITIONS: dict[str, frozenset[str]] = {
    "created": frozenset({"created", "ready", "cancelled"}),
    "ready": frozenset({"ready", "assigned", "blocked", "cancelled"}),
    "assigned": frozenset({"assigned", "in_progress", "blocked", "cancelled"}),
    "in_progress": frozenset({"in_progress", "blocked", "completed", "failed", "cancelled"}),
    "blocked": frozenset({"blocked", "ready", "assigned", "in_progress", "failed", "cancelled"}),
    "completed": frozenset(),
    "failed": frozenset(),
    "cancelled": frozenset(),
}


def validate_task_semantics(task: dict[str, Any]) -> list[str]:
    """Return stable local cross-field errors for a Task legal payload."""

    errors: list[str] = []
    requirements = task.get("required_outputs", [])
    produced = task.get("produced_outputs", [])
    errors.extend(_duplicate_value_errors(requirements, "output_id", "duplicate_task_output_id"))
    errors.extend(_duplicate_value_errors(produced, "output_id", "duplicate_task_produced_output_id"))

    requirements_by_id = {
        item.get("output_id"): item
        for item in requirements
        if isinstance(item.get("output_id"), str)
    }
    produced_ids: set[str] = set()
    for item in produced:
        output_id = item.get("output_id")
        if not isinstance(output_id, str):
            continue
        produced_ids.add(output_id)
        requirement = requirements_by_id.get(output_id)
        if requirement is None:
            errors.append("task_produced_output_unknown")
            continue
        entity_type = item.get("entity", {}).get("entity_type")
        if entity_type != requirement.get("entity_type"):
            errors.append("task_produced_output_type_mismatch")

    if task.get("status") == "completed":
        for requirement in requirements:
            if requirement.get("required") is True and requirement.get("output_id") not in produced_ids:
                errors.append("task_required_output_missing")
                break
    return errors


def validate_task_transition(
    current: dict[str, Any], previous: dict[str, Any] | None
) -> list[str]:
    """Validate Task lifecycle and phase-bounded scope across versions."""

    errors = validate_versioned_entity_transition(current, previous)
    if current.get("legal_payload_schema") != TASK_PAYLOAD_SCHEMA:
        return errors
    payload = current.get("legal_payload", {})
    errors.extend(validate_task_semantics(payload))
    if current.get("entity", {}).get("entity_version") == 1 and payload.get("status") != "created":
        errors.append("task_initial_status_not_created")
    if previous is None or previous.get("legal_payload_schema") != TASK_PAYLOAD_SCHEMA:
        return errors

    previous_payload = previous.get("legal_payload", {})
    for field_name, error_code in (
        ("goal", "task_goal_changed"),
        ("team_plan", "task_team_plan_changed"),
        ("phase", "task_phase_changed"),
        ("dependencies", "task_dependencies_changed"),
        ("required_outputs", "task_required_outputs_changed"),
        ("required_context_fields", "task_required_context_changed"),
    ):
        if payload.get(field_name) != previous_payload.get(field_name):
            errors.append(error_code)

    previous_status = previous_payload.get("status")
    current_status = payload.get("status")
    if previous_status in TASK_TERMINAL_STATUSES:
        errors.append("task_terminal_status_final")
    elif (
        isinstance(previous_status, str)
        and isinstance(current_status, str)
        and current_status not in TASK_ALLOWED_STATUS_TRANSITIONS.get(previous_status, frozenset())
    ):
        errors.append("task_status_transition_not_allowed")
    return errors


@dataclass(frozen=True)
class TaskState:
    """Accepted Task records used for dependency and update checks."""

    tasks: dict[EntityVersionKey, dict[str, Any]] = field(default_factory=dict)
    latest_versions: dict[EntityKey, int] = field(default_factory=dict)


def validate_context_grant_against_task(
    context_grant: dict[str, Any], state: TaskState
) -> list[str]:
    """Bind a ContextGrant to the pinned Task scope and its current lifecycle."""

    task_ref = context_grant.get("task", {})
    key = _entity_ref_key(task_ref)
    if key is None:
        return []
    pinned = state.tasks.get(key)
    if pinned is None:
        return ["context_grant_task_not_found"]
    if pinned.get("legal_payload_schema") != TASK_PAYLOAD_SCHEMA:
        return ["context_grant_task_payload_schema_mismatch"]

    errors: list[str] = []
    _, entity_id, entity_version = key
    latest_version = state.latest_versions.get(("task", entity_id))
    current = pinned
    if isinstance(latest_version, int) and latest_version >= entity_version:
        for version in range(entity_version + 1, latest_version + 1):
            successor = state.tasks.get(("task", entity_id, version))
            if successor is None:
                errors.append("context_grant_task_lineage_incomplete")
                return errors
            current = successor
    elif latest_version != entity_version:
        errors.append("context_grant_task_version_not_current")

    pinned_payload = pinned.get("legal_payload", {})
    current_payload = current.get("legal_payload", {})
    for field_name in ("goal", "team_plan", "phase", "required_context_fields"):
        if current_payload.get(field_name) != pinned_payload.get(field_name):
            errors.append("context_grant_task_scope_changed")
            break
    if not _same_entity_ref(context_grant.get("team_plan"), pinned_payload.get("team_plan")):
        errors.append("context_grant_task_team_plan_mismatch")
    if not _same_entity_ref(
        context_grant.get("recipient_role_assignment"),
        current_payload.get("assigned_role_assignment"),
    ):
        errors.append("context_grant_task_recipient_mismatch")
    requested = set(context_grant.get("fields", []))
    required = set(pinned_payload.get("required_context_fields", []))
    if not requested.issubset(required):
        errors.append("context_grant_field_not_required_by_task")
    if current_payload.get("status") in TASK_TERMINAL_STATUSES:
        errors.append("context_grant_task_terminal")
    return errors


def _resolve_task_goal(
    goal_ref: dict[str, Any], state: GoalContractState
) -> tuple[dict[str, Any] | None, list[str]]:
    key = _entity_ref_key(goal_ref)
    if key is None:
        return None, []
    record = state.contracts.get(key)
    if record is None:
        return None, ["task_goal_not_found"]
    errors: list[str] = []
    _, entity_id, entity_version = key
    latest = state.latest_versions.get(("goal_contract", entity_id))
    effective = record
    if isinstance(latest, int) and latest > entity_version:
        for successor_version in range(entity_version + 1, latest + 1):
            successor = state.contracts.get(("goal_contract", entity_id, successor_version))
            if successor is None:
                return None, ["task_goal_lineage_incomplete"]
            effective = successor
            if successor.get("change_metadata", {}).get("substantive_effect") != "none":
                errors.append("task_goal_version_not_current")
                break
    elif latest != entity_version:
        errors.append("task_goal_version_not_current")
    if effective.get("legal_payload_schema") != GOAL_CONTRACT_PAYLOAD_SCHEMA:
        errors.append("task_goal_payload_schema_mismatch")
        return None, errors
    if effective.get("legal_payload", {}).get("status") != "active":
        errors.append("task_goal_inactive")
    return effective, errors


def _resolve_task_team(
    team_ref: dict[str, Any], state: TeamPlanState
) -> tuple[dict[str, Any] | None, list[str]]:
    key = _entity_ref_key(team_ref)
    if key is None:
        return None, []
    record = state.plans.get(key)
    if record is None:
        return None, ["task_team_plan_not_found"]
    errors: list[str] = []
    _, entity_id, entity_version = key
    latest = state.latest_versions.get(("team_plan", entity_id))
    effective = record
    if isinstance(latest, int) and latest > entity_version:
        for successor_version in range(entity_version + 1, latest + 1):
            successor = state.plans.get(("team_plan", entity_id, successor_version))
            if successor is None:
                return None, ["task_team_plan_lineage_incomplete"]
            effective = successor
            if successor.get("change_metadata", {}).get("substantive_effect") != "none":
                errors.append("task_team_plan_version_not_current")
                break
    elif latest != entity_version:
        errors.append("task_team_plan_version_not_current")
    if effective.get("legal_payload_schema") != TEAM_PLAN_PAYLOAD_SCHEMA:
        errors.append("task_team_plan_payload_schema_mismatch")
        return None, errors
    if effective.get("legal_payload", {}).get("status") != "active":
        errors.append("task_team_plan_inactive")
    return effective, errors


def validate_task_against_goal_team_and_assignment(
    task: dict[str, Any],
    goal_state: GoalContractState,
    team_state: TeamPlanState,
    role_state: RoleAssignmentAuthorityState,
    *,
    evaluated_at: str,
    agent_card_state: AgentCardState | None = None,
) -> list[str]:
    """Validate task basis, partial budget, assignee membership, and phase role."""

    errors = validate_task_semantics(task)
    goal_record, goal_errors = _resolve_task_goal(task.get("goal", {}), goal_state)
    team_record, team_errors = _resolve_task_team(task.get("team_plan", {}), team_state)
    errors.extend(goal_errors)
    errors.extend(team_errors)
    if goal_record is not None:
        deliverable_ids = {
            item.get("deliverable_id")
            for item in goal_record.get("legal_payload", {}).get("deliverables", [])
            if isinstance(item.get("deliverable_id"), str)
        }
        for requirement in task.get("required_outputs", []):
            linked = requirement.get("goal_deliverable_id")
            if isinstance(linked, str) and linked not in deliverable_ids:
                errors.append("task_goal_deliverable_not_found")
    if team_record is not None:
        team = team_record.get("legal_payload", {})
        if not _same_entity_ref(team.get("goal"), task.get("goal")):
            errors.append("task_goal_team_plan_mismatch")
        team_budget = team.get("budget", {})
        for field_name, allocated in task.get("budget_allocation", {}).items():
            team_limit = team_budget.get(field_name)
            if isinstance(allocated, int) and isinstance(team_limit, int) and allocated > team_limit:
                errors.append("task_budget_exceeds_team_plan")
                break

    assignee_ref = task.get("assigned_role_assignment")
    if not isinstance(assignee_ref, dict):
        return errors
    assignee_key = _entity_ref_key(assignee_ref)
    record = role_state.assignments.get(assignee_key) if assignee_key else None
    if record is None:
        errors.append("task_assignee_not_found")
        return errors
    _, entity_id, entity_version = assignee_key
    if role_state.latest_versions.get(("role_assignment", entity_id)) != entity_version:
        errors.append("task_assignee_version_not_current")
    if record.get("legal_payload_schema") != ROLE_ASSIGNMENT_PAYLOAD_SCHEMA:
        errors.append("task_assignee_payload_schema_mismatch")
        return errors
    role_payload = record.get("legal_payload", {})
    if role_payload.get("status") != "active":
        errors.append("task_assignee_inactive")
    when = _parse_datetime(evaluated_at)
    valid_from = role_payload.get("valid_from")
    valid_until = role_payload.get("valid_until")
    if isinstance(valid_from, str) and when < _parse_datetime(valid_from):
        errors.append("task_assignee_not_yet_valid")
    if isinstance(valid_until, str) and when >= _parse_datetime(valid_until):
        errors.append("task_assignee_expired")
    allowed_roles = TASK_PHASE_ROLES.get(task.get("phase"), frozenset())
    if role_payload.get("role") not in allowed_roles:
        errors.append("task_assignee_role_phase_mismatch")
    if team_record is not None:
        assignment_keys = {
            _entity_ref_key(item.get("role_assignment", {}))
            for item in team_record.get("legal_payload", {}).get("assignments", [])
        }
        if assignee_key not in assignment_keys:
            errors.append("task_assignee_not_in_team")
    if agent_card_state is not None:
        errors.extend(f"task_{error}" for error in validate_role_assignment_agent_eligibility(record, agent_card_state))
    return errors


def validate_task_graph_and_dependencies(
    current: dict[str, Any], state: TaskState
) -> list[str]:
    """Validate pinned dependencies, completion readiness, and acyclicity."""

    if current.get("legal_payload_schema") != TASK_PAYLOAD_SCHEMA:
        return []
    errors: list[str] = []
    current_ref = current.get("entity", {})
    current_key = _entity_ref_key(current_ref)
    payload = current.get("legal_payload", {})
    dependency_keys: list[EntityVersionKey] = []
    dependency_identities: set[EntityKey] = set()
    for dependency in payload.get("dependencies", []):
        ref = dependency.get("task", {})
        key = _entity_ref_key(ref)
        if key is None:
            continue
        dependency_keys.append(key)
        identity = key[:2]
        if identity in dependency_identities:
            errors.append("task_duplicate_dependency")
        dependency_identities.add(identity)
        if current_key is not None and current_key[:2] == key[:2]:
            errors.append("task_self_dependency")
            continue
        record = state.tasks.get(key)
        if record is None:
            errors.append("task_dependency_not_found")
            continue
        _, entity_id, entity_version = key
        if state.latest_versions.get(("task", entity_id)) != entity_version:
            errors.append("task_dependency_version_not_current")
        dependency_payload = record.get("legal_payload", {})
        if not _same_entity_ref(dependency_payload.get("goal"), payload.get("goal")):
            errors.append("task_dependency_goal_mismatch")
        if not _same_entity_ref(dependency_payload.get("team_plan"), payload.get("team_plan")):
            errors.append("task_dependency_team_plan_mismatch")
        if payload.get("status") in {"ready", "assigned", "in_progress", "completed"} and dependency_payload.get("status") != "completed":
            errors.append("task_dependency_not_completed")

    # Build a graph from the current candidate plus latest accepted tasks.
    latest_records: dict[EntityKey, dict[str, Any]] = {}
    for identity, version in state.latest_versions.items():
        record = state.tasks.get((identity[0], identity[1], version))
        if record is not None:
            latest_records[identity] = record
    if current_key is not None:
        latest_records[(current_key[0], current_key[1])] = current
    graph: dict[EntityKey, set[EntityKey]] = {}
    for identity, record in latest_records.items():
        deps: set[EntityKey] = set()
        for dependency in record.get("legal_payload", {}).get("dependencies", []):
            ref = dependency.get("task", {})
            if ref.get("entity_type") == "task" and isinstance(ref.get("entity_id"), str):
                deps.add(("task", ref["entity_id"]))
        graph[identity] = deps
    visiting: set[EntityKey] = set()
    visited: set[EntityKey] = set()
    def visit(node: EntityKey) -> bool:
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        for dep in graph.get(node, set()):
            if dep in graph and visit(dep):
                return True
        visiting.remove(node)
        visited.add(node)
        return False
    if any(visit(node) for node in list(graph) if node not in visited):
        errors.append("task_dependency_cycle")
    return errors


def validate_task_budget_pool(
    current: dict[str, Any], state: TaskState, team_state: TeamPlanState
) -> list[str]:
    """Ensure aggregate task call allocations stay within the TeamPlan budget."""

    if current.get("legal_payload_schema") != TASK_PAYLOAD_SCHEMA:
        return []
    team_ref = current.get("legal_payload", {}).get("team_plan", {})
    team_key = _entity_ref_key(team_ref)
    team_record = team_state.plans.get(team_key) if team_key else None
    if team_record is None or team_record.get("legal_payload_schema") != TEAM_PLAN_PAYLOAD_SCHEMA:
        return []
    team_budget = team_record.get("legal_payload", {}).get("budget", {})

    latest_records: dict[EntityKey, dict[str, Any]] = {}
    for identity, version in state.latest_versions.items():
        record = state.tasks.get((identity[0], identity[1], version))
        if record is not None:
            latest_records[identity] = record
    current_key = _entity_ref_key(current.get("entity", {}))
    if current_key is not None:
        latest_records[current_key[:2]] = current

    totals = {"max_model_calls": 0, "max_tool_calls": 0, "max_review_cycles": 0}
    for record in latest_records.values():
        if record.get("legal_payload_schema") != TASK_PAYLOAD_SCHEMA:
            continue
        payload = record.get("legal_payload", {})
        if not _same_entity_ref(payload.get("team_plan"), team_ref):
            continue
        for field_name in totals:
            value = payload.get("budget_allocation", {}).get(field_name)
            if isinstance(value, int):
                totals[field_name] += value
    for field_name, total in totals.items():
        limit = team_budget.get(field_name)
        if isinstance(limit, int) and total > limit:
            return ["task_budget_pool_exceeded"]
    return []


def validate_task_update_authority(
    envelope: dict[str, Any],
    authority_state: RoleAssignmentAuthorityState,
    task_state: TaskState,
    *,
    acceptance_time: str | None = None,
    agent_card_state: AgentCardState | None = None,
) -> list[str]:
    """Restrict task creation and updates to the owner/orchestrator or assignee."""

    errors = validate_envelope_role_assignment_authority(
        envelope,
        authority_state,
        acceptance_time=acceptance_time,
        agent_card_state=agent_card_state,
    )
    operation = get_required_authority_operation(envelope)
    authority_ref = envelope.get("authorized_by", {})
    authority_key = _entity_ref_key(authority_ref)
    authority_record = authority_state.assignments.get(authority_key) if authority_key else None
    role = authority_record.get("legal_payload", {}).get("role") if authority_record else None
    if operation == TASK_CREATE_OPERATION:
        if role not in TASK_CREATE_ROLES and "task_creation_requires_orchestrator_or_owner" not in errors:
            errors.append("task_creation_requires_orchestrator_or_owner")
        return errors
    if operation != TASK_UPDATE_OPERATION:
        return errors
    target = envelope.get("precondition", {}).get("target", {})
    target_key = _entity_ref_key(target)
    task_record = task_state.tasks.get(target_key) if target_key else None
    if task_record is None:
        errors.append("task_authority_target_not_found")
        return errors
    assignee_ref = task_record.get("legal_payload", {}).get("assigned_role_assignment")
    if role not in {"orchestrator", "human_owner"} and not _same_entity_ref(authority_ref, assignee_ref):
        errors.append("task_update_requires_assignee_or_manager")
    return errors



def _duplicate_entity_identity_errors(
    refs: list[dict[str, Any]], error_code: str
) -> list[str]:
    seen: set[EntityKey] = set()
    errors: list[str] = []
    for ref in refs:
        entity_type = ref.get("entity_type")
        entity_id = ref.get("entity_id")
        if not isinstance(entity_type, str) or not isinstance(entity_id, str):
            continue
        identity = (entity_type, entity_id)
        if identity in seen and error_code not in errors:
            errors.append(error_code)
        seen.add(identity)
    return errors


def validate_contribution_semantics(contribution: dict[str, Any]) -> list[str]:
    """Return stable local cross-field errors for a Contribution payload."""

    errors: list[str] = []
    errors.extend(
        _duplicate_entity_identity_errors(
            contribution.get("basis", []), "duplicate_contribution_basis_identity"
        )
    )
    usage_refs = [
        usage.get("grant", {})
        for usage in contribution.get("context_usage", [])
        if isinstance(usage, dict)
    ]
    errors.extend(
        _duplicate_entity_identity_errors(
            usage_refs, "duplicate_contribution_context_grant_identity"
        )
    )
    return errors


def validate_contribution_transition(
    current: dict[str, Any], previous: dict[str, Any] | None
) -> list[str]:
    """Validate immutable provenance and withdrawal rules across versions."""

    errors = validate_versioned_entity_transition(current, previous)
    if current.get("legal_payload_schema") != CONTRIBUTION_PAYLOAD_SCHEMA:
        return errors

    payload = current.get("legal_payload", {})
    errors.extend(validate_contribution_semantics(payload))
    if current.get("entity", {}).get("entity_version") == 1 and payload.get("status") != "active":
        errors.append("contribution_initial_status_not_active")

    if previous is None or previous.get("legal_payload_schema") != CONTRIBUTION_PAYLOAD_SCHEMA:
        return errors

    previous_payload = previous.get("legal_payload", {})
    for field_name, error_code in (
        ("goal", "contribution_goal_changed"),
        ("team_plan", "contribution_team_plan_changed"),
        ("task", "contribution_task_changed"),
        ("authored_by_role_assignment", "contribution_author_changed"),
        ("contribution_type", "contribution_type_changed"),
        ("fulfills_output_id", "contribution_output_binding_changed"),
    ):
        if payload.get(field_name) != previous_payload.get(field_name):
            errors.append(error_code)

    previous_classification = CONTRIBUTION_CLASSIFICATION_RANK.get(
        previous_payload.get("classification")
    )
    current_classification = CONTRIBUTION_CLASSIFICATION_RANK.get(
        payload.get("classification")
    )
    if (
        isinstance(previous_classification, int)
        and isinstance(current_classification, int)
        and current_classification < previous_classification
    ):
        errors.append("contribution_classification_downgraded")

    if previous_payload.get("status") == "withdrawn" and payload.get("status") != "withdrawn":
        errors.append("contribution_withdrawal_final")

    if previous_payload.get("status") == "active" and payload.get("status") == "withdrawn":
        immutable_on_withdrawal = (
            "goal", "team_plan", "task", "authored_by_role_assignment",
            "contribution_type", "content", "fulfills_output_id", "basis",
            "context_usage", "limitations", "confidence",
        )
        if any(payload.get(name) != previous_payload.get(name) for name in immutable_on_withdrawal):
            errors.append("contribution_withdrawal_changed_content")

    return errors


@dataclass(frozen=True)
class ContributionState:
    """Accepted Contribution records used by task-output and lineage checks."""

    contributions: dict[EntityVersionKey, dict[str, Any]] = field(default_factory=dict)
    latest_versions: dict[EntityKey, int] = field(default_factory=dict)


def validate_contribution_against_task_author_and_context(
    current: dict[str, Any],
    task_state: TaskState,
    role_state: RoleAssignmentAuthorityState,
    *,
    evaluated_at: str,
    agent_card_state: AgentCardState | None = None,
    context_grant_state: ContextGrantState | None = None,
) -> list[str]:
    """Bind a Contribution to its current task, assignee, phase, and context use."""

    if current.get("legal_payload_schema") != CONTRIBUTION_PAYLOAD_SCHEMA:
        return []
    payload = current.get("legal_payload", {})
    errors = validate_contribution_semantics(payload)

    task_ref = payload.get("task", {})
    task_key = _entity_ref_key(task_ref)
    task_record = task_state.tasks.get(task_key) if task_key else None
    if task_record is None:
        errors.append("contribution_task_not_found")
        return errors
    if task_record.get("legal_payload_schema") != TASK_PAYLOAD_SCHEMA:
        errors.append("contribution_task_payload_schema_mismatch")
        return errors

    _, task_id, task_version = task_key
    if task_state.latest_versions.get(("task", task_id)) != task_version:
        errors.append("contribution_task_version_not_current")

    task_payload = task_record.get("legal_payload", {})
    if not _same_entity_ref(payload.get("goal"), task_payload.get("goal")):
        errors.append("contribution_goal_mismatch")
    if not _same_entity_ref(payload.get("team_plan"), task_payload.get("team_plan")):
        errors.append("contribution_team_plan_mismatch")

    author_ref = payload.get("authored_by_role_assignment", {})
    if not _same_entity_ref(author_ref, task_payload.get("assigned_role_assignment")):
        errors.append("contribution_author_not_task_assignee")

    contribution_type = payload.get("contribution_type")
    phase = task_payload.get("phase")
    if contribution_type not in CONTRIBUTION_TYPES_BY_TASK_PHASE.get(phase, frozenset()):
        errors.append("contribution_type_not_allowed_for_task_phase")

    task_status = task_payload.get("status")
    if task_status != "in_progress" and not (
        contribution_type == "question" and task_status == "blocked"
    ):
        errors.append("contribution_task_not_accepting_outputs")

    output_id = payload.get("fulfills_output_id")
    if output_id is not None:
        requirements = {
            item.get("output_id"): item
            for item in task_payload.get("required_outputs", [])
            if isinstance(item, dict)
        }
        requirement = requirements.get(output_id)
        if requirement is None:
            errors.append("contribution_output_requirement_not_found")
        elif requirement.get("entity_type") != "contribution":
            errors.append("contribution_output_requirement_type_mismatch")

    author_key = _entity_ref_key(author_ref)
    author_record = role_state.assignments.get(author_key) if author_key else None
    if author_record is None:
        errors.append("contribution_author_role_assignment_not_found")
    else:
        _, role_id, role_version = author_key
        if role_state.latest_versions.get(("role_assignment", role_id)) != role_version:
            errors.append("contribution_author_version_not_current")
        if author_record.get("legal_payload_schema") != ROLE_ASSIGNMENT_PAYLOAD_SCHEMA:
            errors.append("contribution_author_payload_schema_mismatch")
        else:
            role_payload = author_record.get("legal_payload", {})
            if role_payload.get("status") != "active":
                errors.append("contribution_author_inactive")
            when = _parse_datetime(evaluated_at)
            valid_from = role_payload.get("valid_from")
            valid_until = role_payload.get("valid_until")
            if isinstance(valid_from, str) and when < _parse_datetime(valid_from):
                errors.append("contribution_author_not_yet_valid")
            if isinstance(valid_until, str) and when >= _parse_datetime(valid_until):
                errors.append("contribution_author_expired")
            allowed_operations = set(role_payload.get("authority_scope", {}).get("operations", []))
            operation = (
                "contribution.create"
                if current.get("entity", {}).get("entity_version") == 1
                else "contribution.next_version"
            )
            if operation not in allowed_operations:
                errors.append("contribution_author_scope_denied")

            if agent_card_state is not None:
                eligibility_errors = validate_role_assignment_agent_eligibility(
                    author_record, agent_card_state
                )
                errors.extend(f"contribution_{error}" for error in eligibility_errors)
                refs = _agent_card_basis_refs(role_payload)
                if role_payload.get("subject", {}).get("principal_type") == "agent" and len(refs) == 1:
                    card_key = _entity_ref_key(refs[0])
                    card_record = agent_card_state.cards.get(card_key) if card_key else None
                    if card_record is not None:
                        writes = set(
                            card_record.get("legal_payload", {})
                            .get("permissions", {})
                            .get("data_write", [])
                        )
                        if "contribution" not in writes:
                            errors.append("contribution_agent_write_permission_missing")

    if context_grant_state is not None:
        for usage in payload.get("context_usage", []):
            grant_ref = usage.get("grant", {})
            grant_key = _entity_ref_key(grant_ref)
            grant_record = context_grant_state.grants.get(grant_key) if grant_key else None
            if grant_record is None:
                errors.append("contribution_context_grant_not_found")
                continue
            grant_payload = grant_record.get("legal_payload", {})
            contribution_rank = CONTRIBUTION_CLASSIFICATION_RANK.get(
                payload.get("classification")
            )
            grant_rank = CONTRIBUTION_CLASSIFICATION_RANK.get(
                grant_payload.get("classification")
            )
            if (
                isinstance(contribution_rank, int)
                and isinstance(grant_rank, int)
                and contribution_rank < grant_rank
            ):
                errors.append("contribution_classification_below_context")
            if not _same_identity(grant_payload.get("task", {}), task_ref):
                errors.append("contribution_context_grant_task_identity_mismatch")
            errors.extend(
                f"contribution_{error}"
                for error in validate_context_grant_use(
                    grant_ref,
                    recipient_role_assignment=author_ref,
                    task=grant_payload.get("task", {}),
                    requested_fields=usage.get("fields_used", []),
                    purpose_code=usage.get("purpose_code", ""),
                    used_at=evaluated_at,
                    state=context_grant_state,
                    task_state=task_state,
                )
            )

    return errors


def validate_task_outputs_against_contributions(
    task_record: dict[str, Any], state: ContributionState
) -> list[str]:
    """Validate Contribution outputs selected by a completed Task."""

    if task_record.get("legal_payload_schema") != TASK_PAYLOAD_SCHEMA:
        return []
    task_payload = task_record.get("legal_payload", {})
    if task_payload.get("status") != "completed":
        return []

    errors: list[str] = []
    task_entity = task_record.get("entity", {})
    for produced in task_payload.get("produced_outputs", []):
        entity_ref = produced.get("entity", {})
        if entity_ref.get("entity_type") != "contribution":
            continue
        key = _entity_ref_key(entity_ref)
        record = state.contributions.get(key) if key else None
        if record is None:
            errors.append("task_contribution_output_not_found")
            continue
        _, contribution_id, contribution_version = key
        latest = state.latest_versions.get(("contribution", contribution_id))
        effective = record
        if isinstance(latest, int) and latest > contribution_version:
            for version in range(contribution_version + 1, latest + 1):
                successor = state.contributions.get(("contribution", contribution_id, version))
                if successor is None:
                    errors.append("task_contribution_output_lineage_incomplete")
                    effective = record
                    break
                effective = successor
                if successor.get("change_metadata", {}).get("substantive_effect") != "none":
                    errors.append("task_contribution_output_version_not_current")
                    break
        elif latest != contribution_version:
            errors.append("task_contribution_output_version_not_current")

        if effective.get("legal_payload_schema") != CONTRIBUTION_PAYLOAD_SCHEMA:
            errors.append("task_contribution_output_payload_schema_mismatch")
            continue
        contribution = effective.get("legal_payload", {})
        if not _same_identity(contribution.get("task", {}), task_entity):
            errors.append("task_contribution_output_task_mismatch")
        if contribution.get("fulfills_output_id") != produced.get("output_id"):
            errors.append("task_contribution_output_binding_mismatch")
        if contribution.get("status") != "active":
            errors.append("task_contribution_output_inactive")

    return errors


@dataclass(frozen=True)
class EvidenceState:
    """Accepted Evidence records used by later Verification and Decision checks."""

    evidence: dict[EntityVersionKey, dict[str, Any]] = field(default_factory=dict)
    latest_versions: dict[EntityKey, int] = field(default_factory=dict)


def validate_evidence_semantics(evidence: dict[str, Any]) -> list[str]:
    """Return stable local cross-field errors for an Evidence payload."""

    errors: list[str] = []
    errors.extend(
        _duplicate_entity_identity_errors(
            evidence.get("targets", []), "duplicate_evidence_target_identity"
        )
    )
    errors.extend(
        _duplicate_entity_identity_errors(
            evidence.get("basis", []), "duplicate_evidence_basis_identity"
        )
    )

    collected_at = evidence.get("collected_at")
    capture_time = evidence.get("source", {}).get("capture", {}).get("performed_at")
    if (
        isinstance(collected_at, str)
        and isinstance(capture_time, str)
        and collected_at != capture_time
    ):
        errors.append("evidence_collection_time_mismatch")

    evidence_rank = CONTRIBUTION_CLASSIFICATION_RANK.get(evidence.get("classification"))
    source_rank = CONTRIBUTION_CLASSIFICATION_RANK.get(
        evidence.get("source", {}).get("classification")
    )
    if (
        isinstance(evidence_rank, int)
        and isinstance(source_rank, int)
        and evidence_rank < source_rank
    ):
        errors.append("evidence_classification_below_source")

    integrity = evidence.get("integrity", {})
    assessed_at = integrity.get("assessed_at")
    if (
        isinstance(collected_at, str)
        and isinstance(assessed_at, str)
        and _parse_datetime(assessed_at) < _parse_datetime(collected_at)
    ):
        errors.append("evidence_integrity_assessed_before_collection")

    return errors


def validate_evidence_transition(
    current: dict[str, Any], previous: dict[str, Any] | None
) -> list[str]:
    """Validate immutable Evidence provenance and withdrawal rules."""

    errors = validate_versioned_entity_transition(current, previous)
    if current.get("legal_payload_schema") != EVIDENCE_PAYLOAD_SCHEMA:
        return errors

    payload = current.get("legal_payload", {})
    errors.extend(validate_evidence_semantics(payload))
    if current.get("entity", {}).get("entity_version") == 1 and payload.get("status") != "active":
        errors.append("evidence_initial_status_not_active")

    if previous is None or previous.get("legal_payload_schema") != EVIDENCE_PAYLOAD_SCHEMA:
        return errors

    previous_payload = previous.get("legal_payload", {})
    for field_name, error_code in (
        ("goal", "evidence_goal_changed"),
        ("team_plan", "evidence_team_plan_changed"),
        ("task", "evidence_task_changed"),
        ("registered_by_role_assignment", "evidence_registrar_changed"),
        ("evidence_type", "evidence_type_changed"),
        ("relation", "evidence_relation_changed"),
        ("targets", "evidence_targets_changed"),
        ("source", "evidence_source_changed"),
        ("collected_at", "evidence_collection_time_changed"),
    ):
        if payload.get(field_name) != previous_payload.get(field_name):
            errors.append(error_code)

    previous_classification = CONTRIBUTION_CLASSIFICATION_RANK.get(
        previous_payload.get("classification")
    )
    current_classification = CONTRIBUTION_CLASSIFICATION_RANK.get(
        payload.get("classification")
    )
    if (
        isinstance(previous_classification, int)
        and isinstance(current_classification, int)
        and current_classification < previous_classification
    ):
        errors.append("evidence_classification_downgraded")

    if previous_payload.get("status") == "withdrawn" and payload.get("status") != "withdrawn":
        errors.append("evidence_withdrawal_final")

    if previous_payload.get("status") == "active" and payload.get("status") == "withdrawn":
        immutable_on_withdrawal = (
            "goal", "team_plan", "task", "registered_by_role_assignment",
            "evidence_type", "relation", "targets", "summary", "source",
            "collected_at", "integrity", "basis", "classification", "limitations",
        )
        if any(payload.get(name) != previous_payload.get(name) for name in immutable_on_withdrawal):
            errors.append("evidence_withdrawal_changed_content")

    return errors


def _resolve_effective_claim_contribution(
    ref: dict[str, Any], state: ContributionState
) -> tuple[dict[str, Any] | None, list[str]]:
    """Resolve editorial-none successors while preserving pinned claim semantics."""

    errors: list[str] = []
    key = _entity_ref_key(ref)
    record = state.contributions.get(key) if key else None
    if record is None:
        return None, ["evidence_target_claim_not_found"]

    _, contribution_id, contribution_version = key
    latest = state.latest_versions.get(("contribution", contribution_id))
    effective = record
    if isinstance(latest, int) and latest > contribution_version:
        for version in range(contribution_version + 1, latest + 1):
            successor = state.contributions.get(("contribution", contribution_id, version))
            if successor is None:
                errors.append("evidence_target_claim_lineage_incomplete")
                return effective, errors
            effective = successor
            if successor.get("change_metadata", {}).get("substantive_effect") != "none":
                errors.append("evidence_target_claim_version_not_current")
                return effective, errors
    elif latest != contribution_version:
        errors.append("evidence_target_claim_version_not_current")

    return effective, errors


def validate_evidence_against_task_registrar_and_claims(
    current: dict[str, Any],
    task_state: TaskState,
    role_state: RoleAssignmentAuthorityState,
    contribution_state: ContributionState,
    *,
    evaluated_at: str,
    agent_card_state: AgentCardState | None = None,
) -> list[str]:
    """Bind Evidence to its task, registrar, pinned claim targets, and source policy."""

    if current.get("legal_payload_schema") != EVIDENCE_PAYLOAD_SCHEMA:
        return []
    payload = current.get("legal_payload", {})
    errors = validate_evidence_semantics(payload)

    task_ref = payload.get("task", {})
    task_key = _entity_ref_key(task_ref)
    task_record = task_state.tasks.get(task_key) if task_key else None
    if task_record is None:
        errors.append("evidence_task_not_found")
        return errors
    if task_record.get("legal_payload_schema") != TASK_PAYLOAD_SCHEMA:
        errors.append("evidence_task_payload_schema_mismatch")
        return errors

    _, task_id, task_version = task_key
    if task_state.latest_versions.get(("task", task_id)) != task_version:
        errors.append("evidence_task_version_not_current")

    task_payload = task_record.get("legal_payload", {})
    if not _same_entity_ref(payload.get("goal"), task_payload.get("goal")):
        errors.append("evidence_goal_mismatch")
    if not _same_entity_ref(payload.get("team_plan"), task_payload.get("team_plan")):
        errors.append("evidence_team_plan_mismatch")

    registrar_ref = payload.get("registered_by_role_assignment", {})
    if not _same_entity_ref(registrar_ref, task_payload.get("assigned_role_assignment")):
        errors.append("evidence_registrar_not_task_assignee")

    if task_payload.get("phase") not in EVIDENCE_TASK_PHASES:
        errors.append("evidence_not_allowed_for_task_phase")
    if task_payload.get("status") != "in_progress":
        errors.append("evidence_task_not_accepting_outputs")

    when = _parse_datetime(evaluated_at)
    collected_at = payload.get("collected_at")
    if isinstance(collected_at, str) and _parse_datetime(collected_at) > when:
        errors.append("evidence_collected_in_future")

    registrar_key = _entity_ref_key(registrar_ref)
    registrar_record = role_state.assignments.get(registrar_key) if registrar_key else None
    if registrar_record is None:
        errors.append("evidence_registrar_role_assignment_not_found")
    else:
        _, role_id, role_version = registrar_key
        if role_state.latest_versions.get(("role_assignment", role_id)) != role_version:
            errors.append("evidence_registrar_version_not_current")
        if registrar_record.get("legal_payload_schema") != ROLE_ASSIGNMENT_PAYLOAD_SCHEMA:
            errors.append("evidence_registrar_payload_schema_mismatch")
        else:
            role_payload = registrar_record.get("legal_payload", {})
            if role_payload.get("status") != "active":
                errors.append("evidence_registrar_inactive")
            valid_from = role_payload.get("valid_from")
            valid_until = role_payload.get("valid_until")
            if isinstance(valid_from, str) and when < _parse_datetime(valid_from):
                errors.append("evidence_registrar_not_yet_valid")
            if isinstance(valid_until, str) and when >= _parse_datetime(valid_until):
                errors.append("evidence_registrar_expired")
            operation = (
                "evidence.create"
                if current.get("entity", {}).get("entity_version") == 1
                else "evidence.next_version"
            )
            operations = set(role_payload.get("authority_scope", {}).get("operations", []))
            if operation not in operations:
                errors.append("evidence_registrar_scope_denied")

            if agent_card_state is not None:
                eligibility_errors = validate_role_assignment_agent_eligibility(
                    registrar_record, agent_card_state
                )
                errors.extend(f"evidence_{error}" for error in eligibility_errors)
                refs = _agent_card_basis_refs(role_payload)
                if role_payload.get("subject", {}).get("principal_type") == "agent" and len(refs) == 1:
                    card_key = _entity_ref_key(refs[0])
                    card_record = agent_card_state.cards.get(card_key) if card_key else None
                    if card_record is not None:
                        writes = set(
                            card_record.get("legal_payload", {})
                            .get("permissions", {})
                            .get("data_write", [])
                        )
                        if "evidence" not in writes:
                            errors.append("evidence_agent_write_permission_missing")

    for target_ref in payload.get("targets", []):
        effective, target_errors = _resolve_effective_claim_contribution(
            target_ref, contribution_state
        )
        errors.extend(target_errors)
        if effective is None:
            continue
        if effective.get("legal_payload_schema") != CONTRIBUTION_PAYLOAD_SCHEMA:
            errors.append("evidence_target_payload_schema_mismatch")
            continue
        claim = effective.get("legal_payload", {})
        if claim.get("contribution_type") != "claim":
            errors.append("evidence_target_not_claim")
        if claim.get("status") != "active":
            errors.append("evidence_target_claim_inactive")
        if not _same_entity_ref(payload.get("goal"), claim.get("goal")):
            errors.append("evidence_target_goal_mismatch")
        if not _same_entity_ref(payload.get("team_plan"), claim.get("team_plan")):
            errors.append("evidence_target_team_plan_mismatch")

    assessor_ref = payload.get("integrity", {}).get("assessed_by_role_assignment")
    if isinstance(assessor_ref, dict):
        assessor_key = _entity_ref_key(assessor_ref)
        assessor_record = role_state.assignments.get(assessor_key) if assessor_key else None
        if assessor_record is None:
            errors.append("evidence_integrity_assessor_not_found")
        else:
            _, assessor_id, assessor_version = assessor_key
            if role_state.latest_versions.get(("role_assignment", assessor_id)) != assessor_version:
                errors.append("evidence_integrity_assessor_version_not_current")
            assessor_payload = assessor_record.get("legal_payload", {})
            if assessor_payload.get("status") != "active":
                errors.append("evidence_integrity_assessor_inactive")
            operations = set(assessor_payload.get("authority_scope", {}).get("operations", []))
            if "evidence.assess_integrity" not in operations:
                errors.append("evidence_integrity_assessor_scope_denied")

    return errors


VERIFICATION_METHOD_COMPATIBILITY: dict[str, frozenset[str]] = {
    "deterministic": frozenset({"deterministic_rule", "executable_test", "calculation"}),
    "empirical": frozenset({"deterministic_rule", "executable_test", "calculation", "human_check"}),
    "source_based": frozenset({"source_cross_check", "human_check"}),
    "human_judgment": frozenset({"human_check"}),
    "not_currently_verifiable": frozenset({"human_check", "source_cross_check"}),
}


@dataclass(frozen=True)
class VerificationState:
    """Accepted Verification records used by Review, Challenge, and Decision checks."""

    verifications: dict[EntityVersionKey, dict[str, Any]] = field(default_factory=dict)
    latest_versions: dict[EntityKey, int] = field(default_factory=dict)


def validate_verification_semantics(verification: dict[str, Any]) -> list[str]:
    """Return stable local cross-field errors for a Verification payload."""

    errors: list[str] = []
    errors.extend(
        _duplicate_entity_identity_errors(
            verification.get("target_claims", []), "duplicate_verification_target_identity"
        )
    )
    errors.extend(
        _duplicate_entity_identity_errors(
            verification.get("evidence", []), "duplicate_verification_evidence_identity"
        )
    )

    performed_at = verification.get("performed_at")
    assessed_at = verification.get("method", {}).get("validation", {}).get("assessed_at")
    if (
        isinstance(performed_at, str)
        and isinstance(assessed_at, str)
        and _parse_datetime(assessed_at) > _parse_datetime(performed_at)
    ):
        errors.append("verification_method_validated_after_performance")

    if (
        verification.get("conclusion_status") == "conclusive"
        and verification.get("method", {}).get("validation", {}).get("status") != "validated"
    ):
        errors.append("verification_conclusive_method_not_validated")

    return errors


def validate_verification_transition(
    current: dict[str, Any], previous: dict[str, Any] | None
) -> list[str]:
    """Validate immutable Verification run facts and final withdrawal rules."""

    errors = validate_versioned_entity_transition(current, previous)
    if current.get("legal_payload_schema") != VERIFICATION_PAYLOAD_SCHEMA:
        return errors

    payload = current.get("legal_payload", {})
    errors.extend(validate_verification_semantics(payload))
    if current.get("entity", {}).get("entity_version") == 1 and payload.get("status") != "active":
        errors.append("verification_initial_status_not_active")

    if previous is None or previous.get("legal_payload_schema") != VERIFICATION_PAYLOAD_SCHEMA:
        return errors

    previous_payload = previous.get("legal_payload", {})
    for field_name, error_code in (
        ("goal", "verification_goal_changed"),
        ("team_plan", "verification_team_plan_changed"),
        ("task", "verification_task_changed"),
        ("performed_by_role_assignment", "verification_performer_changed"),
        ("target_claims", "verification_targets_changed"),
        ("method", "verification_method_changed"),
        ("evidence", "verification_evidence_changed"),
        ("input_assumptions", "verification_assumptions_changed"),
        ("result", "verification_result_changed"),
        ("conclusion_status", "verification_conclusion_status_changed"),
        ("summary", "verification_summary_changed"),
        ("performed_at", "verification_performed_at_changed"),
    ):
        if payload.get(field_name) != previous_payload.get(field_name):
            errors.append(error_code)

    previous_classification = CONTRIBUTION_CLASSIFICATION_RANK.get(
        previous_payload.get("classification")
    )
    current_classification = CONTRIBUTION_CLASSIFICATION_RANK.get(payload.get("classification"))
    if (
        isinstance(previous_classification, int)
        and isinstance(current_classification, int)
        and current_classification < previous_classification
    ):
        errors.append("verification_classification_downgraded")

    if previous_payload.get("status") == "withdrawn" and payload.get("status") != "withdrawn":
        errors.append("verification_withdrawal_final")

    if previous_payload.get("status") == "active" and payload.get("status") == "withdrawn":
        immutable_on_withdrawal = (
            "goal", "team_plan", "task", "performed_by_role_assignment",
            "target_claims", "method", "evidence", "input_assumptions",
            "result", "conclusion_status", "summary", "not_run_reason",
            "performed_at", "classification", "limitations",
        )
        if any(payload.get(name) != previous_payload.get(name) for name in immutable_on_withdrawal):
            errors.append("verification_withdrawal_changed_content")

    return errors


def _resolve_effective_evidence(
    ref: dict[str, Any], state: EvidenceState
) -> tuple[dict[str, Any] | None, list[str]]:
    """Resolve editorial-none Evidence successors while preserving pinned semantics."""

    errors: list[str] = []
    key = _entity_ref_key(ref)
    record = state.evidence.get(key) if key else None
    if record is None:
        return None, ["verification_evidence_not_found"]

    _, evidence_id, evidence_version = key
    latest = state.latest_versions.get(("evidence", evidence_id))
    effective = record
    if isinstance(latest, int) and latest > evidence_version:
        for version in range(evidence_version + 1, latest + 1):
            successor = state.evidence.get(("evidence", evidence_id, version))
            if successor is None:
                errors.append("verification_evidence_lineage_incomplete")
                return effective, errors
            effective = successor
            if successor.get("change_metadata", {}).get("substantive_effect") != "none":
                errors.append("verification_evidence_version_not_current")
                return effective, errors
    elif latest != evidence_version:
        errors.append("verification_evidence_version_not_current")
    return effective, errors


def validate_verification_against_task_method_claims_and_evidence(
    current: dict[str, Any],
    task_state: TaskState,
    role_state: RoleAssignmentAuthorityState,
    contribution_state: ContributionState,
    evidence_state: EvidenceState,
    *,
    evaluated_at: str,
    agent_card_state: AgentCardState | None = None,
    team_plan_state: TeamPlanState | None = None,
) -> list[str]:
    """Bind a Verification to its task, verifier, method, claims, and Evidence."""

    if current.get("legal_payload_schema") != VERIFICATION_PAYLOAD_SCHEMA:
        return []
    payload = current.get("legal_payload", {})
    errors = validate_verification_semantics(payload)

    task_ref = payload.get("task", {})
    task_key = _entity_ref_key(task_ref)
    task_record = task_state.tasks.get(task_key) if task_key else None
    if task_record is None:
        errors.append("verification_task_not_found")
        return errors
    if task_record.get("legal_payload_schema") != TASK_PAYLOAD_SCHEMA:
        errors.append("verification_task_payload_schema_mismatch")
        return errors

    _, task_id, task_version = task_key
    if task_state.latest_versions.get(("task", task_id)) != task_version:
        errors.append("verification_task_version_not_current")
    task_payload = task_record.get("legal_payload", {})
    if task_payload.get("phase") != "verification":
        errors.append("verification_task_phase_invalid")
    if task_payload.get("status") != "in_progress":
        errors.append("verification_task_not_accepting_outputs")
    if not _same_entity_ref(payload.get("goal"), task_payload.get("goal")):
        errors.append("verification_goal_mismatch")
    if not _same_entity_ref(payload.get("team_plan"), task_payload.get("team_plan")):
        errors.append("verification_team_plan_mismatch")

    performer_ref = payload.get("performed_by_role_assignment", {})
    if not _same_entity_ref(performer_ref, task_payload.get("assigned_role_assignment")):
        errors.append("verification_performer_not_task_assignee")

    when = _parse_datetime(evaluated_at)
    performed_at = payload.get("performed_at")
    if isinstance(performed_at, str) and _parse_datetime(performed_at) > when:
        errors.append("verification_performed_in_future")

    performer_key = _entity_ref_key(performer_ref)
    performer_record = role_state.assignments.get(performer_key) if performer_key else None
    performer_subject = None
    if performer_record is None:
        errors.append("verification_performer_role_assignment_not_found")
    else:
        _, role_id, role_version = performer_key
        if role_state.latest_versions.get(("role_assignment", role_id)) != role_version:
            errors.append("verification_performer_version_not_current")
        if performer_record.get("legal_payload_schema") != ROLE_ASSIGNMENT_PAYLOAD_SCHEMA:
            errors.append("verification_performer_payload_schema_mismatch")
        else:
            role_payload = performer_record.get("legal_payload", {})
            performer_subject = _principal_key(role_payload.get("subject"))
            if role_payload.get("status") != "active":
                errors.append("verification_performer_inactive")
            if role_payload.get("role") != "verifier":
                errors.append("verification_performer_role_invalid")
            valid_from = role_payload.get("valid_from")
            valid_until = role_payload.get("valid_until")
            if isinstance(valid_from, str) and when < _parse_datetime(valid_from):
                errors.append("verification_performer_not_yet_valid")
            if isinstance(valid_until, str) and when >= _parse_datetime(valid_until):
                errors.append("verification_performer_expired")
            operation = (
                "verification.create"
                if current.get("entity", {}).get("entity_version") == 1
                else "verification.next_version"
            )
            operations = set(role_payload.get("authority_scope", {}).get("operations", []))
            if operation not in operations:
                errors.append("verification_performer_scope_denied")
            if agent_card_state is not None:
                eligibility_errors = validate_role_assignment_agent_eligibility(
                    performer_record, agent_card_state
                )
                errors.extend(f"verification_{error}" for error in eligibility_errors)
                refs = _agent_card_basis_refs(role_payload)
                if role_payload.get("subject", {}).get("principal_type") == "agent" and len(refs) == 1:
                    card_key = _entity_ref_key(refs[0])
                    card_record = agent_card_state.cards.get(card_key) if card_key else None
                    if card_record is not None:
                        writes = set(
                            card_record.get("legal_payload", {})
                            .get("permissions", {})
                            .get("data_write", [])
                        )
                        if "verification" not in writes:
                            errors.append("verification_agent_write_permission_missing")

    method = payload.get("method", {})
    validation = method.get("validation", {})
    assessor_ref = validation.get("assessed_by_role_assignment")
    if isinstance(assessor_ref, dict):
        assessor_key = _entity_ref_key(assessor_ref)
        assessor_record = role_state.assignments.get(assessor_key) if assessor_key else None
        if assessor_record is None:
            errors.append("verification_method_assessor_not_found")
        else:
            _, assessor_id, assessor_version = assessor_key
            if role_state.latest_versions.get(("role_assignment", assessor_id)) != assessor_version:
                errors.append("verification_method_assessor_version_not_current")
            if assessor_record.get("legal_payload_schema") != ROLE_ASSIGNMENT_PAYLOAD_SCHEMA:
                errors.append("verification_method_assessor_payload_schema_mismatch")
            assessor_payload = assessor_record.get("legal_payload", {})
            operations = set(assessor_payload.get("authority_scope", {}).get("operations", []))
            if "verification.validate_method" not in operations:
                errors.append("verification_method_assessor_scope_denied")
            if assessor_payload.get("status") != "active":
                errors.append("verification_method_assessor_inactive")
            assessed_at = validation.get("assessed_at")
            if isinstance(assessed_at, str):
                assessed_when = _parse_datetime(assessed_at)
                valid_from = assessor_payload.get("valid_from")
                valid_until = assessor_payload.get("valid_until")
                if isinstance(valid_from, str) and assessed_when < _parse_datetime(valid_from):
                    errors.append("verification_method_assessor_not_yet_valid")
                if isinstance(valid_until, str) and assessed_when >= _parse_datetime(valid_until):
                    errors.append("verification_method_assessor_expired")

    claim_records: list[dict[str, Any]] = []
    claim_keys: set[tuple[str, str, int]] = set()
    claim_identities: set[tuple[str, str]] = set()
    for claim_ref in payload.get("target_claims", []):
        effective, claim_errors = _resolve_effective_claim_contribution(
            claim_ref, contribution_state
        )
        errors.extend(
            error.replace("evidence_target_claim", "verification_target_claim")
            for error in claim_errors
        )
        if effective is None:
            continue
        if effective.get("legal_payload_schema") != CONTRIBUTION_PAYLOAD_SCHEMA:
            errors.append("verification_target_payload_schema_mismatch")
            continue
        claim = effective.get("legal_payload", {})
        if claim.get("contribution_type") != "claim":
            errors.append("verification_target_not_claim")
            continue
        if claim.get("status") != "active":
            errors.append("verification_target_claim_inactive")
        if not _same_entity_ref(payload.get("goal"), claim.get("goal")):
            errors.append("verification_target_goal_mismatch")
        if not _same_entity_ref(payload.get("team_plan"), claim.get("team_plan")):
            errors.append("verification_target_team_plan_mismatch")
        verifiability = claim.get("content", {}).get("verifiability")
        allowed = VERIFICATION_METHOD_COMPATIBILITY.get(verifiability, frozenset())
        if method.get("type") not in allowed:
            errors.append("verification_method_incompatible_with_claim")
        if verifiability == "not_currently_verifiable" and payload.get("result") == "passed":
            errors.append("verification_unverifiable_claim_cannot_pass")
        author_ref = claim.get("authored_by_role_assignment", {})
        author_key = _entity_ref_key(author_ref)
        author_record = role_state.assignments.get(author_key) if author_key else None
        if author_record is not None and performer_subject is not None:
            author_subject = _principal_key(author_record.get("legal_payload", {}).get("subject"))
            enforce_independence = True
            if team_plan_state is not None:
                plan_key = _entity_ref_key(payload.get("team_plan", {}))
                plan_record = team_plan_state.plans.get(plan_key) if plan_key else None
                if plan_record is None:
                    errors.append("verification_team_plan_not_found_for_independence")
                    rules = []
                    enforce_independence = True
                else:
                    rules = plan_record.get("legal_payload", {}).get("independence_rules", [])
                    enforce_independence = any(
                    rule.get("type") == "author_separation"
                    and rule.get("reviewing_role") == "verifier"
                    and "contribution" in rule.get("target_entity_types", [])
                        and "claim" in rule.get("target_contribution_types", [])
                        for rule in rules
                    )
            if enforce_independence and author_subject == performer_subject:
                errors.append("verification_performer_not_independent_from_claim_author")
        key = _entity_ref_key(claim_ref)
        if key:
            claim_keys.add(key)
            claim_identities.add((key[0], key[1]))
        claim_records.append(effective)

    if method.get("type") == "human_check" and performer_record is not None:
        if performer_record.get("legal_payload", {}).get("subject", {}).get("principal_type") != "human":
            errors.append("verification_human_check_requires_human")

    evidence_records: list[dict[str, Any]] = []
    covered_claims: set[tuple[str, str]] = set()
    supported_claims: set[tuple[str, str]] = set()
    refuted_claims: set[tuple[str, str]] = set()
    evidence_classification_max = -1
    all_integrity_verified = True
    for evidence_ref in payload.get("evidence", []):
        effective, evidence_errors = _resolve_effective_evidence(evidence_ref, evidence_state)
        errors.extend(evidence_errors)
        if effective is None:
            continue
        if effective.get("legal_payload_schema") != EVIDENCE_PAYLOAD_SCHEMA:
            errors.append("verification_evidence_payload_schema_mismatch")
            continue
        evidence_payload = effective.get("legal_payload", {})
        if evidence_payload.get("status") != "active":
            errors.append("verification_evidence_inactive")
        if not _same_entity_ref(payload.get("goal"), evidence_payload.get("goal")):
            errors.append("verification_evidence_goal_mismatch")
        if not _same_entity_ref(payload.get("team_plan"), evidence_payload.get("team_plan")):
            errors.append("verification_evidence_team_plan_mismatch")
        evidence_target_identities: set[tuple[str, str]] = set()
        for evidence_target_ref in evidence_payload.get("targets", []):
            _, target_errors = _resolve_effective_claim_contribution(
                evidence_target_ref, contribution_state
            )
            errors.extend(
                error.replace("evidence_target_claim", "verification_evidence_target_claim")
                for error in target_errors
            )
            target_key = _entity_ref_key(evidence_target_ref)
            if target_key:
                evidence_target_identities.add((target_key[0], target_key[1]))
        overlap = evidence_target_identities & claim_identities
        if not overlap:
            errors.append("verification_evidence_not_relevant_to_targets")
        covered_claims.update(overlap)
        if evidence_payload.get("relation") == "supports":
            supported_claims.update(overlap)
        if evidence_payload.get("relation") == "refutes":
            refuted_claims.update(overlap)
        rank = CONTRIBUTION_CLASSIFICATION_RANK.get(evidence_payload.get("classification"))
        if isinstance(rank, int):
            evidence_classification_max = max(evidence_classification_max, rank)
        if evidence_payload.get("integrity", {}).get("status") != "verified":
            all_integrity_verified = False
        evidence_records.append(effective)

    if payload.get("result") != "not_run" and claim_identities - covered_claims:
        errors.append("verification_target_not_covered_by_evidence")
    if payload.get("result") == "passed" and claim_identities - supported_claims:
        errors.append("verification_passed_without_supporting_evidence")
    if payload.get("result") == "failed" and not refuted_claims:
        errors.append("verification_failed_without_refuting_evidence")
    verification_rank = CONTRIBUTION_CLASSIFICATION_RANK.get(payload.get("classification"))
    if (
        isinstance(verification_rank, int)
        and evidence_classification_max >= 0
        and verification_rank < evidence_classification_max
    ):
        errors.append("verification_classification_below_evidence")
    if payload.get("conclusion_status") == "conclusive" and not all_integrity_verified:
        errors.append("verification_conclusive_evidence_not_verified")

    return errors


CHALLENGE_CLOSED_STATUSES = frozenset({"resolved", "rejected", "disclosed_unresolved"})
CHALLENGE_SEVERITY_RANK = {
    "informational": 0,
    "minor": 1,
    "major": 2,
    "critical": 3,
}
CHALLENGE_ALLOWED_TRANSITIONS = {
    "open": frozenset({"acknowledged", "rejected", "escalated"}),
    "acknowledged": frozenset({
        "evidence_requested", "resolved", "rejected", "escalated", "disclosed_unresolved"
    }),
    "evidence_requested": frozenset({
        "acknowledged", "resolved", "rejected", "escalated", "disclosed_unresolved"
    }),
    "resolved": frozenset({"reopened"}),
    "rejected": frozenset({"reopened"}),
    "disclosed_unresolved": frozenset({"reopened"}),
    "reopened": frozenset({"acknowledged", "evidence_requested", "escalated"}),
    "escalated": frozenset({"acknowledged", "evidence_requested", "resolved", "rejected", "disclosed_unresolved"}),
}


@dataclass(frozen=True)
class ChallengeTargetState:
    """Pinned entity records that may be targeted by Challenge records."""

    records: dict[EntityVersionKey, dict[str, Any]] = field(default_factory=dict)
    latest_versions: dict[EntityKey, int] = field(default_factory=dict)


@dataclass(frozen=True)
class ChallengeState:
    """Accepted Challenge records used by Review, Decision, and FinalResult checks."""

    challenges: dict[EntityVersionKey, dict[str, Any]] = field(default_factory=dict)
    latest_versions: dict[EntityKey, int] = field(default_factory=dict)


def validate_challenge_semantics(challenge: dict[str, Any]) -> list[str]:
    """Return stable local cross-field errors for a Challenge payload."""

    errors: list[str] = []
    action_codes: set[str] = set()
    for item in challenge.get("required_resolution", []):
        code = item.get("action_code") if isinstance(item, dict) else None
        if isinstance(code, str):
            if code in action_codes:
                errors.append("duplicate_challenge_resolution_action")
            action_codes.add(code)

    raised_at = challenge.get("raised_at")
    transitioned_at = challenge.get("last_transition_at")
    if isinstance(raised_at, str) and isinstance(transitioned_at, str):
        if _parse_datetime(transitioned_at) < _parse_datetime(raised_at):
            errors.append("challenge_transition_before_raise")

    if challenge.get("status") == "open":
        if not _same_entity_ref(
            challenge.get("raised_by_role_assignment"),
            challenge.get("last_transition_by_role_assignment"),
        ):
            errors.append("challenge_open_transition_actor_mismatch")
        if raised_at != transitioned_at:
            errors.append("challenge_open_transition_time_mismatch")

    resolution = challenge.get("resolution")
    if isinstance(resolution, dict):
        if not _same_entity_ref(
            resolution.get("resolved_by_role_assignment"),
            challenge.get("last_transition_by_role_assignment"),
        ):
            errors.append("challenge_resolution_actor_mismatch")
        if resolution.get("resolved_at") != transitioned_at:
            errors.append("challenge_resolution_time_mismatch")

    reopening = challenge.get("reopening")
    if isinstance(reopening, dict):
        if not _same_entity_ref(
            reopening.get("reopened_by_role_assignment"),
            challenge.get("last_transition_by_role_assignment"),
        ):
            errors.append("challenge_reopening_actor_mismatch")
        if reopening.get("reopened_at") != transitioned_at:
            errors.append("challenge_reopening_time_mismatch")

    target_type = challenge.get("target", {}).get("entity_type")
    category = challenge.get("category")
    expected_types = {
        "claim_correctness": {"contribution"},
        "evidence_integrity": {"evidence"},
        "verification_method": {"verification"},
        "verification_conclusion": {"verification"},
        "context_use": {"context_grant", "contribution"},
    }.get(category)
    if expected_types is not None and target_type not in expected_types:
        errors.append("challenge_category_target_type_mismatch")

    return errors


def validate_challenge_transition(
    current: dict[str, Any], previous: dict[str, Any] | None
) -> list[str]:
    """Validate Challenge lineage, lifecycle, immutable provenance, and reopening."""

    errors = validate_versioned_entity_transition(current, previous)
    if current.get("legal_payload_schema") != CHALLENGE_PAYLOAD_SCHEMA:
        return errors

    payload = current.get("legal_payload", {})
    errors.extend(validate_challenge_semantics(payload))
    if current.get("entity", {}).get("entity_version") == 1 and payload.get("status") != "open":
        errors.append("challenge_initial_status_not_open")

    if previous is None or previous.get("legal_payload_schema") != CHALLENGE_PAYLOAD_SCHEMA:
        return errors

    previous_payload = previous.get("legal_payload", {})
    for field_name, error_code in (
        ("goal", "challenge_goal_changed"),
        ("team_plan", "challenge_team_plan_changed"),
        ("task", "challenge_task_changed"),
        ("raised_by_role_assignment", "challenge_raiser_changed"),
        ("raised_at", "challenge_raised_at_changed"),
        ("target", "challenge_target_changed"),
        ("category", "challenge_category_changed"),
        ("reason", "challenge_reason_changed"),
        ("required_resolution", "challenge_required_resolution_changed"),
        ("resolution_policy", "challenge_resolution_policy_changed"),
        ("fulfills_output_id", "challenge_output_binding_changed"),
    ):
        if payload.get(field_name) != previous_payload.get(field_name):
            errors.append(error_code)

    previous_status = previous_payload.get("status")
    current_status = payload.get("status")
    if current_status != previous_status:
        allowed = CHALLENGE_ALLOWED_TRANSITIONS.get(previous_status, frozenset())
        if current_status not in allowed:
            errors.append("challenge_status_transition_invalid")

    previous_rank = CONTRIBUTION_CLASSIFICATION_RANK.get(previous_payload.get("classification"))
    current_rank = CONTRIBUTION_CLASSIFICATION_RANK.get(payload.get("classification"))
    if isinstance(previous_rank, int) and isinstance(current_rank, int) and current_rank < previous_rank:
        errors.append("challenge_classification_downgraded")

    return errors


def _resolve_challenge_target(
    target_ref: dict[str, Any], state: ChallengeTargetState
) -> tuple[dict[str, Any] | None, list[str]]:
    errors: list[str] = []
    key = _entity_ref_key(target_ref)
    if key is None:
        return None, errors
    record = state.records.get(key)
    if record is None:
        return None, ["challenge_target_not_found"]
    entity_type, entity_id, version = key
    latest = state.latest_versions.get((entity_type, entity_id))
    if latest != version:
        if latest is None or latest < version:
            errors.append("challenge_target_version_not_current")
        else:
            later_records = [
                state.records.get((entity_type, entity_id, candidate))
                for candidate in range(version + 1, latest + 1)
            ]
            editorial_only = all(
                item is not None
                and item.get("change_metadata", {}).get("change_reason") == "editorial_correction"
                and item.get("change_metadata", {}).get("substantive_effect") == "none"
                for item in later_records
            )
            if not editorial_only:
                errors.append("challenge_target_version_not_current")
    return record, errors


def _challenge_target_author_ref(record: dict[str, Any]) -> dict[str, Any] | None:
    schema = record.get("legal_payload_schema")
    payload = record.get("legal_payload", {})
    field_name = {
        CONTRIBUTION_PAYLOAD_SCHEMA: "authored_by_role_assignment",
        EVIDENCE_PAYLOAD_SCHEMA: "registered_by_role_assignment",
        VERIFICATION_PAYLOAD_SCHEMA: "performed_by_role_assignment",
        TASK_PAYLOAD_SCHEMA: "assigned_role_assignment",
    }.get(schema)
    value = payload.get(field_name) if field_name else None
    return value if isinstance(value, dict) else None


def validate_challenge_against_task_raiser_target_and_resolution(
    current: dict[str, Any],
    task_state: TaskState,
    role_state: RoleAssignmentAuthorityState,
    target_state: ChallengeTargetState,
    *,
    evaluated_at: str,
    agent_card_state: AgentCardState | None = None,
    approval_state: ApprovalState | None = None,
) -> list[str]:
    """Bind a Challenge to its task, transition actor, target, and closure policy."""

    if current.get("legal_payload_schema") != CHALLENGE_PAYLOAD_SCHEMA:
        return []
    payload = current.get("legal_payload", {})
    errors = validate_challenge_semantics(payload)

    task_ref = payload.get("task", {})
    task_key = _entity_ref_key(task_ref)
    task_record = task_state.tasks.get(task_key) if task_key else None
    if task_record is None:
        errors.append("challenge_task_not_found")
        return errors
    if task_record.get("legal_payload_schema") != TASK_PAYLOAD_SCHEMA:
        errors.append("challenge_task_payload_schema_mismatch")
        return errors
    task_payload = task_record.get("legal_payload", {})
    if not _same_entity_ref(payload.get("goal"), task_payload.get("goal")):
        errors.append("challenge_goal_mismatch")
    if not _same_entity_ref(payload.get("team_plan"), task_payload.get("team_plan")):
        errors.append("challenge_team_plan_mismatch")

    is_initial = current.get("entity", {}).get("entity_version") == 1
    if is_initial:
        if task_payload.get("status") != "in_progress":
            errors.append("challenge_task_not_in_progress")
        if task_payload.get("phase") not in {"review", "verification", "revision"}:
            errors.append("challenge_task_phase_not_allowed")
        if not _same_entity_ref(
            payload.get("raised_by_role_assignment"),
            task_payload.get("assigned_role_assignment"),
        ):
            errors.append("challenge_raiser_not_task_assignee")
        output_id = payload.get("fulfills_output_id")
        if output_id is not None:
            requirements = {
                item.get("output_id"): item
                for item in task_payload.get("required_outputs", [])
                if isinstance(item, dict)
            }
            requirement = requirements.get(output_id)
            if requirement is None:
                errors.append("challenge_output_requirement_not_found")
            elif requirement.get("entity_type") != "challenge":
                errors.append("challenge_output_requirement_type_mismatch")

    actor_ref = payload.get("last_transition_by_role_assignment", {})
    actor_key = _entity_ref_key(actor_ref)
    actor_record = role_state.assignments.get(actor_key) if actor_key else None
    actor_role = None
    actor_subject = None
    if actor_record is None:
        errors.append("challenge_transition_actor_not_found")
    else:
        _, actor_id, actor_version = actor_key
        if role_state.latest_versions.get(("role_assignment", actor_id)) != actor_version:
            errors.append("challenge_transition_actor_version_not_current")
        if actor_record.get("legal_payload_schema") != ROLE_ASSIGNMENT_PAYLOAD_SCHEMA:
            errors.append("challenge_transition_actor_payload_schema_mismatch")
        else:
            actor_payload = actor_record.get("legal_payload", {})
            actor_role = actor_payload.get("role")
            actor_subject = _principal_key(actor_payload.get("subject"))
            if actor_payload.get("status") != "active":
                errors.append("challenge_transition_actor_inactive")
            when = _parse_datetime(evaluated_at)
            valid_from = actor_payload.get("valid_from")
            valid_until = actor_payload.get("valid_until")
            if isinstance(valid_from, str) and when < _parse_datetime(valid_from):
                errors.append("challenge_transition_actor_not_yet_valid")
            if isinstance(valid_until, str) and when >= _parse_datetime(valid_until):
                errors.append("challenge_transition_actor_expired")
            operation = "challenge.create" if is_initial else "challenge.next_version"
            allowed_operations = set(actor_payload.get("authority_scope", {}).get("operations", []))
            if operation not in allowed_operations:
                errors.append("challenge_transition_actor_scope_denied")
            if agent_card_state is not None:
                errors.extend(
                    f"challenge_{error}"
                    for error in validate_role_assignment_agent_eligibility(
                        actor_record, agent_card_state
                    )
                )
                refs = _agent_card_basis_refs(actor_payload)
                if actor_payload.get("subject", {}).get("principal_type") == "agent" and len(refs) == 1:
                    card_key = _entity_ref_key(refs[0])
                    card_record = agent_card_state.cards.get(card_key) if card_key else None
                    if card_record is not None:
                        writes = set(
                            card_record.get("legal_payload", {})
                            .get("permissions", {})
                            .get("data_write", [])
                        )
                        if "challenge" not in writes:
                            errors.append("challenge_agent_write_permission_missing")

    target_record, target_errors = _resolve_challenge_target(payload.get("target", {}), target_state)
    errors.extend(target_errors)
    target_author_subject = None
    if target_record is not None:
        target_payload = target_record.get("legal_payload", {})
        if isinstance(target_payload.get("goal"), dict) and not _same_entity_ref(
            payload.get("goal"), target_payload.get("goal")
        ):
            errors.append("challenge_target_goal_mismatch")
        if isinstance(target_payload.get("team_plan"), dict) and not _same_entity_ref(
            payload.get("team_plan"), target_payload.get("team_plan")
        ):
            errors.append("challenge_target_team_plan_mismatch")
        target_status = target_payload.get("status")
        if is_initial and target_status in {"withdrawn", "revoked", "retired", "cancelled"}:
            errors.append("challenge_target_inactive")
        target_rank = CONTRIBUTION_CLASSIFICATION_RANK.get(target_payload.get("classification"))
        challenge_rank = CONTRIBUTION_CLASSIFICATION_RANK.get(payload.get("classification"))
        if isinstance(target_rank, int) and isinstance(challenge_rank, int) and challenge_rank < target_rank:
            errors.append("challenge_classification_below_target")
        if payload.get("category") == "claim_correctness":
            if target_record.get("legal_payload_schema") != CONTRIBUTION_PAYLOAD_SCHEMA:
                errors.append("challenge_claim_target_payload_schema_mismatch")
            elif target_payload.get("contribution_type") != "claim":
                errors.append("challenge_claim_target_not_claim")
        author_ref = _challenge_target_author_ref(target_record)
        author_key = _entity_ref_key(author_ref) if author_ref else None
        author_record = role_state.assignments.get(author_key) if author_key else None
        if author_ref is not None and author_record is None:
            errors.append("challenge_target_author_role_assignment_not_found")
        if author_record is not None:
            target_author_subject = _principal_key(
                author_record.get("legal_payload", {}).get("subject")
            )

    status = payload.get("status")
    raised_ref = payload.get("raised_by_role_assignment", {})
    is_challenger = _same_entity_ref(actor_ref, raised_ref)
    allowed_roles = set(payload.get("resolution_policy", {}).get("allowed_roles", []))
    closing = status in CHALLENGE_CLOSED_STATUSES
    if closing and actor_subject is not None and actor_subject == target_author_subject:
        errors.append("challenge_target_author_cannot_close_alone")
    if status == "resolved":
        challenger_allowed = (
            is_challenger
            and payload.get("resolution_policy", {}).get("challenger_may_accept_correction") is True
        )
        if not challenger_allowed and actor_role not in allowed_roles:
            errors.append("challenge_resolver_not_authorized")
    elif status == "rejected":
        if actor_role not in allowed_roles:
            errors.append("challenge_rejector_not_authorized")
        if is_challenger:
            errors.append("challenge_rejection_not_independent")
    elif status == "disclosed_unresolved":
        if actor_role != "human_owner":
            errors.append("challenge_risk_acceptance_requires_human_owner")
        approval_ref = payload.get("resolution", {}).get("risk_acceptance_approval", {})
        approval_key = _entity_ref_key(approval_ref)
        approval_record = (
            approval_state.approvals.get(approval_key)
            if approval_state is not None and approval_key is not None
            else None
        )
        if approval_record is None:
            errors.append("challenge_risk_acceptance_approval_not_found")
        else:
            _, approval_id, approval_version = approval_key
            if approval_state.latest_versions.get(("approval", approval_id)) != approval_version:
                errors.append("challenge_risk_acceptance_approval_not_current")
            approval_payload = approval_record.get("legal_payload", {})
            if approval_record.get("legal_payload_schema") != APPROVAL_PAYLOAD_SCHEMA:
                errors.append("challenge_risk_acceptance_approval_schema_mismatch")
            if approval_payload.get("approval_kind") != "risk_acceptance":
                errors.append("challenge_risk_acceptance_wrong_approval_kind")
            if approval_payload.get("decision") != "approved" or approval_payload.get("status") != "active":
                errors.append("challenge_risk_acceptance_approval_inactive")
            if not any(_same_entity_ref(payload.get("target"), ref) for ref in approval_payload.get("targets", [])):
                # Risk acceptance targets the Challenge itself, not the Challenge target.
                current_ref = current.get("entity", {})
                if not any(_same_identity(current_ref, ref) for ref in approval_payload.get("targets", [])):
                    errors.append("challenge_risk_acceptance_approval_target_mismatch")
            if not _same_entity_ref(payload.get("goal"), approval_payload.get("goal")):
                errors.append("challenge_risk_acceptance_approval_goal_mismatch")
            if not _same_entity_ref(payload.get("team_plan"), approval_payload.get("team_plan")):
                errors.append("challenge_risk_acceptance_approval_team_plan_mismatch")
    elif status == "reopened":
        if not is_challenger and actor_role not in allowed_roles:
            errors.append("challenge_reopener_not_authorized")

    transition_at = payload.get("last_transition_at")
    if isinstance(transition_at, str) and _parse_datetime(transition_at) > _parse_datetime(evaluated_at):
        errors.append("challenge_transition_time_in_future")

    return errors


def challenge_reopening_requires_reassessment(
    current: dict[str, Any], previous: dict[str, Any] | None
) -> bool:
    """Return whether a reopened critical Challenge must trigger dependency propagation."""

    if (
        current.get("legal_payload_schema") != CHALLENGE_PAYLOAD_SCHEMA
        or previous is None
        or previous.get("legal_payload_schema") != CHALLENGE_PAYLOAD_SCHEMA
    ):
        return False
    return (
        previous.get("legal_payload", {}).get("status") in CHALLENGE_CLOSED_STATUSES
        and current.get("legal_payload", {}).get("status") == "reopened"
        and current.get("legal_payload", {}).get("severity") == "critical"
    )



def validate_task_outputs_against_challenges(
    task_record: dict[str, Any], state: ChallengeState
) -> list[str]:
    """Validate Challenge outputs selected by a completed review or verification Task."""

    if task_record.get("legal_payload_schema") != TASK_PAYLOAD_SCHEMA:
        return []
    task_payload = task_record.get("legal_payload", {})
    if task_payload.get("status") != "completed":
        return []

    errors: list[str] = []
    task_entity = task_record.get("entity", {})
    for produced in task_payload.get("produced_outputs", []):
        entity_ref = produced.get("entity", {})
        if entity_ref.get("entity_type") != "challenge":
            continue
        key = _entity_ref_key(entity_ref)
        record = state.challenges.get(key) if key else None
        if record is None:
            errors.append("task_challenge_output_not_found")
            continue
        _, challenge_id, challenge_version = key
        latest = state.latest_versions.get(("challenge", challenge_id))
        effective = record
        if isinstance(latest, int) and latest > challenge_version:
            for version in range(challenge_version + 1, latest + 1):
                successor = state.challenges.get(("challenge", challenge_id, version))
                if successor is None:
                    errors.append("task_challenge_output_lineage_incomplete")
                    effective = record
                    break
                effective = successor
                if successor.get("change_metadata", {}).get("substantive_effect") != "none":
                    # The output remains the original raised Challenge. Later lifecycle
                    # transitions do not invalidate the fact that the Task produced it.
                    break
        elif latest is None or latest < challenge_version:
            errors.append("task_challenge_output_version_not_current")

        if effective.get("legal_payload_schema") != CHALLENGE_PAYLOAD_SCHEMA:
            errors.append("task_challenge_output_payload_schema_mismatch")
            continue
        challenge = record.get("legal_payload", {})
        if not _same_identity(challenge.get("task", {}), task_entity):
            errors.append("task_challenge_output_task_mismatch")
        if challenge.get("fulfills_output_id") != produced.get("output_id"):
            errors.append("task_challenge_output_binding_mismatch")

    return errors


REVIEW_REQUIRED_CRITERIA = frozenset({
    "goal_alignment",
    "requirement_coverage",
    "internal_consistency",
    "risk_and_limitations",
    "evidence_sufficiency",
    "verification_sufficiency",
})
REVIEW_DEFICIENT_OUTCOMES = frozenset({"partially_satisfied", "not_satisfied"})
REVIEW_UNRESOLVED_CHALLENGE_STATUSES = frozenset({
    "open", "acknowledged", "evidence_requested", "escalated", "reopened",
    "disclosed_unresolved",
})


@dataclass(frozen=True)
class ReviewTargetState:
    """Pinned records that may appear in a Review scope."""

    records: dict[EntityVersionKey, dict[str, Any]] = field(default_factory=dict)
    latest_versions: dict[EntityKey, int] = field(default_factory=dict)


@dataclass(frozen=True)
class ReviewState:
    """Accepted Review records used by Task, Decision, and FinalResult checks."""

    reviews: dict[EntityVersionKey, dict[str, Any]] = field(default_factory=dict)
    latest_versions: dict[EntityKey, int] = field(default_factory=dict)


def validate_review_semantics(review: dict[str, Any]) -> list[str]:
    """Return stable local cross-field errors for a holistic Review payload."""

    errors: list[str] = []
    scope = review.get("scope", {})
    errors.extend(
        _duplicate_entity_identity_errors(
            scope.get("targets", []), "duplicate_review_target_identity"
        )
    )
    errors.extend(
        _duplicate_entity_identity_errors(
            review.get("evidence", []), "duplicate_review_evidence_identity"
        )
    )
    errors.extend(
        _duplicate_entity_identity_errors(
            review.get("verifications", []), "duplicate_review_verification_identity"
        )
    )
    errors.extend(
        _duplicate_entity_identity_errors(
            review.get("challenges", []), "duplicate_review_challenge_identity"
        )
    )

    seen: set[str] = set()
    for assessment in review.get("criteria_assessments", []):
        criterion = assessment.get("criterion") if isinstance(assessment, dict) else None
        if isinstance(criterion, str):
            if criterion in seen:
                errors.append("duplicate_review_criterion")
            seen.add(criterion)
        outcome = assessment.get("outcome") if isinstance(assessment, dict) else None
        severity = assessment.get("severity") if isinstance(assessment, dict) else None
        if outcome in {"satisfied", "not_applicable"} and severity != "informational":
            errors.append("review_satisfied_criterion_severity_invalid")

    if seen != REVIEW_REQUIRED_CRITERIA:
        errors.append("review_required_criterion_missing")

    result = review.get("result")
    assessments = review.get("criteria_assessments", [])
    deficient = [
        item for item in assessments
        if isinstance(item, dict) and item.get("outcome") in REVIEW_DEFICIENT_OUTCOMES
    ]
    critical_failures = [
        item for item in assessments
        if isinstance(item, dict)
        and item.get("outcome") == "not_satisfied"
        and item.get("severity") == "critical"
    ]
    if result == "approved" and deficient:
        errors.append("review_approved_with_unsatisfied_criterion")
    if result == "revision_required" and not deficient:
        errors.append("review_revision_without_deficiency")
    if result == "rejected" and not critical_failures:
        errors.append("review_rejected_without_critical_failure")
    if result == "escalation_required":
        critical_issues = [
            item for item in assessments
            if isinstance(item, dict)
            and item.get("outcome") in REVIEW_DEFICIENT_OUTCOMES
            and item.get("severity") == "critical"
        ]
        if not critical_issues:
            errors.append("review_escalation_without_critical_issue")

    return errors


def validate_review_transition(
    current: dict[str, Any], previous: dict[str, Any] | None
) -> list[str]:
    """Validate immutable Review-run facts and final withdrawal semantics."""

    errors = validate_versioned_entity_transition(current, previous)
    if current.get("legal_payload_schema") != REVIEW_PAYLOAD_SCHEMA:
        return errors

    payload = current.get("legal_payload", {})
    errors.extend(validate_review_semantics(payload))
    if current.get("entity", {}).get("entity_version") == 1 and payload.get("status") != "active":
        errors.append("review_initial_status_not_active")

    if previous is None or previous.get("legal_payload_schema") != REVIEW_PAYLOAD_SCHEMA:
        return errors

    previous_payload = previous.get("legal_payload", {})
    for field_name, error_code in (
        ("goal", "review_goal_changed"),
        ("team_plan", "review_team_plan_changed"),
        ("task", "review_task_changed"),
        ("reviewed_by_role_assignment", "review_reviewer_changed"),
        ("reviewed_at", "review_time_changed"),
        ("scope", "review_scope_changed"),
        ("criteria_assessments", "review_assessments_changed"),
        ("evidence", "review_evidence_changed"),
        ("verifications", "review_verifications_changed"),
        ("challenges", "review_challenges_changed"),
        ("result", "review_result_changed"),
        ("summary", "review_summary_changed"),
        ("required_actions", "review_required_actions_changed"),
        ("escalation", "review_escalation_changed"),
        ("fulfills_output_id", "review_output_binding_changed"),
    ):
        if payload.get(field_name) != previous_payload.get(field_name):
            errors.append(error_code)

    previous_rank = CONTRIBUTION_CLASSIFICATION_RANK.get(previous_payload.get("classification"))
    current_rank = CONTRIBUTION_CLASSIFICATION_RANK.get(payload.get("classification"))
    if (
        isinstance(previous_rank, int)
        and isinstance(current_rank, int)
        and current_rank < previous_rank
    ):
        errors.append("review_classification_downgraded")

    if previous_payload.get("status") == "withdrawn" and payload.get("status") != "withdrawn":
        errors.append("review_withdrawal_final")

    if previous_payload.get("status") == "active" and payload.get("status") == "withdrawn":
        immutable_on_withdrawal = (
            "goal", "team_plan", "task", "reviewed_by_role_assignment", "reviewed_at",
            "scope", "criteria_assessments", "evidence", "verifications", "challenges",
            "result", "summary", "required_actions", "escalation", "classification",
            "limitations", "fulfills_output_id",
        )
        if any(payload.get(name) != previous_payload.get(name) for name in immutable_on_withdrawal):
            errors.append("review_withdrawal_changed_content")

    return errors


def _resolve_review_record(
    ref: dict[str, Any],
    records: dict[EntityVersionKey, dict[str, Any]],
    latest_versions: dict[EntityKey, int],
    *,
    prefix: str,
) -> tuple[dict[str, Any] | None, list[str]]:
    """Resolve editorial-none successors while keeping a Review pinned."""

    key = _entity_ref_key(ref)
    record = records.get(key) if key else None
    if record is None:
        return None, [f"review_{prefix}_not_found"]
    _, entity_id, entity_version = key
    latest = latest_versions.get((key[0], entity_id))
    effective = record
    errors: list[str] = []
    if isinstance(latest, int) and latest > entity_version:
        for version in range(entity_version + 1, latest + 1):
            successor = records.get((key[0], entity_id, version))
            if successor is None:
                errors.append(f"review_{prefix}_lineage_incomplete")
                return effective, errors
            effective = successor
            if successor.get("change_metadata", {}).get("substantive_effect") != "none":
                errors.append(f"review_{prefix}_version_not_current")
                return effective, errors
    elif latest != entity_version:
        errors.append(f"review_{prefix}_version_not_current")
    return effective, errors


def _review_source_classification_rank(record: dict[str, Any] | None) -> int | None:
    if record is None:
        return None
    return CONTRIBUTION_CLASSIFICATION_RANK.get(
        record.get("legal_payload", {}).get("classification")
    )


def validate_review_against_task_reviewer_and_sources(
    current: dict[str, Any],
    task_state: TaskState,
    role_state: RoleAssignmentAuthorityState,
    target_state: ReviewTargetState,
    evidence_state: EvidenceState,
    verification_state: VerificationState,
    challenge_state: ChallengeState,
    *,
    evaluated_at: str,
    agent_card_state: AgentCardState | None = None,
) -> list[str]:
    """Bind a Review to its review Task, independent reviewer, and pinned sources."""

    if current.get("legal_payload_schema") != REVIEW_PAYLOAD_SCHEMA:
        return []
    payload = current.get("legal_payload", {})
    errors = validate_review_semantics(payload)

    task_ref = payload.get("task", {})
    task_key = _entity_ref_key(task_ref)
    task_record = task_state.tasks.get(task_key) if task_key else None
    if task_record is None:
        errors.append("review_task_not_found")
        return errors
    if task_record.get("legal_payload_schema") != TASK_PAYLOAD_SCHEMA:
        errors.append("review_task_payload_schema_mismatch")
        return errors
    _, task_id, task_version = task_key
    if task_state.latest_versions.get(("task", task_id)) != task_version:
        errors.append("review_task_version_not_current")
    task_payload = task_record.get("legal_payload", {})
    if task_payload.get("phase") != "review":
        errors.append("review_task_phase_invalid")
    if task_payload.get("status") != "in_progress":
        errors.append("review_task_not_accepting_outputs")
    if not _same_entity_ref(payload.get("goal"), task_payload.get("goal")):
        errors.append("review_goal_mismatch")
    if not _same_entity_ref(payload.get("team_plan"), task_payload.get("team_plan")):
        errors.append("review_team_plan_mismatch")

    reviewer_ref = payload.get("reviewed_by_role_assignment", {})
    if not _same_entity_ref(reviewer_ref, task_payload.get("assigned_role_assignment")):
        errors.append("review_reviewer_not_task_assignee")
    reviewer_key = _entity_ref_key(reviewer_ref)
    reviewer_record = role_state.assignments.get(reviewer_key) if reviewer_key else None
    reviewer_subject = None
    if reviewer_record is None:
        errors.append("review_reviewer_role_assignment_not_found")
    else:
        _, reviewer_id, reviewer_version = reviewer_key
        if role_state.latest_versions.get(("role_assignment", reviewer_id)) != reviewer_version:
            errors.append("review_reviewer_role_assignment_version_not_current")
        if reviewer_record.get("legal_payload_schema") != ROLE_ASSIGNMENT_PAYLOAD_SCHEMA:
            errors.append("review_reviewer_payload_schema_mismatch")
        else:
            reviewer_payload = reviewer_record.get("legal_payload", {})
            reviewer_subject = _principal_key(reviewer_payload.get("subject"))
            if reviewer_payload.get("role") != "reviewer":
                errors.append("review_reviewer_role_invalid")
            if reviewer_payload.get("status") != "active":
                errors.append("review_reviewer_inactive")
            when = _parse_datetime(evaluated_at)
            valid_from = reviewer_payload.get("valid_from")
            valid_until = reviewer_payload.get("valid_until")
            if isinstance(valid_from, str) and when < _parse_datetime(valid_from):
                errors.append("review_reviewer_not_yet_valid")
            if isinstance(valid_until, str) and when >= _parse_datetime(valid_until):
                errors.append("review_reviewer_expired")
            operation = (
                "review.create"
                if current.get("entity", {}).get("entity_version") == 1
                else "review.next_version"
            )
            operations = set(reviewer_payload.get("authority_scope", {}).get("operations", []))
            if operation not in operations:
                errors.append("review_reviewer_scope_denied")
            if agent_card_state is not None:
                errors.extend(
                    f"review_{error}"
                    for error in validate_role_assignment_agent_eligibility(
                        reviewer_record, agent_card_state
                    )
                )
                refs = _agent_card_basis_refs(reviewer_payload)
                if reviewer_payload.get("subject", {}).get("principal_type") == "agent" and len(refs) == 1:
                    card_key = _entity_ref_key(refs[0])
                    card_record = agent_card_state.cards.get(card_key) if card_key else None
                    if card_record is not None:
                        writes = set(
                            card_record.get("legal_payload", {})
                            .get("permissions", {})
                            .get("data_write", [])
                        )
                        if "review" not in writes:
                            errors.append("review_agent_write_permission_missing")

    reviewed_at = payload.get("reviewed_at")
    if isinstance(reviewed_at, str) and _parse_datetime(reviewed_at) > _parse_datetime(evaluated_at):
        errors.append("review_time_in_future")

    review_rank = CONTRIBUTION_CLASSIFICATION_RANK.get(payload.get("classification"))
    source_max_rank = -1
    claim_refs_requiring_verification: list[dict[str, Any]] = []

    for ref in payload.get("scope", {}).get("targets", []):
        record, source_errors = _resolve_review_record(
            ref, target_state.records, target_state.latest_versions, prefix="target"
        )
        errors.extend(source_errors)
        if record is None:
            continue
        source_payload = record.get("legal_payload", {})
        if isinstance(source_payload.get("goal"), dict) and not _same_entity_ref(
            payload.get("goal"), source_payload.get("goal")
        ):
            errors.append("review_target_goal_mismatch")
        if isinstance(source_payload.get("team_plan"), dict) and not _same_entity_ref(
            payload.get("team_plan"), source_payload.get("team_plan")
        ):
            errors.append("review_target_team_plan_mismatch")
        if source_payload.get("status") in {"withdrawn", "revoked", "retired", "cancelled"}:
            errors.append("review_target_inactive")
        rank = _review_source_classification_rank(record)
        if isinstance(rank, int):
            source_max_rank = max(source_max_rank, rank)

        if record.get("legal_payload_schema") == CONTRIBUTION_PAYLOAD_SCHEMA:
            author_ref = source_payload.get("authored_by_role_assignment", {})
            author_key = _entity_ref_key(author_ref)
            author_record = role_state.assignments.get(author_key) if author_key else None
            if author_record is None:
                errors.append("review_target_author_role_assignment_not_found")
            else:
                author_subject = _principal_key(
                    author_record.get("legal_payload", {}).get("subject")
                )
                if reviewer_subject is not None and author_subject == reviewer_subject:
                    errors.append("review_not_independent_from_target_author")
            if (
                source_payload.get("contribution_type") == "claim"
                and source_payload.get("content", {}).get("verifiability")
                in {"deterministic", "empirical", "source_based"}
            ):
                claim_refs_requiring_verification.append(ref)

    resolved_verifications: list[dict[str, Any]] = []
    for ref in payload.get("evidence", []):
        record, source_errors = _resolve_review_record(
            ref, evidence_state.evidence, evidence_state.latest_versions, prefix="evidence"
        )
        errors.extend(source_errors)
        if record is None:
            continue
        if record.get("legal_payload_schema") != EVIDENCE_PAYLOAD_SCHEMA:
            errors.append("review_evidence_payload_schema_mismatch")
            continue
        source_payload = record.get("legal_payload", {})
        if source_payload.get("status") != "active":
            errors.append("review_evidence_inactive")
        if not _same_entity_ref(payload.get("goal"), source_payload.get("goal")):
            errors.append("review_evidence_goal_mismatch")
        if not _same_entity_ref(payload.get("team_plan"), source_payload.get("team_plan")):
            errors.append("review_evidence_team_plan_mismatch")
        rank = _review_source_classification_rank(record)
        if isinstance(rank, int):
            source_max_rank = max(source_max_rank, rank)

    for ref in payload.get("verifications", []):
        record, source_errors = _resolve_review_record(
            ref,
            verification_state.verifications,
            verification_state.latest_versions,
            prefix="verification",
        )
        errors.extend(source_errors)
        if record is None:
            continue
        if record.get("legal_payload_schema") != VERIFICATION_PAYLOAD_SCHEMA:
            errors.append("review_verification_payload_schema_mismatch")
            continue
        source_payload = record.get("legal_payload", {})
        if source_payload.get("status") != "active":
            errors.append("review_verification_inactive")
        if not _same_entity_ref(payload.get("goal"), source_payload.get("goal")):
            errors.append("review_verification_goal_mismatch")
        if not _same_entity_ref(payload.get("team_plan"), source_payload.get("team_plan")):
            errors.append("review_verification_team_plan_mismatch")
        rank = _review_source_classification_rank(record)
        if isinstance(rank, int):
            source_max_rank = max(source_max_rank, rank)
        resolved_verifications.append(record)

    unresolved_critical = False
    for ref in payload.get("challenges", []):
        record, source_errors = _resolve_review_record(
            ref, challenge_state.challenges, challenge_state.latest_versions, prefix="challenge"
        )
        errors.extend(source_errors)
        if record is None:
            continue
        if record.get("legal_payload_schema") != CHALLENGE_PAYLOAD_SCHEMA:
            errors.append("review_challenge_payload_schema_mismatch")
            continue
        source_payload = record.get("legal_payload", {})
        if not _same_entity_ref(payload.get("goal"), source_payload.get("goal")):
            errors.append("review_challenge_goal_mismatch")
        if not _same_entity_ref(payload.get("team_plan"), source_payload.get("team_plan")):
            errors.append("review_challenge_team_plan_mismatch")
        rank = _review_source_classification_rank(record)
        if isinstance(rank, int):
            source_max_rank = max(source_max_rank, rank)
        if (
            source_payload.get("severity") == "critical"
            and source_payload.get("status") in REVIEW_UNRESOLVED_CHALLENGE_STATUSES
        ):
            unresolved_critical = True

    if (
        isinstance(review_rank, int)
        and source_max_rank >= 0
        and review_rank < source_max_rank
    ):
        errors.append("review_classification_below_sources")

    if payload.get("result") == "approved":
        if unresolved_critical:
            errors.append("review_approved_with_unresolved_critical_challenge")
        for record in resolved_verifications:
            verification = record.get("legal_payload", {})
            if verification.get("result") != "passed":
                errors.append("review_approved_with_nonpassing_verification")
                break
        verified_claim_refs = {
            (ref.get("entity_type"), ref.get("entity_id"), ref.get("entity_version"))
            for record in resolved_verifications
            if record.get("legal_payload", {}).get("status") == "active"
            and record.get("legal_payload", {}).get("result") == "passed"
            for ref in record.get("legal_payload", {}).get("target_claims", [])
        }
        for ref in claim_refs_requiring_verification:
            key = (ref.get("entity_type"), ref.get("entity_id"), ref.get("entity_version"))
            if key not in verified_claim_refs:
                errors.append("review_approved_claim_without_passing_verification")
                break

    output_id = payload.get("fulfills_output_id")
    if isinstance(output_id, str):
        requirements = {
            item.get("output_id"): item
            for item in task_payload.get("required_outputs", [])
            if isinstance(item, dict)
        }
        requirement = requirements.get(output_id)
        if requirement is None:
            errors.append("review_output_requirement_not_found")
        elif requirement.get("entity_type") != "review":
            errors.append("review_output_requirement_type_mismatch")

    return errors


def validate_task_outputs_against_reviews(
    task_record: dict[str, Any], state: ReviewState
) -> list[str]:
    """Validate Review outputs selected by a completed review Task."""

    if task_record.get("legal_payload_schema") != TASK_PAYLOAD_SCHEMA:
        return []
    task_payload = task_record.get("legal_payload", {})
    if task_payload.get("status") != "completed":
        return []

    errors: list[str] = []
    task_entity = task_record.get("entity", {})
    for produced in task_payload.get("produced_outputs", []):
        entity_ref = produced.get("entity", {})
        if entity_ref.get("entity_type") != "review":
            continue
        key = _entity_ref_key(entity_ref)
        record = state.reviews.get(key) if key else None
        if record is None:
            errors.append("task_review_output_not_found")
            continue
        _, review_id, review_version = key
        latest = state.latest_versions.get(("review", review_id))
        if latest is None or latest < review_version:
            errors.append("task_review_output_version_not_current")
        if record.get("legal_payload_schema") != REVIEW_PAYLOAD_SCHEMA:
            errors.append("task_review_output_payload_schema_mismatch")
            continue
        review = record.get("legal_payload", {})
        if review.get("status") != "active":
            errors.append("task_review_output_inactive")
        if not _same_identity(review.get("task", {}), task_entity):
            errors.append("task_review_output_task_mismatch")
        if review.get("fulfills_output_id") != produced.get("output_id"):
            errors.append("task_review_output_binding_mismatch")

    return errors


APPROVAL_SEVERITY_RANK = {
    "informational": 0,
    "minor": 1,
    "major": 2,
    "critical": 3,
}
APPROVAL_UNRESOLVED_CHALLENGE_STATUSES = frozenset(
    {"open", "acknowledged", "evidence_requested", "escalated", "reopened", "disclosed_unresolved"}
)


def _approval_authorization_target_ref(payload: dict[str, Any]) -> dict[str, Any] | None:
    authorization = payload.get("authorization", {})
    condition = authorization.get("target_condition", {})
    if condition.get("mode") == "next_version":
        return condition.get("target")
    return None


def validate_approval_semantics(approval: dict[str, Any]) -> list[str]:
    """Validate local cross-field rules for an Approval legal payload."""

    errors: list[str] = []
    decided_at = approval.get("decided_at")
    effective_from = approval.get("effective_from")
    expires_at = approval.get("expires_at")
    if isinstance(decided_at, str) and isinstance(effective_from, str):
        if _parse_datetime(effective_from) < _parse_datetime(decided_at):
            errors.append("approval_effective_before_decision")
    if isinstance(effective_from, str) and isinstance(expires_at, str):
        if _parse_datetime(expires_at) <= _parse_datetime(effective_from):
            errors.append("approval_invalid_validity_interval")

    kind = approval.get("approval_kind")
    if kind == "operation_authorization":
        authorization = approval.get("authorization", {})
        operation = authorization.get("operation")
        if operation in APPROVAL_GOVERNANCE_DELEGATION_FORBIDDEN:
            errors.append("approval_governance_delegation_forbidden")
        targets = approval.get("targets", [])
        condition = authorization.get("target_condition", {})
        if len(targets) == 1:
            target = targets[0]
            if condition.get("mode") == "next_version":
                if not _same_entity_ref(target, condition.get("target")):
                    errors.append("approval_authorization_target_mismatch")
                expected_operation = f"{target.get('entity_type')}.next_version"
            else:
                identity = condition.get("target_identity", {})
                if not _same_identity(target, identity):
                    errors.append("approval_authorization_target_mismatch")
                expected_operation = f"{identity.get('entity_type')}.create"
            if isinstance(operation, str) and operation != expected_operation:
                errors.append("approval_authorization_operation_mismatch")
    elif kind == "risk_acceptance":
        risk = approval.get("risk_acceptance", {})
        accepted = risk.get("accepted_severity")
        if accepted not in APPROVAL_SEVERITY_RANK:
            errors.append("approval_risk_severity_invalid")
    elif kind == "editorial_correction":
        editorial = approval.get("editorial_correction", {})
        check = editorial.get("classification_check", {})
        if check.get("entity_type") not in {"review", "verification"}:
            errors.append("approval_editorial_check_type_invalid")

    if approval.get("decision") == "denied" and approval.get("status") == "revoked":
        errors.append("approval_denial_cannot_be_revoked")
    return errors


def validate_approval_transition(
    current: dict[str, Any], previous: dict[str, Any] | None
) -> list[str]:
    """Keep the approval decision immutable; only an approved record may be revoked."""

    errors = validate_versioned_entity_transition(current, previous)
    if current.get("legal_payload_schema") != APPROVAL_PAYLOAD_SCHEMA:
        return errors
    current_payload = current.get("legal_payload", {})
    errors.extend(validate_approval_semantics(current_payload))
    if previous is None or previous.get("legal_payload_schema") != APPROVAL_PAYLOAD_SCHEMA:
        return errors
    previous_payload = previous.get("legal_payload", {})
    immutable = (
        "goal", "team_plan", "task", "approval_kind", "decision",
        "approved_by_role_assignment", "decided_at", "effective_from", "expires_at",
        "targets", "granted_to_role_assignment", "authorization", "risk_acceptance",
        "editorial_correction", "basis_refs", "conditions", "classification",
        "limitations", "fulfills_output_id",
    )
    for field_name in immutable:
        if current_payload.get(field_name) != previous_payload.get(field_name):
            errors.append(f"approval_{field_name}_changed")
    if previous_payload.get("decision") == "denied":
        errors.append("approval_denial_final")
    if previous_payload.get("status") == "revoked" and current_payload.get("status") != "revoked":
        errors.append("approval_revocation_final")
    return errors


@dataclass(frozen=True)
class ApprovalTargetState:
    records: dict[EntityVersionKey, dict[str, Any]] = field(default_factory=dict)
    latest_versions: dict[EntityKey, int] = field(default_factory=dict)


@dataclass(frozen=True)
class ApprovalState:
    approvals: dict[EntityVersionKey, dict[str, Any]] = field(default_factory=dict)
    latest_versions: dict[EntityKey, int] = field(default_factory=dict)
    consumed: frozenset[EntityVersionKey] = field(default_factory=frozenset)


def _resolve_approval_target(
    ref: dict[str, Any], state: ApprovalTargetState
) -> tuple[dict[str, Any] | None, list[str]]:
    errors: list[str] = []
    key = _entity_ref_key(ref)
    record = state.records.get(key) if key else None
    if record is None:
        return None, ["approval_target_not_found"]
    entity_type, entity_id, version = key
    latest = state.latest_versions.get((entity_type, entity_id))
    if isinstance(latest, int) and latest > version:
        effective = record
        for candidate_version in range(version + 1, latest + 1):
            successor = state.records.get((entity_type, entity_id, candidate_version))
            if successor is None:
                errors.append("approval_target_lineage_incomplete")
                break
            effective = successor
            if successor.get("change_metadata", {}).get("substantive_effect") != "none":
                errors.append("approval_target_version_not_current")
                break
        record = effective
    return record, errors


def validate_approval_against_task_issuer_and_targets(
    current: dict[str, Any],
    task_state: TaskState,
    role_state: RoleAssignmentAuthorityState,
    target_state: ApprovalTargetState,
    *,
    evaluated_at: str,
    goal_state: GoalContractState | None = None,
    challenge_state: ChallengeState | None = None,
    agent_card_state: AgentCardState | None = None,
) -> list[str]:
    """Validate Approval issuer, task binding, targets, and risk policy."""

    if current.get("legal_payload_schema") != APPROVAL_PAYLOAD_SCHEMA:
        return []
    payload = current.get("legal_payload", {})
    errors: list[str] = []

    task_key = _entity_ref_key(payload.get("task", {}))
    task_record = task_state.tasks.get(task_key) if task_key else None
    if task_record is None:
        return ["approval_task_not_found"]
    task_payload = task_record.get("legal_payload", {})
    if task_record.get("legal_payload_schema") != TASK_PAYLOAD_SCHEMA:
        errors.append("approval_task_payload_schema_mismatch")
    if task_payload.get("phase") != "approval":
        errors.append("approval_task_phase_not_approval")
    if task_payload.get("status") != "in_progress":
        errors.append("approval_task_not_in_progress")
    if not _same_entity_ref(payload.get("goal"), task_payload.get("goal")):
        errors.append("approval_goal_mismatch")
    if not _same_entity_ref(payload.get("team_plan"), task_payload.get("team_plan")):
        errors.append("approval_team_plan_mismatch")
    if not _same_entity_ref(
        payload.get("approved_by_role_assignment"),
        task_payload.get("assigned_role_assignment"),
    ):
        errors.append("approval_issuer_not_task_assignee")

    issuer_ref = payload.get("approved_by_role_assignment", {})
    issuer_key = _entity_ref_key(issuer_ref)
    issuer = role_state.assignments.get(issuer_key) if issuer_key else None
    issuer_role = None
    if issuer is None:
        errors.append("approval_issuer_not_found")
    else:
        _, issuer_id, issuer_version = issuer_key
        if role_state.latest_versions.get(("role_assignment", issuer_id)) != issuer_version:
            errors.append("approval_issuer_version_not_current")
        issuer_payload = issuer.get("legal_payload", {})
        issuer_role = issuer_payload.get("role")
        if issuer_payload.get("status") != "active":
            errors.append("approval_issuer_inactive")
        when = _parse_datetime(evaluated_at)
        if isinstance(issuer_payload.get("valid_from"), str) and when < _parse_datetime(issuer_payload["valid_from"]):
            errors.append("approval_issuer_not_yet_valid")
        if isinstance(issuer_payload.get("valid_until"), str) and when >= _parse_datetime(issuer_payload["valid_until"]):
            errors.append("approval_issuer_expired")
        operation = "approval.create" if current.get("entity", {}).get("entity_version") == 1 else "approval.next_version"
        if operation not in set(issuer_payload.get("authority_scope", {}).get("operations", [])):
            errors.append("approval_issuer_scope_denied")
        kind = payload.get("approval_kind")
        capabilities = set(issuer_payload.get("authority_scope", {}).get("capabilities", []))
        if kind in {"operation_authorization", "decision_approval"} and issuer_role not in {"decision_authority", "human_owner"}:
            errors.append("approval_kind_requires_decision_authority_or_owner")
        if kind == "risk_acceptance":
            if issuer_role != "human_owner":
                errors.append("approval_risk_acceptance_requires_human_owner")
            if "accept_risk" not in capabilities:
                errors.append("approval_risk_acceptance_capability_missing")
        if kind == "editorial_correction":
            if issuer_role not in EDITORIAL_CLASSIFICATION_ROLES:
                errors.append("approval_editorial_role_not_allowed")
            if "classify_editorial_correction" not in capabilities:
                errors.append("approval_editorial_capability_missing")
        if agent_card_state is not None:
            errors.extend(f"approval_{e}" for e in validate_role_assignment_agent_eligibility(issuer, agent_card_state))
            refs = _agent_card_basis_refs(issuer_payload)
            if issuer_payload.get("subject", {}).get("principal_type") == "agent" and len(refs) == 1:
                card_key = _entity_ref_key(refs[0]); card = agent_card_state.cards.get(card_key) if card_key else None
                if card is not None and "approval" not in set(card.get("legal_payload", {}).get("permissions", {}).get("data_write", [])):
                    errors.append("approval_agent_write_permission_missing")

    source_max_rank = -1
    for ref in payload.get("targets", []):
        record, target_errors = _resolve_approval_target(ref, target_state)
        errors.extend(target_errors)
        if record is None:
            continue
        target_payload = record.get("legal_payload", {})
        if isinstance(target_payload.get("goal"), dict) and not _same_entity_ref(payload.get("goal"), target_payload.get("goal")):
            errors.append("approval_target_goal_mismatch")
        if isinstance(target_payload.get("team_plan"), dict) and not _same_entity_ref(payload.get("team_plan"), target_payload.get("team_plan")):
            errors.append("approval_target_team_plan_mismatch")
        rank = CONTRIBUTION_CLASSIFICATION_RANK.get(target_payload.get("classification"))
        if isinstance(rank, int): source_max_rank = max(source_max_rank, rank)

    approval_rank = CONTRIBUTION_CLASSIFICATION_RANK.get(payload.get("classification"))
    if isinstance(approval_rank, int) and source_max_rank >= 0 and approval_rank < source_max_rank:
        errors.append("approval_classification_below_targets")

    if payload.get("approval_kind") == "operation_authorization":
        grantee_key = _entity_ref_key(payload.get("granted_to_role_assignment", {}))
        grantee = role_state.assignments.get(grantee_key) if grantee_key else None
        if grantee is None:
            errors.append("approval_grantee_role_assignment_not_found")
        else:
            _, gid, gv = grantee_key
            if role_state.latest_versions.get(("role_assignment", gid)) != gv:
                errors.append("approval_grantee_version_not_current")
            if grantee.get("legal_payload", {}).get("status") != "active":
                errors.append("approval_grantee_inactive")

    if payload.get("approval_kind") == "risk_acceptance":
        if goal_state is None or challenge_state is None:
            errors.append("approval_risk_context_required")
        else:
            goal_ref = payload.get("goal", {}); goal_key = _entity_ref_key(goal_ref)
            goal = goal_state.contracts.get(goal_key) if goal_key else None
            if goal is None:
                errors.append("approval_goal_not_found")
            else:
                policy = goal.get("legal_payload", {}).get("risk_policy", {}).get("risk_acceptance", {})
                if policy.get("allowed") is not True:
                    errors.append("approval_risk_acceptance_not_allowed")
                max_rank = APPROVAL_SEVERITY_RANK.get(policy.get("max_unresolved_challenge_severity"), -1)
                accepted_rank = APPROVAL_SEVERITY_RANK.get(payload.get("risk_acceptance", {}).get("accepted_severity"), -1)
                if accepted_rank > max_rank:
                    errors.append("approval_risk_severity_exceeds_goal_policy")
            for ref in payload.get("targets", []):
                key = _entity_ref_key(ref); challenge = challenge_state.challenges.get(key) if key else None
                if challenge is None:
                    errors.append("approval_risk_challenge_not_found"); continue
                cp = challenge.get("legal_payload", {})
                if cp.get("status") not in APPROVAL_UNRESOLVED_CHALLENGE_STATUSES:
                    errors.append("approval_risk_challenge_not_unresolved")
                accepted_rank = APPROVAL_SEVERITY_RANK.get(payload.get("risk_acceptance", {}).get("accepted_severity"), -1)
                challenge_rank = APPROVAL_SEVERITY_RANK.get(cp.get("severity"), -1)
                if challenge_rank > accepted_rank:
                    errors.append("approval_risk_challenge_exceeds_accepted_severity")

    output_id = payload.get("fulfills_output_id")
    if isinstance(output_id, str):
        reqs = {i.get("output_id"): i for i in task_payload.get("required_outputs", []) if isinstance(i, dict)}
        req = reqs.get(output_id)
        if req is None: errors.append("approval_output_requirement_not_found")
        elif req.get("entity_type") != "approval": errors.append("approval_output_requirement_type_mismatch")

    if isinstance(payload.get("decided_at"), str) and _parse_datetime(payload["decided_at"]) > _parse_datetime(evaluated_at):
        errors.append("approval_decision_time_in_future")
    return errors


def validate_envelope_approval_authority(
    envelope: dict[str, Any],
    approval_state: ApprovalState,
    role_state: RoleAssignmentAuthorityState,
    *,
    acceptance_time: str | None = None,
) -> list[str]:
    """Validate one-time exact operation authority backed by an Approval."""

    ref = envelope.get("authorized_by", {})
    if ref.get("entity_type") != "approval":
        return []
    key = _entity_ref_key(ref)
    record = approval_state.approvals.get(key) if key else None
    if record is None:
        return ["approval_authority_not_found"]
    _, approval_id, approval_version = key
    errors: list[str] = []
    if approval_state.latest_versions.get(("approval", approval_id)) != approval_version:
        errors.append("approval_authority_version_not_current")
    if key in approval_state.consumed:
        errors.append("approval_authority_already_consumed")
    if record.get("legal_payload_schema") != APPROVAL_PAYLOAD_SCHEMA:
        return errors + ["approval_authority_payload_schema_mismatch"]
    payload = record.get("legal_payload", {})
    if payload.get("approval_kind") != "operation_authorization":
        errors.append("approval_not_operation_authorization")
    if payload.get("decision") != "approved":
        errors.append("approval_authority_denied")
    if payload.get("status") != "active":
        errors.append("approval_authority_inactive")
    when_text = envelope.get("recorded_at") if envelope.get("kind") == "event" else acceptance_time
    if not isinstance(when_text, str):
        errors.append("approval_authority_acceptance_time_required")
        return errors
    when = _parse_datetime(when_text)
    if isinstance(payload.get("effective_from"), str) and when < _parse_datetime(payload["effective_from"]):
        errors.append("approval_authority_not_yet_valid")
    if isinstance(payload.get("expires_at"), str) and when >= _parse_datetime(payload["expires_at"]):
        errors.append("approval_authority_expired")

    grantee_key = _entity_ref_key(payload.get("granted_to_role_assignment", {}))
    grantee = role_state.assignments.get(grantee_key) if grantee_key else None
    if grantee is None:
        errors.append("approval_authority_grantee_not_found")
    else:
        gp = grantee.get("legal_payload", {})
        if _principal_key(gp.get("subject")) != get_effective_principal(envelope):
            errors.append("approval_authority_principal_mismatch")
        if gp.get("status") != "active":
            errors.append("approval_authority_grantee_inactive")

    required = get_required_authority_operation(envelope)
    authorization = payload.get("authorization", {})
    if required != authorization.get("operation"):
        errors.append("approval_authority_operation_denied")
    condition = authorization.get("target_condition", {})
    precondition = envelope.get("precondition", {})
    if condition.get("mode") != precondition.get("mode"):
        errors.append("approval_authority_target_mismatch")
    elif condition.get("mode") == "create":
        if not _same_identity(condition.get("target_identity", {}), precondition.get("target_identity", {})):
            errors.append("approval_authority_target_mismatch")
    elif not _same_entity_ref(condition.get("target"), precondition.get("target")):
        errors.append("approval_authority_target_mismatch")
    return errors


def validate_task_outputs_against_approvals(
    task_record: dict[str, Any], state: ApprovalState
) -> list[str]:
    if task_record.get("legal_payload_schema") != TASK_PAYLOAD_SCHEMA:
        return []
    payload = task_record.get("legal_payload", {})
    if payload.get("status") != "completed":
        return []
    errors: list[str] = []
    for produced in payload.get("produced_outputs", []):
        ref = produced.get("entity", {})
        if ref.get("entity_type") != "approval":
            continue
        key = _entity_ref_key(ref); record = state.approvals.get(key) if key else None
        if record is None:
            errors.append("task_approval_output_not_found"); continue
        if record.get("legal_payload_schema") != APPROVAL_PAYLOAD_SCHEMA:
            errors.append("task_approval_output_payload_schema_mismatch"); continue
        if not _same_identity(record.get("legal_payload", {}).get("task", {}), task_record.get("entity", {})):
            errors.append("task_approval_output_task_mismatch")
        if record.get("legal_payload", {}).get("fulfills_output_id") != produced.get("output_id"):
            errors.append("task_approval_output_binding_mismatch")
    return errors



def validate_versioned_entity_editorial_approval(
    current: dict[str, Any], approval_state: ApprovalState
) -> list[str]:
    """Validate the Approval referenced by a non-impacting editorial correction."""

    metadata = current.get("change_metadata", {})
    if metadata.get("substantive_effect") != "none":
        return []
    errors: list[str] = []
    ref = metadata.get("classification_approval", {})
    key = _entity_ref_key(ref)
    record = approval_state.approvals.get(key) if key else None
    if record is None:
        return ["editorial_approval_not_found"]
    _, approval_id, approval_version = key
    if approval_state.latest_versions.get(("approval", approval_id)) != approval_version:
        errors.append("editorial_approval_version_not_current")
    if record.get("legal_payload_schema") != APPROVAL_PAYLOAD_SCHEMA:
        return errors + ["editorial_approval_payload_schema_mismatch"]
    payload = record.get("legal_payload", {})
    if payload.get("approval_kind") != "editorial_correction":
        errors.append("editorial_approval_wrong_kind")
    if payload.get("decision") != "approved" or payload.get("status") != "active":
        errors.append("editorial_approval_inactive")
    previous = current.get("previous_version", {})
    if not any(_same_entity_ref(previous, target) for target in payload.get("targets", [])):
        errors.append("editorial_approval_target_mismatch")
    editorial = payload.get("editorial_correction", {})
    if editorial.get("diff_artifact") != metadata.get("diff_artifact"):
        errors.append("editorial_approval_diff_mismatch")
    if not _same_entity_ref(
        editorial.get("classification_check"), metadata.get("classification_check")
    ):
        errors.append("editorial_approval_check_mismatch")
    return errors


# ---------------------------------------------------------------------------
# Decision
# ---------------------------------------------------------------------------

DECISION_TERMINAL_STATUSES = frozenset({"rejected", "superseded", "revoked"})
DECISION_UNRESOLVED_CHALLENGE_STATUSES = frozenset({
    "open", "acknowledged", "evidence_requested", "escalated", "reopened",
    "disclosed_unresolved",
})
DECISION_ALLOWED_TRANSITIONS = {
    "proposed": frozenset({"under_review", "needs_evidence", "rejected"}),
    "under_review": frozenset({"needs_evidence", "accepted", "rejected"}),
    "needs_evidence": frozenset({"under_review", "accepted", "rejected"}),
    "accepted": frozenset({"reassessment_required", "superseded", "revoked"}),
    "reassessment_required": frozenset({"accepted", "superseded", "revoked"}),
    "rejected": frozenset(),
    "superseded": frozenset(),
    "revoked": frozenset(),
}


@dataclass(frozen=True)
class DecisionState:
    """Accepted Decision records and latest versions."""

    decisions: dict[EntityVersionKey, dict[str, Any]] = field(default_factory=dict)
    latest_versions: dict[EntityKey, int] = field(default_factory=dict)


def validate_decision_semantics(decision: dict[str, Any]) -> list[str]:
    """Return stable local errors for a Decision legal payload."""

    errors: list[str] = []
    options = decision.get("options_considered", [])
    option_ids = [item.get("option_id") for item in options if isinstance(item, dict)]
    if len(option_ids) != len(set(option_ids)):
        errors.append("duplicate_decision_option_id")
    selected = decision.get("selected_option")
    if isinstance(selected, str) and selected not in set(option_ids):
        errors.append("decision_selected_option_unknown")

    for field_name, code in (
        ("based_on_contributions", "duplicate_decision_contribution_identity"),
        ("supporting_evidence", "duplicate_decision_evidence_identity"),
        ("verifications", "duplicate_decision_verification_identity"),
        ("reviews", "duplicate_decision_review_identity"),
        ("addressed_challenges", "duplicate_decision_addressed_challenge_identity"),
        ("unresolved_challenges", "duplicate_decision_unresolved_challenge_identity"),
        ("approvals", "duplicate_decision_approval_identity"),
    ):
        errors.extend(_duplicate_entity_identity_errors(decision.get(field_name, []), code))

    addressed = {
        (ref.get("entity_type"), ref.get("entity_id"))
        for ref in decision.get("addressed_challenges", []) if isinstance(ref, dict)
    }
    unresolved = {
        (ref.get("entity_type"), ref.get("entity_id"))
        for ref in decision.get("unresolved_challenges", []) if isinstance(ref, dict)
    }
    if addressed & unresolved:
        errors.append("decision_challenge_both_addressed_and_unresolved")

    seen_dependencies: set[EntityKey] = set()
    for dependency in decision.get("depends_on_decisions", []):
        if not isinstance(dependency, dict):
            continue
        ref = dependency.get("decision", {})
        key = (ref.get("entity_type"), ref.get("entity_id"))
        if key in seen_dependencies:
            errors.append("duplicate_decision_dependency_identity")
        seen_dependencies.add(key)
        if key == ("decision", decision.get("_entity_id")):
            errors.append("decision_self_dependency")
        if dependency.get("critical") is True and all(
            dependency.get(name) == "notify"
            for name in ("on_revoked", "on_superseded", "on_reassessment")
        ):
            errors.append("decision_critical_dependency_notify_only")

    confidence = decision.get("confidence", {})
    calibrated = confidence.get("calibrated")
    has_numeric = "numeric_value" in confidence
    if has_numeric and "scale_owner" not in confidence:
        errors.append("decision_numeric_confidence_scale_owner_required")
    if calibrated is True and "calibration_reference" not in confidence:
        errors.append("decision_calibrated_confidence_reference_required")
    if calibrated is False and "calibration_reference" in confidence:
        errors.append("decision_uncalibrated_confidence_has_reference")

    proposed_at = decision.get("proposed_at")
    transition_at = decision.get("last_transition_at")
    if isinstance(proposed_at, str) and isinstance(transition_at, str):
        if _parse_datetime(transition_at) < _parse_datetime(proposed_at):
            errors.append("decision_transition_before_proposal")
    decided_at = decision.get("decided_at")
    if isinstance(decided_at, str) and isinstance(transition_at, str):
        if _parse_datetime(decided_at) != _parse_datetime(transition_at):
            errors.append("decision_decided_time_mismatch")
    if decision.get("status") in {"accepted", "rejected", "reassessment_required", "superseded", "revoked"}:
        if not _same_entity_ref(
            decision.get("decided_by_role_assignment"),
            decision.get("last_transition_by_role_assignment"),
        ):
            errors.append("decision_decider_transition_authority_mismatch")
    return errors


def validate_decision_transition(
    current: dict[str, Any], previous: dict[str, Any] | None
) -> list[str]:
    """Validate Decision lifecycle and immutable decision identity facts."""

    errors = validate_versioned_entity_transition(current, previous)
    if current.get("legal_payload_schema") != DECISION_PAYLOAD_SCHEMA:
        return errors
    payload = current.get("legal_payload", {})
    errors.extend(validate_decision_semantics(payload))
    if current.get("entity", {}).get("entity_version") == 1 and payload.get("status") != "proposed":
        errors.append("decision_initial_status_not_proposed")
    if previous is None or previous.get("legal_payload_schema") != DECISION_PAYLOAD_SCHEMA:
        return errors
    previous_payload = previous.get("legal_payload", {})
    for field_name, code in (
        ("goal", "decision_goal_changed"),
        ("team_plan", "decision_team_plan_changed"),
        ("task", "decision_task_changed"),
        ("subject", "decision_subject_changed"),
        ("proposed_by_role_assignment", "decision_proposer_changed"),
        ("proposed_at", "decision_proposal_time_changed"),
        ("options_considered", "decision_options_changed"),
        ("fulfills_output_id", "decision_output_binding_changed"),
    ):
        if payload.get(field_name) != previous_payload.get(field_name):
            errors.append(code)

    old_status = previous_payload.get("status")
    new_status = payload.get("status")
    if new_status not in DECISION_ALLOWED_TRANSITIONS.get(old_status, frozenset()):
        errors.append("decision_status_transition_invalid")
    old_time = previous_payload.get("last_transition_at")
    new_time = payload.get("last_transition_at")
    if isinstance(old_time, str) and isinstance(new_time, str):
        if _parse_datetime(new_time) <= _parse_datetime(old_time):
            errors.append("decision_transition_time_not_increasing")
    if old_status in {"accepted", "reassessment_required"}:
        if payload.get("selected_option") != previous_payload.get("selected_option"):
            errors.append("decision_accepted_selection_changed")
    if old_status in DECISION_TERMINAL_STATUSES:
        errors.append("decision_terminal_status_final")

    old_rank = CONTRIBUTION_CLASSIFICATION_RANK.get(previous_payload.get("classification"))
    new_rank = CONTRIBUTION_CLASSIFICATION_RANK.get(payload.get("classification"))
    if isinstance(old_rank, int) and isinstance(new_rank, int) and new_rank < old_rank:
        errors.append("decision_classification_downgraded")
    return errors


def _resolve_decision_record(
    ref: dict[str, Any], records: dict[EntityVersionKey, dict[str, Any]],
    latest_versions: dict[EntityKey, int], *, prefix: str,
) -> tuple[dict[str, Any] | None, list[str]]:
    key = _entity_ref_key(ref)
    record = records.get(key) if key else None
    if record is None:
        return None, [f"decision_{prefix}_not_found"]
    entity_type, entity_id, entity_version = key
    latest = latest_versions.get((entity_type, entity_id))
    effective = record
    errors: list[str] = []
    if isinstance(latest, int) and latest > entity_version:
        for version in range(entity_version + 1, latest + 1):
            successor = records.get((entity_type, entity_id, version))
            if successor is None:
                errors.append(f"decision_{prefix}_lineage_incomplete")
                return effective, errors
            effective = successor
            if successor.get("change_metadata", {}).get("substantive_effect") != "none":
                errors.append(f"decision_{prefix}_version_not_current")
                return effective, errors
    elif latest != entity_version:
        errors.append(f"decision_{prefix}_version_not_current")
    return effective, errors


def _decision_record_rank(record: dict[str, Any] | None) -> int | None:
    if record is None:
        return None
    return CONTRIBUTION_CLASSIFICATION_RANK.get(record.get("legal_payload", {}).get("classification"))


def validate_decision_against_task_authority_and_sources(
    current: dict[str, Any],
    task_state: TaskState,
    role_state: RoleAssignmentAuthorityState,
    goal_state: GoalContractState,
    team_plan_state: TeamPlanState,
    contribution_state: ContributionState,
    evidence_state: EvidenceState,
    verification_state: VerificationState,
    review_state: ReviewState,
    challenge_state: ChallengeState,
    approval_state: ApprovalState,
    decision_state: DecisionState,
    *,
    evaluated_at: str,
    agent_card_state: AgentCardState | None = None,
) -> list[str]:
    """Bind a Decision to its task, authority, policy, sources, and dependencies."""

    if current.get("legal_payload_schema") != DECISION_PAYLOAD_SCHEMA:
        return []
    payload = current.get("legal_payload", {})
    payload_for_local = dict(payload)
    payload_for_local["_entity_id"] = current.get("entity", {}).get("entity_id")
    errors = validate_decision_semantics(payload_for_local)
    when = _parse_datetime(evaluated_at)

    task_key = _entity_ref_key(payload.get("task", {}))
    task = task_state.tasks.get(task_key) if task_key else None
    if task is None:
        errors.append("decision_task_not_found")
        return errors
    if task.get("legal_payload_schema") != TASK_PAYLOAD_SCHEMA:
        return errors + ["decision_task_payload_schema_mismatch"]
    _, task_id, task_version = task_key
    if task_state.latest_versions.get(("task", task_id)) != task_version:
        errors.append("decision_task_version_not_current")
    tp = task.get("legal_payload", {})
    if tp.get("phase") != "approval":
        errors.append("decision_task_phase_invalid")
    if tp.get("status") != "in_progress":
        errors.append("decision_task_not_accepting_outputs")
    if not _same_entity_ref(payload.get("goal"), tp.get("goal")):
        errors.append("decision_goal_mismatch")
    if not _same_entity_ref(payload.get("team_plan"), tp.get("team_plan")):
        errors.append("decision_team_plan_mismatch")

    transition_ref = payload.get("last_transition_by_role_assignment", {})
    if not _same_entity_ref(transition_ref, tp.get("assigned_role_assignment")):
        errors.append("decision_authority_not_task_assignee")
    role_key = _entity_ref_key(transition_ref)
    role = role_state.assignments.get(role_key) if role_key else None
    if role is None:
        errors.append("decision_authority_role_assignment_not_found")
    else:
        _, rid, rv = role_key
        if role_state.latest_versions.get(("role_assignment", rid)) != rv:
            errors.append("decision_authority_version_not_current")
        rp = role.get("legal_payload", {})
        if rp.get("status") != "active":
            errors.append("decision_authority_inactive")
        if rp.get("role") not in {"decision_authority", "human_owner"}:
            errors.append("decision_authority_role_invalid")
        valid_from = rp.get("valid_from"); valid_until = rp.get("valid_until")
        if isinstance(valid_from, str) and when < _parse_datetime(valid_from):
            errors.append("decision_authority_not_yet_valid")
        if isinstance(valid_until, str) and when >= _parse_datetime(valid_until):
            errors.append("decision_authority_expired")
        operation = "decision.create" if current.get("entity", {}).get("entity_version") == 1 else "decision.next_version"
        if operation not in set(rp.get("authority_scope", {}).get("operations", [])):
            errors.append("decision_authority_scope_denied")
        if rp.get("role") == "decision_authority" and payload.get("subject") not in set(rp.get("authority_scope", {}).get("decision_subjects", [])):
            errors.append("decision_subject_not_authorized")
        if agent_card_state is not None and rp.get("subject", {}).get("principal_type") == "agent":
            refs = _agent_card_basis_refs(rp)
            if len(refs) == 1:
                card_key = _entity_ref_key(refs[0]); card = agent_card_state.cards.get(card_key) if card_key else None
                if card is None:
                    errors.append("decision_agent_card_not_found")
                elif "decision" not in set(card.get("legal_payload", {}).get("permissions", {}).get("data_write", [])):
                    errors.append("decision_agent_write_permission_missing")

    goal_key = _entity_ref_key(payload.get("goal", {})); goal = goal_state.contracts.get(goal_key) if goal_key else None
    policy = None
    if goal is None:
        errors.append("decision_goal_not_found")
    else:
        _, gid, gv = goal_key
        if goal_state.latest_versions.get(("goal_contract", gid)) != gv:
            errors.append("decision_goal_version_not_current")
        for item in goal.get("legal_payload", {}).get("decision_policy", []):
            if item.get("decision_subject") == payload.get("subject"):
                policy = item; break
        if policy is None:
            errors.append("decision_subject_not_in_goal_policy")

    plan_key = _entity_ref_key(payload.get("team_plan", {})); plan = team_plan_state.plans.get(plan_key) if plan_key else None
    if plan is None:
        errors.append("decision_team_plan_not_found")
    else:
        _, pid, pv = plan_key
        if team_plan_state.latest_versions.get(("team_plan", pid)) != pv:
            errors.append("decision_team_plan_version_not_current")
        declared = None
        for item in plan.get("legal_payload", {}).get("decision_authorities", []):
            if item.get("decision_subject") == payload.get("subject"):
                declared = item.get("role_assignment"); break
        if declared is None:
            errors.append("decision_team_authority_missing")
        elif not _same_entity_ref(declared, transition_ref):
            errors.append("decision_team_authority_mismatch")

    source_max_rank = -1
    contribution_records: list[dict[str, Any]] = []
    for ref in payload.get("based_on_contributions", []):
        rec, es = _resolve_decision_record(ref, contribution_state.contributions, contribution_state.latest_versions, prefix="contribution")
        errors.extend(es)
        if rec is not None:
            contribution_records.append(rec)
            cp = rec.get("legal_payload", {})
            if cp.get("status") != "active": errors.append("decision_contribution_inactive")
            if not _same_entity_ref(payload.get("goal"), cp.get("goal")): errors.append("decision_contribution_goal_mismatch")
            rank = _decision_record_rank(rec); source_max_rank = max(source_max_rank, rank if isinstance(rank,int) else -1)

    for ref in payload.get("supporting_evidence", []):
        rec, es = _resolve_decision_record(ref, evidence_state.evidence, evidence_state.latest_versions, prefix="evidence")
        errors.extend(es)
        if rec is not None:
            ep = rec.get("legal_payload", {})
            if ep.get("status") != "active": errors.append("decision_evidence_inactive")
            rank = _decision_record_rank(rec); source_max_rank = max(source_max_rank, rank if isinstance(rank,int) else -1)

    verification_records: list[dict[str, Any]] = []
    for ref in payload.get("verifications", []):
        rec, es = _resolve_decision_record(ref, verification_state.verifications, verification_state.latest_versions, prefix="verification")
        errors.extend(es)
        if rec is not None:
            verification_records.append(rec); vp = rec.get("legal_payload", {})
            if vp.get("status") != "active": errors.append("decision_verification_inactive")
            if vp.get("result") in {"inconclusive", "not_run"} or vp.get("conclusion_status") != "conclusive":
                errors.append("decision_verification_not_conclusive")
            rank = _decision_record_rank(rec); source_max_rank = max(source_max_rank, rank if isinstance(rank,int) else -1)

    review_records: list[dict[str, Any]] = []
    for ref in payload.get("reviews", []):
        rec, es = _resolve_decision_record(ref, review_state.reviews, review_state.latest_versions, prefix="review")
        errors.extend(es)
        if rec is not None:
            review_records.append(rec); rvp = rec.get("legal_payload", {})
            if rvp.get("status") != "active": errors.append("decision_review_inactive")
            rank = _decision_record_rank(rec); source_max_rank = max(source_max_rank, rank if isinstance(rank,int) else -1)

    addressed_records: list[dict[str, Any]] = []
    for ref in payload.get("addressed_challenges", []):
        rec, es = _resolve_decision_record(ref, challenge_state.challenges, challenge_state.latest_versions, prefix="addressed_challenge")
        errors.extend(es)
        if rec is not None:
            addressed_records.append(rec); cp = rec.get("legal_payload", {})
            if cp.get("status") not in {"resolved", "rejected"}:
                errors.append("decision_addressed_challenge_not_closed")
            rank = _decision_record_rank(rec); source_max_rank = max(source_max_rank, rank if isinstance(rank,int) else -1)

    unresolved_records: list[dict[str, Any]] = []
    for ref in payload.get("unresolved_challenges", []):
        rec, es = _resolve_decision_record(ref, challenge_state.challenges, challenge_state.latest_versions, prefix="unresolved_challenge")
        errors.extend(es)
        if rec is not None:
            unresolved_records.append(rec); cp = rec.get("legal_payload", {})
            if cp.get("status") not in DECISION_UNRESOLVED_CHALLENGE_STATUSES:
                errors.append("decision_unresolved_challenge_not_unresolved")
            rank = _decision_record_rank(rec); source_max_rank = max(source_max_rank, rank if isinstance(rank,int) else -1)

    approval_records: list[dict[str, Any]] = []
    for ref in payload.get("approvals", []):
        rec, es = _resolve_decision_record(ref, approval_state.approvals, approval_state.latest_versions, prefix="approval")
        errors.extend(es)
        if rec is not None:
            approval_records.append(rec); ap = rec.get("legal_payload", {})
            if ap.get("status") != "active" or ap.get("decision") != "approved":
                errors.append("decision_approval_not_effective")
            rank = _decision_record_rank(rec); source_max_rank = max(source_max_rank, rank if isinstance(rank,int) else -1)

    status = payload.get("status")
    if status == "accepted":
        if not contribution_records: errors.append("decision_accepted_without_contribution")
        if not verification_records: errors.append("decision_accepted_without_verification")
        if not review_records or not any(r.get("legal_payload", {}).get("result") == "approved" for r in review_records):
            errors.append("decision_accepted_without_approved_review")
        for challenge in unresolved_records:
            cp = challenge.get("legal_payload", {})
            if cp.get("severity") == "critical" and cp.get("status") != "disclosed_unresolved":
                errors.append("decision_accepted_with_unresolved_critical_challenge")
            if cp.get("status") == "disclosed_unresolved":
                target_ref = challenge.get("entity", {})
                has_risk = any(
                    a.get("legal_payload", {}).get("approval_kind") == "risk_acceptance"
                    and any(_same_entity_ref(target_ref, ref) for ref in a.get("legal_payload", {}).get("targets", []))
                    for a in approval_records
                )
                if not has_risk:
                    errors.append("decision_disclosed_challenge_missing_risk_approval")
        if policy and policy.get("human_approval_required") is True:
            current_identity = current.get("entity", {})
            has_decision_approval = any(
                a.get("legal_payload", {}).get("approval_kind") == "decision_approval"
                and any(_same_identity(current_identity, ref) for ref in a.get("legal_payload", {}).get("targets", []))
                for a in approval_records
            )
            if not has_decision_approval:
                errors.append("decision_human_approval_required")

    for dependency in payload.get("depends_on_decisions", []):
        ref = dependency.get("decision", {})
        rec, es = _resolve_decision_record(ref, decision_state.decisions, decision_state.latest_versions, prefix="dependency")
        errors.extend(es)
        if rec is not None:
            dep_status = rec.get("legal_payload", {}).get("status")
            if dep_status != "accepted":
                errors.append("decision_dependency_not_accepted")

    decision_rank = CONTRIBUTION_CLASSIFICATION_RANK.get(payload.get("classification"))
    if isinstance(decision_rank, int) and source_max_rank >= 0 and decision_rank < source_max_rank:
        errors.append("decision_classification_below_sources")

    output_id = payload.get("fulfills_output_id")
    if isinstance(output_id, str):
        reqs = {i.get("output_id"): i for i in tp.get("required_outputs", []) if isinstance(i, dict)}
        req = reqs.get(output_id)
        if req is None: errors.append("decision_output_requirement_not_found")
        elif req.get("entity_type") != "decision": errors.append("decision_output_requirement_type_mismatch")

    transition_at = payload.get("last_transition_at")
    if isinstance(transition_at, str) and _parse_datetime(transition_at) > when:
        errors.append("decision_transition_time_in_future")
    return errors


def validate_decision_dependency_graph(
    candidate: dict[str, Any], state: DecisionState
) -> list[str]:
    """Reject a dependency edge set that creates a cycle."""

    if candidate.get("legal_payload_schema") != DECISION_PAYLOAD_SCHEMA:
        return []
    candidate_key = (
        candidate.get("entity", {}).get("entity_type"),
        candidate.get("entity", {}).get("entity_id"),
    )
    graph: dict[EntityKey, set[EntityKey]] = {}
    for record in state.decisions.values():
        ref = record.get("entity", {})
        key = (ref.get("entity_type"), ref.get("entity_id"))
        graph.setdefault(key, set()).update(
            (item.get("decision", {}).get("entity_type"), item.get("decision", {}).get("entity_id"))
            for item in record.get("legal_payload", {}).get("depends_on_decisions", [])
            if isinstance(item, dict)
        )
    graph[candidate_key] = {
        (item.get("decision", {}).get("entity_type"), item.get("decision", {}).get("entity_id"))
        for item in candidate.get("legal_payload", {}).get("depends_on_decisions", [])
        if isinstance(item, dict)
    }
    visiting: set[EntityKey] = set(); visited: set[EntityKey] = set()
    def dfs(node: EntityKey) -> bool:
        if node in visiting: return True
        if node in visited: return False
        visiting.add(node)
        for child in graph.get(node, set()):
            if dfs(child): return True
        visiting.remove(node); visited.add(node); return False
    return ["decision_dependency_cycle"] if any(dfs(node) for node in list(graph)) else []


def decision_dependency_impacts(
    changed_decision: dict[str, Any], state: DecisionState, *, change: str
) -> dict[EntityKey, str]:
    """Return direct/transitive recorded dependency impacts for a decision change.

    ``change`` is one of ``revoked``, ``superseded``, or ``reassessment``.
    Stronger effects dominate weaker ones: invalidate > block > reassess > notify.
    """

    policy_field = {"revoked":"on_revoked", "superseded":"on_superseded", "reassessment":"on_reassessment"}.get(change)
    if policy_field is None: return {}
    changed_key = (changed_decision.get("entity", {}).get("entity_type"), changed_decision.get("entity", {}).get("entity_id"))
    strength = {"notify":0,"reassess":1,"block":2,"invalidate":3}
    impacts: dict[EntityKey,str] = {}; frontier=[changed_key]
    while frontier:
        source=frontier.pop(0)
        for record in state.decisions.values():
            rp=record.get("legal_payload", {})
            if rp.get("status") not in {"accepted","reassessment_required"}: continue
            key=(record.get("entity",{}).get("entity_type"),record.get("entity",{}).get("entity_id"))
            for dep in rp.get("depends_on_decisions", []):
                dref=dep.get("decision", {})
                if (dref.get("entity_type"),dref.get("entity_id")) != source: continue
                effect=dep.get(policy_field,"notify")
                if strength.get(effect,-1)>strength.get(impacts.get(key,"notify"),0): impacts[key]=effect
                if effect in {"reassess","block","invalidate"}: frontier.append(key)
    return impacts


def decisions_requiring_reassessment_for_reopened_challenge(
    challenge_record: dict[str, Any], state: DecisionState
) -> set[EntityKey]:
    """Find accepted decisions affected by a reopened critical challenge."""

    cp = challenge_record.get("legal_payload", {})
    if cp.get("status") != "reopened" or cp.get("severity") != "critical":
        return set()
    challenge_identity = (challenge_record.get("entity", {}).get("entity_type"), challenge_record.get("entity", {}).get("entity_id"))
    direct: set[EntityKey] = set()
    for record in state.decisions.values():
        rp=record.get("legal_payload", {})
        if rp.get("status") != "accepted": continue
        refs=rp.get("addressed_challenges", [])+rp.get("unresolved_challenges", [])
        if any((r.get("entity_type"),r.get("entity_id"))==challenge_identity for r in refs):
            direct.add((record.get("entity",{}).get("entity_type"),record.get("entity",{}).get("entity_id")))
    affected=set(direct)
    for key in list(direct):
        record=next((r for r in state.decisions.values() if (r.get("entity",{}).get("entity_type"),r.get("entity",{}).get("entity_id"))==key),None)
        if record:
            affected.update(k for k,e in decision_dependency_impacts(record,state,change="reassessment").items() if e in {"reassess","block","invalidate"})
    return affected


def validate_task_outputs_against_decisions(
    task_record: dict[str, Any], state: DecisionState
) -> list[str]:
    if task_record.get("legal_payload_schema") != TASK_PAYLOAD_SCHEMA:
        return []
    payload = task_record.get("legal_payload", {})
    if payload.get("status") != "completed": return []
    errors: list[str] = []
    for produced in payload.get("produced_outputs", []):
        ref=produced.get("entity", {})
        if ref.get("entity_type") != "decision": continue
        key=_entity_ref_key(ref); record=state.decisions.get(key) if key else None
        if record is None: errors.append("task_decision_output_not_found"); continue
        if record.get("legal_payload_schema") != DECISION_PAYLOAD_SCHEMA:
            errors.append("task_decision_output_payload_schema_mismatch"); continue
        rp=record.get("legal_payload", {})
        if not _same_identity(rp.get("task", {}), task_record.get("entity", {})):
            errors.append("task_decision_output_task_mismatch")
        if rp.get("fulfills_output_id") != produced.get("output_id"):
            errors.append("task_decision_output_binding_mismatch")
        if rp.get("status") != "accepted":
            errors.append("task_decision_output_not_accepted")
    return errors


# ---------------------------------------------------------------------------
# ProtocolEvent v0.1
# ---------------------------------------------------------------------------

PROTOCOL_EVENT_EFFECT_TYPES = frozenset({
    "reassessment_required",
    "invalidation_required",
    "execution_blocked",
    "authority_disabled",
    "context_access_disabled",
})


@dataclass(frozen=True)
class ProtocolEventTargetState:
    """Accepted VersionedEntity records that a ProtocolEvent may target."""

    records: dict[EntityVersionKey, dict[str, Any]] = field(default_factory=dict)
    latest_versions: dict[EntityKey, int] = field(default_factory=dict)


@dataclass(frozen=True)
class ProtocolEventState:
    """Accepted immutable ProtocolEvent entities and compensated effects."""

    events: dict[EntityVersionKey, dict[str, Any]] = field(default_factory=dict)
    compensated_events: frozenset[EntityVersionKey] = field(default_factory=frozenset)


@dataclass(frozen=True)
class ProtocolReplayState:
    """Deterministic projection produced from an append-only envelope sequence."""

    session_id: str | None = None
    last_sequence: int = 0
    last_recorded_at: str | None = None
    message_ids: frozenset[str] = field(default_factory=frozenset)
    records: dict[EntityVersionKey, dict[str, Any]] = field(default_factory=dict)
    latest_versions: dict[EntityKey, int] = field(default_factory=dict)
    active_protocol_events: dict[EntityVersionKey, dict[str, Any]] = field(default_factory=dict)
    compensated_protocol_events: frozenset[EntityVersionKey] = field(default_factory=frozenset)
    consumed_approvals: frozenset[EntityVersionKey] = field(default_factory=frozenset)


def validate_protocol_event_semantics(event: dict[str, Any]) -> list[str]:
    """Validate local ProtocolEvent invariants not expressible in JSON Schema."""

    errors: list[str] = []
    event_type = event.get("event_type")
    subject = event.get("subject", {})
    subject_type = subject.get("entity_type")
    triggers = event.get("trigger_refs", [])

    seen_trigger_identities: set[EntityKey] = set()
    for ref in triggers:
        identity = (ref.get("entity_type"), ref.get("entity_id"))
        if identity in seen_trigger_identities:
            errors.append("protocol_event_duplicate_trigger_identity")
        seen_trigger_identities.add(identity)
        if _same_entity_ref(subject, ref):
            errors.append("protocol_event_subject_used_as_trigger")

    if event_type in PROTOCOL_EVENT_EFFECT_TYPES:
        follow_up = event.get("required_follow_up", {})
        expected_operation = (
            f"{subject_type}.next_version" if isinstance(subject_type, str) else None
        )
        if expected_operation and follow_up.get("operation") != expected_operation:
            errors.append("protocol_event_follow_up_operation_mismatch")
        if event_type not in {"reassessment_required", "invalidation_required"}:
            if event.get("propagation") != "none":
                errors.append("protocol_event_propagation_not_allowed")
        if event_type == "reassessment_required" and subject_type == "protocol_event":
            errors.append("protocol_event_cannot_reassess_protocol_event")

    if event_type == "compensation_recorded":
        compensation = event.get("compensation", {})
        target = compensation.get("compensates_event", {})
        replacement = compensation.get("replacement_event")
        if not _same_entity_ref(subject, target):
            errors.append("protocol_event_compensation_subject_mismatch")
        if isinstance(replacement, dict) and _same_entity_ref(target, replacement):
            errors.append("protocol_event_compensation_replacement_same_as_target")

    return errors


def validate_protocol_event_transition(
    current: dict[str, Any], previous: dict[str, Any] | None
) -> list[str]:
    """ProtocolEvents are immutable one-version records; correction is compensating."""

    errors = validate_versioned_entity_transition(current, previous)
    if current.get("legal_payload_schema") != PROTOCOL_EVENT_PAYLOAD_SCHEMA:
        return errors
    errors.extend(validate_protocol_event_semantics(current.get("legal_payload", {})))
    if current.get("entity", {}).get("entity_version") != 1:
        errors.append("protocol_event_version_must_be_one")
    if previous is not None:
        errors.append("protocol_event_previous_record_forbidden")
    if "previous_version" in current or "change_metadata" in current:
        errors.append("protocol_event_lineage_forbidden")
    return errors


def _record_classification_rank(record: dict[str, Any] | None) -> int:
    if record is None:
        return -1
    value = record.get("legal_payload", {}).get("classification")
    return CONTRIBUTION_CLASSIFICATION_RANK.get(value, -1)


def validate_protocol_event_against_authority_and_state(
    envelope: dict[str, Any],
    target_state: ProtocolEventTargetState,
    protocol_event_state: ProtocolEventState,
    role_state: RoleAssignmentAuthorityState,
    *,
    agent_card_state: AgentCardState | None = None,
) -> list[str]:
    """Validate an accepted ProtocolEvent envelope against prior session state."""

    errors = validate_protocol_envelope_semantics(envelope)
    record = envelope.get("payload", {})
    if record.get("legal_payload_schema") != PROTOCOL_EVENT_PAYLOAD_SCHEMA:
        return errors

    errors.extend(
        validate_envelope_role_assignment_authority(
            envelope, role_state, agent_card_state=agent_card_state
        )
    )
    payload = record.get("legal_payload", {})
    subject = payload.get("subject", {})
    event_type = payload.get("event_type")
    event_rank = CONTRIBUTION_CLASSIFICATION_RANK.get(payload.get("classification"), -1)

    authority_ref = envelope.get("authorized_by", {})
    if event_type == "authority_disabled" and _same_entity_ref(subject, authority_ref):
        errors.append("protocol_event_self_disabling_authority")

    if event_type == "compensation_recorded":
        compensation = payload.get("compensation", {})
        target_ref = compensation.get("compensates_event", {})
        target_key = _entity_ref_key(target_ref)
        target_record = protocol_event_state.events.get(target_key) if target_key else None
        if target_record is None:
            errors.append("protocol_event_compensation_target_not_found")
            return errors
        if target_key in protocol_event_state.compensated_events:
            errors.append("protocol_event_effect_already_compensated")
        if target_record.get("legal_payload", {}).get("event_type") == "compensation_recorded":
            errors.append("protocol_event_compensation_of_compensation_forbidden")
        if envelope.get("causation_id") != target_record.get("created_by_event_id"):
            errors.append("protocol_event_compensation_causation_mismatch")
        target_rank = _record_classification_rank(target_record)
        if event_rank < target_rank:
            errors.append("protocol_event_classification_below_sources")
        replacement = compensation.get("replacement_event")
        if isinstance(replacement, dict):
            replacement_key = _entity_ref_key(replacement)
            replacement_record = (
                protocol_event_state.events.get(replacement_key)
                if replacement_key else None
            )
            if replacement_record is None:
                errors.append("protocol_event_replacement_not_found")
        return errors

    subject_key = _entity_ref_key(subject)
    subject_record = target_state.records.get(subject_key) if subject_key else None
    if subject_record is None:
        errors.append("protocol_event_subject_not_found")
    source_rank = _record_classification_rank(subject_record)

    trigger_created_events: set[str] = set()
    for ref in payload.get("trigger_refs", []):
        key = _entity_ref_key(ref)
        trigger_record = target_state.records.get(key) if key else None
        if trigger_record is None:
            errors.append("protocol_event_trigger_not_found")
            continue
        created_by = trigger_record.get("created_by_event_id")
        if isinstance(created_by, str):
            trigger_created_events.add(created_by)
        source_rank = max(source_rank, _record_classification_rank(trigger_record))

    if trigger_created_events and envelope.get("causation_id") not in trigger_created_events:
        errors.append("protocol_event_causation_not_a_trigger_event")
    if event_rank < source_rank:
        errors.append("protocol_event_classification_below_sources")
    return errors


def active_protocol_effects_for_subject(
    subject: dict[str, Any], state: ProtocolEventState
) -> list[dict[str, Any]]:
    """Return active uncompensated ProtocolEvents for one pinned subject."""

    result: list[dict[str, Any]] = []
    for key, record in state.events.items():
        if key in state.compensated_events:
            continue
        payload = record.get("legal_payload", {})
        if payload.get("event_type") == "compensation_recorded":
            continue
        if _same_entity_ref(payload.get("subject"), subject):
            result.append(record)
    return sorted(result, key=lambda item: item.get("created_by_event_id", ""))


def replay_protocol_log(
    envelopes: list[dict[str, Any]],
    *,
    trusted_recorders: frozenset[PrincipalKey] | None = None,
) -> tuple[ProtocolReplayState, list[str]]:
    """Replay an ordered append-only log into a deterministic normative projection.

    Operational and security events remain in the audit chain but do not change
    normative state. Protocol events containing VersionedEntity records advance
    immutable entity state. ProtocolEvent entities add or compensate overlays.
    """

    errors: list[str] = []
    session_id: str | None = None
    last_recorded_at: str | None = None
    message_ids: set[str] = set()
    records: dict[EntityVersionKey, dict[str, Any]] = {}
    latest_versions: dict[EntityKey, int] = {}
    active_events: dict[EntityVersionKey, dict[str, Any]] = {}
    compensated: set[EntityVersionKey] = set()
    consumed_approvals: set[EntityVersionKey] = set()
    bootstrap_completed = False
    used_bootstrap_ids: set[str] = set()
    effective_trusted_recorders = trusted_recorders
    if effective_trusted_recorders is None:
        effective_trusted_recorders = frozenset(
            key for key in (_principal_key(item.get("recorded_by")) for item in envelopes)
            if key is not None
        )

    for expected_sequence, envelope in enumerate(envelopes, start=1):
        message_id = envelope.get("message_id")
        current_session = envelope.get("session_id")
        if session_id is None:
            session_id = current_session if isinstance(current_session, str) else None
        elif current_session != session_id:
            errors.append("protocol_log_session_mismatch")

        if envelope.get("kind") != "event":
            errors.append("protocol_log_command_not_replayable")
            continue
        if envelope.get("sequence") != expected_sequence:
            errors.append("protocol_log_sequence_gap")
        if not isinstance(message_id, str) or message_id in message_ids:
            errors.append("protocol_log_message_id_duplicate")
        causation_id = envelope.get("causation_id")
        if causation_id is not None and causation_id not in message_ids:
            errors.append("protocol_log_causation_not_prior")
        for related in envelope.get("related_events", []):
            if related not in message_ids:
                errors.append("protocol_log_related_event_not_prior")

        recorded_at = envelope.get("recorded_at")
        if isinstance(recorded_at, str) and isinstance(last_recorded_at, str):
            if _parse_datetime(recorded_at) < _parse_datetime(last_recorded_at):
                errors.append("protocol_log_recorded_time_regressed")
        if isinstance(recorded_at, str):
            last_recorded_at = recorded_at

        errors.extend(validate_protocol_envelope_semantics(envelope))
        session_state = SessionValidationState(
            event_count=expected_sequence - 1,
            bootstrap_completed=bootstrap_completed,
            used_bootstrap_ids=frozenset(used_bootstrap_ids),
            trusted_recorders=effective_trusted_recorders,
        )
        errors.extend(validate_protocol_envelope_session_semantics(envelope, session_state))
        if envelope.get("type") == "session.bootstrap.completed":
            bootstrap_completed = True
            bootstrap_id = envelope.get("bootstrap", {}).get("bootstrap_id")
            if isinstance(bootstrap_id, str):
                used_bootstrap_ids.add(bootstrap_id)

        if envelope.get("event_class") == "protocol_event" and envelope.get(
            "payload_schema"
        ) == "urn:w1-cip:schema:0.1:versioned-entity":
            record = envelope.get("payload", {})
            repo_state = VersionedEntityRepositoryState(
                latest_versions=dict(latest_versions),
                accepted_versions=frozenset(records),
            )
            errors.extend(validate_versioned_entity_repository_semantics(record, repo_state))

            role_records = {
                key: value
                for key, value in records.items()
                if key[0] == "role_assignment"
            }
            role_latest = {
                key: value
                for key, value in latest_versions.items()
                if key[0] == "role_assignment"
            }
            role_state = RoleAssignmentAuthorityState(role_records, role_latest)
            if envelope.get("type") != "session.bootstrap.completed":
                errors.extend(validate_envelope_role_assignment_authority(envelope, role_state))
                approval_records = {
                    key: value
                    for key, value in records.items()
                    if key[0] == "approval"
                }
                approval_latest = {
                    key: value
                    for key, value in latest_versions.items()
                    if key[0] == "approval"
                }
                approval_state = ApprovalState(
                    approvals=approval_records,
                    latest_versions=approval_latest,
                    consumed=frozenset(consumed_approvals),
                )
                errors.extend(
                    validate_envelope_approval_authority(
                        envelope, approval_state, role_state
                    )
                )
                approval_key = _entity_ref_key(envelope.get("authorized_by", {}))
                if approval_key is not None and approval_key[0] == "approval":
                    consumed_approvals.add(approval_key)

            if record.get("legal_payload_schema") == PROTOCOL_EVENT_PAYLOAD_SCHEMA:
                target_state = ProtocolEventTargetState(dict(records), dict(latest_versions))
                pe_state = ProtocolEventState(dict(active_events), frozenset(compensated))
                errors.extend(
                    validate_protocol_event_against_authority_and_state(
                        envelope, target_state, pe_state, role_state
                    )
                )
                key = _entity_ref_key(record.get("entity", {}))
                payload = record.get("legal_payload", {})
                if key is not None:
                    if payload.get("event_type") == "compensation_recorded":
                        target_key = _entity_ref_key(
                            payload.get("compensation", {}).get("compensates_event", {})
                        )
                        if target_key is not None:
                            compensated.add(target_key)
                    else:
                        active_events[key] = record

            ref = record.get("entity", {})
            key = _entity_ref_key(ref)
            if key is not None:
                records[key] = record
                latest_versions[(key[0], key[1])] = key[2]

        if isinstance(message_id, str):
            message_ids.add(message_id)

    state = ProtocolReplayState(
        session_id=session_id,
        last_sequence=len(envelopes),
        last_recorded_at=last_recorded_at,
        message_ids=frozenset(message_ids),
        records=records,
        latest_versions=latest_versions,
        active_protocol_events={
            key: value for key, value in active_events.items() if key not in compensated
        },
        compensated_protocol_events=frozenset(compensated),
        consumed_approvals=frozenset(consumed_approvals),
    )
    return state, errors


def protocol_event_payloads_for_decision_change(
    changed_decision: dict[str, Any],
    state: DecisionState,
    *,
    change: str,
) -> list[dict[str, Any]]:
    """Build deterministic effect payloads from recorded Decision dependencies."""

    mapping = {
        "reassess": "reassessment_required",
        "block": "execution_blocked",
        "invalidate": "invalidation_required",
    }
    impacts = decision_dependency_impacts(changed_decision, state, change=change)
    trigger = changed_decision.get("entity", {})
    trigger_rank = _record_classification_rank(changed_decision)
    payloads: list[dict[str, Any]] = []
    for identity, impact in sorted(impacts.items()):
        event_type = mapping.get(impact)
        if event_type is None:
            continue
        latest = state.latest_versions.get(identity)
        record = state.decisions.get((identity[0], identity[1], latest)) if latest else None
        if record is None:
            continue
        subject = record.get("entity", {})
        classification_rank = max(trigger_rank, _record_classification_rank(record), 0)
        classification = next(
            name
            for name, rank in CONTRIBUTION_CLASSIFICATION_RANK.items()
            if rank == classification_rank
        )
        payloads.append({
            "event_type": event_type,
            "subject": subject,
            "trigger_refs": [trigger],
            "reason": {
                "code": f"dependency_{change}_{impact}",
                "description": (
                    f"Recorded dependency policy requires {impact} after the "
                    f"source decision changed by {change}."
                ),
            },
            "classification": classification,
            "propagation": "none",
            "blocking": True,
            "required_follow_up": {
                "operation": "decision.next_version",
                "must_complete_before": "decision.accepted",
            },
        })
    return payloads


def protocol_event_payloads_for_reopened_challenge(
    challenge_record: dict[str, Any], state: DecisionState
) -> list[dict[str, Any]]:
    """Build reassessment signals for decisions affected by a reopened challenge."""

    affected = decisions_requiring_reassessment_for_reopened_challenge(
        challenge_record, state
    )
    trigger = challenge_record.get("entity", {})
    trigger_rank = _record_classification_rank(challenge_record)
    payloads: list[dict[str, Any]] = []
    for identity in sorted(affected):
        latest = state.latest_versions.get(identity)
        record = state.decisions.get((identity[0], identity[1], latest)) if latest else None
        if record is None:
            continue
        rank = max(trigger_rank, _record_classification_rank(record), 0)
        classification = next(
            name for name, value in CONTRIBUTION_CLASSIFICATION_RANK.items() if value == rank
        )
        payloads.append({
            "event_type": "reassessment_required",
            "subject": record.get("entity", {}),
            "trigger_refs": [trigger],
            "reason": {
                "code": "critical_challenge_reopened",
                "description": (
                    "A critical challenge used by the accepted decision was reopened."
                ),
            },
            "classification": classification,
            "propagation": "none",
            "blocking": True,
            "required_follow_up": {
                "operation": "decision.next_version",
                "must_complete_before": "decision.accepted",
            },
        })
    return payloads


@dataclass(frozen=True)
class ExecutionResourcePlanState:
    """Accepted ExecutionResourcePlan records for runtime routing validation."""

    plans: dict[EntityVersionKey, dict[str, Any]] = field(default_factory=dict)
    latest_versions: dict[EntityKey, int] = field(default_factory=dict)


@dataclass(frozen=True)
class ResourceRoutingResult:
    """Deterministic result of applying one execution-resource routing plan."""

    selected_resource_id: str | None
    action: str
    used_fallback: bool = False
    requires_extra_review: bool = False
    disclosure_required: bool = False
    plan_update_required: bool = False
    error_codes: tuple[str, ...] = ()


def _routing_candidate_by_id(rule: dict[str, Any], resource_id: str | None) -> dict[str, Any] | None:
    if not isinstance(resource_id, str):
        return None
    for candidate in rule.get("candidates", []):
        if candidate.get("resource_id") == resource_id:
            return candidate
    return None


def validate_execution_resource_plan_semantics(plan: dict[str, Any]) -> list[str]:
    """Validate local quota, heterogeneity, routing, reserve, and fallback invariants."""

    errors: list[str] = []
    resources = plan.get("resources", [])
    resource_ids: set[str] = set()
    resource_by_id: dict[str, dict[str, Any]] = {}

    for resource in resources:
        resource_id = resource.get("resource_id")
        if resource_id in resource_ids:
            errors.append("execution_resource_duplicate_resource_id")
        if isinstance(resource_id, str):
            resource_ids.add(resource_id)
            resource_by_id[resource_id] = resource

        quota = resource.get("quota", {})
        limit_units = quota.get("limit_units")
        remaining_units = quota.get("remaining_units")
        reserved_units = quota.get("reserved_units")
        if isinstance(limit_units, int) and isinstance(remaining_units, int):
            if remaining_units > limit_units:
                errors.append("execution_resource_remaining_exceeds_limit")
        if isinstance(limit_units, int) and isinstance(reserved_units, int):
            if reserved_units > limit_units:
                errors.append("execution_resource_reserve_exceeds_limit")
        if quota.get("status") == "available" and remaining_units == 0:
            errors.append("execution_resource_available_with_zero_remaining")
        if quota.get("status") == "exhausted" and isinstance(remaining_units, int) and remaining_units != 0:
            errors.append("execution_resource_exhausted_with_remaining")
        observed_at = quota.get("observed_at")
        reset_at = quota.get("reset_at")
        if isinstance(observed_at, str) and isinstance(reset_at, str):
            if _parse_datetime(reset_at) <= _parse_datetime(observed_at):
                errors.append("execution_resource_reset_not_after_observation")

    rule_ids: set[str] = set()
    for rule in plan.get("routing_rules", []):
        rule_id = rule.get("rule_id")
        if rule_id in rule_ids:
            errors.append("execution_resource_duplicate_rule_id")
        if isinstance(rule_id, str):
            rule_ids.add(rule_id)

        candidate_resources: set[str] = set()
        priorities: set[int] = set()
        primaries: list[dict[str, Any]] = []
        for candidate in rule.get("candidates", []):
            resource_id = candidate.get("resource_id")
            priority = candidate.get("priority")
            if resource_id in candidate_resources:
                errors.append("execution_resource_duplicate_candidate")
            if isinstance(resource_id, str):
                candidate_resources.add(resource_id)
            if priority in priorities:
                errors.append("execution_resource_duplicate_candidate_priority")
            if isinstance(priority, int):
                priorities.add(priority)
            if resource_id not in resource_by_id:
                errors.append("execution_resource_candidate_not_declared")
            if candidate.get("mode") == "primary":
                primaries.append(candidate)

        if len(primaries) != 1:
            errors.append("execution_resource_exactly_one_primary_required")
        elif primaries[0].get("priority") != 1:
            errors.append("execution_resource_primary_priority_must_be_one")

        routing_status = rule.get("routing_status")
        active_id = rule.get("active_resource_id")
        if routing_status == "active":
            active_candidate = _routing_candidate_by_id(rule, active_id)
            if active_candidate is None:
                errors.append("execution_resource_active_candidate_not_found")
            else:
                active_resource = resource_by_id.get(active_id)
                if active_resource and active_resource.get("quota", {}).get("status") == "exhausted":
                    errors.append("execution_resource_active_candidate_exhausted")
                minimum_floor = rule.get("minimum_fitness_score")
                fitness = active_candidate.get("fitness_score")
                below_floor = (
                    isinstance(minimum_floor, int)
                    and isinstance(fitness, int)
                    and fitness < minimum_floor
                )
                policy = rule.get("below_floor_policy")
                if below_floor and policy == "prohibited":
                    errors.append("execution_resource_active_candidate_below_floor")
                if below_floor and policy == "allow_with_extra_review" and not rule.get(
                    "additional_review_required"
                ):
                    errors.append("execution_resource_extra_review_required")
                if below_floor and policy == "allow_for_non_final_work":
                    final_phases = {"approval", "synthesis"}
                    if final_phases & set(rule.get("applies_to", {}).get("phases", [])):
                        errors.append("execution_resource_below_floor_final_phase_forbidden")
                if active_candidate.get("mode") == "fallback":
                    if rule.get("activation_reason") == "initial_selection":
                        errors.append("execution_resource_fallback_requires_activation_reason")
                    fallback_from = rule.get("fallback_from_resource_id")
                    if fallback_from not in candidate_resources or fallback_from == active_id:
                        errors.append("execution_resource_invalid_fallback_origin")
                elif "fallback_from_resource_id" in rule:
                    errors.append("execution_resource_primary_must_not_declare_fallback_origin")
        else:
            if "active_resource_id" in rule:
                errors.append("execution_resource_inactive_routing_has_active_resource")
            if rule.get("activation_reason") == "initial_selection":
                errors.append("execution_resource_inactive_routing_requires_unavailability_reason")
            if rule.get("additional_review_required"):
                errors.append("execution_resource_inactive_routing_cannot_require_review")

        if rule.get("selection_objective") == "capability_first" and primaries:
            primary_score = primaries[0].get("fitness_score")
            scores = [c.get("fitness_score") for c in rule.get("candidates", [])]
            numeric_scores = [score for score in scores if isinstance(score, int)]
            if isinstance(primary_score, int) and numeric_scores and primary_score < max(numeric_scores):
                errors.append("execution_resource_capability_first_primary_not_strongest")

        if rule.get("on_primary_unavailable") == "activate_fallback":
            if not any(c.get("mode") == "fallback" for c in rule.get("candidates", [])):
                errors.append("execution_resource_fallback_candidate_required")

    return errors


def validate_execution_resource_plan_transition(
    current: dict[str, Any], previous: dict[str, Any] | None
) -> list[str]:
    """Validate lifecycle and immutable scope across resource-plan versions."""

    errors = validate_versioned_entity_transition(current, previous)
    if current.get("legal_payload_schema") != EXECUTION_RESOURCE_PLAN_PAYLOAD_SCHEMA:
        return errors
    payload = current.get("legal_payload", {})
    errors.extend(validate_execution_resource_plan_semantics(payload))
    if current.get("entity", {}).get("entity_version") == 1 and payload.get("status") != "active":
        errors.append("execution_resource_plan_initial_status_not_active")
    if previous is None or previous.get("legal_payload_schema") != EXECUTION_RESOURCE_PLAN_PAYLOAD_SCHEMA:
        return errors

    old = previous.get("legal_payload", {})
    if payload.get("goal") != old.get("goal"):
        errors.append("execution_resource_plan_goal_changed")
    if payload.get("team_plan") != old.get("team_plan"):
        errors.append("execution_resource_plan_team_plan_changed")
    if old.get("status") in EXECUTION_RESOURCE_PLAN_TERMINAL_STATUSES:
        errors.append("execution_resource_plan_terminal_status_final")

    allowed = {
        "active": {"active", "suspended", "completed", "cancelled"},
        "suspended": {"active", "suspended", "completed", "cancelled"},
        "completed": {"completed"},
        "cancelled": {"cancelled"},
    }
    if payload.get("status") not in allowed.get(old.get("status"), set()):
        errors.append("execution_resource_plan_invalid_status_transition")
    return errors


def _resolve_current_resource_team(
    ref: dict[str, Any], team_state: TeamPlanState
) -> tuple[dict[str, Any] | None, list[str]]:
    key = _entity_ref_key(ref)
    if key is None:
        return None, []
    record = team_state.plans.get(key)
    if record is None:
        return None, ["execution_resource_team_plan_not_found"]
    errors: list[str] = []
    _, entity_id, version = key
    latest = team_state.latest_versions.get(("team_plan", entity_id))
    effective = record
    if isinstance(latest, int) and latest > version:
        for n in range(version + 1, latest + 1):
            successor = team_state.plans.get(("team_plan", entity_id, n))
            if successor is None:
                return None, ["execution_resource_team_plan_lineage_incomplete"]
            effective = successor
            if successor.get("change_metadata", {}).get("substantive_effect") != "none":
                errors.append("execution_resource_team_plan_version_not_current")
                break
    elif latest != version:
        errors.append("execution_resource_team_plan_version_not_current")
    if effective.get("legal_payload_schema") != TEAM_PLAN_PAYLOAD_SCHEMA:
        errors.append("execution_resource_team_plan_payload_schema_mismatch")
        return None, errors
    if effective.get("legal_payload", {}).get("status") != "active":
        errors.append("execution_resource_team_plan_inactive")
    return effective, errors


def validate_execution_resource_plan_against_state(
    plan: dict[str, Any],
    team_state: TeamPlanState,
    role_state: RoleAssignmentAuthorityState,
    agent_card_state: AgentCardState,
    *,
    evaluated_at: str,
) -> list[str]:
    """Resolve team membership, active role assignments, and AgentCards."""

    errors = validate_execution_resource_plan_semantics(plan)
    team_record, team_errors = _resolve_current_resource_team(plan.get("team_plan", {}), team_state)
    errors.extend(team_errors)
    if team_record is None:
        return errors
    team_payload = team_record.get("legal_payload", {})
    if plan.get("goal") != team_payload.get("goal"):
        errors.append("execution_resource_goal_mismatch")

    team_refs = {
        _entity_ref_key(item.get("role_assignment", {}))
        for item in team_payload.get("assignments", [])
    }
    role_by_resource: dict[str, str] = {}
    for resource in plan.get("resources", []):
        resource_id = resource.get("resource_id")
        role_ref = resource.get("role_assignment", {})
        key = _entity_ref_key(role_ref)
        record = role_state.assignments.get(key) if key else None
        if record is None:
            errors.append("execution_resource_role_assignment_not_found")
            continue
        if key not in team_refs:
            errors.append("execution_resource_role_assignment_not_in_team")
        identity = (role_ref.get("entity_type"), role_ref.get("entity_id"))
        latest = role_state.latest_versions.get(identity)
        if latest != role_ref.get("entity_version"):
            errors.append("execution_resource_role_assignment_not_current")
        payload = record.get("legal_payload", {})
        if payload.get("status") != "active":
            errors.append("execution_resource_role_assignment_inactive")
        valid_from = payload.get("valid_from")
        valid_until = payload.get("valid_until")
        now = _parse_datetime(evaluated_at)
        if isinstance(valid_from, str) and now < _parse_datetime(valid_from):
            errors.append("execution_resource_role_assignment_not_yet_valid")
        if isinstance(valid_until, str) and now >= _parse_datetime(valid_until):
            errors.append("execution_resource_role_assignment_expired")
        subject = payload.get("subject", {})
        if subject.get("principal_type") != "agent":
            errors.append("execution_resource_requires_agent_principal")
        if isinstance(resource_id, str) and isinstance(payload.get("role"), str):
            role_by_resource[resource_id] = payload["role"]

        card_ref = resource.get("agent_card", {})
        if card_ref not in _agent_card_basis_refs(payload):
            errors.append("execution_resource_agent_card_ref_mismatch")
        errors.extend(validate_role_assignment_agent_eligibility(record, agent_card_state))

        card_key = _entity_ref_key(card_ref)
        card = agent_card_state.cards.get(card_key) if card_key else None
        if card is None:
            errors.append("execution_resource_agent_card_not_found")
        elif card.get("legal_payload", {}).get("status") != "active":
            errors.append("execution_resource_agent_card_inactive")

        observed_at = resource.get("quota", {}).get("observed_at")
        if isinstance(observed_at, str) and _parse_datetime(observed_at) > now:
            errors.append("execution_resource_quota_observation_in_future")

    for rule in plan.get("routing_rules", []):
        allowed_roles = set(rule.get("applies_to", {}).get("roles", []))
        for candidate in rule.get("candidates", []):
            role = role_by_resource.get(candidate.get("resource_id"))
            if role is not None and role not in allowed_roles:
                errors.append("execution_resource_candidate_role_mismatch")
    return errors


def _routing_rule_matches(
    rule: dict[str, Any],
    *,
    role: str,
    phase: str,
    risk_level: str,
    task_complexity: str,
    domains: set[str],
) -> bool:
    selector = rule.get("applies_to", {})
    if role not in set(selector.get("roles", [])):
        return False
    if phase not in set(selector.get("phases", [])):
        return False
    if selector.get("risk_levels") and risk_level not in set(selector["risk_levels"]):
        return False
    if selector.get("task_complexities") and task_complexity not in set(selector["task_complexities"]):
        return False
    required_domains = set(selector.get("domains", []))
    if required_domains and not required_domains.issubset(domains):
        return False
    return True


def route_execution_resource(
    plan: dict[str, Any],
    *,
    role: str,
    phase: str,
    risk_level: str,
    task_complexity: str,
    domains: set[str] | None = None,
    estimated_units: int = 1,
) -> ResourceRoutingResult:
    """Choose a model resource without equal voting or silent capability downgrade.

    The routine uses task-specific candidate fitness and provider-account quota
    observations. Reserved units are unavailable to non-protected phases. A
    lower-fitness fallback is either rejected or marked for additional review.
    """

    local_errors = validate_execution_resource_plan_semantics(plan)
    if local_errors:
        return ResourceRoutingResult(None, "invalid_plan", error_codes=tuple(local_errors))
    if plan.get("status") != "active":
        return ResourceRoutingResult(None, "blocked", error_codes=("execution_resource_plan_inactive",))
    if estimated_units < 0:
        return ResourceRoutingResult(None, "invalid_request", error_codes=("execution_resource_negative_estimate",))

    matches = [
        rule
        for rule in plan.get("routing_rules", [])
        if _routing_rule_matches(
            rule,
            role=role,
            phase=phase,
            risk_level=risk_level,
            task_complexity=task_complexity,
            domains=domains or set(),
        )
    ]
    if not matches:
        return ResourceRoutingResult(None, "blocked", error_codes=("execution_resource_no_matching_rule",))
    if len(matches) > 1:
        return ResourceRoutingResult(None, "blocked", error_codes=("execution_resource_ambiguous_routing_rule",))

    rule = matches[0]
    resource_by_id = {r.get("resource_id"): r for r in plan.get("resources", [])}
    protected = phase in set(plan.get("reserve_policy", {}).get("protected_phases", []))

    def usable(candidate: dict[str, Any]) -> bool:
        resource = resource_by_id.get(candidate.get("resource_id"), {})
        quota = resource.get("quota", {})
        if quota.get("status") in {"exhausted", "unknown"}:
            return False
        remaining = quota.get("remaining_units")
        reserve = quota.get("reserved_units", 0)
        if not isinstance(remaining, int):
            return quota.get("status") == "available" and estimated_units == 0
        available_units = remaining if protected else max(0, remaining - reserve)
        required = max(estimated_units, candidate.get("minimum_remaining_units", 0))
        return available_units >= required

    candidates = sorted(rule.get("candidates", []), key=lambda c: c.get("priority", 10**9))
    selected = next((candidate for candidate in candidates if usable(candidate)), None)
    if selected is None:
        action = plan.get("fallback_policy", {}).get("all_candidates_unavailable", "blocked")
        if action == "await_reset":
            result_action = "await_reset"
        elif action == "await_user":
            result_action = "await_user"
        elif action == "cancelled":
            result_action = "cancelled"
        else:
            result_action = "blocked"
        return ResourceRoutingResult(
            None,
            result_action,
            disclosure_required=True,
            error_codes=("execution_resource_all_candidates_unavailable",),
        )

    primary = next(c for c in candidates if c.get("mode") == "primary")
    used_fallback = selected.get("mode") == "fallback"
    floor = rule.get("minimum_fitness_score", 0)
    below_floor = selected.get("fitness_score", 0) < floor
    policy = rule.get("below_floor_policy")
    if below_floor and policy == "prohibited":
        return ResourceRoutingResult(
            None,
            "blocked",
            disclosure_required=True,
            error_codes=("execution_resource_selected_below_quality_floor",),
        )
    if below_floor and policy == "allow_for_non_final_work" and phase in {"approval", "synthesis"}:
        return ResourceRoutingResult(
            None,
            "blocked",
            disclosure_required=True,
            error_codes=("execution_resource_lower_tier_final_work_forbidden",),
        )

    selected_id = selected.get("resource_id")
    return ResourceRoutingResult(
        selected_resource_id=selected_id,
        action="selected",
        used_fallback=used_fallback,
        requires_extra_review=(
            used_fallback
            and (
                below_floor
                or selected.get("fitness_score", 0) < primary.get("fitness_score", 0)
            )
        )
        or bool(rule.get("additional_review_required")),
        disclosure_required=used_fallback,
        plan_update_required=(rule.get("routing_status") != "active" or selected_id != rule.get("active_resource_id")),
    )


@dataclass(frozen=True)
class FinalResultState:
    """Accepted FinalResult records and latest versions."""

    results: dict[EntityVersionKey, dict[str, Any]] = field(default_factory=dict)
    latest_versions: dict[EntityKey, int] = field(default_factory=dict)


def _ref_identities(refs: list[dict[str, Any]]) -> list[EntityVersionKey | None]:
    return [_entity_ref_key(ref) for ref in refs]


def validate_final_result_semantics(result: dict[str, Any]) -> list[str]:
    """Validate local completeness, disclosure, and lifecycle invariants."""

    errors: list[str] = []
    deliverables = result.get("deliverable_assessments", [])
    deliverable_ids = [item.get("deliverable_id") for item in deliverables]
    if len(deliverable_ids) != len(set(deliverable_ids)):
        errors.append("final_result_duplicate_deliverable_id")

    criteria = result.get("success_criteria_assessments", [])
    criterion_ids = [item.get("criterion_id") for item in criteria]
    if len(criterion_ids) != len(set(criterion_ids)):
        errors.append("final_result_duplicate_criterion_id")

    verified = set(_ref_identities(result.get("verified_claims", [])))
    refuted = set(_ref_identities(result.get("refuted_claims", [])))
    unverified_items = result.get("unverified_claims", [])
    unverified = [_entity_ref_key(item.get("claim", {})) for item in unverified_items]
    if len(unverified) != len(set(unverified)):
        errors.append("final_result_duplicate_unverified_claim")
    if verified & refuted:
        errors.append("final_result_claim_both_verified_and_refuted")
    if verified & set(unverified):
        errors.append("final_result_claim_both_verified_and_unverified")
    if refuted & set(unverified):
        errors.append("final_result_claim_both_refuted_and_unverified")

    resolved = set(_ref_identities(result.get("resolved_challenges", [])))
    unresolved = set(_ref_identities(result.get("unresolved_challenges", [])))
    if resolved & unresolved:
        errors.append("final_result_challenge_both_resolved_and_unresolved")

    resource_summary = result.get("resource_summary", {})
    resource_outcomes = resource_summary.get("resource_outcomes", [])
    resource_ids = [item.get("resource_id") for item in resource_outcomes]
    if len(resource_ids) != len(set(resource_ids)):
        errors.append("final_result_duplicate_resource_outcome")
    declared_resources = set(resource_ids)
    seen_rules: set[str] = set()
    for disclosure in resource_summary.get("routing_disclosures", []):
        rule_id = disclosure.get("rule_id")
        if rule_id in seen_rules:
            errors.append("final_result_duplicate_routing_disclosure")
        if isinstance(rule_id, str):
            seen_rules.add(rule_id)
        selected = disclosure.get("selected_resource_id")
        if selected not in declared_resources:
            errors.append("final_result_routing_resource_not_declared")
        origin = disclosure.get("fallback_from_resource_id")
        if origin is not None:
            if origin not in declared_resources or origin == selected:
                errors.append("final_result_invalid_fallback_origin")
            if disclosure.get("activation_reason") == "initial_selection":
                errors.append("final_result_fallback_requires_noninitial_reason")

    anchor = result.get("log_anchor", {})
    if anchor.get("last_sequence") != anchor.get("event_count"):
        errors.append("final_result_log_count_sequence_mismatch")

    result_status = result.get("result_status")
    deliverable_outcomes = {item.get("outcome") for item in deliverables}
    criterion_outcomes = {item.get("outcome") for item in criteria}
    if result_status == "succeeded":
        if any(outcome not in {"satisfied", "not_applicable"} for outcome in deliverable_outcomes):
            errors.append("final_result_succeeded_with_incomplete_deliverable")
        if any(outcome != "satisfied" for outcome in criterion_outcomes):
            errors.append("final_result_succeeded_with_unmet_criterion")
        if not result.get("accepted_decisions"):
            errors.append("final_result_succeeded_requires_accepted_decision")
        if not resource_summary.get("all_required_work_completed"):
            errors.append("final_result_succeeded_with_unfinished_work")
    elif result_status == "partially_succeeded":
        if deliverable_outcomes <= {"satisfied", "not_applicable"} and criterion_outcomes <= {"satisfied"}:
            errors.append("final_result_partial_without_recorded_gap")
    elif result_status == "failed":
        if not ({"unsatisfied", "not_produced"} & deliverable_outcomes or "not_satisfied" in criterion_outcomes):
            errors.append("final_result_failed_without_failed_requirement")
    elif result_status == "blocked":
        if resource_summary.get("all_required_work_completed"):
            errors.append("final_result_blocked_but_work_complete")

    lifecycle = result.get("lifecycle_status")
    if lifecycle == "reassessment_required" and not result.get("reassessment", {}).get("trigger_refs"):
        errors.append("final_result_reassessment_trigger_required")
    return errors


def validate_final_result_transition(
    current: dict[str, Any], previous: dict[str, Any] | None
) -> list[str]:
    """Validate immutable publication content and lifecycle-only later versions."""

    errors = validate_versioned_entity_transition(current, previous)
    if current.get("legal_payload_schema") != FINAL_RESULT_PAYLOAD_SCHEMA:
        return errors
    payload = current.get("legal_payload", {})
    errors.extend(validate_final_result_semantics(payload))
    if current.get("entity", {}).get("entity_version") == 1 and payload.get("lifecycle_status") != "published":
        errors.append("final_result_initial_lifecycle_not_published")
    if previous is None or previous.get("legal_payload_schema") != FINAL_RESULT_PAYLOAD_SCHEMA:
        return errors
    old = previous.get("legal_payload", {})
    immutable_fields = (
        "goal", "team_plan", "task", "generated_by_role_assignment", "generated_at",
        "result_status", "summary", "deliverable_assessments", "success_criteria_assessments",
        "accepted_decisions", "verified_claims", "refuted_claims", "unverified_claims", "evidence",
        "verifications", "reviews", "resolved_challenges", "unresolved_challenges",
        "approvals", "limitations", "resource_summary", "log_anchor", "classification",
        "fulfills_output_id",
    )
    for field_name in immutable_fields:
        if payload.get(field_name) != old.get(field_name):
            errors.append(f"final_result_{field_name}_changed")
    previous_lifecycle = old.get("lifecycle_status")
    current_lifecycle = payload.get("lifecycle_status")
    allowed = {
        "published": {"reassessment_required", "superseded", "withdrawn"},
        "reassessment_required": {"superseded", "withdrawn"},
        "superseded": set(),
        "withdrawn": set(),
    }
    if previous_lifecycle in FINAL_RESULT_TERMINAL_LIFECYCLE:
        errors.append("final_result_terminal_lifecycle_final")
    elif current_lifecycle not in allowed.get(previous_lifecycle, set()):
        errors.append("final_result_invalid_lifecycle_transition")
    if current_lifecycle == "superseded":
        replacement = payload.get("superseded_by", {})
        if _same_identity(replacement, current.get("entity", {})):
            errors.append("final_result_cannot_supersede_with_same_identity")
    return errors


def _lookup_current_record(
    ref: dict[str, Any], records: dict[EntityVersionKey, dict[str, Any]],
    latest_versions: dict[EntityKey, int], *, prefix: str,
) -> tuple[dict[str, Any] | None, list[str]]:
    key = _entity_ref_key(ref)
    if key is None:
        return None, []
    record = records.get(key)
    if record is None:
        return None, [f"{prefix}_not_found"]
    latest = latest_versions.get((key[0], key[1]))
    if latest != key[2]:
        return record, [f"{prefix}_version_not_current"]
    return record, []


def validate_final_result_against_state(
    result: dict[str, Any],
    goal_state: GoalContractState,
    team_state: TeamPlanState,
    task_state: TaskState,
    role_state: RoleAssignmentAuthorityState,
    decision_state: DecisionState,
    contribution_state: ContributionState,
    evidence_state: EvidenceState,
    verification_state: VerificationState,
    review_state: ReviewState,
    challenge_state: ChallengeState,
    approval_state: ApprovalState,
    resource_plan_state: ExecutionResourcePlanState,
    *,
    generated_at: str,
    protocol_envelopes: list[dict[str, Any]] | None = None,
) -> list[str]:
    """Resolve the published result against current normative session state."""

    errors = validate_final_result_semantics(result)
    goal, found = _lookup_current_record(
        result.get("goal", {}), goal_state.contracts, goal_state.latest_versions,
        prefix="final_result_goal",
    )
    errors.extend(found)
    team, found = _lookup_current_record(
        result.get("team_plan", {}), team_state.plans, team_state.latest_versions,
        prefix="final_result_team_plan",
    )
    errors.extend(found)
    task, found = _lookup_current_record(
        result.get("task", {}), task_state.tasks, task_state.latest_versions,
        prefix="final_result_task",
    )
    errors.extend(found)
    role, found = _lookup_current_record(
        result.get("generated_by_role_assignment", {}), role_state.assignments,
        role_state.latest_versions, prefix="final_result_generator_role",
    )
    errors.extend(found)

    goal_payload = goal.get("legal_payload", {}) if goal else {}
    team_payload = team.get("legal_payload", {}) if team else {}
    task_payload = task.get("legal_payload", {}) if task else {}
    role_payload = role.get("legal_payload", {}) if role else {}

    if goal and goal.get("legal_payload_schema") != GOAL_CONTRACT_PAYLOAD_SCHEMA:
        errors.append("final_result_goal_payload_schema_mismatch")
    if team and team.get("legal_payload_schema") != TEAM_PLAN_PAYLOAD_SCHEMA:
        errors.append("final_result_team_payload_schema_mismatch")
    if task and task.get("legal_payload_schema") != TASK_PAYLOAD_SCHEMA:
        errors.append("final_result_task_payload_schema_mismatch")
    if role and role.get("legal_payload_schema") != ROLE_ASSIGNMENT_PAYLOAD_SCHEMA:
        errors.append("final_result_generator_role_payload_schema_mismatch")

    if team_payload and not _same_identity(team_payload.get("goal", {}), result.get("goal", {})):
        errors.append("final_result_team_goal_mismatch")
    if task_payload:
        if task_payload.get("phase") != "synthesis":
            errors.append("final_result_task_phase_not_synthesis")
        if task_payload.get("status") != "in_progress":
            errors.append("final_result_task_not_in_progress")
        if not _same_entity_ref(task_payload.get("assigned_role_assignment"), result.get("generated_by_role_assignment")):
            errors.append("final_result_generator_not_task_assignee")
        if not _same_identity(task_payload.get("goal", {}), result.get("goal", {})):
            errors.append("final_result_task_goal_mismatch")
        if not _same_identity(task_payload.get("team_plan", {}), result.get("team_plan", {})):
            errors.append("final_result_task_team_mismatch")
    if role_payload:
        if role_payload.get("role") not in FINAL_RESULT_MANAGEMENT_ROLES:
            errors.append("final_result_generator_role_not_allowed")
        if role_payload.get("status") != "active":
            errors.append("final_result_generator_role_inactive")
        if "final_result.create" not in set(role_payload.get("authority_scope", {}).get("operations", [])):
            errors.append("final_result_generator_scope_denied")

    expected_deliverables = {
        item.get("deliverable_id") for item in goal_payload.get("deliverables", [])
    }
    actual_deliverables = {
        item.get("deliverable_id") for item in result.get("deliverable_assessments", [])
    }
    if expected_deliverables and actual_deliverables != expected_deliverables:
        errors.append("final_result_deliverable_coverage_mismatch")
    expected_criteria = {
        item.get("criterion_id") for item in goal_payload.get("success_criteria", [])
    }
    actual_criteria = {
        item.get("criterion_id") for item in result.get("success_criteria_assessments", [])
    }
    if expected_criteria and actual_criteria != expected_criteria:
        errors.append("final_result_criterion_coverage_mismatch")

    source_rank = 0
    for ref in result.get("accepted_decisions", []):
        record, found = _lookup_current_record(ref, decision_state.decisions, decision_state.latest_versions, prefix="final_result_decision")
        errors.extend(found)
        if record:
            payload = record.get("legal_payload", {})
            if payload.get("status") != "accepted":
                errors.append("final_result_decision_not_accepted")
            if not _same_identity(payload.get("goal", {}), result.get("goal", {})):
                errors.append("final_result_decision_goal_mismatch")
            source_rank = max(source_rank, _record_classification_rank(record))

    verification_targets: set[EntityVersionKey] = set()
    refutation_targets: set[EntityVersionKey] = set()
    for ref in result.get("verifications", []):
        record, found = _lookup_current_record(ref, verification_state.verifications, verification_state.latest_versions, prefix="final_result_verification")
        errors.extend(found)
        if record:
            payload = record.get("legal_payload", {})
            if payload.get("status") != "active":
                errors.append("final_result_verification_inactive")
            if payload.get("result") == "passed" and payload.get("conclusion_status") == "conclusive":
                verification_targets.update(_ref_identities(payload.get("target_claims", [])))
            if payload.get("result") == "failed" and payload.get("conclusion_status") == "conclusive":
                refutation_targets.update(_ref_identities(payload.get("target_claims", [])))
            source_rank = max(source_rank, _record_classification_rank(record))

    for ref in result.get("verified_claims", []):
        record, found = _lookup_current_record(ref, contribution_state.contributions, contribution_state.latest_versions, prefix="final_result_verified_claim")
        errors.extend(found)
        key = _entity_ref_key(ref)
        if record:
            payload = record.get("legal_payload", {})
            if payload.get("contribution_type") != "claim" or payload.get("status") != "active":
                errors.append("final_result_verified_claim_invalid")
            source_rank = max(source_rank, _record_classification_rank(record))
        if key not in verification_targets:
            errors.append("final_result_verified_claim_not_conclusively_passed")

    for ref in result.get("refuted_claims", []):
        record, found = _lookup_current_record(ref, contribution_state.contributions, contribution_state.latest_versions, prefix="final_result_refuted_claim")
        errors.extend(found)
        key = _entity_ref_key(ref)
        if record:
            payload = record.get("legal_payload", {})
            if payload.get("contribution_type") != "claim" or payload.get("status") != "active":
                errors.append("final_result_refuted_claim_invalid")
            source_rank = max(source_rank, _record_classification_rank(record))
        if key not in refutation_targets:
            errors.append("final_result_refuted_claim_not_conclusively_failed")

    for item in result.get("unverified_claims", []):
        ref = item.get("claim", {})
        record, found = _lookup_current_record(ref, contribution_state.contributions, contribution_state.latest_versions, prefix="final_result_unverified_claim")
        errors.extend(found)
        if record and record.get("legal_payload", {}).get("contribution_type") != "claim":
            errors.append("final_result_unverified_item_not_claim")
        if record:
            source_rank = max(source_rank, _record_classification_rank(record))

    for ref in result.get("evidence", []):
        record, found = _lookup_current_record(ref, evidence_state.evidence, evidence_state.latest_versions, prefix="final_result_evidence")
        errors.extend(found)
        if record:
            if record.get("legal_payload", {}).get("status") != "active":
                errors.append("final_result_evidence_inactive")
            source_rank = max(source_rank, _record_classification_rank(record))

    for ref in result.get("reviews", []):
        record, found = _lookup_current_record(ref, review_state.reviews, review_state.latest_versions, prefix="final_result_review")
        errors.extend(found)
        if record:
            if result.get("result_status") == "succeeded" and record.get("legal_payload", {}).get("result") != "approved":
                errors.append("final_result_succeeded_with_nonapproved_review")
            source_rank = max(source_rank, _record_classification_rank(record))

    closed_challenge_statuses = {"resolved", "rejected"}
    for field_name, expect_closed in (("resolved_challenges", True), ("unresolved_challenges", False)):
        for ref in result.get(field_name, []):
            record, found = _lookup_current_record(ref, challenge_state.challenges, challenge_state.latest_versions, prefix="final_result_challenge")
            errors.extend(found)
            if record:
                status = record.get("legal_payload", {}).get("status")
                if expect_closed and status not in closed_challenge_statuses:
                    errors.append("final_result_resolved_challenge_not_closed")
                if not expect_closed and status in closed_challenge_statuses:
                    errors.append("final_result_unresolved_challenge_closed")
                if result.get("result_status") == "succeeded" and record.get("legal_payload", {}).get("severity") == "critical" and status not in closed_challenge_statuses:
                    errors.append("final_result_succeeded_with_critical_challenge")
                source_rank = max(source_rank, _record_classification_rank(record))

    now = _parse_datetime(generated_at)
    for ref in result.get("approvals", []):
        record, found = _lookup_current_record(ref, approval_state.approvals, approval_state.latest_versions, prefix="final_result_approval")
        errors.extend(found)
        if record:
            payload = record.get("legal_payload", {})
            if payload.get("status") != "active" or payload.get("outcome") != "approved":
                errors.append("final_result_approval_not_effective")
            expires = payload.get("expires_at")
            if isinstance(expires, str) and now >= _parse_datetime(expires):
                errors.append("final_result_approval_expired")
            source_rank = max(source_rank, _record_classification_rank(record))

    plan_ref = result.get("resource_summary", {}).get("execution_resource_plan", {})
    plan, found = _lookup_current_record(plan_ref, resource_plan_state.plans, resource_plan_state.latest_versions, prefix="final_result_resource_plan")
    errors.extend(found)
    if plan:
        plan_payload = plan.get("legal_payload", {})
        if plan_payload.get("status") not in {"active", "completed"}:
            errors.append("final_result_resource_plan_inactive")
        declared = {item.get("resource_id"): item for item in plan_payload.get("resources", [])}
        for item in result.get("resource_summary", {}).get("resource_outcomes", []):
            source = declared.get(item.get("resource_id"))
            if source is None:
                errors.append("final_result_resource_not_in_plan")
            elif item.get("final_quota_status") != source.get("quota", {}).get("status"):
                errors.append("final_result_resource_quota_status_mismatch")

    if protocol_envelopes is not None:
        replayed, replay_errors = replay_protocol_log(protocol_envelopes)
        errors.extend(f"final_result_log_{code}" for code in replay_errors)
        anchor = result.get("log_anchor", {})
        if anchor.get("last_sequence") != replayed.last_sequence:
            errors.append("final_result_log_anchor_sequence_mismatch")
        if anchor.get("event_count") != len(protocol_envelopes):
            errors.append("final_result_log_anchor_count_mismatch")
        if protocol_envelopes and anchor.get("last_event_id") != protocol_envelopes[-1].get("message_id"):
            errors.append("final_result_log_anchor_event_mismatch")

    result_rank = CONTRIBUTION_CLASSIFICATION_RANK.get(result.get("classification"), -1)
    if result_rank < source_rank:
        errors.append("final_result_classification_below_sources")
    return errors
