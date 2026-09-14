from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run_cli(workspace: Path, *args: str):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src")
    return subprocess.run(
        [sys.executable, "-m", "w1cip.cli", "--workspace", str(workspace), "--json", *args],
        text=True, capture_output=True, env=env, timeout=30,
    )


class ProviderCertificationCLITests(unittest.TestCase):
    def test_catalog_and_benchmark_need_no_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(0, run_cli(root, "init").returncode)
            catalog = run_cli(root, "connections", "certification-catalog")
            self.assertEqual(0, catalog.returncode, catalog.stderr)
            payload = json.loads(catalog.stdout)
            self.assertGreaterEqual(len(payload["providers"]), 6)
            bench = run_cli(root, "connections", "certification-benchmark")
            self.assertEqual(0, bench.returncode, bench.stderr)
            self.assertTrue(json.loads(bench.stdout)["passed"])

    def test_certify_refuses_network_without_live_flag(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(0, run_cli(root, "init").returncode)
            add = run_cli(root, "connections", "add-local", "local", "--endpoint", "http://127.0.0.1:11434/v1/chat/completions")
            self.assertEqual(0, add.returncode, add.stderr)
            plan = run_cli(root, "connections", "certification-plan", "local", "--model", "local-model", "--level", "runtime")
            self.assertEqual(0, plan.returncode, plan.stderr)
            cert = run_cli(root, "connections", "certify", "local", "--model", "local-model", "--level", "runtime")
            self.assertNotEqual(0, cert.returncode)
            self.assertIn("live_certification_requires_explicit_live_flag", cert.stderr + cert.stdout)


if __name__ == "__main__":
    unittest.main()
