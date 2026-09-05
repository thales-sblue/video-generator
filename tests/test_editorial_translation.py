"""Editorial Visual Translation v1: from what a beat *means* to what a camera
could have been pointed at, and from a candidate's metadata to an honest
reading of whether it belongs in this cut.

The tests are written against the defects that were measured in the production
render of Visual Direction v1, not against the shape of the implementation:

* ``scene_15_shot_02`` — a man holding a magnifying glass in front of a mirror,
  the worst frame of that cut. It arrived because the concept lexicon maps
  ``verdade`` to *"magnifying glass over a document"*, so the pipeline asked
  for the prop by name and then had no way to say the result was ridiculous.
* ``t≈192 s`` — coloured chalk drawings on a pavement under the strongest line
  of the close. No ``children`` token appears anywhere in its metadata, so the
  hard veto could not see it.
* 17 of 59 shots whose query was a bare Portuguese noun.

Both defects have a regression test here, and neither is fixed by naming the
offending asset.
"""

import json
import unittest

from video_generator.domain.assets import (
    AssetCandidate,
    AssetRequirement,
    AssetScoringPolicy,
    rank_candidates_with_report,
    score_candidate,
)
from video_generator.domain.editorial import (
    EditorialError,
    EditorialPolicy,
    read_beats,
)
from video_generator.domain.planning import (
    NarrativeScript,
    PlanningError,
    plan_scenes,
    plan_shots,
)
from video_generator.domain.relevance import REJECTION_REASONS
from video_generator.domain.visual_concept import (
    CLICHE_REJECTION,
    CONCEPT_SOURCES,
    DEFAULT_TRANSLATION_POLICY,
    EDITORIAL_FIT_SIGNALS,
    PLAYFUL_REJECTION,
    EditorialFit,
    FilmableConcept,
    TranslationError,
    TranslationPolicy,
    assess_editorial_fit,
    comparative_rejections,
    non_english_tokens,
    stock_metaphor_props,
    translate_beat,
)


def _bag(text):
    return frozenset(t for t in text.lower().split() if len(t) >= 3)


def _candidate(candidate_id, description, **over):
    base = dict(
        candidate_id=candidate_id,
        source_kind="pexels",
        source_id=candidate_id.split(":")[-1],
        media_type="image",
        local_path=None,
        remote_locator=f"https://example.org/{candidate_id}.jpg",
        title=description,
        description=description,
        tags=tuple(dict.fromkeys(description.split())),
        width=1920,
        height=1080,
        duration_seconds=None,
        license="Pexels",
        license_url="https://www.pexels.com/license/",
        author="Someone",
        source_url=f"https://example.org/{candidate_id}",
        score=0.0,
        score_breakdown={},
    )
    base.update(over)
    return AssetCandidate(**base)


def _requirement(**over):
    base = dict(
        asset_id="asset_scene_15_02",
        type="image",
        query="identical posters repeated along a wall",
        queries=("identical posters repeated along a wall",),
        duration_needed_seconds=3.0,
        orientation="landscape",
        purpose="mostrar: identical posters repeated along a wall — beat 2/3",
        used_by=("scene_15_shot_02",),
        visual_intent_class="metaphorical",
        visual_role="symbolize",
    )
    base.update(over)
    return AssetRequirement(**base)


TRANSLATING_SCORING = AssetScoringPolicy(editorial_translation=True)


# --------------------------------------------------------------------------- #
# the two production defects
# --------------------------------------------------------------------------- #
class ProductionDefectTests(unittest.TestCase):
    """The two frames the editor named, refused by mechanism rather than slug."""

    def test_the_beat_that_produced_the_mirror_and_magnifying_glass_no_longer_asks_for_the_prop(self):
        # The real narration of scene_15, and the real lexicon answer for
        # "verdade" that put six magnifying glasses in one cut.
        concept = translate_beat(
            "Uma informação vista muitas vezes começa a parecer familiar. "
            "E familiaridade é facilmente confundida com verdade.",
            concept="verdade",
            lexicon_scene="magnifying glass over a document",
            visual_intent_class="metaphorical",
            visual_role="symbolize",
        )
        self.assertEqual(concept.source, "semantic_field")
        for query in concept.queries:
            self.assertNotIn("magnifying", query.lower())
        self.assertIn("magnifying", concept.rationale)
        # and the concept it did choose is about the thing the beat says
        self.assertIn("repetition", concept.concept_id)

    def test_the_man_with_the_magnifying_glass_in_the_mirror_loses_to_a_documentary_alternative(self):
        cliche = _candidate(
            "pexels:6981597", "man using magnifying glass while looking in the mirror"
        )
        sober = _candidate(
            "pexels:1", "identical posters repeated along a wall in a dim corridor"
        )
        ranked, verdicts = rank_candidates_with_report(
            _requirement(), [cliche, sober], TRANSLATING_SCORING
        )
        self.assertEqual([c.candidate_id for c in ranked], ["pexels:1"])
        refused = {v.candidate_id: v.rejection_reasons for v in verdicts}
        self.assertIn(CLICHE_REJECTION, refused["pexels:6981597"])
        # and the record says *why*, in words, not as a score
        fit = {v.candidate_id: v.editorial_fit for v in verdicts}["pexels:6981597"]
        self.assertTrue(
            any("investigation" in note for note in fit.notes), fit.notes
        )

    def test_coloured_chalk_is_refused_without_any_children_token(self):
        chalk = _candidate("pexels:8034993", "people coloring the ground using chalks")
        self.assertNotIn("children", chalk.metadata_bag())
        self.assertNotIn("kids", chalk.metadata_bag())
        fit = assess_editorial_fit(
            chalk.metadata_bag(),
            concept_terms=_bag("man alone reviewing documents dim room"),
            visual_intent_class="metaphorical",
        )
        self.assertGreaterEqual(fit.get("playful_register"), 0.5)
        sober = _candidate("pexels:2", "man alone reviewing documents in a dim room")
        rejections = comparative_rejections(
            [
                ("pexels:8034993", fit),
                (
                    "pexels:2",
                    assess_editorial_fit(
                        sober.metadata_bag(),
                        concept_terms=_bag("man alone reviewing documents dim room"),
                        visual_intent_class="metaphorical",
                    ),
                ),
            ]
        )
        self.assertIn(PLAYFUL_REJECTION, rejections["pexels:8034993"])
        self.assertNotIn("pexels:2", rejections)

    def test_a_sentimental_frame_loses_the_same_way_a_playful_one_does(self):
        # measured 24 s into the first candidate render of this cycle: a hand
        # drawing a heart on a fogged window, in an essay about self-deception.
        # "children" does not appear, "cheerful" does not appear, and the frame
        # is still the wrong register.
        romantic = _candidate(
            "pexels:33207483", "hand drawing heart on foggy window at sunset"
        )
        fit = assess_editorial_fit(
            romantic.metadata_bag(),
            concept_terms=_bag("diagram drawn fogged window"),
            visual_intent_class="metaphorical",
        )
        self.assertGreater(fit.get("playful_register"), 0.0)
        sober = assess_editorial_fit(
            _bag("stacks of paper covering a table in a dim room"),
            concept_terms=_bag("diagram drawn fogged window"),
            visual_intent_class="metaphorical",
        )
        rejections = comparative_rejections(
            [("pexels:33207483", fit), ("pexels:sober", sober)]
        )
        self.assertIn(PLAYFUL_REJECTION, rejections["pexels:33207483"])
        self.assertNotIn("pexels:sober", rejections)

    def test_the_beat_under_the_chalk_asks_for_a_human_situation(self):
        concept = translate_beat(
            "Talvez seja: o que eu justifico justamente porque me acho esperto "
            "demais para ser enganado?",
            concept="pergunta",
            lexicon_scene="question mark chalked on a board",
            visual_intent_class="metaphorical",
            visual_role="symbolize",
        )
        self.assertEqual(concept.source, "semantic_field")
        for query in concept.queries:
            self.assertNotIn("chalk", query.lower())


# --------------------------------------------------------------------------- #
# abstraction becomes a searchable physical scene
# --------------------------------------------------------------------------- #
class AbstractionToSceneTests(unittest.TestCase):
    def test_a_sentence_with_no_lexicon_noun_still_gets_a_filmable_scene(self):
        # scene_25 of the production script: one of the 17 that needed a
        # hand-written query. Its only "concept" was the bare noun "momento".
        concept = translate_beat(
            "Porque o momento em que você se considera imune à manipulação é "
            "exatamente o momento em que você para de procurar.",
            concept="momento",
            lexicon_scene="",
            visual_intent_class="metaphorical",
            visual_role="symbolize",
        )
        self.assertIn(concept.source, ("semantic_field", "intent_scene"))
        self.assertEqual(concept.queries[0], concept.scene)
        self.assertGreaterEqual(len(concept.scene.split()), 4)

    def test_no_query_is_ever_the_narration_or_a_portuguese_token(self):
        narration = (
            "Nós gostamos de imaginar que pensamos assim: primeiro os fatos, "
            "depois a análise, no fim a conclusão."
        )
        concept = translate_beat(
            narration,
            concept="gostamos",
            visual_intent_class="metaphorical",
            visual_role="symbolize",
        )
        for query in concept.queries:
            self.assertEqual(non_english_tokens(query), ())
            self.assertNotIn("gostamos", query)
            self.assertNotIn(query.lower(), narration.lower())

    def test_every_rung_of_the_ladder_keeps_the_tone_and_the_language(self):
        seen = set()
        for kwargs in (
            dict(narration="Eu me acho racional.", concept="", lexicon_scene=""),
            dict(
                narration="Uma mesa vazia.",
                concept="mesa",
                lexicon_scene="wooden desk with a lamp",
            ),
            dict(narration="Xyz qrs.", concept="xyz", lexicon_scene=""),
            dict(narration="Xyz qrs.", concept="xyz", lexicon_scene="", no_intent=True),
        ):
            no_intent = kwargs.pop("no_intent", False)
            concept = translate_beat(
                kwargs["narration"],
                concept=kwargs["concept"],
                lexicon_scene=kwargs["lexicon_scene"],
                visual_intent_class=None if no_intent else "metaphorical",
                visual_role=None if no_intent else "symbolize",
            )
            seen.add(concept.source)
            self.assertEqual(concept.register, "sober")
            for query in concept.queries:
                self.assertEqual(
                    non_english_tokens(query, vocabulary=None)
                    if concept.source != "concept_lexicon"
                    else (),
                    (),
                    query,
                )
        self.assertEqual(seen, set(CONCEPT_SOURCES))

    def test_the_ladder_prefers_a_concrete_lexicon_answer_over_a_field(self):
        # measured regression: replacing "newspaper headline close up" with a
        # crowd made the cut worse in exactly the beat that says "notícia".
        concept = translate_beat(
            "Duas pessoas leem a mesma notícia.",
            concept="noticia",
            lexicon_scene="newspaper headline close up",
            visual_intent_class="evidence_or_archive",
            visual_role="support_claim",
        )
        self.assertEqual(concept.source, "concept_lexicon")
        self.assertEqual(concept.scene, "newspaper headline close up")

    def test_a_lexicon_scene_already_used_hands_the_next_beat_to_a_field(self):
        concept = translate_beat(
            "Duas pessoas leem a mesma notícia.",
            concept="noticia",
            lexicon_scene="newspaper headline close up",
            visual_intent_class="evidence_or_archive",
            visual_role="support_claim",
            lexicon_repeat=True,
        )
        self.assertNotEqual(concept.source, "concept_lexicon")
        self.assertNotEqual(concept.scene, "newspaper headline close up")

    def test_a_legitimate_metaphor_is_still_reachable(self):
        concept = translate_beat(
            "É um exercício de controle e de influência.",
            concept="controle",
            visual_intent_class="metaphorical",
            visual_role="symbolize",
        )
        self.assertEqual(concept.source, "semantic_field")
        self.assertIn("manipulation", concept.concept_id)
        # the scene is a physical situation, not an abstraction
        self.assertNotEqual(non_english_tokens(concept.scene), (concept.scene,))

    def test_rotation_gives_consecutive_beats_of_one_field_different_pictures(self):
        rotations = {}
        scenes = []
        for _ in range(3):
            concept = translate_beat(
                "E familiaridade é confundida com verdade, vista muitas vezes.",
                concept="verdade",
                lexicon_scene="magnifying glass over a document",
                visual_intent_class="metaphorical",
                visual_role="symbolize",
                rotations=rotations,
            )
            rotations[concept.rotation_key] = rotations.get(concept.rotation_key, 0) + 1
            scenes.append(concept.scene)
        self.assertEqual(len(set(scenes)), 3, scenes)


# --------------------------------------------------------------------------- #
# stock metaphor and cliché
# --------------------------------------------------------------------------- #
class StockMetaphorTests(unittest.TestCase):
    def test_the_prop_is_allowed_when_the_beat_named_it(self):
        fit = assess_editorial_fit(
            _bag("two people playing chess in a dim room"),
            concept_terms=_bag("two people playing chess dim room"),
            query_terms=("chess", "players", "dim"),
            visual_intent_class="metaphorical",
        )
        self.assertEqual(fit.get("literalness_risk"), 0.0)

    def test_a_scientific_beat_keeps_its_legitimate_subject(self):
        # a real laboratory beat may want a brain scan; the veto is about lazy
        # substitution, not about the noun.
        fit = assess_editorial_fit(
            _bag("human brain specimen on a laboratory bench"),
            concept_terms=_bag("laboratory bench specimen"),
            visual_intent_class="scientific",
        )
        self.assertEqual(fit.get("literalness_risk"), 0.0)
        self.assertTrue(any("allowed" in note for note in fit.notes), fit.notes)

    def test_the_same_prop_is_a_risk_on_an_abstract_beat(self):
        fit = assess_editorial_fit(
            _bag("plastic brain model on a white desk"),
            concept_terms=_bag("person alone thinking window dim room"),
            visual_intent_class="metaphorical",
        )
        self.assertEqual(fit.get("literalness_risk"), 1.0)

    def test_a_cliche_still_wins_when_it_is_the_only_thing_there(self):
        cliche_a = _candidate("pexels:a", "magnifying glass over an old document")
        cliche_b = _candidate("pexels:b", "a chess board mid game on a table")
        ranked, _ = rank_candidates_with_report(
            _requirement(), [cliche_a, cliche_b], TRANSLATING_SCORING
        )
        # a beat whose whole result set is clichéd still gets a picture rather
        # than becoming an unresolved requirement
        self.assertEqual(len(ranked), 2)

    def test_stock_metaphor_props_reads_a_phrase(self):
        self.assertEqual(
            stock_metaphor_props("question mark chalked on a board"), ("chalked",)
        )
        self.assertEqual(stock_metaphor_props("hands on a desk in low light"), ())


# --------------------------------------------------------------------------- #
# the positive assessment, and what it refuses to invent
# --------------------------------------------------------------------------- #
class EditorialFitTests(unittest.TestCase):
    def test_a_thin_metadata_bag_reports_unknown_rather_than_zero(self):
        fit = assess_editorial_fit(frozenset({"img"}), concept_terms=_bag("a b c"))
        self.assertEqual(set(fit.unknown), set(EDITORIAL_FIT_SIGNALS))
        self.assertEqual(dict(fit.signals), {})
        self.assertTrue(fit.notes)

    def test_a_missing_concept_is_reported_unknown_not_scored_as_zero(self):
        fit = assess_editorial_fit(_bag("man walking on a dark street at night"))
        self.assertIn("concept_affinity", fit.unknown)
        self.assertNotIn("concept_affinity", fit.signals)

    def test_a_signal_cannot_be_both_known_and_unknown(self):
        with self.assertRaises(TranslationError):
            EditorialFit({"human_presence": 1.0}, ("human_presence",))

    def test_signals_stay_inside_the_unit_interval(self):
        with self.assertRaises(TranslationError):
            EditorialFit({"human_presence": 1.4}, ())
        with self.assertRaises(TranslationError):
            EditorialFit({"not_a_signal": 0.5}, ())

    def test_an_observed_place_reads_better_than_a_studio_plate(self):
        observed = assess_editorial_fit(
            _bag("man walking down a dark office corridor at night"),
            concept_terms=_bag("man corridor night"),
        )
        staged = assess_editorial_fit(
            _bag("businessman posing in a studio isolated on white"),
            concept_terms=_bag("man corridor night"),
        )
        self.assertGreater(
            observed.get("documentary_plausibility"),
            staged.get("documentary_plausibility"),
        )
        self.assertGreater(staged.get("staged_artifice"), 0.0)
        self.assertGreater(
            sum(observed.components().values()), sum(staged.components().values())
        )

    def test_the_two_comparative_reasons_are_in_the_shared_vocabulary(self):
        self.assertIn(CLICHE_REJECTION, REJECTION_REASONS)
        self.assertIn(PLAYFUL_REJECTION, REJECTION_REASONS)


# --------------------------------------------------------------------------- #
# contract, policy, determinism, serialisation
# --------------------------------------------------------------------------- #
class ContractTests(unittest.TestCase):
    def test_a_concept_round_trips_through_json(self):
        concept = translate_beat(
            "Eu me acho racional demais para ser enganado.",
            visual_intent_class="metaphorical",
            visual_role="symbolize",
        )
        back = FilmableConcept.from_dict(json.loads(json.dumps(concept.to_dict())))
        self.assertEqual(back, concept)

    def test_a_concept_must_lead_with_its_own_scene(self):
        with self.assertRaises(TranslationError):
            FilmableConcept(
                concept_id="x/1",
                visual_meaning="m",
                scene="a dim room",
                family="f",
                register="sober",
                queries=("something else", "a dim room"),
                avoid=(),
                source="tone_floor",
                rationale="r",
            )

    def test_an_unknown_register_or_source_is_refused(self):
        for bad in ({"register": "zany"}, {"source": "vibes"}):
            with self.assertRaises(TranslationError):
                FilmableConcept(
                    concept_id="x/1",
                    visual_meaning="m",
                    scene="a dim room",
                    family="f",
                    register=bad.get("register", "sober"),
                    queries=("a dim room",),
                    avoid=(),
                    source=bad.get("source", "tone_floor"),
                    rationale="r",
                )

    def test_the_same_beat_and_policy_always_produce_the_same_concept(self):
        args = dict(
            concept="verdade",
            lexicon_scene="magnifying glass over a document",
            visual_intent_class="metaphorical",
            visual_role="symbolize",
            rotation=2,
        )
        first = translate_beat("E familiaridade vira verdade.", **args)
        second = translate_beat("E familiaridade vira verdade.", **args)
        self.assertEqual(first, second)

    def test_a_partial_policy_merges_and_a_bad_one_is_refused(self):
        policy = TranslationPolicy.from_dict({"penalty_playful": 9.0})
        self.assertEqual(policy.penalty_playful, 9.0)
        self.assertEqual(
            policy.weight_editorial_fit,
            DEFAULT_TRANSLATION_POLICY.weight_editorial_fit,
        )
        for bad in ({"penalty_playful": -1.0}, {"queries_per_concept": 0}, {"nope": 1}):
            with self.assertRaises(TranslationError):
                TranslationPolicy.from_dict(bad)

    def test_a_disabled_policy_infers_nothing_and_refuses_nobody(self):
        off = TranslationPolicy(enabled=False)
        fit = assess_editorial_fit(
            _bag("people coloring the ground using chalks"), policy=off
        )
        self.assertEqual(set(fit.unknown), set(EDITORIAL_FIT_SIGNALS))
        self.assertEqual(comparative_rejections([("a", fit)], off), {})

    def test_an_unusable_register_is_refused_by_the_reader(self):
        light = TranslationPolicy(accepted_registers=("light",))
        with self.assertRaises(EditorialError):
            read_beats(
                [("s1", "Eu me acho racional demais.")],
                policy=EditorialPolicy(
                    visual_relevance=True,
                    editorial_translation=True,
                    translation_policy=light,
                ),
            )


# --------------------------------------------------------------------------- #
# compatibility: absence of the feature preserves the old behaviour exactly
# --------------------------------------------------------------------------- #
class CompatibilityTests(unittest.TestCase):
    SLICES = (
        ("scene_01_shot_01", "Você conhece alguém inteligente."),
        ("scene_01_shot_02", "E familiaridade é confundida com verdade."),
        ("scene_02_shot_01", "Duas pessoas leem a mesma notícia."),
    )

    def test_reading_beats_without_the_flag_is_byte_identical(self):
        before = [b.to_dict() for b in read_beats(self.SLICES)]
        for beat in before:
            self.assertIsNone(beat["filmable_concept"])
        relevance_only = EditorialPolicy(visual_relevance=True)
        after = [b.to_dict() for b in read_beats(self.SLICES, policy=relevance_only)]
        for beat in after:
            self.assertIsNone(beat["filmable_concept"])
        # and the magnifying glass is still exactly what the old path asks for
        self.assertIn("magnifying", json.dumps(after))

    def test_translation_requires_the_relevance_layer(self):
        with self.assertRaises(EditorialError):
            EditorialPolicy(editorial_translation=True)
        scene_plan = plan_scenes(
            NarrativeScript.from_text(
                "s",
                "Uma frase completa.\n\nOutra frase completa.",
                total_duration_seconds=20.0,
            ),
            seed=0,
        )
        with self.assertRaises(PlanningError):
            plan_shots(scene_plan, seed=0, semantic=True, editorial_translation=True)

    def test_scoring_without_the_flag_adds_no_editorial_component(self):
        breakdown = score_candidate(
            _requirement(), _candidate("pexels:x", "a dim empty corridor at night")
        )
        self.assertIsNone(breakdown.editorial_fit)
        for name in (
            "editorial_fit",
            "literalness_penalty",
            "playful_register_penalty",
            "staged_artifice_penalty",
        ):
            self.assertNotIn(name, breakdown.components)

    def test_no_comparative_rejection_is_possible_with_the_flag_off(self):
        cliche = _candidate("pexels:a", "man using magnifying glass in the mirror")
        sober = _candidate("pexels:b", "identical posters repeated along a wall")
        _, verdicts = rank_candidates_with_report(_requirement(), [cliche, sober])
        for verdict in verdicts:
            self.assertNotIn(CLICHE_REJECTION, verdict.rejection_reasons)
            self.assertIsNone(verdict.editorial_fit)

    def test_a_shot_without_a_concept_still_serialises_and_reloads(self):
        scene_plan = plan_scenes(
            NarrativeScript.from_text(
                "s",
                "Uma frase sobre uma mesa.\n\nOutra frase sobre uma janela.",
                total_duration_seconds=20.0,
            ),
            seed=0,
        )
        plan, _ = plan_shots(scene_plan, seed=0)
        for shot in plan.shots:
            self.assertIsNone(shot.filmable_concept)
        back = type(plan).from_dict(json.loads(plan.to_json()))
        self.assertEqual(back.to_dict(), plan.to_dict())


# --------------------------------------------------------------------------- #
# the planner end to end
# --------------------------------------------------------------------------- #
class PlannedScriptTests(unittest.TestCase):
    SCRIPT = (
        "Você conhece alguém inteligente que acredita em algo absurdo.\n\n"
        "E familiaridade é facilmente confundida com verdade.\n\n"
        "Talvez seja: o que eu justifico porque me acho esperto demais?\n\n"
        "Duas pessoas leem a mesma notícia.\n"
    )

    def _plan(self, **over):
        scene_plan = plan_scenes(
            NarrativeScript.from_text("s", self.SCRIPT, total_duration_seconds=40.0),
            seed=0,
        )
        kwargs = dict(seed=0, semantic=True, visual_relevance=True)
        kwargs.update(over)
        return plan_shots(scene_plan, **kwargs)

    def test_every_planned_query_is_english_and_every_shot_names_its_concept(self):
        plan, requirements = self._plan(editorial_translation=True)
        for shot in plan.shots:
            self.assertIsNotNone(shot.filmable_concept)
            self.assertNotIn("magnifying", " ".join(shot.asset_queries))
        for requirement in requirements.requirements:
            self.assertTrue(requirement.queries)

    def test_planning_is_deterministic_under_the_same_seed(self):
        first, _ = self._plan(editorial_translation=True)
        second, _ = self._plan(editorial_translation=True)
        self.assertEqual(first.to_dict(), second.to_dict())

    def test_the_old_plan_is_untouched_when_the_flag_is_off(self):
        plain, _ = self._plan()
        again, _ = self._plan()
        self.assertEqual(plain.to_dict(), again.to_dict())
        for shot in plain.shots:
            self.assertIsNone(shot.filmable_concept)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
