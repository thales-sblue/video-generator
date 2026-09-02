import tempfile
import unittest
from hashlib import sha256
from pathlib import Path
from unittest.mock import Mock, patch

from video_generator.adapters import (
    FFmpegError,
    KokoroError,
    MediaProbe,
    NarrationArtifact,
    SequenceArtifact,
    StreamProbe,
)
from video_generator.domain import EditOperation, EditPlan
from video_generator.workflows import sequence as sequence_module
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


def fade_plan(*, fade_parameters=None, fade_kwargs=None, place_after_narration=False) -> EditPlan:
    narrated = narrated_plan()
    fade_op = EditOperation(
        "fade-1",
        "fade",
        parameters=(
            {"from_black_seconds": 1, "to_black_seconds": 1.5}
            if fade_parameters is None
            else fade_parameters
        ),
        **(fade_kwargs or {}),
    )
    operations = (
        (*narrated.operations, fade_op)
        if place_after_narration
        else (*narrated.operations[:-1], fade_op, narrated.operations[-1])
    )
    return EditPlan(
        narrated.plan_id,
        narrated.brief_id,
        narrated.sources,
        narrated.output_path,
        operations,
    )


def image_plan(*, image_parameters=None, image_kind: str = "image_clip", **operation_kwargs) -> EditPlan:
    clip_source = str(Path("inputs/first.mp4").resolve())
    image_source = str(Path("inputs/card.png").resolve())
    return EditPlan(
        "plan-image",
        "brief-dark",
        (clip_source, image_source),
        str(Path("output/timeline.mp4").resolve()),
        (
            EditOperation("clip-1", "sequence_clip", clip_source, 1, 2.5),
            EditOperation(
                "image-1",
                image_kind,
                image_source,
                parameters=(
                    {"duration_seconds": 4} if image_parameters is None else image_parameters
                ),
                **operation_kwargs,
            ),
        ),
    )


def image_preflight(plan: EditPlan, *, image_width: int = 1280) -> PreflightReport:
    clip_probe = MediaProbe(
        plan.sources[0],
        1000,
        "mov,mp4",
        10,
        800000,
        (StreamProbe(0, "video", "h264", 10, 1280, 720, None, None),),
    )
    image_probe = MediaProbe(
        plan.sources[1],
        5000,
        "png_pipe",
        None,
        None,
        (StreamProbe(0, "video", "png", None, image_width, 720, None, None),),
    )
    return PreflightReport(plan.plan_id, True, (), (clip_probe, image_probe))


def caption_file_plan(directory, *, srt_name="cues.srt", parameters=None):
    root = Path(directory)
    first = str((root / "first.mp4").resolve())
    second = str((root / "second.mp4").resolve())
    subtitles = str((root / srt_name).resolve())
    return EditPlan(
        "plan-caption-file",
        "brief-dark",
        (first, second, subtitles),
        str((root / "timeline.mp4").resolve()),
        (
            EditOperation("clip-1", "sequence_clip", first, 0, 1.5),
            EditOperation("clip-2", "sequence_clip", second, 0, 2),
            EditOperation(
                "captions-1",
                "captions",
                subtitles,
                parameters=parameters if parameters is not None else {"style": "bottom_box"},
            ),
        ),
    )


def caption_file_preflight(plan):
    video = tuple(
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
    subtitles = MediaProbe(
        plan.sources[2],
        88,
        "srt",
        None,
        None,
        (StreamProbe(0, "subtitle", "subrip", None, None, None, None, None),),
    )
    return PreflightReport(plan.plan_id, True, (), (*video, subtitles))


def narrated_text_plan(*, params=None):
    silent = sequence_plan()
    return EditPlan(
        silent.plan_id,
        silent.brief_id,
        silent.sources,
        silent.output_path,
        (
            *silent.operations,
            EditOperation(
                "voice-1",
                "narration",
                parameters=params
                if params is not None
                else {"duration_policy": "match_timeline", "text": "Hello dark world."},
            ),
        ),
    )


def narrated_text_music_plan(*, music_parameters=None, narration_parameters=None) -> EditPlan:
    spoken = narrated_text_plan(params=narration_parameters)
    music = str(Path("inputs/music.wav").resolve())
    return EditPlan(
        spoken.plan_id,
        spoken.brief_id,
        (*spoken.sources, music),
        spoken.output_path,
        (
            *spoken.operations[:-1],
            EditOperation(
                "music-1",
                "music",
                music,
                parameters=(
                    {"duration_policy": "loop_to_timeline", "gain_db": -18, "duck_db": -9}
                    if music_parameters is None
                    else music_parameters
                ),
            ),
            spoken.operations[-1],
        ),
    )


def narrated_text_music_preflight(plan: EditPlan) -> PreflightReport:
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
    music_probe = MediaProbe(
        plan.sources[2],
        1500,
        "wav",
        0.75,
        1536000,
        (StreamProbe(0, "audio", "pcm_s16le", 0.75, None, None, 48000, 2),),
    )
    return PreflightReport(plan.plan_id, True, (), (*video_probes, music_probe))


def _fake_synth(text, target, *, voice, speed, lang, timeout_seconds):
    return NarrationArtifact(
        output_path=str(target),
        voice=voice,
        speed=speed,
        lang=lang,
        sample_rate_hz=48000,
        channels=2,
        text_sha256=sha256(text.encode("utf-8")).hexdigest(),
        file_size_bytes=2048,
    )


def _probe_of(seconds):
    def _probe(path):
        return MediaProbe(
            str(path),
            2048,
            "wav",
            seconds,
            None,
            (StreamProbe(0, "audio", "pcm_s16le", seconds, None, None, 48000, 2),),
        )

    return _probe


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

    def test_passes_persisted_music_fades_to_composition(self):
        plan = music_plan(
            music_parameters={
                "duration_policy": "loop_to_timeline",
                "gain_db": -14,
                "fade_in_seconds": 0.5,
                "fade_out_seconds": 2,
            }
        )
        seen = {}

        def compose(clips, output, **kwargs):
            seen.update(
                {k: kwargs.get(k) for k in ("music_fade_in_seconds", "music_fade_out_seconds")}
            )
            return SequenceArtifact(
                plan.sources[:2],
                plan.output_path,
                3.5,
                900,
                narration_source_path=plan.sources[2],
                caption_count=2,
                music_source_path=plan.sources[3],
                music_gain_db=-14.0,
                music_fade_in_seconds=0.5,
                music_fade_out_seconds=2.0,
            )

        report = run_sequence_workflow(
            plan,
            preflight=music_preflight,
            compose=compose,
            validate=lambda v, **_: SequenceValidationReport(True, v, 3.5, 0.15, (), None),
        )

        self.assertTrue(report.valid)
        self.assertEqual(seen, {"music_fade_in_seconds": 0.5, "music_fade_out_seconds": 2.0})
        self.assertEqual(report.artifact.music_fade_out_seconds, 2.0)

    def test_ducks_the_bed_under_a_narration_file_for_its_probed_length(self):
        plan = music_plan(
            music_parameters={
                "duration_policy": "loop_to_timeline",
                "gain_db": -14,
                "duck_db": -9,
            }
        )
        seen = {}

        def compose(clips, output, **kwargs):
            seen.update(
                {k: kwargs.get(k) for k in ("music_duck_db", "narration_duration_seconds")}
            )
            return SequenceArtifact(
                plan.sources[:2],
                plan.output_path,
                3.5,
                900,
                narration_source_path=plan.sources[2],
                caption_count=2,
                music_source_path=plan.sources[3],
                music_gain_db=-14.0,
                music_duck_db=-9.0,
            )

        report = run_sequence_workflow(
            plan,
            preflight=music_preflight,
            compose=compose,
            validate=lambda v, **_: SequenceValidationReport(True, v, 3.5, 0.15, (), None),
        )

        self.assertTrue(report.valid)
        # a recorded track matches the timeline, so the duck runs its full length
        self.assertEqual(seen, {"music_duck_db": -9.0, "narration_duration_seconds": 3.5})
        self.assertEqual(report.artifact.music_duck_db, -9.0)

    def test_ducks_the_bed_only_over_the_synthesised_voice(self):
        plan = narrated_text_music_plan(
            narration_parameters={
                "duration_policy": "match_timeline",
                "text": "Hello dark world.",
                "lead_in_seconds": 0.5,
            }
        )
        seen = {}

        def compose(clips, output, **kwargs):
            seen.update(
                {
                    k: kwargs.get(k)
                    for k in (
                        "music_duck_db",
                        "narration_duration_seconds",
                        "narration_lead_in_seconds",
                    )
                }
            )
            return SequenceArtifact(
                tuple(clip.source_path for clip in clips),
                plan.output_path,
                3.5,
                900,
                narration_source_path=kwargs.get("narration_path"),
                music_source_path=plan.sources[2],
                music_gain_db=-18.0,
                music_duck_db=-9.0,
                narration_lead_in_seconds=0.5,
            )

        with patch.object(sequence_module, "synthesize_narration", side_effect=_fake_synth), \
                patch.object(sequence_module, "probe_media", side_effect=_probe_of(2.0)):
            report = run_sequence_workflow(
                plan,
                preflight=narrated_text_music_preflight,
                compose=compose,
                validate=lambda v, **_: SequenceValidationReport(True, v, 3.5, 0.15, (), None),
            )

        self.assertTrue(report.valid)
        # the bed dips for the 2 s of voice that start at 0.5 s and comes back
        # up for the tail of the 3.5 s timeline
        self.assertEqual(
            seen,
            {
                "music_duck_db": -9.0,
                "narration_duration_seconds": 2.0,
                "narration_lead_in_seconds": 0.5,
            },
        )
        self.assertEqual(report.artifact.music_duck_db, -9.0)

    def test_rejects_ducking_without_narration_or_with_an_invalid_level(self):
        for parameters, message in (
            (
                {"duration_policy": "loop_to_timeline", "gain_db": -18, "duck_db": 0},
                "from -60 to less than 0",
            ),
            (
                {"duration_policy": "loop_to_timeline", "gain_db": -18, "duck_db": -61},
                "from -60 to less than 0",
            ),
            (
                {"duration_policy": "loop_to_timeline", "gain_db": -18, "duck_db": "loud"},
                "from -60 to less than 0",
            ),
        ):
            with self.subTest(message=message):
                with self.assertRaisesRegex(SequenceWorkflowError, message):
                    run_sequence_workflow(
                        music_plan(music_parameters=parameters),
                        preflight=lambda _: self.fail("must not preflight invalid music"),
                        compose=lambda *_a, **_k: self.fail("must not compose"),
                    )

        silent = sequence_plan()
        music = str(Path("inputs/music.wav").resolve())
        without_voice = EditPlan(
            silent.plan_id,
            silent.brief_id,
            (*silent.sources, music),
            silent.output_path,
            (
                *silent.operations,
                EditOperation(
                    "music-1",
                    "music",
                    music,
                    parameters={
                        "duration_policy": "loop_to_timeline",
                        "gain_db": -18,
                        "duck_db": -9,
                    },
                ),
            ),
        )
        with self.assertRaisesRegex(SequenceWorkflowError, "duck_db requires a narration"):
            run_sequence_workflow(
                without_voice,
                preflight=lambda _: self.fail("must not preflight"),
                compose=lambda *_a, **_k: self.fail("must not compose"),
            )

    def test_rejects_an_artifact_that_ignored_the_planned_duck(self):
        plan = music_plan(
            music_parameters={
                "duration_policy": "loop_to_timeline",
                "gain_db": -18,
                "duck_db": -9,
            }
        )
        bad_artifact = SequenceArtifact(
            source_paths=plan.sources[:2],
            output_path=plan.output_path,
            duration_seconds=3.5,
            file_size_bytes=500,
            narration_source_path=plan.sources[2],
            caption_count=2,
            music_source_path=plan.sources[3],
            music_gain_db=-18.0,
        )

        with self.assertRaisesRegex(SequenceWorkflowError, "unexpected music metadata"):
            run_sequence_workflow(
                plan,
                preflight=music_preflight,
                compose=lambda *_args, **_kwargs: bad_artifact,
            )

    def test_rejects_music_fades_longer_than_the_timeline(self):
        plan = music_plan(
            music_parameters={
                "duration_policy": "loop_to_timeline",
                "gain_db": -14,
                "fade_in_seconds": 2,
                "fade_out_seconds": 2,
            }
        )
        with self.assertRaisesRegex(SequenceWorkflowError, "fades must not be longer"):
            run_sequence_workflow(
                plan,
                preflight=lambda _: self.fail("must not preflight"),
                compose=lambda *_a, **_k: self.fail("must not compose"),
            )

    def test_passes_persisted_video_fades_to_composition(self):
        plan = fade_plan(
            fade_parameters={"from_black_seconds": 1, "to_black_seconds": 1.5}
        )
        seen = {}

        def compose(clips, output, **kwargs):
            seen.update(
                {
                    key: kwargs.get(key)
                    for key in ("video_fade_in_seconds", "video_fade_out_seconds")
                }
            )
            return SequenceArtifact(
                plan.sources[:2],
                plan.output_path,
                3.5,
                900,
                narration_source_path=plan.sources[2],
                video_fade_in_seconds=1.0,
                video_fade_out_seconds=1.5,
            )

        report = run_sequence_workflow(
            plan,
            preflight=narrated_preflight,
            compose=compose,
            validate=lambda v, **_: SequenceValidationReport(True, v, 3.5, 0.15, (), None),
        )

        self.assertTrue(report.valid)
        self.assertEqual(
            seen, {"video_fade_in_seconds": 1.0, "video_fade_out_seconds": 1.5}
        )
        self.assertEqual(report.artifact.video_fade_out_seconds, 1.5)
        self.assertIn("fade-1", report.operation_ids)

    def test_accepts_a_one_sided_video_fade(self):
        plan = fade_plan(fade_parameters={"from_black_seconds": 2})
        seen = {}

        def compose(clips, output, **kwargs):
            seen.update(
                {
                    key: kwargs.get(key)
                    for key in ("video_fade_in_seconds", "video_fade_out_seconds")
                }
            )
            return SequenceArtifact(
                plan.sources[:2],
                plan.output_path,
                3.5,
                900,
                narration_source_path=plan.sources[2],
                video_fade_in_seconds=2.0,
                video_fade_out_seconds=0.0,
            )

        run_sequence_workflow(
            plan,
            preflight=narrated_preflight,
            compose=compose,
            validate=lambda v, **_: SequenceValidationReport(True, v, 3.5, 0.15, (), None),
        )

        self.assertEqual(seen, {"video_fade_in_seconds": 2.0, "video_fade_out_seconds": 0.0})

    def test_rejects_video_fades_longer_than_the_timeline(self):
        plan = fade_plan(
            fade_parameters={"from_black_seconds": 2, "to_black_seconds": 2}
        )
        with self.assertRaisesRegex(SequenceWorkflowError, "fade must not be longer"):
            run_sequence_workflow(
                plan,
                preflight=lambda _: self.fail("must not preflight"),
                compose=lambda *_a, **_k: self.fail("must not compose"),
            )

    def test_rejects_a_malformed_or_misplaced_fade_before_composition(self):
        cases = (
            (
                fade_plan(fade_parameters={"from_black_seconds": 1, "extra": 1}),
                "fade accepts only",
            ),
            (fade_plan(fade_parameters={}), "fade accepts only"),
            (
                fade_plan(fade_parameters={"from_black_seconds": 0, "to_black_seconds": 0}),
                "fade requires a positive",
            ),
            (
                fade_plan(fade_parameters={"from_black_seconds": -1}),
                "from_black_seconds",
            ),
            (
                fade_plan(fade_kwargs={"source": str(Path("inputs/narration.wav").resolve())}),
                "fade does not take a source",
            ),
            (
                fade_plan(fade_kwargs={"start_seconds": 0, "end_seconds": 1}),
                "fade has no timeline range",
            ),
            (fade_plan(place_after_narration=True), "narration must be the final operation"),
        )
        for plan, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(SequenceWorkflowError, message):
                    run_sequence_workflow(
                        plan,
                        preflight=lambda _: self.fail("must not preflight invalid fade"),
                        compose=lambda *_a, **_k: self.fail("must not compose invalid fade"),
                    )

    def test_rejects_more_than_one_fade_operation(self):
        plan = fade_plan()
        plan = EditPlan(
            plan.plan_id,
            plan.brief_id,
            plan.sources,
            plan.output_path,
            (
                *plan.operations[:-1],
                EditOperation("fade-2", "fade", parameters={"to_black_seconds": 1}),
                plan.operations[-1],
            ),
        )
        with self.assertRaisesRegex(SequenceWorkflowError, "at most one fade"):
            run_sequence_workflow(
                plan,
                preflight=lambda _: self.fail("must not preflight"),
                compose=lambda *_a, **_k: self.fail("must not compose"),
            )

    def test_rejects_invalid_music_policy_gain_or_stream_before_composition(self):
        for parameters, message in (
            ({"duration_policy": "match_timeline", "gain_db": -18}, "loop_to_timeline"),
            ({"duration_policy": "loop_to_timeline", "gain_db": 1}, "from -60 to 0"),
            (
                {"duration_policy": "loop_to_timeline", "gain_db": -6, "wobble": 1},
                "optional fade_in_seconds",
            ),
            (
                {"duration_policy": "loop_to_timeline", "gain_db": -6, "fade_in_seconds": -1},
                "fade_in_seconds",
            ),
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
            (sequence_plan(second_parameters={"transition": "fade"}), "only an optional fit parameter"),
        ):
            with self.subTest(message=message):
                with self.assertRaisesRegex(SequenceWorkflowError, message):
                    run_sequence_workflow(plan, preflight=lambda _: self.fail("must not preflight"))

    def test_rejects_mismatched_dimensions_before_composition(self):
        plan = sequence_plan()

        with self.assertRaisesRegex(SequenceWorkflowError, "matching clip dimensions"):
            run_sequence_workflow(
                plan,
                preflight=lambda value: valid_preflight(value, second_width=1920),
                compose=lambda *_args, **_kwargs: self.fail("must not compose"),
            )

    def test_accepts_a_still_image_segment_and_extends_the_timeline(self):
        plan = image_plan()
        seen = {}

        def compose(clips, output, **kwargs):
            seen["kinds"] = [type(clip).__name__ for clip in clips]
            seen["sources"] = tuple(clip.source_path for clip in clips)
            seen["canvas"] = kwargs.get("canvas")
            return SequenceArtifact(
                tuple(clip.source_path for clip in clips),
                plan.output_path,
                5.5,
                500,
                image_count=1,
            )

        def validate(artifact, **_kwargs):
            return SequenceValidationReport(True, artifact, 5.5, 0.15, (), None)

        report = run_sequence_workflow(
            plan, preflight=image_preflight, compose=compose, validate=validate
        )

        self.assertTrue(report.valid)
        self.assertEqual(seen["kinds"], ["SequenceClip", "SequenceImage"])
        self.assertEqual(seen["sources"], plan.sources)
        self.assertEqual(seen["canvas"], (1280, 720))
        # 1.5 s of clip + 4 s of still
        self.assertEqual(report.artifact.duration_seconds, 5.5)
        self.assertEqual(report.artifact.image_count, 1)

    def test_rejects_an_image_clip_with_an_invalid_duration(self):
        for parameters, message in (
            ({}, "duration_seconds"),
            ({"duration_seconds": 0}, "positive"),
            ({"duration_seconds": -2}, "positive"),
            ({"duration_seconds": 4, "loop": True}, "an optional fit"),
            ({"duration_seconds": 100000}, "must not exceed"),
        ):
            with self.subTest(parameters=parameters):
                with self.assertRaisesRegex(SequenceWorkflowError, message):
                    run_sequence_workflow(
                        image_plan(image_parameters=parameters),
                        preflight=lambda _: self.fail("must not preflight"),
                        compose=lambda *_a, **_k: self.fail("must not compose"),
                    )

    def test_rejects_an_image_clip_that_declares_a_timeline_range(self):
        with self.assertRaisesRegex(SequenceWorkflowError, "no timeline range"):
            run_sequence_workflow(
                image_plan(start_seconds=0, end_seconds=4),
                preflight=lambda _: self.fail("must not preflight"),
                compose=lambda *_a, **_k: self.fail("must not compose"),
            )

    def test_rejects_a_timeline_without_any_video_clip(self):
        first_image = str(Path("inputs/card-a.png").resolve())
        second_image = str(Path("inputs/card-b.png").resolve())
        plan = EditPlan(
            "plan-image-only",
            "brief-dark",
            (first_image, second_image),
            str(Path("output/timeline.mp4").resolve()),
            (
                EditOperation("image-1", "image_clip", first_image, parameters={"duration_seconds": 3}),
                EditOperation("image-2", "image_clip", second_image, parameters={"duration_seconds": 3}),
            ),
        )

        with self.assertRaisesRegex(SequenceWorkflowError, "at least one sequence_clip"):
            run_sequence_workflow(
                plan,
                preflight=lambda _: self.fail("must not preflight"),
                compose=lambda *_a, **_k: self.fail("must not compose"),
            )

    def test_letterboxes_an_image_whose_dimensions_differ_from_the_clips(self):
        seen = {}

        def compose(clips, output, **kwargs):
            seen["canvas"] = kwargs.get("canvas")
            return SequenceArtifact(
                tuple(clip.source_path for clip in clips),
                image_plan().output_path,
                5.5,
                500,
                image_count=1,
            )

        report = run_sequence_workflow(
            image_plan(),
            preflight=lambda value: image_preflight(value, image_width=1920),
            compose=compose,
            validate=lambda artifact, **_k: SequenceValidationReport(
                True, artifact, 5.5, 0.15, (), None
            ),
        )

        self.assertTrue(report.valid)
        # the clips define the canvas; the 1920-wide still is scaled onto it
        self.assertEqual(seen["canvas"], (1280, 720))

    def test_still_rejects_clips_that_do_not_share_dimensions(self):
        clip_a = str(Path("inputs/first.mp4").resolve())
        clip_b = str(Path("inputs/second.mp4").resolve())
        image = str(Path("inputs/card.png").resolve())
        plan = EditPlan(
            "plan-bad-clips",
            "brief-dark",
            (clip_a, clip_b, image),
            str(Path("output/timeline.mp4").resolve()),
            (
                EditOperation("clip-1", "sequence_clip", clip_a, 0, 1),
                EditOperation("clip-2", "sequence_clip", clip_b, 0, 1),
                EditOperation("image-1", "image_clip", image, parameters={"duration_seconds": 1}),
            ),
        )

        def preflight(_):
            probes = (
                MediaProbe(clip_a, 1, "mp4", 10, 1, (StreamProbe(0, "video", "h264", 10, 1280, 720, None, None),)),
                MediaProbe(clip_b, 1, "mp4", 10, 1, (StreamProbe(0, "video", "h264", 10, 1920, 1080, None, None),)),
                MediaProbe(image, 1, "png_pipe", None, None, (StreamProbe(0, "video", "png", None, 800, 600, None, None),)),
            )
            return PreflightReport(plan.plan_id, True, (), probes)

        with self.assertRaisesRegex(SequenceWorkflowError, "matching clip dimensions"):
            run_sequence_workflow(
                plan, preflight=preflight, compose=lambda *_a, **_k: self.fail("must not compose")
            )

    def test_rejects_inconsistent_image_artifact_metadata(self):
        plan = image_plan()
        bad_artifact = SequenceArtifact(plan.sources, plan.output_path, 5.5, 500, image_count=0)

        with self.assertRaisesRegex(SequenceWorkflowError, "unexpected image metadata"):
            run_sequence_workflow(
                plan,
                preflight=image_preflight,
                compose=lambda *_a, **_k: bad_artifact,
            )

    def test_burns_captions_resolved_from_an_srt_source_file(self):
        with tempfile.TemporaryDirectory() as directory:
            plan = caption_file_plan(directory)
            Path(plan.sources[2]).write_text(
                "1\n00:00:00,000 --> 00:00:01,500\nFirst\n\n"
                "2\n00:00:01,500 --> 00:00:03,000\nSecond\n",
                encoding="utf-8",
            )
            burned = {}

            def compose(clips, output, **kwargs):
                burned["cues"] = tuple(
                    (cue.text, cue.start_seconds, cue.end_seconds)
                    for cue in kwargs.get("captions", ())
                )
                return SequenceArtifact(
                    tuple(clip.source_path for clip in clips), plan.output_path, 3.5, 500, caption_count=2
                )

            report = run_sequence_workflow(
                plan,
                preflight=caption_file_preflight,
                compose=compose,
                validate=lambda artifact, **_k: SequenceValidationReport(
                    True, artifact, 3.5, 0.15, (), None
                ),
            )

            self.assertTrue(report.valid)
            self.assertEqual(
                burned["cues"], (("First", 0.0, 1.5), ("Second", 1.5, 3.0))
            )
            self.assertEqual(report.artifact.caption_count, 2)

    def test_rejects_a_captions_file_with_an_unsupported_extension(self):
        with tempfile.TemporaryDirectory() as directory:
            plan = caption_file_plan(directory, srt_name="cues.txt")
            Path(plan.sources[2]).write_text("whatever", encoding="utf-8")
            with self.assertRaisesRegex(SequenceWorkflowError, r"\.srt or \.vtt"):
                run_sequence_workflow(
                    plan,
                    preflight=lambda _: self.fail("must not preflight"),
                    compose=lambda *_a, **_k: self.fail("must not compose"),
                )

    def test_rejects_a_captions_file_that_also_declares_items(self):
        with tempfile.TemporaryDirectory() as directory:
            plan = caption_file_plan(
                directory,
                parameters={
                    "style": "bottom_box",
                    "items": [{"text": "x", "start_seconds": 0, "end_seconds": 1}],
                },
            )
            Path(plan.sources[2]).write_text(
                "1\n00:00:00,000 --> 00:00:01,000\nx\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(SequenceWorkflowError, "only style=bottom_box"):
                run_sequence_workflow(
                    plan,
                    preflight=lambda _: self.fail("must not preflight"),
                    compose=lambda *_a, **_k: self.fail("must not compose"),
                )

    def test_rejects_a_captions_file_whose_cues_exceed_the_timeline(self):
        with tempfile.TemporaryDirectory() as directory:
            plan = caption_file_plan(directory)
            Path(plan.sources[2]).write_text(
                "1\n00:00:00,000 --> 00:01:39,000\ntoo long\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(SequenceWorkflowError, "not exceed the sequence duration"):
                run_sequence_workflow(
                    plan,
                    preflight=caption_file_preflight,
                    compose=lambda *_a, **_k: self.fail("must not compose"),
                )

    def test_rejects_a_missing_captions_file(self):
        with tempfile.TemporaryDirectory() as directory:
            plan = caption_file_plan(directory)
            with self.assertRaisesRegex(SequenceWorkflowError, "cannot read captions file"):
                run_sequence_workflow(
                    plan,
                    preflight=caption_file_preflight,
                    compose=lambda *_a, **_k: self.fail("must not compose"),
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

    def test_synthesises_narration_from_text_and_records_only_the_digest(self):
        plan = narrated_text_plan()
        seen = {}

        def compose(clips, output, **kwargs):
            seen["narration_path"] = kwargs.get("narration_path")
            return SequenceArtifact(
                tuple(clip.source_path for clip in clips),
                plan.output_path,
                3.5,
                500,
                narration_source_path=kwargs.get("narration_path"),
            )

        with patch.object(sequence_module, "synthesize_narration", side_effect=_fake_synth), \
                patch.object(sequence_module, "probe_media", side_effect=_probe_of(3.2)):
            report = run_sequence_workflow(
                plan,
                preflight=valid_preflight,
                compose=compose,
                validate=lambda artifact, **_k: SequenceValidationReport(
                    True, artifact, 3.5, 0.15, (), None
                ),
            )

        self.assertTrue(report.valid)
        self.assertIsNotNone(seen["narration_path"])
        # the transient synthesis path is dropped; the text digest is kept
        self.assertIsNone(report.artifact.narration_source_path)
        self.assertEqual(
            report.artifact.narration_text_sha256,
            sha256(b"Hello dark world.").hexdigest(),
        )
        # the scratch directory next to the output is removed
        self.assertEqual(
            list(Path(plan.output_path).parent.glob(".timeline-tts-*")), []
        )

    def test_rejects_missing_or_unknown_narration_text_parameters(self):
        for params, message in (
            ({"duration_policy": "match_timeline"}, "non-empty string"),
            ({"duration_policy": "match_timeline", "text": "   "}, "non-empty string"),
            (
                {"duration_policy": "match_timeline", "text": "hi", "pitch": 3},
                "text, voice, speed, lang and lead_in_seconds",
            ),
        ):
            with self.subTest(message=message):
                with self.assertRaisesRegex(SequenceWorkflowError, message):
                    run_sequence_workflow(
                        narrated_text_plan(params=params),
                        preflight=lambda _: self.fail("must not preflight"),
                        compose=lambda *_a, **_k: self.fail("must not compose"),
                    )

    def test_rejects_narration_longer_than_the_timeline(self):
        with patch.object(sequence_module, "synthesize_narration", side_effect=_fake_synth), \
                patch.object(sequence_module, "probe_media", side_effect=_probe_of(9.0)):
            with self.assertRaisesRegex(SequenceWorkflowError, "shorten the narration text"):
                run_sequence_workflow(
                    narrated_text_plan(),
                    preflight=valid_preflight,
                    compose=lambda *_a, **_k: self.fail("must not compose"),
                )

    def test_pulls_derived_captions_onto_the_measured_narration_pauses(self):
        base = narrated_text_plan(
            params={
                "duration_policy": "match_timeline",
                "text": "Hello dark world. Here is a second sentence for the caption track.",
                "lead_in_seconds": 0.5,
            }
        )
        plan = EditPlan(
            base.plan_id,
            base.brief_id,
            base.sources,
            base.output_path,
            (
                *base.operations[:-1],
                EditOperation(
                    "captions-1",
                    "captions",
                    parameters={"style": "bottom_box", "from": "narration"},
                ),
                base.operations[-1],
            ),
        )
        seen = {}

        def compose(clips, output, **kwargs):
            seen["captions"] = tuple(
                (cue.text, cue.start_seconds, cue.end_seconds)
                for cue in kwargs.get("captions", ())
            )
            seen["narration_path"] = kwargs.get("narration_path")
            return SequenceArtifact(
                tuple(clip.source_path for clip in clips),
                plan.output_path,
                3.5,
                500,
                narration_source_path=kwargs.get("narration_path"),
                caption_count=len(kwargs.get("captions", ())),
                narration_lead_in_seconds=0.5,
            )

        measured = Mock(return_value=((1.1, 1.3),))
        with patch.object(sequence_module, "synthesize_narration", side_effect=_fake_synth), \
                patch.object(sequence_module, "detect_silences", measured), \
                patch.object(sequence_module, "probe_media", side_effect=_probe_of(3.0)):
            report = run_sequence_workflow(
                plan,
                preflight=valid_preflight,
                compose=compose,
                validate=lambda artifact, **_k: SequenceValidationReport(
                    True, artifact, 3.5, 0.15, (), None
                ),
            )

        self.assertTrue(report.valid)
        # the pauses are measured on the synthesised WAV, before it is discarded
        self.assertEqual(measured.call_args.args[0], seen["narration_path"])
        # the estimate put the break at 0.954 s; the voice really stopped from
        # 1.1 to 1.3, so the cut moves to 1.2 and then shifts by the 0.5 lead-in
        self.assertAlmostEqual(seen["captions"][0][2], 1.7, places=6)
        self.assertAlmostEqual(seen["captions"][1][1], 1.7, places=6)
        self.assertAlmostEqual(seen["captions"][0][1], 0.5, places=6)
        self.assertAlmostEqual(seen["captions"][-1][2], 3.5, places=6)

    def test_reports_a_narration_whose_pauses_cannot_be_measured(self):
        base = narrated_text_plan(
            params={
                "duration_policy": "match_timeline",
                "text": "Hello dark world. Here is a second sentence for the caption track.",
            }
        )
        plan = EditPlan(
            base.plan_id,
            base.brief_id,
            base.sources,
            base.output_path,
            (
                *base.operations[:-1],
                EditOperation(
                    "captions-1",
                    "captions",
                    parameters={"style": "bottom_box", "from": "narration"},
                ),
                base.operations[-1],
            ),
        )

        with patch.object(sequence_module, "synthesize_narration", side_effect=_fake_synth), \
                patch.object(
                    sequence_module,
                    "detect_silences",
                    side_effect=FFmpegError("no audio stream"),
                ), \
                patch.object(sequence_module, "probe_media", side_effect=_probe_of(3.0)):
            with self.assertRaisesRegex(
                SequenceWorkflowError, "could not measure the narration pauses"
            ):
                run_sequence_workflow(
                    plan,
                    preflight=valid_preflight,
                    compose=lambda *_a, **_k: self.fail("must not compose"),
                )

    def test_derives_captions_from_the_narration_text(self):
        base = narrated_text_plan(
            params={
                "duration_policy": "match_timeline",
                "text": "Hello dark world. Here is a second sentence for the caption track.",
            }
        )
        plan = EditPlan(
            base.plan_id,
            base.brief_id,
            base.sources,
            base.output_path,
            (
                *base.operations[:-1],
                EditOperation(
                    "captions-1", "captions", parameters={"style": "bottom_box", "from": "narration"}
                ),
                base.operations[-1],
            ),
        )
        seen = {}

        def compose(clips, output, **kwargs):
            seen["captions"] = tuple(
                (c.text, round(c.start_seconds, 2), round(c.end_seconds, 2))
                for c in kwargs.get("captions", ())
            )
            return SequenceArtifact(
                tuple(clip.source_path for clip in clips),
                plan.output_path,
                3.5,
                500,
                narration_source_path=kwargs.get("narration_path"),
                caption_count=len(kwargs.get("captions", ())),
            )

        with patch.object(sequence_module, "synthesize_narration", side_effect=_fake_synth), \
                patch.object(sequence_module, "detect_silences", return_value=()), \
                patch.object(sequence_module, "probe_media", side_effect=_probe_of(3.0)):
            report = run_sequence_workflow(
                plan,
                preflight=valid_preflight,
                compose=compose,
                validate=lambda artifact, **_k: SequenceValidationReport(
                    True, artifact, 3.5, 0.15, (), None
                ),
            )

        self.assertTrue(report.valid)
        self.assertGreaterEqual(len(seen["captions"]), 2)
        self.assertEqual(seen["captions"][0][1], 0.0)
        self.assertLessEqual(seen["captions"][-1][2], 3.0)
        self.assertEqual(report.artifact.caption_count, len(seen["captions"]))

    def test_holds_text_narration_back_by_the_lead_in_and_shifts_its_captions(self):
        base = narrated_text_plan(
            params={
                "duration_policy": "match_timeline",
                "text": "Hello dark world. Here is a second sentence for the caption track.",
                "lead_in_seconds": 1,
            }
        )
        plan = EditPlan(
            base.plan_id,
            base.brief_id,
            base.sources,
            base.output_path,
            (
                *base.operations[:-1],
                EditOperation(
                    "captions-1", "captions", parameters={"style": "bottom_box", "from": "narration"}
                ),
                base.operations[-1],
            ),
        )
        seen = {}

        def compose(clips, output, **kwargs):
            seen["lead_in"] = kwargs.get("narration_lead_in_seconds")
            seen["captions"] = tuple(
                (round(c.start_seconds, 3), round(c.end_seconds, 3))
                for c in kwargs.get("captions", ())
            )
            return SequenceArtifact(
                tuple(clip.source_path for clip in clips),
                plan.output_path,
                3.5,
                500,
                narration_source_path=kwargs.get("narration_path"),
                caption_count=len(kwargs.get("captions", ())),
                narration_lead_in_seconds=kwargs.get("narration_lead_in_seconds", 0.0),
            )

        # 1 s of atmosphere, then 2 s of voice inside a 3.5 s timeline
        with patch.object(sequence_module, "synthesize_narration", side_effect=_fake_synth), \
                patch.object(sequence_module, "detect_silences", return_value=()), \
                patch.object(sequence_module, "probe_media", side_effect=_probe_of(2.0)):
            report = run_sequence_workflow(
                plan,
                preflight=valid_preflight,
                compose=compose,
                validate=lambda artifact, **_k: SequenceValidationReport(
                    True, artifact, 3.5, 0.15, (), None
                ),
            )

        self.assertTrue(report.valid)
        self.assertEqual(seen["lead_in"], 1.0)
        self.assertEqual(report.artifact.narration_lead_in_seconds, 1.0)
        # no caption before the voice; the last one still ends with it
        self.assertGreaterEqual(len(seen["captions"]), 2)
        self.assertEqual(seen["captions"][0][0], 1.0)
        self.assertEqual(seen["captions"][-1][1], 3.0)

    def test_defaults_the_narration_lead_in_to_zero(self):
        seen = {}

        def compose(clips, output, **kwargs):
            seen["lead_in"] = kwargs.get("narration_lead_in_seconds")
            return SequenceArtifact(
                tuple(clip.source_path for clip in clips),
                narrated_text_plan().output_path,
                3.5,
                500,
                narration_source_path=kwargs.get("narration_path"),
            )

        with patch.object(sequence_module, "synthesize_narration", side_effect=_fake_synth), \
                patch.object(sequence_module, "probe_media", side_effect=_probe_of(3.2)):
            report = run_sequence_workflow(
                narrated_text_plan(),
                preflight=valid_preflight,
                compose=compose,
                validate=lambda artifact, **_k: SequenceValidationReport(
                    True, artifact, 3.5, 0.15, (), None
                ),
            )

        # an absent lead-in is not forwarded at all, keeping the previous graph
        self.assertIsNone(seen["lead_in"])
        self.assertEqual(report.artifact.narration_lead_in_seconds, 0.0)

    def test_rejects_a_lead_in_that_pushes_the_narration_past_the_timeline(self):
        plan = narrated_text_plan(
            params={
                "duration_policy": "match_timeline",
                "text": "Hello dark world.",
                "lead_in_seconds": 2,
            }
        )
        # 2 s lead-in + 2 s of voice overruns the 3.5 s timeline
        with patch.object(sequence_module, "synthesize_narration", side_effect=_fake_synth), \
                patch.object(sequence_module, "probe_media", side_effect=_probe_of(2.0)):
            with self.assertRaisesRegex(SequenceWorkflowError, "shorten the narration text"):
                run_sequence_workflow(
                    plan,
                    preflight=valid_preflight,
                    compose=lambda *_a, **_k: self.fail("must not compose"),
                )

    def test_rejects_an_invalid_or_misplaced_narration_lead_in(self):
        for plan, message in (
            (
                narrated_text_plan(
                    params={
                        "duration_policy": "match_timeline",
                        "text": "hi",
                        "lead_in_seconds": -1,
                    }
                ),
                "lead_in_seconds must be a finite non-negative number",
            ),
            (
                narrated_text_plan(
                    params={
                        "duration_policy": "match_timeline",
                        "text": "hi",
                        "lead_in_seconds": "soon",
                    }
                ),
                "lead_in_seconds must be a finite non-negative number",
            ),
            (
                narrated_plan(
                    narration_parameters={
                        "duration_policy": "match_timeline",
                        "lead_in_seconds": 1,
                    }
                ),
                "narration from a source accepts only duration_policy",
            ),
        ):
            with self.subTest(message=message):
                with self.assertRaisesRegex(SequenceWorkflowError, message):
                    run_sequence_workflow(
                        plan,
                        preflight=lambda _: self.fail("must not preflight"),
                        compose=lambda *_a, **_k: self.fail("must not compose"),
                    )

    def test_rejects_an_artifact_with_an_unexpected_lead_in(self):
        def compose(clips, output, **kwargs):
            return SequenceArtifact(
                tuple(clip.source_path for clip in clips),
                narrated_text_plan().output_path,
                3.5,
                500,
                narration_source_path=kwargs.get("narration_path"),
                narration_lead_in_seconds=2.0,
            )

        with patch.object(sequence_module, "synthesize_narration", side_effect=_fake_synth), \
                patch.object(sequence_module, "probe_media", side_effect=_probe_of(3.2)):
            with self.assertRaisesRegex(SequenceWorkflowError, "unexpected narration metadata"):
                run_sequence_workflow(
                    narrated_text_plan(), preflight=valid_preflight, compose=compose
                )

    def test_rejects_from_narration_captions_without_a_narration_text(self):
        base = sequence_plan()
        plan = EditPlan(
            base.plan_id,
            base.brief_id,
            base.sources,
            base.output_path,
            (
                *base.operations,
                EditOperation(
                    "captions-1", "captions", parameters={"style": "bottom_box", "from": "narration"}
                ),
            ),
        )
        with self.assertRaisesRegex(SequenceWorkflowError, "from=narration require a narration text"):
            run_sequence_workflow(
                plan,
                preflight=lambda _: self.fail("must not preflight"),
                compose=lambda *_a, **_k: self.fail("must not compose"),
            )

    def test_wraps_a_narration_synthesis_failure(self):
        with patch.object(
            sequence_module,
            "synthesize_narration",
            side_effect=KokoroError("Kokoro model files not found under .local-tools/kokoro/"),
        ):
            with self.assertRaisesRegex(SequenceWorkflowError, "narration synthesis failed"):
                run_sequence_workflow(
                    narrated_text_plan(),
                    preflight=valid_preflight,
                    compose=lambda *_a, **_k: self.fail("must not compose"),
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


def target_format_plan(*, tf, first_params=None, second_kind="sequence_clip", second_params=None):
    first = str(Path("inputs/first.mp4").resolve())
    second = str(Path("inputs/second.mp4").resolve())
    if second_kind == "image_clip":
        second_op = EditOperation(
            "clip-2", "image_clip", second, parameters=second_params or {"duration_seconds": 2}
        )
    else:
        second_op = EditOperation("clip-2", "sequence_clip", second, 0, 2, second_params or {})
    return EditPlan(
        "plan-tf",
        "brief-dark",
        (first, second),
        str(Path("output/timeline.mp4").resolve()),
        (
            EditOperation("clip-1", "sequence_clip", first, 1, 2.5, first_params or {}),
            second_op,
        ),
        tf,
    )


def mixed_preflight(plan: EditPlan) -> PreflightReport:
    shapes = ((1920, 1080), (1080, 1920))
    probes = tuple(
        MediaProbe(
            source,
            1000,
            "mov,mp4",
            10,
            800000,
            (StreamProbe(0, "video", "h264", 10, width, height, None, None),),
        )
        for source, (width, height) in zip(plan.sources, shapes, strict=True)
    )
    return PreflightReport(plan.plan_id, True, (), probes)


class TargetFormatWorkflowTests(unittest.TestCase):
    def _capture(self, plan, preflight=mixed_preflight):
        seen = {}

        def compose(clips, output, **kwargs):
            seen["canvas"] = kwargs.get("canvas")
            seen["fits"] = [clip.fit for clip in clips]
            duration = 0.0
            image_count = 0
            for clip in clips:
                if type(clip).__name__ == "SequenceImage":
                    duration += clip.duration_seconds
                    image_count += 1
                else:
                    duration += clip.end_seconds - clip.start_seconds
            seen["duration"] = duration
            return SequenceArtifact(
                tuple(clip.source_path for clip in clips),
                plan.output_path,
                duration,
                500,
                image_count=image_count,
            )

        def validate(artifact, **_kwargs):
            return SequenceValidationReport(True, artifact, seen["duration"], 0.15, (), None)

        report = run_sequence_workflow(
            plan, preflight=preflight, compose=compose, validate=validate
        )
        return report, seen

    def test_canvas_comes_from_the_target_format_across_mixed_resolutions(self):
        from video_generator.domain import TargetFormat

        plan = target_format_plan(tf=TargetFormat(1080, 1920, "cover"))
        report, seen = self._capture(plan)
        self.assertTrue(report.valid)
        self.assertEqual(seen["canvas"], (1080, 1920))
        # default fit flows from the target format to every segment
        self.assertEqual(seen["fits"], ["cover", "cover"])

    def test_per_clip_and_per_image_fit_overrides(self):
        from video_generator.domain import TargetFormat

        plan = target_format_plan(
            tf=TargetFormat(1080, 1920, "contain"),
            first_params={"fit": "cover"},
            second_kind="image_clip",
            second_params={"duration_seconds": 3, "fit": "cover"},
        )
        report, seen = self._capture(plan)
        self.assertTrue(report.valid)
        self.assertEqual(seen["canvas"], (1080, 1920))
        self.assertEqual(seen["fits"], ["cover", "cover"])

    def test_rejects_an_invalid_fit_value(self):
        from video_generator.domain import TargetFormat

        plan = target_format_plan(
            tf=TargetFormat(1080, 1920), first_params={"fit": "stretch"}
        )
        with self.assertRaisesRegex(SequenceWorkflowError, "fit"):
            run_sequence_workflow(
                plan,
                preflight=lambda _: self.fail("must not preflight"),
                compose=lambda *_a, **_k: self.fail("must not compose"),
            )

    def test_rejects_a_fit_without_a_target_format(self):
        plan = sequence_plan(second_parameters={"fit": "cover"})
        with self.assertRaisesRegex(SequenceWorkflowError, "requires the plan to declare a target_format"):
            run_sequence_workflow(
                plan,
                preflight=lambda _: self.fail("must not preflight"),
                compose=lambda *_a, **_k: self.fail("must not compose"),
            )

    def test_legacy_plan_without_target_format_still_inherits_the_clip_canvas(self):
        plan = sequence_plan()
        seen = {}

        def compose(clips, output, **kwargs):
            seen["canvas"] = kwargs.get("canvas")
            seen["fits"] = [clip.fit for clip in clips]
            return SequenceArtifact(
                tuple(clip.source_path for clip in clips), plan.output_path, 3.5, 500
            )

        run_sequence_workflow(
            plan,
            preflight=valid_preflight,
            compose=compose,
            validate=lambda artifact, **_k: SequenceValidationReport(
                True, artifact, 3.5, 0.15, (), None
            ),
        )
        self.assertEqual(seen["canvas"], (1280, 720))
        self.assertEqual(seen["fits"], [None, None])


if __name__ == "__main__":
    unittest.main()
