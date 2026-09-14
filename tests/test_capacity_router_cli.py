from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from w1cip.cli import main as cli_main


class CapacityRouterCLITests(unittest.TestCase):
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

    def test_benchmark_and_observation_round_trip(self) -> None:
        self.run_cli("init")
        benchmark = json.loads(self.run_cli("capacity", "benchmark").stdout)
        self.assertTrue(benchmark["passed"], benchmark)
        self.run_cli(
            "capacity", "observe", "local-coder",
            "--source", "local", "--quota-state", "unlimited", "--terms-status", "official",
        )
        listing = json.loads(self.run_cli("capacity", "list").stdout)
        self.assertEqual(1, listing["count"])
        self.assertEqual("local", listing["observations"][0]["source"])
        self.assertFalse(listing["raw_credentials_stored"])

    def test_adaptive_challenge_plan_is_saved_without_dropping_roles(self) -> None:
        self.run_cli("init")
        self.run_cli("connections", "add-local", "local-a", "--endpoint", "http://127.0.0.1:11434/v1/chat/completions")
        for model_id, quality, roles in (
            ("local-producer", "0.82", ("producer",)),
            ("local-challenger", "0.86", ("challenger",)),
            ("local-synth", "0.90", ("synthesizer",)),
        ):
            args = [
                "connections", "add-model", "local-a", model_id,
                "--model-name", model_id,
                "--capacity-source", "local",
                "--terms-status", "official",
                "--quality-score", quality,
                "--domain", "coding",
            ]
            for role in roles:
                args.extend(["--role", role])
            self.run_cli(*args)
            self.run_cli("capacity", "observe", model_id, "--source", "local", "--quota-state", "unlimited")
        task = self.workspace / "task.json"
        task.write_text(json.dumps({
            "task_id": "code-task", "title": "Code task", "phase": "execution",
            "role": "producer", "expected_output_type": "text", "domains": ["coding"]
        }), encoding="utf-8")
        plan = json.loads(self.run_cli(
            "capacity", "plan", "--task", str(task), "--team-mode", "challenge",
            "--routing-mode", "local_only", "--quality-floor", "0.6", "--producer-count", "1",
            "--fallback-per-role", "0", "--save-portfolio",
        ).stdout)["capacity_plan"]
        self.assertEqual("challenge_synthesis", plan["portfolio"]["strategy"])
        self.assertEqual({"producer", "challenger", "synthesizer"}, {m["role"] for m in plan["portfolio"]["members"]})
        portfolios = json.loads(self.run_cli("models", "portfolios", "list").stdout)["portfolios"]
        adaptive = [p for p in portfolios if p.get("metadata", {}).get("adaptive_capacity_router")]
        self.assertEqual(1, len(adaptive))
        self.assertEqual("challenge_synthesis", adaptive[0]["strategy"])


if __name__ == "__main__":
    unittest.main()
