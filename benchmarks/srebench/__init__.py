"""SRE-Bench Suite: Strict One-Attempt Mode for Site Reliability Engineering Incidents."""

from __future__ import annotations

from typing import Sequence
from ..base import BenchmarkSuite
from w1cip.benchmark_core.runner import BenchmarkTaskSpec


class SREBenchSuite(BenchmarkSuite):
    name = "SRE-Bench"
    version = "1.1.0"
    release_tag = "srebench-v1.1-one-attempt"
    is_public_local = True

    def get_smoke_task(self) -> BenchmarkTaskSpec:
        return BenchmarkTaskSpec(
            task_id="sre-smoke-01",
            benchmark=self.name,
            category="Incident Triage",
            prompt="Alert: 'HTTP 504 Gateway Timeout spike on /checkout'. Primary upstream database CPU is at 12%, but connection pool metrics show 500/500 active connections in wait queue. What is the immediate bottleneck (Connection Pool Exhaustion, Memory Leak, or DNS Failure)?",
            expected_answer="Connection Pool Exhaustion",
            strict_one_attempt=True,  # STRICT ONE ATTEMPT ENFORCEMENT
            timeout_seconds=45.0,
        )

    def get_tasks(self) -> Sequence[BenchmarkTaskSpec]:
        return [
            self.get_smoke_task(),
            BenchmarkTaskSpec(
                task_id="sre-k8s-01",
                benchmark=self.name,
                category="Kubernetes Infrastructure",
                prompt="Pod status is 'CrashLoopBackOff' and exit code is 137. What was the cause of the pod termination (OOMKilled, Liveness Probe Failure, or Segfault)?",
                expected_answer="OOMKilled",
                strict_one_attempt=True,
            ),
            BenchmarkTaskSpec(
                task_id="sre-disk-01",
                benchmark=self.name,
                category="Disk & Filesystem",
                prompt="Command 'df -h' reports root filesystem at 100% capacity with 0 bytes free. Which Linux command locates the top 5 largest files in /var/log? Answer with the primary command tool (du, ls, or find).",
                expected_answer="du",
                strict_one_attempt=True,
            ),
        ]
