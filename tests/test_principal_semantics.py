from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from w1cip.validation import get_effective_principal  # noqa: E402

EXAMPLES = ROOT / "examples" / "protocol-envelope" / "valid"


def load_json(name: str) -> dict:
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


class PrincipalSemanticsTests(unittest.TestCase):
    def test_actor_is_effective_without_representation(self) -> None:
        command = load_json("command-create.json")
        self.assertEqual(
            ("agent", "agent-electronics-01"),
            get_effective_principal(command),
        )

    def test_on_behalf_of_is_effective_when_present(self) -> None:
        event = load_json("protocol-event-next-version.json")
        self.assertEqual(
            ("agent", "agent-electronics-01"),
            get_effective_principal(event),
        )


if __name__ == "__main__":
    unittest.main()
