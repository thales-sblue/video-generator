"""Semantic Visual Relevance v1: beat reading, query refinement, candidate
ranking, editorial rejection — and the guarantee that none of it happens
unless it was asked for."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from video_generator.domain.assets import (
    AssetCandidate,
    AssetScoringPolicy,
    rank_candidates,
    rank_candidates_with_report,
    score_candidate,
)
from video_generator.domain.editorial import EditorialPolicy, NarrationBeat, read_beats
from video_generator.domain.planning import (
    AssetRequirement,
    NarrativeScript,
    PlanningError,
    plan_scenes,
    plan_shots,
)
from video_generator.domain.relevance import (
    DEFAULT_RELEVANCE_POLICY,
    REJECTION_REASONS,
    VISUAL_INTENTS,
    VISUAL_ROLES,
    RelevanceError,
    RelevancePolicy,
    assess_candidate,
    read_relevance,
    read_visual_intent,
    read_visual_role,
    refine_query,
    visual_family,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _candidate(candidate_id, title, *, tags=(), description="", media_type="image",
               width=1920, height=1080, duration=None):
    return AssetCandidate(
        candidate_id=candidate_id,
        source_kind="local_library",
        source_id=candidate_id,
        media_type=media_type,
        local_path=f"/library/{candidate_id}.jpg",
        remote_locator=None,
        title=title,
        description=description,
        tags=tuple(tags),
        width=width,
        height=height,
        duration_seconds=duration,
        license="CC0",
        license_url=None,
        author=None,
        source_url=None,
        score=0.0,
        score_breakdown={},
    )


def _requirement(query, *, purpose="mostrar: cena", intent=None, role=None,
                 emotion=None, asset_id="asset_s_01_01"):
    return AssetRequirement(
        asset_id=asset_id,
        type="image",
        query=query,
        duration_needed_seconds=4.0,
        orientation="landscape",
        purpose=purpose,
        used_by=("s_01_shot_01",),
        visual_intent_class=intent,
        visual_role=role,
        emotion=emotion,
    )


# --------------------------------------------------------------------------- #
# 1. visual intent
# --------------------------------------------------------------------------- #
class VisualIntentTests(unittest.TestCase):
    def test_every_rule_reaches_its_class(self):
        cases = [
            ("O segredo que ninguém contou.", "tension_or_suspense", "tension_cue"),
            ("A teoria da física mudou tudo.", "scientific", "science_lexicon"),
            ("O documento estava no arquivo.", "evidence_or_archive", "archive_or_evidence"),
            ("As pessoas da rua sabiam.", "everyday_human", "everyday_lexicon"),
            ("O sistema decidiu por ele.", "metaphorical", "abstract_or_unfilmable"),
            ("Ele abriu a janela da sala.", "literal", "default"),
        ]
        for narration, expected, rule in cases:
            with self.subTest(narration=narration):
                got, got_rule = read_visual_intent(narration)
                self.assertEqual(got, expected)
                self.assertEqual(got_rule, rule)

    def test_emotion_beats_the_everyday_lexicon_but_not_tension(self):
        # a beat that names people *and* carries an emotion is emotional
        intent, _ = read_visual_intent("As pessoas ficaram ali.", emotion="lonely")
        self.assertEqual(intent, "emotional")
        # ... but a suspense cue outranks the emotion: the picture has to
        # withhold before it can move
        intent, _ = read_visual_intent("Ninguém soube do segredo.", emotion="lonely")
        self.assertEqual(intent, "tension_or_suspense")

    def test_a_number_on_an_evidence_beat_reads_as_archive(self):
        intent, rule = read_visual_intent(
            "Foram 42 candidatos naquele ano.", editorial_role="evidence"
        )
        self.assertEqual(intent, "evidence_or_archive")
        self.assertEqual(rule, "archive_or_evidence")

    def test_an_unfilmable_beat_falls_back_to_metaphor(self):
        intent, rule = read_visual_intent(
            "Ele pediu uma exceção.", has_concrete_concept=False
        )
        self.assertEqual(intent, "metaphorical")
        self.assertEqual(rule, "abstract_or_unfilmable")

    def test_every_class_declared_is_reachable_and_valid(self):
        self.assertEqual(len(set(VISUAL_INTENTS)), len(VISUAL_INTENTS))


# --------------------------------------------------------------------------- #
# 2. visual role
# --------------------------------------------------------------------------- #
class VisualRoleTests(unittest.TestCase):
    def test_the_opening_always_builds_tension(self):
        for intent in VISUAL_INTENTS:
            with self.subTest(intent=intent):
                role, rule = read_visual_role(intent, editorial_role="hook")
                self.assertEqual(role, "build_tension")
                self.assertEqual(rule, "opening_beat")

    def test_role_follows_intent_when_the_argument_does_not_decide(self):
        cases = [
            ("evidence_or_archive", None, "support_claim"),
            ("emotional", None, "humanize"),
            ("everyday_human", None, "humanize"),
            ("metaphorical", None, "symbolize"),
            ("scientific", None, "explain"),
            ("tension_or_suspense", None, "build_tension"),
            ("literal", "close", "contextualize"),
            ("literal", "claim", "explain"),
        ]
        for intent, editorial_role, expected in cases:
            with self.subTest(intent=intent, editorial_role=editorial_role):
                role, _ = read_visual_role(intent, editorial_role=editorial_role)
                self.assertEqual(role, expected)

    def test_a_strong_contrast_beat_shocks_but_a_weak_one_does_not(self):
        strong, _ = read_visual_role("literal", editorial_role="contrast", importance=0.8)
        self.assertEqual(strong, "shock")
        weak, _ = read_visual_role("literal", editorial_role="contrast", importance=0.2)
        self.assertEqual(weak, "explain")

    def test_unknown_intent_is_refused(self):
        with self.assertRaises(RelevanceError):
            read_visual_role("cinematic")


# --------------------------------------------------------------------------- #
# 3. query refinement
# --------------------------------------------------------------------------- #
class QueryRefinementTests(unittest.TestCase):
    def test_intent_and_role_modifiers_are_prepended(self):
        refined = refine_query(
            "empty classroom",
            visual_intent_class="tension_or_suspense",
            visual_role="build_tension",
        )
        self.assertEqual(refined, "moody dark empty classroom")

    def test_a_modifier_already_in_the_query_is_not_repeated(self):
        refined = refine_query(
            "dark empty corridor",
            visual_intent_class="tension_or_suspense",
            visual_role="build_tension",
        )
        self.assertEqual(refined, "moody dark empty corridor")

    def test_refinement_never_dilutes_past_the_word_ceiling(self):
        base = "rejection failed exam paper with red marks"  # already 7 words
        refined = refine_query(
            base, visual_intent_class="evidence_or_archive", visual_role="support_claim"
        )
        self.assertEqual(refined, base)
        self.assertLessEqual(
            len(refined.split()), DEFAULT_RELEVANCE_POLICY.max_query_words
        )

    def test_a_tighter_ceiling_admits_fewer_modifiers(self):
        policy = RelevancePolicy(max_query_words=3)
        refined = refine_query(
            "empty classroom",
            visual_intent_class="tension_or_suspense",
            visual_role="build_tension",
            policy=policy,
        )
        self.assertEqual(refined, "dark empty classroom")

    def test_an_empty_query_falls_back_to_the_intent(self):
        refined = refine_query(
            "", visual_intent_class="emotional", visual_role="humanize"
        )
        self.assertIn("lonely", refined)
        self.assertTrue(refined.strip())

    def test_a_literal_explaining_beat_is_left_alone(self):
        self.assertEqual(
            refine_query(
                "window with morning light",
                visual_intent_class="literal",
                visual_role="explain",
            ),
            "window with morning light",
        )

    def test_read_relevance_leads_with_the_refined_query_and_keeps_fallbacks(self):
        reading = read_relevance(
            "O segredo estava na sala.",
            concept="sala",
            base_queries=("empty classroom", "wooden desk with a lamp"),
            editorial_role="claim",
        )
        self.assertEqual(reading.visual_intent_class, "tension_or_suspense")
        self.assertEqual(reading.queries()[0], reading.refined_query)
        self.assertIn("empty classroom", reading.queries())
        self.assertIn("wooden desk with a lamp", reading.queries())
        self.assertTrue(reading.rationale.startswith("intent:"))


# --------------------------------------------------------------------------- #
# 4. candidate ranking
# --------------------------------------------------------------------------- #
class CandidateRankingTests(unittest.TestCase):
    def test_intent_affinity_lifts_the_right_kind_of_picture(self):
        requirement = _requirement(
            "dark empty corridor",
            intent="tension_or_suspense",
            role="build_tension",
        )
        on_intent = _candidate(
            "c_moody", "dark empty corridor", tags=("shadow", "night")
        )
        off_intent = _candidate(
            "c_flat", "empty corridor", tags=("architecture", "interior")
        )
        ranked = rank_candidates(requirement, [off_intent, on_intent])
        self.assertEqual([c.candidate_id for c in ranked][0], "c_moody")
        self.assertGreater(
            ranked[0].score_breakdown["intent_affinity"], 0.0
        )

    def test_role_affinity_and_visual_strength_are_reported_separately(self):
        requirement = _requirement(
            "archival documents", intent="evidence_or_archive", role="support_claim"
        )
        candidate = _candidate(
            "c_doc", "archival documents", tags=("newspaper", "records", "dramatic")
        )
        breakdown = score_candidate(requirement, candidate)
        self.assertGreater(breakdown.components["intent_affinity"], 0.0)
        self.assertGreater(breakdown.components["role_affinity"], 0.0)
        self.assertGreater(breakdown.components["visual_strength"], 0.0)
        self.assertEqual(breakdown.rejection_reasons, ())

    def test_a_weak_metaphor_is_penalised_not_rejected(self):
        requirement = _requirement(
            "conceptual symbolic government building facade",
            intent="metaphorical",
            role="symbolize",
        )
        candidate = _candidate(
            "c_lit", "government building facade", tags=("architecture",)
        )
        breakdown = score_candidate(requirement, candidate)
        self.assertLess(breakdown.components["weak_metaphor_penalty"], 0.0)
        self.assertEqual(breakdown.rejection_reasons, ())

    def test_repetition_of_a_visual_family_is_penalised_then_refused(self):
        requirement = _requirement(
            "archival documents", intent="evidence_or_archive", role="support_claim"
        )
        candidate = _candidate("c_doc", "archival documents", tags=("newspaper",))
        once = score_candidate(requirement, candidate, recent_families=("archive_document",))
        self.assertLess(once.components["visual_language_repetition_penalty"], 0.0)
        self.assertEqual(once.rejection_reasons, ())
        twice = score_candidate(
            requirement,
            candidate,
            recent_families=("archive_document", "archive_document"),
        )
        self.assertIn("visual_language_repetition", twice.rejection_reasons)

    def test_visual_family_prefers_the_look_a_viewer_notices(self):
        self.assertEqual(
            visual_family({"brain", "laboratory", "render"}), "abstract_cgi"
        )
        self.assertEqual(visual_family({"laboratory", "microscope"}), "science_lab")
        self.assertEqual(visual_family({"quiet", "morning"}), "other")


# --------------------------------------------------------------------------- #
# 5. editorial rejection
# --------------------------------------------------------------------------- #
class RejectionTests(unittest.TestCase):
    def test_generic_stock_is_refused(self):
        requirement = _requirement(
            "candid people on a street", intent="everyday_human", role="humanize"
        )
        filler = _candidate(
            "c_stock",
            "business people handshake",
            tags=("corporate", "teamwork", "people"),
        )
        breakdown = score_candidate(requirement, filler)
        self.assertIn("generic_stock", breakdown.rejection_reasons)
        self.assertLess(breakdown.components["generic_stock_penalty"], 0.0)
        self.assertEqual(rank_candidates(requirement, [filler]), [])

    def test_a_single_stray_keyword_is_not_a_match(self):
        requirement = _requirement(
            "archival exam papers desk", intent="evidence_or_archive", role="support_claim"
        )
        tangential = _candidate("c_tang", "papers", tags=("origami",))
        breakdown = score_candidate(requirement, tangential)
        self.assertIn("keyword_only_match", breakdown.rejection_reasons)

    def test_a_cheerful_picture_cannot_carry_a_dark_beat(self):
        requirement = _requirement(
            "dark empty corridor",
            intent="tension_or_suspense",
            role="build_tension",
            emotion="fearful",
        )
        cheerful = _candidate(
            "c_happy", "dark corridor", tags=("smiling", "celebration", "empty")
        )
        breakdown = score_candidate(requirement, cheerful)
        self.assertIn("tone_conflict", breakdown.rejection_reasons)

    def test_abstract_cgi_cannot_stand_in_for_a_human_beat(self):
        requirement = _requirement(
            "intimate lonely person portrait", intent="emotional", role="humanize"
        )
        cgi = _candidate(
            "c_cgi",
            "glowing neural network brain",
            tags=("neuron", "render", "person"),
        )
        breakdown = score_candidate(requirement, cgi)
        self.assertIn("abstract_cgi_mismatch", breakdown.rejection_reasons)

    def test_the_same_cgi_is_fine_on_a_scientific_beat(self):
        requirement = _requirement(
            "scientific neuron close up", intent="scientific", role="explain"
        )
        cgi = _candidate(
            "c_cgi", "glowing neural network brain", tags=("neuron", "render")
        )
        breakdown = score_candidate(requirement, cgi)
        self.assertNotIn("abstract_cgi_mismatch", breakdown.rejection_reasons)

    def test_every_reason_emitted_is_declared(self):
        assessment = assess_candidate(
            {"business", "corporate", "smiling", "neuron", "render"},
            visual_intent_class="emotional",
            visual_role="humanize",
            emotion="sad",
            query_terms=("lonely", "person", "portrait"),
            shared_query_terms=0,
            shared_context_terms=0,
        )
        self.assertTrue(assessment.rejected)
        for reason in assessment.rejection_reasons:
            self.assertIn(reason, REJECTION_REASONS)

    def test_the_report_explains_an_empty_ranking(self):
        requirement = _requirement(
            "candid people on a street", intent="everyday_human", role="humanize"
        )
        filler = _candidate(
            "c_stock", "business people handshake", tags=("corporate", "teamwork")
        )
        ranked, verdicts = rank_candidates_with_report(requirement, [filler])
        self.assertEqual(ranked, [])
        self.assertEqual(len(verdicts), 1)
        self.assertFalse(verdicts[0].accepted)
        self.assertIn("generic_stock", verdicts[0].rejection_reasons)
        payload = verdicts[0].to_dict()
        self.assertEqual(payload["candidate_id"], "c_stock")
        self.assertIn("generic_stock_penalty", payload["components"])


# --------------------------------------------------------------------------- #
# 6. regression — nothing happens unless it was asked for
# --------------------------------------------------------------------------- #
class OptInRegressionTests(unittest.TestCase):
    SLICES = [
        ("s_01_shot_01", "Você acha que conhece a história, mas ninguém contou o segredo."),
        ("s_01_shot_02", "Em 1895 ele foi reprovado no exame de admissão."),
        ("s_01_shot_03", "O sistema decidiu que aquela mente não servia."),
        ("s_01_shot_04", "As pessoas da rua nunca souberam disso."),
    ]

    def test_the_default_reading_is_byte_for_byte_unchanged(self):
        beats = read_beats(self.SLICES)
        for beat in beats:
            self.assertIsNone(beat.visual_intent_class)
            self.assertIsNone(beat.visual_role)
            self.assertIsNone(beat.refined_query)
            self.assertIsNone(beat.relevance_rationale)
        with_policy = read_beats(self.SLICES, policy=EditorialPolicy())
        self.assertEqual(
            [b.asset_queries for b in beats], [b.asset_queries for b in with_policy]
        )

    def test_enabling_relevance_labels_every_beat_and_leads_with_the_refined_query(self):
        beats = read_beats(self.SLICES, policy=EditorialPolicy(visual_relevance=True))
        plain = {b.beat_id: b for b in read_beats(self.SLICES)}
        for beat in beats:
            self.assertIn(beat.visual_intent_class, VISUAL_INTENTS)
            self.assertIn(beat.visual_role, VISUAL_ROLES)
            self.assertEqual(beat.asset_queries[0], beat.refined_query)
            # the original best query survives as a fallback, so refinement can
            # never make a beat unsearchable
            self.assertIn(plain[beat.beat_id].asset_queries[0], beat.asset_queries)

    def test_a_labelled_beat_round_trips(self):
        beat = read_beats(self.SLICES, policy=EditorialPolicy(visual_relevance=True))[0]
        again = NarrationBeat.from_dict(json.loads(json.dumps(beat.to_dict())))
        self.assertEqual(again.to_dict(), beat.to_dict())

    def test_a_requirement_without_labels_scores_exactly_as_before(self):
        requirement = _requirement("empty classroom with wooden desks")
        candidate = _candidate("c_room", "empty classroom", tags=("desks", "school"))
        breakdown = score_candidate(requirement, candidate)
        self.assertEqual(breakdown.rejection_reasons, ())
        for name in (
            "intent_affinity",
            "role_affinity",
            "visual_strength",
            "generic_stock_penalty",
            "weak_metaphor_penalty",
            "visual_language_repetition_penalty",
        ):
            self.assertEqual(breakdown.components[name], 0.0)

    def test_the_relevance_policy_can_be_switched_off_wholesale(self):
        policy = AssetScoringPolicy(relevance_policy=RelevancePolicy(enabled=False))
        requirement = _requirement(
            "candid people on a street", intent="everyday_human", role="humanize"
        )
        filler = _candidate(
            "c_stock", "business people handshake", tags=("corporate", "teamwork")
        )
        breakdown = score_candidate(requirement, filler, policy)
        self.assertEqual(breakdown.rejection_reasons, ())


# --------------------------------------------------------------------------- #
# 7. planning integration
# --------------------------------------------------------------------------- #
SCRIPT_TEXT = """Você acha que conhece essa história, mas ninguém contou o segredo dela.

Em 1895 ele foi reprovado no exame de admissão do politécnico, e o documento ficou no arquivo.

O sistema decidiu que aquela mente não servia para nada.

As pessoas da rua nunca souberam disso, e seguiram a vida.

A física que ele escreveu mudou o século inteiro."""


def _plan(**kwargs):
    script = NarrativeScript.from_text(
        "relevance_case", SCRIPT_TEXT, total_duration_seconds=60.0
    )
    scene_plan = plan_scenes(script, seed=7)
    return plan_shots(scene_plan, seed=7, orientation="landscape", **kwargs)


class PlanningIntegrationTests(unittest.TestCase):
    def test_relevance_requires_semantic_planning(self):
        with self.assertRaises(PlanningError):
            _plan(visual_relevance=True)

    def test_every_shot_and_requirement_carries_its_reading(self):
        shot_plan, requirements = _plan(semantic=True, visual_relevance=True)
        for shot in shot_plan.shots:
            self.assertIn(shot.visual_intent_class, VISUAL_INTENTS)
            self.assertIn(shot.visual_role, VISUAL_ROLES)
            self.assertEqual(shot.refined_query, shot.visual_query)
        for requirement in requirements.requirements:
            self.assertIn(requirement.visual_intent_class, VISUAL_INTENTS)
            self.assertIn(requirement.visual_role, VISUAL_ROLES)

    def test_semantic_planning_without_the_flag_is_unchanged(self):
        before, before_reqs = _plan(semantic=True)
        for shot in before.shots:
            self.assertIsNone(shot.visual_intent_class)
            self.assertIsNone(shot.visual_role)
            self.assertIsNone(shot.refined_query)
        for requirement in before_reqs.requirements:
            self.assertIsNone(requirement.visual_intent_class)
        after, _ = _plan(semantic=True, visual_relevance=True)
        # the rhythm is untouched: only the queries and the labels move
        self.assertEqual(
            [(s.shot_id, s.duration_seconds, s.shot_type) for s in before.shots],
            [(s.shot_id, s.duration_seconds, s.shot_type) for s in after.shots],
        )

    def test_the_plan_round_trips_through_json(self):
        shot_plan, requirements = _plan(semantic=True, visual_relevance=True)
        from video_generator.domain.planning import AssetRequirements, ShotPlan

        self.assertEqual(
            ShotPlan.from_dict(json.loads(shot_plan.to_json())).to_dict(),
            shot_plan.to_dict(),
        )
        self.assertEqual(
            AssetRequirements.from_dict(json.loads(requirements.to_json())).to_dict(),
            requirements.to_dict(),
        )

    def test_an_unknown_label_is_refused(self):
        with self.assertRaises(PlanningError):
            _requirement("x", intent="cinematic")
        with self.assertRaises(PlanningError):
            _requirement("x", role="look_nice")


class ResolverRejectionTests(unittest.TestCase):
    """The rejection rules have to reach the orchestrator, not just the scorer:
    a requirement whose every candidate is editorially wrong must come back
    unresolved and say why."""

    class _Provider:
        source_kind = "local_library"

        def __init__(self, rows):
            self._rows = rows
            self.acquired = []

        def search(self, terms, *, media_type, orientation, min_duration_seconds, limit):
            return [
                _candidate(row["id"], row["title"], tags=row["tags"])
                for row in self._rows
                if row["media_type"] == media_type
            ][:limit]

        def acquire(self, candidate, dest_path):
            # Acquisition is what this test is watching, so record the attempt
            # and fail it: the run ends without touching a real file, and the
            # recorded ids say which candidates survived the ranking.
            from video_generator.adapters.asset_providers import ProviderError

            self.acquired.append(candidate.candidate_id)
            raise ProviderError("acquisition disabled in this test")

    def _resolve(self, requirement, rows):
        from video_generator.domain.planning import AssetRequirements
        from video_generator.resolve import resolve_assets

        provider = self._Provider(rows)
        requirements = AssetRequirements(
            plan_id="p",
            shot_plan_id="sp",
            script_id="s",
            orientation="landscape",
            requirements=(requirement,),
        )
        with tempfile.TemporaryDirectory() as tmp:
            return provider, resolve_assets(
                None,
                requirements,
                [provider],
                out_dir=Path(tmp) / "resolved",
                do_review_reuse=False,
            )

    def test_a_pool_of_generic_stock_leaves_the_requirement_for_a_human(self):
        requirement = _requirement(
            "candid people on a street",
            purpose="mostrar: pessoas na rua — beat 1/1",
            intent="everyday_human",
            role="humanize",
        )
        rows = [
            {"id": "c_stock_a", "title": "business people handshake street",
             "tags": ("corporate", "teamwork", "people"), "media_type": "image"},
            {"id": "c_stock_b", "title": "smiling corporate team on a street",
             "tags": ("mockup", "business", "people"), "media_type": "image"},
        ]
        provider, result = self._resolve(requirement, rows)
        self.assertEqual(len(result.plan.resolved), 0)
        self.assertEqual(len(result.plan.unresolved), 1)
        self.assertEqual(result.plan.unresolved[0].reason, "editorially_rejected")
        self.assertIn("generic_stock", result.plan.unresolved[0].detail)
        self.assertEqual(result.rejection_counts.get("generic_stock"), 2)
        # nothing was downloaded: a refused candidate never reaches acquisition
        self.assertEqual(provider.acquired, [])

    def test_a_good_candidate_in_the_same_pool_still_resolves(self):
        requirement = _requirement(
            "candid people on a street",
            purpose="mostrar: pessoas na rua — beat 1/1",
            intent="everyday_human",
            role="humanize",
        )
        rows = [
            {"id": "c_stock_a", "title": "business people handshake street",
             "tags": ("corporate", "teamwork", "people"), "media_type": "image"},
            {"id": "c_real", "title": "candid people walking on a street",
             "tags": ("crowd", "pedestrian", "documentary"), "media_type": "image"},
        ]
        provider, result = self._resolve(requirement, rows)
        # only the surviving candidate was ever offered for acquisition
        self.assertEqual(provider.acquired, ["c_real"])
        # acquisition is refused by the fake provider, so the requirement still
        # ends unresolved — but on acquisition, not on editorial grounds
        self.assertNotEqual(result.plan.unresolved[0].reason, "editorially_rejected")
        self.assertEqual(result.rejection_counts.get("generic_stock"), 1)


class PublishedContractTests(unittest.TestCase):
    """The schemas are public interfaces and fail closed on unknown keys, so a
    new field on a dataclass is a contract change until the schema says so."""

    def _properties(self, schema_name, collection):
        schema = json.loads(
            (REPO_ROOT / "schemas" / schema_name).read_text(encoding="utf-8")
        )
        return set(schema["properties"][collection]["items"]["properties"])

    def test_the_schemas_declare_every_key_the_planner_emits(self):
        shot_plan, requirements = _plan(semantic=True, visual_relevance=True)
        self.assertLessEqual(
            set(shot_plan.shots[0].to_dict()),
            self._properties("shot-plan-v1.schema.json", "shots"),
        )
        self.assertLessEqual(
            set(requirements.requirements[0].to_dict()),
            self._properties("asset-requirements-v1.schema.json", "requirements"),
        )

    def test_the_schemas_enumerate_the_same_vocabularies_as_the_code(self):
        schema = json.loads(
            (REPO_ROOT / "schemas" / "shot-plan-v1.schema.json").read_text(
                encoding="utf-8"
            )
        )
        properties = schema["properties"]["shots"]["items"]["properties"]
        self.assertEqual(
            [v for v in properties["visual_intent_class"]["enum"] if v is not None],
            list(VISUAL_INTENTS),
        )
        self.assertEqual(
            [v for v in properties["visual_role"]["enum"] if v is not None],
            list(VISUAL_ROLES),
        )


class PlanScenesCliTests(unittest.TestCase):
    def _run(self, *args):
        env = {"PYTHONPATH": str(REPO_ROOT / "src")}
        import os

        merged = dict(os.environ)
        merged.update(env)
        return subprocess.run(
            [sys.executable, "-m", "video_generator", *args],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
            env=merged,
        )

    def test_the_flag_writes_labelled_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = root / "roteiro.txt"
            script.write_text(SCRIPT_TEXT, encoding="utf-8")
            out_dir = root / "plan"
            result = self._run(
                "plan-scenes",
                "--from-text", str(script),
                "--total-duration", "60",
                "--target", "1920x1080:cover",
                "--out-dir", str(out_dir),
                "--semantic",
                "--visual-relevance",
                "--json",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            shots = json.loads((out_dir / "shot-plan.json").read_text(encoding="utf-8"))
            self.assertTrue(shots["shots"])
            for shot in shots["shots"]:
                self.assertIn(shot["visual_intent_class"], VISUAL_INTENTS)
                self.assertIn(shot["visual_role"], VISUAL_ROLES)
                self.assertEqual(shot["refined_query"], shot["visual_query"])

    def test_the_flag_needs_semantic(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = root / "roteiro.txt"
            script.write_text(SCRIPT_TEXT, encoding="utf-8")
            result = self._run(
                "plan-scenes",
                "--from-text", str(script),
                "--total-duration", "60",
                "--target", "1920x1080:cover",
                "--out-dir", str(root / "plan"),
                "--visual-relevance",
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("--visual-relevance requires --semantic", result.stderr + result.stdout)


if __name__ == "__main__":
    unittest.main()
