import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from video_generator.adapters import MediaProbe, SequenceArtifact, StreamProbe
from video_generator.cli import _format_sequence_workflow, main
from video_generator.doctor import DoctorReport, ToolStatus
from video_generator.domain import EditOperation, EditPlan
from video_generator.manifests import ManifestError
from video_generator.validation import PreflightReport, SequenceValidationReport
from video_generator.workflows import SequenceWorkflowReport


class SequenceWorkflowSummaryTests(unittest.TestCase):
    def _report(self, **artifact_kwargs):
        artifact = SequenceArtifact(("a", "b"), "out.mp4", 3.0, 100, **artifact_kwargs)
        return SequenceWorkflowReport(
            "plan-1",
            ("clip-1", "clip-2"),
            PreflightReport("plan-1", True, (), ()),
            artifact,
            SequenceValidationReport(True, artifact, 3.0, 0.15, (), None),
        )

    def test_narration_line_distinguishes_text_from_file_and_none(self):
        self.assertIn(
            "Narration: not included",
            _format_sequence_workflow(self._report(), Path("m.json")),
        )
        self.assertIn(
            "Narration: included",
            _format_sequence_workflow(
                self._report(narration_source_path="voice.wav"), Path("m.json")
            ),
        )
        self.assertIn(
            "Narration: from text",
            _format_sequence_workflow(
                self._report(narration_text_sha256="a" * 64), Path("m.json")
            ),
        )

    def test_fade_line_reports_the_black_spans(self):
        self.assertIn(
            "Fades: not applied",
            _format_sequence_workflow(self._report(), Path("m.json")),
        )
        self.assertIn(
            "Fades: from black 1.0s / to black 1.5s",
            _format_sequence_workflow(
                self._report(video_fade_in_seconds=1.0, video_fade_out_seconds=1.5),
                Path("m.json"),
            ),
        )


class SequenceWorkflowCliTests(unittest.TestCase):
    def test_removes_a_final_when_manifest_creation_fails(self):
        stderr = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.mp4"
            second = root / "second.mp4"
            output = root / "final.mp4"
            first.write_bytes(b"first")
            second.write_bytes(b"second")
            plan = EditPlan(
                "plan-final",
                "brief-dark",
                (str(first), str(second)),
                str(output),
                (
                    EditOperation("clip-1", "sequence_clip", str(first), 0, 1),
                    EditOperation("clip-2", "sequence_clip", str(second), 0, 1),
                ),
            )
            plan_path = root / "edit-plan.json"
            plan_path.write_text(plan.to_json(), encoding="utf-8")

            def run(plan_value, **kwargs):
                kwargs["before_compose"](plan_value)
                output.write_bytes(b"validated but unmanifested")
                artifact = SequenceArtifact(
                    plan.sources,
                    str(output.resolve()),
                    2,
                    output.stat().st_size,
                )
                validation = SequenceValidationReport(True, artifact, 2, 0.15, (), None)
                return SequenceWorkflowReport(
                    plan.plan_id,
                    ("clip-1", "clip-2"),
                    PreflightReport(plan.plan_id, True, (), ()),
                    artifact,
                    validation,
                    "final",
                )

            with patch(
                "video_generator.cli.run_final_sequence_workflow", side_effect=run
            ), patch("video_generator.cli.run_doctor", return_value=object()), patch(
                "video_generator.cli.build_sequence_render_manifest",
                side_effect=ManifestError("source changed"),
            ), contextlib.redirect_stderr(stderr):
                exit_code = main(["execute-final-sequence-plan", str(plan_path)])

            self.assertEqual(exit_code, 2)
            self.assertFalse(output.exists())
            self.assertIn("source changed", stderr.getvalue())

    def test_executes_persisted_sequence_and_publishes_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.mp4"
            second = root / "second.mp4"
            narration = root / "narration.wav"
            music = root / "music.wav"
            output = root / "final.mp4"
            plan_path = root / "edit-plan.json"
            manifest_path = root / "render-manifest.json"
            first.write_bytes(b"first")
            second.write_bytes(b"second")
            narration.write_bytes(b"narration")
            music.write_bytes(b"music")
            output.write_bytes(b"timeline")
            plan = EditPlan(
                "plan-sequence",
                "brief-dark",
                (str(first), str(second), str(narration), str(music)),
                str(output),
                (
                    EditOperation("clip-1", "sequence_clip", str(first), 0, 1),
                    EditOperation("clip-2", "sequence_clip", str(second), 0, 2),
                    EditOperation(
                        "captions-1",
                        "captions",
                        parameters={
                            "style": "bottom_box",
                            "items": [
                                {"text": "Caption", "start_seconds": 0, "end_seconds": 3}
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
            plan_path.write_text(plan.to_json(), encoding="utf-8")
            artifact = SequenceArtifact(
                (str(first.resolve()), str(second.resolve())),
                str(output.resolve()),
                3,
                output.stat().st_size,
                str(narration.resolve()),
                1,
                str(music.resolve()),
                -18.0,
            )
            probe = MediaProbe(
                str(output.resolve()),
                output.stat().st_size,
                "mov,mp4",
                3,
                1000,
                (StreamProbe(0, "video", "h264", 3, 1280, 720, None, None),),
            )
            narration_probe = MediaProbe(
                str(narration.resolve()),
                narration.stat().st_size,
                "wav",
                3,
                1536000,
                (StreamProbe(0, "audio", "pcm_s16le", 3, None, None, 48000, 2),),
            )
            music_probe = MediaProbe(
                str(music.resolve()),
                music.stat().st_size,
                "wav",
                1,
                1536000,
                (StreamProbe(0, "audio", "pcm_s16le", 1, None, None, 48000, 2),),
            )
            preflight = PreflightReport(
                plan.plan_id, True, (), (probe, probe, narration_probe, music_probe)
            )
            validation = SequenceValidationReport(True, artifact, 3, 0.15, (), probe)
            report = SequenceWorkflowReport(
                plan.plan_id,
                ("clip-1", "clip-2", "captions-1", "music-1", "voice-1"),
                preflight,
                artifact,
                validation,
                "final",
            )
            doctor = DoctorReport(
                "Windows",
                "11",
                "AMD64",
                True,
                False,
                True,
                "config/default.toml",
                (
                    ToolStatus("FFmpeg", True, "ffmpeg 8", "C:/tools/ffmpeg.exe"),
                    ToolStatus("ffprobe", True, "ffprobe 8", "C:/tools/ffprobe.exe"),
                ),
            )

            def run(plan_value, **kwargs):
                kwargs["before_compose"](plan_value)
                return report

            stdout = io.StringIO()
            with patch("video_generator.cli.run_final_sequence_workflow", side_effect=run), patch(
                "video_generator.cli.run_doctor", return_value=doctor
            ), contextlib.redirect_stdout(stdout):
                exit_code = main(
                    [
                        "execute-final-sequence-plan",
                        str(plan_path),
                        "--manifest",
                        str(manifest_path),
                        "--json",
                    ]
                )

            payload = json.loads(stdout.getvalue())
            persisted = json.loads(manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["workflow"], "video-sequence")
        self.assertEqual(persisted["workflow"], "video-sequence")
        self.assertEqual(len(persisted["sources"]), 4)
        self.assertEqual(payload["artifact"]["caption_count"], 1)
        self.assertEqual(payload["artifact"]["music_gain_db"], -18.0)
        self.assertEqual(payload["publication"], "final")
        self.assertEqual(persisted["editorial_review"], "not_performed")
        self.assertTrue(persisted["technical_validation_valid"])

    def test_requires_the_final_command_for_a_final_mp4_plan(self):
        stderr = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.mp4"
            second = root / "second.mp4"
            first.write_bytes(b"first")
            second.write_bytes(b"second")
            plan = EditPlan(
                "plan-final",
                "brief-dark",
                (str(first), str(second)),
                str(root / "final.mp4"),
                (
                    EditOperation("clip-1", "sequence_clip", str(first), 0, 1),
                    EditOperation("clip-2", "sequence_clip", str(second), 0, 1),
                ),
            )
            plan_path = root / "edit-plan.json"
            plan_path.write_text(plan.to_json(), encoding="utf-8")

            with contextlib.redirect_stderr(stderr):
                exit_code = main(["execute-sequence-plan", str(plan_path)])

        self.assertEqual(exit_code, 2)
        self.assertIn("execute-final-sequence-plan", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
