from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path

from w1cip.intelligence_search import (
    GitHubRepositorySearchAdapter,
    HuggingFaceModelSearchAdapter,
    IntelligenceSearchEngine,
    IntelligenceSearchNetworkError,
    IntelligenceSearchStore,
    OpenRouterCatalogAdapter,
    PublicJSONClient,
    SearchCandidate,
    run_intelligence_search_benchmark,
    search_catalog,
)


class FakeResponse:
    def __init__(self, payload):
        self._raw = json.dumps(payload).encode("utf-8")

    def read(self, amount=-1):
        return self._raw if amount < 0 else self._raw[:amount]


class RoutingOpener:
    def __init__(self, routes):
        self.routes = routes
        self.requests = []

    def __call__(self, request, timeout=0):
        self.requests.append((request.full_url, dict(request.header_items()), timeout))
        for prefix, payload in self.routes.items():
            if request.full_url.startswith(prefix):
                return FakeResponse(payload)
        raise AssertionError(f"Unexpected URL: {request.full_url}")


class IntelligenceSearchTests(unittest.TestCase):
    def test_public_client_blocks_non_allowlisted_hosts(self):
        client = PublicJSONClient(opener=lambda *a, **k: FakeResponse({}))
        with self.assertRaises(IntelligenceSearchNetworkError):
            client.get_json("https://example.com/models")

    def test_openrouter_keeps_only_free_models(self):
        opener = RoutingOpener({
            "https://openrouter.ai/api/v1/models": {
                "data": [
                    {"id": "vendor/free:free", "name": "Free", "pricing": {"prompt": "0", "completion": "0"}},
                    {"id": "vendor/paid", "name": "Paid", "pricing": {"prompt": "0.2", "completion": "0.5"}},
                ]
            }
        })
        result = OpenRouterCatalogAdapter().search("", limit=10, client=PublicJSONClient(opener=opener))
        self.assertEqual(["vendor/free:free"], [x.model_id for x in result])
        self.assertEqual("verified_free", result[0].free_status)
        self.assertTrue(result[0].requires_auth)
        self.assertFalse(result[0].metadata["entitlement_inferred"])

    def test_github_results_remain_manual_review(self):
        opener = RoutingOpener({
            "https://api.github.com/search/repositories": {
                "items": [{
                    "full_name": "demo/free-llm-gateway", "description": "Free OpenAI compatible LLM API gateway",
                    "html_url": "https://github.com/demo/free-llm-gateway", "stargazers_count": 120,
                    "forks_count": 12, "updated_at": "2026-08-11T00:00:00Z", "archived": False,
                    "license": {"spdx_id": "MIT"},
                }]
            }
        })
        result = GitHubRepositorySearchAdapter().search("free llm", limit=5, client=PublicJSONClient(opener=opener))
        self.assertEqual(1, len(result))
        self.assertEqual("manual_review", result[0].review_status)
        self.assertEqual("unknown", result[0].terms_status)
        self.assertFalse(result[0].metadata["trust_inferred_from_stars"])

    def test_huggingface_is_local_candidate_not_free_hosted_entitlement(self):
        opener = RoutingOpener({
            "https://huggingface.co/api/models": [{
                "id": "demo/model", "downloads": 1000, "likes": 9, "pipeline_tag": "text-generation",
                "tags": ["license:apache-2.0", "text-generation"],
            }]
        })
        result = HuggingFaceModelSearchAdapter().search("demo", limit=5, client=PublicJSONClient(opener=opener))
        self.assertEqual("local_candidate", result[0].review_status)
        self.assertEqual("open_weights_candidate", result[0].free_status)
        self.assertFalse(result[0].metadata["hosted_inference_free_inferred"])

    def test_engine_caches_ranked_results_locally(self):
        class Adapter:
            source_id = "openrouter"
            def search(self, query, *, limit, client):
                return [SearchCandidate(
                    candidate_id="x", source_id="openrouter", source_kind="hosted_model", title="X",
                    url="https://openrouter.ai/x", free_status="verified_free", terms_status="verified_third_party",
                    review_status="connectable", score=.9,
                )]
        with tempfile.TemporaryDirectory() as td:
            store = IntelligenceSearchStore(Path(td) / "search.sqlite3")
            report = IntelligenceSearchEngine(store, adapters=(Adapter(),)).search("x")
            self.assertEqual(1, len(report.candidates))
            self.assertEqual("x", store.list()[0].candidate_id)

    def test_catalog_is_serverless_local_first(self):
        catalog = search_catalog()
        self.assertTrue(catalog["local_first"])
        self.assertFalse(catalog["w1_owned_server_required"])
        self.assertFalse(catalog["background_cloud_index"])
        self.assertFalse(catalog["security"]["credentials_cached"])
        self.assertFalse(catalog["security"]["third_party_auto_routing"])

    def test_benchmark(self):
        result = run_intelligence_search_benchmark()
        self.assertTrue(result["passed"], result)


if __name__ == "__main__":
    unittest.main()
