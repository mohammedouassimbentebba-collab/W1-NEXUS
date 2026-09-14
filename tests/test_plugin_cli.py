from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from w1cip.cli import main as cli_main


class PluginCLITests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name) / "workspace"
        self.workspace.mkdir()
        self._run("init")
        self.source = Path(self.temp.name) / "plugin"

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

    def test_scaffold_validate_install_conformance_and_health(self) -> None:
        scaffolded = self._run("plugins", "scaffold", str(self.source), "--id", "org.example.cli", "--name", "CLI Example")
        self.assertEqual(scaffolded["manifest"]["plugin_id"], "org.example.cli")
        validated = self._run("plugins", "validate", str(self.source))
        self.assertTrue(validated["compatibility"]["compatible"])
        installed = self._run("plugins", "install", str(self.source), "--enable")
        self.assertTrue(installed["enabled"])
        listed = self._run("plugins", "list")
        self.assertEqual(len(listed["plugins"]), 1)
        conform = self._run("plugins", "conformance", "org.example.cli")
        self.assertTrue(conform["passed"])
        health = self._run("plugins", "health", "org.example.cli")
        self.assertTrue(health["health"]["ok"])

    def test_plugin_benchmark_cli(self) -> None:
        result = self._run("plugins", "benchmark")
        self.assertTrue(result["passed"])


if __name__ == "__main__":
    unittest.main()
