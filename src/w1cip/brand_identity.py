"""W1 Nexus release identity and brand-contract verification.

The brand contract intentionally separates source-code licensing from project
identity.  It does not claim that W1 is a registered company or that W1 Nexus
is a registered trademark.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, asdict
from importlib import resources
from pathlib import Path
from typing import Any, Mapping

BRAND_IDENTITY_VERSION = "1.0"
EXPECTED_LICENSE_SPDX = "Apache-2.0"
APACHE_2_OFFICIAL_TEXT_SHA256 = "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30"


class BrandIdentityError(RuntimeError):
    code = "brand_identity_invalid"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _packaged_manifest() -> dict[str, Any]:
    text = resources.files("w1cip").joinpath("brand_assets/brand-manifest.json").read_text(encoding="utf-8")
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise BrandIdentityError("brand_manifest_object_required")
    return payload


def load_brand_manifest(project_root: str | Path | None = None) -> tuple[dict[str, Any], bool]:
    if project_root is not None:
        root = Path(project_root).resolve()
        path = root / "brand" / "brand-manifest.json"
        if path.is_file():
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise BrandIdentityError("brand_manifest_object_required")
            return payload, True
    return _packaged_manifest(), False


def verify_brand_identity(project_root: str | Path | None = None, *, require_production_assets: bool = False) -> dict[str, Any]:
    manifest, source_context = load_brand_manifest(project_root)
    checks: dict[str, bool] = {
        "schema_supported": manifest.get("schema") == "w1-brand-identity/1.0",
        "display_name_is_w1_nexus_tm": manifest.get("display_name") == "W1 Nexus™",
        "app_id_is_stable": manifest.get("application_id") == "com.w1.nexus",
        "license_declared_apache_2": manifest.get("license_spdx") == EXPECTED_LICENSE_SPDX,
        "registered_mark_not_claimed": manifest.get("registered_mark_claimed") is False,
        "master_concept_hash_declared": bool((manifest.get("master_concept") or {}).get("sha256")),
    }
    blockers: list[str] = []
    warnings: list[str] = []
    production = manifest.get("production_assets") if isinstance(manifest.get("production_assets"), Mapping) else {}
    required_assets = [
        details for details in production.values()
        if isinstance(details, Mapping) and bool(details.get("required_for_native_release"))
    ]
    if source_context:
        root = Path(project_root).resolve()  # type: ignore[arg-type]
        license_path = root / "LICENSE"
        notice_path = root / "NOTICE"
        policy_path = root / "BRAND-POLICY.md"
        master = root / str((manifest.get("master_concept") or {}).get("path") or "")
        checks.update({
            "license_file_present": license_path.is_file(),
            "notice_file_present": notice_path.is_file(),
            "brand_policy_present": policy_path.is_file(),
            "master_concept_present": master.is_file(),
            "master_concept_hash_matches": master.is_file() and _sha256_file(master) == (manifest.get("master_concept") or {}).get("sha256"),
            "apache_2_text_is_official_copy": license_path.is_file() and _sha256_file(license_path) == APACHE_2_OFFICIAL_TEXT_SHA256,
        })
        missing_production: list[str] = []
        mismatched_production: list[str] = []
        for details in required_assets:
            rel = str(details.get("path") or "")
            path = root / rel
            if not path.is_file():
                missing_production.append(rel)
                continue
            expected = str(details.get("sha256") or "")
            if not expected or _sha256_file(path) != expected:
                mismatched_production.append(rel)
        checks["required_native_brand_assets_present"] = not missing_production
        checks["required_native_brand_assets_hash_match"] = not mismatched_production
        if missing_production:
            warnings.append("production_brand_assets_pending:" + ",".join(missing_production))
        if mismatched_production:
            warnings.append("production_brand_assets_hash_mismatch:" + ",".join(mismatched_production))
    else:
        checks["installed_brand_contract_available"] = True
        package_root = resources.files("w1cip.brand_assets")
        installed_ready = True
        installed_mismatch: list[str] = []
        for details in required_assets:
            if details.get("status") != "ready":
                installed_ready = False
                continue
            package_asset = str(details.get("package_asset") or Path(str(details.get("path") or "")).name)
            asset = package_root.joinpath(package_asset)
            try:
                payload = asset.read_bytes()
            except Exception:
                installed_ready = False
                installed_mismatch.append(package_asset + ":missing")
                continue
            expected = str(details.get("sha256") or "")
            if not expected or hashlib.sha256(payload).hexdigest() != expected:
                installed_ready = False
                installed_mismatch.append(package_asset + ":sha256")
        checks["required_native_brand_assets_present"] = installed_ready
        checks["required_native_brand_assets_hash_match"] = not installed_mismatch
        if not installed_ready:
            warnings.append("production_brand_assets_not_embedded_in_wheel")
        if installed_mismatch:
            warnings.append("production_brand_assets_package_mismatch:" + ",".join(installed_mismatch))

    core_check_names = [name for name in checks if name not in {"required_native_brand_assets_present", "required_native_brand_assets_hash_match"}]
    for name in core_check_names:
        if not checks[name]:
            blockers.append(name)
    if require_production_assets and not checks["required_native_brand_assets_present"]:
        blockers.append("required_native_brand_assets_missing")
    if require_production_assets and not checks.get("required_native_brand_assets_hash_match", False):
        blockers.append("required_native_brand_assets_hash_mismatch")
    return {
        "brand_identity_version": BRAND_IDENTITY_VERSION,
        "passed": not blockers,
        "source_context": source_context,
        "checks": checks,
        "blockers": blockers,
        "warnings": warnings,
        "identity": {
            "product_name": manifest.get("product_name"),
            "display_name": manifest.get("display_name"),
            "application_id": manifest.get("application_id"),
            "license_spdx": manifest.get("license_spdx"),
            "trademark_status": manifest.get("trademark_status"),
            "master_concept_sha256": (manifest.get("master_concept") or {}).get("sha256"),
        },
        "production_ready": bool(checks["required_native_brand_assets_present"] and checks.get("required_native_brand_assets_hash_match", False)),
    }


__all__ = [
    "BRAND_IDENTITY_VERSION", "EXPECTED_LICENSE_SPDX", "APACHE_2_OFFICIAL_TEXT_SHA256",
    "BrandIdentityError", "load_brand_manifest", "verify_brand_identity",
]
