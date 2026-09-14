from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from w1cip.validation import (  # noqa: E402
    SessionValidationState,
    validate_protocol_envelope_session_semantics,
)

EXAMPLES = ROOT / "examples" / "protocol-envelope" / "valid"


def load_json(name: str) -> dict:
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


class SessionBootstrapSemanticTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bootstrap = load_json("session-bootstrap.json")
        self.command = load_json("command-create.json")
        self.security_event = load_json("security-event.json")

    def test_valid_first_bootstrap(self) -> None:
        state = SessionValidationState(
            trusted_recorders=frozenset({("runtime", "orchestrator-runtime-01")})
        )
        self.assertEqual(
            [], validate_protocol_envelope_session_semantics(self.bootstrap, state)
        )

    def test_bootstrap_requires_trusted_recorder(self) -> None:
        state = SessionValidationState()
        self.assertEqual(
            ["bootstrap_recorder_untrusted"],
            validate_protocol_envelope_session_semantics(self.bootstrap, state),
        )

    def test_bootstrap_must_be_first_event(self) -> None:
        state = SessionValidationState(
            event_count=1,
            trusted_recorders=frozenset({("runtime", "orchestrator-runtime-01")}),
        )
        self.assertEqual(
            ["bootstrap_not_first_event"],
            validate_protocol_envelope_session_semantics(self.bootstrap, state),
        )

    def test_bootstrap_cannot_repeat(self) -> None:
        state = SessionValidationState(
            event_count=1,
            bootstrap_completed=True,
            used_bootstrap_ids=frozenset({"bootstrap-session-demo-001"}),
            trusted_recorders=frozenset({("runtime", "orchestrator-runtime-01")}),
        )
        self.assertEqual(
            ["bootstrap_already_completed", "bootstrap_id_reused"],
            validate_protocol_envelope_session_semantics(self.bootstrap, state),
        )

    def test_ordinary_command_rejected_before_bootstrap(self) -> None:
        self.assertEqual(
            ["session_not_bootstrapped"],
            validate_protocol_envelope_session_semantics(
                self.command, SessionValidationState()
            ),
        )


    def test_ordinary_event_requires_trusted_recorder(self) -> None:
        state = SessionValidationState(event_count=1, bootstrap_completed=True)
        self.assertEqual(
            ["event_recorder_untrusted"],
            validate_protocol_envelope_session_semantics(self.security_event, state),
        )

    def test_ordinary_messages_allowed_after_bootstrap(self) -> None:
        state = SessionValidationState(
            event_count=1,
            bootstrap_completed=True,
            trusted_recorders=frozenset({("runtime", "orchestrator-runtime-01")}),
        )
        self.assertEqual(
            [], validate_protocol_envelope_session_semantics(self.command, state)
        )
        self.assertEqual(
            [],
            validate_protocol_envelope_session_semantics(self.security_event, state),
        )


if __name__ == "__main__":
    unittest.main()
