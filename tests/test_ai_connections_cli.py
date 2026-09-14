from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from w1cip.cli import main as cli_main


class AIConnectionsCLITests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_cli(self, *arguments: str, expected: int = 0) -> SimpleNamespace:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            rc = cli_main(["--workspace", str(self.workspace), "--json", "--no-color", *arguments])
        result = SimpleNamespace(returncode=rc, stdout=stdout.getvalue(), stderr=stderr.getvalue())
        self.assertEqual(expected, rc, result.stdout + result.stderr)
        return result

    def test_catalog_and_benchmark(self) -> None:
        self.run_cli("init")
        catalog = json.loads(self.run_cli("connections", "catalog").stdout)
        ids = {item["provider_id"] for item in catalog["providers"]}
        self.assertTrue({"openai", "anthropic", "gemini", "xai", "local-openai-compatible"}.issubset(ids))
        benchmark = json.loads(self.run_cli("connections", "benchmark").stdout)
        self.assertTrue(benchmark["passed"], benchmark)

    def test_local_connection_model_and_team_round_trip(self) -> None:
        self.run_cli("init")
        self.run_cli("connections", "add-local", "local-main", "--endpoint", "http://127.0.0.1:11434/v1/chat/completions")
        self.run_cli("connections", "add-model", "local-main", "local-coder", "--model-name", "qwen-test", "--role", "producer")
        team_file = self.workspace / "team.json"
        team_file.write_text(json.dumps({
            "team_id": "local-team",
            "display_name": "Local Team",
            "mode": "solo",
            "members": [{"model_id": "local-coder", "role": "producer"}]
        }), encoding="utf-8")
        added = json.loads(self.run_cli("teams", "add", "--file", str(team_file)).stdout)
        self.assertEqual("best_fit", added["compiled_portfolio"]["strategy"])
        listing = json.loads(self.run_cli("teams", "list").stdout)
        self.assertEqual(1, listing["count"])
        self.run_cli("connections", "add-custom", "public-custom", "--endpoint", "https://models.example.test/v1/chat/completions")
        connection_state = json.loads(self.run_cli("connections", "list").stdout)
        self.assertEqual(2, len(connection_state["connections"]))
        local = next(item for item in connection_state["connections"] if item["connection_id"] == "local-main")
        custom = next(item for item in connection_state["connections"] if item["connection_id"] == "public-custom")
        self.assertEqual(1, len(local["models"]))
        self.assertEqual("none", custom["auth_mode"])

    def test_local_connection_rejects_lan_plain_http(self) -> None:
        self.run_cli("init")
        result = self.run_cli(
            "connections", "add-local", "unsafe", "--endpoint", "http://192.168.1.7:11434/v1/chat/completions", expected=1
        )
        self.assertIn("local_connection_must_be_loopback_http", result.stderr + result.stdout)


if __name__ == "__main__":
    unittest.main()
