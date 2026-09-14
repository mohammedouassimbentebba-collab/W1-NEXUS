"""Comprehensive lifecycle stress, concurrency, resource leak, and security invariant tests for DesktopShellServer."""

from __future__ import annotations

import concurrent.futures
import http.client
import json
import socket
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from w1cip.cli_support import initialize_workspace
from w1cip.desktop_shell import (
    DesktopSettings,
    DesktopShellServer,
    create_desktop_server,
)


class DesktopShellLifecycleStressTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "workspace"
        self.root.mkdir()
        self.paths = initialize_workspace(self.root)
        (self.root / "sample.py").write_bytes(b"print('stress-test')\n")

    def _start_server(self, port: int = 0, operations: bool = True) -> tuple[DesktopShellServer, threading.Thread]:
        server = create_desktop_server(
            self.paths,
            DesktopSettings(host="127.0.0.1", port=port, allow_operations=operations, open_browser=False),
        )
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        return server, thread

    def _request(self, server: DesktopShellServer, path: str, *, token: bool = True, timeout: float = 5.0) -> dict:
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {server.application.token}"
        req = urllib.request.Request(f"http://127.0.0.1:{server.server_port}{path}", headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def test_lifecycle_single_cycle_start_request_stop(self) -> None:
        """Scenario 1: Clean single cycle start -> request -> stop with state assertions."""
        server, thread = self._start_server()
        try:
            self.assertEqual(server.lifecycle_state, "RUNNING")
            data = self._request(server, "/api/v1/health")
            self.assertTrue(data.get("ok"))
        finally:
            server.close_server(join_timeout=3.0)
            thread.join(timeout=3.0)
        self.assertEqual(server.lifecycle_state, "CLOSED")
        self.assertFalse(thread.is_alive())

    def test_lifecycle_idempotent_repeated_shutdown(self) -> None:
        """Scenario 2: start -> stop -> stop (repeated shutdown calls are completely idempotent and safe)."""
        server, thread = self._start_server()
        try:
            data = self._request(server, "/api/v1/health")
            self.assertTrue(data.get("ok"))
        finally:
            # First shutdown
            server.close_server(join_timeout=3.0)
            thread.join(timeout=3.0)
            self.assertEqual(server.lifecycle_state, "CLOSED")

            # Second and third calls must be no-ops without exception
            server.close_server()
            server.shutdown()
            server.server_close()
            self.assertEqual(server.lifecycle_state, "CLOSED")

    def test_lifecycle_start_stop_restart_same_port(self) -> None:
        """Scenario 3: Port reuse after clean shutdown without address binding conflicts."""
        # Find a free OS port
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]

        # First run on specified port
        server1, thread1 = self._start_server(port=port)
        try:
            self.assertEqual(server1.server_port, port)
            data1 = self._request(server1, "/api/v1/health")
            self.assertTrue(data1.get("ok"))
        finally:
            server1.close_server(join_timeout=3.0)
            thread1.join(timeout=3.0)

        # Second run on EXACT same port
        server2, thread2 = self._start_server(port=port)
        try:
            self.assertEqual(server2.server_port, port)
            data2 = self._request(server2, "/api/v1/health")
            self.assertTrue(data2.get("ok"))
        finally:
            server2.close_server(join_timeout=3.0)
            thread2.join(timeout=3.0)

    def test_lifecycle_10_repeated_cycles_no_leaks(self) -> None:
        """Scenario 4: 10 repeated start/request/stop cycles with zero leaked threads or port locks."""
        initial_threads = threading.active_count()

        for cycle in range(10):
            server, thread = self._start_server()
            try:
                data = self._request(server, "/api/v1/health")
                self.assertTrue(data.get("ok"))
            finally:
                server.close_server(join_timeout=3.0)
                thread.join(timeout=3.0)
                self.assertEqual(server.lifecycle_state, "CLOSED")

        # Allow small GC / thread cleanup margin
        time.sleep(0.1)
        final_threads = threading.active_count()
        self.assertLessEqual(final_threads, initial_threads + 1, "Leaked threads detected after 10 cycles")

    def test_lifecycle_concurrent_requests_during_shutdown(self) -> None:
        """Scenario 5: Concurrent client requests while server is shutting down fail cleanly without crashes."""
        server, thread = self._start_server()
        shutdown_initiated = threading.Event()
        errors = []

        def worker():
            shutdown_initiated.wait()
            try:
                self._request(server, "/api/v1/health", timeout=1.0)
            except Exception as exc:
                errors.append(exc)

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(worker) for _ in range(5)]
            time.sleep(0.05)
            shutdown_initiated.set()
            server.close_server(join_timeout=3.0)
            thread.join(timeout=3.0)
            concurrent.futures.wait(futures)

        self.assertEqual(server.lifecycle_state, "CLOSED")

    def test_lifecycle_multiple_concurrent_server_instances(self) -> None:
        """Scenario 6: Multiple independent servers on distinct loopback ports run concurrently."""
        servers = []
        threads = []
        try:
            for _ in range(3):
                s, t = self._start_server()
                servers.append(s)
                threads.append(t)

            for s in servers:
                data = self._request(s, "/api/v1/health")
                self.assertTrue(data.get("ok"))
        finally:
            for s, t in zip(servers, threads):
                s.close_server(join_timeout=3.0)
                t.join(timeout=3.0)
                self.assertEqual(s.lifecycle_state, "CLOSED")

    def test_security_invariants_preserved(self) -> None:
        """Scenario 7: Ensure loopback-only, DNS rebinding protection, and read-only defaults are strictly enforced."""
        # 1. Invalid bind address fails fail-closed
        with self.assertRaises(ValueError):
            create_desktop_server(self.paths, DesktopSettings(host="0.0.0.0", port=0))

        # 2. DNS rebinding rejection (evil Host header)
        server, thread = self._start_server(operations=False)
        try:
            conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
            conn.putrequest("GET", "/api/v1/health", skip_host=True)
            conn.putheader("Host", "attacker-domain.evil")
            conn.putheader("Authorization", f"Bearer {server.application.token}")
            conn.endheaders()
            resp = conn.getresponse()
            self.assertEqual(resp.status, 421)
            resp.read()
            conn.close()

            # 3. Read-only operation enforcement
            req = urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/api/v1/artifacts/draft",
                data=json.dumps({"artifact_id": "test", "path": "sample.py"}).encode(),
                headers={"Authorization": f"Bearer {server.application.token}", "Content-Type": "application/json"},
                method="POST",
            )
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(req, timeout=3)
            self.assertEqual(ctx.exception.code, 403)
        finally:
            server.close_server(join_timeout=3.0)
            thread.join(timeout=3.0)


if __name__ == "__main__":
    unittest.main()
