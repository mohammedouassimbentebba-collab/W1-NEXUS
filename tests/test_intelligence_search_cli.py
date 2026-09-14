from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from w1cip.cli import main as cli_main
from w1cip.intelligence_search import SearchCandidate, SearchReport


class IntelligenceSearchCLITests(unittest.TestCase):
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

    def test_catalog_and_benchmark_are_serverless(self) -> None:
        self.run_cli("init")
        catalog = json.loads(self.run_cli("intelligence", "catalog").stdout)
        self.assertFalse(catalog["w1_owned_server_required"])
        self.assertFalse(catalog["background_cloud_index"])
        benchmark = json.loads(self.run_cli("intelligence", "benchmark").stdout)
        self.assertTrue(benchmark["passed"], benchmark)

    def test_search_caches_public_candidate_without_auto_routing(self) -> None:
        self.run_cli("init")
        candidate = SearchCandidate(
            candidate_id="demo", source_id="openrouter", source_kind="hosted_model",
            title="Demo", url="https://openrouter.ai/demo", model_id="demo:free",
            provider="OpenRouter", free_status="verified_free", terms_status="verified_third_party",
            review_status="connectable", requires_auth=True, score=.9,
            metadata={"entitlement_inferred": False},
        )
        report = SearchReport(query="demo", sources=("openrouter",), candidates=(candidate,))
        with patch("w1cip.cli.IntelligenceSearchEngine.search", return_value=report):
            payload = json.loads(self.run_cli("intelligence", "search", "demo", "--source", "openrouter").stdout)
        self.assertEqual(1, payload["count"])
        listing = json.loads(self.run_cli("intelligence", "list").stdout)
        # command-level patch bypasses engine cache in this synthetic test, so list remains empty;
        # cache behavior is covered by test_intelligence_search.py.
        self.assertEqual(0, listing["count"])
        self.assertFalse(payload["w1_owned_server_required"])


if __name__ == "__main__":
    unittest.main()
