import tempfile
import unittest
from pathlib import Path

from video_generator.adapters import FFmpegError, MediaProbe, SequenceArtifact, StreamProbe
from video_generator.domain import EditOperation, EditPlan
from video_generator.validation import (
    PreflightReport,
    SequenceValidationIssue,
    SequenceValidationReport,
)
from video_generator.workflows import (
    SequenceWorkflowError,
    run_final_sequence_workflow,
    run_sequence_workflow,
)


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


def captioned_plan(*, caption_parameters=None) -> EditPlan:
    narrated = narrated_plan()
    parameters = caption_parameters or {
        "style": "bottom_box",
        "items": [
            {"text": "First thought", "start_seconds": 0, "end_seconds": 1.5},
            {"text": "Second thought", "start_seconds": 1.5, "end_seconds": 3.5},
        ],
    }
    return EditPlan(
        narrated.plan_id,
        narrated.brief_id,
        narrated.sources,
        narrated.output_path,
        (
            *narrated.operations[:-1],
            EditOperation("captions-1", "captions", parameters=parameters),
            narrated.operations[-1],
        ),
    )


def music_plan(*, music_parameters=None) -> EditPlan:
    captioned = captioned_plan()
    music = str(Path("inputs/music.wav").resolve())
    return EditPlan(
        captioned.plan_id,
        captioned.brief_id,
        (*captioned.sources, music),
        captioned.output_path,
        (
            *captioned.operations[:-1],
            EditOperation(
                "music-1",
                "music",
                music,
                parameters=(
                    {"duration_policy": "loop_to_timeline", "gain_db": -18}
                    if music_parameters is None
                    else music_parameters
                ),
            ),
            captioned.operations[-1],
        ),
    )


def music_preflight(plan: EditPlan, *, audio: bool = True) -> PreflightReport:
    base = narrated_preflight(plan)
    streams = (
        (StreamProbe(0, "audio", "pcm_s16le", 0.75, None, None, 48000, 2),)
        if audio
        else (StreamProbe(0, "video", "h264", 0.75, 1280, 720, None, None),)
    )
    music_probe = MediaProbe(plan.sources[3], 1500, "wav", 0.75, 1536000, streams)
    return PreflightReport(plan.plan_id, True, (), (*base.sources, music_probe))


class SequenceWorkflowTests(unittest.TestCase):
    def test_stages_validates_and_exclusively_publishes_final_mp4(self):
        with tempfile.TemporaryDirectory() as directory:
            base = sequence_plan()
            final_path = Path(directory) / "final.mp4"
            plan = EditPlan(
                base.plan_id,
                base.brief_id,
                base.sources,
                str(final_path),
                base.operations,
            )
            validated_paths = []
            fingerprint_plans = []

            def compose(clips, output, **_kwargs):
                Path(output).write_bytes(b"validated final render")
                return SequenceArtifact(
                    tuple(clip.source_path for clip in clips),
                    str(Path(output).resolve()),
                    3.5,
                    Path(output).stat().st_size,
                )

            def validate(artifact, **_kwargs):
                validated_paths.append(artifact.output_path)
                return SequenceValidationReport(True, artifact, 3.5, 0.15, (), None)

            report = run_final_sequence_workflow(
                plan,
                preflight=valid_preflight,
                before_compose=lambda value: fingerprint_plans.append(value),
                compose=compose,
                validate=validate,
            )

            self.assertEqual(report.publication, "final")
            self.assertEqual(report.artifact.output_path, str(final_path.resolve()))
            self.assertEqual(final_path.read_bytes(), b"validated final render")
            self.assertEqual(len(validated_paths), 2)
            self.assertNotEqual(validated_paths[0], str(final_path.resolve()))
            self.assertEqual(validated_paths[1], str(final_path.resolve()))
            self.assertEqual(fingerprint_plans, [plan])
            self.assertEqual(list(Path(directory).glob(".final-staging-*")), [])

    def test_refuses_or_removes_unvalidated_final_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            base = sequence_plan()
            final_path = Path(directory) / "final.mp4"
            plan = EditPlan(
                base.plan_id,
                base.brief_id,
                base.sources,
                str(final_path),
                base.operations,
            )

            def compose(clips, output, **_kwargs):
                Path(output).write_bytes(b"invalid render")
                return SequenceArtifact(
                    tuple(clip.source_path for clip in clips),
                    str(Path(output).resolve()),
                    3.5,
                    Path(output).stat().st_size,
                )

            def reject(artifact, **_kwargs):
                return SequenceValidationReport(
                    False,
                    artifact,
                    4.0,
                    0.15,
                    (SequenceValidationIssue("duration_mismatch", "duration differs"),),
                    None,
                )

            with self.assertRaisesRegex(SequenceWorkflowError, "duration_mismatch"):
                run_final_sequence_workflow(
                    plan,
                    preflight=valid_preflight,
                    compose=compose,
                    validate=reject,
                )

            self.assertFalse(final_path.exists())
            self.assertEqual(list(Path(directory).glob(".final-staging-*")), [])

            validation_calls = 0

            def reject_published(artifact, **_kwargs):
                nonlocal validation_calls
                validation_calls += 1
                if validation_calls == 1:
                    return SequenceValidationReport(True, artifact, 3.5, 0.15, (), None)
                return reject(artifact)

            with self.assertRaisesRegex(SequenceWorkflowError, "published final"):
                run_final_sequence_workflow(
                    plan,
                    preflight=valid_preflight,
                    compose=compose,
                    validate=reject_published,
                )

            self.assertFalse(final_path.exists())
            self.assertEqual(list(Path(directory).glob(".final-staging-*")), [])

            final_path.write_bytes(b"existing final")
            with self.assertRaisesRegex(SequenceWorkflowError, "already exists"):
                run_final_sequence_workflow(
                    plan,
                    preflight=lambda _: self.fail("must not preflight"),
                )
            self.assertEqual(final_path.read_bytes(), b"existing final")

    def test_final_publication_requires_the_canonical_filename(self):
        plan = sequence_plan()

        with self.assertRaisesRegex(SequenceWorkflowError, "named final.mp4"):
            run_final_sequence_workflow(
                plan,
                preflight=lambda _: self.fail("must not preflight"),
            )

        final_plan = EditPlan(
            plan.plan_id,
            plan.brief_id,
            plan.sources,
            str(Path("output/final.mp4").resolve()),
            plan.operations,
        )
        with self.assertRaisesRegex(SequenceWorkflowError, "run_final_sequence_workflow"):
            run_sequence_workflow(
                final_plan,
                preflight=lambda _: self.fail("must not preflight"),
            )

    def test_mixes_persisted_looped_music_under_captioned_narration(self):
        plan = music_plan()

        def compose(clips, output, **kwargs):
            self.assertEqual(kwargs["music_path"], plan.sources[3])
            self.assertEqual(kwargs["music_gain_db"], -18.0)
            return SequenceArtifact(
                source_paths=plan.sources[:2],
                output_path=plan.output_path,
                duration_seconds=3.5,
                file_size_bytes=900,
                narration_source_path=plan.sources[2],
                caption_count=2,
                music_source_path=plan.sources[3],
                music_gain_db=-18.0,
            )

        report = run_sequence_workflow(
            plan,
            preflight=music_preflight,
            compose=compose,
            validate=lambda value, **_: SequenceValidationReport(
                True, value, 3.5, 0.15, (), None
            ),
        )

        self.assertTrue(report.valid)
        self.assertEqual(report.operation_ids[-2:], ("music-1", "voice-1"))
        self.assertEqual(report.artifact.music_gain_db, -18.0)

    def test_rejects_invalid_music_policy_gain_or_stream_before_composition(self):
        for parameters, message in (
            ({"duration_policy": "match_timeline", "gain_db": -18}, "loop_to_timeline"),
            ({"duration_policy": "loop_to_timeline", "gain_db": 1}, "from -60 to 0"),
        ):
            with self.subTest(message=message):
                with self.assertRaisesRegex(SequenceWorkflowError, message):
                    run_sequence_workflow(
                        music_plan(music_parameters=parameters),
                        preflight=lambda _: self.fail("must not preflight invalid music"),
                        compose=lambda *_args, **_kwargs: self.fail("must not compose"),
                    )

        plan = music_plan()
        with self.assertRaisesRegex(SequenceWorkflowError, "one audio stream"):
            run_sequence_workflow(
                plan,
                preflight=lambda value: music_preflight(value, audio=False),
                compose=lambda *_args, **_kwargs: self.fail("must not compose"),
            )

    def test_burns_persisted_caption_track_before_matched_narration(self):
        plan = captioned_plan()

        def compose(clips, output, **kwargs):
            cues = kwargs["captions"]
            self.assertEqual([cue.text for cue in cues], ["First thought", "Second thought"])
            self.assertEqual(kwargs["narration_path"], plan.sources[2])
            return SequenceArtifact(
                plan.sources[:2], plan.output_path, 3.5, 700, plan.sources[2], 2
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
        self.assertEqual(report.artifact.caption_count, 2)
        self.assertEqual(report.operation_ids[-2:], ("captions-1", "voice-1"))

    def test_rejects_invalid_caption_track_before_composition(self):
        cases = (
            ({"style": "unknown", "items": []}, "style=bottom_box"),
            (
                {
                    "style": "bottom_box",
                    "items": [
                        {"text": "late", "start_seconds": 3, "end_seconds": 4}
                    ],
                },
                "must not exceed",
            ),
            (
                {
                    "style": "bottom_box",
                    "items": [
                        {"text": "first", "start_seconds": 0, "end_seconds": 2},
                        {"text": "overlap", "start_seconds": 1, "end_seconds": 3},
                    ],
                },
                "non-overlapping",
            ),
            (
                {
                    "style": "bottom_box",
                    "items": [
                        {"text": "<b>styled</b>", "start_seconds": 0, "end_seconds": 1}
                    ],
                },
                "markup",
            ),
        )
        for parameters, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(SequenceWorkflowError, message):
                    run_sequence_workflow(
                        captioned_plan(caption_parameters=parameters),
                        preflight=lambda _: self.fail("must not preflight invalid captions"),
                        compose=lambda *_args, **_kwargs: self.fail("must not compose"),
                    )

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

    def test_rejects_inconsistent_caption_artifact_metadata(self):
        plan = captioned_plan()
        bad_artifact = SequenceArtifact(
            plan.sources[:2], plan.output_path, 3.5, 500, plan.sources[2], 1
        )

        with self.assertRaisesRegex(SequenceWorkflowError, "unexpected caption metadata"):
            run_sequence_workflow(
                plan,
                preflight=narrated_preflight,
                compose=lambda *_args, **_kwargs: bad_artifact,
            )

    def test_rejects_inconsistent_music_artifact_metadata(self):
        plan = music_plan()
        bad_artifact = SequenceArtifact(
            source_paths=plan.sources[:2],
            output_path=plan.output_path,
            duration_seconds=3.5,
            file_size_bytes=500,
            narration_source_path=plan.sources[2],
            caption_count=2,
            music_source_path=plan.sources[3],
            music_gain_db=-6.0,
        )

        with self.assertRaisesRegex(SequenceWorkflowError, "unexpected music metadata"):
            run_sequence_workflow(
                plan,
                preflight=music_preflight,
                compose=lambda *_args, **_kwargs: bad_artifact,
            )

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
