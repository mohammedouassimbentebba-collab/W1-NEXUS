from __future__ import annotations

import json
import tempfile
from pathlib import Path

from w1cip.memory import (
    MemoryDraft,
    MemoryPrincipal,
    MemoryProvenance,
    MemoryQuery,
    MemoryStore,
)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="w1-memory-demo-") as directory:
        owner = MemoryPrincipal("human", "demo-owner", groups=("demo-team",))
        with MemoryStore(Path(directory) / "memory.sqlite3") as store:
            store.add(
                MemoryDraft(
                    memory_id="mem-demo-voltage",
                    namespace_id="w1-demo",
                    project_id="pump-project",
                    kind="fact",
                    subject="pump prototype",
                    predicate="supply-voltage",
                    value=12,
                    text="The demonstration pump supply voltage is 12 V.",
                    summary="Supply voltage: 12 V.",
                    confidence=0.99,
                    sensitivity="internal",
                    visibility="team",
                    team_id="demo-team",
                    tags=("pump", "electronics"),
                    provenance=MemoryProvenance(
                        "user_statement",
                        owner,
                        source_ref="demo://user-input",
                    ),
                ),
                actor=owner,
            )
            results = store.search(
                MemoryQuery(
                    namespace_id="w1-demo",
                    project_id="pump-project",
                    text="pump voltage",
                ),
                actor=owner,
            )
            bundle = store.build_context_bundle(
                MemoryQuery(
                    namespace_id="w1-demo",
                    project_id="pump-project",
                    text="pump voltage",
                ),
                actor=owner,
                token_budget=128,
            )
            print(
                json.dumps(
                    {
                        "result_count": len(results),
                        "top_citation": results[0].record.citation,
                        "context": bundle.as_prompt_context(),
                        "integrity": store.verify_integrity(),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )


if __name__ == "__main__":
    main()
