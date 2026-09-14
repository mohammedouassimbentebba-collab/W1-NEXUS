from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from w1cip.action_runtime import ActionPolicy, ActionRequest, ActionRuntime
from w1cip.universal_artifacts import (
    ArtifactIndependentReviewRequired,
    ArtifactPatchError,
    ArtifactVersionConflict,
    UniversalArtifactStore,
    apply_json_patch,
    reference_artifact_models,
    render_artifact,
    run_universal_artifact_benchmark,
    _render_xlsx_minimal,
    validate_export_bytes,
)


class UniversalArtifactTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "project"
        self.root.mkdir()
        self.store = UniversalArtifactStore(self.root / ".w1nexus" / "office-artifacts.sqlite3", self.root)

    def tearDown(self) -> None:
        self.store.close()
        self.temp.cleanup()

    def test_json_pointer_patch_and_identity_protection(self) -> None:
        model = reference_artifact_models()["document"]
        changed = apply_json_patch(model, [
            {"op": "test", "path": "/kind", "value": "document"},
            {"op": "add", "path": "/content/blocks/-", "value": {"type": "paragraph", "text": "Added"}},
        ])
        self.assertEqual(changed["content"]["blocks"][-1]["text"], "Added")
        with self.assertRaises(ArtifactPatchError):
            apply_json_patch(model, [{"op": "replace", "path": "/artifact_id", "value": "other"}])

    def test_document_lifecycle_review_and_binary_export(self) -> None:
        first = self.store.create(reference_artifact_models()["document"], created_by="author")
        second = self.store.patch(
            first.artifact_id,
            [{"op": "add", "path": "/content/blocks/-", "value": {"type": "paragraph", "text": "Reviewed addition."}}],
            created_by="author",
            expected_parent_hash=first.model_hash,
        )
        with self.assertRaises(ArtifactIndependentReviewRequired):
            self.store.review(
                first.artifact_id, version=second.version, reviewer="author",
                outcome="approved", rationale="self review",
            )
        self.store.review(
            first.artifact_id, version=second.version, reviewer="reviewer",
            outcome="approved", rationale="Independent content and schema review passed.",
        )
        policy = ActionPolicy(require_approval_for_writes=True)
        with ActionRuntime(self.root, policy=policy) as runtime:
            record = self.store.export(
                first.artifact_id, format="docx", output_path="exports/report.docx",
                exported_by="owner", action_runtime=runtime, issue_action_approval=True,
            )
        data = (self.root / "exports" / "report.docx").read_bytes()
        self.assertEqual(record.output_hash, validate_export_bytes(data, "docx")["metadata"]["sha256"])
        self.assertTrue(validate_export_bytes(data, "docx")["valid"])
        self.assertTrue(self.store.verify()["valid"])

    def test_all_reference_exports_are_structurally_valid(self) -> None:
        formats = {
            "document": ("docx", "pdf", "json"),
            "spreadsheet": ("xlsx", "pdf", "json"),
            "presentation": ("pptx", "pdf", "json"),
        }
        for kind, model in reference_artifact_models().items():
            for selected_format in formats[kind]:
                with self.subTest(kind=kind, format=selected_format):
                    data = render_artifact(model, selected_format)
                    self.assertTrue(validate_export_bytes(data, selected_format)["valid"])

    def test_staged_binary_publication_is_private_idempotent_and_reversible(self) -> None:
        raw = b"\x00W1\xffbinary\n"
        digest = __import__("hashlib").sha256(raw).hexdigest()
        with ActionRuntime(self.root) as runtime:
            staging = runtime.state_dir / "office-staging" / "sample.bin"
            staging.parent.mkdir(parents=True, exist_ok=True)
            staging.write_bytes(raw)
            request = ActionRequest(
                action_id="binary-write-one", kind="file.publish_staged",
                parameters={
                    "path": "artifact.bin",
                    "staging_path": "office-staging/sample.bin",
                    "sha256": digest,
                },
                requested_by="test",
            )
            plan = runtime.plan(request)
            self.assertEqual(plan.risk, "reversible_write")
            result = runtime.execute(request)
            self.assertTrue(result.metadata["binary"])
            self.assertEqual((self.root / "artifact.bin").read_bytes(), raw)
            self.assertFalse(staging.exists())

            # A completed action remains idempotent even after the ephemeral
            # staging file has been consumed.
            replay = runtime.execute(request)
            self.assertEqual(replay.action_id, result.action_id)

            row = runtime._connection.execute(
                "SELECT request_json FROM actions WHERE action_id=?", (request.action_id,)
            ).fetchone()
            request_json = row["request_json"]
            self.assertNotIn(raw.hex(), request_json)
            self.assertNotIn("V0j/", request_json)
            self.assertIn(digest, request_json)

            runtime.undo("binary-write-one", requested_by="test")
        self.assertFalse((self.root / "artifact.bin").exists())

    def test_dependency_free_xlsx_fallback_is_structurally_valid(self) -> None:
        data = _render_xlsx_minimal(reference_artifact_models()["spreadsheet"])
        report = validate_export_bytes(data, "xlsx")
        self.assertTrue(report["valid"], report)
        with __import__("zipfile").ZipFile(__import__("io").BytesIO(data)) as archive:
            self.assertIn("xl/workbook.xml", archive.namelist())
            self.assertIn("xl/worksheets/sheet1.xml", archive.namelist())
            worksheet = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
            self.assertIn("<f>", worksheet)

    def test_optimistic_revision_conflict(self) -> None:
        first = self.store.create(reference_artifact_models()["document"], created_by="author")
        self.store.patch(
            first.artifact_id,
            [{"op": "replace", "path": "/title", "value": "Changed"}],
            created_by="author", expected_parent_hash=first.model_hash,
        )
        with self.assertRaises(ArtifactVersionConflict):
            self.store.patch(
                first.artifact_id,
                [{"op": "replace", "path": "/title", "value": "Stale"}],
                created_by="author", expected_parent_hash=first.model_hash,
            )

    def test_tampering_is_detected(self) -> None:
        revision = self.store.create(reference_artifact_models()["document"], created_by="author")
        connection = sqlite3.connect(self.store.db_path)
        model = dict(revision.model)
        model["title"] = "Tampered"
        connection.execute(
            "UPDATE office_revisions SET model_json=? WHERE artifact_id=? AND version=1",
            (json.dumps(model), revision.artifact_id),
        )
        connection.commit()
        connection.close()
        report = self.store.verify()
        self.assertFalse(report["valid"])
        self.assertTrue(any("model hash mismatch" in item for item in report["errors"]))

    def test_reference_benchmark(self) -> None:
        report = run_universal_artifact_benchmark()
        self.assertTrue(report["passed"], report)
        self.assertTrue(all(report["probes"].values()))


if __name__ == "__main__":
    unittest.main()
