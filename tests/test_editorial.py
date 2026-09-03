"""Tests for the editorial semantics layer.

Covers the NarrationBeat reading (concept, entities, emotion, visual intent,
candidate queries, importance, role), the TextEvent emphasis layer and its
independence from captions, the hook policy, and the optional VisualStyle.
"""

import unittest
from pathlib import Path

from video_generator.domain.editorial import (
    DARK_DOCUMENTARY_V1,
    DEFAULT_EDITORIAL_POLICY,
    DEFAULT_HOOK_POLICY,
    EDITORIAL_ROLES,
    ConceptEntry,
    EditorialError,
    EditorialPolicy,
    HookPolicy,
    NarrationBeat,
    TextEvent,
    VisualStyle,
    plan_text_events,
    read_beats,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "roteiro_einstein_exame.txt"

EINSTEIN_SLICE = "Einstein nao conseguiu entrar na universidade na primeira tentativa."
EXAM_SLICE = "Em 1895 ele foi reprovado no exame de admissao do Politecnico."


def _beats(*texts, policy=DEFAULT_EDITORIAL_POLICY):
    return read_beats(
        [(f"scene_01_shot_{i:02d}", t) for i, t in enumerate(texts, start=1)],
        policy=policy,
    )


class ReadBeatsTests(unittest.TestCase):
    def test_a_beat_reads_a_concept_and_a_visual_intent_from_narration(self):
        (beat,) = _beats(EXAM_SLICE)
        self.assertEqual(beat.beat_id, "scene_01_shot_01")
        self.assertIn(beat.editorial_role, EDITORIAL_ROLES)
        # the concept is the filmable idea, not the first noun it saw
        self.assertIn(beat.concept, ("reprovado", "exame", "admissao", "politecnico"))
        self.assertTrue(beat.visual_intent.startswith("mostrar: "))
        self.assertGreater(len(beat.visual_intent), len("mostrar: "))

    def test_a_query_carries_more_context_than_a_bare_entity(self):
        (beat,) = _beats(EINSTEIN_SLICE)
        self.assertIn("Einstein", beat.entities)
        best = beat.asset_queries[0]
        # the leading query describes a scene, it is not just the person's name
        self.assertNotEqual(best.strip().lower(), "einstein")
        self.assertGreaterEqual(len(best.split()), 2)

    def test_the_exam_beat_asks_for_an_exam_not_only_for_the_person(self):
        (beat,) = _beats(EXAM_SLICE)
        joined = " ".join(beat.asset_queries).lower()
        self.assertTrue(
            any(word in joined for word in ("exam", "rejected", "admission", "university")),
            joined,
        )

    def test_queries_are_english_so_they_can_match_a_stock_library(self):
        (beat,) = _beats(EXAM_SLICE)
        # the intent stays in the script's language for the human reviewer...
        self.assertNotEqual(beat.visual_intent, beat.asset_queries[0])
        # ...while at least one query is drawn from the English lexicon
        self.assertTrue(
            any(
                entry.visual in " ".join(beat.asset_queries)
                for entry in DEFAULT_EDITORIAL_POLICY.concept_lexicon.values()
            )
        )

    def test_multiple_candidate_queries_when_the_beat_supports_them(self):
        (beat,) = _beats(EXAM_SLICE)
        self.assertGreaterEqual(len(beat.asset_queries), 2)
        self.assertEqual(len(set(beat.asset_queries)), len(beat.asset_queries))

    def test_a_term_that_dominates_the_script_never_leads_a_query(self):
        # "Einstein" in every beat: it is the subject of the video, so it must
        # not be what every single shot asks a stock library for
        beats = _beats(
            "Einstein foi reprovado no exame.",
            "Einstein foi para a escola em Aarau.",
            "Einstein entrou no politecnico depois.",
            "Einstein trabalhou em uma repartição de patentes.",
        )
        leading = [b.asset_queries[0].lower() for b in beats]
        self.assertTrue(all(not q.startswith("einstein") for q in leading), leading)

    def test_an_emotion_colours_the_query(self):
        (beat,) = _beats("Ele foi recusado e ficou frustrado com a prova.")
        self.assertIsNotNone(beat.emotion)
        self.assertIn(beat.emotion, " ".join(beat.asset_queries))

    def test_a_fragment_with_no_concept_borrows_the_scene_context(self):
        scene = "Ele pediu uma exceção para prestar o exame de admissao."
        beats = read_beats([("scene_01_shot_01", "Ele pediu uma exceção. A exceção foi", scene)])
        # without the context it would ask for a picture of "pediu"
        self.assertNotEqual(beats[0].concept, "pediu")
        self.assertGreaterEqual(len(beats[0].asset_queries[0].split()), 2)

    def test_a_lexicon_phrase_is_never_mangled_by_de_duplication(self):
        (beat,) = _beats("Existe uma versao dessa historia.")
        self.assertIn("two newspaper pages side by side", beat.asset_queries)

    def test_the_opening_beats_are_read_as_hook(self):
        beats = _beats(*[f"Frase numero {n} do roteiro completo." for n in range(1, 21)])
        self.assertEqual(beats[0].editorial_role, "hook")
        self.assertNotEqual(beats[-1].editorial_role, "hook")

    def test_a_capitalised_verb_opening_a_sentence_is_not_a_name(self):
        # "Estudou." is capitalised because a sentence began, not because it
        # names anyone; letting it through puts a verb in front of a query
        beats = _beats(
            "Estudou. Tem uma carreira respeitavel.",
            "A carreira dele seguiu adiante.",
        )
        for beat in beats:
            self.assertNotIn("Estudou", beat.entities)
            self.assertFalse(beat.asset_queries[0].lower().startswith("estudou"))

    def test_a_name_seen_mid_sentence_is_kept(self):
        beats = _beats(
            "O nome dele era Albert Einstein.",
            "Einstein foi reprovado no exame.",
        )
        self.assertIn("Einstein", beats[1].entities)

    def test_reading_is_deterministic(self):
        text = FIXTURE.read_text(encoding="utf-8")
        slices = [(f"b{i}", p) for i, p in enumerate(text.split("\n\n")) if p.strip()]
        first = read_beats(slices)
        second = read_beats(slices)
        self.assertEqual(
            [b.to_dict() for b in first], [b.to_dict() for b in second]
        )

    def test_beat_round_trips_through_its_dict(self):
        (beat,) = _beats(EXAM_SLICE)
        self.assertEqual(NarrationBeat.from_dict(beat.to_dict()), beat)

    def test_rejects_an_empty_slice_list(self):
        with self.assertRaises(EditorialError):
            read_beats([])

    def test_rejects_a_beat_with_no_queries(self):
        with self.assertRaises(EditorialError):
            NarrationBeat(
                beat_id="b", narration="n", concept="c", entities=(), emotion=None,
                visual_intent="i", asset_queries=(), importance=0.5,
                editorial_role="claim",
            )

    def test_rejects_an_unknown_editorial_role(self):
        with self.assertRaises(EditorialError):
            NarrationBeat(
                beat_id="b", narration="n", concept="c", entities=(), emotion=None,
                visual_intent="i", asset_queries=("x",), importance=0.5,
                editorial_role="banger",
            )


class EditorialPolicyTests(unittest.TestCase):
    def test_the_lexicon_is_data_an_operator_can_extend(self):
        policy = EditorialPolicy(
            concept_lexicon={"skate": ConceptEntry("skateboard on asphalt", "object")}
        )
        (beat,) = _beats("O skate estava parado na calçada.", policy=policy)
        self.assertEqual(beat.asset_queries[0], "skateboard on asphalt")
        self.assertEqual(beat.shot_type_hint, "object")

    def test_entity_aliases_translate_a_named_place(self):
        policy = EditorialPolicy(entity_aliases={"zurique": "Zurich"})
        (beat,) = _beats("Ele fez a prova em Zurique naquele ano.", policy=policy)
        self.assertIn("Zurich", beat.entities)

    def test_rejects_a_broken_lexicon(self):
        with self.assertRaises(EditorialError):
            EditorialPolicy(concept_lexicon={"x": "not a ConceptEntry"})

    def test_rejects_an_out_of_range_knob(self):
        with self.assertRaises(EditorialError):
            EditorialPolicy(dominant_term_document_ratio=1.5)
        with self.assertRaises(EditorialError):
            EditorialPolicy(max_queries_per_beat=0)


class TextEventTests(unittest.TestCase):
    def _timeline(self, beats, span=4.0):
        return {b.beat_id: (i * span, i * span + span) for i, b in enumerate(beats)}

    def test_not_every_line_becomes_a_text_event(self):
        beats = _beats(*[f"Uma frase comum sobre o assunto numero {n}." for n in range(1, 16)])
        events = plan_text_events(beats, self._timeline(beats))
        self.assertLess(len(events), len(beats))

    def test_a_date_becomes_an_emphasised_event(self):
        beats = _beats("Em 1895 ele foi reprovado no exame de admissao.")
        events = plan_text_events(beats, self._timeline(beats))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].category, "date")
        self.assertIn("1895", events[0].text)
        self.assertEqual(events[0].animation, "pop")

    def test_a_number_becomes_an_emphasised_event(self):
        beats = _beats("Vinte e seis anos depois ele recebeu o Nobel.")
        events = plan_text_events(beats, self._timeline(beats))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].category, "number")

    def test_event_text_is_taken_verbatim_from_the_narration(self):
        beats = _beats("Em 1895 ele foi reprovado no exame de admissao.")
        events = plan_text_events(beats, self._timeline(beats))
        spoken = beats[0].narration.upper()
        for word in events[0].text.split():
            self.assertIn(word.strip(".,"), spoken)

    def test_events_are_spaced_so_emphasis_does_not_become_wallpaper(self):
        beats = _beats(*[f"Em {1890 + n} algo importante aconteceu ali." for n in range(1, 12)])
        events = plan_text_events(beats, self._timeline(beats, span=1.0))
        for earlier, later in zip(events, events[1:]):
            self.assertGreaterEqual(later.start_seconds, earlier.end_seconds)

    def test_a_text_event_is_not_a_caption_cue(self):
        # captions transcribe the voice; a text event lifts a fragment, carries
        # a category, a position and an animation, and may sit anywhere on screen
        beats = _beats("Em 1895 ele foi reprovado no exame de admissao.")
        (event,) = plan_text_events(beats, self._timeline(beats))
        self.assertNotEqual(event.text, beats[0].narration)
        self.assertLess(len(event.text), len(beats[0].narration))
        self.assertIn(event.position, ("top", "middle", "lower"))
        self.assertEqual(event.beat_id, beats[0].beat_id)

    def test_an_event_never_outlives_its_shot(self):
        beats = _beats("Em 1895 ele foi reprovado no exame de admissao.")
        timings = {beats[0].beat_id: (0.0, 1.2)}
        (event,) = plan_text_events(beats, timings)
        self.assertLessEqual(event.end_seconds, 1.2)
        self.assertGreaterEqual(event.start_seconds, 0.0)

    def test_an_emphasis_span_never_crosses_a_clause_boundary(self):
        beats = _beats("Em 1896, ele se formou em Aarau e entrou no politecnico.")
        events = plan_text_events(beats, self._timeline(beats))
        # a year stands alone rather than trailing into the sentence after it
        self.assertEqual(events[0].text, "1896")

    def test_a_figure_keeps_the_unit_that_gives_it_meaning(self):
        beats = _beats("Vinte e seis anos depois ele recebeu o premio.")
        events = plan_text_events(beats, self._timeline(beats))
        self.assertEqual(events[0].category, "number")
        self.assertIn("ANOS", events[0].text)

    def test_a_span_does_not_end_on_a_function_word(self):
        beats = _beats("Em 1902 ele estava conferindo pedidos de patente.")
        events = plan_text_events(beats, self._timeline(beats))
        self.assertTrue(events)
        self.assertFalse(events[0].text.endswith(" ELE"))
        self.assertFalse(events[0].text.endswith(" DE"))

    def test_a_beat_with_no_timing_is_skipped(self):
        beats = _beats("Em 1895 ele foi reprovado no exame de admissao.")
        self.assertEqual(plan_text_events(beats, {}), ())

    def test_text_event_rejects_markup_that_could_become_subtitle_syntax(self):
        for bad in ("{\\an8}HACK", "<b>HACK</b>"):
            with self.assertRaises(EditorialError):
                TextEvent(
                    event_id="t", text=bad, start_seconds=0.0, end_seconds=1.0,
                    category="keyword", importance=0.5, position="top",
                    animation="fade",
                )

    def test_text_event_rejects_an_over_long_line(self):
        with self.assertRaises(EditorialError):
            TextEvent(
                event_id="t", text="X" * 40, start_seconds=0.0, end_seconds=1.0,
                category="keyword", importance=0.5, position="top", animation="fade",
            )

    def test_text_event_round_trips_through_its_dict(self):
        event = TextEvent(
            event_id="t", text="1895", start_seconds=0.0, end_seconds=1.0,
            category="date", importance=0.8, position="middle", animation="pop",
            beat_id="scene_01_shot_01",
        )
        self.assertEqual(TextEvent.from_dict(event.to_dict()), event)


class HookPolicyTests(unittest.TestCase):
    def test_the_hook_lets_a_weaker_beat_through(self):
        beats = _beats(*[f"Uma frase sobre a prova numero {n}." for n in range(1, 21)])
        timings = {b.beat_id: (i * 4.0, i * 4.0 + 4.0) for i, b in enumerate(beats)}
        # a bar the body cannot clear at all
        strict = EditorialPolicy(text_event_min_importance=0.95)
        without = plan_text_events(beats, timings, policy=strict, hook_policy=None)
        self.assertEqual(without, ())
        # the same beats, with the opening allowed its own lower bar
        with_hook = plan_text_events(
            beats, timings, policy=strict,
            hook_policy=HookPolicy(hook_seconds=40.0, text_event_min_importance=0.3),
        )
        self.assertGreater(len(with_hook), 0)
        # and the concession applies to the opening only
        self.assertTrue(all(e.start_seconds < 40.0 for e in with_hook))

    def test_covers_only_the_opening(self):
        policy = HookPolicy(hook_seconds=30.0)
        self.assertTrue(policy.covers(0.0))
        self.assertTrue(policy.covers(29.9))
        self.assertFalse(policy.covers(30.0))

    def test_round_trips_and_rejects_a_bad_value(self):
        self.assertEqual(HookPolicy.from_dict(DEFAULT_HOOK_POLICY.to_dict()), DEFAULT_HOOK_POLICY)
        with self.assertRaises(EditorialError):
            HookPolicy(hook_seconds=0.0)


class VisualStyleTests(unittest.TestCase):
    def test_the_default_brand_kit_is_a_named_reusable_identity(self):
        self.assertEqual(DARK_DOCUMENTARY_V1.style_id, "dark-documentary-v1")
        self.assertEqual(VisualStyle(), DARK_DOCUMENTARY_V1)

    def test_colours_convert_to_the_byte_order_libass_expects(self):
        style = VisualStyle(accent="#E5A33C")
        self.assertEqual(style.ass_colour("accent"), "&H003CA3E5")

    def test_identity_may_not_repaint_the_assets(self):
        VisualStyle(vignette_strength=0.35)
        with self.assertRaises(EditorialError):
            VisualStyle(vignette_strength=0.6)

    def test_rejects_a_colour_that_is_not_a_hex_triplet(self):
        for bad in ("black", "#FFF", "#GGGGGG"):
            with self.assertRaises(EditorialError):
                VisualStyle(accent=bad)

    def test_round_trips_through_its_dict(self):
        style = VisualStyle(accent="#3C8FE5", emphasis_font_scale=2.0)
        self.assertEqual(VisualStyle.from_dict(style.to_dict()), style)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
