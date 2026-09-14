from __future__ import annotations

import json
import tempfile
from dataclasses import asdict
from pathlib import Path

from w1cip.action_runtime import ActionRequest, ActionRuntime


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="w1-action-demo-") as directory:
        root = Path(directory)
        with ActionRuntime(root) as runtime:
            write = ActionRequest(
                action_id="write-demo-file",
                kind="file.write",
                parameters={"path": "result.txt", "content": "verified result\n"},
            )
            written = runtime.execute(write)
            print(json.dumps(asdict(written), indent=2))
            assert (root / "result.txt").read_text() == "verified result\n"

            delete = ActionRequest(
                action_id="delete-demo-file",
                kind="file.delete",
                parameters={"path": "result.txt"},
            )
            plan = runtime.plan(delete)
            approval = runtime.issue_approval(plan, issued_by="human-owner")
            deleted = runtime.execute(delete, approval=approval)
            print(json.dumps(asdict(deleted), indent=2))
            assert not (root / "result.txt").exists()

            runtime.undo(delete.action_id)
            assert (root / "result.txt").read_text() == "verified result\n"
            print("Action Runtime demo passed")


if __name__ == "__main__":
    main()
