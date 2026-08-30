import unittest
from pathlib import Path

from video_generator.adapters import FFmpegError, MediaProbe, SequenceArtifact, StreamProbe
from video_generator.domain import EditOperation, EditPlan
from video_generator.validation import PreflightReport, SequenceValidationReport
from video_generator.workflows import SequenceWorkflowError, run_sequence_workflow


def sequence_plan(*, second_kind: str = "sequence_clip", second_parameters=None) -> EditPlan:
    first = str(Path("inputs/first.mp4").resolve())
    second = str(Path("inputs/second.mp4").resolve())
    return EditPlan(
        "plan-sequence",
        "brief-dark",
        (first, second),
        str(Path("output/timeline.mp4").resolve()),
        (
            EditOperation("clip-1", "sequence_clip", first, 1, 2.5),
            EditOperation(
                "clip-2",
                second_kind,
                second,
                0,
                2,
                second_parameters or {},
            ),
        ),
    )


def valid_preflight(plan: EditPlan, *, second_width: int = 1280) -> PreflightReport:
    probes = tuple(
        MediaProbe(
            source,
            1000,
            "mov,mp4",
            10,
            800000,
            (StreamProbe(0, "video", "h264", 10, width, 720, None, None),),
        )
        for source, width in zip(plan.sources, (1280, second_width), strict=True)
    )
    return PreflightReport(plan.plan_id, True, (), probes)


def narrated_plan(*, narration_parameters=None) -> EditPlan:
    silent = sequence_plan()
    narration = str(Path("inputs/narration.wav").resolve())
    return EditPlan(
        silent.plan_id,
        silent.brief_id,
        (*silent.sources, narration),
        silent.output_path,
        (
            *silent.operations,
            EditOperation(
                "voice-1",
                "narration",
                narration,
                parameters=(
                    {"duration_policy": "match_timeline"}
                    if narration_parameters is None
                    else narration_parameters
                ),
            ),
        ),
    )


def narrated_preflight(plan: EditPlan, *, duration: float = 3.5, audio: bool = True) -> PreflightReport:
    video_probes = tuple(
        MediaProbe(
            source,
            1000,
            "mov,mp4",
            10,
            800000,
            (StreamProbe(0, "video", "h264", 10, 1280, 720, None, None),),
        )
        for source in plan.sources[:2]
    )
    narration_streams = (
        (StreamProbe(0, "audio", "pcm_s16le", duration, None, None, 48000, 2),)
        if audio
        else (StreamProbe(0, "video", "h264", duration, 1280, 720, None, None),)
    )
    narration_probe = MediaProbe(
        plan.sources[2], 2000, "wav", duration, 1536000, narration_streams
    )
    return PreflightReport(plan.plan_id, True, (), (*video_probes, narration_probe))


class SequenceWorkflowTests(unittest.TestCase):
    def test_composes_valid_narration_and_preserves_persisted_policy(self):
        plan = narrated_plan()

        def compose(clips, output, **kwargs):
            self.assertEqual(kwargs["narration_path"], plan.sources[2])
            return SequenceArtifact(
                plan.sources[:2], plan.output_path, 3.5, 700, plan.sources[2]
            )

        artifact = SequenceArtifact(
            plan.sources[:2], plan.output_path, 3.5, 700, plan.sources[2]
        )
        report = run_sequence_workflow(
            plan,
            preflight=narrated_preflight,
            compose=compose,
            validate=lambda value, **_: SequenceValidationReport(
                True, value, 3.5, 0.15, (), None
            ),
        )

        self.assertTrue(report.valid)
        self.assertEqual(report.artifact, artifact)
        self.assertEqual(report.operation_ids[-1], "voice-1")

    def test_rejects_invalid_narration_stream_policy_or_duration_before_composition(self):
        cases = (
            (narrated_plan(narration_parameters={}), None, "duration_policy"),
            (narrated_plan(), narrated_preflight(narrated_plan(), audio=False), "one audio stream"),
            (narrated_plan(), narrated_preflight(narrated_plan(), duration=4.0), "must match timeline"),
        )
        for plan, report, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(SequenceWorkflowError, message):
                    run_sequence_workflow(
                        plan,
                        preflight=(lambda _: report) if report is not None else None,
                        compose=lambda *_args, **_kwargs: self.fail("must not compose"),
                    )

    def test_wraps_ffmpeg_failure_without_claiming_a_report(self):
        plan = narrated_plan()

        def fail(*_args, **_kwargs):
            raise FFmpegError("encoder failed")

        with self.assertRaisesRegex(SequenceWorkflowError, "encoder failed"):
            run_sequence_workflow(
                plan,
                preflight=narrated_preflight,
                compose=fail,
            )

    def test_runs_preflight_composition_and_validation_in_clip_order(self):
        plan = sequence_plan()
        calls = []

        def compose(clips, output, **kwargs):
            calls.append("compose")
            self.assertEqual(tuple(clip.source_path for clip in clips), plan.sources)
            self.assertEqual(tuple((clip.start_seconds, clip.end_seconds) for clip in clips), ((1, 2.5), (0, 2)))
            self.assertEqual(output, plan.output_path)
            self.assertEqual(kwargs["timeout_seconds"], 30)
            return SequenceArtifact(plan.sources, plan.output_path, 3.5, 500)

        def validate(artifact, **kwargs):
            calls.append("validate")
            self.assertEqual(kwargs["duration_tolerance_seconds"], 0.2)
            return SequenceValidationReport(True, artifact, 3.5, 0.2, (), None)

        report = run_sequence_workflow(
            plan,
            timeout_seconds=30,
            duration_tolerance_seconds=0.2,
            preflight=lambda value: valid_preflight(value),
            before_compose=lambda _: calls.append("fingerprint"),
            compose=compose,
            validate=validate,
        )

        self.assertEqual(calls, ["fingerprint", "compose", "validate"])
        self.assertTrue(report.valid)
        self.assertEqual(report.operation_ids, ("clip-1", "clip-2"))

    def test_rejects_unsupported_plans_before_preflight(self):
        for plan, message in (
            (sequence_plan(second_kind="extract_segment"), "does not support"),
            (sequence_plan(second_parameters={"transition": "fade"}), "does not accept parameters"),
        ):
            with self.subTest(message=message):
                with self.assertRaisesRegex(SequenceWorkflowError, message):
                    run_sequence_workflow(plan, preflight=lambda _: self.fail("must not preflight"))

    def test_rejects_mismatched_dimensions_before_composition(self):
        plan = sequence_plan()

        with self.assertRaisesRegex(SequenceWorkflowError, "matching source dimensions"):
            run_sequence_workflow(
                plan,
                preflight=lambda value: valid_preflight(value, second_width=1920),
                compose=lambda *_args, **_kwargs: self.fail("must not compose"),
            )

    def test_rejects_inconsistent_artifact_metadata(self):
        plan = sequence_plan()
        bad_artifact = SequenceArtifact(tuple(reversed(plan.sources)), plan.output_path, 3.5, 500)

        with self.assertRaisesRegex(SequenceWorkflowError, "unexpected clip order"):
            run_sequence_workflow(
                plan,
                preflight=lambda value: valid_preflight(value),
                compose=lambda *_args, **_kwargs: bad_artifact,
            )

    def test_rejects_inconsistent_narration_artifact_metadata(self):
        plan = narrated_plan()
        bad_artifact = SequenceArtifact(
            plan.sources[:2],
            plan.output_path,
            3.5,
            500,
            str(Path("inputs/other-narration.wav").resolve()),
        )

        with self.assertRaisesRegex(SequenceWorkflowError, "unexpected narration metadata"):
            run_sequence_workflow(
                plan,
                preflight=narrated_preflight,
                compose=lambda *_args, **_kwargs: bad_artifact,
            )


if __name__ == "__main__":
    unittest.main()
