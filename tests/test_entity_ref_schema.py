from __future__ import annotations

import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "w1-cip" / "0.1" / "entity-ref.schema.json"
REF_EXAMPLES = ROOT / "examples" / "entity-ref"
IDENTITY_EXAMPLES = ROOT / "examples" / "entity-identity"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class EntityRefSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = load_json(SCHEMA_PATH)
        Draft202012Validator.check_schema(cls.schema)
        cls.ref_validator = Draft202012Validator(cls.schema)
        registry = Registry().with_resource(
            cls.schema["$id"], Resource.from_contents(cls.schema)
        )
        cls.identity_validator = Draft202012Validator(
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "$ref": "urn:w1-cip:schema:0.1:entity-ref#entity-identity",
            },
            registry=registry,
        )

    @staticmethod
    def errors(validator: Draft202012Validator, instance: dict) -> list:
        return sorted(validator.iter_errors(instance), key=lambda error: list(error.path))

    def _assert_examples(self, directory: Path, validator: Draft202012Validator, valid: bool) -> None:
        paths = sorted(directory.glob("*.json"))
        self.assertTrue(paths, f"no examples found in {directory}")
        for path in paths:
            with self.subTest(path=path.name):
                errors = self.errors(validator, load_json(path))
                if valid:
                    self.assertEqual([], errors)
                else:
                    self.assertTrue(errors, f"{path.name} unexpectedly passed validation")

    def test_valid_entity_refs(self) -> None:
        self._assert_examples(REF_EXAMPLES / "valid", self.ref_validator, True)

    def test_invalid_entity_refs(self) -> None:
        self._assert_examples(REF_EXAMPLES / "invalid", self.ref_validator, False)

    def test_valid_entity_identities(self) -> None:
        self._assert_examples(IDENTITY_EXAMPLES / "valid", self.identity_validator, True)

    def test_invalid_entity_identities(self) -> None:
        self._assert_examples(IDENTITY_EXAMPLES / "invalid", self.identity_validator, False)

    def test_protocol_envelope_uses_standalone_entity_ref(self) -> None:
        protocol = load_json(
            ROOT / "schemas" / "w1-cip" / "0.1" / "protocol-envelope.schema.json"
        )
        self.assertEqual(
            "urn:w1-cip:schema:0.1:entity-ref",
            protocol["properties"]["entity"]["$ref"],
        )
        self.assertEqual(
            "urn:w1-cip:schema:0.1:entity-ref#entity-identity",
            protocol["$defs"]["createPrecondition"]["properties"]["target_identity"]["$ref"],
        )
        self.assertNotIn("entityRefPreview", protocol["$defs"])
        self.assertNotIn("entityIdentity", protocol["$defs"])


if __name__ == "__main__":
    unittest.main()
