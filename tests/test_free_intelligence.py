import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path

from w1cip.ai_connections import AIConnectionStore, AIConnectionService
from w1cip.capacity_router import CapacityStore
from w1cip.free_intelligence import FreeIntelligenceDiscovery
from w1cip.model_access import ModelAccessStore


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self): return json.dumps(self.payload).encode()


class FreeIntelligenceTests(unittest.TestCase):
    def build(self, td):
        cs = AIConnectionStore(Path(td) / 'connections.sqlite3')
        ms = ModelAccessStore(Path(td) / 'models.sqlite3')
        cap = CapacityStore(Path(td) / 'capacity.sqlite3')
        return cs, ms, cap, FreeIntelligenceDiscovery(connection_store=cs, model_store=ms, capacity_store=cap, service=AIConnectionService(cs, ms))

    def test_third_party_gateways_are_opt_in(self):
        with tempfile.TemporaryDirectory() as td:
            cs, ms, cap, discovery = self.build(td)
            findings = discovery.scan_loopback(include_third_party=False, opener=lambda req, timeout: FakeResponse({'data': []}))
            by_id = {x.target_id: x for x in findings}
            self.assertEqual(by_id['freellmapi-local'].status, 'opt_in_required')
            self.assertEqual(by_id['9router-local'].status, 'opt_in_required')

    def test_ollama_models_can_be_discovered_and_adopted(self):
        with tempfile.TemporaryDirectory() as td:
            cs, ms, cap, discovery = self.build(td)
            def opener(req, timeout):
                url = req.full_url
                if '11434' in url:
                    return FakeResponse({'data': [{'id': 'qwen-local'}, {'id': 'llama-local'}]})
                raise urllib.error.URLError('not running')
            findings = discovery.scan_loopback(include_third_party=True, opener=opener)
            ollama = next(x for x in findings if x.target_id == 'ollama-local')
            self.assertEqual(ollama.status, 'available')
            self.assertEqual(len(ollama.models), 2)
            adopted = discovery.adopt_local_ollama(ollama)
            self.assertEqual(adopted['count'], 2)
            self.assertEqual(len(ms.list_profiles()), 2)
            observations = cap.list()
            self.assertTrue(all(x.source == 'local' and x.quota_state == 'unlimited' for x in observations))

    def test_connected_model_visibility_does_not_claim_free_entitlement(self):
        with tempfile.TemporaryDirectory() as td:
            cs, ms, cap, discovery = self.build(td)
            service = AIConnectionService(cs, ms)
            service.add_local_connection(connection_id='local', endpoint='http://127.0.0.1:11434/v1/chat/completions')
            discovery.service.discover_models = lambda connection_id, timeout_seconds=8.0: ('model-a',)
            finding = discovery.scan_connected()[0]
            self.assertEqual(finding.capacity_source, 'unknown')
            self.assertIn('not inferred', finding.message)
