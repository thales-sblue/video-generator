import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from video_generator.cli import main

REPO_ROOT = Path(__file__).resolve().parents[1]
ROTEIRO = REPO_ROOT / "assets" / "desumanizando" / "video_01" / "roteiro_narracao.txt"


def _run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


class PlanScenesCliTests(unittest.TestCase):
    def test_from_text_writes_three_artifacts(self):
        with tempfile.TemporaryDirectory() as d:
            out_dir = Path(d) / "out"
            code, stdout, stderr = _run(
                [
                    "plan-scenes",
                    "--from-text", str(ROTEIRO),
                    "--total-duration", "270",
                    "--target", "1920x1080:cover",
                    "--seed", "20260902",
                    "--out-dir", str(out_dir),
                ]
            )
            self.assertEqual(code, 0, stderr)
            scene = json.loads((out_dir / "scene-plan.json").read_text(encoding="utf-8"))
            shot = json.loads((out_dir / "shot-plan.json").read_text(encoding="utf-8"))
            assets = json.loads((out_dir / "asset-requirements.json").read_text(encoding="utf-8"))
            self.assertGreaterEqual(len(scene["scenes"]), 8)
            self.assertGreaterEqual(len(shot["shots"]), 50)
            self.assertEqual(assets["orientation"], "landscape")
            self.assertIn("scenes", stdout)

    def test_is_deterministic_across_runs(self):
        with tempfile.TemporaryDirectory() as d:
            a, b = Path(d) / "a", Path(d) / "b"
            argv = lambda o: [
                "plan-scenes", "--from-text", str(ROTEIRO), "--total-duration", "270",
                "--target", "1920x1080", "--seed", "1", "--out-dir", str(o),
            ]
            self.assertEqual(_run(argv(a))[0], 0)
            self.assertEqual(_run(argv(b))[0], 0)
            for name in ("scene-plan.json", "shot-plan.json", "asset-requirements.json"):
                self.assertEqual(
                    (a / name).read_text(encoding="utf-8"),
                    (b / name).read_text(encoding="utf-8"),
                )

    def test_refuses_to_overwrite_without_force(self):
        with tempfile.TemporaryDirectory() as d:
            out_dir = Path(d) / "out"
            argv = [
                "plan-scenes", "--from-text", str(ROTEIRO), "--total-duration", "270",
                "--target", "1920x1080", "--out-dir", str(out_dir),
            ]
            self.assertEqual(_run(argv)[0], 0)
            code, _, stderr = _run(argv)
            self.assertEqual(code, 2)
            self.assertIn("overwrite", stderr.lower())
            self.assertEqual(_run(argv + ["--force"])[0], 0)

    def test_overrides_are_applied_to_all_three_documents(self):
        with tempfile.TemporaryDirectory() as d:
            out_dir = Path(d) / "out"
            overrides = Path(d) / "ov.json"
            overrides.write_text(
                json.dumps({"scenes": {"scene_02": {"visual_intent": "um novo enquadramento"}}}),
                encoding="utf-8",
            )
            code, _, stderr = _run(
                [
                    "plan-scenes", "--from-text", str(ROTEIRO), "--total-duration", "270",
                    "--target", "1920x1080", "--out-dir", str(out_dir),
                    "--overrides", str(overrides),
                ]
            )
            self.assertEqual(code, 0, stderr)
            scene = json.loads((out_dir / "scene-plan.json").read_text(encoding="utf-8"))
            match = next(s for s in scene["scenes"] if s["scene_id"] == "scene_02")
            self.assertEqual(match["visual_intent"], "um novo enquadramento")
            self.assertEqual(match["visual_intent_provenance"], "authored")

    def test_emit_edit_plan_with_asset_bindings(self):
        with tempfile.TemporaryDirectory() as d:
            out_dir = Path(d) / "out"
            self.assertEqual(
                _run(
                    ["plan-scenes", "--from-text", str(ROTEIRO), "--total-duration", "270",
                     "--target", "1920x1080:cover", "--out-dir", str(out_dir)]
                )[0],
                0,
            )
            assets = json.loads((out_dir / "asset-requirements.json").read_text(encoding="utf-8"))
            bindings = {}
            for req in assets["requirements"]:
                p = Path(d) / (req["asset_id"] + (".mp4" if req["type"] == "video" else ".jpg"))
                p.write_bytes(b"x")
                bindings[req["asset_id"]] = str(p)
            bindings_path = Path(d) / "bindings.json"
            bindings_path.write_text(json.dumps(bindings), encoding="utf-8")
            edit_plan_path = out_dir / "edit-plan.json"
            code, _, stderr = _run(
                ["plan-scenes", "--from-text", str(ROTEIRO), "--total-duration", "270",
                 "--target", "1920x1080:cover", "--out-dir", str(out_dir), "--force",
                 "--emit-edit-plan", str(edit_plan_path), "--assets", str(bindings_path)]
            )
            self.assertEqual(code, 0, stderr)
            plan = json.loads(edit_plan_path.read_text(encoding="utf-8"))
            self.assertEqual(plan["target_format"]["width"], 1920)
            kinds = [op["kind"] for op in plan["operations"]]
            self.assertTrue(all(k in ("image_clip", "sequence_clip") for k in kinds))

    def test_planning_error_exits_two(self):
        with tempfile.TemporaryDirectory() as d:
            bad = Path(d) / "bad.json"
            bad.write_text(json.dumps({"schema_version": 1, "script_id": "x", "blocks": []}), encoding="utf-8")
            code, _, stderr = _run(
                ["plan-scenes", "--script", str(bad), "--target", "1920x1080", "--out-dir", str(Path(d) / "o")]
            )
            self.assertEqual(code, 2)
            self.assertTrue(stderr.strip())


if __name__ == "__main__":
    unittest.main()
