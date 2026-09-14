"""Deterministic end-to-end demo used by the W1 Nexus CLI.

The demo intentionally uses scripted providers and generic artifact envelopes so
it can run without credentials or network access.  It exercises the operational
runtime: append-only persistence, quota-aware fallback, context minimisation,
independent verification/review, decision/approval, synthesis, replay, and
integrity verification.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Callable, Mapping

from .orchestrator import (
    ArtifactEnvelopeFactory,
    CompiledTask,
    CompiledWorkflow,
    OrchestratorCore,
    OrchestratorJournal,
    OrchestratorRunResult,
    ProviderQuotaExhausted,
    ProviderResponse,
    RunInputs,
    RuntimeEvent,
    ScriptedProvider,
)
from .session_store import SessionStore

TRUSTED_RECORDER = ("runtime", "runtime-local-001")
DEMO_SESSION_ID = "session-w1-reference-demo-001"
DEMO_RUN_ID = "run-w1-reference-demo-001"


def bootstrap_events() -> list[dict[str, Any]]:
    first: dict[str, Any] = {
        "protocol": "w1-cip",
        "protocol_version": "0.1",
        "message_id": "evt-w1-demo-bootstrap-001",
        "session_id": DEMO_SESSION_ID,
        "kind": "event",
        "event_class": "protocol_event",
        "type": "session.bootstrap.completed",
        "actor": {"principal_type": "runtime", "principal_id": "runtime-local-001"},
        "recorded_by": {"principal_type": "runtime", "principal_id": "runtime-local-001"},
        "occurred_at": "2026-08-05T17:00:00Z",
        "received_at": "2026-08-05T17:00:00Z",
        "recorded_at": "2026-08-05T17:00:00Z",
        "correlation_id": "w1-reference-demo",
        "related_events": [],
        "entity": {
            "entity_type": "role_assignment",
            "entity_id": "demo-owner",
            "entity_version": 1,
        },
        "precondition": {
            "mode": "create",
            "expected_absent": True,
            "target_identity": {
                "entity_type": "role_assignment",
                "entity_id": "demo-owner",
            },
        },
        "bootstrap": {
            "mode": "local_runtime",
            "bootstrap_id": "bootstrap-w1-reference-demo",
            "scope": "create_initial_role_assignment",
        },
        "payload_schema": "urn:w1-cip:schema:0.1:versioned-entity",
        "payload": {
            "entity": {
                "entity_type": "role_assignment",
                "entity_id": "demo-owner",
                "entity_version": 1,
            },
            "created_by_event_id": "evt-w1-demo-bootstrap-001",
            "legal_payload_schema": "urn:w1-cip:entity-payload:0.1:role-assignment",
            "legal_payload": {
                "subject": {"principal_type": "human", "principal_id": "human-owner-001"},
                "role": "human_owner",
                "authority_scope": {
                    "operations": ["role_assignment.create", "role_assignment.next_version"]
                },
                "status": "active",
                "valid_from": "2026-08-05T17:00:00Z",
                "assignment_reason": "reference demo bootstrap owner",
            },
        },
    }
    second = deepcopy(first)
    second.update(
        {
            "message_id": "evt-w1-demo-owner-002",
            "type": "entity.version.created",
            "actor": {"principal_type": "human", "principal_id": "human-owner-001"},
            "authorized_by": {
                "entity_type": "role_assignment",
                "entity_id": "demo-owner",
                "entity_version": 1,
            },
            "occurred_at": "2026-08-05T17:00:01Z",
            "received_at": "2026-08-05T17:00:01Z",
            "recorded_at": "2026-08-05T17:00:01Z",
            "causation_id": "evt-w1-demo-bootstrap-001",
            "entity": {
                "entity_type": "role_assignment",
                "entity_id": "demo-owner",
                "entity_version": 2,
            },
            "precondition": {
                "mode": "next_version",
                "expected_entity_version": 1,
                "target": {
                    "entity_type": "role_assignment",
                    "entity_id": "demo-owner",
                    "entity_version": 1,
                },
            },
        }
    )
    second.pop("bootstrap", None)
    second["payload"] = {
        "entity": {
            "entity_type": "role_assignment",
            "entity_id": "demo-owner",
            "entity_version": 2,
        },
        "created_by_event_id": "evt-w1-demo-owner-002",
        "legal_payload_schema": "urn:w1-cip:entity-payload:0.1:role-assignment",
        "legal_payload": {
            "subject": {"principal_type": "human", "principal_id": "human-owner-001"},
            "role": "human_owner",
            "authority_scope": {
                "operations": [
                    "role_assignment.create",
                    "role_assignment.next_version",
                    "artifact.create",
                ]
            },
            "status": "active",
            "valid_from": "2026-08-05T17:00:00Z",
            "assignment_reason": "reference demo owner with trace authority",
        },
        "previous_version": {
            "entity_type": "role_assignment",
            "entity_id": "demo-owner",
            "entity_version": 1,
        },
        "change_metadata": {
            "change_reason": "semantic",
            "substantive_effect": "may_affect_dependents",
        },
    }
    return [first, second]


def workflow() -> CompiledWorkflow:
    return CompiledWorkflow(
        tasks=(
            CompiledTask(
                task_id="prepare-proposal",
                title="Prepare a constrained engineering proposal",
                phase="execution",
                role="executor",
                expected_output_type="contribution",
                required_context_fields=("supply-voltage", "startup-current"),
                estimated_units=2,
                domains=("electronics",),
            ),
            CompiledTask(
                task_id="collect-evidence",
                title="Collect evidence for the proposal",
                phase="verification",
                role="verifier",
                expected_output_type="evidence",
                depends_on=("prepare-proposal",),
                estimated_units=1,
                domains=("electronics",),
            ),
            CompiledTask(
                task_id="verify-proposal",
                title="Verify the proposal against constraints",
                phase="verification",
                role="verifier",
                expected_output_type="verification",
                depends_on=("collect-evidence",),
                estimated_units=1,
                domains=("electronics",),
            ),
            CompiledTask(
                task_id="review-solution-package",
                title="Independently review the complete package",
                phase="review",
                role="reviewer",
                expected_output_type="review",
                depends_on=("verify-proposal",),
                estimated_units=1,
                domains=("electronics",),
            ),
            CompiledTask(
                task_id="decide-driver-selection",
                title="Select the accepted option",
                phase="approval",
                role="decision_authority",
                expected_output_type="decision",
                depends_on=("review-solution-package",),
                estimated_units=1,
                domains=("electronics",),
            ),
            CompiledTask(
                task_id="approve-final-decisions",
                title="Approve the selected decision",
                phase="approval",
                role="decision_authority",
                expected_output_type="approval",
                depends_on=("decide-driver-selection",),
                estimated_units=1,
                domains=("electronics",),
            ),
            CompiledTask(
                task_id="synthesize-final-result",
                title="Synthesize the verified final result",
                phase="synthesis",
                role="synthesizer",
                expected_output_type="final_result",
                depends_on=("approve-final-decisions",),
                estimated_units=1,
                domains=("electronics",),
            ),
        ),
        risk_level="medium",
        error_cost="material",
        task_complexity="bounded",
    )


def _resource(resource_id: str, *, remaining: int = 20, reserved: int = 0) -> dict[str, Any]:
    return {
        "resource_id": resource_id,
        "quota": {
            "accounting_scope": "user_account",
            "meter": "requests",
            "window": "day",
            "status": "available",
            "limit_units": 20,
            "remaining_units": remaining,
            "reserved_units": reserved,
            "observed_at": "2026-08-05T17:00:00Z",
            "reset_at": "2026-08-06T17:00:00Z",
            "source": "reference_demo",
        },
    }


def _rule(
    rule_id: str,
    *,
    role: str,
    phase: str,
    primary: str,
    fallback: str | None = None,
    primary_score: int = 96,
    fallback_score: int = 88,
) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = [
        {
            "resource_id": primary,
            "mode": "primary",
            "priority": 1,
            "fitness_score": primary_score,
            "assessment_source": "human_assessment",
            "assessment_confidence": "medium",
            "assessment_basis": "Reference-demo task-specific assessment.",
            "minimum_remaining_units": 1,
        }
    ]
    if fallback:
        candidates.append(
            {
                "resource_id": fallback,
                "mode": "fallback",
                "priority": 2,
                "fitness_score": fallback_score,
                "assessment_source": "human_assessment",
                "assessment_confidence": "medium",
                "assessment_basis": "Reference-demo fallback assessment.",
                "minimum_remaining_units": 1,
            }
        )
    return {
        "rule_id": rule_id,
        "applies_to": {
            "roles": [role],
            "phases": [phase],
            "risk_levels": ["medium"],
            "task_complexities": ["bounded"],
            "domains": [],
        },
        "selection_objective": "balanced",
        "minimum_fitness_score": 90,
        "active_resource_id": primary,
        "activation_reason": "initial_selection",
        "candidates": candidates,
        "on_primary_unavailable": "activate_fallback" if fallback else "block",
        "below_floor_policy": "allow_with_extra_review",
        "additional_review_required": False,
        "routing_status": "active",
    }


def resource_plan() -> dict[str, Any]:
    return {
        "resources": [
            _resource("frontier-primary", remaining=2),
            _resource("balanced-fallback"),
            _resource("independent-verifier", reserved=2),
            _resource("independent-reviewer", reserved=2),
            _resource("decision-authority", reserved=2),
            _resource("synthesis-resource", reserved=2),
        ],
        "routing_rules": [
            _rule(
                "executor-route",
                role="executor",
                phase="execution",
                primary="frontier-primary",
                fallback="balanced-fallback",
            ),
            _rule(
                "verifier-route",
                role="verifier",
                phase="verification",
                primary="independent-verifier",
            ),
            _rule(
                "reviewer-route",
                role="reviewer",
                phase="review",
                primary="independent-reviewer",
            ),
            _rule(
                "decision-route",
                role="decision_authority",
                phase="approval",
                primary="decision-authority",
            ),
            _rule(
                "synthesis-route",
                role="synthesizer",
                phase="synthesis",
                primary="synthesis-resource",
            ),
        ],
        "reserve_policy": {
            "protected_phases": ["verification", "review", "approval", "synthesis"]
        },
        "heterogeneity_policy": {
            "model_count_voting": "prohibited",
            "decision_basis": "authority_evidence_verification",
            "fitness_scope": "task_specific",
            "silent_downgrade": "prohibited",
        },
        "fallback_policy": {
            "all_candidates_unavailable": "await_reset",
            "cross_provider_allowed": True,
            "disclosure_required": True,
            "preserve_artifacts": True,
        },
        "status": "active",
    }


def providers() -> dict[str, ScriptedProvider]:
    return {
        "frontier-primary": ScriptedProvider(
            "frontier-primary",
            [ProviderQuotaExhausted("2026-08-06T17:00:00Z")],
        ),
        "balanced-fallback": ScriptedProvider(
            "balanced-fallback",
            [
                ProviderResponse(
                    output_type="contribution",
                    payload={
                        "proposal": "candidate-b",
                        "reason": "Meets voltage and startup-current constraints.",
                    },
                    context_fields_used=("supply-voltage", "startup-current"),
                )
            ],
        ),
        "independent-verifier": ScriptedProvider(
            "independent-verifier",
            [
                ProviderResponse(
                    output_type="evidence",
                    payload={"source": "synthetic-datasheet", "peak-current": 20},
                ),
                ProviderResponse(
                    output_type="verification",
                    payload={"result": "passed", "conclusion_status": "conclusive"},
                ),
            ],
        ),
        "independent-reviewer": ScriptedProvider(
            "independent-reviewer",
            [
                ProviderResponse(
                    output_type="review",
                    payload={"result": "approved", "independent": True},
                )
            ],
        ),
        "decision-authority": ScriptedProvider(
            "decision-authority",
            [
                ProviderResponse(
                    output_type="decision",
                    payload={"selected_option": "candidate-b", "status": "accepted"},
                ),
                ProviderResponse(
                    output_type="approval",
                    payload={"decision": "approved", "scope": "driver-selection"},
                ),
            ],
        ),
        "synthesis-resource": ScriptedProvider(
            "synthesis-resource",
            [
                ProviderResponse(
                    output_type="final_result",
                    payload={
                        "result_status": "succeeded",
                        "selected_option": "candidate-b",
                        "verified": True,
                        "limitations": ["Synthetic reference data only."],
                    },
                )
            ],
        ),
    }


def run_reference_demo(
    root: str | Path,
    *,
    event_listener: Callable[[RuntimeEvent], None] | None = None,
    reset: bool = False,
) -> tuple[OrchestratorRunResult, dict[str, Any]]:
    root_path = Path(root)
    state = root_path / ".w1nexus"
    state.mkdir(parents=True, exist_ok=True)
    session_path = state / "session.sqlite3"
    journal_path = state / "orchestrator.sqlite3"
    if reset:
        for path in (session_path, journal_path):
            if path.exists():
                path.unlink()

    with SessionStore(
        session_path,
        trusted_recorders={TRUSTED_RECORDER},
    ) as store, OrchestratorJournal(journal_path) as journal:
        if not store.list_sessions():
            for envelope in bootstrap_events():
                store.append(envelope)

        factory = ArtifactEnvelopeFactory(
            actor={"principal_type": "human", "principal_id": "human-owner-001"},
            authorized_by={
                "entity_type": "role_assignment",
                "entity_id": "demo-owner",
                "entity_version": 2,
            },
            recorded_by={"principal_type": "runtime", "principal_id": "runtime-local-001"},
            correlation_id="w1-reference-demo",
        )
        core = OrchestratorCore(
            session_store=store,
            journal=journal,
            providers=providers(),
            envelope_factory=factory,
            event_listener=event_listener,
        )
        result = core.run(
            RunInputs(
                run_id=DEMO_RUN_ID,
                session_id=DEMO_SESSION_ID,
                goal={},
                team_plan={"budget": {"max_model_calls": 12}},
                resource_plan=resource_plan(),
                context={
                    "supply-voltage": 12,
                    "startup-current": 18,
                    "private-note": "must never leave the context minimizer",
                },
                domains=frozenset({"electronics"}),
                max_attempts_per_resource=2,
            ),
            workflow=workflow(),
        )
        integrity = store.verify_integrity(DEMO_SESSION_ID)
        audit = {
            "session_id": DEMO_SESSION_ID,
            "run_id": DEMO_RUN_ID,
            "session_integrity": integrity.valid,
            "event_count": store.get_summary(DEMO_SESSION_ID).event_count,
            "active_protocol_effects": store.get_active_protocol_effects(DEMO_SESSION_ID),
            "quota_observations": journal.quota_observations(DEMO_RUN_ID),
            "started_calls": journal.count_started_calls(DEMO_RUN_ID),
        }
        return result, audit
