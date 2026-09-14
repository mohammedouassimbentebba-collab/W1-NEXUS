"""Comprehensive lifecycle stress, concurrency, resource leak, and security tests for NativeServiceController."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from w1cip.native_packaging import (
    NativeServiceController,
    ServiceLifecycleError,
    process_marker,
    run_native_packaging_benchmark,
)


class NativePackagingLifecycleStressTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "workspace"
        self.root.mkdir()
        (self.root / ".w1nexus").mkdir(parents=True)

    def test_lifecycle_10_repeated_start_stop_cycles_no_zombies(self) -> None:
        """Scenario 1: 10 consecutive start/stop cycles with process termination verification."""
        controller = NativeServiceController(self.root)
        for i in range(10):
            port = 19100 + i
            state = controller.start(
                command=[sys.executable, "-c", "import time; time.sleep(30)"],
                port=port,
            )
            pid = state.pid
            self.assertTrue(controller.status()["running"])
            self.assertIsNotNone(process_marker(pid))

            stopped = controller.stop(timeout_seconds=3.0)
            self.assertTrue(stopped["stopped"])
            self.assertEqual(stopped["pid"], pid)
            self.assertFalse(controller.status()["running"])
            self.assertIsNone(process_marker(pid))
            self.assertFalse(controller.state_path.is_file())

    def test_lifecycle_10_repeated_benchmark_cycles(self) -> None:
        """Scenario 2: 10 repeated full native packaging benchmarks without resource leaks."""
        for _ in range(10):
            result = run_native_packaging_benchmark()
            self.assertTrue(result["passed"])
            self.assertGreaterEqual(result["metrics"]["probe_count"], 14)

    def test_lifecycle_start_stop_same_port_reuse(self) -> None:
        """Scenario 3: Start, stop, and immediately restart on the same port."""
        controller = NativeServiceController(self.root)
        port = 19200
        state1 = controller.start(
            command=[sys.executable, "-c", "import time; time.sleep(30)"],
            port=port,
        )
        self.assertTrue(controller.status()["running"])
        controller.stop(timeout_seconds=3.0)
        self.assertFalse(controller.status()["running"])

        state2 = controller.start(
            command=[sys.executable, "-c", "import time; time.sleep(30)"],
            port=port,
        )
        self.assertTrue(controller.status()["running"])
        self.assertNotEqual(state1.pid, state2.pid)
        controller.stop(timeout_seconds=3.0)
        self.assertFalse(controller.status()["running"])

    def test_lifecycle_idempotent_duplicate_stop(self) -> None:
        """Scenario 4: Duplicate stop() calls are safe, idempotent, and do not raise."""
        controller = NativeServiceController(self.root)
        controller.start(
            command=[sys.executable, "-c", "import time; time.sleep(30)"],
            port=19201,
        )
        stopped1 = controller.stop(timeout_seconds=3.0)
        self.assertTrue(stopped1["stopped"])

        # Second and third stops when already stopped
        stopped2 = controller.stop(timeout_seconds=3.0)
        self.assertFalse(stopped2["stopped"])
        self.assertEqual(stopped2["reason"], "not_running")

        stopped3 = controller.stop(timeout_seconds=3.0)
        self.assertFalse(stopped3["stopped"])
        self.assertEqual(stopped3["reason"], "not_running")

    def test_lifecycle_duplicate_start_rejected(self) -> None:
        """Scenario 5: Calling start() while already running is rejected fail-closed."""
        controller = NativeServiceController(self.root)
        controller.start(
            command=[sys.executable, "-c", "import time; time.sleep(30)"],
            port=19202,
        )
        try:
            with self.assertRaises(ServiceLifecycleError) as ctx:
                controller.start(
                    command=[sys.executable, "-c", "import time; time.sleep(30)"],
                    port=19203,
                )
            self.assertIn("already_running", str(ctx.exception))
        finally:
            controller.stop(timeout_seconds=3.0)

    def test_lifecycle_stale_state_auto_cleanup(self) -> None:
        """Scenario 6: Dead PID in state file is detected as stale and cleaned up on status/start/stop."""
        controller = NativeServiceController(self.root)
        # Write state with a non-existent PID
        fake_state = {
            "pid": 99999999,
            "process_marker": "stale-marker-99999999",
            "port": 19204,
            "started_at": "2026-08-20T00:00:00Z",
            "command_sha256": "0" * 64,
        }
        controller.state_path.write_text(json.dumps(fake_state), encoding="utf-8")

        # status() reports not running
        status = controller.status()
        self.assertFalse(status["running"])

        # stop() cleans up the stale state file
        stopped = controller.stop(timeout_seconds=1.0)
        self.assertFalse(stopped["stopped"])
        self.assertEqual(stopped["reason"], "stale_state_removed")
        self.assertFalse(controller.state_path.is_file())

        # Now start succeeds cleanly
        state = controller.start(
            command=[sys.executable, "-c", "import time; time.sleep(30)"],
            port=19204,
        )
        self.assertTrue(controller.status()["running"])
        controller.stop(timeout_seconds=3.0)

    def test_lifecycle_identity_mismatch_fails_closed(self) -> None:
        """Scenario 7: Forged or mismatched process marker prevents stopping unauthorized processes."""
        controller = NativeServiceController(self.root)
        state = controller.start(
            command=[sys.executable, "-c", "import time; time.sleep(30)"],
            port=19205,
        )
        real_pid = state.pid
        try:
            payload = json.loads(controller.state_path.read_text(encoding="utf-8"))
            payload["process_marker"] = "forged-attacker-marker"
            controller.state_path.write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaises(ServiceLifecycleError) as ctx:
                controller.stop(timeout_seconds=1.0)
            self.assertIn("identity_mismatch", str(ctx.exception))
        finally:
            # Clean up the actual running process directly
            owned = controller._owned_processes.get(real_pid)
            if owned is not None:
                owned.kill()
                owned.wait(timeout=2.0)
            elif os.name == "nt":
                import ctypes
                kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
                handle = kernel32.OpenProcess(1, False, real_pid)
                if handle:
                    kernel32.TerminateProcess(handle, 1)
                    kernel32.CloseHandle(handle)
            else:
                try:
                    os.kill(real_pid, signal.SIGKILL)
                except OSError:
                    pass

    def test_lifecycle_cross_workspace_isolation(self) -> None:
        """Scenario 8: Separate workspaces manage independent lifecycle states without conflict."""
        workspace2 = Path(self.temp.name) / "workspace2"
        workspace2.mkdir()
        (workspace2 / ".w1nexus").mkdir(parents=True)

        ctrl1 = NativeServiceController(self.root)
        ctrl2 = NativeServiceController(workspace2)

        state1 = ctrl1.start(
            command=[sys.executable, "-c", "import time; time.sleep(30)"],
            port=19206,
        )
        state2 = ctrl2.start(
            command=[sys.executable, "-c", "import time; time.sleep(30)"],
            port=19207,
        )

        self.assertTrue(ctrl1.status()["running"])
        self.assertTrue(ctrl2.status()["running"])
        self.assertNotEqual(state1.pid, state2.pid)

        ctrl1.stop(timeout_seconds=3.0)
        self.assertFalse(ctrl1.status()["running"])
        self.assertTrue(ctrl2.status()["running"])

        ctrl2.stop(timeout_seconds=3.0)
        self.assertFalse(ctrl2.status()["running"])


if __name__ == "__main__":
    unittest.main()
