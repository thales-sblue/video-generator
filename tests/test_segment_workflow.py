import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from video_generator.adapters import FFmpegError, MediaProbe, SegmentArtifact, StreamProbe
from video_generator.cli import main
from video_generator.domain import EditOperation, EditPlan
from video_generator.validation import (
    PreflightIssue,
    PreflightReport,
    SegmentValidationIssue,
    SegmentValidationReport,
)
from video_generator.workflows import (
    SegmentWorkflowError,
    SegmentWorkflowReport,
    run_segment_workflow,
)


def segment_plan(*, kind: str = "extract_segment", parameters=None) -> EditPlan:
    source = str(Path("inputs/source.mp4").resolve())
    output = str(Path("output/segment.mp4").resolve())
    return EditPlan(
        plan_id="plan-1",
        brief_id="brief-1",
        sources=(source,),
        output_path=output,
        operations=(
            EditOperation(
                operation_id="extract-1",
                kind=kind,
                source=source,
                start_seconds=2,
                end_seconds=5,
                parameters=parameters or {},
            ),
        ),
    )


def source_probe(plan: EditPlan) -> MediaProbe:
    return MediaProbe(
        source_path=plan.sources[0],
        file_size_bytes=1000,
        format_name="mov,mp4",
        duration_seconds=10,
        bit_rate_bps=800000,
        streams=(StreamProbe(0, "video", "h264", 10, 1920, 1080, None, None),),
    )


def valid_preflight(plan: EditPlan) -> PreflightReport:
    return PreflightReport(plan.plan_id, True, (), (source_probe(plan),))


def validation_for(artifact: SegmentArtifact, *, valid: bool = True) -> SegmentValidationReport:
    issues = () if valid else (SegmentValidationIssue("duration_mismatch", "duration differs"),)
    return SegmentValidationReport(
        valid=valid,
        artifact=artifact,
        expected_duration_seconds=3,
        actual_duration_seconds=3 if valid else 3.5,
        duration_tolerance_seconds=0.1,
        issues=issues,
        probe=None,
    )


def workflow_report(plan: EditPlan, *, valid: bool = True) -> SegmentWorkflowReport:
    artifact = SegmentArtifact(plan.sources[0], plan.output_path, 2, 5, 500)
    return SegmentWorkflowReport(
        plan_id=plan.plan_id,
        operation_id=plan.operations[0].operation_id,
        preflight=valid_preflight(plan),
        artifact=artifact,
        validation=validation_for(artifact, valid=valid),
    )


class SegmentWorkflowTests(unittest.TestCase):
    def test_runs_preflight_extraction_and_validation_in_order(self):
        plan = segment_plan()
        artifact = SegmentArtifact(plan.sources[0], plan.output_path, 2, 5, 500)
        calls = []

        def preflight(value):
            calls.append("preflight")
            return valid_preflight(value)

        def extract(*args, **kwargs):
            calls.append("extract")
            self.assertEqual(args, (plan.sources[0], plan.output_path))
            self.assertEqual(kwargs["start_seconds"], 2)
            self.assertEqual(kwargs["end_seconds"], 5)
            self.assertEqual(kwargs["timeout_seconds"], 30)
            return artifact

        def validate(*args, **kwargs):
            calls.append("validate")
            self.assertEqual(args, (artifact,))
            self.assertEqual(kwargs["duration_tolerance_seconds"], 0.25)
            return validation_for(artifact)

        def before_extract(value):
            self.assertEqual(value, plan)
            calls.append("fingerprint")

        report = run_segment_workflow(
            plan,
            timeout_seconds=30,
            duration_tolerance_seconds=0.25,
            preflight=preflight,
            before_extract=before_extract,
            extract=extract,
            validate=validate,
        )

        self.assertEqual(calls, ["preflight", "fingerprint", "extract", "validate"])
        self.assertTrue(report.valid)
        payload = json.loads(report.to_json())
        self.assertEqual(payload["workflow"], "segment-extract")
        self.assertEqual(payload["artifact"]["output_path"], plan.output_path)

    def test_rejects_unsupported_plan_before_preflight_or_writes(self):
        preflight = Mock()
        extract = Mock()

        with self.assertRaisesRegex(SegmentWorkflowError, "does not support"):
            run_segment_workflow(
                segment_plan(kind="trim"),
                preflight=preflight,
                extract=extract,
            )
        with self.assertRaisesRegex(SegmentWorkflowError, "does not accept parameters"):
            run_segment_workflow(
                segment_plan(parameters={"codec": "h264"}),
                preflight=preflight,
                extract=extract,
            )

        preflight.assert_not_called()
        extract.assert_not_called()

    def test_rejects_invalid_runtime_values_before_preflight_or_writes(self):
        preflight = Mock()
        extract = Mock()

        with self.assertRaisesRegex(SegmentWorkflowError, "timeout_seconds"):
            run_segment_workflow(
                segment_plan(),
                timeout_seconds=0,
                preflight=preflight,
                extract=extract,
            )
        with self.assertRaisesRegex(SegmentWorkflowError, "duration_tolerance_seconds"):
            run_segment_workflow(
                segment_plan(),
                duration_tolerance_seconds=float("nan"),
                preflight=preflight,
                extract=extract,
            )

        preflight.assert_not_called()
        extract.assert_not_called()

    def test_rejects_failed_preflight_before_extraction(self):
        plan = segment_plan()
        failed = PreflightReport(
            plan.plan_id,
            False,
            (PreflightIssue("end_out_of_range", "end exceeds source", plan.sources[0], "extract-1"),),
            (source_probe(plan),),
        )
        extract = Mock()

        with self.assertRaisesRegex(SegmentWorkflowError, "end_out_of_range"):
            run_segment_workflow(plan, preflight=lambda value: failed, extract=extract)

        extract.assert_not_called()

    def test_reports_invalid_artifact_and_wraps_extraction_failure(self):
        plan = segment_plan()
        artifact = SegmentArtifact(plan.sources[0], plan.output_path, 2, 5, 500)
        report = run_segment_workflow(
            plan,
            preflight=valid_preflight,
            extract=lambda *args, **kwargs: artifact,
            validate=lambda *args, **kwargs: validation_for(artifact, valid=False),
        )

        self.assertFalse(report.valid)
        self.assertEqual(report.validation.issues[0].code, "duration_mismatch")
        with self.assertRaisesRegex(SegmentWorkflowError, "ffmpeg unavailable"):
            run_segment_workflow(
                plan,
                preflight=valid_preflight,
                extract=Mock(side_effect=FFmpegError("ffmpeg unavailable")),
            )

    def test_rejects_adapter_metadata_that_does_not_match_the_plan(self):
        plan = segment_plan()
        unexpected = SegmentArtifact(
            plan.sources[0],
            str(Path("output/other.mp4").resolve()),
            2,
            5,
            500,
        )
        validate = Mock()

        with self.assertRaisesRegex(SegmentWorkflowError, "unexpected output path"):
            run_segment_workflow(
                plan,
                preflight=valid_preflight,
                extract=lambda *args, **kwargs: unexpected,
                validate=validate,
            )

        validate.assert_not_called()


class SegmentWorkflowCliTests(unittest.TestCase):
    def test_loads_persisted_plan_and_emits_workflow_report(self):
        plan = segment_plan()
        report = workflow_report(plan)
        manifest = Mock()
        manifest.to_dict.return_value = {"schema_version": 1, "manifest_id": "manifest-plan-1"}
        stdout = io.StringIO()

        def execute_workflow(value, **kwargs):
            kwargs["before_extract"](value)
            return report

        with tempfile.TemporaryDirectory() as directory:
            plan_path = Path(directory) / "edit-plan.json"
            plan_path.write_text(plan.to_json(), encoding="utf-8")
            with patch(
                "video_generator.cli.run_segment_workflow",
                side_effect=execute_workflow,
            ) as execute, patch(
                "video_generator.cli.fingerprint_file",
                return_value=Mock(),
            ), patch(
                "video_generator.cli.build_segment_render_manifest",
                return_value=manifest,
            ) as build, patch(
                "video_generator.cli.publish_render_manifest",
                return_value=Path(plan.output_path + ".manifest.json"),
            ) as publish, contextlib.redirect_stdout(stdout):
                exit_code = main(
                    [
                        "execute-segment-plan",
                        str(plan_path),
                        "--timeout-seconds",
                        "30",
                        "--duration-tolerance-seconds",
                        "0.25",
                        "--json",
                    ]
                )

        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertTrue(payload["valid"])
        self.assertEqual(payload["manifest"]["manifest_id"], "manifest-plan-1")
        executed_plan = execute.call_args.args[0]
        self.assertEqual(executed_plan.to_dict(), plan.to_dict())
        self.assertEqual(execute.call_args.kwargs["timeout_seconds"], 30)
        self.assertEqual(execute.call_args.kwargs["duration_tolerance_seconds"], 0.25)
        self.assertEqual(build.call_args.args[:2], (executed_plan, report))
        self.assertEqual(publish.call_args.args[0], manifest)

    def test_returns_one_for_invalid_artifact_and_two_for_rejected_plan(self):
        plan = segment_plan()
        stdout = io.StringIO()
        stderr = io.StringIO()
        manifest = Mock()
        manifest.to_dict.return_value = {"schema_version": 1, "manifest_id": "manifest-plan-1"}

        invalid_report = workflow_report(plan, valid=False)

        def execute_invalid(value, **kwargs):
            kwargs["before_extract"](value)
            return invalid_report

        with tempfile.TemporaryDirectory() as directory:
            plan_path = Path(directory) / "edit-plan.json"
            plan_path.write_text(plan.to_json(), encoding="utf-8")
            with patch(
                "video_generator.cli.run_segment_workflow",
                side_effect=execute_invalid,
            ), patch(
                "video_generator.cli.fingerprint_file",
                return_value=Mock(),
            ), patch(
                "video_generator.cli.build_segment_render_manifest",
                return_value=manifest,
            ), patch(
                "video_generator.cli.publish_render_manifest",
                return_value=Path(plan.output_path + ".manifest.json"),
            ), contextlib.redirect_stdout(stdout):
                invalid_exit = main(["execute-segment-plan", str(plan_path), "--json"])
            with patch(
                "video_generator.cli.run_segment_workflow",
                side_effect=SegmentWorkflowError("unsupported operation"),
            ), contextlib.redirect_stderr(stderr):
                rejected_exit = main(["execute-segment-plan", str(plan_path)])

        self.assertEqual(invalid_exit, 1)
        self.assertFalse(json.loads(stdout.getvalue())["valid"])
        self.assertEqual(rejected_exit, 2)
        self.assertIn("Segment workflow error: unsupported operation", stderr.getvalue())

    def test_refuses_existing_manifest_before_executing_media(self):
        plan = segment_plan()
        stderr = io.StringIO()
        execute = Mock()

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan_path = root / "edit-plan.json"
            manifest_path = root / "render-manifest.json"
            plan_path.write_text(plan.to_json(), encoding="utf-8")
            manifest_path.write_text("existing", encoding="utf-8")
            with patch(
                "video_generator.cli.run_segment_workflow",
                execute,
            ), contextlib.redirect_stderr(stderr):
                exit_code = main(
                    [
                        "execute-segment-plan",
                        str(plan_path),
                        "--manifest",
                        str(manifest_path),
                    ]
                )

        self.assertEqual(exit_code, 2)
        self.assertIn("manifest already exists", stderr.getvalue())
        execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
