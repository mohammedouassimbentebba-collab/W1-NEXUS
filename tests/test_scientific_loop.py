from __future__ import annotations

import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from w1cip.memory import MemoryPrincipal, MemoryStore
from w1cip.scientific import (
    ScientificLab,
    ScientificPrincipal,
    ScientificStateError,
    ScientificValidationError,
)
from w1cip.secure_execution import (
    NetworkPolicy,
    SandboxLimits,
    SandboxProfile,
    SandboxRequest,
    SecureExecutionFabric,
)


def plan(study_id: str = "pump-current-study", *, creator_variant: bool = False, replications: int = 0) -> dict:
    return {
        "study_id": study_id,
        "namespace_id": "w1-user",
        "project_id": "pump-project",
        "title": "Pump controller current comparison",
        "research_question": "Does candidate B draw less startup current than candidate A?",
        "hypotheses": [
            {
                "hypothesis_id": "h-lower-current",
                "statement": "Candidate B draws less startup current than candidate A.",
                "null_statement": "Candidate B does not draw less startup current than candidate A.",
                "predictions": [
                    {
                        "prediction_id": "p-current-gap",
                        "metric": "peak-current",
                        "direction": "negative",
                    }
                ],
            }
        ],
        "experiment": {
            "design": "controlled-benchmark",
            "independent_variables": ["candidate"],
            "dependent_variables": ["peak-current"],
            "controls": ["supply-voltage"],
            "sample_size_min": 6,
            "randomization": "blocked",
            "blinding": "none",
            "observation_schema": {
                "observation_id": "string",
                "candidate": "string",
                "peak-current": "number",
            },
            "command": [sys.executable, "-c", "print('experiment')"],
            "sandbox_profile": "science-local",
            "artifact_globs": [],
        },
        "analysis_plan": [
            {
                "analysis_id": "current-difference",
                "method": "difference_in_means",
                "value_field": "peak-current",
                "group_field": "candidate",
                "group_a": "candidate-a",
                "group_b": "candidate-b",
                "alpha": 0.05,
            }
        ],
        "falsification_criteria": [
            {
                "criterion_id": "f-current-direction",
                "hypothesis_id": "h-lower-current",
                "analysis_id": "current-difference",
                "rule": "effect_direction_and_significance",
                "expected_direction": "positive",
                "alpha": 0.05,
            }
        ],
        "replication_policy": {
            "minimum_replications": replications,
            "independent_required": True,
        },
        "classification": "internal",
    }


def observations(prefix: str = "o") -> list[dict]:
    return [
        {"observation_id": f"{prefix}-a-1", "candidate": "candidate-a", "peak-current": 20.0},
        {"observation_id": f"{prefix}-a-2", "candidate": "candidate-a", "peak-current": 21.0},
        {"observation_id": f"{prefix}-a-3", "candidate": "candidate-a", "peak-current": 19.5},
        {"observation_id": f"{prefix}-b-1", "candidate": "candidate-b", "peak-current": 15.0},
        {"observation_id": f"{prefix}-b-2", "candidate": "candidate-b", "peak-current": 14.5},
        {"observation_id": f"{prefix}-b-3", "candidate": "candidate-b", "peak-current": 15.5},
    ]


class ScientificLoopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.lab = ScientificLab(self.root / ".w1nexus" / "science.sqlite3", workspace_root=self.root)
        self.creator = ScientificPrincipal("human", "researcher-one")
        self.analyst = ScientificPrincipal("agent", "statistician-one")
        self.reviewer = ScientificPrincipal("human", "reviewer-one")

    def tearDown(self) -> None:
        self.lab.close()
        self.temp.cleanup()

    def complete(self, study_id: str = "pump-current-study") -> dict:
        self.lab.create_study(plan(study_id), actor=self.creator)
        self.lab.preregister(study_id, actor=self.creator)
        self.lab.add_observations(study_id, observations(study_id[:4]), actor=self.creator)
        self.lab.run_analysis(study_id, "current-difference", actor=self.analyst)
        return self.lab.evaluate(study_id, actor=self.analyst)

    def test_preregistered_workflow_supports_hypothesis(self) -> None:
        evaluation = self.complete()
        self.assertEqual("supported", evaluation["hypotheses"][0]["outcome"])
        self.assertTrue(self.lab.verify(study_id="pump-current-study")["ok"])

    def test_analysis_before_observations_is_rejected(self) -> None:
        self.lab.create_study(plan(), actor=self.creator)
        self.lab.preregister("pump-current-study", actor=self.creator)
        with self.assertRaises(ScientificStateError):
            self.lab.run_analysis("pump-current-study", "current-difference", actor=self.analyst)

    def test_exploratory_analysis_cannot_replace_preregistered_analysis(self) -> None:
        self.lab.create_study(plan(), actor=self.creator)
        self.lab.preregister("pump-current-study", actor=self.creator)
        self.lab.add_observations("pump-current-study", observations(), actor=self.creator)
        self.lab.run_analysis(
            "pump-current-study",
            "exploratory-current-gap",
            actor=self.analyst,
            exploratory_spec={
                "method": "difference_in_means",
                "value_field": "peak-current",
                "group_field": "candidate",
                "group_a": "candidate-a",
                "group_b": "candidate-b",
            },
        )
        with self.assertRaises(ScientificStateError):
            self.lab.evaluate("pump-current-study", actor=self.analyst)

    def test_duplicate_observation_is_rejected_and_chain_remains_valid(self) -> None:
        self.lab.create_study(plan(), actor=self.creator)
        self.lab.preregister("pump-current-study", actor=self.creator)
        self.lab.add_observations("pump-current-study", observations(), actor=self.creator)
        with self.assertRaises(ScientificValidationError):
            self.lab.add_observations("pump-current-study", [observations()[0]], actor=self.creator)
        self.assertTrue(self.lab.verify(study_id="pump-current-study")["ok"])

    def test_experiment_execution_must_match_preregistered_command(self) -> None:
        self.lab.create_study(plan(), actor=self.creator)
        self.lab.preregister("pump-current-study", actor=self.creator)
        fabric = SecureExecutionFabric(self.root)
        fabric.save_profile(
            SandboxProfile(
                profile_id="science-local",
                backend="local",
                require_hard_isolation=False,
                root_read_only=False,
                network=NetworkPolicy(mode="inherit"),
                limits=SandboxLimits(wall_seconds=5, cpu_seconds=3, memory_mb=512, pids=64),
                allowed_commands=(Path(sys.executable).name,),
                artifact_globs=(),
            )
        )
        try:
            with self.assertRaises(ScientificValidationError):
                self.lab.execute_experiment(
                    "pump-current-study",
                    SandboxRequest(
                        execution_id="wrong-command",
                        profile_id="science-local",
                        argv=(sys.executable, "-c", "print('different')"),
                    ),
                    actor=self.creator,
                    fabric=fabric,
                )
            result = self.lab.execute_experiment(
                "pump-current-study",
                SandboxRequest(
                    execution_id="registered-command",
                    profile_id="science-local",
                    argv=tuple(plan()["experiment"]["command"]),
                ),
                actor=self.creator,
                fabric=fabric,
            )
            self.assertEqual("completed", result["result"]["status"])
        finally:
            fabric.close()

    def test_review_requires_independence_and_completed_checks(self) -> None:
        self.complete()
        with self.assertRaises(ScientificValidationError):
            self.lab.review(
                "pump-current-study", reviewer=self.creator, outcome="approved", rationale="self review"
            )
        review = self.lab.review(
            "pump-current-study", reviewer=self.reviewer, outcome="approved", rationale="Plan, data, and analysis accepted."
        )
        self.assertTrue(all(review["checks"].values()))

    def test_replication_requires_independent_creator(self) -> None:
        self.complete("original-study")
        self.lab.create_study(plan("replication-study"), actor=self.creator)
        self.lab.preregister("replication-study", actor=self.creator)
        self.lab.add_observations("replication-study", observations("r"), actor=self.creator)
        self.lab.run_analysis("replication-study", "current-difference", actor=self.analyst)
        self.lab.evaluate("replication-study", actor=self.analyst)
        with self.assertRaises(ScientificValidationError):
            self.lab.compare_replication("original-study", "replication-study", actor=self.reviewer)

    def test_independent_replication_is_compared(self) -> None:
        self.complete("original-study")
        second_creator = ScientificPrincipal("human", "researcher-two")
        self.lab.create_study(plan("replication-study"), actor=second_creator)
        self.lab.preregister("replication-study", actor=second_creator)
        self.lab.add_observations("replication-study", observations("r"), actor=second_creator)
        self.lab.run_analysis("replication-study", "current-difference", actor=self.analyst)
        self.lab.evaluate("replication-study", actor=self.analyst)
        comparison = self.lab.compare_replication("original-study", "replication-study", actor=self.reviewer)
        self.assertEqual("replicated", comparison["overall"])

    def test_reproducibility_package_is_verified(self) -> None:
        self.complete()
        self.lab.review(
            "pump-current-study", reviewer=self.reviewer, outcome="approved", rationale="Independent approval."
        )
        package = self.lab.build_reproducibility_package(
            "pump-current-study", self.root / "reports" / "study.zip", actor=self.reviewer
        )
        self.assertTrue(Path(package["path"]).is_file())
        verified = self.lab.verify_package(package["path"])
        self.assertTrue(verified["ok"])
        with zipfile.ZipFile(package["path"], "r") as archive:
            self.assertIn("manifest.json", archive.namelist())
            self.assertIn("observations.jsonl", archive.namelist())

    def test_supported_finding_can_only_be_published_after_approval(self) -> None:
        self.complete()
        memory = MemoryStore(self.root / ".w1nexus" / "memory.sqlite3")
        actor = MemoryPrincipal("human", "researcher-one")
        try:
            with self.assertRaises(ScientificStateError):
                self.lab.publish_supported_hypothesis_to_memory(
                    "pump-current-study", "h-lower-current", memory_store=memory, actor=actor
                )
            self.lab.review(
                "pump-current-study", reviewer=self.reviewer, outcome="approved", rationale="Approved."
            )
            published = self.lab.publish_supported_hypothesis_to_memory(
                "pump-current-study", "h-lower-current", memory_store=memory, actor=actor
            )
            self.assertTrue(published["citation"].startswith("memory:"))
        finally:
            memory.close()

    def test_minimum_sample_and_post_analysis_data_lock_are_enforced(self) -> None:
        self.lab.create_study(plan(), actor=self.creator)
        self.lab.preregister("pump-current-study", actor=self.creator)
        self.lab.add_observations("pump-current-study", observations()[:4], actor=self.creator)
        with self.assertRaises(ScientificStateError):
            self.lab.run_analysis("pump-current-study", "current-difference", actor=self.analyst)
        self.lab.add_observations("pump-current-study", observations()[4:], actor=self.creator)
        self.lab.run_analysis("pump-current-study", "current-difference", actor=self.analyst)
        with self.assertRaises(ScientificStateError):
            self.lab.add_observations(
                "pump-current-study",
                [{"observation_id": "late-one", "candidate": "candidate-b", "peak-current": 14.0}],
                actor=self.creator,
            )

    def test_w1cip_bridge_preserves_digests_and_does_not_forge_entities(self) -> None:
        self.complete()
        self.lab.review(
            "pump-current-study", reviewer=self.reviewer, outcome="approved", rationale="Approved."
        )
        bridge = self.lab.build_w1cip_bridge_manifest("pump-current-study")
        self.assertEqual("verified", bridge["source"]["integrity_status"])
        self.assertEqual("supports", bridge["claim_assessments"][0]["suggested_evidence_relation"])
        self.assertTrue(bridge["recommended_w1cip_mapping"]["create_new_claim_contribution_if_missing"])
        self.assertNotIn("entity_type", bridge["study_ref"])

    def test_tampering_is_detected(self) -> None:
        self.complete()
        self.lab._connection.execute("DROP TRIGGER observations_no_update")
        self.lab._connection.execute(
            "UPDATE observations SET data_json=? WHERE study_id=? AND observation_id=?",
            (json.dumps({"observation_id": "pump-a-1", "candidate": "candidate-a", "peak-current": 1}), "pump-current-study", "pump-a-1"),
        )
        self.lab._connection.commit()
        result = self.lab.verify(study_id="pump-current-study")
        self.assertFalse(result["ok"])
        self.assertTrue(any(item.startswith("observation:") for item in result["errors"]))


if __name__ == "__main__":
    unittest.main()
