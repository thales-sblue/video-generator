"""Tests for Editorial Motion Typography v1, from the sentence to the ASS.

Covers the composition rules (what goes on screen and why), the layout
geometry (where the pixels land), the ``motion_typography`` EditPlan operation,
its parsing in the sequence workflow, and the fact that turning the layer off
leaves every older behaviour exactly as it was.
"""

import json
import tempfile
import unittest
from pathlib import Path

from video_generator.adapters.ffmpeg import (
    FFmpegError,
    MotionTextBlockCue,
    MotionTextCue,
    SequenceImage,
    TextStyleSpec,
    _motion_dialogues,
    _motion_placements,
    _motion_style,
    compose_video_sequence,
)
from video_generator.domain import (
    DARK_DOCUMENTARY_V1,
    EditOperation,
    EditPlan,
    NarrativeScript,
    TargetFormat,
    plan_scenes,
    plan_shots,
)
from video_generator.domain.planning import plan_shot_motion_typography
from video_generator.domain.typography import (
    BLOCK_WEIGHTS,
    DEFAULT_TYPOGRAPHY_POLICY,
    TEXT_LAYOUTS,
    TEXT_MOTIONS,
    TEXT_ROLES,
    MotionTextEvent,
    MotionTypographyPolicy,
    TextBlock,
    TypographyError,
    _is_derived,
    motion_typography_operation,
    plan_motion_typography,
    spoken_words,
    stream_alignment,
)
from video_generator.workflows.sequence import (
    SequenceWorkflowError,
    _motion_text_cues,
    _operations_from_plan,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "roteiro_einstein_exame.txt"


def _blocks(*pairs):
    return tuple(TextBlock(text, weight) for text, weight in pairs)


def _event(**kwargs):
    base = dict(
        event_id="type_001",
        blocks=_blocks(("QUANDO SE REPETE", "micro"), ("FAMILIAR", "massive")),
        role="statement",
        layout="stacked_hierarchy",
        motion="stagger_rise",
        start_seconds=1.0,
        end_seconds=4.0,
    )
    base.update(kwargs)
    return MotionTextEvent(**base)


class TextBlockTests(unittest.TestCase):
    """A block is copy, and copy is data."""

    def test_markup_is_refused(self):
        with self.assertRaisesRegex(TypographyError, "markup"):
            TextBlock("{\\an8}X", "massive")

    def test_control_characters_are_refused(self):
        # tabs and newlines are collapsed to spaces first, on purpose; anything
        # else in the control range is copy that must never reach a subtitle
        with self.assertRaisesRegex(TypographyError, "control"):
            TextBlock("A\x07B", "massive")
        self.assertEqual(TextBlock("A\tB", "massive").text, "A B")

    def test_an_unknown_weight_is_refused(self):
        with self.assertRaises(TypographyError):
            TextBlock("X", "enormous")

    def test_a_block_may_not_become_a_paragraph(self):
        with self.assertRaises(TypographyError):
            TextBlock("um dois tres quatro cinco seis sete oito nove", "small")

    def test_whitespace_is_collapsed(self):
        self.assertEqual(TextBlock("  A   B ", "small").text, "A B")


class MotionTextEventTests(unittest.TestCase):
    """The invariants that keep this layer from decaying into a caption."""

    def test_a_multi_block_event_must_set_two_weights(self):
        with self.assertRaisesRegex(TypographyError, "two weights"):
            _event(blocks=_blocks(("A", "massive"), ("B", "massive")))

    def test_a_layout_refuses_a_block_count_it_was_not_composed_for(self):
        with self.assertRaisesRegex(TypographyError, "does not compose"):
            _event(layout="dominant_word")

    def test_every_layout_accepts_at_least_one_block_count(self):
        from video_generator.domain.typography import _LAYOUT_BLOCKS, _LAYOUT_WEIGHTS

        self.assertEqual(set(_LAYOUT_BLOCKS), set(TEXT_LAYOUTS))
        for layout, counts in _LAYOUT_BLOCKS.items():
            for count in counts:
                weights = _LAYOUT_WEIGHTS[layout][count]
                self.assertEqual(len(weights), count)
                self.assertTrue(set(weights) <= set(BLOCK_WEIGHTS))
                if count > 1:
                    self.assertGreater(len(set(weights)), 1, layout)

    def test_an_event_too_short_to_read_is_refused(self):
        with self.assertRaisesRegex(TypographyError, "400 ms"):
            _event(start_seconds=1.0, end_seconds=1.2)

    def test_round_trips_through_a_dict(self):
        event = _event(beat_id="shot_1", importance=0.7, rationale="why")
        self.assertEqual(MotionTextEvent.from_dict(event.to_dict()), event)

    def test_an_unknown_key_is_refused(self):
        payload = _event().to_dict()
        payload["colour"] = "red"
        with self.assertRaises(TypographyError):
            MotionTextEvent.from_dict(payload)


class DerivationTests(unittest.TestCase):
    """The type is a reading of the sentence, never a crop of it."""

    NARRATION = "Uma informacao vista muitas vezes comeca a parecer familiar."

    def test_a_contiguous_run_of_the_narration_is_refused(self):
        self.assertFalse(_is_derived(("VISTA MUITAS VEZES",), self.NARRATION))

    def test_a_recomposition_is_accepted(self):
        self.assertTrue(_is_derived(("QUANDO SE REPETE", "FAMILIAR"), self.NARRATION))

    def test_one_word_is_emphasis_rather_than_a_quote(self):
        self.assertTrue(_is_derived(("FAMILIAR",), self.NARRATION))

    def test_a_composition_long_enough_to_be_a_caption_is_refused(self):
        self.assertFalse(
            _is_derived(("A B C D E", "F G H I J"), self.NARRATION)
        )


class PolicyTests(unittest.TestCase):
    def test_unknown_keys_are_refused(self):
        with self.assertRaises(TypographyError):
            MotionTypographyPolicy.from_dict({"min_events": 2, "colour": "red"})

    def test_min_may_not_exceed_max(self):
        with self.assertRaises(TypographyError):
            MotionTypographyPolicy(min_events=9, max_events=4)

    def test_round_trips(self):
        policy = MotionTypographyPolicy.from_dict({"min_events": 3, "max_events": 6})
        self.assertEqual(
            MotionTypographyPolicy.from_dict(policy.to_dict()).to_dict(),
            policy.to_dict(),
        )


class SpokenWordTests(unittest.TestCase):
    """Timing comes from the measured voice when the voice is available."""

    CUES = (
        ("Uma informacao vista", 0.0, 1.5),
        ("muitas vezes comeca a parecer familiar.", 1.5, 4.0),
    )

    def test_words_are_spread_inside_each_measured_cue(self):
        words = spoken_words(self.CUES)
        self.assertEqual([w.folded for w in words][:3], ["uma", "informacao", "vista"])
        self.assertAlmostEqual(words[0].start_seconds, 0.0, places=6)
        self.assertAlmostEqual(words[-1].end_seconds, 4.0, places=6)
        for earlier, later in zip(words, words[1:]):
            self.assertLessEqual(earlier.end_seconds, later.start_seconds + 1e-9)

    def test_a_cue_out_of_order_is_refused(self):
        with self.assertRaises(TypographyError):
            spoken_words((("a b", 2.0, 3.0), ("c d", 0.5, 1.0)))

    def test_alignment_reports_a_matching_stream(self):
        words = spoken_words(self.CUES)
        stream = "Uma informacao vista muitas vezes comeca a parecer familiar.".split()
        self.assertGreater(stream_alignment(stream, words), 0.9)

    def test_alignment_reports_a_drifted_stream(self):
        words = spoken_words(self.CUES)
        self.assertLess(stream_alignment(["nada", "disso", "aqui"], words), 0.75)


class PlannedLayerTests(unittest.TestCase):
    """The layer, planned against a real script."""

    @classmethod
    def setUpClass(cls):
        script = NarrativeScript.from_text(
            "einstein", FIXTURE.read_text(encoding="utf-8"),
            total_duration_seconds=270.0,
        )
        cls.scene_plan = plan_scenes(script)
        cls.shot_plan, _ = plan_shots(
            cls.scene_plan, orientation="landscape", semantic=True
        )
        cls.events = plan_shot_motion_typography(cls.scene_plan, cls.shot_plan)

    def test_the_layer_is_a_dense_edit_but_not_a_caption_track(self):
        # the editorial-density contract: many interventions, packed unevenly,
        # but still bounded and still not one graphic per spoken word
        self.assertGreaterEqual(len(self.events), 20)
        self.assertLessEqual(len(self.events), DEFAULT_TYPOGRAPHY_POLICY.max_events)
        spoken = sum(
            len(row[1].split())
            for row in __import__(
                "video_generator.domain.planning", fromlist=["narration_slices"]
            ).narration_slices(self.scene_plan, self.shot_plan)
        )
        # a caption track would have one cue every two or three words
        self.assertLess(len(self.events), spoken / 6)

    def test_intensity_is_spent_unevenly(self):
        levels = {e.intensity for e in self.events}
        # a real edit leans harder on some beats than others
        self.assertGreater(len(levels), 1)
        for event in self.events:
            self.assertIn(event.intensity, ("low", "medium", "high", "peak"))
            self.assertIn(event.intent, __import__(
                "video_generator.domain.typography", fromlist=["EDITORIAL_INTENTS"]
            ).EDITORIAL_INTENTS)
            self.assertIn(event.surface, ("bare", "scrim", "card"))

    def test_a_built_statement_arrives_in_ordered_touching_fragments(self):
        chains: dict[str, list] = {}
        for event in self.events:
            if event.chain_id is not None:
                chains.setdefault(event.chain_id, []).append(event)
        # if the script offers no chainable sentence that is allowed; but when
        # a chain exists it must be whole, ordered and contiguous
        for chain_id, parts in chains.items():
            parts.sort(key=lambda e: e.start_seconds)
            self.assertGreaterEqual(len(parts), 2, chain_id)
            self.assertEqual(
                [p.chain_position for p in parts], list(range(len(parts))), chain_id
            )
            self.assertTrue(all(p.chain_length == len(parts) for p in parts), chain_id)
            for earlier, later in zip(parts, parts[1:]):
                self.assertLessEqual(earlier.start_seconds, later.start_seconds)

    def test_no_stretch_stays_dark_for_long(self):
        end = max(e.end_seconds for e in self.events)
        covered = sorted((e.start_seconds, e.end_seconds) for e in self.events)
        cursor = 0.0
        longest = 0.0
        for start, finish in covered:
            longest = max(longest, start - cursor)
            cursor = max(cursor, finish)
        longest = max(longest, end - cursor)
        # the coverage pass keeps the frame from going quiet for a whole shot
        self.assertLess(longest, DEFAULT_TYPOGRAPHY_POLICY.max_dark_seconds + 3.0)

    def test_events_are_ordered_and_never_overlap(self):
        for earlier, later in zip(self.events, self.events[1:]):
            self.assertLessEqual(earlier.end_seconds, later.start_seconds)

    def test_no_event_reproduces_a_run_of_its_own_narration(self):
        from video_generator.domain.editorial import read_beats
        from video_generator.domain.planning import narration_slices

        beats = {
            beat.beat_id: beat
            for beat in read_beats(narration_slices(self.scene_plan, self.shot_plan))
        }
        for event in self.events:
            source = event.rationale.split("from: ")[-1]
            # a built statement, a parallel run and a lifted quote are *meant*
            # to reproduce the sentence in stages; every other event is still a
            # reading of it, never a crop
            if event.intent not in (
                "statement_build", "sequence", "quote_fragment"
            ):
                self.assertTrue(
                    _is_derived([block.text for block in event.blocks], source),
                    f"{event.text!r} copies {source!r}",
                )
            self.assertIn(event.beat_id, beats)

    def test_every_multi_block_event_carries_a_hierarchy(self):
        for event in self.events:
            if len(event.blocks) > 1:
                self.assertGreater(len({b.weight for b in event.blocks}), 1)

    def test_roles_layouts_and_motions_stay_inside_the_vocabulary(self):
        for event in self.events:
            self.assertIn(event.role, TEXT_ROLES)
            self.assertIn(event.layout, TEXT_LAYOUTS)
            self.assertIn(event.motion, TEXT_MOTIONS)

    def test_the_same_layout_never_runs_twice_back_to_back(self):
        for earlier, later in zip(self.events, self.events[1:]):
            self.assertNotEqual(earlier.layout, later.layout)

    def test_the_accent_is_never_spent_on_two_events_running(self):
        accented = [any(b.accent for b in e.blocks) for e in self.events]
        for earlier, later in zip(accented, accented[1:]):
            self.assertFalse(earlier and later)

    def test_planning_is_deterministic(self):
        again = plan_shot_motion_typography(self.scene_plan, self.shot_plan)
        self.assertEqual(
            [e.to_dict() for e in again], [e.to_dict() for e in self.events]
        )

    def test_a_stricter_cap_is_obeyed(self):
        events = plan_shot_motion_typography(
            self.scene_plan,
            self.shot_plan,
            typography_policy=MotionTypographyPolicy(min_events=2, max_events=4),
        )
        self.assertLessEqual(len(events), 4)

    def test_measured_captions_move_the_events_onto_the_voice(self):
        # a caption track that runs at a different pace from the planned shots:
        # every event must follow the words, not the shot boundaries
        text = " ".join(
            " ".join(row[1].split()) for row in
            __import__(
                "video_generator.domain.planning", fromlist=["narration_slices"]
            ).narration_slices(self.scene_plan, self.shot_plan)
        )
        words = text.split()
        cues = tuple(
            (" ".join(words[index:index + 4]), index * 0.5, index * 0.5 + 0.5)
            for index in range(0, len(words), 4)
        )
        timed = plan_shot_motion_typography(
            self.scene_plan, self.shot_plan, caption_cues=cues
        )
        self.assertTrue(timed)
        self.assertNotEqual(
            [e.start_seconds for e in timed],
            [e.start_seconds for e in self.events],
        )

    def test_captions_for_a_different_script_are_ignored_rather_than_trusted(self):
        cues = tuple(
            ("palavra nenhuma daqui", index * 2.0, index * 2.0 + 2.0)
            for index in range(40)
        )
        fallback = plan_shot_motion_typography(
            self.scene_plan, self.shot_plan, caption_cues=cues
        )
        self.assertEqual(
            [e.start_seconds for e in fallback],
            [e.start_seconds for e in self.events],
        )


class OperationTests(unittest.TestCase):
    def test_the_operation_carries_blocks_layouts_and_the_two_faces(self):
        operation = motion_typography_operation(
            [_event()],
            visual_style=DARK_DOCUMENTARY_V1,
            display_font="Segoe UI",
            support_font="Bahnschrift",
            muted="#8A8F96",
        )
        self.assertEqual(operation.kind, "motion_typography")
        item = operation.parameters["items"][0]
        self.assertEqual(item["layout"], "stacked_hierarchy")
        self.assertEqual([b["weight"] for b in item["blocks"]], ["micro", "massive"])
        style = operation.parameters["style"]
        self.assertEqual(style["font_name"], "Segoe UI")
        self.assertEqual(style["support_font_name"], "Bahnschrift")
        self.assertEqual(style["muted"], "&H00968F8A")

    def test_an_empty_layer_is_refused(self):
        with self.assertRaises(TypographyError):
            motion_typography_operation([])


def _minimal_plan(tmp, extra):
    first, second = tmp / "a.jpg", tmp / "b.jpg"
    for path in (first, second):
        path.write_bytes(b"x")
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


class WorkflowParsingTests(unittest.TestCase):
    """What the sequence workflow accepts, and what it refuses."""

    def _parameters(self, **overrides):
        item = {
            "start_seconds": 0.5,
            "end_seconds": 3.0,
            "layout": "small_plus_massive",
            "motion": "stagger_rise",
            "blocks": [
                {"text": "QUANDO SE REPETE", "weight": "small"},
                {"text": "FAMILIAR", "weight": "massive", "accent": True},
            ],
        }
        item.update(overrides)
        return {"items": [item]}

    def test_a_well_formed_layer_parses(self):
        cues, style = _motion_text_cues(self._parameters())
        self.assertEqual(len(cues), 1)
        self.assertEqual(cues[0].layout, "small_plus_massive")
        self.assertEqual(len(cues[0].blocks), 2)
        self.assertTrue(cues[0].blocks[1].accent)
        self.assertIsNone(style)

    def test_markup_in_a_block_is_refused(self):
        with self.assertRaisesRegex(SequenceWorkflowError, "markup"):
            _motion_text_cues(
                self._parameters(blocks=[{"text": "{\\an8}X", "weight": "massive"}])
            )

    def test_an_unknown_layout_is_refused(self):
        with self.assertRaisesRegex(SequenceWorkflowError, "layout"):
            _motion_text_cues(self._parameters(layout="diagonal"))

    def test_overlapping_events_are_refused(self):
        parameters = self._parameters()
        parameters["items"].append(dict(parameters["items"][0], start_seconds=1.0))
        with self.assertRaisesRegex(SequenceWorkflowError, "non-overlapping"):
            _motion_text_cues(parameters)

    def test_a_font_name_that_could_become_syntax_is_refused(self):
        parameters = self._parameters()
        parameters["style"] = {"font_name": "Seg{\\an8}oe"}
        with self.assertRaisesRegex(SequenceWorkflowError, "font family"):
            _motion_text_cues(parameters)

    def test_a_style_carries_both_faces_and_the_muted_colour(self):
        parameters = self._parameters()
        parameters["style"] = {
            "font_name": "Segoe UI",
            "support_font_name": "Bahnschrift",
            "muted": "&H00968F8A",
        }
        _cues, style = _motion_text_cues(parameters)
        self.assertEqual(style.font_name, "Segoe UI")
        self.assertEqual(style.support_font_name, "Bahnschrift")
        self.assertEqual(style.muted, "&H00968F8A")

    def test_a_plan_with_the_layer_yields_cues(self):
        with tempfile.TemporaryDirectory() as d:
            operation = motion_typography_operation(
                [_event()], visual_style=DARK_DOCUMENTARY_V1, support_font="Bahnschrift"
            )
            parsed = _operations_from_plan(_minimal_plan(Path(d), extra=(operation,)))
            self.assertEqual(len(parsed.motion_text), 1)
            self.assertEqual(parsed.captions, ())
            self.assertEqual(parsed.text_events, ())
            self.assertEqual(parsed.text_style.support_font_name, "Bahnschrift")

    def test_a_plan_without_the_layer_parses_exactly_as_before(self):
        with tempfile.TemporaryDirectory() as d:
            parsed = _operations_from_plan(_minimal_plan(Path(d), extra=()))
            self.assertEqual(parsed.motion_text, ())
            self.assertIsNone(parsed.text_style)

    def test_two_layers_are_refused(self):
        with tempfile.TemporaryDirectory() as d:
            first = motion_typography_operation([_event()])
            second = motion_typography_operation(
                [_event(event_id="type_002", start_seconds=4.5, end_seconds=5.5)],
                operation_id="motion_typography_2",
            )
            with self.assertRaises(SequenceWorkflowError):
                _operations_from_plan(_minimal_plan(Path(d), extra=(first, second)))


class LayoutGeometryTests(unittest.TestCase):
    """Where the pixels land."""

    STYLE = TextStyleSpec(
        font_name="Segoe UI", support_font_name="Bahnschrift", muted="&H00968F8A"
    )
    WIDTH, HEIGHT = 1920, 1080

    def _cue(self, layout, count):
        from video_generator.domain.typography import _LAYOUT_WEIGHTS

        weights = _LAYOUT_WEIGHTS[layout][count]
        return MotionTextCue(
            blocks=tuple(
                MotionTextBlockCue("IRRACIONALIDADE"[: 4 + index * 5], weight)
                for index, weight in enumerate(weights)
            ),
            start_seconds=0.0,
            end_seconds=3.0,
            layout=layout,
            motion="fade_rise",
        )

    def test_every_layout_places_every_block_inside_the_frame(self):
        from video_generator.domain.typography import _LAYOUT_BLOCKS

        for layout, counts in _LAYOUT_BLOCKS.items():
            for count in counts:
                placements = _motion_placements(
                    self._cue(layout, count), self.WIDTH, self.HEIGHT, self.STYLE
                )
                self.assertEqual(len(placements), count, layout)
                for anchor, x, y, size, _block in placements:
                    self.assertGreaterEqual(x, 0, layout)
                    self.assertLessEqual(x, self.WIDTH, layout)
                    self.assertGreaterEqual(y, 0, layout)
                    self.assertLess(y + size * 1.2, self.HEIGHT, layout)
                    self.assertIn(anchor, ("left", "right", "centre"))

    def test_a_long_word_is_shrunk_rather_than_pushed_off_the_frame(self):
        short = MotionTextCue(
            (MotionTextBlockCue("EGO", "massive"),), 0.0, 2.0, "dominant_word", "scale_in"
        )
        long = MotionTextCue(
            (MotionTextBlockCue("RESPONSABILIDADES", "massive"),),
            0.0, 2.0, "dominant_word", "scale_in",
        )
        short_size = _motion_placements(short, self.WIDTH, self.HEIGHT, self.STYLE)[0][3]
        long_size = _motion_placements(long, self.WIDTH, self.HEIGHT, self.STYLE)[0][3]
        self.assertLess(long_size, short_size)
        self.assertLess(len("RESPONSABILIDADES") * 0.605 * long_size, self.WIDTH)

    def test_the_scale_contrast_between_support_and_display_is_real(self):
        placements = _motion_placements(
            self._cue("small_plus_massive", 2), self.WIDTH, self.HEIGHT, self.STYLE
        )
        support, display = placements[0][3], placements[1][3]
        self.assertGreater(display, support * 2.5)

    def test_split_statement_is_a_diagonal(self):
        placements = _motion_placements(
            self._cue("split_statement", 2), self.WIDTH, self.HEIGHT, self.STYLE
        )
        (first_anchor, _fx, first_y, _fs, _fb) = placements[0]
        (second_anchor, _sx, second_y, _ss, _sb) = placements[1]
        self.assertEqual((first_anchor, second_anchor), ("left", "right"))
        self.assertGreater(second_y, first_y + self.HEIGHT * 0.4)


class AssEmissionTests(unittest.TestCase):
    """What libass is actually handed."""

    STYLE = TextStyleSpec(
        font_name="Segoe UI", support_font_name="Bahnschrift", muted="&H00968F8A"
    )

    def _lines(self, motion="stagger_rise"):
        cue = MotionTextCue(
            blocks=(
                MotionTextBlockCue("QUANDO SE REPETE", "micro"),
                MotionTextBlockCue("FAMILIAR", "massive", accent=True),
            ),
            start_seconds=2.0,
            end_seconds=5.0,
            layout="stacked_hierarchy",
            motion=motion,
        )
        return _motion_dialogues(cue, 2.0, 5.0, 1920, 1080, self.STYLE)

    def test_one_dialogue_per_block_on_the_emphasis_layer(self):
        lines = self._lines()
        self.assertEqual(len(lines), 2)
        for line in lines:
            self.assertTrue(line.startswith("Dialogue: 1,"))
            self.assertIn(",Motion,,", line)

    def test_the_two_faces_and_the_two_colours_reach_the_override(self):
        support, display = self._lines()
        self.assertIn("\\fnBahnschrift", support)
        self.assertIn("\\c&H968F8A&", support)
        self.assertIn("\\fnSegoe UI", display)
        self.assertIn("\\c&H3CA3E5&", display)
        self.assertIn("\\b1", display)

    def test_the_display_block_arrives_after_the_support_block(self):
        support, display = self._lines()
        self.assertLess(support.split(",")[1], display.split(",")[1])

    def test_a_non_staggered_motion_still_leads_slightly(self):
        support, display = self._lines(motion="fade_rise")
        self.assertLessEqual(support.split(",")[1], display.split(",")[1])

    def test_each_motion_emits_its_own_override(self):
        self.assertIn("\\t(0,260", "".join(self._lines(motion="scale_in")))
        self.assertIn("\\clip(", "".join(self._lines(motion="masked_reveal")))
        self.assertIn("\\move(", "".join(self._lines(motion="fade_rise")))

    def test_the_style_block_names_the_display_face(self):
        self.assertIn("Style: Motion,Segoe UI,", _motion_style(1920, 1080, self.STYLE))


class ComposeValidationTests(unittest.TestCase):
    """The adapter refuses an unsafe or impossible layer before FFmpeg runs."""

    def _images(self, tmp):
        paths = []
        for name in ("a.jpg", "b.jpg"):
            path = tmp / name
            path.write_bytes(b"x")
            paths.append(SequenceImage(str(path), 3.0, "cover"))
        return paths

    def _compose(self, tmp, **kwargs):
        return compose_video_sequence(
            self._images(tmp), tmp / "out.mp4", canvas=(1920, 1080), **kwargs
        )

    def _cue(self, text="X", start=0.0, end=1.0, **kwargs):
        return MotionTextCue(
            (MotionTextBlockCue(text, kwargs.pop("weight", "massive")),),
            start,
            end,
            kwargs.pop("layout", "dominant_word"),
            kwargs.pop("motion", "scale_in"),
        )

    def test_rejects_markup_in_a_block(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(FFmpegError, "markup"):
                self._compose(Path(d), motion_text=(self._cue("{\\an8}X"),))

    def test_rejects_an_event_past_the_end_of_the_timeline(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(FFmpegError, "must not exceed"):
                self._compose(Path(d), motion_text=(self._cue(end=99.0),))

    def test_rejects_overlapping_events(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(FFmpegError, "non-overlapping"):
                self._compose(
                    Path(d),
                    motion_text=(
                        self._cue("A", 0.0, 3.0),
                        self._cue("B", 1.0, 4.0),
                    ),
                )

    def test_rejects_an_unknown_layout_or_motion(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(FFmpegError, "layout"):
                self._compose(Path(d), motion_text=(self._cue(layout="diagonal"),))
            with self.assertRaisesRegex(FFmpegError, "motion"):
                self._compose(Path(d), motion_text=(self._cue(motion="explode"),))

    def test_rejects_the_layer_without_a_canvas(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            with self.assertRaisesRegex(FFmpegError, "canvas"):
                compose_video_sequence(
                    self._images(tmp), tmp / "out.mp4", motion_text=(self._cue(),)
                )

    def test_rejects_a_font_name_that_could_become_syntax(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(FFmpegError, "font name"):
                self._compose(
                    Path(d),
                    motion_text=(self._cue(),),
                    text_style=TextStyleSpec(font_name="Seg{\\an8}oe"),
                )


class ManifestValidationTests(unittest.TestCase):
    """A plan carrying the layer must not fail validation on form."""

    def test_the_tail_walker_understands_the_operation(self):
        from video_generator.validation.manifest import _motion_typography_match

        operation = motion_typography_operation(
            [_event()], visual_style=DARK_DOCUMENTARY_V1, support_font="Bahnschrift"
        )
        self.assertTrue(_motion_typography_match(operation, 10.0))
        self.assertFalse(_motion_typography_match(operation, 2.0))

    def test_a_malformed_block_is_refused(self):
        from video_generator.validation.manifest import _motion_typography_match

        broken = EditOperation(
            operation_id="motion_typography",
            kind="motion_typography",
            parameters={
                "items": [
                    {
                        "start_seconds": 0.0,
                        "end_seconds": 2.0,
                        "blocks": [{"text": "X", "weight": "enormous"}],
                    }
                ]
            },
        )
        self.assertFalse(_motion_typography_match(broken, 10.0))


class SemanticPlanEmitsTheLayerTests(unittest.TestCase):
    """The CLI path: the plan carries typography instead of an emphasis line."""

    def test_the_layer_replaces_text_events_in_the_emitted_plan(self):
        from video_generator.cli import main

        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            bindings = tmp / "assets.json"
            out_dir = tmp / "plan"
            script = tmp / "script.txt"
            script.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
            # a first pass only to learn the asset ids this script needs
            self.assertEqual(
                main(
                    [
                        "plan-scenes", "--from-text", str(script),
                        "--total-duration", "270", "--seed", "0",
                        "--target", "1920x1080:cover", "--semantic",
                        "--out-dir", str(out_dir),
                    ]
                ),
                0,
            )
            requirements = json.loads(
                (out_dir / "asset-requirements.json").read_text(encoding="utf-8")
            )
            asset = tmp / "asset.jpg"
            asset.write_bytes(b"x")
            bindings.write_text(
                json.dumps(
                    {r["asset_id"]: str(asset) for r in requirements["requirements"]}
                ),
                encoding="utf-8",
            )
            plan_path = tmp / "edit-plan.json"
            self.assertEqual(
                main(
                    [
                        "plan-scenes", "--from-text", str(script),
                        "--total-duration", "270", "--seed", "0",
                        "--target", "1920x1080:cover", "--semantic",
                        "--motion-typography", str(tmp / "typography.json"),
                        "--assets", str(bindings),
                        "--emit-edit-plan", str(plan_path),
                        "--out-dir", str(out_dir), "--force",
                    ]
                ),
                0,
            )
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            kinds = [op["kind"] for op in plan["operations"]]
            self.assertIn("motion_typography", kinds)
            self.assertNotIn("text_events", kinds)
            self.assertNotIn("captions", kinds)
            emitted = json.loads(
                (tmp / "typography.json").read_text(encoding="utf-8")
            )
            self.assertTrue(emitted["events"])
            self.assertEqual(
                len(emitted["events"]),
                len(
                    next(
                        op for op in plan["operations"]
                        if op["kind"] == "motion_typography"
                    )["parameters"]["items"]
                ),
            )

    def test_the_flag_requires_the_semantic_reading(self):
        from video_generator.cli import main

        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            script = tmp / "script.txt"
            script.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
            self.assertEqual(
                main(
                    [
                        "plan-scenes", "--from-text", str(script),
                        "--total-duration", "270", "--seed", "0",
                        "--target", "1920x1080:cover",
                        "--motion-typography", str(tmp / "typography.json"),
                        "--out-dir", str(tmp / "plan"),
                    ]
                ),
                2,
            )


if __name__ == "__main__":
    unittest.main()
