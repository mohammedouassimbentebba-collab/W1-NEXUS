"""Unit tests for Native C Aerospace Sandbox Core."""

import unittest
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from w1cip.native_sandbox import (
    diagnose_sandbox_status,
    execute_sandboxed,
    SandboxExecutionResult,
)


class NativeSandboxTests(unittest.TestCase):
    def test_sandbox_diagnostics(self):
        diag = diagnose_sandbox_status()
        self.assertIn("platform", diag)
        self.assertIn("backend", diag)
        self.assertTrue(diag.get("unconstrained_compute", False))

    def test_successful_command_execution(self):
        res = execute_sandboxed("echo NEXUS_SANDBOX_ACTIVE", timeout_s=5.0)
        self.assertEqual(res.exit_code, 0)
        self.assertFalse(res.timed_out)
        self.assertFalse(res.memory_exceeded)
        self.assertIn("NEXUS_SANDBOX_ACTIVE", res.stdout)
        self.assertGreater(res.elapsed_ms, 0)

    def test_timeout_containment_without_crash(self):
        # 1-second timeout on a 5-second sleep
        cmd = "powershell -Command Start-Sleep -Seconds 5"
        res = execute_sandboxed(cmd, timeout_s=1.0)
        self.assertTrue(res.timed_out)
        self.assertEqual(res.exit_code, 124)
        self.assertLess(res.elapsed_ms, 3000)

    def test_unconstrained_compute_throughput(self):
        # Verify CPU math calculation runs at full hardware speed
        py_math = "python -c \"import math; print(sum(math.sqrt(i) for i in range(100000)))\""
        res = execute_sandboxed(py_math, timeout_s=10.0, memory_limit_mb=2048)
        self.assertEqual(res.exit_code, 0)
        self.assertFalse(res.timed_out)
        self.assertGreater(float(res.stdout.strip()), 0)


if __name__ == "__main__":
    unittest.main()
