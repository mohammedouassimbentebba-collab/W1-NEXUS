from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from w1cip.ai_connections import AIConnectionRecord, AIConnectionStore
from w1cip.cli import main as cli_main
from w1cip.cli_support import WorkspacePaths
from w1cip.intelligence_search import IntelligenceSearchStore, SearchCandidate


class IntelligenceActivationCLITests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name)
        self.paths = WorkspacePaths.from_root(self.workspace)
        self.run_cli("init")
        IntelligenceSearchStore(self.paths.intelligence_search_db).put_many((SearchCandidate(
            candidate_id="or-cli", source_id="openrouter", source_kind="hosted_model", title="CLI Free",
            url="https://openrouter.ai/demo/cli:free", provider="OpenRouter", model_id="demo/cli:free",
            free_status="verified_free", terms_status="verified_third_party", review_status="connectable",
            requires_auth=True, score=.96,
        ),))
        AIConnectionStore(self.paths.ai_connections_db).put_connection(AIConnectionRecord(
            connection_id="or-cli-main", provider_id="openrouter", display_name="OpenRouter CLI",
            auth_mode="api_key", credential_reference="w1-credential:cli-redacted",
            endpoint="https://openrouter.ai/api/v1/chat/completions", status="connected",
            discovered_models=("demo/cli:free",),
        ))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_cli(self, *arguments: str, expected: int = 0):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            rc = cli_main(["--workspace", str(self.workspace), "--json", "--no-color", *arguments])
        self.assertEqual(expected, rc, stdout.getvalue() + stderr.getvalue())
        return json.loads(stdout.getvalue()) if stdout.getvalue().strip() else {}

    def test_activation_benchmark_and_assessment(self):
        benchmark = self.run_cli("intelligence", "activation-benchmark")
        self.assertTrue(benchmark["passed"])
        assessment = self.run_cli("intelligence", "assess", "or-cli")
        self.assertTrue(assessment["activation_allowed"])

    def test_activate_and_list_records(self):
        result = self.run_cli("intelligence", "activate", "or-cli")
        self.assertEqual("activated", result["record"]["status"])
        listing = self.run_cli("intelligence", "activations")
        self.assertEqual(1, listing["count"])
        self.assertTrue(listing["audit_chain_valid"])
        self.assertFalse(listing["raw_credentials_stored"])

    def test_activate_with_explicit_profile_id_and_alias(self):
        result_custom = self.run_cli("intelligence", "activate", "or-cli", "--profile-id", "my-custom-free-model")
        self.assertEqual("activated", result_custom["record"]["status"])
        self.assertEqual("my-custom-free-model", result_custom["record"]["profile_id"])

        # Also test --profile alias
        result_alias = self.run_cli("intelligence", "activate", "or-cli", "--profile", "my-alias-free-model")
        self.assertEqual("activated", result_alias["record"]["status"])
        self.assertEqual("my-alias-free-model", result_alias["record"]["profile_id"])

    def test_activate_human_readable_output(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            rc = cli_main(["--workspace", str(self.workspace), "--no-color", "intelligence", "activate", "or-cli"])
        self.assertEqual(0, rc)
        out = stdout.getvalue()
        self.assertIn("Activated or-cli as", out)
        self.assertIn("third-party-free routing still requires explicit opt-in", out)

    def test_intelligence_review_cli(self):
        reviewed = self.run_cli("intelligence", "review", "or-cli", "--decision", "approved", "--note", "Verified candidate")
        self.assertEqual("approved", reviewed["record"]["decision"])
        self.assertTrue(reviewed["audit_chain_valid"])

    def test_activate_nonexistent_candidate_fails(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            rc = cli_main(["--workspace", str(self.workspace), "--json", "--no-color", "intelligence", "activate", "non-existent-candidate"])
        self.assertNotEqual(0, rc)


if __name__ == "__main__":
    unittest.main()

