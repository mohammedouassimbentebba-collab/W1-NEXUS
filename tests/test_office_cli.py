from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from w1cip.cli import main as cli_main
from w1cip.universal_artifacts import reference_artifact_models, validate_export_bytes


class OfficeCLITests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_cli(self, *args: str, expected: int = 0):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli_main(["--workspace", str(self.root), "--json", *args])
        self.assertEqual(code, expected, f"stdout={out.getvalue()} stderr={err.getvalue()}")
        return json.loads(out.getvalue()) if out.getvalue().strip() else {}

    def test_office_cli_document_cycle(self) -> None:
        self.run_cli("init")
        spec = self.root / "document.json"
        spec.write_text(json.dumps(reference_artifact_models()["document"]), encoding="utf-8")
        valid = self.run_cli("office", "validate", "--spec", str(spec))
        self.assertTrue(valid["valid"])
        created = self.run_cli("office", "create", "--spec", str(spec), "--created-by", "author")
        patch = self.root / "patch.json"
        patch.write_text(json.dumps([
            {"op": "add", "path": "/content/blocks/-", "value": {"type": "paragraph", "text": "CLI patch"}}
        ]), encoding="utf-8")
        changed = self.run_cli(
            "office", "patch", created["artifact_id"], "--patch", str(patch),
            "--created-by", "author", "--expected-parent-hash", created["model_hash"],
        )
        self.run_cli(
            "office", "review", created["artifact_id"], "--version", str(changed["version"]),
            "--reviewer", "reviewer", "--outcome", "approved", "--rationale", "verified",
        )
        exported = self.run_cli(
            "office", "export", created["artifact_id"], "--format", "pdf",
            "--output", "exports/document.pdf", "--exported-by", "owner", "--approve-action",
        )
        output = self.root / exported["output_path"]
        self.assertTrue(output.is_file())
        self.assertTrue(validate_export_bytes(output.read_bytes(), "pdf")["valid"])
        verified = self.run_cli("office", "verify")
        self.assertTrue(verified["valid"])

    def test_office_demo_exports_nine_files(self) -> None:
        self.run_cli("init")
        report = self.run_cli("office", "demo", "--output-dir", "office-demo")
        self.assertTrue(report["passed"])
        self.assertEqual(len(report["exported"]), 9)


if __name__ == "__main__":
    unittest.main()
