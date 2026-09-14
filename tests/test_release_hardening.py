from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from w1cip.native_packaging import DeepLinkDenied, parse_deep_link
from w1cip.release_hardening import (
    ExternalBenchmarkEvidenceError,
    ExternalResultStore,
    external_benchmark_catalog,
    release_gate,
    run_release_hardening_benchmark,
    source_manifest,
)

ROOT = Path(__file__).resolve().parents[1]


class ReleaseHardeningTests(unittest.TestCase):
    def test_hardening_benchmark_passes_without_network(self) -> None:
        result = run_release_hardening_benchmark(ROOT)
        self.assertTrue(result["passed"], result)
        self.assertEqual(result["metrics"]["network_calls"], 0)
        self.assertGreaterEqual(result["metrics"]["fuzz_cases"], 600)
        self.assertTrue(result["collaboration_load_recovery"]["audit_tamper_detected"])

    def test_deep_link_sensitive_aliases_fail_closed(self) -> None:
        for key in ("password", "credential", "api_key", "access_token", "refresh_token", "bearer"):
            with self.subTest(key=key), self.assertRaises(DeepLinkDenied):
                parse_deep_link(f"w1://workspace/open?{key}=do-not-accept")

    def test_source_manifest_is_deterministic(self) -> None:
        first = source_manifest(ROOT)
        second = source_manifest(ROOT)
        self.assertEqual(first["tree_sha256"], second["tree_sha256"])
        self.assertGreater(len(first["files"]), 20)

    def test_external_catalog_never_contains_scores(self) -> None:
        catalog = external_benchmark_catalog()
        self.assertGreaterEqual(len(catalog["benchmarks"]), 5)
        for item in catalog["benchmarks"]:
            self.assertNotIn("score", item)
            self.assertNotIn("metrics", item)
            self.assertNotIn("result", item)

    def test_external_result_requires_raw_evidence_and_is_immutable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            store = ExternalResultStore(workspace)
            evidence = workspace / "upstream-result.json"
            evidence.write_text('{"pass_rate": 0.5}\n', encoding="utf-8")
            result = {
                "result_id": "mcp-run-1",
                "benchmark_id": "mcp-conformance-2026-07-28",
                "benchmark_version": "2026-07-28",
                "upstream_commit": "0123456789abcdef",
                "adapter_version": "w1-dev41",
                "executed_at": "2026-08-08T12:00:00Z",
                "system_under_test": "W1 Nexus MCP",
                "metrics": {"pass_rate": 0.5},
                "environment": {"os": "test"},
            }
            recorded = store.record(result, evidence)
            self.assertEqual(recorded["raw_evidence_size"], evidence.stat().st_size)
            self.assertTrue(store.verify("mcp-run-1", evidence)["valid"])
            with self.assertRaises(ExternalBenchmarkEvidenceError):
                store.record(result, evidence)
            evidence.write_text('{"pass_rate": 1.0}\n', encoding="utf-8")
            self.assertFalse(store.verify("mcp-run-1", evidence)["valid"])

    def test_public_release_gate_accepts_intentional_apache_identity(self) -> None:
        result = release_gate(ROOT, public_release=True)
        self.assertTrue(result["passed"], result)
        self.assertTrue(result["checks"]["public_license_present"])
        self.assertTrue(result["checks"]["release_identity_verified"])
        self.assertEqual(result["brand_identity"]["identity"]["license_spdx"], "Apache-2.0")
        # Native publication remains a separate packaging/signing boundary.
        self.assertTrue(result["brand_identity"]["production_ready"])


if __name__ == "__main__":
    unittest.main()
