from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from w1cip.ai_connections import AIConnectionRecord, AIConnectionStore, provider_catalog_entry
from w1cip.capacity_router import CapacityStore
from w1cip.intelligence_activation import (
    IntelligenceActivationService,
    IntelligenceActivationStore,
    run_intelligence_activation_benchmark,
)
from w1cip.intelligence_search import IntelligenceSearchStore, SearchCandidate
from w1cip.model_access import ModelAccessStore


class IntelligenceActivationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.search = IntelligenceSearchStore(root / "search.sqlite3")
        self.activation = IntelligenceActivationStore(root / "search.sqlite3")
        self.connections = AIConnectionStore(root / "connections.sqlite3")
        self.models = ModelAccessStore(root / "models.sqlite3")
        self.capacity = CapacityStore(root / "capacity.sqlite3")
        self.service = IntelligenceActivationService(
            search_store=self.search,
            activation_store=self.activation,
            connection_store=self.connections,
            model_store=self.models,
            capacity_store=self.capacity,
        )
        self.free = SearchCandidate(
            candidate_id="or-free",
            source_id="openrouter",
            source_kind="hosted_model",
            title="Free Model",
            url="https://openrouter.ai/demo/free:free",
            provider="OpenRouter",
            model_id="demo/free:free",
            free_status="verified_free",
            terms_status="verified_third_party",
            review_status="connectable",
            requires_auth=True,
            score=.96,
        )
        self.search.put_many((self.free,))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def add_connection(self, discovered=()):
        self.connections.put_connection(AIConnectionRecord(
            connection_id="or-main",
            provider_id="openrouter",
            display_name="OpenRouter",
            auth_mode="api_key",
            credential_reference="w1-credential:redacted-reference",
            endpoint="https://openrouter.ai/api/v1/chat/completions",
            status="connected",
            discovered_models=tuple(discovered),
        ))

    def test_openrouter_exists_in_provider_catalog(self):
        provider = provider_catalog_entry("openrouter")
        self.assertEqual("openai_compatible", provider.connector_type)
        self.assertEqual("https://openrouter.ai/api/v1/models", provider.model_list_endpoint)

    def test_candidate_get_roundtrip(self):
        loaded = self.search.get(self.free.candidate_id)
        self.assertIsNotNone(loaded)
        self.assertEqual(self.free.model_id, loaded.model_id)

    def test_connection_and_model_discovery_are_required(self):
        first = self.service.assess(self.free.candidate_id)
        self.assertEqual("connection_required", first.state)
        self.add_connection()
        second = self.service.assess(self.free.candidate_id)
        self.assertEqual("model_discovery_required", second.state)
        self.assertFalse(second.activation_allowed)

    def test_provider_discovery_match_unlocks_activation(self):
        self.add_connection(("demo/free:free",))
        result = self.service.assess(self.free.candidate_id)
        self.assertTrue(result.activation_allowed)
        self.assertEqual("activation_ready", result.state)
        self.assertEqual("or-main", result.connection_id)
        self.assertFalse(result.evidence["live_network_call_performed"])

    def test_github_and_huggingface_never_directly_activate(self):
        github = SearchCandidate(
            candidate_id="gh-x", source_id="github", source_kind="repository", title="Repo",
            url="https://github.com/demo/repo", free_status="free_marker", terms_status="unknown",
            review_status="manual_review", score=.7,
        )
        hf = SearchCandidate(
            candidate_id="hf-x", source_id="huggingface", source_kind="open_model", title="Weights",
            url="https://huggingface.co/demo/model", model_id="demo/model",
            free_status="open_weights_candidate", terms_status="verified_third_party",
            review_status="local_candidate", score=.72,
        )
        self.search.put_many((github, hf))
        self.assertEqual("manual_review_required", self.service.assess("gh-x").state)
        self.assertEqual("local_runtime_required", self.service.assess("hf-x").state)

    def test_activation_is_conservative_and_audited(self):
        self.add_connection(("demo/free:free",))
        record = self.service.activate(self.free.candidate_id)
        profile = self.models.get_profile(record.profile_id)
        observation = self.capacity.get(record.profile_id)
        self.assertEqual("demo/free:free", profile.model_name)
        self.assertTrue(profile.metadata["quality_unbenchmarked"])
        self.assertEqual("third_party_free", observation.source)
        self.assertEqual("unknown", observation.quota_state)
        self.assertFalse(observation.entitlement_verified)
        self.assertTrue(observation.metadata["default_router_opt_in_required"])
        self.assertTrue(self.activation.verify_audit())

    def test_manual_review_does_not_create_entitlement(self):
        record = self.service.review(self.free.candidate_id, decision="approved", note="Reviewed catalog metadata.")
        self.assertEqual("reviewed", record.status)
        self.assertTrue(record.evidence["review_does_not_create_entitlement"])
        self.assertEqual([], self.models.list_profiles())

    def test_benchmark(self):
        result = run_intelligence_activation_benchmark()
        self.assertTrue(result["passed"], result)
        self.assertEqual(0, result["live_network_calls"])


if __name__ == "__main__":
    unittest.main()
