"""Terminal-Bench 4.0 Suite: Systems, shell, and command-line tool reasoning."""

from __future__ import annotations

from typing import Sequence
from ..base import BenchmarkSuite
from w1cip.benchmark_core.runner import BenchmarkTaskSpec


class TerminalBench4Suite(BenchmarkSuite):
    name = "Terminal-Bench 4.0"
    version = "4.0.1"
    release_tag = "tb4-v4.0.1"
    is_public_local = True

    def get_smoke_task(self) -> BenchmarkTaskSpec:
        return BenchmarkTaskSpec(
            task_id="tb4-smoke-01",
            benchmark=self.name,
            category="File Manipulation",
            prompt="Write a bash command that counts the number of lines containing 'ERROR' in '/var/log/syslog'. Answer with only the command.",
            expected_answer="grep",
            timeout_seconds=30.0,
        )

    def get_tasks(self) -> Sequence[BenchmarkTaskSpec]:
        return [
            self.get_smoke_task(),
            BenchmarkTaskSpec(
                task_id="tb4-sed-01",
                benchmark=self.name,
                category="Text Processing",
                prompt="Given the sed command 'sed -i s/foo/bar/g config.txt', what will every occurrence of 'foo' be replaced with?",
                expected_answer="bar",
            ),
            BenchmarkTaskSpec(
                task_id="tb4-net-01",
                benchmark=self.name,
                category="Networking",
                prompt="What flag in curl causes it to follow HTTP redirects? Answer with the flag.",
                expected_answer="-L",
            ),
            BenchmarkTaskSpec(
                task_id="tb4-git-01",
                benchmark=self.name,
                category="Version Control",
                prompt="What git command aborts an in-progress merge conflict? Answer with the exact command line.",
                expected_answer="git merge --abort",
            ),
        ]
