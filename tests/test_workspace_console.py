from __future__ import annotations

import http.client
import json
import os
import stat
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from w1cip.cli_support import initialize_workspace
from w1cip.reference_demo import run_reference_demo
from w1cip.workspace_console import (
    ConsoleSettings,
    WorkspaceConsoleApplication,
    WorkspaceSnapshotService,
    create_console_server,
)


class WorkspaceConsoleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "workspace"
        self.root.mkdir()
        self.paths = initialize_workspace(self.root)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _server(self, *, allow_operations: bool = False):
        server = create_console_server(
            self.paths,
            ConsoleSettings(host="127.0.0.1", port=0, allow_operations=allow_operations, poll_seconds=0.25),
        )
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 2)
        return server

    def _request(self, server, path: str, *, token: bool = True, method: str = "GET", body=None):
        url = f"http://127.0.0.1:{server.server_port}{path}"
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {server.application.token}"
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, headers=headers, data=data, method=method)
        return urllib.request.urlopen(request, timeout=8)

    def test_snapshot_has_all_workspace_surfaces(self) -> None:
        snapshot = WorkspaceSnapshotService(self.paths).snapshot()
        for key in (
            "workspace", "metrics", "health", "sessions", "runs", "agents", "providers",
            "approvals", "memory", "science", "sandboxes", "merges", "tools", "git", "audit",
        ):
            self.assertIn(key, snapshot)
        self.assertEqual(snapshot["workspace"]["name"], "workspace")
        self.assertEqual(len(snapshot["snapshot_digest"]), 64)

    def test_token_file_is_owner_only_on_posix(self) -> None:
        app = WorkspaceConsoleApplication(self.paths, ConsoleSettings(port=0))
        mode = stat.S_IMODE(app.token_path.stat().st_mode)
        if os.name != "nt":
            self.assertEqual(mode, 0o600)
        self.assertGreaterEqual(len(app.token), 32)

    def test_server_serves_dashboard_and_protects_api(self) -> None:
        server = self._server()
        with self._request(server, "/", token=False) as response:
            html = response.read().decode()
        self.assertIn("W1 NEXUS", html)
        self.assertIn("Operations Workspace", html)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self._request(server, "/api/snapshot", token=False)
        self.assertEqual(caught.exception.code, 401)
        with self._request(server, "/api/snapshot") as response:
            snapshot = json.load(response)
        self.assertEqual(snapshot["console"]["version"], "0.1.0-dev50")
        self.assertTrue(snapshot["console"]["read_only"])

    def test_host_header_rejects_dns_rebinding_shape(self) -> None:
        server = self._server()
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        connection.putrequest("GET", "/api/health", skip_host=True)
        connection.putheader("Host", "evil.example")
        connection.putheader("Authorization", f"Bearer {server.application.token}")
        connection.endheaders()
        response = connection.getresponse()
        self.assertEqual(response.status, 421)
        response.read()
        connection.close()

    def test_sse_emits_snapshot(self) -> None:
        server = self._server()
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        connection.request("GET", f"/api/stream?token={server.application.token}")
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        first = response.readline().decode().strip()
        second = response.readline().decode().strip()
        self.assertEqual(first, "event: snapshot")
        self.assertTrue(second.startswith("data: "))
        payload = json.loads(second[6:])
        self.assertIn("snapshot_digest", payload)
        connection.close()

    def test_read_only_integrity_operation_is_available(self) -> None:
        server = self._server()
        with self._request(
            server,
            "/api/operation",
            method="POST",
            body={"operation": "verify_all_sessions"},
        ) as response:
            payload = json.load(response)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["result"]["valid"])

    def test_state_changing_operation_requires_opt_in(self) -> None:
        server = self._server(allow_operations=False)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self._request(
                server,
                "/api/operation",
                method="POST",
                body={"operation": "run_reference_demo", "reset": True},
            )
        self.assertEqual(caught.exception.code, 403)

    def test_reference_demo_populates_live_snapshot(self) -> None:
        result, audit = run_reference_demo(self.root, reset=True)
        self.assertEqual(result.status, "completed")
        self.assertTrue(audit["session_integrity"])
        snapshot = WorkspaceSnapshotService(self.paths).snapshot()
        self.assertGreaterEqual(len(snapshot["sessions"]), 1)
        self.assertGreaterEqual(len(snapshot["runs"]), 1)
        self.assertIsNotNone(snapshot["final_result"])
        self.assertGreaterEqual(len(snapshot["audit"]), 1)

    def test_static_assets_have_no_remote_dependencies(self) -> None:
        package_assets = Path(__file__).parents[1] / "src" / "w1cip" / "workspace_assets"
        selected = [package_assets / "index.html", package_assets / "styles.css", package_assets / "app.js"]
        combined = "\n".join(path.read_text(encoding="utf-8") for path in selected)
        self.assertNotIn("https://", combined)
        self.assertNotIn("http://", combined)
        self.assertNotIn("cdn.", combined.lower())

    def test_non_loopback_binding_is_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "workspace_console_loopback_only"):
            create_console_server(self.paths, ConsoleSettings(host="0.0.0.0", port=0))


if __name__ == "__main__":
    unittest.main()
