from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

from w1cip.ai_connections import AIConnectionRecord, AIConnectionStore
from w1cip.cli_support import initialize_workspace
from w1cip.desktop_shell import DesktopSettings, create_desktop_server
from w1cip.intelligence_search import IntelligenceSearchStore, SearchCandidate


class IntelligenceActivationDesktopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "workspace"
        self.root.mkdir()
        self.paths = initialize_workspace(self.root)
        IntelligenceSearchStore(self.paths.intelligence_search_db).put_many((SearchCandidate(
            candidate_id="or-desktop", source_id="openrouter", source_kind="hosted_model", title="Desktop Free",
            url="https://openrouter.ai/demo/desktop:free", provider="OpenRouter", model_id="demo/desktop:free",
            free_status="verified_free", terms_status="verified_third_party", review_status="connectable",
            requires_auth=True, score=.96,
        ),))
        AIConnectionStore(self.paths.ai_connections_db).put_connection(AIConnectionRecord(
            connection_id="or-desktop-main", provider_id="openrouter", display_name="OpenRouter Desktop",
            auth_mode="api_key", credential_reference="w1-credential:desktop-redacted",
            endpoint="https://openrouter.ai/api/v1/chat/completions", status="connected",
            discovered_models=("demo/desktop:free",),
        ))

    def _server(self):
        server = create_desktop_server(
            self.paths,
            DesktopSettings(host="127.0.0.1", port=0, allow_operations=True, open_browser=False),
        )
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        def _cleanup():
            server.close_server(join_timeout=3.0)
            thread.join(timeout=3.0)
        self.addCleanup(_cleanup)
        return server

    def _request(self, server, path: str, *, method="GET", body=None):
        headers = {"Authorization": f"Bearer {server.application.token}"}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}{path}", headers=headers, data=data, method=method,
        )
        return urllib.request.urlopen(request, timeout=8)

    def test_assess_activate_and_state_over_http(self):
        server = self._server()
        with self._request(
            server, "/api/v1/ai/intelligence-search/assess", method="POST",
            body={"candidate_id": "or-desktop"},
        ) as response:
            assessment = json.load(response)["result"]
        self.assertTrue(assessment["activation_allowed"])
        self.assertEqual("activation_ready", assessment["state"])

        with self._request(
            server, "/api/v1/ai/intelligence-search/activate", method="POST",
            body={"candidate_id": "or-desktop", "connection_id": assessment["connection_id"]},
        ) as response:
            activated = json.load(response)["result"]
        self.assertEqual("activated", activated["record"]["status"])
        self.assertTrue(activated["third_party_free_router_opt_in_required"])

        with self._request(server, "/api/v1/state") as response:
            state = json.load(response)
        self.assertEqual(1, state["intelligence_search"]["activation_count"])
        self.assertTrue(state["intelligence_search"]["activation_audit_chain_valid"])
        self.assertEqual("third_party_free", state["adaptive_capacity"]["observations"][0]["source"])


if __name__ == "__main__":
    unittest.main()
