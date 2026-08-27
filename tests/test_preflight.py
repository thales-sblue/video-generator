import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from video_generator.adapters import MediaProbe, ProbeError, StreamProbe
from video_generator.cli import main
from video_generator.domain import EditOperation, EditPlan
from video_generator.validation import preflight_edit_plan


def media_probe(source: str, duration: float | None = 10) -> MediaProbe:
    return MediaProbe(
        source_path=str(Path(source).resolve()),
        file_size_bytes=100,
        format_name="mov,mp4",
        duration_seconds=duration,
        bit_rate_bps=800000,
        streams=(
            StreamProbe(
                index=0,
                codec_type="video",
                codec_name="h264",
                duration_seconds=duration,
                width=1920,
                height=1080,
                sample_rate_hz=None,
                channels=None,
            ),
        ),
    )


def edit_plan(source: str, *, start: float = 1, end: float = 8) -> EditPlan:
    return EditPlan(
        plan_id="plan-1",
        brief_id="brief-1",
        sources=(source,),
        output_path="output/result.mp4",
        operations=(
            EditOperation(
                operation_id="trim-1",
                kind="trim",
                source=source,
                start_seconds=start,
                end_seconds=end,
            ),
        ),
    )


class PreflightTests(unittest.TestCase):
    def test_accepts_timed_operation_within_probed_duration(self):
        plan = edit_plan("inputs/source.mp4")

        report = preflight_edit_plan(plan, probe=lambda source: media_probe(str(source)))

        self.assertTrue(report.valid)
        self.assertEqual(report.issues, ())
        self.assertEqual(len(report.sources), 1)

    def test_rejects_operation_beyond_source_duration(self):
        plan = edit_plan("inputs/source.mp4", end=12)

        report = preflight_edit_plan(plan, probe=lambda source: media_probe(str(source), duration=10))

        self.assertFalse(report.valid)
        self.assertEqual(report.issues[0].code, "end_out_of_range")
        self.assertEqual(report.issues[0].operation_id, "trim-1")

    def test_fails_closed_when_source_or_duration_cannot_be_inspected(self):
        unavailable = edit_plan("inputs/missing.mp4")

        def fail_probe(source: str | Path) -> MediaProbe:
            raise ProbeError(f"source does not exist: {source}")

        unavailable_report = preflight_edit_plan(unavailable, probe=fail_probe)
        unknown_duration_report = preflight_edit_plan(
            edit_plan("inputs/still.png"),
            probe=lambda source: media_probe(str(source), duration=None),
        )

        self.assertEqual(unavailable_report.issues[0].code, "source_unavailable")
        self.assertEqual(unknown_duration_report.issues[0].code, "source_duration_unknown")
        self.assertFalse(unavailable_report.valid)
        self.assertFalse(unknown_duration_report.valid)

    def test_rejects_timed_operation_without_source(self):
        plan = EditPlan(
            plan_id="plan-1",
            brief_id="brief-1",
            sources=("inputs/source.mp4",),
            output_path="output/result.mp4",
            operations=(EditOperation("trim-1", "trim", start_seconds=1, end_seconds=8),),
        )

        report = preflight_edit_plan(plan, probe=lambda source: media_probe(str(source)))

        self.assertFalse(report.valid)
        self.assertEqual(report.issues[0].code, "timed_operation_without_source")
        self.assertEqual(report.issues[0].operation_id, "trim-1")

    def test_cli_loads_plan_and_returns_machine_readable_failure(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            plan_path = Path(temporary_directory) / "edit-plan.json"
            plan_path.write_text(edit_plan("inputs/source.mp4", end=12).to_json(), encoding="utf-8")
            stdout = io.StringIO()
            with patch(
                "video_generator.validation.preflight.probe_media",
                return_value=media_probe("inputs/source.mp4", duration=10),
            ), contextlib.redirect_stdout(stdout):
                exit_code = main(["preflight", str(plan_path), "--json"])

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 1)
        self.assertFalse(payload["valid"])
        self.assertEqual(payload["issues"][0]["code"], "end_out_of_range")

    def test_cli_rejects_invalid_plan_before_probing(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            plan_path = Path(temporary_directory) / "edit-plan.json"
            plan_path.write_text('{"schema_version": 1}', encoding="utf-8")
            stderr = io.StringIO()
            with patch("video_generator.validation.preflight.probe_media") as probe, contextlib.redirect_stderr(
                stderr
            ):
                exit_code = main(["preflight", str(plan_path)])

        self.assertEqual(exit_code, 2)
        self.assertIn("Plan error:", stderr.getvalue())
        probe.assert_not_called()


if __name__ == "__main__":
    unittest.main()
