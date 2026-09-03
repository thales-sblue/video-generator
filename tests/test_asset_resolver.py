"""Tests for the Asset Resolver domain layer (pure, stdlib-only, no network).

Covers the resolution contracts (AssetCandidate, AssetProvenance,
ResolvedAsset, UnresolvedRequirement, AssetResolutionPlan), the scoring
policy, deterministic lexical query sanitisation, deterministic candidate
ranking and the semantic reuse-compatibility check.
"""

import json
import unittest
from pathlib import Path

from video_generator.domain import AssetRequirement, AssetRequirements
from video_generator.domain.assets import (
    DEFAULT_SCORING_POLICY,
    AssetCandidate,
    AssetProvenance,
    AssetResolutionError,
    AssetResolutionPlan,
    AssetScoringPolicy,
    ResolvedAsset,
    UnresolvedRequirement,
    rank_candidates,
    review_reuse,
    sanitize_query,
    score_candidate,
)

HEX64 = "a" * 64


def _candidate(**over):
    base = dict(
        candidate_id="local:img_office_01",
        source_kind="local_library",
        source_id="img_office_01",
        media_type="image",
        local_path="/lib/img_office_01.jpg",
        remote_locator=None,
        title="Empty open-plan office at dusk",
        description="A quiet corporate office, rows of desks, low light",
        tags=("office", "desk", "corporate", "workplace"),
        width=1920,
        height=1080,
        duration_seconds=None,
        license="CC0-1.0",
        license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        author="Jane Doe",
        source_url="https://example.org/photos/office-01",
        score=0.0,
        score_breakdown={},
    )
    base.update(over)
    return AssetCandidate(**base)


def _requirement(**over):
    base = dict(
        asset_id="asset_scene_01_01",
        type="image",
        query="wide establishing, escritorio, mesa, corporativo",
        duration_needed_seconds=4.0,
        orientation="landscape",
        purpose="mostrar: escritorio / rotina — beat 1/4",
        used_by=("scene_01_shot_01",),
    )
    base.update(over)
    return AssetRequirement(**base)


def _provenance(**over):
    base = dict(
        asset_id="asset_scene_01_01",
        candidate_id="local:img_office_01",
        source_kind="local_library",
        source_url="https://example.org/photos/office-01",
        author="Jane Doe",
        license="CC0-1.0",
        license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        acquired_at="2026-09-02T12:00:00Z",
        original_filename="office-01.jpg",
        local_path="output/resolved-assets/files/asset_scene_01_01.jpg",
        sha256=HEX64,
    )
    base.update(over)
    return AssetProvenance(**base)


class ScoringPolicyContractTests(unittest.TestCase):
    def test_default_round_trips(self):
        policy = AssetScoringPolicy()
        restored = AssetScoringPolicy.from_dict(json.loads(policy.to_json()))
        self.assertEqual(restored, policy)
        self.assertEqual(policy, DEFAULT_SCORING_POLICY)
        self.assertTrue(policy.to_json().endswith("\n"))

    def test_from_dict_fills_omitted_fields(self):
        policy = AssetScoringPolicy.from_dict({"weight_query_match": 9.0})
        self.assertEqual(policy.weight_query_match, 9.0)
        self.assertEqual(
            policy.reuse_semantic_min_shared_terms,
            DEFAULT_SCORING_POLICY.reuse_semantic_min_shared_terms,
        )

    def test_rejects_unknown_field(self):
        with self.assertRaises(AssetResolutionError):
            AssetScoringPolicy.from_dict({"nope": 1})

    def test_rejects_negative_weight(self):
        with self.assertRaises(AssetResolutionError):
            AssetScoringPolicy(weight_query_match=-1.0)

    def test_rejects_min_meaningful_terms_below_one(self):
        with self.assertRaises(AssetResolutionError):
            AssetScoringPolicy(min_meaningful_query_terms=0)


class SanitizeQueryTests(unittest.TestCase):
    def test_drops_stopwords_and_keeps_content_terms(self):
        result = sanitize_query(
            "wide establishing, o escritorio e a mesa de trabalho",
            purpose="mostrar: escritorio / rotina — beat 1/4",
            visual_intent="a rotina de um escritorio corporativo",
        )
        self.assertIn("escritorio", result.terms)
        self.assertIn("mesa", result.terms)
        self.assertNotIn("o", result.terms)
        self.assertNotIn("e", result.terms)
        self.assertTrue(result.usable)

    def test_is_deterministic_and_order_stable(self):
        args = (
            "medium b roll human, pessoas lendo uma noticia",
            "mostrar: pessoas / noticia — beat 2/5",
            "duas pessoas lendo a mesma noticia",
        )
        first = sanitize_query(*args)
        second = sanitize_query(*args)
        self.assertEqual(first.terms, second.terms)

    def test_generic_only_query_is_not_usable(self):
        result = sanitize_query(
            "medium symbolic, outras, palavras",
            purpose="mostrar: outras / palavras — beat 3/7",
            visual_intent="mostrar: outras / palavras",
        )
        self.assertFalse(result.usable)
        self.assertEqual(result.reason, "needs_editorial_override")

    def test_shot_type_words_are_not_treated_as_content(self):
        result = sanitize_query(
            "wide establishing, ", purpose="", visual_intent=""
        )
        self.assertNotIn("establishing", result.terms)
        self.assertNotIn("wide", result.terms)
        self.assertFalse(result.usable)


class ScoreCandidateTests(unittest.TestCase):
    def test_wrong_media_type_disqualifies(self):
        req = _requirement(type="image")
        cand = _candidate(media_type="video", duration_seconds=10.0)
        breakdown = score_candidate(req, cand, DEFAULT_SCORING_POLICY)
        self.assertTrue(breakdown.disqualified)
        self.assertIn("media_type", breakdown.disqualified_reasons)

    def test_video_shorter_than_needed_disqualifies(self):
        req = _requirement(type="video", duration_needed_seconds=8.0)
        cand = _candidate(media_type="video", duration_seconds=3.0)
        breakdown = score_candidate(req, cand, DEFAULT_SCORING_POLICY)
        self.assertTrue(breakdown.disqualified)
        self.assertIn("duration", breakdown.disqualified_reasons)

    def test_resolution_below_floor_disqualifies_when_policy_strict(self):
        policy = AssetScoringPolicy(disqualify_below_resolution=True)
        req = _requirement()
        cand = _candidate(width=320, height=240)
        breakdown = score_candidate(req, cand, policy)
        self.assertTrue(breakdown.disqualified)
        self.assertIn("resolution", breakdown.disqualified_reasons)

    def test_orientation_mismatch_penalised_not_fatal_by_default(self):
        req = _requirement(orientation="landscape")
        cand = _candidate(width=1080, height=1920)  # portrait
        breakdown = score_candidate(req, cand, DEFAULT_SCORING_POLICY)
        self.assertFalse(breakdown.disqualified)
        self.assertLess(breakdown.components["orientation_match"], 0.0)

    def test_query_term_overlap_raises_score(self):
        req = _requirement(query="wide establishing, escritorio, mesa")
        near = _candidate(tags=("office", "desk", "escritorio", "mesa"))
        far = _candidate(
            candidate_id="local:img_beach",
            tags=("beach", "ocean", "sand"),
            title="Waves on an empty beach",
            description="sunny coastline",
        )
        near_total = score_candidate(req, near, DEFAULT_SCORING_POLICY).total
        far_total = score_candidate(req, far, DEFAULT_SCORING_POLICY).total
        self.assertGreater(near_total, far_total)

    def test_breakdown_components_are_inspectable_and_sum_to_total(self):
        req = _requirement()
        cand = _candidate()
        breakdown = score_candidate(req, cand, DEFAULT_SCORING_POLICY)
        self.assertAlmostEqual(
            breakdown.total, sum(breakdown.components.values()), places=9
        )
        self.assertIn("query_match", breakdown.components)
        self.assertIn("purpose_match", breakdown.components)
        self.assertIn("type_match", breakdown.components)


class RankCandidatesTests(unittest.TestCase):
    def test_ranking_is_deterministic_for_same_input(self):
        req = _requirement()
        cands = [
            _candidate(candidate_id=f"local:c{i}", tags=("office", f"tag{i}"))
            for i in range(5)
        ]
        first = [c.candidate_id for c in rank_candidates(req, cands, DEFAULT_SCORING_POLICY)]
        second = [c.candidate_id for c in rank_candidates(req, list(reversed(cands)), DEFAULT_SCORING_POLICY)]
        self.assertEqual(first, second)

    def test_disqualified_candidates_are_dropped(self):
        req = _requirement(type="image")
        good = _candidate(candidate_id="local:good")
        bad = _candidate(candidate_id="local:bad", media_type="video", duration_seconds=1.0)
        ranked = rank_candidates(req, [bad, good], DEFAULT_SCORING_POLICY)
        self.assertEqual([c.candidate_id for c in ranked], ["local:good"])

    def test_chosen_candidate_carries_score_and_breakdown(self):
        req = _requirement()
        ranked = rank_candidates(req, [_candidate()], DEFAULT_SCORING_POLICY)
        self.assertGreater(ranked[0].score, 0.0)
        self.assertTrue(ranked[0].score_breakdown)


class ReviewReuseTests(unittest.TestCase):
    def _two_shot_reuse(self, second_query, second_purpose):
        # one requirement shared by two shots from different scenes
        req = AssetRequirement(
            asset_id="asset_scene_04_01",
            type="image",
            query="close document, noticia, manchete, jornal",
            duration_needed_seconds=4.0,
            orientation="landscape",
            purpose="mostrar: noticia / manchete — beat 1/3",
            used_by=("scene_04_shot_01", "scene_09_shot_02"),
        )
        reqs = AssetRequirements(
            plan_id="p-asset-requirements",
            shot_plan_id="p-shot-plan",
            script_id="p",
            orientation="landscape",
            requirements=(req,),
        )
        shot_context = {
            "scene_04_shot_01": {
                "visual_query": "close document, noticia, manchete, jornal",
                "purpose": "mostrar: noticia / manchete — beat 1/3",
                "visual_intent": "a mesma noticia lida de dois jeitos",
            },
            "scene_09_shot_02": {
                "visual_query": second_query,
                "purpose": second_purpose,
                "visual_intent": second_purpose,
            },
        }
        return reqs, shot_context

    def test_compatible_reuse_is_kept(self):
        reqs, ctx = self._two_shot_reuse(
            "close document, noticia, jornal, manchete",
            "mostrar: noticia / jornal — beat 2/4",
        )
        revised = review_reuse(reqs, ctx, DEFAULT_SCORING_POLICY)
        self.assertEqual(len(revised.requirements), 1)
        self.assertEqual(
            revised.requirements[0].used_by,
            ("scene_04_shot_01", "scene_09_shot_02"),
        )
        self.assertEqual(revised.split_count, 0)

    def test_incompatible_reuse_is_split_into_its_own_requirement(self):
        reqs, ctx = self._two_shot_reuse(
            "medium symbolic, outras, palavras",
            "mostrar: outras / palavras — beat 2/4",
        )
        revised = review_reuse(reqs, ctx, DEFAULT_SCORING_POLICY)
        self.assertEqual(len(revised.requirements), 2)
        anchors = {r.asset_id: r.used_by for r in revised.requirements}
        self.assertIn(("scene_04_shot_01",), anchors.values())
        peeled = [r for r in revised.requirements if r.used_by == ("scene_09_shot_02",)]
        self.assertEqual(len(peeled), 1)
        self.assertNotEqual(peeled[0].asset_id, "asset_scene_04_01")
        self.assertEqual(revised.split_count, 1)

    def test_review_is_deterministic(self):
        reqs, ctx = self._two_shot_reuse(
            "medium symbolic, outras, palavras",
            "mostrar: outras / palavras — beat 2/4",
        )
        a = review_reuse(reqs, ctx, DEFAULT_SCORING_POLICY)
        b = review_reuse(reqs, ctx, DEFAULT_SCORING_POLICY)
        self.assertEqual(
            [r.to_dict() for r in a.requirements],
            [r.to_dict() for r in b.requirements],
        )


class ProvenanceContractTests(unittest.TestCase):
    def test_round_trips(self):
        prov = _provenance()
        self.assertEqual(AssetProvenance.from_dict(json.loads(prov.to_json())), prov)

    def test_rejects_non_hex_sha256(self):
        with self.assertRaises(AssetResolutionError):
            _provenance(sha256="not-a-hash")

    def test_rejects_short_sha256(self):
        with self.assertRaises(AssetResolutionError):
            _provenance(sha256="abc123")

    def test_rejects_unknown_license_token_is_allowed_but_blank_is_not(self):
        _provenance(license="unknown")  # allowed: honestly unknown
        with self.assertRaises(AssetResolutionError):
            _provenance(license="")

    def test_rejects_bad_timestamp(self):
        with self.assertRaises(AssetResolutionError):
            _provenance(acquired_at="last tuesday")


class ResolvedAssetContractTests(unittest.TestCase):
    def test_round_trips_with_embedded_requirement_and_provenance(self):
        resolved = ResolvedAsset(
            asset_id="asset_scene_01_01",
            local_path="output/resolved-assets/files/asset_scene_01_01.jpg",
            candidate_id="local:img_office_01",
            requirement=_requirement(),
            score=12.5,
            provenance=_provenance(),
        )
        restored = ResolvedAsset.from_dict(json.loads(resolved.to_json()))
        self.assertEqual(restored, resolved)

    def test_provenance_is_mandatory(self):
        with self.assertRaises(AssetResolutionError):
            ResolvedAsset(
                asset_id="asset_scene_01_01",
                local_path="x.jpg",
                candidate_id="local:img_office_01",
                requirement=_requirement(),
                score=1.0,
                provenance=None,
            )

    def test_local_path_must_match_provenance_local_path(self):
        with self.assertRaises(AssetResolutionError):
            ResolvedAsset(
                asset_id="asset_scene_01_01",
                local_path="a.jpg",
                candidate_id="local:img_office_01",
                requirement=_requirement(),
                score=1.0,
                provenance=_provenance(local_path="b.jpg"),
            )


class ResolutionPlanContractTests(unittest.TestCase):
    def _plan(self, resolved=(), unresolved=()):
        return AssetResolutionPlan(
            plan_id="p-asset-resolution-plan",
            shot_plan_id="p-shot-plan",
            script_id="p",
            resolved=tuple(resolved),
            unresolved=tuple(unresolved),
        )

    def test_round_trips(self):
        resolved = ResolvedAsset(
            asset_id="asset_scene_01_01",
            local_path=_provenance().local_path,
            candidate_id="local:img_office_01",
            requirement=_requirement(),
            score=3.0,
            provenance=_provenance(),
        )
        plan = self._plan(resolved=[resolved])
        self.assertEqual(
            AssetResolutionPlan.from_dict(json.loads(plan.to_json())), plan
        )

    def test_bindings_cover_exactly_the_resolved_assets(self):
        resolved = ResolvedAsset(
            asset_id="asset_scene_01_01",
            local_path=_provenance().local_path,
            candidate_id="local:img_office_01",
            requirement=_requirement(),
            score=3.0,
            provenance=_provenance(),
        )
        unresolved = UnresolvedRequirement(
            asset_id="asset_scene_02_01",
            requirement=_requirement(asset_id="asset_scene_02_01", used_by=("scene_02_shot_01",)),
            reason="no_compatible_candidate",
            detail="library had no landscape office clip",
        )
        plan = self._plan(resolved=[resolved], unresolved=[unresolved])
        self.assertEqual(plan.to_bindings(), {"asset_scene_01_01": _provenance().local_path})

    def test_validate_against_requirements_needs_every_asset_id_once(self):
        req_a = _requirement(asset_id="asset_scene_01_01", used_by=("scene_01_shot_01",))
        req_b = _requirement(asset_id="asset_scene_02_01", used_by=("scene_02_shot_01",))
        requirements = AssetRequirements(
            plan_id="p-asset-requirements",
            shot_plan_id="p-shot-plan",
            script_id="p",
            orientation="landscape",
            requirements=(req_a, req_b),
        )
        resolved = ResolvedAsset(
            asset_id="asset_scene_01_01",
            local_path=_provenance().local_path,
            candidate_id="local:img_office_01",
            requirement=req_a,
            score=3.0,
            provenance=_provenance(),
        )
        # missing asset_scene_02_01 entirely -> invalid
        plan = self._plan(resolved=[resolved])
        with self.assertRaises(AssetResolutionError):
            plan.validate_against(requirements)
        # now cover it as unresolved -> valid
        plan_ok = self._plan(
            resolved=[resolved],
            unresolved=[
                UnresolvedRequirement(
                    asset_id="asset_scene_02_01",
                    requirement=req_b,
                    reason="no_candidates",
                    detail=None,
                )
            ],
        )
        plan_ok.validate_against(requirements)

    def test_asset_id_cannot_be_both_resolved_and_unresolved(self):
        resolved = ResolvedAsset(
            asset_id="asset_scene_01_01",
            local_path=_provenance().local_path,
            candidate_id="local:img_office_01",
            requirement=_requirement(),
            score=3.0,
            provenance=_provenance(),
        )
        dup = UnresolvedRequirement(
            asset_id="asset_scene_01_01",
            requirement=_requirement(),
            reason="no_candidates",
            detail=None,
        )
        with self.assertRaises(AssetResolutionError):
            self._plan(resolved=[resolved], unresolved=[dup])


class SchemaShapeTests(unittest.TestCase):
    """The v1 schema's declared keys must match what the dataclasses serialise
    (a light stdlib check — the repo carries no jsonschema dependency)."""

    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        self.schema = json.loads(
            (root / "schemas" / "asset-resolution-plan-v1.schema.json").read_text("utf-8")
        )

    def _required(self, ref):
        return set(self.schema["$defs"][ref]["required"])

    def test_plan_serialisation_matches_schema(self):
        plan = AssetResolutionPlan(
            plan_id="p-asset-resolution-plan",
            shot_plan_id="p-shot-plan",
            script_id="p",
            resolved=(
                ResolvedAsset(
                    asset_id="asset_scene_01_01",
                    local_path=_provenance().local_path,
                    candidate_id="local:img_office_01",
                    requirement=_requirement(),
                    score=3.0,
                    provenance=_provenance(),
                ),
            ),
            unresolved=(
                UnresolvedRequirement(
                    asset_id="asset_scene_02_01",
                    requirement=_requirement(asset_id="asset_scene_02_01", used_by=("scene_02_shot_01",)),
                    reason="needs_editorial_override",
                    detail="thin query",
                    sanitized_query="mesa",
                ),
            ),
        )
        payload = json.loads(plan.to_json())
        self.assertEqual(set(payload), set(self.schema["required"]))
        self.assertEqual(
            set(payload["resolved"][0]), self._required("resolved_asset")
        )
        self.assertEqual(
            set(payload["resolved"][0]["provenance"]), self._required("asset_provenance")
        )
        self.assertTrue(
            self._required("unresolved_requirement").issubset(set(payload["unresolved"][0]))
        )

    def test_candidate_serialisation_matches_schema(self):
        payload = json.loads(_candidate().to_json())
        self.assertEqual(set(payload), self._required("asset_candidate"))


if __name__ == "__main__":
    unittest.main()
