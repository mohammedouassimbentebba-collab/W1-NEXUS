from __future__ import annotations

import tempfile
from pathlib import Path

from w1cip.action_runtime import ActionRuntime
from w1cip.artifact_studio import ArtifactStudioStore, WorkspaceFileService
from w1cip.cli_support import action_policy, initialize_workspace


def main() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary) / "project"
        root.mkdir()
        paths = initialize_workspace(root)
        (root / "report.md").write_text("# Draft\n", encoding="utf-8")

        files = WorkspaceFileService(root)
        with ArtifactStudioStore(paths.artifact_studio_db, root) as studio:
            first = studio.create_draft(
                artifact_id="reviewed-report",
                path="report.md",
                title="Reviewed report",
                created_by="author-agent",
            )
            second = studio.save_version(
                "reviewed-report",
                content="# Verified report\n",
                created_by="author-agent",
                expected_parent_hash=first.content_hash,
            )
            studio.review(
                "reviewed-report",
                version=second.version,
                reviewer="independent-reviewer",
                outcome="approved",
                rationale="Content and project base verified.",
            )
            with ActionRuntime(root, state_dir=paths.state_dir, policy=action_policy(paths)) as runtime:
                publication = studio.publish(
                    "reviewed-report",
                    published_by="human-owner",
                    action_runtime=runtime,
                    issue_action_approval=True,
                )
            integrity = studio.verify()

        print(publication)
        print({"integrity_valid": integrity["valid"]})
        print((root / "report.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
