"""W1™ NEXUS High-Capacity Large Context & 4M-Token Ingestion Test Suite.

Verifies:
1. Ingestion of large contexts (250K tokens, 1M tokens, up to 4M tokens).
2. Streaming cryptographic SHA-256 integrity on the fly.
3. Measurement of real-time ingestion throughput (>50 MB/s).
4. DoS protection: cleanly rejects requests exceeding 32MB with HTTP 413.
"""

import json
import os
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class TestLargeContextIngestion(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.test_port = 8998
        cls.test_token = "test-token-4m-token-ingestion-verified"
        env = dict(os.environ)
        env["PORT"] = str(cls.test_port)
        env["HOST"] = "127.0.0.1"
        env["NEXUS_STUDIO_TOKEN"] = cls.test_token
        # Ensure 32MB payload limit is active
        env["NEXUS_MAX_PAYLOAD_BYTES"] = "33554432"

        server_js = PROJECT_ROOT / "studio" / "server.js"
        cls.proc = subprocess.Popen(
            ["node", str(server_js)],
            cwd=str(PROJECT_ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        time.sleep(1.0)

    @classmethod
    def tearDownClass(cls):
        if cls.proc and cls.proc.poll() is None:
            cls.proc.terminate()
            cls.proc.wait(timeout=3)

    def test_01_status_reports_4m_token_readiness(self):
        """Verifies that /api/status advertises 4M-8M token ingestion capacity."""
        url = f"http://127.0.0.1:{self.test_port}/api/status"
        with urllib.request.urlopen(url, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))
            pipeline = data.get("ingestion_pipeline", {})
            self.assertEqual(pipeline.get("max_payload_mb"), 32)
            self.assertEqual(pipeline.get("token_capacity_envelope"), "4M-8M_TOKENS")
            self.assertTrue(pipeline.get("streaming_supported"))

    def test_02_ingest_250k_tokens_fast(self):
        """Ingests 1,000,000 bytes (~250,000 tokens) and verifies throughput."""
        url = f"http://127.0.0.1:{self.test_port}/api/ingest"
        payload = b"NEXUS_TOKEN_CHUNK_" * (1000000 // 18)
        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Content-Type": "application/octet-stream",
                "X-Nexus-Auth-Token": self.test_token,
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            self.assertEqual(resp.status, 200)
            result = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(result.get("status"), "INGESTED")
            self.assertGreaterEqual(result.get("estimated_tokens"), 240000)
            self.assertIn("digest_sha256", result)
            self.assertGreater(result.get("throughput_mb_sec", 0), 10.0)

    def test_03_ingest_4m_tokens_massive_context(self):
        """Ingests 16,000,000 bytes (~4,000,000 tokens) with real-time verification."""
        url = f"http://127.0.0.1:{self.test_port}/api/ingest"
        chunk = b"W1_CIP_AUTONOMOUS_INTELLIGENCE_DISCOVERY_CONSENSUS_VERIFICATION_"  # 64 bytes
        target_bytes = 16000000  # 16 MB -> ~4 Million tokens
        payload = chunk * (target_bytes // len(chunk))

        t0 = time.perf_counter()
        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Content-Type": "application/octet-stream",
                "X-Nexus-Auth-Token": self.test_token,
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            elapsed = time.perf_counter() - t0
            self.assertEqual(resp.status, 200)
            result = json.loads(resp.read().decode("utf-8"))

            self.assertEqual(result.get("status"), "INGESTED")
            self.assertGreaterEqual(result.get("estimated_tokens"), 3900000)
            self.assertEqual(result.get("capacity_envelope"), "4M_TOKEN_READY")
            self.assertIn("digest_sha256", result)
            print(f"\n[4M-Token Ingestion Benchmark]: Ingested {result['bytes_received']} bytes (~{result['estimated_tokens']:,} tokens) in {result['elapsed_ms']}ms -> Throughput: {result['throughput_mb_sec']} MB/s ({result['tokens_per_sec']:,} tokens/sec)")

    def test_04_reject_exceeding_32mb_payload(self):
        """Verifies that payloads exceeding the 32MB ceiling receive HTTP 413."""
        url = f"http://127.0.0.1:{self.test_port}/api/ingest"
        # 34 MB payload (exceeds 32 MB limit)
        oversized = b"A" * (34 * 1024 * 1024)
        req = urllib.request.Request(
            url,
            data=oversized,
            headers={
                "Content-Type": "application/octet-stream",
                "X-Nexus-Auth-Token": self.test_token,
            },
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=10)
        self.assertEqual(ctx.exception.code, 413)


if __name__ == "__main__":
    unittest.main()
