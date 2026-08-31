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
    def test_accepts_captioned_mixed_sequence_and_all_source_fingerprints(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.mp4"
            second = root / "second.mp4"
            narration = root / "narration.wav"
            music = root / "music.wav"
            output = root / "final.mp4"
            for path, content in (
                (first, b"first"),
                (second, b"second"),
                (narration, b"narration"),
                (music, b"music"),
                (output, b"final"),
            ):
                path.write_bytes(content)
            plan = EditPlan(
                "plan-narrated",
                "brief-dark",
                (str(first), str(second), str(narration), str(music)),
                str(output),
                (
                    EditOperation("clip-1", "sequence_clip", str(first), 0, 1),
                    EditOperation("clip-2", "sequence_clip", str(second), 0, 1),
                    EditOperation(
                        "captions-1",
                        "captions",
                        parameters={
                            "style": "bottom_box",
                            "items": [
                                {"text": "First", "start_seconds": 0, "end_seconds": 1},
                                {"text": "Second", "start_seconds": 1, "end_seconds": 2},
                            ],
                        },
                    ),
                    EditOperation(
                        "music-1",
                        "music",
                        str(music),
                        parameters={"duration_policy": "loop_to_timeline", "gain_db": -18},
                    ),
                    EditOperation(
                        "voice-1",
                        "narration",
                        str(narration),
                        parameters={"duration_policy": "match_timeline"},
                    ),
                ),
            )
            manifest = RenderManifest(
                "manifest-plan-narrated",
                plan.plan_id,
                plan.brief_id,
                "video-sequence",
                fingerprint_plan(plan),
                tuple(fingerprint_file(source) for source in plan.sources),
                (fingerprint_file(output),),
                (
                    ToolRecord("FFmpeg", "C:/tools/ffmpeg.exe", "ffmpeg 8"),
                    ToolRecord("ffprobe", "C:/tools/ffprobe.exe", "ffprobe 8"),
                ),
                True,
            )

            report = validate_render_manifest(manifest, plan)
            invalid_caption_plan = EditPlan(
                plan.plan_id,
                plan.brief_id,
                plan.sources,
                plan.output_path,
                (
                    *plan.operations[:2],
                    EditOperation(
                        "captions-1",
                        "captions",
                        parameters={
                            "style": "bottom_box",
                            "items": [
                                {"text": "{\\an8}override", "start_seconds": 0, "end_seconds": 1}
                            ],
                        },
                    ),
                    *plan.operations[-2:],
                ),
            )
            invalid_report = validate_render_manifest(manifest, invalid_caption_plan)

        self.assertTrue(report.technically_ready)
        self.assertEqual(report.issues, ())
        self.assertIn("workflow_plan_mismatch", {issue.code for issue in invalid_report.issues})

    def test_accepts_a_sequence_plan_with_image_clip_and_captions_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            clip = root / "clip.mp4"
            still = root / "card.png"
            subtitles = root / "cues.srt"
            output = root / "final.mp4"
            for path, content in (
                (clip, b"clip"),
                (still, b"still"),
                (subtitles, b"1\n00:00:00,000 --> 00:00:01,000\nhi\n"),
                (output, b"final"),
            ):
                path.write_bytes(content)
            plan = EditPlan(
                "plan-image-captions",
                "brief-dark",
                (str(clip), str(still), str(subtitles)),
                str(output),
                (
                    EditOperation("clip-1", "sequence_clip", str(clip), 0, 1.5),
                    EditOperation(
                        "image-1", "image_clip", str(still), parameters={"duration_seconds": 2}
                    ),
                    EditOperation(
                        "captions-1", "captions", str(subtitles), parameters={"style": "bottom_box"}
                    ),
                ),
            )
            manifest = RenderManifest(
                "manifest-plan-image-captions",
                plan.plan_id,
                plan.brief_id,
                "video-sequence",
                fingerprint_plan(plan),
                tuple(fingerprint_file(source) for source in plan.sources),
                (fingerprint_file(output),),
                (
                    ToolRecord("FFmpeg", "C:/tools/ffmpeg.exe", "ffmpeg 8"),
                    ToolRecord("ffprobe", "C:/tools/ffprobe.exe", "ffprobe 8"),
                ),
                True,
            )

            report = validate_render_manifest(manifest, plan)

        self.assertTrue(report.technically_ready)
        self.assertEqual(report.issues, ())

    def test_accepts_a_sequence_plan_with_text_narration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "a.mp4"
            second = root / "b.mp4"
            output = root / "final.mp4"
            for path in (first, second, output):
                path.write_bytes(path.stem.encode("utf-8"))
            plan = EditPlan(
                "plan-tts",
                "brief-dark",
                (str(first), str(second)),
                str(output),
                (
                    EditOperation("clip-1", "sequence_clip", str(first), 0, 1),
                    EditOperation("clip-2", "sequence_clip", str(second), 0, 1),
                    EditOperation(
                        "voice-1",
                        "narration",
                        parameters={
                            "duration_policy": "match_timeline",
                            "text": "Dark script.",
                            "voice": "am_adam",
                            "speed": 1.1,
                            "lang": "en-gb",
                        },
                    ),
                ),
            )
            manifest = RenderManifest(
                "manifest-plan-tts",
                plan.plan_id,
                plan.brief_id,
                "video-sequence",
                fingerprint_plan(plan),
                tuple(fingerprint_file(source) for source in plan.sources),
                (fingerprint_file(output),),
                (
                    ToolRecord("FFmpeg", "C:/tools/ffmpeg.exe", "ffmpeg 8"),
                    ToolRecord("ffprobe", "C:/tools/ffprobe.exe", "ffprobe 8"),
                ),
                True,
            )

            report = validate_render_manifest(manifest, plan)

        self.assertTrue(report.technically_ready)
        self.assertEqual(report.issues, ())

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
