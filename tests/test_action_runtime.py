from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

from w1cip.action_runtime import (
    ActionApprovalInvalid,
    ActionApprovalRequired,
    ActionPolicy,
    ActionPolicyDenied,
    ActionRequest,
    ActionRuntime,
)


class ActionRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        (self.root / ".w1nexus").mkdir()
        self.runtime = ActionRuntime(self.root)

    def tearDown(self) -> None:
        self.runtime.close()
        self.temp.cleanup()

    def request(self, action_id: str, kind: str, **parameters: object) -> ActionRequest:
        return ActionRequest(action_id=action_id, kind=kind, parameters=parameters)

    def approve(self, request: ActionRequest):
        return self.runtime.issue_approval(
            self.runtime.plan(request), issued_by="human-owner", ttl_seconds=60
        )

    def test_atomic_write_is_idempotent_and_reversible(self) -> None:
        path = self.root / "notes.txt"
        path.write_text("before", encoding="utf-8")
        request = self.request("write-notes", "file.write", path="notes.txt", content="after")
        result = self.runtime.execute(request)
        self.assertEqual("completed", result.status)
        self.assertEqual("after", path.read_text(encoding="utf-8"))
        repeated = self.runtime.execute(request)
        self.assertEqual(asdict(result), asdict(repeated))
        undone = self.runtime.undo("write-notes")
        self.assertEqual("before", path.read_text(encoding="utf-8"))
        self.assertIn("notes.txt", undone.changed_paths)

    def test_new_file_is_removed_by_undo(self) -> None:
        path = self.root / "created.txt"
        request = self.request("create-file", "file.write", path="created.txt", content="hello")
        self.runtime.execute(request)
        self.assertTrue(path.exists())
        self.runtime.undo("create-file")
        self.assertFalse(path.exists())

    def test_delete_requires_one_time_digest_bound_approval(self) -> None:
        target = self.root / "delete-me.txt"
        target.write_text("important", encoding="utf-8")
        request = self.request("delete-file", "file.delete", path="delete-me.txt")
        with self.assertRaises(ActionApprovalRequired):
            self.runtime.execute(request)
        approval = self.approve(request)
        result = self.runtime.execute(request, approval=approval)
        self.assertEqual("completed", result.status)
        self.assertFalse(target.exists())
        self.runtime.undo("delete-file")
        self.assertEqual("important", target.read_text(encoding="utf-8"))

        other = self.root / "other.txt"
        other.write_text("x", encoding="utf-8")
        other_request = self.request("delete-other", "file.delete", path="other.txt")
        with self.assertRaises(ActionApprovalInvalid):
            self.runtime.execute(other_request, approval=approval)

    def test_path_escape_and_protected_paths_are_denied(self) -> None:
        with self.assertRaises(ActionPolicyDenied):
            self.runtime.plan(self.request("escape", "file.write", path="../escape.txt", content="x"))
        with self.assertRaises(ActionPolicyDenied):
            self.runtime.plan(self.request("secret", "file.write", path=".w1nexus/action-secret.key", content="x"))

    def test_symlink_escape_is_denied(self) -> None:
        external = Path(self.temp.name).parent / (Path(self.temp.name).name + "-outside")
        external.mkdir(exist_ok=True)
        link = self.root / "outside-link"
        created = False
        is_junction = False
        try:
            if hasattr(os, "symlink"):
                try:
                    os.symlink(external, link, target_is_directory=True)
                    created = True
                except OSError:
                    # Windows unprivileged accounts without Developer Mode cannot create symlinks
                    pass
            if not created and os.name == "nt":
                try:
                    import _winapi

                    _winapi.CreateJunction(str(external), str(link))
                    created = True
                    is_junction = True
                except (ImportError, OSError):
                    pass
            if not created:
                self.skipTest("symlink and junction creation unavailable in this environment")

            with self.assertRaises(ActionPolicyDenied):
                self.runtime.plan(
                    self.request("symlink-escape", "file.write", path="outside-link/x.txt", content="x")
                )
        finally:
            if link.exists() or (link.is_symlink() if hasattr(link, "is_symlink") else False):
                if is_junction:
                    try:
                        link.rmdir()
                    except OSError:
                        link.unlink(missing_ok=True)
                else:
                    link.unlink(missing_ok=True)
            shutil.rmtree(external, ignore_errors=True)

    def test_commands_use_direct_argv_strip_secrets_and_record_failure(self) -> None:
        os.environ["SECRET_TOKEN"] = "must-not-leak"
        request = self.request(
            "check-env",
            "command.run",
            argv=[
                sys.executable,
                "-c",
                "print(__import__('os').environ.get('SECRET_TOKEN','missing'))",
            ],
            cwd=".",
        )
        approval = self.approve(request)
        result = self.runtime.execute(request, approval=approval)
        self.assertEqual("completed", result.status)
        self.assertEqual("missing", result.stdout.strip())
        self.assertNotIn("must-not-leak", json.dumps(asdict(result)))

        denied = self.request("shell", "command.run", argv=["bash", "-lc", "echo x"], cwd=".")
        with self.assertRaises(ActionPolicyDenied):
            self.runtime.plan(denied)

    def test_command_timeout_kills_process(self) -> None:
        policy = ActionPolicy(max_command_seconds=1)
        self.runtime.close()
        self.runtime = ActionRuntime(self.root, policy=policy)
        request = self.request(
            "slow-command",
            "command.run",
            argv=[sys.executable, "-c", "__import__('time').sleep(5)"],
            cwd=".",
            timeout_seconds=1,
        )
        result = self.runtime.execute(request, approval=self.approve(request))
        self.assertEqual("failed", result.status)
        self.assertEqual("action_command_timed_out", result.error_code)

    def test_windows_executable_identity_allowlist_and_denylist_rules(self) -> None:
        # python.exe and python3.exe are canonically allowed
        for py_exe in ["python.exe", "python3.exe", "pytest.exe"]:
            slug = py_exe.replace(".", "-")
            req = self.request(f"plan-{slug}", "command.run", argv=[py_exe, "--version"], cwd=".")
            plan = self.runtime.plan(req)
            self.assertIsNotNone(plan)

        # non-allowlisted .exe is strictly denied
        for evil in ["evil.exe", "notepad.exe", "unknown-tool.exe"]:
            slug = evil.replace(".", "-")
            req = self.request(f"plan-{slug}", "command.run", argv=[evil, "arg"], cwd=".")
            with self.assertRaises(ActionPolicyDenied) as ctx:
                self.runtime.plan(req)
            self.assertIn("Command not allowlisted", str(ctx.exception))

        # denied executables with or without .exe are strictly denied
        for denied in ["bash", "bash.exe", "cmd", "cmd.exe", "powershell", "powershell.exe", "curl", "curl.exe", "ssh.exe"]:
            slug = denied.replace(".", "-")
            req = self.request(f"plan-{slug}", "command.run", argv=[denied, "-c", "dir"], cwd=".")
            with self.assertRaises(ActionPolicyDenied) as ctx:
                self.runtime.plan(req)
            self.assertIn("Command denied", str(ctx.exception))

    def test_command_is_read_only_with_exe_suffix(self) -> None:
        # python.exe -V is read-only and does not require human approval
        req = self.request("py-version", "command.run", argv=["python.exe", "-V"], cwd=".")
        plan = self.runtime.plan(req)
        self.assertFalse(plan.requires_approval)
        self.assertEqual("read_only", plan.risk)

    def test_read_only_git_status_does_not_require_approval(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git unavailable")
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        request = self.request("git-status", "command.run", argv=["git", "status", "--short"], cwd=".")
        plan = self.runtime.plan(request)
        self.assertFalse(plan.requires_approval)
        result = self.runtime.execute(request)
        self.assertEqual(0, result.exit_code)

    def test_git_worktree_lifecycle_and_undo(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git unavailable")
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=self.root, check=True)
        (self.root / "README.txt").write_text("root", encoding="utf-8")
        subprocess.run(["git", "add", "README.txt"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", "initial"], cwd=self.root, check=True)

        request = self.request(
            "worktree-create-agent-one",
            "git.worktree.create",
            agent_id="agent-one",
            branch="w1/agent-one",
            ref="HEAD",
        )
        result = self.runtime.execute(request, approval=self.approve(request))
        self.assertEqual("completed", result.status)
        worktrees = self.runtime.list_worktrees()
        self.assertEqual("agent-one", worktrees[0]["agent_id"])
        path = Path(worktrees[0]["path"])
        self.assertTrue((path / "README.txt").is_file())
        self.runtime.undo(request.action_id)
        self.assertFalse(path.exists())
        self.assertEqual([], self.runtime.list_worktrees())


if __name__ == "__main__":
    unittest.main()
