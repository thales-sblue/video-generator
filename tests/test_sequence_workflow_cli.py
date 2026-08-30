import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from video_generator.adapters import MediaProbe, SequenceArtifact, StreamProbe
from video_generator.cli import main
from video_generator.doctor import DoctorReport, ToolStatus
from video_generator.domain import EditOperation, EditPlan
from video_generator.validation import PreflightReport, SequenceValidationReport
from video_generator.workflows import SequenceWorkflowReport


class SequenceWorkflowCliTests(unittest.TestCase):
    def test_executes_persisted_sequence_and_publishes_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.mp4"
            second = root / "second.mp4"
            narration = root / "narration.wav"
            output = root / "final.mp4"
            plan_path = root / "edit-plan.json"
            manifest_path = root / "render-manifest.json"
            first.write_bytes(b"first")
            second.write_bytes(b"second")
            narration.write_bytes(b"narration")
            output.write_bytes(b"timeline")
            plan = EditPlan(
                "plan-sequence",
                "brief-dark",
                (str(first), str(second), str(narration)),
                str(output),
                (
                    EditOperation("clip-1", "sequence_clip", str(first), 0, 1),
                    EditOperation("clip-2", "sequence_clip", str(second), 0, 2),
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
            preflight = PreflightReport(plan.plan_id, True, (), (probe, probe, narration_probe))
            validation = SequenceValidationReport(True, artifact, 3, 0.15, (), probe)
            report = SequenceWorkflowReport(
                plan.plan_id,
                ("clip-1", "clip-2", "voice-1"),
                preflight,
                artifact,
                validation,
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
            with patch("video_generator.cli.run_sequence_workflow", side_effect=run), patch(
                "video_generator.cli.run_doctor", return_value=doctor
            ), contextlib.redirect_stdout(stdout):
                exit_code = main(
                    [
                        "execute-sequence-plan",
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
        self.assertEqual(len(persisted["sources"]), 3)
        self.assertEqual(persisted["editorial_review"], "not_performed")
        self.assertTrue(persisted["technical_validation_valid"])


if __name__ == "__main__":
    unittest.main()
