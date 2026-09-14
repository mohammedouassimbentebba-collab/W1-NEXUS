from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from w1cip.parallel_runtime import (
    AgentJob,
    MergeCoordinator,
    MergeReviewInvalid,
    ParallelAgentRuntime,
    ParallelPlanError,
    ParallelRunPolicy,
)


class ParallelRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        subprocess.run(["git", "init", "-b", "main"], cwd=self.root, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "W1 Test"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.email", "test@w1.local"], cwd=self.root, check=True)
        (self.root / "README.md").write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-m", "base"], cwd=self.root, check=True, capture_output=True)
        self.runtime = ParallelAgentRuntime(self.root, policy=ParallelRunPolicy(max_workers=2, lock_wait_seconds=5))

    def tearDown(self) -> None:
        self.runtime.close()
        self.temp.cleanup()

    def job(self, job_id: str, agent_id: str, path: str, content: str, **kwargs):
        code = f"from pathlib import Path; Path({path!r}).write_text({content!r}, encoding='utf-8')"
        return AgentJob(
            job_id=job_id,
            agent_id=agent_id,
            branch=f"w1/{agent_id}",
            argv=(sys.executable, "-c", code),
            expected_outputs=(path,),
            **kwargs,
        )

    def test_parallel_jobs_create_isolated_commits_and_events(self) -> None:
        jobs = [
            self.job("job-a", "agent-a", "a.txt", "A"),
            self.job("job-b", "agent-b", "b.txt", "B"),
        ]
        result = self.runtime.run(jobs, run_id="run-one", approved_by="human-owner")
        self.assertEqual("completed", result["run"]["status"])
        self.assertEqual({"completed"}, {j["status"] for j in result["jobs"]})
        self.assertTrue(all(j["result"]["commit_sha"] for j in result["jobs"]))
        self.assertFalse((self.root / "a.txt").exists())
        self.assertIn("agent_job_finished", {e["event_type"] for e in result["events"]})

    def test_dependencies_block_downstream_after_failure(self) -> None:
        fail = AgentJob("job-fail", "agent-fail", (sys.executable, "-c", "raise SystemExit(2)"), "w1/fail")
        downstream = self.job("job-down", "agent-down", "down.txt", "x", dependencies=("job-fail",))
        result = self.runtime.run([fail, downstream], run_id="run-fail", approved_by="human-owner")
        states = {j["job_id"]: j["status"] for j in result["jobs"]}
        self.assertEqual("failed", states["job-fail"])
        self.assertEqual("blocked", states["job-down"])

    def test_resource_locks_serialize_overlapping_paths(self) -> None:
        active = 0
        max_active = 0
        guard = threading.Lock()

        class Executor:
            def execute(inner, job, worktree, cancel):
                nonlocal active, max_active
                with guard:
                    active += 1
                    max_active = max(max_active, active)
                time.sleep(0.15)
                (worktree / f"{job.agent_id}.txt").write_text("x", encoding="utf-8")
                with guard:
                    active -= 1
                return 0, "", ""

        self.runtime.executor = Executor()
        jobs = [
            AgentJob("lock-a", "lock-agent-a", ("noop",), "w1/lock-a", resource_paths=("src/shared",)),
            AgentJob("lock-b", "lock-agent-b", ("noop",), "w1/lock-b", resource_paths=("src/shared/file.py",)),
        ]
        self.runtime.run(jobs, run_id="run-lock", approved_by="human-owner")
        self.assertEqual(1, max_active)

    def test_merge_requires_independent_review_and_fast_forwards_target(self) -> None:
        jobs = [
            self.job("merge-a", "merge-agent-a", "a.txt", "A"),
            self.job("merge-b", "merge-agent-b", "b.txt", "B"),
        ]
        self.runtime.run(jobs, run_id="run-merge", approved_by="human-owner")
        coordinator = MergeCoordinator(self.runtime)
        proposal = coordinator.propose("run-merge", test_argv=(sys.executable, "-c", "import pathlib; assert pathlib.Path('a.txt').exists() and pathlib.Path('b.txt').exists()"))
        self.assertEqual("tests_passed", proposal.status)
        with self.assertRaises(MergeReviewInvalid):
            coordinator.review(proposal.proposal_id, reviewer_id="merge-agent-a", outcome="approved", rationale="self review")
        coordinator.review(proposal.proposal_id, reviewer_id="independent-reviewer", outcome="approved", rationale="tests and diff accepted")
        commit = coordinator.apply(proposal.proposal_id, approved_by="human-owner")
        self.assertEqual(commit, subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.root, text=True, capture_output=True, check=True).stdout.strip())
        self.assertEqual("A", (self.root / "a.txt").read_text(encoding="utf-8"))
        self.assertEqual("B", (self.root / "b.txt").read_text(encoding="utf-8"))


    def test_conflicting_agent_changes_stop_at_integration(self) -> None:
        (self.root / "shared.txt").write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "add", "shared.txt"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-m", "shared base"], cwd=self.root, check=True, capture_output=True)
        jobs = [
            self.job("conflict-a", "conflict-agent-a", "shared.txt", "A\n"),
            self.job("conflict-b", "conflict-agent-b", "shared.txt", "B\n"),
        ]
        self.runtime.run(jobs, run_id="run-conflict", approved_by="owner")
        proposal = MergeCoordinator(self.runtime).propose("run-conflict")
        self.assertEqual("conflicted", proposal.status)
        self.assertIn("shared.txt", proposal.potential_conflicts)

    def test_cancel_stops_running_agent(self) -> None:
        class SlowExecutor:
            def execute(inner, job, worktree, cancel):
                for _ in range(100):
                    if cancel.is_set():
                        return 130, "", "cancelled"
                    time.sleep(0.02)
                return 0, "", ""
        self.runtime.executor = SlowExecutor()
        job = AgentJob("slow-job", "slow-agent", ("noop",), "w1/slow")
        holder = {}
        thread = threading.Thread(target=lambda: holder.setdefault("result", self.runtime.run([job], run_id="run-cancel", approved_by="owner")))
        thread.start()
        for _ in range(100):
            if "run-cancel" in self.runtime._cancel:
                break
            time.sleep(0.01)
        self.runtime.cancel("run-cancel")
        thread.join(timeout=10)
        self.assertFalse(thread.is_alive())
        self.assertEqual("cancelled", holder["result"]["run"]["status"])

    def test_cycle_and_duplicate_agents_rejected(self) -> None:
        with self.assertRaises(ParallelPlanError):
            self.runtime.run([
                AgentJob("a", "same", ("x",), "w1/a", dependencies=("b",)),
                AgentJob("b", "other", ("x",), "w1/b", dependencies=("a",)),
            ], run_id="cycle", approved_by="owner")
        with self.assertRaises(ParallelPlanError):
            self.runtime.run([
                AgentJob("a", "same", ("x",), "w1/a"),
                AgentJob("b", "same", ("x",), "w1/b"),
            ], run_id="dupe", approved_by="owner")


if __name__ == "__main__":
    unittest.main()

class ParallelCLITests(unittest.TestCase):
    def test_cli_parallel_plan_and_status(self) -> None:
        from w1cip.cli import main
        from w1cip.cli_support import initialize_workspace
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "W1 CLI"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "cli@w1.local"], cwd=root, check=True)
            (root / "base.txt").write_text("base", encoding="utf-8")
            subprocess.run(["git", "add", "base.txt"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-m", "base"], cwd=root, check=True, capture_output=True)
            initialize_workspace(root)
            plan = {
                "run_id": "cli-parallel",
                "jobs": [{
                    "job_id": "cli-job",
                    "agent_id": "cli-agent",
                    "branch": "w1/cli-agent",
                    "argv": [sys.executable, "-c", "from pathlib import Path; Path('cli.txt').write_text('ok')"],
                    "expected_outputs": ["cli.txt"]
                }]
            }
            # Keep the plan outside the repository to preserve the clean-repo precondition.
            outside = root.parent / f"{root.name}-plan.json"
            outside.write_text(json.dumps(plan), encoding="utf-8")
            try:
                self.assertEqual(0, main(["--workspace", str(root), "--json", "agents", "run", "--plan", str(outside), "--approved-by", "owner"]))
                self.assertEqual(0, main(["--workspace", str(root), "--json", "agents", "status", "cli-parallel"]))
            finally:
                outside.unlink(missing_ok=True)
