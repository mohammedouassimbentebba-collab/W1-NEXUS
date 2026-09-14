from __future__ import annotations

import tempfile
from pathlib import Path

from w1cip.action_runtime import ActionPolicy, ActionRuntime
from w1cip.universal_artifacts import UniversalArtifactStore, reference_artifact_models


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="w1-office-demo-") as directory:
        workspace = Path(directory)
        state = workspace / ".w1nexus"
        store = UniversalArtifactStore(state / "office-artifacts.sqlite3", workspace)
        try:
            created = store.create(reference_artifact_models()["document"], created_by="author-agent")
            revised = store.patch(
                created.artifact_id,
                [{
                    "op": "add",
                    "path": "/content/blocks/-",
                    "value": {"type": "paragraph", "text": "Independently reviewed revision."},
                }],
                created_by="author-agent",
                expected_parent_hash=created.model_hash,
            )
            store.review(
                created.artifact_id,
                version=revised.version,
                reviewer="review-agent",
                outcome="approved",
                rationale="Schema, content, and provenance verified.",
            )
            with ActionRuntime(
                workspace,
                state_dir=state,
                policy=ActionPolicy(require_approval_for_writes=True),
            ) as runtime:
                exported = store.export(
                    created.artifact_id,
                    format="docx",
                    output_path="exports/reviewed-document.docx",
                    exported_by="owner",
                    action_runtime=runtime,
                    issue_action_approval=True,
                )
            print(exported.as_dict())
            print(store.verify())
        finally:
            store.close()


if __name__ == "__main__":
    main()
