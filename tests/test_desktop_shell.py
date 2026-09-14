from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from w1cip.cli_support import initialize_workspace
from w1cip.desktop_shell import DesktopSettings, create_desktop_server, detect_desktop_backend


class DesktopShellTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "workspace"
        self.root.mkdir()
        self.paths = initialize_workspace(self.root)
        (self.root / "hello.py").write_bytes(b"print('hello')\n")

    def _server(self, *, operations: bool = False):
        server = create_desktop_server(
            self.paths,
            DesktopSettings(host="127.0.0.1", port=0, allow_operations=operations, open_browser=False),
        )
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        def _cleanup():
            server.close_server(join_timeout=3.0)
            thread.join(timeout=3.0)
        self.addCleanup(_cleanup)
        return server

    def _request(self, server, path: str, *, token: bool = True, method: str = "GET", body=None):
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {server.application.token}"
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}{path}", headers=headers, data=data, method=method,
        )
        return urllib.request.urlopen(request, timeout=8)

    def test_shell_serves_offline_assets_and_protects_api(self) -> None:
        server = self._server()
        with self._request(server, "/", token=False) as response:
            page = response.read().decode()
        self.assertIn("W1 INTELLIGENCE ORCHESTRATOR", page)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self._request(server, "/api/v1/state", token=False)
        self.assertEqual(caught.exception.code, 401)
        with self._request(server, "/api/v1/state") as response:
            state = json.load(response)
        self.assertEqual(state["desktop"]["version"], "0.1.0-dev50")
        self.assertFalse(state["desktop"]["allow_operations"])
        self.assertFalse(state["desktop"].get("w1_owned_server_required", False))

    def test_tree_and_file_api(self) -> None:
        server = self._server()
        with self._request(server, "/api/v1/tree") as response:
            tree = json.load(response)
        self.assertGreaterEqual(tree["entry_count"], 1)
        with self._request(server, "/api/v1/file?path=hello.py") as response:
            file = json.load(response)
        self.assertEqual(file["text"], "print('hello')\n")

    def test_ai_connections_and_team_studio_over_http(self) -> None:
        server = self._server(operations=True)
        with self._request(server, "/api/v1/ai/catalog") as response:
            catalog = json.load(response)
        provider_ids = {item["provider_id"] for item in catalog["providers"]}
        self.assertTrue({"openai", "anthropic", "gemini", "xai"}.issubset(provider_ids))
        with self._request(
            server, "/api/v1/ai/connection/local", method="POST",
            body={"connection_id": "local-main", "endpoint": "http://127.0.0.1:11434/v1/chat/completions"},
        ) as response:
            connection = json.load(response)["result"]
        self.assertEqual("local-main", connection["connection_id"])
        with self._request(
            server, "/api/v1/ai/model", method="POST",
            body={"connection_id": "local-main", "model_id": "local-coder", "model_name": "qwen-test", "roles": ["producer"]},
        ) as response:
            model = json.load(response)["result"]
        self.assertEqual("local-coder", model["model_id"])
        with self._request(
            server, "/api/v1/ai/team", method="POST",
            body={"team": {"team_id": "solo-team", "display_name": "Solo Team", "mode": "solo", "members": [{"model_id": "local-coder", "role": "producer"}]}},
        ) as response:
            team = json.load(response)["result"]
        self.assertEqual("best_fit", team["compiled_portfolio"]["strategy"])
        with self._request(server, "/api/v1/ai/state") as response:
            state = json.load(response)
        self.assertEqual(1, len(state["connections"]))
        self.assertEqual(1, len(state["teams"]))

    def test_adaptive_capacity_api_preserves_challenge_roles(self) -> None:
        server = self._server(operations=True)
        with self._request(
            server, "/api/v1/ai/connection/local", method="POST",
            body={"connection_id": "adaptive-local", "endpoint": "http://127.0.0.1:11434/v1/chat/completions"},
        ):
            pass
        for model_id, role, quality in (
            ("producer-a", "producer", 0.82),
            ("challenger-a", "challenger", 0.86),
            ("synth-a", "synthesizer", 0.90),
        ):
            with self._request(
                server, "/api/v1/ai/model", method="POST",
                body={
                    "connection_id": "adaptive-local", "model_id": model_id, "model_name": model_id,
                    "roles": [role], "domains": ["coding"], "capabilities": {"general": quality, "coding": quality, role: quality},
                    "metadata": {"capacity_source": "local", "terms_status": "official"},
                },
            ):
                pass
            with self._request(
                server, "/api/v1/ai/capacity/observe", method="POST",
                body={"model_id": model_id, "source": "local", "quota_state": "unlimited", "terms_status": "official"},
            ):
                pass
        with self._request(
            server, "/api/v1/ai/capacity/plan", method="POST",
            body={
                "plan_id": "adaptive-http", "display_name": "Adaptive HTTP", "team_mode": "challenge",
                "task": {"task_id": "t", "title": "T", "domains": ["coding"]},
                "policy": {"mode": "local_only", "quality_floor": 0.6, "producer_count": 1, "fallback_per_role": 0},
                "save_portfolio": True,
            },
        ) as response:
            result = json.load(response)["result"]["capacity_plan"]
        self.assertEqual("challenge_synthesis", result["portfolio"]["strategy"])
        self.assertEqual({"producer", "challenger", "synthesizer"}, {m["role"] for m in result["portfolio"]["members"]})
        with self._request(server, "/api/v1/ai/capacity") as response:
            capacity = json.load(response)
        self.assertEqual(3, capacity["count"])
        self.assertTrue(capacity["benchmark"]["passed"])

    def test_operations_are_read_only_by_default(self) -> None:
        server = self._server(operations=False)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self._request(
                server, "/api/v1/artifacts/draft", method="POST",
                body={"artifact_id": "hello-script", "path": "hello.py", "title": "Hello", "created_by": "author"},
            )
        self.assertEqual(caught.exception.code, 403)

    def test_complete_artifact_flow_over_http(self) -> None:
        server = self._server(operations=True)
        with self._request(
            server, "/api/v1/artifacts/draft", method="POST",
            body={"artifact_id": "hello-script", "path": "hello.py", "title": "Hello", "created_by": "author"},
        ) as response:
            draft = json.load(response)["result"]
        with self._request(
            server, "/api/v1/artifacts/version", method="POST",
            body={"artifact_id": "hello-script", "content": "print('changed')\n", "created_by": "author", "expected_parent_hash": draft["content_hash"]},
        ) as response:
            version = json.load(response)["result"]
        with self._request(
            server, "/api/v1/artifacts/review", method="POST",
            body={"artifact_id": "hello-script", "version": version["version"], "reviewer": "reviewer", "outcome": "approved", "rationale": "verified"},
        ) as response:
            self.assertEqual(json.load(response)["result"]["outcome"], "approved")
        with self._request(
            server, "/api/v1/artifacts/publish", method="POST",
            body={"artifact_id": "hello-script", "published_by": "owner", "issue_action_approval": True},
        ) as response:
            publication = json.load(response)["result"]
        self.assertEqual(publication["action_id"], "publish-hello-script-v2")
        self.assertEqual((self.root / "hello.py").read_text(), "print('changed')\n")


    def test_office_artifact_flow_over_http(self) -> None:
        from w1cip.universal_artifacts import reference_artifact_models

        server = self._server(operations=True)
        with self._request(
            server, "/api/v1/office/create", method="POST",
            body={"model": reference_artifact_models()["document"], "created_by": "author"},
        ) as response:
            created = json.load(response)["result"]
        with self._request(
            server, "/api/v1/office/review", method="POST",
            body={
                "artifact_id": created["artifact_id"], "version": created["version"],
                "reviewer": "reviewer", "outcome": "approved", "rationale": "verified",
            },
        ) as response:
            self.assertEqual(json.load(response)["result"]["outcome"], "approved")
        with self._request(
            server, "/api/v1/office/export", method="POST",
            body={
                "artifact_id": created["artifact_id"], "format": "pdf",
                "output_path": "exports/http-document.pdf", "exported_by": "owner",
                "issue_action_approval": True,
            },
        ) as response:
            exported = json.load(response)["result"]
        self.assertEqual(exported["format"], "pdf")
        self.assertTrue((self.root / "exports" / "http-document.pdf").is_file())
        with self._request(server, "/api/v1/state") as response:
            state = json.load(response)
        self.assertEqual(len(state["office_artifacts"]), 1)

    def test_terminal_plan_and_run_are_governed(self) -> None:
        server = self._server(operations=True)
        payload = {"action_id": "desktop-python-version", "argv": ["python", "-c", "print('desktop-ok')"]}
        with self._request(server, "/api/v1/terminal/plan", method="POST", body=payload) as response:
            plan = json.load(response)["result"]
        self.assertTrue(plan["requires_approval"])
        with self._request(
            server, "/api/v1/terminal/run", method="POST", body={**payload, "approve": True},
        ) as response:
            result = json.load(response)["result"]
        self.assertEqual(result["status"], "completed")

    def test_dns_rebinding_and_public_bind_are_rejected(self) -> None:
        server = self._server()
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        connection.putrequest("GET", "/api/v1/health", skip_host=True)
        connection.putheader("Host", "evil.example")
        connection.putheader("Authorization", f"Bearer {server.application.token}")
        connection.endheaders()
        response = connection.getresponse()
        self.assertEqual(response.status, 421)
        response.read()
        connection.close()
        with self.assertRaisesRegex(ValueError, "desktop_shell_loopback_only"):
            create_desktop_server(self.paths, DesktopSettings(host="0.0.0.0", port=0))



    def test_step49_intelligence_search_catalog_and_local_cache_state(self) -> None:
        server = self._server(operations=True)
        with self._request(server, "/api/v1/ai/intelligence-search/catalog") as response:
            catalog = json.load(response)
        self.assertTrue(catalog["local_first"])
        self.assertFalse(catalog["w1_owned_server_required"])
        self.assertFalse(catalog["background_cloud_index"])
        self.assertEqual({"openrouter", "github", "huggingface"}, {x["source_id"] for x in catalog["sources"]})
        with self._request(server, "/api/v1/state") as response:
            state = json.load(response)
        self.assertIn("intelligence_search", state)
        self.assertFalse(state["intelligence_search"]["w1_owned_server_required"])

    def test_step48_conversation_history_and_token_bounded_context_over_http(self) -> None:
        server = self._server(operations=True)
        conversation_id = "http-context-chat"
        for index in range(10):
            with self._request(
                server, "/api/v1/chat/message", method="POST",
                body={
                    "conversation_id": conversation_id,
                    "role": "user" if index % 2 == 0 else "assistant",
                    "content": f"Turn {index} " + ("project context " * 30),
                    "title": "Context efficiency",
                },
            ) as response:
                saved = json.load(response)["result"]
            self.assertFalse(saved["long_term_memory_promoted"])
        with self._request(server, f"/api/v1/chat/messages?conversation_id={conversation_id}") as response:
            messages = json.load(response)
        self.assertEqual(10, len(messages["messages"]))
        with self._request(
            server, f"/api/v1/chat/context?conversation_id={conversation_id}&token_budget=256&query=project"
        ) as response:
            context = json.load(response)
        self.assertLessEqual(context["estimated_tokens"], 256)
        self.assertGreater(context["full_history_tokens"], context["estimated_tokens"])
        self.assertGreater(context["estimated_tokens_saved"], 0)

    def test_step48_free_discovery_catalog_and_safe_default_scan_over_http(self) -> None:
        server = self._server(operations=True)
        with self._request(server, "/api/v1/ai/free-discovery/catalog") as response:
            catalog = json.load(response)
        by_id = {item["target_id"]: item for item in catalog["targets"]}
        self.assertIn("ollama-local", by_id)
        self.assertEqual("unknown", by_id["freellmapi-local"]["terms_status"])
        self.assertEqual("unknown", by_id["9router-local"]["terms_status"])
        with self._request(
            server, "/api/v1/ai/free-discovery/scan", method="POST",
            body={"include_third_party": False, "scan_connected": False, "timeout_seconds": 0.05},
        ) as response:
            scan = json.load(response)["result"]
        findings = {item["target_id"]: item for item in scan["findings"]}
        self.assertEqual("opt_in_required", findings["freellmapi-local"]["status"])
        self.assertEqual("opt_in_required", findings["9router-local"]["status"])
        self.assertFalse(scan["entitlement_inferred"])

    def test_product_ux_has_warm_light_and_dark_modes(self) -> None:
        assets = Path(__file__).parents[1] / "src" / "w1cip" / "desktop_assets"
        page = (assets / "index.html").read_text(encoding="utf-8")
        css = (assets / "styles.css").read_text(encoding="utf-8")
        app = (assets / "app.js").read_text(encoding="utf-8")
        self.assertIn('data-theme="light"', page)
        self.assertIn('data-view-page="home"', page)
        self.assertIn('data-view-page="developer"', page)
        self.assertIn('id="theme-toggle"', page)
        self.assertIn('id="home-prompt"', page)
        self.assertIn('id="home-plan"', page)
        self.assertIn('html[data-theme="dark"]', css)
        self.assertIn('--bg:#f4f0e8', css)
        self.assertIn("localStorage.getItem('w1-theme')", app)
        self.assertIn("/api/v1/ai/capacity/plan", app)
        self.assertIn("showView('developer')", app)

    def test_assets_have_no_remote_dependencies(self) -> None:
        assets = Path(__file__).parents[1] / "src" / "w1cip" / "desktop_assets"
        combined = "\n".join((assets / name).read_text(encoding="utf-8") for name in ("index.html", "styles.css", "app.js"))
        self.assertNotIn("https://", combined)
        self.assertNotIn("http://", combined)
        self.assertIn(detect_desktop_backend(), {"browser-pwa", "native-webview"})


if __name__ == "__main__":
    unittest.main()
