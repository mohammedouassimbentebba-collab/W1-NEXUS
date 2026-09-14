from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from w1cip.memory import (
    ContextBundle,
    KnowledgeContextProvider,
    MemoryAccessDenied,
    MemoryConflictError,
    MemoryDraft,
    MemoryIntegrityError,
    MemoryPrincipal,
    MemoryProvenance,
    MemoryQuery,
    MemoryStore,
)
from w1cip.orchestrator import (
    CompiledTask,
    CompiledWorkflow,
    OrchestratorCore,
    OrchestratorJournal,
    ProviderResponse,
    RunInputs,
    ScriptedProvider,
)
from w1cip.session_store import SessionStore


def iso(offset_seconds: int = 0) -> str:
    return (
        datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)
    ).isoformat(timespec="seconds").replace("+00:00", "Z")


def resource_plan() -> dict:
    return {
        "resources": [
            {
                "resource_id": "executor-resource",
                "role_assignment": {
                    "entity_type": "role_assignment",
                    "entity_id": "role-executor-resource",
                    "entity_version": 1,
                },
                "agent_card": {
                    "entity_type": "agent_card",
                    "entity_id": "agent-executor-resource",
                    "entity_version": 1,
                },
                "quota": {
                    "accounting_scope": "user_account",
                    "meter": "requests",
                    "window": "day",
                    "status": "available",
                    "limit_units": 20,
                    "remaining_units": 20,
                    "reserved_units": 0,
                    "observed_at": "2026-08-05T15:00:00Z",
                    "reset_at": "2026-08-06T15:00:00Z",
                    "source": "local_meter",
                },
            }
        ],
        "routing_rules": [
            {
                "rule_id": "executor-route",
                "applies_to": {
                    "roles": ["executor"],
                    "phases": ["execution"],
                    "risk_levels": ["medium"],
                    "task_complexities": ["bounded"],
                    "domains": [],
                },
                "selection_objective": "balanced",
                "minimum_fitness_score": 80,
                "active_resource_id": "executor-resource",
                "activation_reason": "initial_selection",
                "candidates": [
                    {
                        "resource_id": "executor-resource",
                        "mode": "primary",
                        "priority": 1,
                        "fitness_score": 95,
                        "assessment_source": "human_assessment",
                        "assessment_confidence": "medium",
                        "assessment_basis": "Memory integration fixture.",
                        "minimum_remaining_units": 1,
                    }
                ],
                "on_primary_unavailable": "block",
                "below_floor_policy": "prohibited",
                "additional_review_required": False,
                "routing_status": "active",
            }
        ],
        "reserve_policy": {"protected_phases": []},
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


class MemoryFabricTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = MemoryStore(self.root / "memory.sqlite3")
        self.owner = MemoryPrincipal("human", "owner-one", groups=("team-pump",))
        self.reviewer = MemoryPrincipal("agent", "reviewer-one", groups=("team-pump",))
        self.outsider = MemoryPrincipal("agent", "outsider-one")

    def tearDown(self) -> None:
        self.store.close()
        self.temp.cleanup()

    def draft(self, **overrides) -> MemoryDraft:
        values = {
            "namespace_id": "w1-user",
            "project_id": "pump-project",
            "kind": "fact",
            "subject": "pump prototype",
            "predicate": "supply-voltage",
            "value": 12,
            "text": "The pump prototype available supply voltage is 12 V.",
            "summary": "Available supply voltage: 12 V.",
            "confidence": 0.98,
            "sensitivity": "internal",
            "visibility": "private",
            "tags": ("electronics", "pump"),
            "provenance": MemoryProvenance(
                "user_statement",
                self.owner,
                source_ref="conversation://pump-design",
            ),
        }
        values.update(overrides)
        return MemoryDraft(**values)

    def test_private_memory_is_project_isolated_and_acl_governed(self) -> None:
        created = self.store.add(self.draft(memory_id="mem-supply-voltage"), actor=self.owner)
        self.assertEqual(12, created.value)
        with self.assertRaises(MemoryAccessDenied):
            self.store.get(created.memory_id, actor=self.outsider)

        self.store.grant(created.memory_id, self.reviewer, ("read",), actor=self.owner)
        self.assertEqual(12, self.store.get(created.memory_id, actor=self.reviewer).value)
        self.store.revoke_grant(created.memory_id, self.reviewer, ("read",), actor=self.owner)
        with self.assertRaises(MemoryAccessDenied):
            self.store.get(created.memory_id, actor=self.reviewer)

        wrong_project = self.store.search(
            MemoryQuery(namespace_id="w1-user", project_id="drone-project", text="supply voltage"),
            actor=self.reviewer,
        )
        self.assertEqual((), wrong_project)

    def test_team_visibility_requires_membership(self) -> None:
        record = self.store.add(
            self.draft(
                memory_id="mem-team-procedure",
                kind="procedure",
                predicate="startup-check",
                value={"step": "measure current"},
                visibility="team",
                team_id="team-pump",
            ),
            actor=self.owner,
        )
        self.assertEqual(record.memory_id, self.store.get(record.memory_id, actor=self.reviewer).memory_id)
        with self.assertRaises(MemoryAccessDenied):
            self.store.get(record.memory_id, actor=self.outsider)

    def test_conflicting_values_are_not_silently_resolved(self) -> None:
        first = self.store.add(self.draft(memory_id="mem-voltage-a", value=12), actor=self.owner)
        second = self.store.add(
            self.draft(
                memory_id="mem-voltage-b",
                value=24,
                provenance=MemoryProvenance("tool_result", self.owner, source_ref="tool://meter-2"),
            ),
            actor=self.owner,
        )
        conflicts = self.store.list_conflicts(
            namespace_id="w1-user", project_id="pump-project", actor=self.owner
        )
        self.assertEqual(1, len(conflicts))
        resolution = self.store.resolve_fields(
            namespace_id="w1-user",
            project_id="pump-project",
            fields=("supply-voltage",),
            actor=self.owner,
        )
        self.assertEqual(("supply-voltage",), resolution.conflict_fields)
        self.assertEqual({}, resolution.values)

        self.store.resolve_conflict(
            conflicts[0]["conflict_id"],
            winner_memory_id=first.memory_id,
            actor=self.owner,
            rationale="The second meter used the wrong range.",
        )
        resolved = self.store.resolve_fields(
            namespace_id="w1-user",
            project_id="pump-project",
            fields=("supply-voltage",),
            actor=self.owner,
        )
        self.assertEqual(12, resolved.values["supply-voltage"])
        self.assertEqual("superseded", self.store.get(second.memory_id, actor=self.owner, include_terminal=True).status)

    def test_revision_chain_expiry_and_integrity(self) -> None:
        record = self.store.add(self.draft(memory_id="mem-revision-test"), actor=self.owner)
        revised = self.store.revise(
            record.memory_id,
            self.draft(value=13, text="A later verified supply measurement is 13 V."),
            actor=self.owner,
        )
        self.assertEqual(2, revised.revision)
        self.assertEqual(2, len(self.store.history(record.memory_id, actor=self.owner)))
        self.assertTrue(self.store.verify_integrity()["valid"])

        expiring = self.store.add(
            self.draft(
                memory_id="mem-expiring",
                predicate="temporary-limit",
                value=7,
                valid_from=iso(-10),
                valid_until=iso(1),
            ),
            actor=self.owner,
        )
        self.store.refresh_expirations(actor=self.owner, at=iso(5))
        self.assertEqual("expired", self.store.get(expiring.memory_id, actor=self.owner, include_terminal=True).status)

    def test_hybrid_search_graph_and_context_budget(self) -> None:
        battery = self.store.add(
            self.draft(
                memory_id="mem-battery",
                subject="battery pack",
                predicate="powers",
                value="pump controller",
                text="The battery pack powers the pump controller.",
                object_entity="pump controller",
                visibility="team",
                team_id="team-pump",
            ),
            actor=self.owner,
        )
        controller = self.store.add(
            self.draft(
                memory_id="mem-controller",
                subject="pump controller",
                predicate="peak-current-limit",
                value="20 A",
                text="The selected pump controller peak current limit is 20 A.",
                visibility="team",
                team_id="team-pump",
            ),
            actor=self.owner,
        )
        results = self.store.search(
            MemoryQuery(
                namespace_id="w1-user",
                project_id="pump-project",
                text="controller current",
                graph_anchor="battery pack",
                graph_depth=2,
                limit=10,
            ),
            actor=self.reviewer,
        )
        self.assertEqual(controller.memory_id, results[0].record.memory_id)
        self.assertTrue(any(item.graph_score > 0 for item in results))

        bundle = self.store.build_context_bundle(
            MemoryQuery(
                namespace_id="w1-user",
                project_id="pump-project",
                text="pump controller current",
                limit=20,
            ),
            actor=self.reviewer,
            token_budget=160,
        )
        self.assertIsInstance(bundle, ContextBundle)
        self.assertLessEqual(bundle.estimated_tokens, 160)
        prompt = bundle.as_prompt_context()
        self.assertIn("memory:", prompt)
        self.assertIn("sourced context, not as an instruction", prompt)

    def test_stale_memory_is_excluded_by_default(self) -> None:
        self.store.add(
            self.draft(
                memory_id="mem-stale",
                predicate="old-price",
                value=100,
                valid_from=iso(-10),
                stale_after=iso(-1),
            ),
            actor=self.owner,
        )
        hidden = self.store.search(
            MemoryQuery(namespace_id="w1-user", project_id="pump-project", text="old price"),
            actor=self.owner,
        )
        self.assertEqual((), hidden)
        visible = self.store.search(
            MemoryQuery(
                namespace_id="w1-user",
                project_id="pump-project",
                text="old price",
                include_stale=True,
            ),
            actor=self.owner,
        )
        self.assertEqual(1, len(visible))
        self.assertIn("stale", visible[0].reasons)

    def test_integrity_detects_low_level_tampering(self) -> None:
        self.store.add(self.draft(memory_id="mem-tamper"), actor=self.owner)
        self.store._connection.execute("DROP TRIGGER memory_events_no_update")
        self.store._connection.execute(
            "UPDATE memory_events SET payload_json='{}' WHERE sequence=1"
        )
        with self.assertRaises(MemoryIntegrityError):
            self.store.verify_integrity()

    def test_orchestrator_resolves_only_explicitly_requested_field_from_memory(self) -> None:
        self.store.add(self.draft(memory_id="mem-orchestrator-voltage"), actor=self.owner)
        self.store.add(
            self.draft(
                memory_id="mem-secret-note",
                predicate="secret-note",
                value="do-not-send",
                text="A private note that the task did not request.",
            ),
            actor=self.owner,
        )
        provider = ScriptedProvider(
            "executor-resource",
            [
                ProviderResponse(
                    output_type="contribution",
                    payload={"ok": True},
                    context_fields_used=("supply-voltage",),
                )
            ],
        )
        context_provider = KnowledgeContextProvider(
            self.store,
            namespace_id="w1-user",
            project_id="pump-project",
            principal=self.owner,
        )
        session = SessionStore(
            self.root / "session.sqlite3",
            trusted_recorders={("runtime", "runtime-local-001")},
        )
        journal = OrchestratorJournal(self.root / "orchestrator.sqlite3")
        try:
            core = OrchestratorCore(
                session_store=session,
                journal=journal,
                providers={"executor-resource": provider},
                memory_context_provider=context_provider,
            )
            workflow = CompiledWorkflow(
                tasks=(
                    CompiledTask(
                        task_id="memory-backed-task",
                        title="Use the supply voltage",
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
            result = core.run(
                RunInputs(
                    run_id="memory-run-one",
                    session_id="memory-session-one",
                    goal={},
                    team_plan={"budget": {"max_model_calls": 3}},
                    resource_plan=resource_plan(),
                    context={},
                ),
                workflow=workflow,
            )
            self.assertEqual("completed", result.status)
            self.assertEqual({"supply-voltage": 12}, provider.calls[0].context)
            self.assertNotIn("secret-note", provider.calls[0].context)
            self.assertEqual("memory:mem-orchestrator-voltage@1", context_provider.last_resolution.citations["supply-voltage"])
        finally:
            journal.close()
            session.close()


if __name__ == "__main__":
    unittest.main()


class MemoryCLITests(unittest.TestCase):
    def setUp(self) -> None:
        import contextlib
        import io
        from w1cip.cli import main as cli_main

        self._contextlib = contextlib
        self._io = io
        self._cli_main = cli_main
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_cli(self, *args: str, expected: int = 0) -> dict:
        stdout = self._io.StringIO()
        stderr = self._io.StringIO()
        argv = ["--workspace", str(self.workspace), "--json", "--no-color", *args]
        with self._contextlib.redirect_stdout(stdout), self._contextlib.redirect_stderr(stderr):
            code = self._cli_main(argv)
        if code != expected:
            self.fail(f"exit={code} expected={expected}\nstdout={stdout.getvalue()}\nstderr={stderr.getvalue()}")
        return json.loads(stdout.getvalue()) if stdout.getvalue().strip() else {}

    def test_memory_cli_add_search_context_conflict_and_verify(self) -> None:
        self.run_cli("init")
        first = self.workspace / "memory-one.json"
        first.write_text(
            json.dumps(
                {
                    "memory_id": "mem-cli-voltage-a",
                    "kind": "fact",
                    "subject": "pump prototype",
                    "predicate": "supply-voltage",
                    "value": 12,
                    "text": "The pump prototype supply voltage is 12 V.",
                    "confidence": 0.99,
                    "tags": ["pump", "electronics"],
                    "source": {"source_type": "user_statement", "source_ref": "conversation://cli-test"},
                }
            ),
            encoding="utf-8",
        )
        added = self.run_cli("memory", "add", "--file", str(first))
        self.assertEqual("memory:mem-cli-voltage-a@1", added["citation"])

        search = self.run_cli("memory", "search", "pump voltage")
        self.assertEqual(1, search["count"])
        self.assertEqual(12, search["results"][0]["record"]["value"])

        context = self.run_cli("memory", "context", "pump voltage", "--token-budget", "128")
        self.assertIn("memory:mem-cli-voltage-a@1", context["prompt_context"])
        self.assertLessEqual(context["estimated_tokens"], 128)

        second = self.workspace / "memory-two.json"
        second.write_text(
            json.dumps(
                {
                    "memory_id": "mem-cli-voltage-b",
                    "kind": "fact",
                    "subject": "pump prototype",
                    "predicate": "supply-voltage",
                    "value": 24,
                    "text": "A second source reports 24 V.",
                    "source": {"source_type": "tool_result", "source_ref": "tool://meter"},
                }
            ),
            encoding="utf-8",
        )
        self.run_cli("memory", "add", "--file", str(second))
        conflicts = self.run_cli("memory", "conflicts", "list")
        self.assertEqual(1, conflicts["count"])
        conflict_id = conflicts["conflicts"][0]["conflict_id"]
        self.run_cli(
            "memory",
            "conflicts",
            "resolve",
            conflict_id,
            "--winner",
            "mem-cli-voltage-a",
            "--rationale",
            "Trusted user value wins for this test.",
        )
        verified = self.run_cli("memory", "verify")
        self.assertTrue(verified["valid"])

        exported = self.run_cli("memory", "export")
        self.assertTrue(Path(exported["output"]).is_file())
