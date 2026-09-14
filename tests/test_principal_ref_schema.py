from __future__ import annotations

import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "w1-cip" / "0.1" / "principal-ref.schema.json"
EXAMPLES = ROOT / "examples" / "principal-ref"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class PrincipalRefSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = load_json(SCHEMA_PATH)
        Draft202012Validator.check_schema(cls.schema)
        cls.validator = Draft202012Validator(cls.schema)

    def errors(self, instance: dict) -> list:
        return sorted(self.validator.iter_errors(instance), key=lambda error: list(error.path))

    def test_valid_examples(self) -> None:
        paths = sorted((EXAMPLES / "valid").glob("*.json"))
        self.assertTrue(paths, "no valid PrincipalRef examples found")
        for path in paths:
            with self.subTest(path=path.name):
                self.assertEqual([], self.errors(load_json(path)))

    def test_invalid_examples(self) -> None:
        paths = sorted((EXAMPLES / "invalid").glob("*.json"))
        self.assertTrue(paths, "no invalid PrincipalRef examples found")
        for path in paths:
            with self.subTest(path=path.name):
                self.assertTrue(self.errors(load_json(path)))


if __name__ == "__main__":
    unittest.main()
