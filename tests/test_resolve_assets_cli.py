"""CLI tests for `python -m video_generator resolve-assets`."""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from video_generator.cli import main
from video_generator.domain import NarrativeScript, plan_scenes, plan_shots

SCRIPT = """\
Uma pessoa inteligente pode acreditar em algo absurdo sem perceber.

O raciocinio serve para descobrir a verdade, mas tambem para proteger crencas.

Coloque tudo isso dentro da internet, um sistema que observa a sua atencao.

A pergunta muda: que ideias absurdas eu justifico por me achar esperto demais?
"""


def _sidecar(tags):
    return json.dumps(
        {
            "title": " ".join(tags),
            "description": "imagem de " + ", ".join(tags),
            "tags": tags,
            "license": "CC0-1.0",
            "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
            "author": "Test Fixture",
            "source_url": "https://example.org/fixture",
            "width": 1920,
            "height": 1080,
        }
    )


class ResolveAssetsCliTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

        script = NarrativeScript.from_text("myside", SCRIPT, total_duration_seconds=90.0)
        scene_plan = plan_scenes(script, seed=3)
        shot_plan, requirements = plan_shots(scene_plan, seed=3, orientation="landscape")
        self.shot_plan_path = self.root / "shot-plan.json"
        self.requirements_path = self.root / "asset-requirements.json"
        self.shot_plan_path.write_text(shot_plan.to_json(), encoding="utf-8")
        self.requirements_path.write_text(requirements.to_json(), encoding="utf-8")

        self.library = self.root / "library"
        term_sets = [
            ["pessoa", "mesa", "escritorio"],
            ["noticia", "jornal", "internet"],
            ["raciocinio", "verdade", "mente"],
            ["atencao", "algoritmo", "tela"],
            ["crenca", "identidade", "grupo"],
            ["computador", "leitura", "pessoa"],
        ]
        for i, tags in enumerate(term_sets):
            img = self.library / f"img_{i:02d}.jpg"
            img.parent.mkdir(parents=True, exist_ok=True)
            img.write_bytes(f"fake-image-{i}".encode())
            (self.library / f"img_{i:02d}.jpg.json").write_text(_sidecar(tags), encoding="utf-8")

        self.out = self.root / "resolved"

    def _run(self, *extra):
        return main(
            [
                "resolve-assets",
                "--shot-plan", str(self.shot_plan_path),
                "--asset-requirements", str(self.requirements_path),
                "--library", str(self.library),
                "--out-dir", str(self.out),
                "--no-probe",
                *extra,
            ]
        )

    def test_writes_the_three_artifacts_and_acquires_files(self):
        code = self._run("--json")
        self.assertEqual(code, 0)
        plan = json.loads((self.out / "asset-resolution-plan.json").read_text("utf-8"))
        provenance = json.loads((self.out / "asset-provenance.json").read_text("utf-8"))
        bindings = json.loads((self.out / "asset-bindings.json").read_text("utf-8"))

        self.assertTrue(plan["resolved"])
        # every resolved asset has provenance and a real file on disk
        prov_ids = {p["asset_id"] for p in provenance}
        for entry in plan["resolved"]:
            self.assertIn(entry["asset_id"], prov_ids)
            self.assertRegex(entry["provenance"]["sha256"], r"^[0-9a-f]{64}$")
            self.assertTrue(Path(entry["local_path"]).exists())
        # bindings cover exactly the resolved assets
        self.assertEqual(set(bindings), {e["asset_id"] for e in plan["resolved"]})

    def test_refuses_to_overwrite_without_force(self):
        self.assertEqual(self._run(), 0)
        self.assertEqual(self._run(), 2)  # artifacts already exist
        self.assertEqual(self._run("--force"), 0)

    def test_require_complete_exits_three_when_unresolved_remain(self):
        # a tiny library cannot satisfy every video requirement -> unresolved
        code = self._run("--require-complete")
        plan = json.loads((self.out / "asset-resolution-plan.json").read_text("utf-8"))
        if plan["unresolved"]:
            self.assertEqual(code, 3)
        else:  # pragma: no cover - depends on the derived plan
            self.assertEqual(code, 0)

    def test_unknown_provider_is_rejected(self):
        code = self._run("--providers", "local,teleport")
        self.assertEqual(code, 2)

    def test_reuse_review_split_is_reported(self):
        code = self._run("--json")
        self.assertEqual(code, 0)
        revised = json.loads(
            (self.out / "revised-asset-requirements.json").read_text("utf-8")
        )
        self.assertIn("requirements", revised)


if __name__ == "__main__":
    unittest.main()
