"""Tests for the editorial emphasis layer on its way to the screen.

Covers the ``text_events`` EditPlan operation, its parsing in the sequence
workflow, the visual-identity style it carries, and the ASS the adapter emits.
"""

import json
import tempfile
import unittest
from pathlib import Path

from video_generator.adapters.ffmpeg import (
    FFmpegError,
    SequenceImage,
    TextEventCue,
    TextStyleSpec,
    _caption_ass_header,
    _emphasis_styles,
    _event_override,
    _inline_colour,
)
from video_generator.domain import (
    DARK_DOCUMENTARY_V1,
    EditOperation,
    NarrativeScript,
    PlanningError,
    TargetFormat,
    TextEvent,
    VisualStyle,
    narration_slices,
    plan_scenes,
    plan_shot_text_events,
    plan_shots,
    shot_plan_to_edit_plan,
    shot_timeline,
    text_events_operation,
)
from video_generator.workflows.sequence import (
    SequenceWorkflowError,
    _operations_from_plan,
    _text_event_cues,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "roteiro_einstein_exame.txt"


def _event(**kwargs):
    base = dict(
        event_id="text_001", text="1895", start_seconds=0.0, end_seconds=2.0,
        category="date", importance=0.8, position="middle", animation="pop",
    )
    base.update(kwargs)
    return TextEvent(**base)


class ShotTimelineTests(unittest.TestCase):
    """Absolute time is derived from the shot plan, never stored twice."""

    def setUp(self):
        script = NarrativeScript.from_text(
            "einstein", FIXTURE.read_text(encoding="utf-8"),
            total_duration_seconds=270.0,
        )
        self.scene_plan = plan_scenes(script)
        self.shot_plan, _ = plan_shots(
            self.scene_plan, orientation="landscape", semantic=True
        )

    def test_the_timeline_is_contiguous_and_matches_the_durations(self):
        timeline = shot_timeline(self.shot_plan)
        self.assertEqual(len(timeline), len(self.shot_plan.shots))
        cursor = 0.0
        for shot in self.shot_plan.shots:
            start, end = timeline[shot.shot_id]
            self.assertAlmostEqual(start, cursor, places=6)
            self.assertAlmostEqual(end - start, shot.duration_seconds, places=6)
            cursor = end
        self.assertAlmostEqual(
            cursor, sum(s.duration_seconds for s in self.shot_plan.shots), places=6
        )

    def test_narration_slices_cover_every_shot_exactly_once(self):
        slices = narration_slices(self.scene_plan, self.shot_plan)
        self.assertEqual(
            [row[0] for row in slices], [s.shot_id for s in self.shot_plan.shots]
        )

    def test_events_land_inside_the_shots_that_produced_them(self):
        timeline = shot_timeline(self.shot_plan)
        events = plan_shot_text_events(self.scene_plan, self.shot_plan)
        self.assertTrue(events)
        for event in events:
            start, end = timeline[event.beat_id]
            # offsets are rounded to the millisecond when they are written out
            self.assertGreaterEqual(event.start_seconds, start - 1e-3)
            self.assertLessEqual(event.end_seconds, end + 1e-3)

    def test_emphasis_text_is_always_spoken_in_the_narration(self):
        events = plan_shot_text_events(self.scene_plan, self.shot_plan)
        spoken = " ".join(s.narration for s in self.scene_plan.scenes).upper()
        for event in events:
            for word in event.text.split():
                self.assertIn(word.strip(".,;:!?"), spoken, event.text)

    def test_rejects_a_plan_of_the_wrong_type(self):
        with self.assertRaises(PlanningError):
            shot_timeline(self.scene_plan)
        with self.assertRaises(PlanningError):
            narration_slices(self.shot_plan, self.shot_plan)


class TextEventsOperationTests(unittest.TestCase):
    def test_builds_a_renderer_operation_carrying_the_brand_kit(self):
        operation = text_events_operation(
            [_event()], visual_style=DARK_DOCUMENTARY_V1
        )
        self.assertEqual(operation.kind, "text_events")
        self.assertEqual(len(operation.parameters["items"]), 1)
        style = operation.parameters["style"]
        self.assertEqual(style["accent"], DARK_DOCUMENTARY_V1.ass_colour("accent"))
        self.assertEqual(style["font_name"], DARK_DOCUMENTARY_V1.font_name)

    def test_a_style_is_optional(self):
        operation = text_events_operation([_event()])
        self.assertNotIn("style", operation.parameters)

    def test_numbers_and_dates_get_the_accented_treatment(self):
        for category, emphasised in (
            ("date", True), ("number", True), ("question", True),
            ("emphasis", True), ("keyword", False),
        ):
            operation = text_events_operation([_event(category=category)])
            self.assertEqual(
                operation.parameters["items"][0]["emphasis"], emphasised, category
            )

    def test_rejects_an_empty_layer_and_a_wrong_type(self):
        with self.assertRaises(PlanningError):
            text_events_operation([])
        with self.assertRaises(PlanningError):
            text_events_operation(["not an event"])
        with self.assertRaises(PlanningError):
            text_events_operation([_event()], visual_style="dark")


class TextEventWorkflowParsingTests(unittest.TestCase):
    def _params(self, **overrides):
        item = {"text": "1895", "start_seconds": 0.0, "end_seconds": 2.0}
        item.update(overrides.pop("item", {}))
        params = {"items": [item]}
        params.update(overrides)
        return params

    def test_parses_items_and_defaults(self):
        cues, style = _text_event_cues(self._params())
        self.assertEqual(len(cues), 1)
        self.assertEqual(cues[0].position, "top")
        self.assertEqual(cues[0].animation, "fade")
        self.assertFalse(cues[0].emphasis)
        self.assertIsNone(style)

    def test_parses_the_visual_identity(self):
        _cues, style = _text_event_cues(
            self._params(style={"accent": "&H003CA3E5", "emphasis_scale": 1.8})
        )
        self.assertEqual(style.accent, "&H003CA3E5")
        self.assertEqual(style.emphasis_scale, 1.8)

    def test_rejects_markup_that_could_become_ass_syntax(self):
        for bad in ("{\\an8}X", "<b>X</b>"):
            with self.assertRaises(SequenceWorkflowError):
                _text_event_cues(self._params(item={"text": bad}))

    def test_rejects_overlapping_or_unordered_events(self):
        params = {
            "items": [
                {"text": "A", "start_seconds": 0.0, "end_seconds": 3.0},
                {"text": "B", "start_seconds": 1.0, "end_seconds": 4.0},
            ]
        }
        with self.assertRaises(SequenceWorkflowError):
            _text_event_cues(params)

    def test_rejects_unknown_keys_positions_and_animations(self):
        with self.assertRaises(SequenceWorkflowError):
            _text_event_cues(self._params(colour="red"))
        with self.assertRaises(SequenceWorkflowError):
            _text_event_cues(self._params(item={"position": "diagonal"}))
        with self.assertRaises(SequenceWorkflowError):
            _text_event_cues(self._params(item={"animation": "explode"}))
        with self.assertRaises(SequenceWorkflowError):
            _text_event_cues(self._params(style={"accent": "orange"}))

    def test_rejects_an_empty_or_oversized_layer(self):
        with self.assertRaises(SequenceWorkflowError):
            _text_event_cues({"items": []})
        with self.assertRaises(SequenceWorkflowError):
            _text_event_cues(
                {"items": [
                    {"text": "X", "start_seconds": i, "end_seconds": i + 0.5}
                    for i in range(201)
                ]}
            )


class TextEventPlanIntegrationTests(unittest.TestCase):
    """A plan carrying the layer survives the round trip through the workflow."""

    def test_a_plan_without_the_layer_parses_exactly_as_before(self):
        with tempfile.TemporaryDirectory() as d:
            plan = _minimal_plan(Path(d), extra=())
            parsed = _operations_from_plan(plan)
            self.assertEqual(parsed.text_events, ())
            self.assertIsNone(parsed.text_style)
            self.assertIsNone(parsed.direction)

    def test_a_plan_with_the_layer_yields_cues_and_a_style(self):
        with tempfile.TemporaryDirectory() as d:
            operation = text_events_operation(
                [_event()], visual_style=DARK_DOCUMENTARY_V1
            )
            plan = _minimal_plan(Path(d), extra=(operation,))
            parsed = _operations_from_plan(plan)
            cues, style = parsed.text_events, parsed.text_style
            self.assertEqual(len(cues), 1)
            self.assertEqual(cues[0].text, "1895")
            self.assertTrue(cues[0].emphasis)
            self.assertEqual(style.accent, DARK_DOCUMENTARY_V1.ass_colour("accent"))

    def test_two_layers_are_refused(self):
        with tempfile.TemporaryDirectory() as d:
            operation = text_events_operation([_event()])
            second = text_events_operation(
                [_event(event_id="text_002", text="1896",
                        start_seconds=3.0, end_seconds=5.0)],
                operation_id="text_events_2",
            )
            plan = _minimal_plan(Path(d), extra=(operation, second))
            with self.assertRaises(SequenceWorkflowError):
                _operations_from_plan(plan)


def _minimal_plan(tmp, extra):
    """Two still images plus whatever extra operations the test wants."""

    first, second = tmp / "a.jpg", tmp / "b.jpg"
    for path in (first, second):
        path.write_bytes(b"x")
    from video_generator.domain import EditPlan

    return EditPlan(
        plan_id="p",
        brief_id="b",
        sources=(str(first), str(second)),
        output_path=str(tmp / "out.mp4"),
        operations=(
            EditOperation(
                operation_id="s1", kind="image_clip", source=str(first),
                parameters={"duration_seconds": 3.0, "fit": "cover"},
            ),
            EditOperation(
                operation_id="s2", kind="image_clip", source=str(second),
                parameters={"duration_seconds": 3.0, "fit": "cover"},
            ),
        )
        + tuple(extra),
        target_format=TargetFormat(1920, 1080, "cover"),
    )


class AssEmissionTests(unittest.TestCase):
    """What libass is actually handed."""

    def setUp(self):
        self.width, self.height = 1920, 1080
        self.style = TextStyleSpec()

    def test_the_emphasis_styles_are_added_to_the_styles_block(self):
        header = _caption_ass_header(self.width, self.height)
        spliced = header.replace(
            "\n\n[Events]\n",
            "\n" + _emphasis_styles(self.width, self.height, self.style)
            + "\n[Events]\n",
            1,
        )
        styles_block, events_block = spliced.split("[Events]")
        self.assertIn("Style: Emphasis,", styles_block)
        self.assertIn("Style: Keyword,", styles_block)
        self.assertIn("Style: Caption,", styles_block)
        self.assertNotIn("Style:", events_block)

    def test_emphasis_is_larger_than_a_caption(self):
        styles = _emphasis_styles(self.width, self.height, self.style)
        emphasis_size = int(styles.split("Style: Emphasis,Sans,")[1].split(",")[0])
        keyword_size = int(styles.split("Style: Keyword,Sans,")[1].split(",")[0])
        caption = _caption_ass_header(self.width, self.height)
        caption_size = int(caption.split("Style: Caption,Sans,")[1].split(",")[0])
        self.assertGreater(emphasis_size, keyword_size)
        self.assertGreater(keyword_size, caption_size)

    def test_each_animation_emits_its_own_override(self):
        seen = set()
        for animation in ("fade", "pop", "slide", "highlight"):
            override = _event_override(
                TextEventCue("X", 0.0, 1.0, "top", animation), self.width,
                self.height, self.style,
            )
            self.assertTrue(override.startswith("{") and override.endswith("}"))
            self.assertIn("\\fad(", override)
            seen.add(override)
        self.assertEqual(len(seen), 4)

    def test_position_maps_to_an_ass_alignment(self):
        for position, tag in (("top", "\\an8"), ("middle", "\\an5"), ("lower", "\\an2")):
            override = _event_override(
                TextEventCue("X", 0.0, 1.0, position, "fade"), self.width,
                self.height, self.style,
            )
            self.assertIn(tag, override)

    def test_an_inline_colour_is_converted_to_override_form(self):
        self.assertEqual(_inline_colour("&H003CA3E5"), "&H3CA3E5&")
        self.assertEqual(_inline_colour("&H3CA3E5"), "&H3CA3E5&")

    def test_the_identity_accent_reaches_the_style(self):
        style = TextStyleSpec(accent=VisualStyle(accent="#3C8FE5").ass_colour("accent"))
        styles = _emphasis_styles(self.width, self.height, style)
        self.assertIn("&H00E58F3C", styles)


class ComposeValidationTests(unittest.TestCase):
    """The adapter refuses an unsafe or impossible emphasis layer before FFmpeg."""

    def _images(self, tmp):
        paths = []
        for name in ("a.jpg", "b.jpg"):
            path = tmp / name
            path.write_bytes(b"x")
            paths.append(SequenceImage(str(path), 3.0, "cover"))
        return paths

    def _compose(self, tmp, **kwargs):
        from video_generator.adapters.ffmpeg import compose_video_sequence

        return compose_video_sequence(
            self._images(tmp), tmp / "out.mp4", canvas=(1920, 1080), **kwargs
        )

    def test_rejects_markup_in_an_event(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            with self.assertRaisesRegex(FFmpegError, "markup"):
                self._compose(
                    tmp, text_events=(TextEventCue("{\\an8}X", 0.0, 1.0),)
                )

    def test_rejects_an_event_past_the_end_of_the_timeline(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            with self.assertRaisesRegex(FFmpegError, "must not exceed"):
                self._compose(tmp, text_events=(TextEventCue("X", 0.0, 99.0),))

    def test_rejects_overlapping_events(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            with self.assertRaisesRegex(FFmpegError, "ordered and non-overlapping"):
                self._compose(
                    tmp,
                    text_events=(
                        TextEventCue("A", 0.0, 3.0),
                        TextEventCue("B", 1.0, 4.0),
                    ),
                )

    def test_rejects_an_unknown_position_or_animation(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            with self.assertRaises(FFmpegError):
                self._compose(tmp, text_events=(TextEventCue("X", 0.0, 1.0, "corner"),))
            with self.assertRaises(FFmpegError):
                self._compose(
                    tmp, text_events=(TextEventCue("X", 0.0, 1.0, "top", "explode"),)
                )

    def test_rejects_events_without_a_canvas(self):
        from video_generator.adapters.ffmpeg import compose_video_sequence

        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            with self.assertRaisesRegex(FFmpegError, "canvas"):
                compose_video_sequence(
                    self._images(tmp), tmp / "out.mp4",
                    text_events=(TextEventCue("X", 0.0, 1.0),),
                )


class SemanticPlanEmitsTheLayerTests(unittest.TestCase):
    def test_a_semantic_plan_carries_captions_free_emphasis_into_the_edit_plan(self):
        script = NarrativeScript.from_text(
            "einstein", FIXTURE.read_text(encoding="utf-8"),
            total_duration_seconds=270.0,
        )
        scene_plan = plan_scenes(script)
        shot_plan, assets = plan_shots(
            scene_plan, orientation="landscape", semantic=True
        )
        events = plan_shot_text_events(scene_plan, shot_plan)
        self.assertTrue(events)
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            bindings = {}
            for req in assets.requirements:
                path = tmp / f"{req.asset_id}{'.mp4' if req.type == 'video' else '.jpg'}"
                path.write_bytes(b"x")
                bindings[req.asset_id] = str(path)
            plan = shot_plan_to_edit_plan(
                shot_plan, bindings, plan_id="p", brief_id="b",
                output_path=str(tmp / "out.mp4"),
                target_format=TargetFormat(1920, 1080, "cover"),
                extra_operations=(
                    text_events_operation(events, visual_style=DARK_DOCUMENTARY_V1),
                ),
            )
        kinds = [op.kind for op in plan.operations]
        self.assertEqual(kinds[-1], "text_events")
        self.assertEqual(kinds.count("text_events"), 1)
        # the layer is a plain part of the persisted plan
        restored = json.loads(plan.to_json())
        self.assertEqual(restored["operations"][-1]["kind"], "text_events")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
