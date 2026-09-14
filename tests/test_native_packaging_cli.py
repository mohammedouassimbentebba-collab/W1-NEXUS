from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from w1cip.cli import main as cli_main


class NativePackagingCLITests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name)
        self._run("init")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _run(self, *args: str, expected: int = 0):
        stdout, stderr = io.StringIO(), io.StringIO()
        argv = ["--workspace", str(self.workspace), "--json", "--no-color", *args]
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            rc = cli_main(argv)
        if rc != expected:
            self.fail(f"rc={rc} expected={expected}\nstdout={stdout.getvalue()}\nstderr={stderr.getvalue()}")
        return json.loads(stdout.getvalue()) if stdout.getvalue().strip() else None

    def test_packaging_doctor_and_benchmark(self) -> None:
        doctor = self._run("packaging", "doctor", "--target", "windows")
        self.assertEqual(doctor["target"], "windows")
        self.assertIn("can_compile_native_installer_here", doctor)
        benchmark = self._run("packaging", "benchmark")
        self.assertTrue(benchmark["passed"])

    def test_sources_are_generated_inside_workspace(self) -> None:
        generated = self._run("packaging", "sources", "--output", "build/windows")
        self.assertEqual(generated["target"], "windows")
        self.assertTrue((self.workspace / "build" / "windows" / "installer.iss").is_file())
        self.assertTrue((self.workspace / "build" / "windows" / "packaging-manifest.json").is_file())

    def test_source_rc_check_does_not_require_initialized_workspace(self) -> None:
        root = Path(__file__).resolve().parents[1]
        stdout, stderr = io.StringIO(), io.StringIO()
        argv = ["--json", "--no-color", "packaging", "rc-check", "--source-root", str(root)]
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            rc = cli_main(argv)
        self.assertEqual(rc, 0, stdout.getvalue() + stderr.getvalue())
        payload = json.loads(stdout.getvalue())
        self.assertTrue(payload["passed"], payload)
        self.assertTrue(payload["claims"]["source_rc_ready"])

    def test_project_descriptor_cli(self) -> None:
        created = self._run(
            "packaging", "project", "create", "demo.w1nexus",
            "--name", "Demo", "--workspace-path", ".",
        )
        self.assertEqual(created["descriptor"]["name"], "Demo")
        shown = self._run("packaging", "project", "show", "demo.w1nexus")
        self.assertEqual(Path(shown["resolved_workspace"]), self.workspace.resolve())

    def test_deep_link_cli_is_fail_closed(self) -> None:
        accepted = self._run("packaging", "deep-link", "w1://settings/open?section=models")
        self.assertEqual(accepted["area"], "settings")
        stdout, stderr = io.StringIO(), io.StringIO()
        argv = ["--workspace", str(self.workspace), "--json", "packaging", "deep-link", "w1://workspace/open?command=cmd.exe"]
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            rc = cli_main(argv)
        self.assertEqual(rc, 1)
        error = json.loads(stdout.getvalue())
        self.assertEqual(error["error_code"], "native_deep_link_denied")


if __name__ == "__main__":
    unittest.main()
