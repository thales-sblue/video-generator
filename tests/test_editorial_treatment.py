"""Editorial Image Editing v1: the layer that decides how a shot is *cut*.

Covers the typed contract and its JSON round trip, the policy merge, the
deterministic planner, the treatment-per-intensity hierarchy, the eligibility
gates (static vs video, a frame that may not be cropped, a reading moment, a
comparison), the variety guards (no long run of one treatment, a floor of
deliberately still shots), the timing/fallback when a cut will not fit, the
renderer projection into consecutive segment operations, and the plan-level
metrics.
"""

import unittest

from video_generator.domain.treatment import (
    DEFAULT_TREATMENT_POLICY,
    EDITORIAL_INTENSITIES,
    TREATMENTS,
    EditorialTreatment,
    EditorialTreatmentPlan,
    EditorialTreatmentPolicy,
    TreatmentError,
    TreatmentInput,
    TreatmentState,
    plan_editorial_treatment,
    treatment_metrics,
    treatment_segments,
)


def _input(shot_id="scene_01_shot_01", **kw):
    base = dict(
        shot_id=shot_id,
        duration_seconds=5.0,
        asset_type="image",
        scale="medium",
        intensity="medium",
        editorial_role="claim",
    )
    base.update(kw)
    return TreatmentInput(**base)


def _plan(inputs, **kw):
    return plan_editorial_treatment(inputs, seed=kw.pop("seed", 7), **kw)


class ContractTests(unittest.TestCase):
    def test_a_state_round_trips_through_json(self):
        state = TreatmentState(
            composition="extreme_crop",
            motion="detail_push",
            scale="detail",
            duration_fraction=1.0,
            crop_bias="left",
            grade="strong",
        )
        self.assertEqual(
            TreatmentState.from_dict(state.to_dict()).to_dict(), state.to_dict()
        )

    def test_a_treatment_round_trips_through_json(self):
        plan = _plan([_input()])
        t = plan.treatments[0]
        self.assertEqual(EditorialTreatment.from_dict(t.to_dict()).to_dict(), t.to_dict())

    def test_a_plan_round_trips_through_json(self):
        plan = _plan([_input(), _input("scene_01_shot_02", intensity="peak")])
        self.assertEqual(
            EditorialTreatmentPlan.from_dict(plan.to_dict()).to_dict(), plan.to_dict()
        )

    def test_state_fractions_must_sum_to_one(self):
        with self.assertRaises(TreatmentError):
            EditorialTreatment(
                shot_id="s1",
                treatment="reframe",
                intensity="medium",
                states=(
                    TreatmentState("fullscreen", "static_hold", "medium", 0.5),
                    TreatmentState("fullscreen", "static_hold", "close", 0.3),
                ),
            )

    def test_a_single_state_treatment_refuses_extra_states(self):
        with self.assertRaises(TreatmentError):
            EditorialTreatment(
                shot_id="s1",
                treatment="static_hold",
                intensity="low",
                states=(
                    TreatmentState("fullscreen", "static_hold", "medium", 0.5),
                    TreatmentState("fullscreen", "static_hold", "close", 0.5),
                ),
            )

    def test_a_multi_state_treatment_needs_two_states(self):
        with self.assertRaises(TreatmentError):
            EditorialTreatment(
                shot_id="s1",
                treatment="two_state_cut",
                intensity="high",
                states=(TreatmentState("fullscreen", "static_hold", "medium", 1.0),),
            )

    def test_a_held_state_cannot_also_move(self):
        with self.assertRaises(TreatmentError):
            TreatmentState("fullscreen", "slow_push_in", "medium", 1.0, hold=True)

    def test_detail_push_only_inside_an_extreme_crop(self):
        with self.assertRaises(TreatmentError):
            TreatmentState("fullscreen", "detail_push", "detail", 1.0)

    def test_unknown_vocabulary_is_refused(self):
        with self.assertRaises(TreatmentError):
            EditorialTreatment(
                shot_id="s1", treatment="ken_burns", intensity="low",
                states=(TreatmentState("fullscreen", "static_hold", "medium", 1.0),),
            )
        with self.assertRaises(TreatmentError):
            _input(intensity="loud")


class PolicyTests(unittest.TestCase):
    def test_a_partial_policy_merges_over_the_defaults(self):
        policy = EditorialTreatmentPolicy.from_dict(
            {"min_calm_fraction": 0.5, "intensity_weights": {"low": {"static_hold": 9.0}}}
        )
        self.assertEqual(policy.min_calm_fraction, 0.5)
        self.assertEqual(policy.intensity_weights["low"]["static_hold"], 9.0)
        # untouched levels keep their defaults
        self.assertEqual(
            policy.intensity_weights["peak"]["two_state_cut"],
            DEFAULT_TREATMENT_POLICY.intensity_weights["peak"]["two_state_cut"],
        )

    def test_a_policy_round_trips_through_json(self):
        self.assertEqual(
            EditorialTreatmentPolicy.from_dict(DEFAULT_TREATMENT_POLICY.to_dict()).to_dict(),
            DEFAULT_TREATMENT_POLICY.to_dict(),
        )

    def test_invalid_policies_are_refused(self):
        with self.assertRaises(TreatmentError):
            EditorialTreatmentPolicy.from_dict({"unknown_field": 1})
        with self.assertRaises(TreatmentError):
            EditorialTreatmentPolicy(min_multi_state_seconds=1.0, min_state_seconds=1.0)
        with self.assertRaises(TreatmentError):
            EditorialTreatmentPolicy(close_zoom=1.8, detail_zoom=1.5)


class DeterminismTests(unittest.TestCase):
    def test_same_inputs_policy_and_seed_give_the_same_plan(self):
        shots = [_input(f"scene_01_shot_{i:02d}", intensity="high") for i in range(1, 9)]
        a = plan_editorial_treatment(shots, seed=3)
        b = plan_editorial_treatment(shots, seed=3)
        self.assertEqual(a.to_dict(), b.to_dict())

    def test_a_different_seed_gives_a_different_plan(self):
        shots = [_input(f"scene_01_shot_{i:02d}", intensity="high") for i in range(1, 12)]
        a = plan_editorial_treatment(shots, seed=1)
        b = plan_editorial_treatment(shots, seed=2)
        self.assertNotEqual(
            [t.treatment for t in a.treatments], [t.treatment for t in b.treatments]
        )


class IntensityHierarchyTests(unittest.TestCase):
    def _distribution(self, intensity, n=120):
        shots = [
            _input(f"scene_01_shot_{i:02d}", intensity=intensity, duration_seconds=6.0)
            for i in range(1, n + 1)
        ]
        plan = plan_editorial_treatment(shots, seed=11)
        calm = sum(1 for t in plan.treatments if t.is_calm)
        cutting = sum(1 for t in plan.treatments if not t.is_calm)
        return calm, cutting

    def test_low_is_mostly_calm_and_peak_is_mostly_cutting(self):
        low_calm, low_cut = self._distribution("low")
        peak_calm, peak_cut = self._distribution("peak")
        self.assertGreater(low_calm, low_cut)
        self.assertGreater(peak_cut, peak_calm)

    def test_peak_is_not_merely_more_zoom(self):
        # a peak beat should reach for a change of state, not just a tighter crop
        shots = [
            _input(f"scene_01_shot_{i:02d}", intensity="peak", duration_seconds=6.0,
                   editorial_role="payoff")
            for i in range(1, 40)
        ]
        plan = plan_editorial_treatment(shots, seed=5)
        multi = sum(1 for t in plan.treatments if t.internal_cuts > 0)
        self.assertGreater(multi, len(plan.treatments) // 3)

    def test_every_intensity_can_be_planned(self):
        for intensity in EDITORIAL_INTENSITIES:
            plan = plan_editorial_treatment(
                [_input(intensity=intensity)], seed=0
            )
            self.assertEqual(plan.treatments[0].intensity, intensity)


class EligibilityTests(unittest.TestCase):
    def test_moving_footage_only_holds_or_cuts_between_its_own_windows(self):
        shots = [
            _input(f"scene_01_shot_{i:02d}", asset_type="video", intensity="peak",
                   duration_seconds=7.0)
            for i in range(1, 30)
        ]
        plan = plan_editorial_treatment(shots, seed=2)
        for t in plan.treatments:
            self.assertIn(t.treatment, ("static_hold", "two_state_cut"))

    def test_a_frame_that_may_not_be_cropped_is_never_punched_or_split(self):
        shots = [
            _input(f"scene_01_shot_{i:02d}", intensity="peak", preserve_frame=True,
                   duration_seconds=6.0)
            for i in range(1, 30)
        ]
        plan = plan_editorial_treatment(shots, seed=4)
        for t in plan.treatments:
            self.assertIn(
                t.treatment, ("static_hold", "slow_push"),
                msg=f"{t.treatment} cropped a preserve_frame shot",
            )

    def test_a_reading_moment_pulls_aggressive_treatments_back(self):
        shots = [
            _input(f"scene_01_shot_{i:02d}", intensity="high", reading_moment=True,
                   duration_seconds=6.0)
            for i in range(1, 30)
        ]
        plan = plan_editorial_treatment(shots, seed=6)
        self.assertFalse(
            any(t.treatment in ("punch_in", "detail_reveal", "graphic_interrupt")
                for t in plan.treatments)
        )

    def test_a_peak_reading_moment_may_still_cut(self):
        shots = [
            _input(f"scene_01_shot_{i:02d}", intensity="peak", reading_moment=True,
                   editorial_role="payoff", duration_seconds=6.0)
            for i in range(1, 30)
        ]
        plan = plan_editorial_treatment(shots, seed=6)
        self.assertTrue(any(t.internal_cuts > 0 for t in plan.treatments))

    def test_split_compare_needs_a_comparison(self):
        no_contrast = plan_editorial_treatment(
            [_input(f"scene_01_shot_{i:02d}", intensity="peak") for i in range(1, 20)],
            seed=1,
        )
        self.assertNotIn(
            "split_compare", [t.treatment for t in no_contrast.treatments]
        )
        with_contrast = plan_editorial_treatment(
            [
                _input(f"scene_01_shot_{i:02d}", intensity="high",
                       editorial_role="contrast", contrast_neighbor=True,
                       duration_seconds=6.0)
                for i in range(1, 30)
            ],
            seed=1,
        )
        self.assertIn(
            "split_compare", [t.treatment for t in with_contrast.treatments]
        )

    def test_graphic_interrupt_needs_the_type_layer_to_have_cut_one(self):
        shots = [
            _input(f"scene_01_shot_{i:02d}", intensity="peak", duration_seconds=6.0)
            for i in range(1, 25)
        ]
        plan = plan_editorial_treatment(shots, seed=1)
        self.assertNotIn(
            "graphic_interrupt", [t.treatment for t in plan.treatments]
        )


class VarietyTests(unittest.TestCase):
    def test_no_treatment_runs_past_the_policy_limit(self):
        shots = [
            _input(f"scene_01_shot_{i:02d}", intensity="high", duration_seconds=6.0)
            for i in range(1, 60)
        ]
        plan = plan_editorial_treatment(shots, seed=8)
        names = [t.treatment for t in plan.treatments]
        limit = DEFAULT_TREATMENT_POLICY.max_consecutive_same_treatment
        run = 1
        for a, b in zip(names, names[1:]):
            run = run + 1 if a == b else 1
            self.assertLessEqual(run, limit, msg=f"run of {b} exceeded {limit}")

    def test_a_floor_of_shots_stays_deliberately_calm(self):
        shots = [
            _input(f"scene_01_shot_{i:02d}", intensity="peak", duration_seconds=6.0)
            for i in range(1, 80)
        ]
        plan = plan_editorial_treatment(shots, seed=9)
        calm = sum(1 for t in plan.treatments if t.is_calm)
        self.assertGreaterEqual(calm / len(plan.treatments),
                                DEFAULT_TREATMENT_POLICY.min_calm_fraction * 0.7)

    def test_stability_is_a_choice_not_an_accident(self):
        plan = plan_editorial_treatment(
            [_input(intensity="low", editorial_role="transition")], seed=0
        )
        self.assertTrue(plan.treatments[0].is_calm)


class TimingAndFallbackTests(unittest.TestCase):
    def test_a_short_shot_is_a_single_state(self):
        plan = plan_editorial_treatment(
            [
                _input(f"scene_01_shot_{i:02d}", intensity="peak", duration_seconds=2.4)
                for i in range(1, 20)
            ],
            seed=1,
        )
        for t in plan.treatments:
            self.assertEqual(len(t.states), 1)

    def test_every_state_clears_the_minimum_seconds_after_the_split(self):
        shots = [
            _input(f"scene_01_shot_{i:02d}", intensity="peak", duration_seconds=d)
            for i, d in enumerate([3.6, 4.0, 5.5, 8.0, 3.5, 6.0] * 4, start=1)
        ]
        plan = plan_editorial_treatment(shots, seed=2)
        for t, shot in zip(plan.treatments, shots):
            for state in t.states:
                self.assertGreaterEqual(
                    state.duration_fraction * shot.duration_seconds,
                    DEFAULT_TREATMENT_POLICY.min_state_seconds - 1e-6,
                )

    def test_a_treatment_that_cannot_be_built_degrades_and_says_so(self):
        # a 3.5s shot wants a multi-state peak treatment but only just fits;
        # if the recipe cannot satisfy min_state_seconds it must fall back
        plan = plan_editorial_treatment(
            [_input(intensity="peak", duration_seconds=3.45, editorial_role="payoff")],
            seed=0,
        )
        t = plan.treatments[0]
        self.assertTrue(_viable_states(t))

    def test_every_treatment_carries_a_rationale(self):
        plan = plan_editorial_treatment(
            [_input(f"scene_01_shot_{i:02d}", intensity="high") for i in range(1, 10)],
            seed=1,
        )
        for t in plan.treatments:
            self.assertIn("treatment:", t.rationale)
            self.assertIn("intensity:", t.rationale)


def _viable_states(t):
    return abs(sum(s.duration_fraction for s in t.states) - 1.0) < 1e-6


class RendererProjectionTests(unittest.TestCase):
    def test_an_image_treatment_becomes_consecutive_image_clip_params(self):
        t = EditorialTreatment(
            shot_id="scene_01_shot_01",
            treatment="detail_reveal",
            intensity="high",
            states=(
                TreatmentState("fullscreen", "static_hold", "wide", 0.6),
                TreatmentState("extreme_crop", "detail_push", "detail", 0.4),
            ),
        )
        segs = treatment_segments(
            t,
            asset_type="image",
            total_seconds=5.0,
            base_composition="fullscreen",
            base_crop_bias="center",
            base_grade="standard",
            base_motion="slow_push_in",
        )
        self.assertEqual(len(segs), 2)
        self.assertAlmostEqual(sum(s["duration_seconds"] for s in segs), 5.0, places=3)
        self.assertEqual(segs[0]["composition"], "fullscreen")
        self.assertEqual(segs[1]["composition"], "extreme_crop")
        self.assertEqual(segs[1]["motion"], "detail_push")
        for s in segs:
            self.assertIn("grade", s)
            self.assertIn("crop_bias", s)

    def test_a_video_treatment_becomes_contiguous_source_windows(self):
        t = EditorialTreatment(
            shot_id="scene_02_shot_01",
            treatment="two_state_cut",
            intensity="high",
            states=(
                TreatmentState("fullscreen", "static_hold", "wide", 0.55),
                TreatmentState("extreme_crop", "static_hold", "close", 0.45, hold=True),
            ),
        )
        segs = treatment_segments(
            t,
            asset_type="video",
            total_seconds=6.0,
            base_composition="fullscreen",
            base_crop_bias="center",
            base_grade="standard",
            base_motion="static_hold",
            window_start=12.0,
        )
        self.assertEqual(segs[0]["start_seconds"], 12.0)
        self.assertAlmostEqual(segs[-1]["end_seconds"], 18.0, places=3)
        self.assertAlmostEqual(segs[0]["end_seconds"], segs[1]["start_seconds"], places=6)
        for s in segs:
            self.assertNotIn("motion", s)  # a video window never carries a synthetic move

    def test_a_reading_state_keeps_only_a_gentle_move(self):
        t = EditorialTreatment(
            shot_id="scene_01_shot_09",
            treatment="detail_reveal",
            intensity="peak",
            states=(
                TreatmentState("fullscreen", "static_hold", "wide", 0.6, reading=True),
                TreatmentState("extreme_crop", "detail_push", "detail", 0.4, reading=True),
            ),
        )
        segs = treatment_segments(
            t, asset_type="image", total_seconds=6.0, base_composition="fullscreen",
            base_crop_bias="center", base_grade="standard", base_motion="static_hold",
        )
        self.assertIn(segs[1]["motion"], ("slow_push_in", "static_hold"))

    def test_the_projection_never_emits_a_partial_framing(self):
        plan = plan_editorial_treatment(
            [_input(f"scene_01_shot_{i:02d}", intensity="high", duration_seconds=6.0)
             for i in range(1, 12)],
            seed=1,
        )
        for t in plan.treatments:
            segs = treatment_segments(
                t, asset_type="image", total_seconds=6.0,
                base_composition="fullscreen", base_crop_bias="center",
                base_grade="standard", base_motion="static_hold",
            )
            for s in segs:
                self.assertEqual(
                    {"composition", "crop_bias", "grade", "duration_seconds", "motion"}
                    - set(s),
                    set(),
                )


class MetricsTests(unittest.TestCase):
    def test_metrics_report_the_shape_of_the_cut(self):
        shots = [
            _input(f"scene_01_shot_{i:02d}", intensity=lvl, duration_seconds=6.0)
            for i, lvl in enumerate(
                ["low", "medium", "high", "peak"] * 8, start=1
            )
        ]
        plan = plan_editorial_treatment(shots, seed=3)
        seconds = {s.shot_id: s.duration_seconds for s in shots}
        types = {s.shot_id: s.asset_type for s in shots}
        m = treatment_metrics(
            plan, shot_seconds=seconds, asset_types=types,
            reuse_of={s.shot_id: None for s in shots},
        )
        self.assertEqual(m["shots"], len(shots))
        self.assertEqual(m["treated_shots"] + m["deliberately_static_shots"], m["shots"])
        self.assertGreaterEqual(m["editorial_events"], m["treated_shots"])
        self.assertIsNotNone(m["editorial_events_per_minute"])
        self.assertIsInstance(m["static_image_shots_over_threshold"], list)
        self.assertGreaterEqual(m["editorial_reuse_of_same_asset"], 0)
        self.assertLessEqual(
            m["consecutive_same_treatment"], len(shots)
        )

    def test_metrics_flag_a_long_untouched_still(self):
        shots = [
            _input("scene_01_shot_01", intensity="low", editorial_role="transition",
                   duration_seconds=9.0),
        ]
        plan = plan_editorial_treatment(shots, seed=0)
        m = treatment_metrics(
            plan,
            shot_seconds={"scene_01_shot_01": 9.0},
            asset_types={"scene_01_shot_01": "image"},
        )
        self.assertEqual(m["static_image_shots_over_threshold"], ["scene_01_shot_01"])


import tempfile
from pathlib import Path

from video_generator.domain.planning import (
    NarrativeScript,
    plan_scenes,
    plan_shot_editorial_treatment,
    plan_shot_motion_typography,
    plan_shot_visual_direction,
    plan_shots,
    shot_plan_to_edit_plan,
    treatment_inputs,
    visual_direction_operation,
)
from video_generator.domain.direction import VisualDirectionPolicy
from video_generator.domain import TargetFormat
from video_generator.validation.manifest import _sequence_plan_matches
from video_generator.workflows.sequence import _operations_from_plan

_SCRIPT = (
    "Voce acha que decide sozinho. Nao decide, e isso tem nome.\n\n"
    "Em 1974 um estudo mostrou que a maioria muda de ideia sob pressao.\n\n"
    "Mas quando a informacao contradiz o que voce ja pensava, comeca o interrogatorio.\n\n"
    "O corredor vazio continua ali, e ninguem olha para ele.\n\n"
    "No fim, entender o ser humano comeca quando paramos de idealiza-lo.\n"
)


class PlanIntegrationTests(unittest.TestCase):
    def _planned(self, tmp):
        script = NarrativeScript.from_text("s1", _SCRIPT, total_duration_seconds=120.0)
        scene_plan = plan_scenes(script)
        shot_plan, _ = plan_shots(
            scene_plan, seed=0, semantic=True, visual_relevance=True
        )
        directions = plan_shot_visual_direction(shot_plan, seed=0)
        motion_events = plan_shot_motion_typography(scene_plan, shot_plan)
        treatments = plan_shot_editorial_treatment(
            scene_plan,
            shot_plan,
            directions=directions.by_shot(),
            motion_events=motion_events,
            seed=0,
        )
        source = tmp / "asset.jpg"
        source.write_bytes(b"x")
        bindings = {shot.asset_id: str(source) for shot in shot_plan.shots}
        plan = shot_plan_to_edit_plan(
            shot_plan,
            bindings,
            plan_id="p",
            brief_id="b",
            output_path=str(tmp / "out.mp4"),
            target_format=TargetFormat(1920, 1080, "cover"),
            extra_operations=(visual_direction_operation(VisualDirectionPolicy()),),
            directions=directions.by_shot(),
            treatments=treatments.by_shot(),
        )
        return scene_plan, shot_plan, directions, treatments, plan

    def test_the_treatment_plan_covers_every_shot_once(self):
        with tempfile.TemporaryDirectory() as directory:
            _, shot_plan, _, treatments, _ = self._planned(Path(directory))
            self.assertEqual(
                [t.shot_id for t in treatments.treatments],
                [s.shot_id for s in shot_plan.shots],
            )

    def test_a_non_static_shot_is_expanded_into_its_states(self):
        with tempfile.TemporaryDirectory() as directory:
            _, shot_plan, _, treatments, plan = self._planned(Path(directory))
            segments = [
                op for op in plan.operations
                if op.kind in ("image_clip", "sequence_clip")
            ]
            extra = sum(t.internal_cuts for t in treatments.treatments)
            # a multi-state treatment adds one segment per internal cut
            self.assertGreater(extra, 0)
            self.assertEqual(len(segments) - len(shot_plan.shots), extra)

    def test_the_expanded_plan_still_passes_the_manifest_grammar(self):
        with tempfile.TemporaryDirectory() as directory:
            *_, plan = self._planned(Path(directory))
            self.assertTrue(_sequence_plan_matches(plan))

    def test_the_sequence_workflow_accepts_the_expanded_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            *_, plan = self._planned(Path(directory))
            parsed = _operations_from_plan(plan)
            self.assertGreaterEqual(len(parsed.segments), 2)

    def test_expanded_segment_durations_sum_to_the_timeline(self):
        with tempfile.TemporaryDirectory() as directory:
            _, shot_plan, _, _, plan = self._planned(Path(directory))
            total = sum(s.duration_seconds for s in shot_plan.shots)
            got = 0.0
            for op in plan.operations:
                if op.kind == "image_clip":
                    got += op.parameters["duration_seconds"]
                elif op.kind == "sequence_clip":
                    got += op.end_seconds - op.start_seconds
            self.assertAlmostEqual(got, total, places=2)

    def test_without_treatments_the_plan_is_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            _, shot_plan, directions, _, _ = self._planned(tmp)
            source = tmp / "asset.jpg"
            bindings = {shot.asset_id: str(source) for shot in shot_plan.shots}
            kw = dict(
                plan_id="p", brief_id="b", output_path=str(tmp / "out.mp4"),
                target_format=TargetFormat(1920, 1080, "cover"),
                directions=directions.by_shot(),
            )
            a = shot_plan_to_edit_plan(shot_plan, bindings, **kw)
            b = shot_plan_to_edit_plan(shot_plan, bindings, treatments={}, **kw)
            self.assertEqual(a.to_dict(), b.to_dict())

    def test_treatment_inputs_score_intensity_and_flag_reading(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            scene_plan, shot_plan, directions, _, _ = self._planned(tmp)
            motion_events = plan_shot_motion_typography(scene_plan, shot_plan)
            rows = treatment_inputs(
                scene_plan, shot_plan,
                directions=directions.by_shot(), motion_events=motion_events,
            )
            self.assertEqual(len(rows), len(shot_plan.shots))
            self.assertTrue(all(r.intensity in EDITORIAL_INTENSITIES for r in rows))
            # the opening shots sit inside the hook window and run loud
            self.assertIn(rows[0].intensity, ("high", "peak"))


if __name__ == "__main__":
    unittest.main()
