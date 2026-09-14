from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from w1cip.action_runtime import (
    ActionApprovalInvalid,
    ActionApprovalRequired,
    ActionPolicyDenied,
    ActionRequest,
    ActionRuntime,
)
from w1cip.computer_use import (
    ComputerElementChanged,
    ComputerInputDenied,
    EphemeralInputError,
    VirtualComputerDriver,
    detect_computer_backend,
    resolve_unique_element,
    run_computer_use_benchmark,
)
from w1cip.evaluation import run_governed_computer_use_benchmark


class ComputerUseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.driver = VirtualComputerDriver(width=800, height=600)
        self.runtime = ActionRuntime(self.root, computer_driver=self.driver)

    def tearDown(self) -> None:
        self.runtime.close()
        self.temp.cleanup()

    def test_observation_is_read_only_and_does_not_require_approval(self) -> None:
        request = ActionRequest(action_id="observe-desktop", kind="computer.observe", parameters={})
        plan = self.runtime.plan(request)
        self.assertEqual("read_only", plan.risk)
        self.assertFalse(plan.requires_approval)
        result = self.runtime.execute(request)
        self.assertEqual("completed", result.status)
        self.assertEqual(800, result.metadata["screen_width"])
        self.assertEqual(600, result.metadata["screen_height"])

    def test_pointer_click_requires_digest_bound_approval(self) -> None:
        request = ActionRequest(
            action_id="click-target",
            kind="computer.pointer.click",
            parameters={"x": 200, "y": 150, "button": "left", "clicks": 1},
        )
        with self.assertRaises(ActionApprovalRequired):
            self.runtime.execute(request)
        approval = self.runtime.issue_approval(self.runtime.plan(request), issued_by="human-owner", ttl_seconds=60)
        result = self.runtime.execute(request, approval=approval)
        self.assertEqual("completed", result.status)
        self.assertEqual("click", self.driver.events[-1]["kind"])

        changed = ActionRequest(
            action_id="click-other-target",
            kind="computer.pointer.click",
            parameters={"x": 201, "y": 150, "button": "left", "clicks": 1},
        )
        with self.assertRaises(ActionApprovalInvalid):
            self.runtime.execute(changed, approval=approval)

    def test_raw_key_typing_remains_denied_outside_ephemeral_surface(self) -> None:
        request = ActionRequest(
            action_id="type-letter",
            kind="computer.key.press",
            parameters={"key": "a"},
        )
        with self.assertRaises(ActionPolicyDenied):
            self.runtime.plan(request)
        with self.assertRaises(ComputerInputDenied):
            self.driver.press_key("a")

    def test_bounds_are_enforced(self) -> None:
        request = ActionRequest(
            action_id="too-many-clicks",
            kind="computer.pointer.click",
            parameters={"x": 1, "y": 1, "clicks": 4},
        )
        with self.assertRaises(ActionPolicyDenied):
            self.runtime.plan(request)
        with self.assertRaises(ComputerInputDenied):
            self.driver.move_pointer(900, 10)


    def test_screenshot_requires_approval_and_persists_only_capture_reference(self) -> None:
        request = ActionRequest(action_id="screen-shot", kind="computer.screenshot", parameters={})
        with self.assertRaises(ActionApprovalRequired):
            self.runtime.execute(request)
        approval = self.runtime.issue_approval(self.runtime.plan(request), issued_by="human-owner", ttl_seconds=60)
        result = self.runtime.execute(request, approval=approval)
        capture = Path(result.metadata["capture_path"])
        self.assertTrue(capture.is_file())
        self.assertTrue(capture.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertNotIn("png_bytes", result.metadata)

    def test_ui_elements_are_ephemeral_and_selector_fingerprint_is_enforced(self) -> None:
        request = ActionRequest(
            action_id="inspect-elements",
            kind="computer.elements.list",
            parameters={"selector": {"window_title": "W1 Virtual App"}},
        )
        approval = self.runtime.issue_approval(self.runtime.plan(request), issued_by="human-owner", ttl_seconds=60)
        result = self.runtime.execute(request, approval=approval)
        self.assertGreaterEqual(result.metadata["element_count"], 3)
        submit = next(item for item in result.metadata["elements"] if item["name"] == "Submit")
        persisted = self.runtime.get_result("inspect-elements")
        self.assertIsNotNone(persisted)
        self.assertNotIn("elements", persisted.metadata)
        self.assertFalse(persisted.metadata["elements_persisted"])

        click = ActionRequest(
            action_id="selector-click",
            kind="computer.element.click",
            parameters={
                "selector": {"role": "button", "name": "Submit"},
                "expected_fingerprint": submit["fingerprint"],
            },
        )
        click_approval = self.runtime.issue_approval(self.runtime.plan(click), issued_by="human-owner", ttl_seconds=60)
        click_result = self.runtime.execute(click, approval=click_approval)
        self.assertEqual("completed", click_result.status)
        self.assertEqual("click", self.driver.events[-1]["kind"])

        stale = ActionRequest(
            action_id="selector-click-stale",
            kind="computer.element.click",
            parameters={
                "selector": {"role": "button", "name": "Submit"},
                "expected_fingerprint": "0" * 64,
            },
        )
        stale_approval = self.runtime.issue_approval(self.runtime.plan(stale), issued_by="human-owner", ttl_seconds=60)
        with self.assertRaises(ComputerElementChanged):
            self.runtime.execute(stale, approval=stale_approval)

    def test_ephemeral_text_never_enters_action_journal(self) -> None:
        target = resolve_unique_element(self.driver, {"role": "textbox", "name": "Name"})
        secret_text = "Correct-Horse-Demo-Only"
        ref = self.runtime.register_ephemeral_input(secret_text, ttl_seconds=60)
        request = ActionRequest(
            action_id="secure-type",
            kind="computer.element.type",
            parameters={
                "selector": {"role": "textbox", "name": "Name"},
                "expected_fingerprint": target.fingerprint,
                **ref.as_request_parameters(),
            },
        )
        approval = self.runtime.issue_approval(self.runtime.plan(request), issued_by="human-owner", ttl_seconds=60)
        result = self.runtime.execute(request, approval=approval)
        self.assertEqual("completed", result.status)
        self.assertFalse(result.metadata["text_persisted"])
        self.assertEqual("type_text", self.driver.events[-1]["kind"])
        self.assertNotIn("text", self.driver.events[-1])
        rows = self.runtime._connection.execute("SELECT request_json, result_json FROM actions").fetchall()
        journal_text = "\n".join((row["request_json"] or "") + (row["result_json"] or "") for row in rows)
        self.assertNotIn(secret_text, journal_text)

        retry = ActionRequest(
            action_id="secure-type-reuse",
            kind="computer.element.type",
            parameters={
                "selector": {"role": "textbox", "name": "Name"},
                "expected_fingerprint": target.fingerprint,
                **ref.as_request_parameters(),
            },
        )
        retry_approval = self.runtime.issue_approval(self.runtime.plan(retry), issued_by="human-owner", ttl_seconds=60)
        with self.assertRaises(EphemeralInputError):
            self.runtime.execute(retry, approval=retry_approval)

    def test_governed_file_chooser_uses_workspace_file_and_selector(self) -> None:
        sample = self.root / "sample.txt"
        sample.write_text("demo")
        target = resolve_unique_element(self.driver, {"role": "textbox", "name": "Name"})
        request = ActionRequest(
            action_id="choose-file",
            kind="computer.file.choose",
            parameters={
                "selector": {"role": "textbox", "name": "Name"},
                "expected_fingerprint": target.fingerprint,
                "path": "sample.txt",
                "submit": True,
            },
        )
        approval = self.runtime.issue_approval(self.runtime.plan(request), issued_by="human-owner", ttl_seconds=60)
        result = self.runtime.execute(request, approval=approval)
        self.assertEqual("sample.txt", result.metadata["workspace_path"])
        self.assertEqual("type_text", self.driver.events[-2]["kind"])
        self.assertEqual({"kind": "key", "key": "enter"}, self.driver.events[-1])

    def test_virtual_driver_benchmark(self) -> None:
        result = run_computer_use_benchmark()
        self.assertTrue(result["passed"])
        self.assertTrue(result["probes"]["free_text_key_denied"])

    def test_governed_benchmark_and_detection(self) -> None:
        result = run_governed_computer_use_benchmark()
        self.assertTrue(result["passed"])
        status = detect_computer_backend()
        self.assertIn("backend", status)
        self.assertIn("available", status)


if __name__ == "__main__":
    unittest.main()
