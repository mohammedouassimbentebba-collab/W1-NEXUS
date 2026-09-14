from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from w1cip.native_packaging import (
    AppIdentity,
    DeepLinkDenied,
    NativeServiceController,
    ProjectDescriptor,
    ProjectDescriptorInvalid,
    UpdateArtifact,
    UpdateManifest,
    UpdateVerificationError,
    generate_windows_packaging_sources,
    native_build_plan,
    parse_deep_link,
    run_native_packaging_benchmark,
    sha256_file,
    verify_update_artifact,
    verify_windows_release_candidate,
)


class NativePackagingTests(unittest.TestCase):
    def test_app_identity_is_stable(self) -> None:
        identity = AppIdentity()
        identity.validate()
        self.assertEqual(identity.app_id, "com.w1.nexus")
        self.assertEqual(identity.url_scheme, "w1")
        self.assertEqual(identity.project_extension, ".w1nexus")

    def test_deep_link_routes_are_allowlisted(self) -> None:
        action = parse_deep_link("w1://artifact/open?id=abc-123")
        self.assertEqual(action.area, "artifact")
        self.assertEqual(action.parameters["id"], "abc-123")
        with self.assertRaises(DeepLinkDenied):
            parse_deep_link("https://example.com/")
        with self.assertRaises(DeepLinkDenied):
            parse_deep_link("w1://terminal/run?argv=cmd.exe")

    def test_deep_link_never_accepts_shell_or_secret_parameters(self) -> None:
        for uri in (
            "w1://workspace/open?command=calc.exe",
            "w1://settings/open?token=secret",
            "w1://workspace/open?path=..%2Foutside",
            "w1://workspace/open?path=C%3A%5CWindows",
        ):
            with self.subTest(uri=uri), self.assertRaises(DeepLinkDenied):
                parse_deep_link(uri)

    def test_project_descriptor_is_relative_and_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            selected = root / "demo.w1nexus"
            ProjectDescriptor(name="Demo", workspace="workspace", open_target="docs/readme.md").write(selected)
            loaded = ProjectDescriptor.load(selected)
            self.assertEqual(loaded.resolve_workspace(selected), workspace.resolve())
            with self.assertRaises(ProjectDescriptorInvalid):
                ProjectDescriptor(name="Bad", workspace="../outside").validate()

    def test_update_manifest_requires_https_and_signature_metadata(self) -> None:
        artifact = UpdateArtifact(
            platform="windows", architecture="x64", version="1.0.0",
            url="https://updates.example.invalid/w1.exe", size=1, sha256="0" * 64,
            signature_kind="authenticode", signer_identity="thumbprint:abc",
        )
        manifest = UpdateManifest(channel="stable", published_at="2026-08-08T00:00:00Z", artifacts=(artifact,))
        manifest.validate()
        parsed = UpdateManifest.from_mapping(manifest.as_dict())
        self.assertEqual(parsed.select("windows", "x64").version, "1.0.0")
        with self.assertRaises(UpdateVerificationError):
            UpdateArtifact(**{**artifact.as_dict(), "url": "http://example.invalid/w1.exe"}).validate()
        with self.assertRaises(UpdateVerificationError):
            UpdateArtifact(**{**artifact.as_dict(), "signer_identity": None}).validate()

    def test_update_payload_hash_is_verified(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "payload.bin"
            path.write_bytes(b"verified")
            artifact = UpdateArtifact(
                platform="windows", architecture="x64", version="1.0.0",
                url="https://updates.example.invalid/payload.bin", size=path.stat().st_size,
                sha256=sha256_file(path), signature_kind="authenticode", signer_identity="test",
            )
            result = verify_update_artifact(artifact, path)
            self.assertTrue(result["integrity_verified"])
            self.assertFalse(result["native_signature_verified"])
            path.write_bytes(b"tampered")
            with self.assertRaises(UpdateVerificationError):
                verify_update_artifact(artifact, path)

    def test_windows_sources_are_per_user_and_secret_free(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = generate_windows_packaging_sources(directory)
            self.assertEqual(len(result["files"]), 6)
            inno = (Path(directory) / "installer.iss").read_text(encoding="utf-8")
            register = (Path(directory) / "register-user.ps1").read_text(encoding="utf-8")
            self.assertIn("Root: HKCU", inno)
            self.assertNotIn("HKLM", inno)
            self.assertIn("URL Protocol", register)
            self.assertIn("--deep-link", inno)
            self.assertIn("--project", inno)
            self.assertNotIn("refresh_token", inno)
            self.assertIn("SetupIconFile=..\\..\\brand\\production\\w1-nexus.ico", inno)
            spec = (Path(directory) / "w1-nexus.spec").read_text(encoding="utf-8")
            self.assertIn("icon=str(icon)", spec)
            self.assertIn("version=str(version_info)", spec)
            self.assertTrue((Path(directory) / "version-info.txt").is_file())

    def test_native_build_plan_does_not_claim_cross_compile(self) -> None:
        plan = native_build_plan(target="windows")
        if os.name != "nt":
            self.assertFalse(plan.can_compile_native_installer_here)
        self.assertIn("pyinstaller", plan.tools)
        self.assertIn("signtool", plan.tools)

    def test_service_state_is_identity_bound_and_private(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            (workspace / ".w1nexus").mkdir(parents=True)
            controller = NativeServiceController(workspace)
            state = controller.start(command=[sys.executable, "-c", "import time; time.sleep(20)"], port=19001)
            self.addCleanup(lambda: controller.stop(timeout_seconds=1) if controller.status().get("running") else None)
            status = controller.status()
            self.assertTrue(status["running"])
            self.assertTrue(status["process_identity_matches"])
            if os.name != "nt":
                self.assertEqual(controller.state_path.stat().st_mode & 0o777, 0o600)
            stopped = controller.stop(timeout_seconds=3)
            self.assertTrue(stopped["stopped"])

    def test_service_refuses_identity_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            state_dir = workspace / ".w1nexus"
            state_dir.mkdir(parents=True)
            controller = NativeServiceController(workspace)
            state = controller.start(command=[sys.executable, "-c", "import time; time.sleep(20)"], port=19002)
            payload = json.loads(controller.state_path.read_text())
            payload["process_marker"] = "forged-marker"
            controller.state_path.write_text(json.dumps(payload))
            with self.assertRaisesRegex(Exception, "identity_mismatch"):
                controller.stop(timeout_seconds=0.2)
            os.kill(state.pid, 15)
            controller._owned_processes[state.pid].wait(timeout=2)

    def test_source_windows_release_candidate_contract(self) -> None:
        root = Path(__file__).resolve().parents[1]
        result = verify_windows_release_candidate(root)
        self.assertTrue(result["passed"], result)
        self.assertTrue(result["checks"]["brand_assets_production_ready"])
        self.assertTrue(result["checks"]["checked_in_sources_match_generator"])
        self.assertFalse(result["claims"]["windows_exe_compiled_here"])
        self.assertFalse(result["claims"]["authenticode_signed_here"])

    def test_native_packaging_benchmark(self) -> None:
        result = run_native_packaging_benchmark()
        self.assertTrue(result["passed"], result)
        self.assertGreaterEqual(result["metrics"]["probe_count"], 14)
        self.assertFalse(result["metrics"]["native_installer_compiled_in_benchmark"])


if __name__ == "__main__":
    unittest.main()
