from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from w1cip.brand_identity import APACHE_2_OFFICIAL_TEXT_SHA256, load_brand_manifest, verify_brand_identity

ROOT = Path(__file__).resolve().parents[1]


class BrandIdentityTests(unittest.TestCase):
    def test_source_release_identity_is_verified(self) -> None:
        result = verify_brand_identity(ROOT)
        self.assertTrue(result["passed"], result)
        self.assertEqual(result["identity"]["display_name"], "W1 Nexus™")
        self.assertEqual(result["identity"]["license_spdx"], "Apache-2.0")
        self.assertTrue(result["checks"]["apache_2_text_is_official_copy"])
        self.assertTrue(result["checks"]["master_concept_hash_matches"])
        self.assertTrue(result["production_ready"])
        self.assertTrue(result["checks"]["required_native_brand_assets_present"])
        self.assertTrue(result["checks"]["required_native_brand_assets_hash_match"])

    def test_master_concept_is_hash_pinned(self) -> None:
        manifest, source = load_brand_manifest(ROOT)
        self.assertTrue(source)
        selected = ROOT / manifest["master_concept"]["path"]
        self.assertTrue(selected.is_file())
        import hashlib
        self.assertEqual(hashlib.sha256(selected.read_bytes()).hexdigest(), manifest["master_concept"]["sha256"])

    def test_registered_mark_is_not_claimed(self) -> None:
        manifest, _ = load_brand_manifest(ROOT)
        self.assertFalse(manifest["registered_mark_claimed"])
        self.assertEqual(manifest["trademark_status"], "unregistered-project-mark")

    def test_production_asset_gate_is_satisfied(self) -> None:
        result = verify_brand_identity(ROOT, require_production_assets=True)
        self.assertTrue(result["passed"], result)
        self.assertTrue(result["production_ready"])

    def test_tampered_production_asset_fails_native_gate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            shutil.copytree(ROOT / "brand", target / "brand")
            shutil.copy2(ROOT / "NOTICE", target / "NOTICE")
            shutil.copy2(ROOT / "BRAND-POLICY.md", target / "BRAND-POLICY.md")
            shutil.copy2(ROOT / "LICENSE", target / "LICENSE")
            selected = target / "brand" / "production" / "w1-nexus.ico"
            selected.write_bytes(selected.read_bytes() + b"tamper")
            result = verify_brand_identity(target, require_production_assets=True)
            self.assertFalse(result["passed"])
            self.assertIn("required_native_brand_assets_hash_mismatch", result["blockers"])

    def test_tampered_license_fails_release_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            shutil.copytree(ROOT / "brand", target / "brand")
            shutil.copy2(ROOT / "NOTICE", target / "NOTICE")
            shutil.copy2(ROOT / "BRAND-POLICY.md", target / "BRAND-POLICY.md")
            (target / "LICENSE").write_text("not-apache\n", encoding="utf-8")
            result = verify_brand_identity(target)
            self.assertFalse(result["passed"])
            self.assertFalse(result["checks"]["apache_2_text_is_official_copy"])


if __name__ == "__main__":
    unittest.main()
