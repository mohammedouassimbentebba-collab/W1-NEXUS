from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from w1cip.model_access import ModelProfile, PythonPluginAdapterFactory
from w1cip.orchestrator import CompiledTask, ProviderRequest
from w1cip.plugin_system import (
    PluginIntegrityError,
    PluginManager,
    PluginManifest,
    PluginManifestError,
    PluginPermissionError,
    compatibility_report,
    run_plugin_adoption_benchmark,
    scaffold_plugin,
)
from w1cip.sdk import PLUGIN_API_VERSION, SDK_VERSION


class PluginSystemTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.source = self.root / "plugin"
        scaffold_plugin(self.source, plugin_id="org.example.echo", name="Echo")
        self.manager = PluginManager(self.workspace)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_manifest_and_compatibility_contract(self) -> None:
        manifest = PluginManifest.load(self.source)
        self.assertEqual(manifest.api_version, PLUGIN_API_VERSION)
        self.assertTrue(compatibility_report(manifest)["compatible"])
        self.assertEqual(SDK_VERSION, "1.2.0")

    def test_unknown_manifest_fields_fail_closed(self) -> None:
        payload = json.loads((self.source / "w1-plugin.json").read_text())
        payload["mystery"] = True
        with self.assertRaises(PluginManifestError):
            PluginManifest.from_mapping(payload)

    def test_install_copies_and_integrity_locks_source(self) -> None:
        record = self.manager.install(self.source, enable=True)
        self.assertNotEqual(Path(record.path), self.source)
        self.assertTrue(self.manager.verify(record.manifest.plugin_id)["verified"])
        (self.source / "plugin.py").write_text("# source changed\n")
        self.assertTrue(self.manager.verify(record.manifest.plugin_id)["verified"])

    def test_installed_tamper_is_denied_before_execution(self) -> None:
        record = self.manager.install(self.source, enable=True)
        target = Path(record.path) / "plugin.py"
        target.write_text(target.read_text() + "\n# tampered\n")
        with self.assertRaises(PluginIntegrityError):
            self.manager.run(record.manifest.plugin_id, operation="health")

    def test_permission_grant_required_to_enable(self) -> None:
        payload = json.loads((self.source / "w1-plugin.json").read_text())
        payload["permissions"] = ["workspace.write"]
        (self.source / "w1-plugin.json").write_text(json.dumps(payload))
        with self.assertRaises(PluginPermissionError):
            self.manager.install(self.source, enable=True)
        record = self.manager.install(self.source, granted_permissions=["workspace.write"], enable=True)
        self.assertTrue(record.enabled)

    def test_revoking_required_permission_auto_disables(self) -> None:
        payload = json.loads((self.source / "w1-plugin.json").read_text())
        payload["permissions"] = ["workspace.read"]
        (self.source / "w1-plugin.json").write_text(json.dumps(payload))
        record = self.manager.install(self.source, granted_permissions=["workspace.read"], enable=True)
        changed = self.manager.revoke(record.manifest.plugin_id, ["workspace.read"])
        self.assertFalse(changed.enabled)

    def test_conformance_suite_passes_scaffold(self) -> None:
        result = self.manager.conformance(self.source)
        self.assertTrue(result["passed"])
        self.assertTrue(result["probes"]["provider_contract"])

    def test_managed_plugin_integrates_with_model_access_factory(self) -> None:
        record = self.manager.install(self.source, enable=True)
        profile = ModelProfile(
            model_id="plugin-model", display_name="Plugin", provider_id="plugin",
            connector_type="custom", connector_resource_id="plugin-resource", model_name="echo",
            access_mode="custom_plugin", privacy_mode="local", capabilities={"executor": 1.0},
            plugin_factory=f"w1-plugin:{record.manifest.plugin_id}",
        )
        adapter = PythonPluginAdapterFactory(self.manager).build(profile)
        response = adapter.invoke(ProviderRequest(
            run_id="r", session_id="s",
            task=CompiledTask(task_id="t", title="T", phase="execution", role="executor", expected_output_type="contribution", required_context_fields=("input",), domains=()),
            resource_id="plugin-resource", idempotency_key="k", context={"input": "safe"}, prior_outputs={}, attempt=1,
        ))
        self.assertEqual(response.payload["message"], "Hello from a governed W1 plugin")

    def test_benchmark(self) -> None:
        self.assertTrue(run_plugin_adoption_benchmark()["passed"])


if __name__ == "__main__":
    unittest.main()
