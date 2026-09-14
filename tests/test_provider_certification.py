from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from w1cip.ai_connections import AIConnectionRecord, AIConnectionStore
from w1cip.credential_broker import CredentialBroker, CredentialBrokerStore, MemoryCredentialVault
from w1cip.provider_certification import (
    BillableCertificationApprovalRequired,
    LiveCertificationRequired,
    ProviderCertificationStore,
    ProviderCertifier,
    PROVIDER_CONTRACTS,
    provider_certification_catalog,
    run_provider_certification_benchmark,
)
from w1cip.provider_connectors import HTTPRequest, HTTPResponse


class QueueTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests: list[HTTPRequest] = []

    def send(self, request: HTTPRequest) -> HTTPResponse:
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("responses_exhausted")
        return self.responses.pop(0)


def response(status: int, body, headers=None):
    return HTTPResponse(status, headers or {}, json.dumps(body).encode())


class ProviderCertificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.connections = AIConnectionStore(self.root / "connections.sqlite3")
        self.credentials = CredentialBrokerStore(self.root / "credentials.sqlite3")
        self.vault = MemoryCredentialVault()
        self.broker = CredentialBroker(self.credentials, self.vault)
        ref = self.broker.store_credential("openai-test", "secret-provider-value", provider_id="openai")
        self.connections.put_connection(AIConnectionRecord(
            connection_id="openai-main", provider_id="openai", display_name="OpenAI", auth_mode="api_key",
            credential_reference=ref, endpoint="https://api.openai.com/v1/responses", status="connected",
        ))
        self.store = ProviderCertificationStore(self.root / "certifications.sqlite3")

    def tearDown(self) -> None:
        self.store.close()
        self.credentials.close()
        self.temp.cleanup()

    def test_catalog_tracks_current_preferred_surfaces(self) -> None:
        ids = {item["provider_id"] for item in provider_certification_catalog()}
        self.assertTrue({"openai", "anthropic", "gemini", "xai"}.issubset(ids))
        self.assertEqual("Responses API", PROVIDER_CONTRACTS["xai"].preferred_surface)
        self.assertEqual("Interactions API", PROVIDER_CONTRACTS["gemini"].preferred_surface)

    def test_plan_is_non_network_and_billable_probes_are_explicit(self) -> None:
        certifier = ProviderCertifier(self.connections, self.store, credential_broker=self.broker)
        plan = certifier.plan("openai-main", model_name="gpt-test", level="full")
        self.assertTrue(plan["live_calls_required"])
        self.assertEqual(3, plan["maximum_billable_calls"])
        with self.assertRaises(LiveCertificationRequired):
            certifier.run("openai-main", model_name="gpt-test", level="full", live=False, allow_billable=True)
        with self.assertRaises(BillableCertificationApprovalRequired):
            certifier.run("openai-main", model_name="gpt-test", level="full", live=True, allow_billable=False)

    def test_preflight_certification_persists_only_fingerprints(self) -> None:
        transport = QueueTransport([response(200, {"data": [{"id": "gpt-test"}]}, {"x-request-id": "req-1"})])
        certifier = ProviderCertifier(self.connections, self.store, credential_broker=self.broker, transport=transport)
        record = certifier.run("openai-main", model_name="gpt-test", level="preflight", live=True)
        self.assertEqual("CERTIFIED", record.status)
        self.assertEqual(1, record.live_calls)
        raw = (self.root / "certifications.sqlite3").read_bytes()
        self.assertNotIn(b"secret-provider-value", raw)
        self.assertNotIn(b"gpt-test\"}]", raw)
        records = self.store.list("openai-main")
        self.assertEqual(record.certification_id, records[0]["certification_id"])

    def test_executable_benchmark(self) -> None:
        result = run_provider_certification_benchmark()
        self.assertTrue(result["passed"], result)
        self.assertEqual(0, result["metrics"]["live_provider_calls"])

    def test_stable_sdk_exports_certification_contract(self) -> None:
        from w1cip.sdk import ProviderCertifier as SDKProviderCertifier, SDK_VERSION, provider_certification_catalog as sdk_catalog
        self.assertEqual("1.2.0", SDK_VERSION)
        self.assertIs(SDKProviderCertifier, ProviderCertifier)
        self.assertGreaterEqual(len(sdk_catalog()), 6)


if __name__ == "__main__":
    unittest.main()
