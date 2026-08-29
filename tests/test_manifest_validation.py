import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from video_generator.domain import EditOperation, EditPlan, RenderManifest, ToolRecord
from video_generator.cli import main
from video_generator.manifests import fingerprint_file, fingerprint_plan
from video_generator.validation import validate_render_manifest


def state(directory: str, *, technical_valid: bool = True):
    root = Path(directory)
    source = root / "source.mp4"
    output = root / "segment.mp4"
    source.write_bytes(b"immutable source")
    output.write_bytes(b"rendered segment")
    plan = EditPlan(
        "plan-1",
        "brief-1",
        (str(source),),
        str(output),
        (EditOperation("extract-1", "extract_segment", str(source), 0, 1),),
    )
    manifest = RenderManifest(
        manifest_id="manifest-plan-1",
        plan_id=plan.plan_id,
        brief_id=plan.brief_id,
        workflow="segment-extract",
        plan_sha256=fingerprint_plan(plan),
        sources=(fingerprint_file(source),),
        outputs=(fingerprint_file(output),),
        tools=(
            ToolRecord("FFmpeg", "C:/tools/ffmpeg.exe", "ffmpeg version 7.1"),
            ToolRecord("ffprobe", "C:/tools/ffprobe.exe", "ffprobe version 7.1"),
        ),
        technical_validation_valid=technical_valid,
        technical_validation_issues=() if technical_valid else ("duration_mismatch",),
    )
    return plan, manifest, source, output


class ManifestValidationTests(unittest.TestCase):
    def test_accepts_unchanged_plan_sources_and_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            plan, manifest, _, _ = state(directory)
            report = validate_render_manifest(manifest, plan)

        self.assertTrue(report.integrity_valid)
        self.assertTrue(report.technically_ready)
        self.assertEqual(report.issues, ())
        self.assertTrue(json.loads(report.to_json())["technically_ready"])

    def test_detects_changed_and_missing_files_without_modifying_them(self):
        with tempfile.TemporaryDirectory() as directory:
            plan, manifest, source, output = state(directory)
            output.write_bytes(b"changed output")
            source.unlink()

            report = validate_render_manifest(manifest, plan)

            self.assertFalse(report.integrity_valid)
            self.assertFalse(report.technically_ready)
            self.assertEqual(output.read_bytes(), b"changed output")
            self.assertEqual(
                {issue.code for issue in report.issues},
                {"file_unavailable", "file_size_mismatch", "file_hash_mismatch"},
            )

    def test_detects_plan_linkage_and_content_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            plan, manifest, _, output = state(directory)
            changed_plan = EditPlan(
                "plan-2",
                "brief-2",
                plan.sources,
                str(output),
                (EditOperation("extract-1", "extract_segment", plan.sources[0], 0, 0.5),),
            )

            report = validate_render_manifest(manifest, changed_plan)

        self.assertEqual(
            {issue.code for issue in report.issues},
            {"plan_id_mismatch", "brief_id_mismatch", "plan_hash_mismatch"},
        )

    def test_keeps_recorded_technical_failure_separate_from_integrity(self):
        with tempfile.TemporaryDirectory() as directory:
            plan, manifest, _, _ = state(directory, technical_valid=False)
            report = validate_render_manifest(manifest, plan)

        self.assertTrue(report.integrity_valid)
        self.assertFalse(report.technical_validation_valid)
        self.assertFalse(report.technically_ready)
        self.assertEqual(report.recorded_technical_issues, ("duration_mismatch",))
        self.assertEqual(report.editorial_review, "not_performed")

    def test_rejects_manifest_for_an_unsupported_workflow(self):
        with tempfile.TemporaryDirectory() as directory:
            plan, manifest, _, _ = state(directory)
            payload = manifest.to_dict()
            payload["workflow"] = "unknown-workflow"
            report = validate_render_manifest(RenderManifest.from_dict(payload), plan)

        self.assertFalse(report.integrity_valid)
        self.assertEqual(report.issues[0].code, "unsupported_workflow")


class ManifestValidationCliTests(unittest.TestCase):
    def test_loads_persisted_contracts_and_returns_integrity_exit_codes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan, manifest, _, output = state(directory)
            plan_path = root / "edit-plan.json"
            manifest_path = root / "render-manifest.json"
            plan_path.write_text(plan.to_json(), encoding="utf-8")
            manifest_path.write_text(manifest.to_json(), encoding="utf-8")
            valid_stdout = io.StringIO()
            with contextlib.redirect_stdout(valid_stdout):
                valid_exit = main(
                    ["validate-manifest", str(manifest_path), "--plan", str(plan_path), "--json"]
                )

            output.write_bytes(b"tampered output")
            invalid_stdout = io.StringIO()
            with contextlib.redirect_stdout(invalid_stdout):
                invalid_exit = main(
                    ["validate-manifest", str(manifest_path), "--plan", str(plan_path), "--json"]
                )

        self.assertEqual(valid_exit, 0)
        self.assertTrue(json.loads(valid_stdout.getvalue())["technically_ready"])
        self.assertEqual(invalid_exit, 1)
        self.assertFalse(json.loads(invalid_stdout.getvalue())["integrity_valid"])

    def test_returns_two_for_invalid_manifest_contract(self):
        stderr = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan, _, _, _ = state(directory)
            plan_path = root / "edit-plan.json"
            manifest_path = root / "render-manifest.json"
            plan_path.write_text(plan.to_json(), encoding="utf-8")
            manifest_path.write_text('{"schema_version": 1}', encoding="utf-8")

            with contextlib.redirect_stderr(stderr):
                exit_code = main(
                    ["validate-manifest", str(manifest_path), "--plan", str(plan_path)]
                )

        self.assertEqual(exit_code, 2)
        self.assertIn("Manifest validation error", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
