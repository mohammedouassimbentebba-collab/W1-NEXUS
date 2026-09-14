from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from w1cip.cli import main as cli_main


class W1GatewayCLITests(unittest.TestCase):
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

    def test_cli_gateway_benchmark(self) -> None:
        self.run_cli("init")
        res = self.run_cli("gateway", "benchmark")
        data = json.loads(res.stdout)
        self.assertTrue(data.get("passed"))
        self.assertEqual(data.get("probes_failed"), 0)

    def test_cli_gateway_stats(self) -> None:
        self.run_cli("init")
        res = self.run_cli("gateway", "stats")
        data = json.loads(res.stdout)
        self.assertGreaterEqual(data.get("total_providers", 0), 10)
        self.assertGreaterEqual(data.get("total_routes", 0), 15)

    def test_cli_gateway_list_providers(self) -> None:
        self.run_cli("init")
        res = self.run_cli("gateway", "list-providers")
        data = json.loads(res.stdout)
        self.assertGreaterEqual(data.get("count", 0), 10)
        self.assertIn("providers", data)

    def test_cli_gateway_list_identities(self) -> None:
        self.run_cli("init")
        res = self.run_cli("gateway", "list-identities")
        data = json.loads(res.stdout)
        self.assertGreaterEqual(data.get("count", 0), 8)
        self.assertIn("identities", data)

    def test_cli_gateway_list_routes(self) -> None:
        self.run_cli("init")
        res = self.run_cli("gateway", "list-routes", "--free-only")
        data = json.loads(res.stdout)
        self.assertGreaterEqual(data.get("count", 0), 8)
        for r in data.get("routes", []):
            self.assertTrue(r.get("is_free"))

    def test_cli_gateway_select_route(self) -> None:
        self.run_cli("init")
        res = self.run_cli("gateway", "select-route", "--task", "coding", "--free-only")
        data = json.loads(res.stdout)
        self.assertIsNotNone(data.get("selected"))
        self.assertGreaterEqual(data.get("fallback_count", 0), 1)

    def test_cli_gateway_explain_route(self) -> None:
        self.run_cli("init")
        res = self.run_cli("gateway", "explain-route", "--task", "coding", "--free-only")
        data = json.loads(res.stdout)
        self.assertIsNotNone(data.get("selected_route_id"))
        self.assertGreater(len(data.get("positive_factors", [])), 0)
        self.assertGreater(len(data.get("fallback_hierarchy", [])), 0)

    def test_cli_gateway_build_team(self) -> None:
        self.run_cli("init")
        res = self.run_cli("gateway", "build-team", "--task", "coding", "--budget", "0.0", "--team-mode", "challenge")
        data = json.loads(res.stdout)
        self.assertIn("team", data)
        self.assertIn("portfolio", data)
        team = data["team"]
        self.assertIsNotNone(team.get("producer"))
        self.assertIsNotNone(team.get("reviewer"))
        self.assertIsNotNone(team.get("verifier"))
        self.assertGreater(team["diversity"]["score"], 0.0)

    def test_cli_gateway_discover_and_inspect(self) -> None:
        self.run_cli("init")
        # Discover all bootstrap seed
        res = self.run_cli("gateway", "discover")
        data = json.loads(res.stdout)
        self.assertGreater(data.get("routes_registered", 0), 10)

        # Ingest specific provider with custom account
        res = self.run_cli("gateway", "discover", "--provider", "groq", "--account", "corp_acc")
        data = json.loads(res.stdout)
        self.assertEqual(data.get("provider_id"), "groq")
        self.assertEqual(data.get("account_id"), "corp_acc")
        self.assertGreater(data.get("routes_ingested", 0), 0)

        # Inspect specific route
        res = self.run_cli("gateway", "inspect", "llama-3.3-70b@groq:corp_acc")
        data = json.loads(res.stdout)
        self.assertEqual(data["route"]["account_id"], "corp_acc")
        self.assertEqual(data["route"]["provider_id"], "groq")
        self.assertEqual(data["identity"]["canonical_id"], "llama-3.3-70b")

        # Verify route
        res = self.run_cli("gateway", "verify-route", "llama-3.3-70b@groq:corp_acc")
        data = json.loads(res.stdout)
        self.assertEqual(data.get("status"), "VERIFIED_OFFLINE_PROBE")

    def test_cli_gateway_errors(self) -> None:
        self.run_cli("init")
        # Unknown inspect route
        res = self.run_cli("gateway", "inspect", "non_existent_route_12345", expected=2)
        data = json.loads(res.stdout)
        self.assertEqual(data.get("error_code"), "gateway_route_not_found")

        # Unknown provider in discover
        res = self.run_cli("gateway", "discover", "--provider", "non_existent_provider_xyz", expected=2)
        data = json.loads(res.stdout)
        self.assertEqual(data.get("error_code"), "gateway_provider_unknown")


if __name__ == "__main__":
    unittest.main()
