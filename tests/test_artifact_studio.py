from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from w1cip.action_runtime import ActionPolicy, ActionRuntime
from w1cip.artifact_studio import (
    ArtifactConflict,
    ArtifactPathDenied,
    ArtifactReviewRequired,
    ArtifactStudioStore,
    WorkspaceFileService,
    run_artifact_studio_benchmark,
)


class ArtifactStudioTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "project"
        (self.root / "src").mkdir(parents=True)
        (self.root / "src" / "main.py").write_text("value = 1\n", encoding="utf-8")
        (self.root / "data.csv").write_text("name,value\npump,12\n", encoding="utf-8")
        (self.root / "config.json").write_text('{"enabled":true}\n', encoding="utf-8")
        self.store = ArtifactStudioStore(self.root / ".w1nexus" / "artifact-studio.sqlite3", self.root)

    def tearDown(self) -> None:
        self.store.close()
        self.temp.cleanup()

    def test_tree_and_structured_previews(self) -> None:
        tree = self.store.files.tree(max_depth=3)
        self.assertGreaterEqual(tree["entry_count"], 4)
        json_preview = self.store.files.preview("config.json")
        self.assertTrue(json_preview["valid"])
        self.assertTrue(json_preview["structured"]["enabled"])
        csv_preview = self.store.files.preview("data.csv")
        self.assertEqual(csv_preview["table"]["header"], ["name", "value"])

    def test_path_escape_and_internal_state_are_denied(self) -> None:
        service = WorkspaceFileService(self.root)
        for path in ("../outside.txt", ".w1nexus/workspace.json", ".git/config"):
            with self.assertRaises(ArtifactPathDenied):
                service.resolve(path, allow_missing=True)

    def test_symlink_is_not_followed(self) -> None:
        if os.name == "nt":
            self.skipTest("symlink permissions vary on Windows")
        outside = Path(self.temp.name) / "outside.txt"
        outside.write_text("secret", encoding="utf-8")
        (self.root / "link.txt").symlink_to(outside)
        with self.assertRaises(ArtifactPathDenied):
            self.store.files.read("link.txt")

    def test_immutable_versions_review_and_governed_publish(self) -> None:
        first = self.store.create_draft(
            artifact_id="main-script", path="src/main.py", title="Main", created_by="author",
        )
        second = self.store.save_version(
            "main-script", content="value = 2\n", created_by="author",
            expected_parent_hash=first.content_hash,
        )
        with self.assertRaises(ArtifactReviewRequired):
            self.store.review(
                "main-script", version=second.version, reviewer="author",
                outcome="approved", rationale="self review",
            )
        self.store.add_comment(
            "main-script", version=2, author="reviewer", body="Looks correct", line_number=1,
        )
        self.store.review(
            "main-script", version=2, reviewer="reviewer",
            outcome="approved", rationale="Independent review passed.",
        )
        policy = ActionPolicy(require_approval_for_writes=True)
        with ActionRuntime(self.root, policy=policy) as runtime:
            publication = self.store.publish(
                "main-script", published_by="owner", action_runtime=runtime,
                issue_action_approval=True,
            )
        self.assertEqual(publication["action_id"], "publish-main-script-v2")
        self.assertEqual((self.root / "src" / "main.py").read_text(), "value = 2\n")
        history = self.store.history("main-script")
        self.assertEqual(len(history["versions"]), 2)
        self.assertEqual(len(history["reviews"]), 1)
        self.assertEqual(len(history["publications"]), 1)
        self.assertTrue(self.store.verify()["valid"])

    def test_optimistic_version_conflict(self) -> None:
        first = self.store.create_draft(
            artifact_id="conflict-script", path="src/main.py", title="Conflict", created_by="author",
        )
        self.store.save_version(
            "conflict-script", content="value = 2\n", created_by="author",
            expected_parent_hash=first.content_hash,
        )
        with self.assertRaises(ArtifactConflict):
            self.store.save_version(
                "conflict-script", content="value = 3\n", created_by="author",
                expected_parent_hash=first.content_hash,
            )

    def test_workspace_change_blocks_publish(self) -> None:
        first = self.store.create_draft(
            artifact_id="stale-script", path="src/main.py", title="Stale", created_by="author",
        )
        changed = self.store.save_version(
            "stale-script", content="value = 2\n", created_by="author",
            expected_parent_hash=first.content_hash,
        )
        self.store.review(
            "stale-script", version=changed.version, reviewer="reviewer",
            outcome="approved", rationale="approved",
        )
        (self.root / "src" / "main.py").write_text("external = true\n", encoding="utf-8")
        with ActionRuntime(self.root) as runtime:
            with self.assertRaises(ArtifactConflict):
                self.store.publish("stale-script", published_by="owner", action_runtime=runtime)

    def test_tampering_is_detected(self) -> None:
        self.store.create_draft(
            artifact_id="tamper-script", path="src/main.py", title="Tamper", created_by="author",
        )
        connection = sqlite3.connect(self.store.db_path)
        connection.execute(
            "UPDATE artifact_versions SET content='modified' WHERE artifact_id='tamper-script' AND version=1"
        )
        connection.commit()
        connection.close()
        report = self.store.verify()
        self.assertFalse(report["valid"])
        self.assertTrue(any("content hash mismatch" in item for item in report["errors"]))

    def test_reference_benchmark(self) -> None:
        report = run_artifact_studio_benchmark()
        self.assertTrue(report["passed"], report)
        self.assertTrue(all(report["probes"].values()))


if __name__ == "__main__":
    unittest.main()
