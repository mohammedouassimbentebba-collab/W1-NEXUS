"""Persist and replay the repository's bootstrap example."""

from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from w1cip.session_store import SessionStore  # noqa: E402

DATABASE = Path(__file__).with_name("demo-session.sqlite3")
BOOTSTRAP = ROOT / "examples" / "protocol-envelope" / "valid" / "session-bootstrap.json"


def main() -> None:
    envelope = json.loads(BOOTSTRAP.read_text(encoding="utf-8"))
    candidate = deepcopy(envelope)
    candidate.pop("sequence", None)

    with SessionStore(
        DATABASE,
        trusted_recorders={("runtime", "orchestrator-runtime-01")},
    ) as store:
        result = store.append(candidate, expected_last_sequence=0)
        report = store.verify_integrity(envelope["session_id"])
        print(json.dumps({
            "sequence": result.sequence,
            "event_hash": result.event_hash,
            "already_present": result.already_present,
            "integrity_valid": report.valid,
        }, indent=2))


if __name__ == "__main__":
    main()
