"""Multi-model / multi-credential architecture tests for NEXUS.

Covers the credential x model matrix, catalog-vs-inference distinction, model
parameter isolation, provenance auditing without secret leakage, and backward
compatibility with the legacy per-family environment setup.
"""

from __future__ import annotations

import os
import unittest
from unittest import mock

from w1cip.benchmark_core.availability import (
    CredentialModelAvailability,
    CredentialModelAvailabilityTester,
)
from w1cip.benchmark_core.model_adapter import get_model_adapter
from w1cip.benchmark_core.profiles import (
    KIMI_K3_PROFILE,
    MUSE_GLIMMER_PROFILE,
    DEEPSEEK_V4_PRO_PROFILE,
    get_model_profile,
)
from w1cip.benchmark_core.schema import NormalizedBenchmarkRunRecord
from w1cip.benchmark_core.secrets import (
    CREDENTIAL_REGISTRY,
    resolve_credential_key,
)


KIMI = "moonshotai/kimi-k3"
MUSE = "meta/muse-glimmer-30b"
DEEPSEEK = "deepseek-ai/deepseek-v4-pro-0813"


class _FakeMatrix:
    """In-memory credential x model entitlement used to emulate discovery."""

    def __init__(self, mapping: dict[str, set[str]]) -> None:
        # credential_ref -> set of model_ids visible in catalog
        self.catalog = mapping
        # model_ids where inference actually succeeds (per credential)
        self.inference_ok: dict[str, set[str]] = {}

    def catalog_visible(self, credential_ref: str, model_id: str) -> bool:
        return model_id in self.catalog.get(credential_ref, set())

    def inference_available(self, credential_ref: str, model_id: str) -> bool:
        ok_for_credential = self.inference_ok.get(credential_ref)
        if ok_for_credential is not None:
            return model_id in ok_for_credential
        return True


class MultiCredentialArchitectureTests(unittest.TestCase):

    def _tester(self, fake: _FakeMatrix, model_ids=None, credential_refs=None):
        return CredentialModelAvailabilityTester(
            catalog_probe=fake.catalog_visible,
            inference_probe=fake.inference_available,
            model_ids=model_ids or [KIMI, MUSE, DEEPSEEK],
            credential_refs=credential_refs or list(fake.catalog),
        )

    # ------------------------------------------------------------------
    # Test 1: Credential A -> Kimi only
    # ------------------------------------------------------------------
    def test_credential_a_kimi_only(self) -> None:
        fake = _FakeMatrix({"credA": {KIMI}})
        records = self._tester(fake).matrix()
        visible = [r for r in records if r.catalog_visible]
        self.assertEqual([KIMI], [r.model_id for r in visible])
        self.assertTrue(all(r.credential_ref == "credA" for r in visible))
        self.assertTrue(all(r.operational for r in visible))

    # ------------------------------------------------------------------
    # Test 2: Credential A -> Kimi + Muse + DeepSeek (same credential)
    # ------------------------------------------------------------------
    def test_credential_a_multiple_models_same_credential(self) -> None:
        fake = _FakeMatrix({"credA": {KIMI, MUSE, DEEPSEEK}})
        records = self._tester(fake).matrix()
        visible = [r for r in records if r.catalog_visible]
        self.assertEqual(sorted([KIMI, MUSE, DEEPSEEK]), sorted(r.model_id for r in visible))
        # All three share the SAME credential_ref -> no fake credentials.
        self.assertEqual({"credA"}, {r.credential_ref for r in visible})
        self.assertTrue(all(r.operational for r in visible))

    # ------------------------------------------------------------------
    # Test 3: three credentials, each a different single model
    # ------------------------------------------------------------------
    def test_three_credentials_one_model_each(self) -> None:
        fake = _FakeMatrix(
            {"credA": {KIMI}, "credB": {DEEPSEEK}, "credC": {MUSE}}
        )
        records = self._tester(fake).matrix()
        pairs = {(r.credential_ref, r.model_id) for r in records if r.catalog_visible}
        self.assertEqual(
            {("credA", KIMI), ("credB", DEEPSEEK), ("credC", MUSE)},
            pairs,
        )
        # None of the credentials incorrectly claims access to another model.
        self.assertNotIn(("credA", MUSE), pairs)

    # ------------------------------------------------------------------
    # Test 4: catalog visible but inference fails -> not operational
    # ------------------------------------------------------------------
    def test_catalog_visible_inference_fails_not_operational(self) -> None:
        fake = _FakeMatrix({"credA": {KIMI}})
        fake.inference_ok = {"credA": set()}  # catalog says Kimi but inference fails
        records = self._tester(fake).matrix()
        kimi = next(r for r in records if r.model_id == KIMI)
        self.assertTrue(kimi.catalog_visible)
        self.assertFalse(kimi.inference_available)
        self.assertFalse(kimi.operational)

    # ------------------------------------------------------------------
    # Test 5: model-specific params must not leak across shared credential
    # ------------------------------------------------------------------
    def test_model_params_do_not_leak_across_shared_credential(self) -> None:
        kimi = get_model_profile(KIMI)
        muse = get_model_profile(MUSE)
        deepseek = get_model_profile(DEEPSEEK)

        # Kimi's reasoning_effort="max" must never appear in Muse/DeepSeek.
        self.assertEqual("max", kimi.reasoning_effort)
        self.assertIsNone(muse.reasoning_effort)
        self.assertIsNone(deepseek.reasoning_effort)

        # DeepSeek's thinking template must not appear in Kimi/Muse.
        self.assertEqual({"thinking": False}, deepseek.chat_template_kwargs)
        self.assertIsNone(kimi.chat_template_kwargs)
        self.assertIsNone(muse.chat_template_kwargs)

        # Temperature/top_p contracts remain distinct.
        self.assertEqual(1.0, kimi.default_temperature)
        self.assertEqual(0.7, muse.default_temperature)
        self.assertEqual(0.95, kimi.default_top_p)
        self.assertEqual(0.95, muse.default_top_p)

    # ------------------------------------------------------------------
    # Test 6: trial record contains provider/credential/model/endpoint,
    #         and zero secret material.
    # ------------------------------------------------------------------
    def test_record_provenance_no_secret_leak(self) -> None:
        record = NormalizedBenchmarkRunRecord(
            run_id="r1",
            benchmark="Bench",
            benchmark_version="1.0",
            benchmark_release_tag="tag",
            task_id="t1",
            task_category="cat",
            model=KIMI,
            provider="nvidia",
            provider_id="nvidia",
            endpoint_url="https://integrate.api.nvidia.com/v1",
            credential_ref="nvidia-main",
            mode="MODE_A_RAW_MODEL",
            attempt=1,
            max_attempts_allowed=1,
            success=True,
            score=1.0,
            input_tokens=10,
            output_tokens=5,
            reasoning_tokens=0,
            cached_tokens=0,
            total_tokens=15,
            tool_calls=0,
            successful_tool_calls=0,
            failed_tool_calls=0,
            tool_call_types=[],
            latency_ms=1.0,
            time_to_first_token_ms=0.5,
            model_time_ms=1.0,
            tool_time_ms=0.0,
            environment_time_ms=0.0,
            retries=0,
            failure_type=None,
            failure_reason=None,
        )
        d = record.to_dict()
        self.assertEqual("nvidia", d["provider_id"])
        self.assertEqual("nvidia-main", d["credential_ref"])
        self.assertEqual(KIMI, d["model"])
        self.assertEqual("https://integrate.api.nvidia.com/v1", d["endpoint_url"])
        blob = repr(d).lower()
        self.assertNotIn("nvapi-", blob)
        self.assertNotIn("api_key", blob)

    # ------------------------------------------------------------------
    # Test 7: legacy env setup continues to work
    # ------------------------------------------------------------------
    @mock.patch.dict(
        os.environ,
        {
            "NVIDIA_API_KEY": "mock-test-nvidia-key-for-unit-tests-only",
            "NVIDIA_API_KEY_KIMI": "mock-test-nvidia-key-kimi-for-unit-tests-only",
        },
        clear=False,
    )
    def test_legacy_env_backward_compat(self) -> None:
        # The legacy per-family env mapping still resolves a key.
        self.assertTrue(resolve_credential_key("nvidia-kimi"))
        self.assertTrue(resolve_credential_key("nvidia-main"))
        # get_model_adapter("kimi") still builds an adapter without errors.
        adapter = get_model_adapter("kimi")
        self.assertEqual(KIMI, adapter.model_id)
        # The legacy registry still maps the legacy env var names.
        self.assertEqual("NVIDIA_API_KEY_KIMI", CREDENTIAL_REGISTRY["nvidia-kimi"])

    # ------------------------------------------------------------------
    # Test 8: same model + two credentials -> two distinct provenance records
    # ------------------------------------------------------------------
    def test_same_model_two_credentials_distinct_provenance(self) -> None:
        def make_record(credential_ref: str) -> NormalizedBenchmarkRunRecord:
            return NormalizedBenchmarkRunRecord(
                run_id="r", benchmark="b", benchmark_version="1", benchmark_release_tag="t",
                task_id="task", task_category="cat", model=KIMI, provider="nvidia",
                provider_id="nvidia", endpoint_url="https://x/v1", credential_ref=credential_ref,
                mode="MODE_A_RAW_MODEL", attempt=1, max_attempts_allowed=1, success=True,
                score=1.0, input_tokens=1, output_tokens=1, reasoning_tokens=0, cached_tokens=0,
                total_tokens=2, tool_calls=0, successful_tool_calls=0, failed_tool_calls=0,
                tool_call_types=[], latency_ms=1.0, time_to_first_token_ms=None, model_time_ms=1.0,
                tool_time_ms=0.0, environment_time_ms=0.0, retries=0, failure_type=None,
                failure_reason=None,
            )

        rec_a = make_record("credA")
        rec_b = make_record("credB")
        # Same model, different credentials -> distinct provenance identity.
        self.assertEqual(rec_a.model, rec_b.model)
        self.assertNotEqual(rec_a.credential_ref, rec_b.credential_ref)
        # Round-trip preserves the distinct credential refs.
        self.assertEqual("credA", NormalizedBenchmarkRunRecord.from_dict(rec_a.to_dict()).credential_ref)
        self.assertEqual("credB", NormalizedBenchmarkRunRecord.from_dict(rec_b.to_dict()).credential_ref)

    # ------------------------------------------------------------------
    # Test 9: one credential + three models, no fake credentials
    # ------------------------------------------------------------------
    def test_one_credential_three_models_no_fake_credentials(self) -> None:
        fake = _FakeMatrix({"credA": {KIMI, MUSE, DEEPSEEK}})
        records = self._tester(fake).matrix()
        visible = [r for r in records if r.catalog_visible]
        # Exactly one real credential backs all three models.
        self.assertEqual({("credA", KIMI), ("credA", MUSE), ("credA", DEEPSEEK)},
                         {(r.credential_ref, r.model_id) for r in visible})
        self.assertEqual({"credA"}, {r.credential_ref for r in visible})
        self.assertEqual(3, len(visible))

    # ------------------------------------------------------------------
    # Test 10: resolve_model explicit mode
    # ------------------------------------------------------------------
    def test_resolve_model_explicit_mode(self) -> None:
        from w1cip.benchmark_core.model_adapter import resolve_model
        from w1cip.benchmark_core.secrets import register_credential

        register_credential("nvidia-custom", "CUSTOM_SECRET_ENV", label="Custom Test Credential")
        adapter = resolve_model(
            provider="nvidia",
            model_id="moonshotai/kimi-k3",
            credential_ref="nvidia-custom",
        )
        self.assertEqual("moonshotai/kimi-k3", adapter.model_id)
        self.assertEqual("nvidia-custom", adapter.credential_ref)

        # Non-existent credential raises ValueError
        with self.assertRaises(ValueError):
            resolve_model(
                provider="nvidia",
                model_id="moonshotai/kimi-k3",
                credential_ref="non-existent-cred",
            )

    # ------------------------------------------------------------------
    # Test 11: resolve_model auto mode with entitlements
    # ------------------------------------------------------------------
    def test_resolve_model_auto_mode_with_entitlements(self) -> None:
        from w1cip.benchmark_core.model_adapter import resolve_model
        from w1cip.benchmark_core.secrets import register_credential

        register_credential("nvidia-cred-kimi", "KEY_KIMI", label="Kimi Only")
        register_credential("nvidia-cred-deepseek", "KEY_DEEPSEEK", label="DeepSeek Only")

        entitlements = {
            "nvidia-cred-kimi": [KIMI],
            "nvidia-cred-deepseek": [DEEPSEEK],
        }

        # Auto mode selects nvidia-cred-kimi for KIMI
        adapter_kimi = resolve_model(
            provider="nvidia",
            model_id=KIMI,
            credential_ref=None,
            entitlements=entitlements,
        )
        self.assertEqual("nvidia-cred-kimi", adapter_kimi.credential_ref)

        # Auto mode selects nvidia-cred-deepseek for DEEPSEEK
        adapter_ds = resolve_model(
            provider="nvidia",
            model_id=DEEPSEEK,
            credential_ref=None,
            entitlements=entitlements,
        )
        self.assertEqual("nvidia-cred-deepseek", adapter_ds.credential_ref)

    # ------------------------------------------------------------------
    # Test 12: explicit mode enforces entitlement if matrix is supplied
    # ------------------------------------------------------------------
    def test_explicit_selector_enforces_entitlement(self) -> None:
        from w1cip.benchmark_core.model_adapter import resolve_model
        from w1cip.benchmark_core.secrets import register_credential

        register_credential("nvidia-cred-restricted", "KEY_RESTRICTED")
        entitlements = {
            "nvidia-cred-restricted": [MUSE],  # only entitled for Muse
        }

        # Attempting to explicitly use restricted credential for Kimi must raise PermissionError
        with self.assertRaises(PermissionError):
            resolve_model(
                provider="nvidia",
                model_id=KIMI,
                credential_ref="nvidia-cred-restricted",
                entitlements=entitlements,
            )

    # ------------------------------------------------------------------
    # Test 13: profile-driven generic OpenAICompatibleAdapter
    # ------------------------------------------------------------------
    def test_openai_compatible_adapter_profile_contract(self) -> None:
        from w1cip.benchmark_core.model_adapter import OpenAICompatibleAdapter
        from w1cip.benchmark_core.profiles import ModelProfile, register_model_profile

        custom_prof = ModelProfile(
            provider_id="nvidia",
            model_id="custom-vendor/custom-model-7b",
            display_name="Custom 7B",
            streaming_preferred=False,
            timeout_seconds=45.0,
            min_max_tokens=2048,
            default_temperature=0.8,
            default_top_p=0.9,
        )
        register_model_profile(custom_prof)

        adapter = OpenAICompatibleAdapter(
            model_id="custom-vendor/custom-model-7b",
            credential_ref="nvidia-main",
        )
        self.assertEqual("custom-vendor/custom-model-7b", adapter.model_id)
        self.assertEqual(45.0, adapter.profile.timeout_seconds)
        self.assertEqual(0.8, adapter.profile.default_temperature)
        self.assertEqual(0.9, adapter.profile.default_top_p)
        self.assertFalse(adapter.profile.streaming_preferred)


if __name__ == "__main__":
    unittest.main()