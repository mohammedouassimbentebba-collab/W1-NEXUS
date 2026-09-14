from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

from w1cip.cli import main as cli_main
from w1cip.parallel_runtime import AgentJob, ParallelAgentRuntime, ParallelRunPolicy
from w1cip.secure_execution import (
    NetworkPolicy,
    SandboxAttestationInvalid,
    SandboxBackendUnavailable,
    SandboxLimits,
    SandboxPolicyDenied,
    SandboxProfile,
    SandboxRequest,
    SecureExecutionFabric,
)


class SecureExecutionFabricTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.fabric = SecureExecutionFabric(self.root)
        self.local_profile = SandboxProfile(
            profile_id="test-local",
            backend="local",
            root_read_only=False,
            require_hard_isolation=False,
            network=NetworkPolicy(mode="inherit"),
            limits=SandboxLimits(
                wall_seconds=5,
                cpu_seconds=3,
                memory_mb=1024,
                pids=256,
                output_bytes=4096,
                artifact_bytes=1024 * 1024,
            ),
            allowed_commands=(Path(sys.executable).name,),
            environment_allowlist=("PATH", "LANG", "PYTHONPATH"),
            artifact_globs=("artifacts/**",),
            secret_names=("TEST_SECRET",),
        )
        self.fabric.save_profile(self.local_profile)

    def tearDown(self) -> None:
        self.fabric.close()
        self.temp.cleanup()

    def request(self, execution_id: str, code: str, **kwargs) -> SandboxRequest:
        return SandboxRequest(
            execution_id=execution_id,
            profile_id="test-local",
            argv=(sys.executable, "-c", code),
            **kwargs,
        )

    def test_local_execution_extracts_artifact_and_attests_without_persisting_secret(self) -> None:
        secret = "super-sensitive-test-value"
        code = (
            "from pathlib import Path; import os; "
            "Path('artifacts').mkdir(); "
            "Path('artifacts/result.txt').write_text('ok', encoding='utf-8'); "
            "print('secret=' + os.environ.get('TEST_SECRET', ''))"
        )
        result = self.fabric.execute(
            self.request("secure-artifact", code, secret_values={"TEST_SECRET": secret})
        )
        self.assertEqual("completed", result.status, result.stderr)
        self.assertEqual(1, len(result.artifacts))
        self.assertEqual("artifacts/result.txt", result.artifacts[0].relative_path)
        self.assertTrue(Path(result.artifacts[0].stored_path).is_file())
        self.assertIn("secret=[REDACTED_SECRET]", result.stdout)
        self.assertNotIn(secret, json.dumps(self.fabric.journal.get("secure-artifact")))
        self.assertTrue(self.fabric.verify_attestation(result.attestation))
        self.assertFalse(result.attestation.claims["secret_values_persisted_by_w1_control_plane"])

    def test_timeout_force_terminates_process(self) -> None:
        profile = SandboxProfile(
            profile_id="timeout-local",
            backend="local",
            root_read_only=False,
            require_hard_isolation=False,
            network=NetworkPolicy(mode="inherit"),
            limits=SandboxLimits(wall_seconds=1, cpu_seconds=5, memory_mb=1024, pids=256),
            allowed_commands=(Path(sys.executable).name,),
            artifact_globs=(),
        )
        self.fabric.save_profile(profile)
        result = self.fabric.execute(
            SandboxRequest(
                execution_id="timeout-process",
                profile_id="timeout-local",
                argv=(sys.executable, "-c", "import time; time.sleep(10)"),
            )
        )
        self.assertEqual("timed_out", result.status)
        self.assertLess(result.telemetry.wall_seconds, 5)
        self.assertEqual("process_watchdog", result.telemetry.enforcement["wall"])

    def test_output_is_capped_and_marked(self) -> None:
        profile = SandboxProfile(
            profile_id="output-local",
            backend="local",
            root_read_only=False,
            require_hard_isolation=False,
            network=NetworkPolicy(mode="inherit"),
            limits=SandboxLimits(wall_seconds=5, cpu_seconds=3, memory_mb=1024, pids=256, output_bytes=128),
            allowed_commands=(Path(sys.executable).name,),
            artifact_globs=(),
        )
        self.fabric.save_profile(profile)
        result = self.fabric.execute(
            SandboxRequest(
                execution_id="output-cap",
                profile_id="output-local",
                argv=(sys.executable, "-c", "print('x' * 2000000)"),
            )
        )
        self.assertTrue(result.telemetry.output_truncated)
        self.assertLessEqual(len(result.stdout.encode()) + len(result.stderr.encode()), 128)

    def test_hard_isolation_never_silently_falls_back_to_local(self) -> None:
        profile = SandboxProfile(
            profile_id="hard-local",
            backend="local",
            image=None,
            require_hard_isolation=True,
            network=NetworkPolicy(mode="none"),
            allowed_commands=(Path(sys.executable).name,),
        )
        self.fabric.save_profile(profile)
        request = SandboxRequest(
            execution_id="hard-local-denied",
            profile_id="hard-local",
            argv=(sys.executable, "-c", "print('no')"),
        )
        with self.assertRaises(SandboxBackendUnavailable):
            self.fabric.plan(request)

    def test_local_backend_refuses_network_policy_it_cannot_enforce(self) -> None:
        profile = SandboxProfile(
            profile_id="local-no-network",
            backend="local",
            require_hard_isolation=False,
            network=NetworkPolicy(mode="none"),
            allowed_commands=(Path(sys.executable).name,),
        )
        self.fabric.save_profile(profile)
        with self.assertRaises(SandboxPolicyDenied):
            self.fabric.plan(
                SandboxRequest(
                    execution_id="local-network-denied",
                    profile_id="local-no-network",
                    argv=(sys.executable, "-c", "print('x')"),
                )
            )

    def test_attestation_tampering_is_detected(self) -> None:
        result = self.fabric.execute(self.request("attestation-test", "print('ok')"))
        payload = asdict(result.attestation)
        payload["claims"]["hard_isolation"] = True
        with self.assertRaises(SandboxAttestationInvalid):
            self.fabric.verify_attestation(payload)

    def test_oci_command_has_hardening_and_never_contains_secret_value(self) -> None:
        profile = SandboxProfile(
            profile_id="oci-test",
            backend="docker",
            image="python:3.13-slim",
            root_read_only=True,
            network=NetworkPolicy(mode="none"),
            limits=SandboxLimits(memory_mb=512, pids=32),
            allowed_commands=("python",),
            secret_names=("API_TOKEN",),
        )
        request = SandboxRequest(
            execution_id="oci-command",
            profile_id="oci-test",
            argv=("python", "-c", "print('ok')"),
            secret_values={"API_TOKEN": "must-not-appear"},
        )
        with tempfile.TemporaryDirectory() as td:
            command = self.fabric._oci_command(request, profile, "docker", Path(td))
        joined = " ".join(command)
        self.assertIn("--read-only", command)
        self.assertIn("--network none", joined)
        self.assertIn("--cap-drop ALL", joined)
        self.assertIn("--memory 512m", joined)
        self.assertIn("--cpus 1.0", joined)
        self.assertIn("--env-file", command)
        self.assertNotIn("must-not-appear", joined)


    def test_same_execution_request_is_idempotent(self) -> None:
        request = self.request("idempotent-execution", "print('once')")
        first = self.fabric.execute(request)
        second = self.fabric.execute(request)
        self.assertEqual(first.attestation.attestation_id, second.attestation.attestation_id)
        self.assertEqual(1, len([x for x in self.fabric.journal.list_executions() if x["execution_id"] == "idempotent-execution"]))

    def test_mount_outside_profile_roots_is_denied(self) -> None:
        profile = SandboxProfile(
            profile_id="mount-policy",
            backend="docker",
            image="python:3.13-slim",
            require_hard_isolation=True,
            network=NetworkPolicy(mode="none"),
            allowed_commands=("python",),
            mount_source_roots=("allowed",),
        )
        allowed = self.root / "allowed"
        allowed.mkdir()
        outside = self.root / "outside"
        outside.mkdir()
        request = SandboxRequest(
            execution_id="mount-outside",
            profile_id="mount-policy",
            argv=("python", "-c", "print('x')"),
            mounts=(__import__('w1cip.secure_execution', fromlist=['MountSpec']).MountSpec(source=str(outside), target="/data", mode="ro"),),
        )
        # Validate the policy directly so the test does not depend on a Docker daemon.
        with self.assertRaises(SandboxPolicyDenied):
            self.fabric._validate_request(request, profile, "docker")

    def test_symlink_artifact_is_not_extracted(self) -> None:
        if os.name == "nt":
            self.skipTest("symlink permissions vary on Windows")
        target = self.root / "outside.txt"
        target.write_text("outside", encoding="utf-8")
        artifacts = self.root / "artifacts"
        artifacts.mkdir()
        (artifacts / "link.txt").symlink_to(target)
        result = self.fabric.execute(self.request("symlink-artifact", "print('ok')"))
        self.assertEqual((), result.artifacts)


class SecureExecutionCLITests(unittest.TestCase):
    def test_cli_profile_plan_run_status_and_attestation(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(0, cli_main(["--workspace", str(root), "--json", "init"]))
            profile = {
                "profile_id": "cli-local",
                "backend": "local",
                "root_read_only": False,
                "require_hard_isolation": False,
                "network": {"mode": "inherit"},
                "allowed_commands": [Path(sys.executable).name],
                "artifact_globs": ["artifacts/**"],
                "limits": {"wall_seconds": 5, "cpu_seconds": 3, "memory_mb": 1024, "pids": 256},
            }
            request = {
                "execution_id": "cli-sandbox",
                "profile_id": "cli-local",
                "argv": [sys.executable, "-c", "from pathlib import Path; Path('artifacts').mkdir(); Path('artifacts/cli.txt').write_text('ok')"],
            }
            profile_path = root.parent / f"{root.name}-profile.json"
            request_path = root.parent / f"{root.name}-request.json"
            profile_path.write_text(json.dumps(profile), encoding="utf-8")
            request_path.write_text(json.dumps(request), encoding="utf-8")
            try:
                self.assertEqual(0, cli_main(["--workspace", str(root), "--json", "sandboxes", "profiles", "add", "--file", str(profile_path)]))
                self.assertEqual(0, cli_main(["--workspace", str(root), "--json", "sandboxes", "plan", "--request", str(request_path)]))
                self.assertEqual(0, cli_main(["--workspace", str(root), "--json", "sandboxes", "run", "--request", str(request_path)]))
                self.assertEqual(0, cli_main(["--workspace", str(root), "--json", "sandboxes", "status", "cli-sandbox"]))
                self.assertEqual(0, cli_main(["--workspace", str(root), "--json", "sandboxes", "verify-attestation", "cli-sandbox"]))
            finally:
                profile_path.unlink(missing_ok=True)
                request_path.unlink(missing_ok=True)


class SecureParallelIntegrationTests(unittest.TestCase):
    def test_parallel_agent_can_use_declared_sandbox_profile(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "W1 Test"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "test@w1.local"], cwd=root, check=True)
            (root / "README.md").write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "add", "README.md"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-m", "base"], cwd=root, check=True, capture_output=True)
            fabric = SecureExecutionFabric(root)
            fabric.save_profile(
                SandboxProfile(
                    profile_id="agent-local",
                    backend="local",
                    root_read_only=False,
                    require_hard_isolation=False,
                    network=NetworkPolicy(mode="inherit"),
                    limits=SandboxLimits(wall_seconds=10, cpu_seconds=5, memory_mb=1024, pids=256),
                    allowed_commands=(Path(sys.executable).name,),
                    artifact_globs=(),
                )
            )
            fabric.close()
            runtime = ParallelAgentRuntime(root, policy=ParallelRunPolicy(max_workers=1, lock_wait_seconds=5))
            try:
                job = AgentJob(
                    job_id="sandbox-job",
                    agent_id="sandbox-agent",
                    branch="w1/sandbox-agent",
                    argv=(sys.executable, "-c", "from pathlib import Path; Path('sandbox.txt').write_text('ok')"),
                    expected_outputs=("sandbox.txt",),
                    sandbox_profile="agent-local",
                )
                result = runtime.run([job], run_id="sandbox-parallel", approved_by="owner")
                self.assertEqual("completed", result["run"]["status"])
                journal = SecureExecutionFabric(root)
                try:
                    executions = journal.journal.list_executions()
                    self.assertEqual(1, len(executions))
                    self.assertEqual("completed", executions[0]["status"])
                finally:
                    journal.close()
            finally:
                runtime.close()


if __name__ == "__main__":
    unittest.main()
