"""Run a compact OrchestratorCore demo with quota fallback and persistence."""

from __future__ import annotations

import json
import tempfile
from copy import deepcopy
from pathlib import Path

from w1cip.orchestrator import (
    ArtifactEnvelopeFactory,
    CompiledTask,
    CompiledWorkflow,
    OrchestratorCore,
    OrchestratorJournal,
    ProviderQuotaExhausted,
    ProviderResponse,
    RunInputs,
    ScriptedProvider,
)
from w1cip.session_store import SessionStore


def bootstrap_events() -> list[dict]:
    first = {
        "protocol": "w1-cip",
        "protocol_version": "0.1",
        "message_id": "evt-demo-bootstrap-001",
        "session_id": "session-orchestrator-demo-001",
        "kind": "event",
        "event_class": "protocol_event",
        "type": "session.bootstrap.completed",
        "actor": {"principal_type": "runtime", "principal_id": "runtime-local-001"},
        "recorded_by": {"principal_type": "runtime", "principal_id": "runtime-local-001"},
        "occurred_at": "2026-08-05T15:00:00Z",
        "received_at": "2026-08-05T15:00:00Z",
        "recorded_at": "2026-08-05T15:00:00Z",
        "correlation_id": "orchestrator-demo",
        "related_events": [],
        "entity": {"entity_type": "role_assignment", "entity_id": "owner", "entity_version": 1},
        "precondition": {
            "mode": "create",
            "expected_absent": True,
            "target_identity": {"entity_type": "role_assignment", "entity_id": "owner"},
        },
        "bootstrap": {
            "mode": "local_runtime",
            "bootstrap_id": "bootstrap-orchestrator-demo",
            "scope": "create_initial_role_assignment",
        },
        "payload_schema": "urn:w1-cip:schema:0.1:versioned-entity",
        "payload": {
            "entity": {"entity_type": "role_assignment", "entity_id": "owner", "entity_version": 1},
            "created_by_event_id": "evt-demo-bootstrap-001",
            "legal_payload_schema": "urn:w1-cip:entity-payload:0.1:role-assignment",
            "legal_payload": {
                "subject": {"principal_type": "human", "principal_id": "human-owner-001"},
                "role": "human_owner",
                "authority_scope": {
                    "operations": ["role_assignment.create", "role_assignment.next_version"]
                },
                "status": "active",
                "valid_from": "2026-08-05T15:00:00Z",
                "assignment_reason": "demo bootstrap owner",
            },
        },
    }
    second = deepcopy(first)
    second.update(
        {
            "message_id": "evt-demo-owner-002",
            "type": "entity.version.created",
            "actor": {"principal_type": "human", "principal_id": "human-owner-001"},
            "authorized_by": {
                "entity_type": "role_assignment",
                "entity_id": "owner",
                "entity_version": 1,
            },
            "occurred_at": "2026-08-05T15:00:01Z",
            "received_at": "2026-08-05T15:00:01Z",
            "recorded_at": "2026-08-05T15:00:01Z",
            "causation_id": "evt-demo-bootstrap-001",
            "entity": {"entity_type": "role_assignment", "entity_id": "owner", "entity_version": 2},
            "precondition": {
                "mode": "next_version",
                "expected_entity_version": 1,
                "target": {"entity_type": "role_assignment", "entity_id": "owner", "entity_version": 1},
            },
        }
    )
    second.pop("bootstrap", None)
    second["payload"] = {
        "entity": {"entity_type": "role_assignment", "entity_id": "owner", "entity_version": 2},
        "created_by_event_id": "evt-demo-owner-002",
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
            "valid_from": "2026-08-05T15:00:00Z",
            "assignment_reason": "demo owner with artifact authority",
        },
        "previous_version": {"entity_type": "role_assignment", "entity_id": "owner", "entity_version": 1},
        "change_metadata": {
            "change_reason": "semantic",
            "substantive_effect": "may_affect_dependents",
        },
    }
    return [first, second]


def plan() -> dict:
    def resource(resource_id: str) -> dict:
        return {
            "resource_id": resource_id,
            "quota": {
                "status": "available",
                "limit_units": 10,
                "remaining_units": 10,
                "reserved_units": 0,
                "observed_at": "2026-08-05T15:00:00Z",
                "reset_at": "2026-08-06T15:00:00Z",
            },
        }

    return {
        "resources": [resource("strong"), resource("fallback")],
        "routing_rules": [
            {
                "rule_id": "execution-route",
                "applies_to": {
                    "roles": ["executor"],
                    "phases": ["execution"],
                    "risk_levels": ["medium"],
                    "task_complexities": ["bounded"],
                    "domains": [],
                },
                "selection_objective": "balanced",
                "minimum_fitness_score": 80,
                "active_resource_id": "strong",
                "activation_reason": "initial_selection",
                "candidates": [
                    {
                        "resource_id": "strong",
                        "mode": "primary",
                        "priority": 1,
                        "fitness_score": 96,
                        "minimum_remaining_units": 1,
                    },
                    {
                        "resource_id": "fallback",
                        "mode": "fallback",
                        "priority": 2,
                        "fitness_score": 96,
                        "minimum_remaining_units": 1,
                    },
                ],
                "on_primary_unavailable": "activate_fallback",
                "below_floor_policy": "allow_with_extra_review",
                "additional_review_required": False,
                "routing_status": "active",
            }
        ],
        "reserve_policy": {"protected_phases": []},
        "fallback_policy": {"all_candidates_unavailable": "await_reset"},
        "status": "active",
    }


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        with SessionStore(
            root / "session.sqlite3",
            trusted_recorders={("runtime", "runtime-local-001")},
        ) as store, OrchestratorJournal(root / "journal.sqlite3") as journal:
            for envelope in bootstrap_events():
                store.append(envelope)

            workflow = CompiledWorkflow(
                tasks=(
                    CompiledTask(
                        task_id="prepare-proposal",
                        title="Prepare proposal",
                        phase="execution",
                        role="executor",
                        expected_output_type="contribution",
                        required_context_fields=("supply-voltage",),
                    ),
                ),
                risk_level="medium",
                error_cost="material",
                task_complexity="bounded",
            )
            providers = {
                "strong": ScriptedProvider("strong", [ProviderQuotaExhausted()]),
                "fallback": ScriptedProvider(
                    "fallback",
                    [
                        ProviderResponse(
                            output_type="contribution",
                            payload={"candidate": "candidate-b"},
                            context_fields_used=("supply-voltage",),
                        )
                    ],
                ),
            }
            factory = ArtifactEnvelopeFactory(
                actor={"principal_type": "human", "principal_id": "human-owner-001"},
                authorized_by={
                    "entity_type": "role_assignment",
                    "entity_id": "owner",
                    "entity_version": 2,
                },
                recorded_by={"principal_type": "runtime", "principal_id": "runtime-local-001"},
                correlation_id="orchestrator-demo",
            )
            core = OrchestratorCore(
                session_store=store,
                journal=journal,
                providers=providers,
                envelope_factory=factory,
            )
            result = core.run(
                RunInputs(
                    run_id="orchestrator-demo-run",
                    session_id="session-orchestrator-demo-001",
                    goal={},
                    team_plan={"budget": {"max_model_calls": 4}},
                    resource_plan=plan(),
                    context={"supply-voltage": 12, "private-note": "not granted"},
                ),
                workflow=workflow,
            )
            print(
                json.dumps(
                    {
                        "status": result.status,
                        "tasks": [task.__dict__ for task in result.tasks],
                        "disclosures": result.disclosures,
                        "session_integrity": store.verify_integrity(result.session_id).valid,
                    },
                    ensure_ascii=False,
                    indent=2,
                    default=list,
                )
            )


if __name__ == "__main__":
    main()
