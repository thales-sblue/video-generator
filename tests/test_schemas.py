import json
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class SchemaTests(unittest.TestCase):
    def test_public_schemas_are_valid_json_and_fail_closed(self):
        expected = {
            "video-request-v1.schema.json",
            "video-brief-v1.schema.json",
            "edit-plan-v1.schema.json",
            "render-manifest-v1.schema.json",
            "narrative-script-v1.schema.json",
            "scene-plan-v1.schema.json",
            "shot-plan-v1.schema.json",
            "asset-requirements-v1.schema.json",
            "asset-resolution-plan-v1.schema.json",
            "screen-deck-v1.schema.json",
            "visual-curation-set-v1.schema.json",
            "visual-lock-v1.schema.json",
            "cut-review-v1.schema.json",
        }
        schema_paths = set((REPOSITORY_ROOT / "schemas").glob("*.schema.json"))

        self.assertEqual({path.name for path in schema_paths}, expected)
        for path in schema_paths:
            with self.subTest(schema=path.name):
                schema = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
                self.assertFalse(schema["additionalProperties"])
                self.assertIn("schema_version", schema["required"])
                self.assertEqual(schema["properties"]["schema_version"]["const"], 1)


if __name__ == "__main__":
    unittest.main()
