"""Tests for the Scene Planner + Shot Planner domain layer.

Covers RhythmPolicy, the four planning contracts, the local vs cross-document
invariants, plan_scenes / plan_shots / apply_overrides / shot_plan_to_edit_plan,
and the Desumanizando acceptance run.
"""

import json
import math
import unittest
from pathlib import Path

from video_generator.domain import (
    DEFAULT_RHYTHM_POLICY,
    AssetRequirement,
    AssetRequirements,
    EditPlan,
    NarrativeBlock,
    NarrativeScript,
    PlanningError,
    RhythmPolicy,
    Scene,
    ScenePlan,
    Shot,
    ShotPlan,
    TargetFormat,
    apply_overrides,
    plan_scenes,
    plan_shots,
    shot_plan_to_edit_plan,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
ROTEIRO = REPO_ROOT / "assets" / "desumanizando" / "video_01" / "roteiro_narracao.txt"
# The first Desumanizando narration runs ~4 min 30 s at Kokoro speed 0.92.
DESUMANIZANDO_TOTAL_SECONDS = 270.0


class RhythmPolicyTests(unittest.TestCase):
    def test_default_round_trips_and_is_deterministic(self):
        policy = RhythmPolicy()
        restored = RhythmPolicy.from_dict(json.loads(policy.to_json()))
        self.assertEqual(restored, policy)
        self.assertEqual(policy.to_json(), policy.to_json())
        self.assertTrue(policy.to_json().endswith("\n"))
        self.assertEqual(policy, DEFAULT_RHYTHM_POLICY)

    def test_from_dict_fills_omitted_fields_from_default(self):
        policy = RhythmPolicy.from_dict({"speaking_rate_wpm": 180.0})
        self.assertEqual(policy.speaking_rate_wpm, 180.0)
        self.assertEqual(policy.min_shot_seconds, DEFAULT_RHYTHM_POLICY.min_shot_seconds)
        self.assertEqual(policy.scale_cycle, DEFAULT_RHYTHM_POLICY.scale_cycle)

    def test_rejects_unordered_duration_band(self):
        with self.assertRaises(PlanningError):
            RhythmPolicy(min_shot_seconds=7.0, soft_max_shot_seconds=6.0)

    def test_rejects_shot_over_absolute_ceiling_relationship(self):
        with self.assertRaises(PlanningError):
            RhythmPolicy(soft_max_shot_seconds=12.0, absolute_max_shot_seconds=10.0)

    def test_rejects_asset_type_key_absent_from_weights(self):
        with self.assertRaises(PlanningError):
            RhythmPolicy(asset_type_for_shot_type={"not_a_shot_type": "video"})

    def test_rejects_bad_asset_type_value(self):
        with self.assertRaises(PlanningError):
            RhythmPolicy(asset_type_for_shot_type={"object": "gif"})

    def test_rejects_ratio_and_fraction_out_of_range(self):
        with self.assertRaises(PlanningError):
            RhythmPolicy(min_distinct_asset_ratio=1.5)
        with self.assertRaises(PlanningError):
            RhythmPolicy(duration_jitter_fraction=1.2)

    def test_discouraged_cliches_is_a_query_concern_not_a_shot_type(self):
        # the clichés must never be shot types the picker can land on
        for cliche in DEFAULT_RHYTHM_POLICY.discouraged_cliches:
            self.assertNotIn(cliche, DEFAULT_RHYTHM_POLICY.shot_type_weights)


class NarrativeScriptTests(unittest.TestCase):
    def test_from_text_splits_on_blank_lines(self):
        script = NarrativeScript.from_text("s1", "Um.\n\nDois.\n\n\nTrês.\n")
        self.assertEqual([b.text for b in script.blocks], ["Um.", "Dois.", "Três."])
        self.assertTrue(all(b.duration_seconds is None for b in script.blocks))
        self.assertEqual(script.script_id, "s1")

    def test_round_trips(self):
        script = NarrativeScript(
            script_id="s1",
            blocks=(
                NarrativeBlock("Abertura.", visual_intent="hook"),
                NarrativeBlock("Ponto de virada.", emphasis=True),
            ),
            total_duration_seconds=42.0,
        )
        restored = NarrativeScript.from_dict(json.loads(script.to_json()))
        self.assertEqual(restored, script)

    def test_rejects_empty_block_text(self):
        with self.assertRaises(PlanningError):
            NarrativeBlock("   ")

    def test_rejects_no_blocks(self):
        with self.assertRaises(PlanningError):
            NarrativeScript(script_id="s1", blocks=())

    def test_rejects_total_disagreeing_with_block_durations(self):
        with self.assertRaises(PlanningError):
            NarrativeScript(
                script_id="s1",
                blocks=(
                    NarrativeBlock("Um.", duration_seconds=10.0),
                    NarrativeBlock("Dois.", duration_seconds=10.0),
                ),
                total_duration_seconds=100.0,
            )


def _good_scene(**overrides):
    base = dict(
        scene_id="scene_01",
        narration="Um bloco de narração com sentido.",
        duration_seconds=12.0,
        visual_intent="mostrar: confirmação / crença",
        visual_intent_provenance="derived",
        source_block_indices=(0,),
        emphasis_offsets=(),
    )
    base.update(overrides)
    return Scene(**base)


def _good_scene_plan(**overrides):
    base = dict(
        plan_id="s1-scene-plan",
        script_id="s1",
        seed=0,
        policy=DEFAULT_RHYTHM_POLICY,
        scenes=(
            _good_scene(scene_id="scene_01", source_block_indices=(0,)),
            _good_scene(scene_id="scene_02", source_block_indices=(1, 2), duration_seconds=8.0),
        ),
        total_duration_seconds=20.0,
    )
    base.update(overrides)
    return ScenePlan(**base)


class SceneContractTests(unittest.TestCase):
    def test_round_trips(self):
        plan = _good_scene_plan()
        restored = ScenePlan.from_dict(json.loads(plan.to_json()))
        self.assertEqual(restored, plan)
        self.assertEqual(plan.to_json(), plan.to_json())

    def test_rejects_non_contiguous_scene_ids(self):
        with self.assertRaises(PlanningError):
            _good_scene_plan(
                scenes=(
                    _good_scene(scene_id="scene_01", source_block_indices=(0,)),
                    _good_scene(scene_id="scene_03", source_block_indices=(1,)),
                ),
                total_duration_seconds=24.0,
            )

    def test_rejects_block_index_gap_across_scenes(self):
        with self.assertRaises(PlanningError):
            _good_scene_plan(
                scenes=(
                    _good_scene(scene_id="scene_01", source_block_indices=(0,)),
                    _good_scene(scene_id="scene_02", source_block_indices=(2,)),
                ),
                total_duration_seconds=24.0,
            )

    def test_rejects_scene_duration_sum_mismatch(self):
        with self.assertRaises(PlanningError):
            _good_scene_plan(total_duration_seconds=999.0)

    def test_rejects_emphasis_offset_outside_open_interval(self):
        with self.assertRaises(PlanningError):
            _good_scene(emphasis_offsets=(0.0,))
        with self.assertRaises(PlanningError):
            _good_scene(duration_seconds=12.0, emphasis_offsets=(12.0,))

    def test_rejects_unknown_provenance(self):
        with self.assertRaises(PlanningError):
            _good_scene(visual_intent_provenance="guessed")

    def test_post_init_does_not_need_the_script(self):
        # a self-contained ScenePlan validates without any NarrativeScript
        _good_scene_plan()


class ScenePlanValidateAgainstTests(unittest.TestCase):
    def _script(self):
        return NarrativeScript(
            script_id="s1",
            blocks=(
                NarrativeBlock("Bloco um."),
                NarrativeBlock("Bloco dois."),
                NarrativeBlock("Bloco três.", emphasis=True),
            ),
        )

    def test_rejects_when_max_block_index_is_not_last_block(self):
        script = self._script()
        plan = _good_scene_plan(
            scenes=(
                _good_scene(scene_id="scene_01", source_block_indices=(0,)),
                _good_scene(scene_id="scene_02", source_block_indices=(1,), duration_seconds=8.0),
            ),
            total_duration_seconds=20.0,
        )
        with self.assertRaises(PlanningError):
            plan.validate_against(script)

    def test_rejects_when_interior_emphasis_block_has_no_offset(self):
        script = self._script()
        # blocks 1 and 2 in scene_02; block 2 is emphasis and interior -> needs an offset
        plan = _good_scene_plan(
            scenes=(
                _good_scene(
                    scene_id="scene_01",
                    source_block_indices=(0,),
                    narration="Bloco um.",
                ),
                _good_scene(
                    scene_id="scene_02",
                    source_block_indices=(1, 2),
                    duration_seconds=8.0,
                    narration="Bloco dois.\n\nBloco três.",
                    emphasis_offsets=(),
                ),
            ),
            total_duration_seconds=20.0,
        )
        with self.assertRaises(PlanningError):
            plan.validate_against(script)


class PlanScenesTests(unittest.TestCase):
    def test_measured_total_is_preserved_within_one_ms(self):
        script = NarrativeScript.from_text(
            "s1", "Parágrafo um bem completo.\n\nParágrafo dois igualmente.\n\nTrês.",
            total_duration_seconds=60.0,
        )
        plan = plan_scenes(script)
        self.assertAlmostEqual(plan.total_duration_seconds, 60.0, places=3)
        self.assertAlmostEqual(
            sum(s.duration_seconds for s in plan.scenes), 60.0, places=3
        )
        plan.validate_against(script)

    def test_estimate_path_sums_scene_durations(self):
        script = NarrativeScript.from_text("s1", "Um dois três quatro.\n\nCinco seis sete oito.")
        plan = plan_scenes(script)
        self.assertAlmostEqual(
            plan.total_duration_seconds,
            sum(s.duration_seconds for s in plan.scenes),
            places=3,
        )

    def test_block_indices_cover_every_block_in_order(self):
        script = NarrativeScript.from_text(
            "s1", "\n\n".join(f"Bloco número {n} com texto." for n in range(12)),
            total_duration_seconds=120.0,
        )
        plan = plan_scenes(script)
        seen = [i for scene in plan.scenes for i in scene.source_block_indices]
        self.assertEqual(seen, list(range(12)))

    def test_authored_visual_intent_is_kept_and_marked(self):
        script = NarrativeScript(
            script_id="s1",
            blocks=(
                NarrativeBlock("Abertura com um gancho forte.", visual_intent="rosto na janela"),
                NarrativeBlock("Continuação do raciocínio aqui."),
            ),
            total_duration_seconds=30.0,
        )
        plan = plan_scenes(script)
        self.assertEqual(plan.scenes[0].visual_intent, "rosto na janela")
        self.assertEqual(plan.scenes[0].visual_intent_provenance, "authored")

    def test_derived_visual_intent_avoids_cliche_tokens(self):
        script = NarrativeScript.from_text(
            "s1",
            "O cérebro cérebro cérebro processa cérebro tudo isso rapido.\n\n"
            "Outra parte qualquer do texto para a segunda cena aqui.",
            total_duration_seconds=40.0,
        )
        plan = plan_scenes(script)
        self.assertEqual(plan.scenes[0].visual_intent_provenance, "derived")
        self.assertNotIn("cérebro", plan.scenes[0].visual_intent.lower())

    def test_interior_emphasis_becomes_an_offset(self):
        script = NarrativeScript(
            script_id="s1",
            blocks=(
                NarrativeBlock("Primeiro bloco da cena um.", duration_seconds=8.0),
                NarrativeBlock("Segundo bloco, vira a chave.", duration_seconds=4.0, emphasis=True),
            ),
        )
        plan = plan_scenes(script)
        # both blocks land in one scene (8s hits min_scene, then 4s trailing merges back)
        self.assertEqual(len(plan.scenes), 1)
        self.assertEqual(len(plan.scenes[0].emphasis_offsets), 1)
        self.assertAlmostEqual(plan.scenes[0].emphasis_offsets[0], 8.0, places=3)

    def test_first_block_emphasis_makes_no_offset(self):
        script = NarrativeScript(
            script_id="s1",
            blocks=(
                NarrativeBlock("Bloco de abertura enfático.", duration_seconds=10.0, emphasis=True),
                NarrativeBlock("Bloco seguinte, nova cena.", duration_seconds=10.0),
            ),
        )
        plan = plan_scenes(script)
        self.assertEqual(plan.scenes[0].emphasis_offsets, ())

    def test_is_byte_identical_for_fixed_inputs(self):
        script = NarrativeScript.from_text(
            "s1", "Um texto.\n\nDois texto.\n\nTrês texto.", total_duration_seconds=45.0
        )
        self.assertEqual(plan_scenes(script, seed=7).to_json(), plan_scenes(script, seed=7).to_json())

    def test_desumanizando_yields_at_least_eight_scenes(self):
        script = NarrativeScript.from_text(
            "desumanizando-01", ROTEIRO.read_text(encoding="utf-8"),
            total_duration_seconds=DESUMANIZANDO_TOTAL_SECONDS,
        )
        plan = plan_scenes(script)
        self.assertGreaterEqual(len(plan.scenes), 8)
        plan.validate_against(script)


def _script_for_shots(total=270.0):
    return NarrativeScript.from_text(
        "desumanizando-01", ROTEIRO.read_text(encoding="utf-8"),
        total_duration_seconds=total,
    )


class ShotContractTests(unittest.TestCase):
    def _shot(self, **overrides):
        base = dict(
            shot_id="scene_01_shot_01",
            scene_id="scene_01",
            index=1,
            duration_seconds=4.0,
            asset_type="image",
            shot_type="object",
            scale="wide",
            visual_query="wide object, uma mesa",
            purpose="mostrar: algo — beat 1/2",
            beat=False,
            asset_id="asset_scene_01_01",
            reuse_of=None,
            framing={"motion": "zoom_in", "crop_bias": "center"},
            justification=None,
            provenance={"visual_query": "derived", "purpose": "derived", "shot_type": "derived"},
        )
        base.update(overrides)
        return Shot(**base)

    def _wrap(self, shot):
        """A single shot in an otherwise-valid ShotPlan carrying the default policy."""
        return ShotPlan(
            plan_id="s1-shot-plan",
            scene_plan_id="s1-scene-plan",
            script_id="s1",
            seed=0,
            policy=DEFAULT_RHYTHM_POLICY,
            shots=(shot,),
        )

    def test_round_trips_through_a_shot_plan(self):
        plan = ShotPlan(
            plan_id="s1-shot-plan",
            scene_plan_id="s1-scene-plan",
            script_id="s1",
            seed=0,
            policy=DEFAULT_RHYTHM_POLICY,
            shots=(
                self._shot(shot_id="scene_01_shot_01", index=1),
                self._shot(
                    shot_id="scene_01_shot_02", index=2, scale="medium",
                    shot_type="environment", asset_type="video",
                    asset_id="asset_scene_01_02",
                    framing={"motion": "pan_left", "crop_bias": "left"},
                ),
            ),
        )
        restored = ShotPlan.from_dict(json.loads(plan.to_json()))
        self.assertEqual(restored, plan)

    def test_rejects_shot_id_not_matching_scene_and_index(self):
        with self.assertRaises(PlanningError):
            self._shot(shot_id="scene_01_shot_09", index=1)

    def test_rejects_duration_over_absolute_ceiling(self):
        with self.assertRaises(PlanningError):
            self._wrap(self._shot(duration_seconds=11.0, justification="x"))

    def test_requires_justification_exactly_when_over_soft_max(self):
        with self.assertRaises(PlanningError):
            self._wrap(self._shot(duration_seconds=9.0))  # over soft_max 8, no justification
        self._wrap(self._shot(duration_seconds=9.0, justification="segment floor"))  # ok
        with self.assertRaises(PlanningError):
            self._wrap(self._shot(duration_seconds=4.0, justification="not needed"))

    def test_rejects_unknown_shot_type_or_scale(self):
        with self.assertRaises(PlanningError):
            self._wrap(self._shot(shot_type="brain"))
        with self.assertRaises(PlanningError):
            self._wrap(self._shot(scale="dutch"))

    def test_rejects_asset_type_inconsistent_with_shot_type(self):
        with self.assertRaises(PlanningError):
            self._wrap(self._shot(shot_type="interface", asset_type="image"))  # -> video

    def test_rejects_bad_framing_motion(self):
        with self.assertRaises(PlanningError):
            self._shot(framing={"motion": "barrel_roll", "crop_bias": "center"})

    def test_rejects_reuse_of_pointing_forward(self):
        with self.assertRaises(PlanningError):
            ShotPlan(
                plan_id="s1-shot-plan",
                scene_plan_id="s1-scene-plan",
                script_id="s1",
                seed=0,
                policy=DEFAULT_RHYTHM_POLICY,
                shots=(
                    self._shot(shot_id="scene_01_shot_01", index=1, reuse_of="asset_scene_01_02"),
                    self._shot(shot_id="scene_01_shot_02", index=2, asset_id="asset_scene_01_02"),
                ),
            )

    def test_rejects_consecutive_same_shot_type_beyond_limit(self):
        with self.assertRaises(PlanningError):
            ShotPlan(
                plan_id="s1-shot-plan",
                scene_plan_id="s1-scene-plan",
                script_id="s1",
                seed=0,
                policy=DEFAULT_RHYTHM_POLICY,
                shots=(
                    self._shot(shot_id="scene_01_shot_01", index=1, shot_type="object"),
                    self._shot(
                        shot_id="scene_01_shot_02", index=2, shot_type="object",
                        asset_id="asset_scene_01_02", scale="medium",
                    ),
                ),
            )


class PlanShotsTests(unittest.TestCase):
    def setUp(self):
        self.script = _script_for_shots()
        self.scene_plan = plan_scenes(self.script)
        self.shot_plan, self.assets = plan_shots(
            self.scene_plan, orientation="landscape"
        )

    def test_returns_validated_documents(self):
        self.shot_plan.validate_against(self.scene_plan)
        self.assets.validate_against(self.shot_plan)

    def test_every_shot_duration_is_within_bounds(self):
        p = DEFAULT_RHYTHM_POLICY
        for shot in self.shot_plan.shots:
            self.assertGreater(shot.duration_seconds, 0.0)
            self.assertLessEqual(shot.duration_seconds, p.absolute_max_shot_seconds)
            self.assertGreaterEqual(
                shot.duration_seconds, p.min_shot_seconds - 1e-6
            )
            if shot.duration_seconds > p.soft_max_shot_seconds + 1e-6:
                self.assertIsNotNone(shot.justification)

    def test_per_scene_durations_sum_to_scene_duration(self):
        for scene in self.scene_plan.scenes:
            shots = [s for s in self.shot_plan.shots if s.scene_id == scene.scene_id]
            self.assertAlmostEqual(
                sum(s.duration_seconds for s in shots),
                scene.duration_seconds,
                places=3,
            )

    def test_residual_is_spread_not_dumped_on_the_last_shot(self):
        # find a scene with >= 3 shots and assert the last shot is not an outlier
        for scene in self.scene_plan.scenes:
            shots = [s for s in self.shot_plan.shots if s.scene_id == scene.scene_id]
            if len(shots) >= 3:
                durs = [s.duration_seconds for s in shots]
                mean = sum(durs) / len(durs)
                spread = max(durs) - min(durs)
                self.assertGreater(spread, 0.0)  # jitter actually happened
                last_dev = abs(durs[-1] - mean)
                other_dev = max(abs(d - mean) for d in durs[:-1])
                # the last shot must not carry a disproportionate share of the deviation
                self.assertLessEqual(last_dev, other_dev + 1e-6)
                return
        self.skipTest("no scene with >= 3 shots")

    def test_shot_ids_are_unique_and_well_formed(self):
        ids = [s.shot_id for s in self.shot_plan.shots]
        self.assertEqual(len(ids), len(set(ids)))
        for s in self.shot_plan.shots:
            self.assertEqual(s.shot_id, f"{s.scene_id}_shot_{s.index:02d}")

    def test_index_is_contiguous_per_scene(self):
        by_scene: dict[str, list[int]] = {}
        for s in self.shot_plan.shots:
            by_scene.setdefault(s.scene_id, []).append(s.index)
        for scene_id, indices in by_scene.items():
            self.assertEqual(indices, list(range(1, len(indices) + 1)))

    def test_emphasis_offsets_have_exactly_one_beat_shot(self):
        for scene in self.scene_plan.scenes:
            if not scene.emphasis_offsets:
                continue
            shots = [s for s in self.shot_plan.shots if s.scene_id == scene.scene_id]
            running = 0.0
            starts = []
            for s in shots:
                starts.append((running, s))
                running += s.duration_seconds
            for offset in scene.emphasis_offsets:
                matching = [s for start, s in starts if abs(start - offset) <= 1e-2]
                self.assertEqual(len(matching), 1)
                self.assertTrue(matching[0].beat)
        # no other beats
        for scene in self.scene_plan.scenes:
            shots = [s for s in self.shot_plan.shots if s.scene_id == scene.scene_id]
            running = 0.0
            for s in shots:
                if s.beat:
                    self.assertTrue(
                        any(abs(running - o) <= 1e-2 for o in scene.emphasis_offsets)
                    )
                running += s.duration_seconds

    def test_shot_type_distribution_is_varied(self):
        counts: dict[str, int] = {}
        for s in self.shot_plan.shots:
            counts[s.shot_type] = counts.get(s.shot_type, 0) + 1
        total = len(self.shot_plan.shots)
        self.assertGreaterEqual(len(counts), 5)
        self.assertLess(max(counts.values()) / total, 0.4)

    def test_is_deterministic_for_same_inputs(self):
        a_shot, a_assets = plan_shots(self.scene_plan, orientation="landscape")
        b_shot, b_assets = plan_shots(self.scene_plan, orientation="landscape")
        self.assertEqual(a_shot.to_json(), b_shot.to_json())
        self.assertEqual(a_assets.to_json(), b_assets.to_json())

    def test_a_different_seed_changes_the_plan_but_stays_valid(self):
        other_scene_plan = plan_scenes(self.script, seed=99)
        other_shot, other_assets = plan_shots(
            other_scene_plan, seed=99, orientation="landscape"
        )
        other_shot.validate_against(other_scene_plan)
        other_assets.validate_against(other_shot)
        self.assertNotEqual(other_shot.to_json(), self.shot_plan.to_json())

    def test_reuse_respects_gap_and_cap_and_ratio(self):
        p = DEFAULT_RHYTHM_POLICY
        users: dict[str, int] = {}
        for s in self.shot_plan.shots:
            users[s.asset_id] = users.get(s.asset_id, 0) + 1
        self.assertLessEqual(max(users.values()), p.reuse_max_per_source)
        distinct = len(users)
        self.assertGreaterEqual(
            distinct, math.ceil(p.min_distinct_asset_ratio * len(self.shot_plan.shots))
        )


class ShotPlanValidateAgainstTests(unittest.TestCase):
    def setUp(self):
        self.script = _script_for_shots()
        self.scene_plan = plan_scenes(self.script)
        self.shot_plan, self.assets = plan_shots(self.scene_plan, orientation="landscape")

    def _plain_shot(self, scene_id, index, duration, **extra):
        base = dict(
            shot_id=f"{scene_id}_shot_{index:02d}",
            scene_id=scene_id,
            index=index,
            duration_seconds=duration,
            asset_type="image",
            shot_type="object",
            scale="wide",
            visual_query="q",
            purpose="p",
            beat=False,
            asset_id=f"asset_{scene_id}_{index:02d}",
            reuse_of=None,
            framing={"motion": None, "crop_bias": "center"},
            justification=None,
            provenance={"visual_query": "derived", "purpose": "derived", "shot_type": "derived"},
        )
        base.update(extra)
        return Shot(**base)

    def test_rejects_scene_id_absent_from_scene_plan(self):
        only_scene = Scene(
            scene_id="scene_01",
            narration="Uma cena curta e sintética.",
            duration_seconds=5.0,
            visual_intent="mostrar: algo",
            visual_intent_provenance="derived",
            source_block_indices=(0,),
            emphasis_offsets=(),
        )
        one_scene = ScenePlan(
            plan_id="x-scene-plan",
            script_id="x",
            seed=0,
            policy=DEFAULT_RHYTHM_POLICY,
            scenes=(only_scene,),
            total_duration_seconds=5.0,
        )
        two_scene_shots = ShotPlan(
            plan_id="x-shot-plan",
            scene_plan_id="x-scene-plan",
            script_id="x",
            seed=0,
            policy=DEFAULT_RHYTHM_POLICY,
            shots=(
                self._plain_shot("scene_01", 1, 5.0),
                self._plain_shot("scene_02", 1, 5.0, shot_type="document"),
            ),
        )
        with self.assertRaises(PlanningError):
            two_scene_shots.validate_against(one_scene)

    def test_rejects_per_scene_duration_mismatch(self):
        # rebuild the first scene's shots to sum wrong
        shots = list(self.shot_plan.shots)
        first = shots[0]
        shots[0] = Shot(**{**first.to_dict(), "duration_seconds": first.duration_seconds + 2.0})
        with self.assertRaises(PlanningError):
            broken = ShotPlan(
                plan_id=self.shot_plan.plan_id,
                scene_plan_id=self.shot_plan.scene_plan_id,
                script_id=self.shot_plan.script_id,
                seed=self.shot_plan.seed,
                policy=self.shot_plan.policy,
                shots=tuple(shots),
            )
            broken.validate_against(self.scene_plan)


class EmphasisTimelineTests(unittest.TestCase):
    def _script(self):
        # one scene from three blocks; the middle block is an emphasis beat
        return NarrativeScript(
            script_id="emph",
            blocks=(
                NarrativeBlock("Primeiro trecho curto de abertura.", duration_seconds=4.0),
                NarrativeBlock("A CHAVE VIRA AQUI, um ponto de virada claro.", duration_seconds=8.0, emphasis=True),
                NarrativeBlock("Fecho do raciocínio depois da virada, tranquilo.", duration_seconds=8.0),
            ),
        )

    def test_emphasis_offset_gets_a_single_beat_shot_on_the_timeline(self):
        script = self._script()
        scene_plan = plan_scenes(script)
        shot_plan, assets = plan_shots(scene_plan, orientation="landscape")
        shot_plan.validate_against(scene_plan)
        assets.validate_against(shot_plan)
        beats = [s for s in shot_plan.shots if s.beat]
        self.assertGreaterEqual(len(beats), 1)
        # every emphasis offset in every scene lands on exactly one beat shot start
        for scene in scene_plan.scenes:
            running = 0.0
            starts = []
            for s in shot_plan.shots:
                if s.scene_id != scene.scene_id:
                    continue
                starts.append((running, s))
                running += s.duration_seconds
            for offset in scene.emphasis_offsets:
                hit = [s for start, s in starts if abs(start - offset) <= 1e-2]
                self.assertEqual(len(hit), 1)
                self.assertTrue(hit[0].beat)


class ApplyOverridesTests(unittest.TestCase):
    def setUp(self):
        self.script = _script_for_shots()
        self.scene_plan = plan_scenes(self.script)
        self.shot_plan, self.assets = plan_shots(self.scene_plan, orientation="landscape")

    def test_scene_visual_intent_override_lands_in_the_scene_plan(self):
        overrides = {"scenes": {"scene_02": {"visual_intent": "viés de confirmação como filtro"}}}
        sp, shp, ar = apply_overrides(self.scene_plan, self.shot_plan, self.assets, overrides,
                                      script=self.script)
        scene = sp.scene("scene_02")
        self.assertEqual(scene.visual_intent, "viés de confirmação como filtro")
        self.assertEqual(scene.visual_intent_provenance, "authored")
        # derived shots of that scene mention the new intent in their purpose
        for shot in shp.shots:
            if shot.scene_id == "scene_02" and shot.provenance["purpose"] == "derived":
                self.assertIn("viés de confirmação como filtro", shot.purpose)
        sp.validate_against(self.script)
        shp.validate_against(sp)
        ar.validate_against(shp)

    def test_shot_editorial_override_flips_only_that_provenance(self):
        target = self.shot_plan.shots[3].shot_id
        overrides = {"shots": {target: {"visual_query": "duas manchetes opostas lado a lado"}}}
        sp, shp, ar = apply_overrides(self.scene_plan, self.shot_plan, self.assets, overrides,
                                      script=self.script)
        changed = next(s for s in shp.shots if s.shot_id == target)
        self.assertEqual(changed.visual_query, "duas manchetes opostas lado a lado")
        self.assertEqual(changed.provenance["visual_query"], "authored")
        self.assertEqual(changed.provenance["purpose"], "derived")

    def test_shot_type_override_propagates_asset_type_into_requirements(self):
        # find a shot whose current shot_type maps to image, override to a video type
        target = next(
            s for s in self.shot_plan.shots if s.asset_type == "image"
        )
        overrides = {"shots": {target.shot_id: {"shot_type": "interface"}}}
        sp, shp, ar = apply_overrides(self.scene_plan, self.shot_plan, self.assets, overrides,
                                      script=self.script)
        changed = next(s for s in shp.shots if s.shot_id == target.shot_id)
        self.assertEqual(changed.shot_type, "interface")
        self.assertEqual(changed.asset_type, "video")
        req = next(r for r in ar.requirements if changed.shot_id in r.used_by)
        self.assertEqual(req.type, "video")
        ar.validate_against(shp)

    def test_rejects_structural_and_unknown_keys(self):
        first = self.shot_plan.shots[0].shot_id
        for bad in (
            {"shots": {first: {"duration_seconds": 3.0}}},
            {"shots": {first: {"index": 9}}},
            {"shots": {first: {"reuse_of": "asset_x"}}},
            {"shots": {"scene_99_shot_01": {"visual_query": "x"}}},
            {"scenes": {"scene_99": {"visual_intent": "x"}}},
            {"scenes": {"scene_01": {"duration_seconds": 1.0}}},
        ):
            with self.assertRaises(PlanningError):
                apply_overrides(self.scene_plan, self.shot_plan, self.assets, bad,
                                script=self.script)

    def test_is_deterministic(self):
        overrides = {"shots": {self.shot_plan.shots[2].shot_id: {"purpose": "novo proposito"}}}
        a = apply_overrides(self.scene_plan, self.shot_plan, self.assets, overrides, script=self.script)
        b = apply_overrides(self.scene_plan, self.shot_plan, self.assets, overrides, script=self.script)
        self.assertEqual(
            tuple(d.to_json() for d in a), tuple(d.to_json() for d in b)
        )


class ShotPlanToEditPlanTests(unittest.TestCase):
    def setUp(self):
        self.script = _script_for_shots()
        self.scene_plan = plan_scenes(self.script)
        self.shot_plan, self.assets = plan_shots(self.scene_plan, orientation="landscape")
        self.target = TargetFormat(1920, 1080, "cover")

    def _bindings(self, tmp: Path):
        bindings = {}
        for i, req in enumerate(self.assets.requirements):
            suffix = ".mp4" if req.type == "video" else ".jpg"
            path = tmp / f"{req.asset_id}{suffix}"
            path.write_bytes(b"x")
            bindings[req.asset_id] = str(path)
        return bindings

    def test_one_operation_per_shot_and_extras_appended(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            bindings = self._bindings(tmp)
            extra = (
                __import__("video_generator.domain", fromlist=["EditOperation"]).EditOperation(
                    operation_id="narration",
                    kind="narration",
                    parameters={"duration_policy": "match_timeline", "text": "oi"},
                ),
            )
            plan = shot_plan_to_edit_plan(
                self.shot_plan,
                bindings,
                plan_id="desu-plan",
                brief_id="desu-brief",
                output_path=str(tmp / "out.mp4"),
                target_format=self.target,
                extra_operations=extra,
            )
            self.assertIsInstance(plan, EditPlan)
            self.assertEqual(plan.target_format, self.target)
            timeline = [
                op for op in plan.operations if op.kind in ("image_clip", "sequence_clip")
            ]
            self.assertEqual(len(timeline), len(self.shot_plan.shots))
            self.assertEqual(plan.operations[-1].kind, "narration")
            # round-trips through the existing contract
            self.assertEqual(EditPlan.from_dict(json.loads(plan.to_json())), plan)

    def test_image_and_video_operation_shapes_match_the_workflow(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            bindings = self._bindings(tmp)
            plan = shot_plan_to_edit_plan(
                self.shot_plan, bindings, plan_id="p", brief_id="b",
                output_path=str(tmp / "out.mp4"), target_format=self.target,
            )
            shot_by_op = {s.shot_id: s for s in self.shot_plan.shots}
            for op in plan.operations:
                if op.kind == "image_clip":
                    self.assertIsNone(op.start_seconds)
                    self.assertIsNone(op.end_seconds)
                    self.assertLessEqual(
                        set(op.parameters), {"duration_seconds", "fit", "motion"}
                    )
                    self.assertAlmostEqual(
                        op.parameters["duration_seconds"],
                        shot_by_op[op.operation_id].duration_seconds,
                        places=6,
                    )
                    if "motion" in op.parameters:
                        self.assertIn(
                            op.parameters["motion"],
                            ("zoom_in", "zoom_out", "pan_left", "pan_right", "pan_up", "pan_down"),
                        )
                elif op.kind == "sequence_clip":
                    self.assertIsNotNone(op.start_seconds)
                    self.assertAlmostEqual(
                        op.end_seconds - op.start_seconds,
                        shot_by_op[op.operation_id].duration_seconds,
                        places=6,
                    )
                    self.assertLessEqual(set(op.parameters), {"fit"})

    def test_reused_asset_shares_source_with_different_framing(self):
        import tempfile

        reused = [s for s in self.shot_plan.shots if s.reuse_of]
        self.assertTrue(reused, "expected the plan to contain a reuse")
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            bindings = self._bindings(tmp)
            plan = shot_plan_to_edit_plan(
                self.shot_plan, bindings, plan_id="p", brief_id="b",
                output_path=str(tmp / "out.mp4"), target_format=self.target,
            )
            ops = {op.operation_id: op for op in plan.operations}
            for shot in reused:
                first_user = next(
                    s for s in self.shot_plan.shots if s.asset_id == shot.asset_id
                )
                self.assertEqual(ops[shot.shot_id].source, ops[first_user.shot_id].source)

    def test_missing_binding_is_rejected(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            bindings = self._bindings(tmp)
            bindings.pop(next(iter(bindings)))
            with self.assertRaises(PlanningError):
                shot_plan_to_edit_plan(
                    self.shot_plan, bindings, plan_id="p", brief_id="b",
                    output_path=str(tmp / "out.mp4"), target_format=self.target,
                )


class DesumanizandoAcceptanceTests(unittest.TestCase):
    def test_full_pipeline_meets_the_editorial_bar(self):
        script = NarrativeScript.from_text(
            "desumanizando-01", ROTEIRO.read_text(encoding="utf-8"),
            total_duration_seconds=DESUMANIZANDO_TOTAL_SECONDS,
        )
        scene_plan = plan_scenes(script)
        shot_plan, assets = plan_shots(scene_plan, orientation="landscape")
        shot_plan.validate_against(scene_plan)
        assets.validate_against(shot_plan)

        self.assertGreaterEqual(len(scene_plan.scenes), 8)
        self.assertGreaterEqual(len(shot_plan.shots), 50)

        durs = [s.duration_seconds for s in shot_plan.shots]
        self.assertTrue(all(d <= 10.0 for d in durs))
        in_band = sum(1 for d in durs if 2.0 <= d <= 6.0) / len(durs)
        self.assertGreaterEqual(in_band, 0.6)

        types = {}
        for s in shot_plan.shots:
            types[s.shot_type] = types.get(s.shot_type, 0) + 1
        self.assertGreaterEqual(len(types), 5)
        self.assertLess(max(types.values()) / len(shot_plan.shots), 0.4)

        used = set()
        for req in assets.requirements:
            used |= set(req.used_by)
        self.assertEqual(used, {s.shot_id for s in shot_plan.shots})

        # byte-for-byte reproducible from script + policy + seed
        again_scene = plan_scenes(script)
        again_shot, again_assets = plan_shots(again_scene, orientation="landscape")
        self.assertEqual(again_shot.to_json(), shot_plan.to_json())
        self.assertEqual(again_assets.to_json(), assets.to_json())


class SchemaAgreementTests(unittest.TestCase):
    """The generated JSON documents agree with the public schemas (structural
    key-set check; the project keeps tests stdlib-only, so this is not a full
    JSON Schema validation)."""

    SCHEMAS = REPO_ROOT / "schemas"

    def _check(self, schema_name, data):
        schema = json.loads((self.SCHEMAS / schema_name).read_text(encoding="utf-8"))

        def check_object(node, spec):
            props = spec.get("properties", {})
            required = set(spec.get("required", []))
            self.assertTrue(required <= set(node), f"{schema_name}: missing {required - set(node)}")
            if spec.get("additionalProperties") is False:
                self.assertTrue(
                    set(node) <= set(props),
                    f"{schema_name}: unexpected {set(node) - set(props)}",
                )
            for key, value in node.items():
                sub = props.get(key)
                if not isinstance(sub, dict):
                    continue
                if sub.get("type") == "object" and isinstance(value, dict) and "properties" in sub:
                    check_object(value, sub)
                if sub.get("type") == "array" and isinstance(value, list) and value:
                    item_spec = sub.get("items", {})
                    if item_spec.get("type") == "object":
                        for item in value:
                            check_object(item, item_spec)

        check_object(data, schema)

    def test_generated_documents_match_their_schemas(self):
        script = NarrativeScript.from_text(
            "desumanizando-01", ROTEIRO.read_text(encoding="utf-8"),
            total_duration_seconds=DESUMANIZANDO_TOTAL_SECONDS,
        )
        scene_plan = plan_scenes(script)
        shot_plan, assets = plan_shots(scene_plan, orientation="landscape")
        self._check("narrative-script-v1.schema.json", json.loads(script.to_json()))
        self._check("scene-plan-v1.schema.json", json.loads(scene_plan.to_json()))
        self._check("shot-plan-v1.schema.json", json.loads(shot_plan.to_json()))
        self._check("asset-requirements-v1.schema.json", json.loads(assets.to_json()))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
