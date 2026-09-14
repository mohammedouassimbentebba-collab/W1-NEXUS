from __future__ import annotations

import json
import tempfile
import unittest
import urllib.parse
from collections import deque
from pathlib import Path

from w1cip.credential_broker import (
    AccountNotFound,
    BrokerSecretResolver,
    CredentialBroker,
    CredentialBrokerStore,
    CredentialNotFound,
    MemoryCredentialVault,
    OAuthAuthorizationPending,
    OAuthConfigurationError,
    OAuthProviderConfig,
    OAuthSlowDown,
    OAuthStateInvalid,
    run_credential_broker_benchmark,
)
from w1cip.provider_connectors import ConnectorConfig, HTTPRequest, HTTPResponse, ProviderRegistry, StaticSecretResolver
from w1cip.orchestrator import CompiledTask, ProviderRequest


class QueueTransport:
    def __init__(self, responses):
        self.responses = deque(responses)
        self.requests: list[HTTPRequest] = []

    def send(self, request: HTTPRequest) -> HTTPResponse:
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("transport_exhausted")
        return self.responses.popleft()


def response(status: int, body: dict | None = None) -> HTTPResponse:
    return HTTPResponse(status=status, headers={}, body=json.dumps(body or {}).encode("utf-8"))


def provider(**kwargs) -> OAuthProviderConfig:
    defaults = dict(
        provider_id="example",
        issuer="https://auth.example.test",
        authorization_endpoint="https://auth.example.test/authorize",
        token_endpoint="https://auth.example.test/token",
        device_authorization_endpoint="https://auth.example.test/device",
        revocation_endpoint="https://auth.example.test/revoke",
        default_client_id="w1-client",
        grant_types_supported=("authorization_code", "refresh_token", "urn:ietf:params:oauth:grant-type:device_code"),
        code_challenge_methods_supported=("S256",),
    )
    defaults.update(kwargs)
    return OAuthProviderConfig(**defaults)


class CredentialBrokerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root / "credentials.sqlite3"
        self.store = CredentialBrokerStore(self.db)
        self.vault = MemoryCredentialVault()

    def tearDown(self) -> None:
        self.store.close()
        self.temp.cleanup()

    def broker(self, responses=()) -> tuple[CredentialBroker, QueueTransport]:
        transport = QueueTransport(responses)
        broker = CredentialBroker(self.store, self.vault, transport=transport)
        broker.register_provider(provider())
        return broker, transport

    def database_bytes(self) -> bytes:
        self.store.connection.execute("PRAGMA wal_checkpoint(FULL)")
        data = self.db.read_bytes()
        wal = Path(str(self.db) + "-wal")
        if wal.exists():
            data += wal.read_bytes()
        return data

    def test_direct_credential_is_vault_only_and_resolves_by_reference(self) -> None:
        broker, _ = self.broker()
        secret = "api-key-should-never-be-in-sqlite"
        reference = broker.store_credential("openai-main", secret, provider_id="example", label="Main key")
        self.assertEqual("w1-credential:openai-main", reference)
        self.assertEqual(secret, broker.resolve_reference(reference))
        self.assertNotIn(secret.encode(), self.database_bytes())
        inventory = self.store.list_credentials()
        self.assertEqual(reference, inventory[0]["credential_reference"])
        self.assertNotIn(secret, json.dumps(inventory))

    def test_pkce_authorization_stores_state_hash_and_verifier_outside_sqlite(self) -> None:
        broker, _ = self.broker()
        started = broker.start_authorization(
            "example", redirect_uri="http://127.0.0.1:9911/callback", scopes=("models.read",)
        )
        query = urllib.parse.parse_qs(urllib.parse.urlparse(started.authorization_url).query)
        self.assertEqual(["S256"], query["code_challenge_method"])
        self.assertEqual([started.state], query["state"])
        row = self.store.authorization_by_state(started.state)
        verifier = self.vault.get(row["verifier_key"])
        self.assertGreater(len(verifier), 40)
        data = self.database_bytes()
        self.assertNotIn(started.state.encode(), data)
        self.assertNotIn(verifier.encode(), data)

    def test_code_exchange_is_one_time_and_tokens_are_vault_only(self) -> None:
        access = "access-secret-value"
        refresh = "refresh-secret-value"
        broker, transport = self.broker([
            response(200, {"access_token": access, "refresh_token": refresh, "expires_in": 3600, "token_type": "Bearer"})
        ])
        started = broker.start_authorization(
            "example", redirect_uri="http://127.0.0.1:9911/callback", scopes=("models.read",), account_id="alice"
        )
        account = broker.complete_authorization(state=started.state, code="one-time-code", display_name="Alice")
        self.assertEqual("alice", account.account_id)
        self.assertTrue(account.refresh_present)
        self.assertEqual(access, broker.access_token("alice", auto_refresh=False))
        sent = urllib.parse.parse_qs(transport.requests[-1].body.decode())
        self.assertEqual(["authorization_code"], sent["grant_type"])
        self.assertIn("code_verifier", sent)
        self.assertNotIn(access.encode(), self.database_bytes())
        self.assertNotIn(refresh.encode(), self.database_bytes())
        with self.assertRaises(OAuthStateInvalid):
            broker.complete_authorization(state=started.state, code="replay")

    def test_refresh_rotates_access_and_refresh_token(self) -> None:
        broker, _ = self.broker([
            response(200, {"access_token": "first", "refresh_token": "refresh-a", "expires_in": 1}),
            response(200, {"access_token": "second", "refresh_token": "refresh-b", "expires_in": 3600}),
        ])
        started = broker.start_authorization("example", redirect_uri="http://127.0.0.1:9911/callback", account_id="refreshable")
        broker.complete_authorization(state=started.state, code="code")
        refreshed = broker.refresh_account("refreshable")
        self.assertEqual("second", broker.access_token(refreshed.account_id, auto_refresh=False))
        bundle = json.loads(self.vault.get(refreshed.credential_key))
        self.assertEqual("refresh-b", bundle["refresh_token"])
        self.assertNotIn(b"refresh-b", self.database_bytes())

    def test_broker_secret_resolver_supports_env_and_account_references(self) -> None:
        broker, _ = self.broker([
            response(200, {"access_token": "oauth-access", "expires_in": 3600})
        ])
        started = broker.start_authorization("example", redirect_uri="http://127.0.0.1:9911/callback", account_id="resolver-account")
        broker.complete_authorization(state=started.state, code="code")
        resolver = BrokerSecretResolver(broker, StaticSecretResolver({"ENV_KEY": "env-secret"}))
        self.assertEqual("oauth-access", resolver.resolve("w1-account:resolver-account"))
        self.assertEqual("env-secret", resolver.resolve("ENV_KEY"))

    def test_broker_reference_drives_existing_provider_connector(self) -> None:
        broker_transport = QueueTransport([
            response(200, {"access_token": "connector-oauth-token", "expires_in": 3600})
        ])
        broker = CredentialBroker(self.store, self.vault, transport=broker_transport)
        broker.register_provider(provider())
        started = broker.start_authorization("example", redirect_uri="http://127.0.0.1:1/cb", account_id="connector-account")
        broker.complete_authorization(state=started.state, code="code")
        contract = json.dumps({
            "output_type": "contribution",
            "payload": {"ok": True},
            "context_fields_used": ["input"]
        })
        connector_transport = QueueTransport([
            response(200, {"choices": [{"message": {"content": contract}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7}})
        ])
        registry = ProviderRegistry(transport=connector_transport, secret_resolver=BrokerSecretResolver(broker))
        connector = registry.build("openai_compatible", ConnectorConfig(
            resource_id="broker-resource",
            model="model-test",
            api_key_reference="w1-account:connector-account",
            endpoint="https://api.example.test/v1/chat/completions",
            accounting_meter="tokens",
        ))
        request = ProviderRequest(
            run_id="run", session_id="session",
            task=CompiledTask(task_id="task", title="Task", phase="execution", role="executor", expected_output_type="contribution", required_context_fields=("input",)),
            resource_id="broker-resource", idempotency_key="idem", context={"input": "safe"}, prior_outputs={}, attempt=1,
        )
        result = connector.invoke(request)
        self.assertTrue(result.payload["ok"])
        self.assertEqual("Bearer connector-oauth-token", connector_transport.requests[0].headers["authorization"])

    def test_multiple_accounts_for_same_provider_are_separate(self) -> None:
        broker, _ = self.broker([
            response(200, {"access_token": "alice-token", "expires_in": 3600}),
            response(200, {"access_token": "bob-token", "expires_in": 3600}),
        ])
        first = broker.start_authorization("example", redirect_uri="http://127.0.0.1:1/cb", account_id="alice")
        broker.complete_authorization(state=first.state, code="alice-code")
        second = broker.start_authorization("example", redirect_uri="http://127.0.0.1:1/cb", account_id="bob")
        broker.complete_authorization(state=second.state, code="bob-code")
        accounts = self.store.list_accounts("example")
        self.assertEqual(["alice", "bob"], [item.account_id for item in accounts])
        self.assertEqual("alice-token", broker.access_token("alice", auto_refresh=False))
        self.assertEqual("bob-token", broker.access_token("bob", auto_refresh=False))

    def test_device_authorization_pending_then_completes(self) -> None:
        broker, transport = self.broker([
            response(200, {"device_code": "device-secret", "user_code": "ABCD-EFGH", "verification_uri": "https://auth.example.test/device/verify", "expires_in": 600, "interval": 1}),
            response(400, {"error": "authorization_pending"}),
            response(200, {"access_token": "device-access", "refresh_token": "device-refresh", "expires_in": 3600}),
        ])
        started = broker.start_device_authorization("example", scopes=("models.read",), account_id="device-user")
        self.assertEqual("ABCD-EFGH", started.user_code)
        self.assertNotIn(b"device-secret", self.database_bytes())
        with self.assertRaises(OAuthAuthorizationPending):
            broker.poll_device_authorization(started.device_id, enforce_interval=False)
        account = broker.poll_device_authorization(started.device_id, enforce_interval=False)
        self.assertEqual("device-access", broker.access_token(account.account_id, auto_refresh=False))
        self.assertEqual(3, len(transport.requests))

    def test_device_poll_interval_is_governed(self) -> None:
        broker, _ = self.broker([
            response(200, {"device_code": "device-secret", "user_code": "CODE", "verification_uri": "https://auth.example.test/device/verify", "expires_in": 600, "interval": 30}),
            response(400, {"error": "authorization_pending"}),
        ])
        started = broker.start_device_authorization("example", account_id="device-user")
        with self.assertRaises(OAuthAuthorizationPending):
            broker.poll_device_authorization(started.device_id, enforce_interval=False)
        with self.assertRaises(OAuthSlowDown):
            broker.poll_device_authorization(started.device_id, enforce_interval=True)

    def test_discovery_updates_capabilities_and_validates_issuer(self) -> None:
        transport = QueueTransport([
            response(200, {
                "issuer": "https://auth.example.test",
                "authorization_endpoint": "https://auth.example.test/a",
                "token_endpoint": "https://auth.example.test/t",
                "device_authorization_endpoint": "https://auth.example.test/d",
                "revocation_endpoint": "https://auth.example.test/r",
                "code_challenge_methods_supported": ["S256"],
                "grant_types_supported": ["authorization_code", "refresh_token"],
                "scopes_supported": ["one", "two"],
            })
        ])
        broker = CredentialBroker(self.store, self.vault, transport=transport)
        broker.register_provider(OAuthProviderConfig(provider_id="discover", issuer="https://auth.example.test", default_client_id="client"))
        discovered = broker.discover_provider("discover")
        capabilities = discovered.capabilities()
        self.assertTrue(capabilities["pkce_s256"])
        self.assertTrue(capabilities["device_authorization"])
        self.assertEqual(["one", "two"], capabilities["scopes_supported"])

    def test_remote_plain_http_oauth_is_rejected(self) -> None:
        with self.assertRaises(OAuthConfigurationError):
            OAuthProviderConfig(provider_id="bad", issuer="http://example.com")
        allowed = OAuthProviderConfig(provider_id="local", issuer="http://127.0.0.1:9999", allow_insecure_loopback=True)
        self.assertEqual("local", allowed.provider_id)

    def test_client_secret_post_reads_secret_from_vault_not_provider_metadata(self) -> None:
        transport = QueueTransport([response(200, {"access_token": "access", "expires_in": 3600})])
        broker = CredentialBroker(self.store, self.vault, transport=transport)
        broker.store_credential("oauth-client-secret", "very-secret")
        broker.register_provider(provider(
            client_secret_credential_id="oauth-client-secret",
            token_endpoint_auth_method="client_secret_post",
        ))
        started = broker.start_authorization("example", redirect_uri="http://127.0.0.1:1/cb", account_id="client-secret-user")
        broker.complete_authorization(state=started.state, code="code")
        sent = urllib.parse.parse_qs(transport.requests[-1].body.decode())
        self.assertEqual(["very-secret"], sent["client_secret"])
        provider_json = json.dumps(self.store.get_provider("example").redacted_dict())
        self.assertNotIn("very-secret", provider_json)
        self.assertNotIn(b"very-secret", self.database_bytes())

    def test_revocation_uses_refresh_token_and_removes_local_account(self) -> None:
        broker, transport = self.broker([
            response(200, {"access_token": "access", "refresh_token": "refresh", "expires_in": 3600}),
            HTTPResponse(status=200, headers={}, body=b""),
        ])
        started = broker.start_authorization("example", redirect_uri="http://127.0.0.1:1/cb", account_id="revoke-me")
        broker.complete_authorization(state=started.state, code="code")
        self.assertTrue(broker.revoke_account("revoke-me"))
        form = urllib.parse.parse_qs(transport.requests[-1].body.decode())
        self.assertEqual(["refresh"], form["token"])
        with self.assertRaises(AccountNotFound):
            self.store.get_account("revoke-me")

    def test_delete_credential_removes_metadata_and_vault_item(self) -> None:
        broker, _ = self.broker()
        broker.store_credential("temporary", "secret")
        self.assertTrue(broker.delete_credential("temporary"))
        with self.assertRaises(CredentialNotFound):
            broker.resolve_credential("temporary")

    def test_audit_is_redacted_and_hash_chained(self) -> None:
        broker, _ = self.broker()
        broker.store_credential("key", "hidden-value")
        events = list(reversed(self.store.audit_events()))
        self.assertGreaterEqual(len(events), 2)
        previous = "0" * 64
        for event in events:
            self.assertEqual(previous, event["previous_digest"])
            previous = event["event_digest"]
        self.assertNotIn("hidden-value", json.dumps(events))


    def test_sensitive_provider_metadata_and_insecure_redirect_are_rejected(self) -> None:
        with self.assertRaises(OAuthConfigurationError):
            provider(metadata={"client_secret": "must-not-be-here"})
        broker, _ = self.broker()
        with self.assertRaises(OAuthConfigurationError):
            broker.start_authorization("example", redirect_uri="http://remote.example/callback")

    def test_audit_chain_verifier_detects_tampering(self) -> None:
        broker, _ = self.broker()
        broker.store_credential("key", "hidden")
        self.assertTrue(self.store.verify_audit_chain()["valid"])
        self.store.connection.execute("UPDATE credential_audit SET details_json='{}' WHERE sequence=(SELECT MAX(sequence) FROM credential_audit)")
        self.store.connection.commit()
        self.assertFalse(self.store.verify_audit_chain()["valid"])

    def test_benchmark_passes_without_live_network_or_native_vault(self) -> None:
        result = run_credential_broker_benchmark()
        self.assertTrue(result["passed"], result)
        self.assertGreaterEqual(len(result["probes"]), 10)


if __name__ == "__main__":
    unittest.main()
