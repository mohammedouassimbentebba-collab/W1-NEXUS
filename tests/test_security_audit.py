"""W1™ NEXUS Security Audit & Hardening Regression Test Suite.

Verifies:
1. Studio /api/execute requires valid X-Nexus-Auth-Token (rejection with 401).
2. Studio path traversal defenses block attempts to access files outside studio/public/.
3. C Sandbox handles large stdout bursts (>128 KB) without pipe deadlock.
4. C Sandbox enforces timeout watchdog (exit code 124) on infinite loops.
5. Zero leakage of API keys (sk-* / nvapi-*) across runtime endpoints and telemetry.
"""

import json
import os
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from w1cip.native_sandbox import execute_sandboxed, diagnose_sandbox_status


class TestSecurityAudit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Start a local studio server on an ephemeral test port
        cls.test_port = 8899
        cls.test_token = "test-secret-token-w1-nexus-security-777"
        env = dict(os.environ)
        env["PORT"] = str(cls.test_port)
        env["HOST"] = "127.0.0.1"
        env["NEXUS_STUDIO_TOKEN"] = cls.test_token

        server_js = PROJECT_ROOT / "studio" / "server.js"
        cls.proc = subprocess.Popen(
            ["node", str(server_js)],
            cwd=str(PROJECT_ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        # Wait for server to bind
        time.sleep(1.0)

    @classmethod
    def tearDownClass(cls):
        if cls.proc and cls.proc.poll() is None:
            cls.proc.terminate()
            cls.proc.wait(timeout=3)

    def test_01_execute_unauthorized_rejection(self):
        """Verifies that /api/execute strictly rejects requests without auth token."""
        url = f"http://127.0.0.1:{self.test_port}/api/execute"
        req = urllib.request.Request(
            url,
            data=json.dumps({"command": "echo FORBIDDEN"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(ctx.exception.code, 401)

    def test_02_execute_invalid_token_rejection(self):
        """Verifies that /api/execute strictly rejects requests with invalid auth token."""
        url = f"http://127.0.0.1:{self.test_port}/api/execute"
        req = urllib.request.Request(
            url,
            data=json.dumps({"command": "echo FORBIDDEN"}).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "X-Nexus-Auth-Token": "invalid-bogus-token-123",
            },
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(ctx.exception.code, 401)

    def test_03_execute_authorized_success(self):
        """Verifies that /api/execute succeeds when providing valid X-Nexus-Auth-Token."""
        url = f"http://127.0.0.1:{self.test_port}/api/execute"
        req = urllib.request.Request(
            url,
            data=json.dumps({"command": "echo AUTH_AUTHORIZED_PASS"}).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "X-Nexus-Auth-Token": self.test_token,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                self.assertEqual(resp.status, 200)
                data = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(data.get("exit_code"), 0)
                self.assertIn("AUTH_AUTHORIZED_PASS", data.get("stdout", ""))
        except urllib.error.HTTPError as err:
            err_body = err.read().decode("utf-8")
            self.fail(f"HTTPError {err.code}: {err_body}")

    def test_04_directory_traversal_rejection(self):
        """Verifies that requests attempting to escape public directory receive HTTP 403."""
        traversal_urls = [
            f"http://127.0.0.1:{self.test_port}/../package.json",
            f"http://127.0.0.1:{self.test_port}/..%2fpackage.json",
            f"http://127.0.0.1:{self.test_port}/../../.env",
        ]
        for url in traversal_urls:
            try:
                urllib.request.urlopen(url, timeout=3)
                self.fail(f"Path traversal URL should have failed: {url}")
            except urllib.error.HTTPError as err:
                self.assertIn(err.code, [403, 404])

    def test_05_c_sandbox_high_volume_pipe_throughput(self):
        """Verifies that C Sandbox handles massive stdout without pipe buffer deadlock."""
        target_bytes = 150000
        cmd = f'python -c "print(\'Z\' * {target_bytes})"'
        res = execute_sandboxed(cmd, timeout_s=5.0)

        self.assertEqual(res.exit_code, 0)
        self.assertFalse(res.timed_out)
        self.assertGreaterEqual(len(res.stdout), target_bytes)
        self.assertLess(res.elapsed_ms, 2000)

    def test_06_c_sandbox_timeout_containment(self):
        """Verifies that runaway processes are terminated with exit code 124."""
        cmd = 'python -c "import time; time.sleep(10)"'
        res = execute_sandboxed(cmd, timeout_s=1.0)

        self.assertEqual(res.exit_code, 124)
        self.assertTrue(res.timed_out)

    def test_07_zero_secret_leak_in_status_endpoint(self):
        """Verifies that /api/status does not leak API keys."""
        url = f"http://127.0.0.1:{self.test_port}/api/status"
        with urllib.request.urlopen(url, timeout=5) as resp:
            body = resp.read().decode("utf-8")
            self.assertNotIn("sk-", body)
            self.assertNotIn("nvapi-", body)
    def _raw(self, method, path, host=None, headers=None, body=None):
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", self.test_port, timeout=8)
        h = dict(headers or {})
        h["Host"] = host or f"127.0.0.1:{self.test_port}"
        conn.request(method, path, body=body, headers=h)
        resp = conn.getresponse()
        data = resp.read().decode("utf-8", errors="replace")
        hdrs = dict(resp.getheaders())
        conn.close()
        return resp.status, data, hdrs

    def test_08_dns_rebinding_host_rejected(self):
        """A foreign Host header (DNS rebinding) must not receive the page or the token."""
        status, body, _ = self._raw("GET", "/", host="evil.example.com")
        self.assertEqual(status, 421)
        self.assertNotIn(self.test_token, body)

    def test_09_query_token_rejected_on_execute(self):
        """Tokens in the query string must not authorize execution."""
        status, _, _ = self._raw(
            "POST", f"/api/execute?token={self.test_token}",
            headers={"Content-Type": "application/json"},
            body=json.dumps({"command": "echo X"}),
        )
        self.assertEqual(status, 401)

    def test_10_sse_requires_token(self):
        status, _, _ = self._raw("GET", "/api/events")
        self.assertEqual(status, 401)

    def test_11_security_headers_present(self):
        status, _, hdrs = self._raw("GET", "/api/status")
        self.assertEqual(status, 200)
        self.assertEqual(hdrs.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(hdrs.get("X-Frame-Options"), "DENY")
        self.assertIn("frame-ancestors 'none'", hdrs.get("Content-Security-Policy", ""))
        self.assertNotEqual(hdrs.get("Access-Control-Allow-Origin"), "*")

    def test_12_python_source_injection_neutralized(self):
        """Quotes/newlines in the command must not break out into the Python bridge."""
        payload = "echo SAFE\"); import os; print(\"PWNED"
        status, body, _ = self._raw(
            "POST", "/api/execute",
            headers={"Content-Type": "application/json", "X-Nexus-Auth-Token": self.test_token},
            body=json.dumps({"command": payload}),
        )
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data.get("backend"), "native_c_win32_job")
        self.assertNotIn("\nPWNED", data.get("stdout", ""))


if __name__ == "__main__":
    unittest.main()
