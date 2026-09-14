from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from w1cip.cli import main as cli_main


class CollaborationCLITests(unittest.TestCase):
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

    def test_benchmark(self) -> None:
        result = self._run("collab", "benchmark")
        self.assertTrue(result["passed"])

    def test_team_project_share_replica_and_sync(self) -> None:
        team = self._run("collab", "team", "create", "--name", "Team", "--owner", "alice", "--id", "team1")
        self.assertEqual(team["team_id"], "team1")
        invite = self._run("collab", "invite", "create", "team1", "--created-by", "alice", "--role", "member", "--expires-hours", "1")
        token = invite["one_time_token"]

        stdout, stderr = io.StringIO(), io.StringIO()
        argv = ["--workspace", str(self.workspace), "--json", "collab", "invite", "accept", "--principal", "bob", "--stdin"]
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr), patch("sys.stdin", io.StringIO(token)):
            rc = cli_main(argv)
        self.assertEqual(rc, 0)

        project = self._run("collab", "project", "create", "team1", "--name", "P", "--created-by", "alice", "--id", "p1")
        self.assertEqual(project["project_id"], "p1")
        self._run("collab", "share", "grant", "p1", "bob", "editor", "--actor", "alice")
        self._run("collab", "replica", "register", "team1", "--principal", "bob", "--id", "bobdev")
        mutation = {
            "mutation_id": "m1", "project_id": "p1", "device_id": "bobdev", "sequence": 1,
            "base_revision": 0, "key": "x", "operation": "set", "value": 7,
            "previous_event_hash": None, "created_at": "2026-08-08T00:00:00Z"
        }
        path = self.workspace / "mutation.json"
        path.write_text(json.dumps(mutation), encoding="utf-8")
        pushed = self._run("collab", "sync", "push", "--mutation", str(path), "--principal", "bob")
        self.assertTrue(pushed["accepted"])
        state = self._run("collab", "project", "state", "p1", "--principal", "bob")
        self.assertEqual(state["values"]["x"], 7)
        audit = self._run("collab", "audit", "verify", "team1")
        self.assertTrue(audit["valid"])


if __name__ == "__main__":
    unittest.main()
