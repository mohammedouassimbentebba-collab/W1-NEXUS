from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import shutil
import sys
import tempfile
import unittest
from unittest import mock
from types import SimpleNamespace
from pathlib import Path

from w1cip.cli import CLI_VERSION, main as cli_main
from w1cip.credential_broker import MemoryCredentialVault

ROOT = Path(__file__).resolve().parents[1]


class CLITests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name)
        self.env = os.environ.copy()
        self.env["PYTHONPATH"] = str(ROOT / "src")
        self.env["NO_COLOR"] = "1"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_cli(self, *arguments: str, expected: int = 0) -> SimpleNamespace:
        stdout = io.StringIO()
        stderr = io.StringIO()
        command = [
            "--workspace",
            str(self.workspace),
            "--json",
            "--no-color",
            *arguments,
        ]
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            returncode = cli_main(command)
        result = SimpleNamespace(returncode=returncode, stdout=stdout.getvalue(), stderr=stderr.getvalue())
        if result.returncode != expected:
            self.fail(
                f"CLI return code {result.returncode}, expected {expected}\n"
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
        return result

    def test_module_entrypoint_is_executable(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "w1cip", "--version"],
            cwd=ROOT,
            env=self.env,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn(f"w1 {CLI_VERSION}", result.stdout)

    def test_init_and_doctor_emit_machine_readable_json(self) -> None:
        initialized = json.loads(self.run_cli("init").stdout)
        self.assertEqual(str(self.workspace.resolve()), initialized["workspace"])
        self.assertTrue((self.workspace / ".w1nexus" / "workspace.json").is_file())
        report = json.loads(self.run_cli("doctor").stdout)
        self.assertTrue(report["ok"])
        self.assertGreaterEqual(report["warning_count"], 1)
        names = {check["name"] for check in report["checks"]}
        self.assertIn("provider_models_pinned", names)
        self.assertIn("schemas", names)
        self.assertIn("runtime_authority_configured", names)

    def test_accounts_and_credentials_cli_are_redaction_safe(self) -> None:
        self.run_cli("init")
        provider_file = self.workspace / "oauth-provider.json"
        provider_file.write_text(json.dumps({
            "provider_id": "example",
            "issuer": "https://auth.example.test",
            "authorization_endpoint": "https://auth.example.test/authorize",
            "token_endpoint": "https://auth.example.test/token",
            "device_authorization_endpoint": "https://auth.example.test/device",
            "default_client_id": "w1-client",
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"]
        }), encoding="utf-8")
        added = json.loads(self.run_cli("accounts", "providers", "add", "--file", str(provider_file)).stdout)
        self.assertEqual("example", added["provider_id"])
        listed = json.loads(self.run_cli("accounts", "providers", "list").stdout)
        self.assertEqual(1, listed["count"])
        benchmark = json.loads(self.run_cli("accounts", "benchmark").stdout)
        self.assertTrue(benchmark["passed"])

        vault = MemoryCredentialVault()
        secret = "cli-secret-must-not-hit-sqlite"
        with mock.patch("w1cip.cli.create_native_credential_vault", return_value=vault), mock.patch("sys.stdin", io.StringIO(secret + "\n")):
            stored = json.loads(self.run_cli("credentials", "set", "example-key", "--provider", "example", "--stdin").stdout)
        self.assertEqual("w1-credential:example-key", stored["credential_reference"])
        credentials = json.loads(self.run_cli("credentials", "list").stdout)
        self.assertEqual(1, credentials["count"])
        self.assertNotIn(secret, json.dumps(credentials))
        db = self.workspace / ".w1nexus" / "credential-broker.sqlite3"
        raw = db.read_bytes()
        wal = Path(str(db) + "-wal")
        if wal.exists():
            raw += wal.read_bytes()
        self.assertNotIn(secret.encode(), raw)

    def test_offline_demo_is_complete_integrity_checked_and_idempotent(self) -> None:
        self.run_cli("init")
        first = json.loads(self.run_cli("demo", "--reset", "--events", "silent").stdout)
        self.assertEqual("completed", first["status"])
        self.assertTrue(first["audit"]["session_integrity"])
        self.assertEqual("candidate-b", first["final_output"]["selected_option"])
        event_count = first["audit"]["event_count"]
        call_count = first["audit"]["started_calls"]
        self.assertGreater(event_count, 2)
        self.assertEqual(8, call_count)
        self.assertTrue(any(item.get("reason") == "quota_exhausted" for item in first["disclosures"]))
        self.assertTrue(
            any(item.get("additional_review_required") is True for item in first["disclosures"])
        )

        second = json.loads(self.run_cli("demo", "--events", "silent").stdout)
        self.assertEqual(event_count, second["audit"]["event_count"])
        self.assertEqual(call_count, second["audit"]["started_calls"])

        verified = json.loads(
            self.run_cli("sessions", "verify", "session-w1-reference-demo-001").stdout
        )
        self.assertTrue(verified["valid"])

    def test_audit_and_run_export_create_reports(self) -> None:
        self.run_cli("init")
        self.run_cli("demo", "--reset", "--events", "silent")
        audit = json.loads(
            self.run_cli(
                "audit",
                "session-w1-reference-demo-001",
                "--format",
                "markdown",
            ).stdout
        )
        audit_path = Path(audit["output"])
        self.assertTrue(audit_path.is_file())
        self.assertIn("Session Audit", audit_path.read_text(encoding="utf-8"))

        exported = json.loads(
            self.run_cli(
                "export",
                "run-w1-reference-demo-001",
                "--format",
                "json",
            ).stdout
        )
        export_path = Path(exported["output"])
        self.assertTrue(export_path.is_file())
        payload = json.loads(export_path.read_text(encoding="utf-8"))
        self.assertEqual("completed", payload["status"])

    def test_plan_mode_compiles_without_initializing_or_invoking_providers(self) -> None:
        goal = ROOT / "examples" / "goal-contract" / "valid" / "pump-driver-selection.json"
        team = ROOT / "examples" / "team-plan" / "valid" / "pump-driver-team.json"
        resources = (
            ROOT
            / "examples"
            / "execution-resource-plan"
            / "valid"
            / "heterogeneous-model-routing.json"
        )
        result = json.loads(
            self.run_cli(
                "plan",
                "--goal",
                str(goal),
                "--team",
                str(team),
                "--resources",
                str(resources),
            ).stdout
        )
        self.assertGreater(result["task_count"], 0)
        self.assertEqual(result["task_count"], len(result["tasks"]))
        self.assertFalse((self.workspace / ".w1nexus" / "session.sqlite3").exists())


    def test_sessions_append_and_rebuild_from_protocol_envelopes(self) -> None:
        self.run_cli("init")
        from w1cip.reference_demo import bootstrap_events

        document = self.workspace / "bootstrap.json"
        document.write_text(json.dumps(bootstrap_events()), encoding="utf-8")
        appended = json.loads(
            self.run_cli("sessions", "append", "--file", str(document)).stdout
        )
        self.assertEqual(2, len(appended["appended"]))
        rebuilt = json.loads(
            self.run_cli("sessions", "rebuild", "session-w1-reference-demo-001").stdout
        )
        self.assertEqual(2, rebuilt["last_sequence"])
        verified = json.loads(
            self.run_cli("sessions", "verify", "session-w1-reference-demo-001").stdout
        )
        self.assertTrue(verified["valid"])

    def test_internal_benchmark_checks_resilience_invariants(self) -> None:
        self.run_cli("init")
        result = json.loads(self.run_cli("benchmark", "--reset").stdout)
        self.assertTrue(result["ok"])
        self.assertTrue(result["checks"]["context_minimised"])
        self.assertTrue(result["checks"]["rerun_did_not_duplicate_calls"])
        self.assertIn("not a third-party product benchmark", result["scope"])

    def test_actions_cli_plans_executes_and_undoes_reversible_write(self) -> None:
        self.run_cli("init")
        request = self.workspace / "write-action.json"
        request.write_text(
            json.dumps({
                "action_id": "write-generated-file",
                "kind": "file.write",
                "parameters": {"path": "generated.txt", "content": "hello from W1"},
                "requested_by": "human-owner"
            }),
            encoding="utf-8",
        )
        plan = json.loads(self.run_cli("actions", "plan", "--request", str(request)).stdout)
        self.assertTrue(plan["reversible"])
        self.assertFalse(plan["requires_approval"])
        executed = json.loads(self.run_cli("actions", "execute", "--request", str(request)).stdout)
        self.assertEqual("completed", executed["status"])
        self.assertEqual("hello from W1", (self.workspace / "generated.txt").read_text(encoding="utf-8"))
        undone = json.loads(self.run_cli("actions", "undo", "write-generated-file").stdout)
        self.assertEqual("completed", undone["status"])
        self.assertFalse((self.workspace / "generated.txt").exists())

    def test_sensitive_action_requires_explicit_approval_and_worktree_is_isolated(self) -> None:
        self.run_cli("init")
        target = self.workspace / "delete.txt"
        target.write_text("keep", encoding="utf-8")
        request = self.workspace / "delete-action.json"
        request.write_text(
            json.dumps({
                "action_id": "delete-protected-file",
                "kind": "file.delete",
                "parameters": {"path": "delete.txt"}
            }),
            encoding="utf-8",
        )
        denied = self.run_cli("actions", "execute", "--request", str(request), expected=2)
        self.assertEqual("action_approval_required", json.loads(denied.stdout)["error_code"])
        accepted = json.loads(
            self.run_cli(
                "actions", "execute", "--request", str(request), "--approve", "--issued-by", "human-owner"
            ).stdout
        )
        self.assertEqual("completed", accepted["status"])
        self.assertFalse(target.exists())

        if shutil.which("git") is None:
            return
        subprocess.run(["git", "init", "-q"], cwd=self.workspace, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.workspace, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=self.workspace, check=True)
        (self.workspace / "README.txt").write_text("root", encoding="utf-8")
        subprocess.run(["git", "add", "README.txt"], cwd=self.workspace, check=True)
        subprocess.run(["git", "commit", "-qm", "initial"], cwd=self.workspace, check=True)
        created = json.loads(
            self.run_cli(
                "worktrees", "create", "agent-one", "--branch", "w1/agent-one", "--approve"
            ).stdout
        )
        self.assertEqual("completed", created["status"])
        listed = json.loads(self.run_cli("worktrees", "list").stdout)
        self.assertEqual("agent-one", listed["worktrees"][0]["agent_id"])

    def test_mcp_server_management_probe_and_unified_tool_call(self) -> None:
        self.run_cli("init")
        os.environ["PYTHONPATH"] = str(ROOT / "src")
        command_json = json.dumps([sys.executable, "-m", "w1cip.mcp_demo_server"])
        added = json.loads(
            self.run_cli(
                "mcp", "servers", "add", "demo",
                "--transport", "stdio",
                "--command-json", command_json,
                "--env", "PYTHONPATH=PYTHONPATH",
                "--auto-approve-tool", "echo",
            ).stdout
        )
        self.assertEqual("demo", added["added"]["server_id"])
        listed = json.loads(self.run_cli("mcp", "servers", "list").stdout)
        self.assertEqual(1, len(listed["servers"]))
        probed = json.loads(self.run_cli("mcp", "probe", "demo").stdout)
        self.assertEqual(2, probed["tool_count"])
        self.assertEqual(1, probed["resource_count"])
        self.assertEqual(1, probed["prompt_count"])

        arguments = self.workspace / "echo-args.json"
        arguments.write_text(json.dumps({"text": "hello MCP"}), encoding="utf-8")
        tools = json.loads(self.run_cli("tools", "list", "--server", "demo").stdout)
        names = {item["name"] for item in tools["tools"]}
        self.assertIn("w1.workspace.read_text", names)
        self.assertIn("mcp.demo.echo", names)
        called = json.loads(
            self.run_cli(
                "tools", "call",
                "--call-id", "echo-cli-one",
                "--name", "mcp.demo.echo",
                "--arguments", str(arguments),
                "--server", "demo",
            ).stdout
        )
        self.assertEqual("completed", called["status"])
        self.assertEqual("hello MCP", called["structured_content"]["text"])

        resources = json.loads(self.run_cli("resources", "list", "--server", "demo").stdout)
        self.assertEqual("w1-demo://status", resources["resources"][0]["uri"])
        prompt_args = self.workspace / "prompt-args.json"
        prompt_args.write_text(json.dumps({"subject": "the patch"}), encoding="utf-8")
        prompt = json.loads(
            self.run_cli(
                "prompts", "get", "--server", "demo", "--name", "review",
                "--arguments", str(prompt_args),
            ).stdout
        )
        self.assertIn("Review the patch", prompt["messages"][0]["content"]["text"])

    def test_remote_tool_requires_explicit_registry_approval(self) -> None:
        self.run_cli("init")
        os.environ["PYTHONPATH"] = str(ROOT / "src")
        command_json = json.dumps([sys.executable, "-m", "w1cip.mcp_demo_server"])
        self.run_cli(
            "mcp", "servers", "add", "demo",
            "--transport", "stdio",
            "--command-json", command_json,
            "--env", "PYTHONPATH=PYTHONPATH",
        )
        arguments = self.workspace / "sum-args.json"
        arguments.write_text(json.dumps({"a": 4, "b": 5}), encoding="utf-8")
        denied = self.run_cli(
            "tools", "call",
            "--call-id", "sum-cli-one",
            "--name", "mcp.demo.sum",
            "--arguments", str(arguments),
            "--server", "demo",
            expected=2,
        )
        self.assertEqual("tool_approval_required", json.loads(denied.stdout)["error_code"])
        approval_doc = json.loads(
            self.run_cli(
                "tools", "approve",
                "--call-id", "sum-cli-one",
                "--name", "mcp.demo.sum",
                "--arguments", str(arguments),
                "--server", "demo",
                "--issued-by", "human-owner",
            ).stdout
        )
        called = json.loads(
            self.run_cli(
                "tools", "call",
                "--call-id", "sum-cli-one",
                "--name", "mcp.demo.sum",
                "--arguments", str(arguments),
                "--server", "demo",
                "--approval", approval_doc["output"],
            ).stdout
        )
        self.assertEqual(9, called["structured_content"]["value"])

    def test_w1_can_serve_its_governed_local_tools_over_stdio(self) -> None:
        self.run_cli("init")
        os.environ["PYTHONPATH"] = str(ROOT / "src")
        from w1cip.mcp import MCPServerConfig, MCPStdioClient

        config = MCPServerConfig(
            server_id="w1-local",
            transport="stdio",
            command=(
                sys.executable, "-m", "w1cip",
                "--workspace", str(self.workspace),
                "mcp", "serve", "--stdio",
            ),
            environment={"PYTHONPATH": "PYTHONPATH"},
        )
        (self.workspace / "visible.txt").write_text("visible", encoding="utf-8")
        with MCPStdioClient(config) as client:
            names = {item["name"] for item in client.list_tools()}
            self.assertIn("w1.workspace.read_text", names)
            result = client.call_tool("w1.workspace.read_text", {"path": "visible.txt"})
            self.assertEqual("visible", result["structuredContent"]["text"])
            resources = client.list_resources()
            self.assertIn("w1://capabilities", {item["uri"] for item in resources})

    def test_scientific_loop_cli_preregisters_analyzes_reviews_and_packages(self) -> None:
        self.run_cli("init")
        plan = {
            "study_id": "cli-current-study",
            "namespace_id": "w1-local",
            "project_id": "default-project",
            "title": "CLI current study",
            "research_question": "Is candidate B lower current than candidate A?",
            "hypotheses": [{
                "hypothesis_id": "h-current",
                "statement": "Candidate B has lower current.",
                "null_statement": "Candidate B does not have lower current.",
                "predictions": [{"prediction_id": "p-current", "metric": "peak-current", "direction": "negative"}],
            }],
            "experiment": {
                "design": "controlled-benchmark",
                "sample_size_min": 6,
                "observation_schema": {"observation_id": "string", "candidate": "string", "peak-current": "number"},
                "command": [sys.executable, "-c", "print('experiment')"],
                "sandbox_profile": "trusted-local",
            },
            "analysis_plan": [{
                "analysis_id": "current-difference",
                "method": "difference_in_means",
                "value_field": "peak-current",
                "group_field": "candidate",
                "group_a": "candidate-a",
                "group_b": "candidate-b",
                "alpha": 0.05,
            }],
            "falsification_criteria": [{
                "criterion_id": "f-current",
                "hypothesis_id": "h-current",
                "analysis_id": "current-difference",
                "rule": "effect_direction_and_significance",
                "expected_direction": "positive",
                "alpha": 0.05,
            }],
            "replication_policy": {"minimum_replications": 0, "independent_required": True},
            "classification": "internal",
        }
        rows = [
            {"observation_id": "a-one", "candidate": "candidate-a", "peak-current": 20.0},
            {"observation_id": "a-two", "candidate": "candidate-a", "peak-current": 21.0},
            {"observation_id": "a-three", "candidate": "candidate-a", "peak-current": 19.5},
            {"observation_id": "b-one", "candidate": "candidate-b", "peak-current": 15.0},
            {"observation_id": "b-two", "candidate": "candidate-b", "peak-current": 14.5},
            {"observation_id": "b-three", "candidate": "candidate-b", "peak-current": 15.5},
        ]
        plan_path = self.workspace / "study-plan.json"
        rows_path = self.workspace / "observations.json"
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        rows_path.write_text(json.dumps(rows), encoding="utf-8")
        created = json.loads(self.run_cli("science", "create", "--plan", str(plan_path)).stdout)
        self.assertEqual("draft", created["status"])
        preregistered = json.loads(self.run_cli("science", "preregister", "cli-current-study").stdout)
        self.assertEqual("preregistered", preregistered["status"])
        self.run_cli("science", "observe", "cli-current-study", "--file", str(rows_path))
        analysis = json.loads(self.run_cli("science", "analyze", "cli-current-study", "--analysis-id", "current-difference").stdout)
        self.assertGreater(analysis["result"]["effect"], 0)
        evaluation = json.loads(self.run_cli("science", "evaluate", "cli-current-study").stdout)
        self.assertEqual("supported", evaluation["hypotheses"][0]["outcome"])
        review = json.loads(self.run_cli(
            "science", "review", "cli-current-study",
            "--reviewer-id", "independent-reviewer",
            "--outcome", "approved",
            "--rationale", "Preregistration, observations, and planned analysis verified.",
        ).stdout)
        self.assertEqual("approved", review["outcome"])
        package_path = self.workspace / "reports" / "cli-study.zip"
        package = json.loads(self.run_cli("science", "package", "cli-current-study", "--output", str(package_path)).stdout)
        self.assertTrue(Path(package["path"]).is_file())
        verified = json.loads(self.run_cli("science", "verify-package", "--file", str(package_path)).stdout)
        self.assertTrue(verified["ok"])
        integrity = json.loads(self.run_cli("science", "verify", "--study-id", "cli-current-study").stdout)
        self.assertTrue(integrity["ok"])

    def test_capabilities_disclose_limits_instead_of_claiming_universal_superiority(self) -> None:
        result = json.loads(self.run_cli("capabilities").stdout)
        self.assertIn("multi-provider task-specific routing", result["implemented"])
        self.assertIn(
            "benchmark evidence proving universal superiority over other products",
            result["not_yet_implemented"],
        )

    def test_workspace_snapshot_cli_exports_live_console_payload(self) -> None:
        self.run_cli("init")
        output = self.workspace / "workspace-snapshot.json"
        result = json.loads(
            self.run_cli("workspace", "snapshot", "--output", str(output)).stdout
        )
        self.assertTrue(output.is_file())
        payload = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(self.workspace.name, payload["workspace"]["name"])
        self.assertEqual(64, len(payload["snapshot_digest"]))
        self.assertEqual(str(output), result["output"])

    def test_workspace_console_rejects_non_loopback_binding(self) -> None:
        self.run_cli("init")
        result = self.run_cli(
            "workspace", "serve", "--host", "0.0.0.0", "--no-browser", expected=2
        )
        payload = json.loads(result.stdout)
        self.assertEqual("workspace_console_loopback_only", payload["error_code"])


if __name__ == "__main__":
    unittest.main()
