from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from w1cip.cli import main as cli_main


class ArtifactStudioCLITests(unittest.TestCase):
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

    def test_cli_artifact_lifecycle_and_desktop_doctor(self) -> None:
        self.run_cli("init")
        target = self.root / "main.py"
        target.write_text("value = 1\n", encoding="utf-8")
        created = self.run_cli(
            "studio", "artifacts", "create",
            "--artifact-id", "main-script", "--path", "main.py",
            "--title", "Main", "--created-by", "author",
        )
        content = self.root / "next.txt"
        content.write_text("value = 2\n", encoding="utf-8")
        saved = self.run_cli(
            "studio", "artifacts", "save", "main-script",
            "--content-file", str(content), "--created-by", "author",
            "--expected-parent-hash", created["content_hash"],
        )
        self.run_cli(
            "studio", "artifacts", "review", "main-script",
            "--version", str(saved["version"]), "--reviewer", "reviewer",
            "--outcome", "approved", "--rationale", "verified",
        )
        published = self.run_cli(
            "studio", "artifacts", "publish", "main-script",
            "--published-by", "owner", "--approve-action",
        )
        self.assertEqual(published["action_id"], "publish-main-script-v2")
        self.assertEqual(target.read_text(), "value = 2\n")
        verified = self.run_cli("studio", "artifacts", "verify")
        self.assertTrue(verified["valid"])
        doctor = self.run_cli("desktop", "doctor")
        self.assertTrue(doctor["artifact_studio_benchmark"]["passed"])
        self.assertFalse(doctor["w1_owned_server_required"])

    def test_terminal_plan_does_not_execute(self) -> None:
        self.run_cli("init")
        plan = self.run_cli(
            "studio", "terminal", "plan",
            "--action-id", "studio-plan-one",
            "--argv-json", '["python","-c","print(123)"]',
        )
        self.assertTrue(plan["requires_approval"])
        self.assertFalse((self.root / "123").exists())


if __name__ == "__main__":
    unittest.main()
