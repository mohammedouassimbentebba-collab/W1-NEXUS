from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from w1cip.cli import main as cli_main

ROOT = Path(__file__).resolve().parents[1]


class ReleaseCLITests(unittest.TestCase):
    def _run(self, workspace: Path, *args: str, expected: int = 0):
        stdout, stderr = io.StringIO(), io.StringIO()
        argv = ["--workspace", str(workspace), "--json", "--no-color", *args]
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            rc = cli_main(argv)
        if rc != expected:
            self.fail(f"rc={rc} expected={expected}\nstdout={stdout.getvalue()}\nstderr={stderr.getvalue()}")
        return json.loads(stdout.getvalue()) if stdout.getvalue().strip() else None

    def test_release_benchmark_and_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            self._run(workspace, "init")
            result = self._run(workspace, "release", "benchmark")
            self.assertTrue(result["passed"])
            catalog = self._run(workspace, "release", "catalog")
            self.assertGreaterEqual(len(catalog["benchmarks"]), 5)

    def test_release_result_record_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            self._run(workspace, "init")
            evidence = workspace / "evidence.json"
            evidence.write_text('{"ok": true}\n', encoding="utf-8")
            result_path = workspace / "result.json"
            result_path.write_text(json.dumps({
                "result_id": "bfcl-test",
                "benchmark_id": "bfcl-v4",
                "benchmark_version": "v4-pinned",
                "upstream_commit": "abcdef0123456789",
                "adapter_version": "dev41",
                "executed_at": "2026-08-08T12:00:00Z",
                "system_under_test": "test-stack",
                "metrics": {"accuracy": 0.1},
                "environment": {"os": "test"},
            }), encoding="utf-8")
            recorded = self._run(workspace, "release", "record", "--result", str(result_path), "--evidence", str(evidence))
            self.assertEqual(recorded["result_id"], "bfcl-test")
            verified = self._run(workspace, "release", "verify-result", "bfcl-test", "--evidence", str(evidence))
            self.assertTrue(verified["valid"])

    def test_public_gate_accepts_apache_release_identity(self) -> None:
        # Gate is a source-project command; point workspace at the checkout.
        result = self._run(ROOT, "release", "gate", "--public")
        self.assertTrue(result["passed"], result)
        self.assertTrue(result["checks"]["release_identity_verified"])
        self.assertEqual(result["brand_identity"]["identity"]["license_spdx"], "Apache-2.0")


if __name__ == "__main__":
    unittest.main()
