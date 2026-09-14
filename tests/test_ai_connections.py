from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from w1cip.ai_connections import (
    AIConnectionService,
    AIConnectionStore,
    AITeamDefinition,
    AITeamMember,
    compile_team_portfolio,
    provider_catalog,
    run_ai_connections_benchmark,
)
from w1cip.credential_broker import AccountRecord, CredentialBroker, CredentialBrokerStore, MemoryCredentialVault
from w1cip.model_access import ModelAccessFabric, ModelAccessStore, ModelProfile
from w1cip.orchestrator import CompiledTask, ProviderRequest, ProviderResponse


class Adapter:
    supports_idempotency = True

    def __init__(self, resource_id: str, marker: str) -> None:
        self.resource_id = resource_id
        self.marker = marker
        self.calls = []

    def invoke(self, request: ProviderRequest) -> ProviderResponse:
        self.calls.append(request)
        return ProviderResponse(
            output_type=request.task.expected_output_type,
            payload={"marker": self.marker, "prior": sorted(request.prior_outputs)},
        )


def request() -> ProviderRequest:
    return ProviderRequest(
        run_id="run-ai-team",
        session_id="session-ai-team",
        task=CompiledTask(
            task_id="task-ai-team",
            title="Build a governed result",
            phase="execution",
            role="executor",
            expected_output_type="contribution",
            domains=("general",),
        ),
        resource_id="portfolio-team",
        idempotency_key="ai-team-test",
        context={},
        prior_outputs={},
        attempt=1,
    )


class AIConnectionsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.connection_store = AIConnectionStore(self.root / "ai.sqlite3")
        self.model_store = ModelAccessStore(self.root / "models.sqlite3")
        self.credential_store = CredentialBrokerStore(self.root / "credentials.sqlite3")
        self.vault = MemoryCredentialVault()
        self.broker = CredentialBroker(self.credential_store, self.vault)
        self.service = AIConnectionService(
            self.connection_store,
            self.model_store,
            credential_broker=self.broker,
            credential_store=self.credential_store,
        )

    def tearDown(self) -> None:
        self.credential_store.close()
        self.temp.cleanup()

    def test_catalog_exposes_major_connections_without_subscription_assumption(self) -> None:
        entries = {item["provider_id"]: item for item in provider_catalog()}
        self.assertTrue({"openai", "anthropic", "gemini", "xai"}.issubset(entries))
        self.assertIn("subscription", entries["openai"]["notes"].lower())
        self.assertEqual(("api_key", "oauth"), tuple(entries["gemini"]["supported_auth_modes"]))

    def test_api_key_connection_persists_reference_not_secret(self) -> None:
        secret = "never-write-this-secret-to-connections-db"
        record = self.service.add_api_key_connection(
            connection_id="claude-main",
            provider_id="anthropic",
            secret_value=secret,
        )
        self.assertEqual("w1-credential:ai-claude-main", record.credential_reference)
        self.assertEqual(secret, self.vault.get("credential:ai-claude-main"))
        self.assertNotIn(secret.encode(), (self.root / "ai.sqlite3").read_bytes())

    def test_local_connection_is_loopback_only(self) -> None:
        record = self.service.add_local_connection(
            connection_id="local", endpoint="http://127.0.0.1:11434/v1/chat/completions"
        )
        self.assertEqual("none", record.auth_mode)
        with self.assertRaises(Exception):
            self.service.add_local_connection(
                connection_id="bad", endpoint="http://192.168.1.20:11434/v1/chat/completions"
            )

    def test_builtin_provider_endpoint_cannot_exfiltrate_credentials(self) -> None:
        with self.assertRaisesRegex(Exception, "built_in_provider_endpoint_host_locked"):
            self.service.add_api_key_connection(
                connection_id="bad-openai", provider_id="openai", secret_value="secret",
                endpoint="https://evil.example/v1/responses",
            )
        with self.assertRaises(Exception):
            self.vault.get("credential:ai-bad-openai")
        custom = self.service.add_api_key_connection(
            connection_id="custom", provider_id="custom-openai-compatible", secret_value="secret",
            endpoint="https://models.example.test/v1/chat/completions",
        )
        self.assertEqual("https://models.example.test/v1/chat/completions", custom.endpoint)
        with self.assertRaisesRegex(Exception, "custom_provider_requires_https_or_loopback_http"):
            self.service.add_api_key_connection(
                connection_id="unsafe-custom", provider_id="custom-openai-compatible", secret_value="secret",
                endpoint="http://192.168.1.9:8080/v1/chat/completions",
            )
        public_noauth = self.service.add_custom_connection(
            connection_id="public-custom", endpoint="https://public.example.test/v1/chat/completions"
        )
        self.assertEqual("none", public_noauth.auth_mode)

    def test_model_profiles_are_bound_to_connection_reference(self) -> None:
        connection = self.service.add_api_key_connection(
            connection_id="openai-main", provider_id="openai", secret_value="api-secret"
        )
        profile = self.service.add_model(
            connection_id=connection.connection_id,
            model_id="coder",
            model_name="fixed-model",
            roles=("producer",),
        )
        self.assertEqual(connection.credential_reference, profile.credential_reference)
        self.assertEqual("openai", profile.provider_id)
        self.assertEqual("openai-main", profile.metadata["ai_connection_id"])

    def test_gemini_oauth_account_selects_bearer_connector(self) -> None:
        self.credential_store.put_account(AccountRecord(
            account_id="google-main", provider_id="gemini", client_id="client",
            credential_key="account:google-main", scopes=("scope",), display_name="Google main"
        ))
        connection = self.service.attach_oauth_account(
            connection_id="gemini-main", provider_id="gemini", account_id="google-main"
        )
        profile = self.service.add_model(
            connection_id=connection.connection_id, model_id="gemini-coder", model_name="gemini-test"
        )
        self.assertEqual("oauth_broker", profile.access_mode)
        self.assertEqual("gemini_oauth_generate_content", profile.connector_type)
        self.assertEqual("w1-account:google-main", profile.credential_reference)

    def test_team_modes_compile_to_existing_governed_runtime(self) -> None:
        solo = AITeamDefinition(
            team_id="solo", display_name="Solo", mode="solo", members=(AITeamMember("a"),)
        )
        self.assertEqual("best_fit", compile_team_portfolio(solo).strategy)
        parallel = AITeamDefinition(
            team_id="parallel", display_name="Parallel", mode="parallel", members=(AITeamMember("a"), AITeamMember("b"))
        )
        self.assertEqual("parallel_collect", compile_team_portfolio(parallel).strategy)
        verify = AITeamDefinition(
            team_id="verify", display_name="Verify", mode="verify",
            members=(AITeamMember("a", "producer"), AITeamMember("b", "verifier")),
        )
        self.assertEqual("verified_synthesis", compile_team_portfolio(verify).strategy)

    def test_challenge_strategy_passes_outputs_model_to_model(self) -> None:
        profiles = (
            ModelProfile("producer", "Producer", "p1", "openai_compatible", "r1", "m1", "local_endpoint", "local", {"general": 0.8}, endpoint="http://127.0.0.1:1/v1/chat/completions"),
            ModelProfile("challenger", "Challenger", "p2", "openai_compatible", "r2", "m2", "local_endpoint", "local", {"general": 0.8}, endpoint="http://127.0.0.1:1/v1/chat/completions"),
            ModelProfile("synth", "Synth", "p3", "openai_compatible", "r3", "m3", "local_endpoint", "local", {"general": 0.8}, endpoint="http://127.0.0.1:1/v1/chat/completions"),
        )
        for profile in profiles:
            self.model_store.put_profile(profile)
        team = AITeamDefinition(
            team_id="challenge-team",
            display_name="Challenge Team",
            mode="challenge",
            members=(
                AITeamMember("producer", "producer"),
                AITeamMember("challenger", "challenger"),
                AITeamMember("synth", "synthesizer"),
            ),
        )
        self.service.save_team(team)
        producer = Adapter("r1", "producer")
        challenger = Adapter("r2", "challenger")
        synth = Adapter("r3", "synth")
        fabric = ModelAccessFabric(
            self.model_store,
            adapter_overrides={"producer": producer, "challenger": challenger, "synth": synth},
        )
        result = fabric.invoke_portfolio("challenge-team", request())
        self.assertEqual("synth", result.selected_model_id)
        self.assertEqual(["producer"], list(challenger.calls[0].prior_outputs.keys()))
        self.assertIn("_w1_challenge", synth.calls[0].prior_outputs)
        self.assertIn("producer", synth.calls[0].prior_outputs)
        self.assertIn("challenge_is_review_not_formal_verification", result.warnings)

    def test_stable_sdk_exports_team_contract(self) -> None:
        from w1cip.sdk import AITeamDefinition as SDKAITeamDefinition, AITeamMember as SDKAITeamMember, SDK_VERSION, provider_catalog as sdk_provider_catalog
        self.assertEqual("1.2.0", SDK_VERSION)
        self.assertIs(SDKAITeamDefinition, AITeamDefinition)
        self.assertIs(SDKAITeamMember, AITeamMember)
        self.assertGreaterEqual(len(sdk_provider_catalog()), 6)

    def test_executable_benchmark(self) -> None:
        benchmark = run_ai_connections_benchmark()
        self.assertTrue(benchmark["passed"], benchmark)
        self.assertEqual(0, benchmark["metrics"]["live_provider_calls"])


if __name__ == "__main__":
    unittest.main()
