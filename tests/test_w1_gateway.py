from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from w1cip.w1_gateway import (
    ENTITLEMENT_STATES,
    KNOWN_MODEL_IDENTITIES,
    MODEL_ARCHITECTURES,
    QUALITY_TIERS,
    ROUTE_HEALTH_STATES,
    ROUTE_SOURCE_TYPES,
    W1_PROVIDER_REGISTRY,
    DiversityScore,
    FreeTeamBuilder,
    FreeTeamSpec,
    GatewayConfigurationError,
    GatewayStore,
    GatewayTeamBuildError,
    ModelIdentity,
    ModelRoute,
    RouteCost,
    RouteExplanation,
    RouteQuota,
    SelectedRoute,
    TeamBuilder,
    TeamPolicy,
    TeamSpec,
    W1RouteSelector,
    build_identities_from_registry,
    build_routes_from_registry,
    gateway_statistics,
    ingest_discovered_models,
    populate_gateway_store,
    run_gateway_benchmark,
    verify_route_live,
)


class ModelIdentityTests(unittest.TestCase):
    def test_model_identity_valid(self) -> None:
        identity = ModelIdentity(
            canonical_id="test-model",
            family="test-family",
            vendor="test-vendor",
            quality_tier="frontier",
            context_window=100_000,
            capabilities=frozenset({"coding", "reasoning"}),
            architecture="mixture_of_experts",
        )
        self.assertEqual(identity.canonical_id, "test-model")
        self.assertEqual(identity.quality_score, 0.95)
        self.assertEqual(identity.architecture, "mixture_of_experts")
        self.assertTrue(identity.matches_requirements(required_capabilities=frozenset({"coding"})))
        self.assertFalse(identity.matches_requirements(required_capabilities=frozenset({"vision"})))
        self.assertTrue(identity.matches_requirements(min_context=50_000))
        self.assertFalse(identity.matches_requirements(min_context=200_000))
        self.assertTrue(identity.matches_requirements(preferred_architecture="mixture_of_experts"))
        self.assertFalse(identity.matches_requirements(preferred_architecture="dense"))

    def test_model_identity_invalid_cases(self) -> None:
        with self.assertRaises(GatewayConfigurationError):
            ModelIdentity(
                canonical_id="",
                family="f",
                vendor="v",
                quality_tier="strong",
                context_window=1000,
                capabilities=frozenset(),
            )

        with self.assertRaises(GatewayConfigurationError):
            ModelIdentity(
                canonical_id="m",
                family="f",
                vendor="v",
                quality_tier="invalid_tier",
                context_window=1000,
                capabilities=frozenset(),
            )

        with self.assertRaises(GatewayConfigurationError):
            ModelIdentity(
                canonical_id="m",
                family="f",
                vendor="v",
                quality_tier="strong",
                context_window=-10,
                capabilities=frozenset(),
            )

        with self.assertRaises(GatewayConfigurationError):
            ModelIdentity(
                canonical_id="m",
                family="f",
                vendor="v",
                quality_tier="strong",
                context_window=1000,
                capabilities=frozenset(),
                architecture="non_existent_arch",
            )


class ModelRouteTests(unittest.TestCase):
    def test_route_lifecycle_and_health(self) -> None:
        cost = RouteCost(input_per_million=1.0, output_per_million=2.0)
        quota = RouteQuota(rpm=30, tpm=50000, state="available")
        route = ModelRoute(
            route_id="test@provider:acc1",
            model_identity_id="test-model",
            provider_id="provider",
            account_id="acc1",
            model_id_at_provider="test-p-model",
            source_type="paid",
            cost=cost,
            quota=quota,
            auth_mode="api_key",
            api_key_env="TEST_KEY",
            endpoint="https://api.test.com/v1",
        )
        self.assertFalse(route.is_free)
        self.assertTrue(route.is_available)
        self.assertEqual(route.health, "unknown")

        route.record_success()
        self.assertEqual(route.health, "healthy")
        self.assertEqual(route.consecutive_failures, 0)
        self.assertIsNotNone(route.last_success_at)

        route.record_failure()
        self.assertEqual(route.health, "degraded")
        self.assertEqual(route.consecutive_failures, 1)

        route.record_failure()
        route.record_failure()
        self.assertEqual(route.health, "down")
        self.assertFalse(route.is_available)

    def test_free_route_detection(self) -> None:
        cost_free = RouteCost(0.0, 0.0)
        quota = RouteQuota(state="available")
        route = ModelRoute(
            route_id="free@prov",
            model_identity_id="free-model",
            provider_id="prov",
            model_id_at_provider="free-m",
            source_type="official_free_tier",
            cost=cost_free,
            quota=quota,
            auth_mode="api_key",
            api_key_env=None,
            endpoint="https://api.free.com",
        )
        self.assertTrue(route.is_free)
        self.assertEqual(cost_free.estimated_task_cost(input_tokens=1000, output_tokens=500), 0.0)


class GatewayStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "test_gateway.sqlite3"
        self.store = GatewayStore(self.db_path)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_put_get_list_identities_and_routes(self) -> None:
        identity = ModelIdentity(
            canonical_id="canon-1",
            family="fam-1",
            vendor="vend-1",
            quality_tier="strong",
            context_window=128000,
            capabilities=frozenset({"coding"}),
        )
        self.store.put_identity(identity)
        self.assertEqual(self.store.identity_count(), 1)
        fetched_id = self.store.get_identity("canon-1")
        self.assertIsNotNone(fetched_id)
        self.assertEqual(fetched_id["vendor"], "vend-1")

        route = ModelRoute(
            route_id="r1@p1:default",
            model_identity_id="canon-1",
            provider_id="p1",
            account_id="default",
            model_id_at_provider="m1",
            source_type="official_free",
            cost=RouteCost(0.0, 0.0),
            quota=RouteQuota(rpm=10, state="available"),
            auth_mode="api_key",
            api_key_env=None,
            endpoint="https://p1.com",
        )
        self.store.put_route(route)
        self.assertEqual(self.store.route_count(), 1)
        fetched_r = self.store.get_route("r1@p1:default")
        self.assertIsNotNone(fetched_r)
        self.assertEqual(fetched_r["provider_id"], "p1")

        routes = self.store.list_routes(provider_id="p1", free_only=True)
        self.assertEqual(len(routes), 1)

        self.store.log_health_event("r1@p1:default", "ping_success", {"latency_ms": 120})

        deleted = self.store.delete_route("r1@p1:default")
        self.assertTrue(deleted)
        self.assertEqual(self.store.route_count(), 0)

    def test_dynamic_ingestion(self) -> None:
        res = ingest_discovered_models(
            self.store,
            provider_id="dynamic_prov",
            account_id="acc_team",
            models=[
                {
                    "id": "dyn/model-a:free",
                    "canonical_id": "model-a",
                    "family": "model-a-fam",
                    "vendor": "open_corp",
                    "quality_tier": "strong",
                    "context_window": 64000,
                    "capabilities": ["coding", "reasoning"],
                    "free_tier": True,
                },
                {
                    "id": "dyn/model-b",
                    "canonical_id": "model-b",
                    "quality_tier": "frontier",
                    "context_window": 128000,
                    "capabilities": ["coding"],
                    "input_cost_per_million": 2.0,
                    "output_cost_per_million": 8.0,
                },
            ],
        )
        self.assertEqual(res["identities_ingested"], 2)
        self.assertEqual(res["routes_ingested"], 2)
        self.assertEqual(self.store.identity_count(), 2)
        self.assertEqual(self.store.route_count(), 2)

        free_routes = self.store.list_routes(free_only=True)
        self.assertEqual(len(free_routes), 1)
        self.assertEqual(free_routes[0]["route_id"], "model-a@dynamic_prov:acc_team")


class RouteSelectorAndExplainabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.selector = W1RouteSelector.from_registry()

    def test_find_routes_filters(self) -> None:
        routes = self.selector.find_routes(
            required_capabilities=frozenset({"coding"}),
            min_quality=0.70,
            free_only=True,
        )
        self.assertGreater(len(routes), 0)
        for r in routes:
            self.assertTrue(r.route.is_free)
            self.assertIn("coding", r.identity.capabilities)

    def test_select_best_with_rejected_tracking(self) -> None:
        res = self.selector.select_best(
            required_capabilities=frozenset({"coding"}),
            min_quality=0.70,
            free_only=True,
        )
        self.assertIsNotNone(res.selected)
        self.assertGreater(len(res.fallbacks), 0)
        self.assertGreater(len(res.rejected), 0)

    def test_explain_route(self) -> None:
        explanation = self.selector.explain_route(
            task_type="coding",
            min_quality=0.70,
            free_only=True,
        )
        self.assertIsNotNone(explanation.selected_route_id)
        self.assertGreater(len(explanation.positive_factors), 0)
        self.assertGreater(len(explanation.rejected_candidates), 0)
        self.assertGreater(len(explanation.fallback_hierarchy), 0)

        human_text = explanation.render_human()
        self.assertIn("Selected Route:", human_text)
        self.assertIn("Rejected Alternatives:", human_text)
        self.assertIn("Fallback Hierarchy:", human_text)


class TeamBuilderAndMultiDiversityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.builder = TeamBuilder.from_registry()

    def test_build_team_enforces_diversity(self) -> None:
        policy = TeamPolicy(
            budget=0.0,
            enforce_vendor_diversity=True,
            enforce_family_diversity=True,
            team_mode="challenge",
            require_verifier=True,
        )
        team = self.builder.build_team("coding", policy=policy)
        self.assertIsNotNone(team.producer)
        self.assertIsNotNone(team.reviewer)
        self.assertIsNotNone(team.verifier)

        # Ensure producer and reviewer are different routes and different providers/vendors
        self.assertNotEqual(team.producer.route.route_id, team.reviewer.route.route_id)
        self.assertNotEqual(team.producer.route.provider_id, team.reviewer.route.provider_id)
        self.assertGreater(team.diversity.score, 0.0)

        # Portfolio integration
        portfolio = team.to_portfolio()
        self.assertEqual(len(portfolio.members), 3)
        self.assertEqual(portfolio.members[0].role, "producer")
        self.assertEqual(portfolio.members[1].role, "synthesizer")
        self.assertEqual(portfolio.members[2].role, "challenger")
        self.assertEqual(portfolio.strategy, "challenge_synthesis")

    def test_backward_compatibility_alias(self) -> None:
        free_builder = FreeTeamBuilder.from_registry()
        team = free_builder.build_team("general")
        self.assertIsInstance(team, FreeTeamSpec)


class LayeredBenchmarkTests(unittest.TestCase):
    def test_offline_contract_benchmark_all_probes_pass(self) -> None:
        res = run_gateway_benchmark()
        self.assertTrue(res["passed"])
        self.assertEqual(res["probes_failed"], 0)
        self.assertGreaterEqual(res["probes_passed"], 10)
        self.assertTrue(res["probes"]["multi_route_for_same_model"])
        self.assertTrue(res["probes"]["team_provider_diversity"])
        self.assertTrue(res["probes"]["explanation_generated"])
        self.assertTrue(res["probes"]["dynamic_ingestion_works"])

    def test_verify_route_live_structure(self) -> None:
        routes = build_routes_from_registry()
        route = routes[0]
        probe_res = verify_route_live(route)
        self.assertEqual(probe_res["route_id"], route.route_id)
        self.assertEqual(probe_res["status"], "VERIFIED_OFFLINE_PROBE")
        self.assertIn("evidence_digest", probe_res)

    def test_gateway_statistics(self) -> None:
        stats = gateway_statistics()
        self.assertGreaterEqual(stats["total_providers"], 10)
        self.assertGreaterEqual(stats["total_model_identities"], 10)
        self.assertGreaterEqual(stats["free_routes"], 10)
        self.assertIn("identities_by_architecture", stats)


if __name__ == "__main__":
    unittest.main()
