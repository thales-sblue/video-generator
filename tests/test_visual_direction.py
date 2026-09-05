"""Visual Direction v1: the layer that decides *how* a chosen asset appears.

Covers the contract, the policy, the deterministic planner, the four filter
grammars the adapter builds from it, the emphasis layer's density and accent,
the hard editorial veto, and the two defects this cycle fixed — an override
that used to switch the semantic layer off for the whole plan, and a
``text_events`` operation the manifest validator did not know.
"""

import json
import tempfile
import unittest
from pathlib import Path

from video_generator.adapters.ffmpeg import (
    COMPOSITIONS as ADAPTER_COMPOSITIONS,
    DEFAULT_DIRECTION_SPEC,
    DIRECTION_MOTIONS as ADAPTER_MOTIONS,
    DirectionSpec,
    FFmpegError,
    SequenceClip,
    SequenceImage,
    TextEventCue,
    TextStyleSpec,
    _composition_graph,
    _direction_motion_filter,
    _event_text,
    _grade_chain,
)
from video_generator.domain import (
    EditOperation,
    EditPlan,
    TargetFormat,
)
from video_generator.domain.direction import (
    COMPOSITIONS,
    GRADE_INTENSITIES,
    MOTIONS,
    DirectionError,
    DirectionInput,
    GradePolicy,
    VisualDirection,
    VisualDirectionPlan,
    VisualDirectionPolicy,
    plan_visual_direction,
)
from video_generator.domain.editorial import (
    DEFAULT_EDITORIAL_POLICY,
    EditorialPolicy,
    HookPolicy,
    NarrationBeat,
    TextEvent,
    plan_text_events,
)
from video_generator.domain.planning import (
    NarrativeScript,
    apply_overrides,
    plan_scenes,
    plan_shot_visual_direction,
    plan_shots,
    shot_plan_to_edit_plan,
    visual_direction_operation,
)
from video_generator.domain.relevance import (
    DEFAULT_RELEVANCE_POLICY,
    HARD_VETO_REASONS,
    REJECTION_REASONS,
    RelevancePolicy,
    assess_candidate,
    hard_veto_reasons,
    refine_query,
)
from video_generator.validation.manifest import _sequence_plan_matches
from video_generator.workflows.sequence import (
    SequenceWorkflowError,
    _direction_spec,
    _operations_from_plan,
    _segment_direction,
)

SCRIPT = (
    "Voce acha que decide sozinho. Nao decide, e isso tem nome.\n\n"
    "Em 1974 um estudo mostrou que a maioria muda de ideia sob pressao.\n\n"
    "Mas quando a informacao contradiz, comeca o interrogatorio.\n\n"
    "O corredor vazio continua ali, e ninguem olha para ele.\n\n"
    "No fim, entender o ser humano comeca quando paramos de idealiza-lo.\n"
)


def _input(shot_id="scene_01_shot_01", **kwargs):
    values = dict(
        shot_id=shot_id,
        duration_seconds=3.0,
        asset_type="image",
        scale="wide",
        visual_role="build_tension",
        visual_intent_class="tension_or_suspense",
        text="empty corridor at night",
    )
    values.update(kwargs)
    return DirectionInput(**values)


def _inputs(count, **kwargs):
    return [
        _input(shot_id=f"scene_01_shot_{index:02d}", **kwargs)
        for index in range(1, count + 1)
    ]


# --------------------------------------------------------------------------- #
# 1. the contract
# --------------------------------------------------------------------------- #
class VisualDirectionContractTests(unittest.TestCase):
    def test_a_direction_round_trips_through_json(self):
        direction = VisualDirection(
            shot_id="scene_04_shot_02",
            composition="extreme_crop",
            motion="detail_push",
            grade="standard",
            emphasis=False,
            crop_bias="left",
            visual_motif="corridor",
            visual_role="build_tension",
            visual_intent_class="tension_or_suspense",
            rationale="role:build_tension",
        )
        again = VisualDirection.from_dict(
            json.loads(json.dumps(direction.to_dict()))
        )
        self.assertEqual(again, direction)

    def test_every_vocabulary_value_is_rejected_when_unknown(self):
        for field, value in (
            ("composition", "kenburns"),
            ("motion", "zoom_in"),
            ("grade", "extreme"),
            ("crop_bias", "diagonal"),
            ("text_zone", "middle_left"),
            ("visual_role", "sell"),
            ("visual_intent_class", "vibes"),
        ):
            with self.subTest(field=field):
                kwargs = {
                    "shot_id": "scene_01_shot_01",
                    "composition": "fullscreen",
                    "motion": "static_hold",
                    "grade": "none",
                    field: value,
                }
                with self.assertRaises(DirectionError):
                    VisualDirection(**kwargs)

    def test_a_text_focus_composition_must_say_where_the_type_goes(self):
        with self.assertRaises(DirectionError):
            VisualDirection(
                shot_id="scene_01_shot_01",
                composition="text_focus",
                motion="static_hold",
                grade="none",
            )

    def test_a_detail_push_only_exists_inside_an_extreme_crop(self):
        with self.assertRaises(DirectionError):
            VisualDirection(
                shot_id="scene_01_shot_01",
                composition="fullscreen",
                motion="detail_push",
                grade="none",
            )

    def test_a_plan_round_trips_and_refuses_duplicate_shots(self):
        plan = plan_visual_direction(_inputs(4), seed=3)
        again = VisualDirectionPlan.from_dict(
            json.loads(json.dumps(plan.to_dict()))
        )
        self.assertEqual(
            [d.to_dict() for d in again.directions],
            [d.to_dict() for d in plan.directions],
        )
        with self.assertRaises(DirectionError):
            VisualDirectionPlan(
                plan_id="p",
                shot_plan_id="s",
                script_id="c",
                seed=0,
                policy=VisualDirectionPolicy(),
                directions=(plan.directions[0], plan.directions[0]),
            )


# --------------------------------------------------------------------------- #
# 2. the policy
# --------------------------------------------------------------------------- #
class VisualDirectionPolicyTests(unittest.TestCase):
    def test_a_partial_policy_merges_over_the_defaults(self):
        policy = VisualDirectionPolicy.from_dict(
            {
                "style_id": "channel-x",
                "accent": "#e5a33c",
                "grade": {"saturation": 0.5},
                "composition_weights": {"explain": {"inset": 9.0}},
            }
        )
        self.assertEqual(policy.style_id, "channel-x")
        self.assertEqual(policy.accent, "#E5A33C")
        self.assertEqual(policy.grade.saturation, 0.5)
        # untouched grade numbers and untouched roles keep their defaults
        self.assertEqual(policy.grade.grain, GradePolicy().grain)
        self.assertEqual(policy.composition_weights["explain"]["inset"], 9.0)
        self.assertEqual(
            policy.composition_weights["shock"],
            VisualDirectionPolicy().composition_weights["shock"],
        )

    def test_a_channel_policy_shaped_like_a_real_one_is_valid(self):
        """The shape a project file has to satisfy, kept as a fixture.

        The real ``projects/desumanizando_01/visual-direction-v1.json`` is not
        versioned (``projects/`` is ignored), so the suite must not depend on
        it: a clean clone has to be able to run this.
        """

        path = Path(__file__).resolve().parent / "fixtures" / "visual-direction-policy.json"
        policy = VisualDirectionPolicy.from_dict(
            json.loads(path.read_text(encoding="utf-8"))
        )
        self.assertEqual(policy.accent, "#E5A33C")
        self.assertEqual(set(policy.grade_intensities), set(GRADE_INTENSITIES))
        self.assertGreater(policy.grade.luminance_pull, 0.0)

    def test_the_project_policy_on_disk_is_valid_when_present(self):
        path = (
            Path(__file__).resolve().parents[1]
            / "projects"
            / "desumanizando_01"
            / "visual-direction-v1.json"
        )
        if not path.is_file():
            self.skipTest("project policies are not versioned; nothing to check here")
        policy = VisualDirectionPolicy.from_dict(
            json.loads(path.read_text(encoding="utf-8"))
        )
        self.assertEqual(policy.accent, "#E5A33C")

    def test_a_policy_round_trips_through_json(self):
        policy = VisualDirectionPolicy.from_dict(
            {"style_id": "x", "min_motion_seconds": 2.5}
        )
        again = VisualDirectionPolicy.from_dict(json.loads(policy.to_json()))
        self.assertEqual(again.to_dict(), policy.to_dict())

    def test_invalid_policies_are_refused(self):
        for payload, reason in (
            ({"accent": "orange"}, "not a hex colour"),
            ({"grade_intensities": {"none": 0.4, "subtle": 1, "standard": 1, "strong": 1}}, "none must be 0"),
            ({"dark_luma": 0.9, "bright_luma": 0.2}, "dark above bright"),
            ({"composition_weights": {"nope": {"inset": 1.0}}}, "unknown role"),
            ({"motion_weights": {"explain": {"zoom_in": 1.0}}}, "unknown motion"),
            ({"unknown_field": 1}, "unknown field"),
            ({"grade": {"saturation": 9.0}}, "out of range"),
        ):
            with self.subTest(reason=reason):
                with self.assertRaises(DirectionError):
                    VisualDirectionPolicy.from_dict(payload)

    def test_grade_intensity_follows_the_measured_brightness_of_the_asset(self):
        policy = VisualDirectionPolicy()
        self.assertEqual(policy.grade_for_luma(0.05)[0], "subtle")
        self.assertEqual(policy.grade_for_luma(0.40)[0], "standard")
        self.assertEqual(policy.grade_for_luma(0.90)[0], "strong")
        self.assertEqual(policy.grade_for_luma(None)[0], policy.default_grade)

    def test_a_motif_is_read_from_the_shot_s_own_words(self):
        policy = VisualDirectionPolicy()
        self.assertEqual(policy.motif_for("a long empty corridor"), "corridor")
        self.assertEqual(policy.motif_for("hands holding a pen"), "hands")
        self.assertIsNone(policy.motif_for("quiet afternoon"))


# --------------------------------------------------------------------------- #
# 3. the planner
# --------------------------------------------------------------------------- #
class VisualDirectionPlannerTests(unittest.TestCase):
    def test_the_same_inputs_policy_and_seed_give_the_same_plan(self):
        shots = _inputs(24)
        first = plan_visual_direction(shots, seed=7)
        second = plan_visual_direction(shots, seed=7)
        self.assertEqual(
            [d.to_dict() for d in first.directions],
            [d.to_dict() for d in second.directions],
        )

    def test_a_different_seed_gives_a_different_plan(self):
        shots = _inputs(24)
        first = plan_visual_direction(shots, seed=0)
        second = plan_visual_direction(shots, seed=1)
        self.assertNotEqual(
            [d.composition for d in first.directions],
            [d.composition for d in second.directions],
        )

    def test_no_composition_or_motion_runs_past_the_policy_limit(self):
        policy = VisualDirectionPolicy()
        plan = plan_visual_direction(_inputs(40), policy=policy, seed=2)
        for attribute, limit in (
            ("composition", policy.max_consecutive_same_composition),
            ("motion", policy.max_consecutive_same_motion),
        ):
            values = [getattr(d, attribute) for d in plan.directions]
            run = 1
            for previous, current in zip(values, values[1:]):
                run = run + 1 if previous == current else 1
                self.assertLessEqual(run, limit, f"{attribute} run of {run}")

    def test_moving_footage_never_gets_a_still_only_composition_or_a_move(self):
        plan = plan_visual_direction(
            _inputs(30, asset_type="video"), seed=5
        )
        for direction in plan.directions:
            self.assertNotIn(direction.composition, ("inset", "layered", "split"))
            self.assertEqual(direction.motion, "static_hold")

    def test_a_shot_too_short_for_a_move_is_held(self):
        plan = plan_visual_direction(_inputs(6, duration_seconds=0.9), seed=1)
        self.assertTrue(all(d.motion == "static_hold" for d in plan.directions))

    def test_text_focus_is_only_available_to_a_shot_carrying_emphasis(self):
        plain = plan_visual_direction(_inputs(30), seed=4)
        self.assertNotIn("text_focus", [d.composition for d in plain.directions])
        emphasised = plan_visual_direction(
            _inputs(30, emphasis=True, text_zone="middle"), seed=4
        )
        picked = [d for d in emphasised.directions if d.composition == "text_focus"]
        self.assertTrue(picked)
        self.assertTrue(all(d.text_zone == "middle" for d in picked))

    def test_split_needs_a_beat_that_is_actually_a_comparison(self):
        plain = plan_visual_direction(_inputs(30), seed=6)
        self.assertNotIn("split", [d.composition for d in plain.directions])
        contrast = plan_visual_direction(
            _inputs(30, editorial_role="contrast", visual_role="shock"), seed=6
        )
        self.assertIn("split", [d.composition for d in contrast.directions])

    def test_a_frame_that_may_not_be_cut_is_never_cropped(self):
        plan = plan_visual_direction(_inputs(30, preserve_frame=True), seed=8)
        compositions = {d.composition for d in plan.directions}
        self.assertNotIn("extreme_crop", compositions)
        self.assertNotIn("split", compositions)

    def test_the_planner_degrades_to_fullscreen_instead_of_failing(self):
        # every weight zeroed: there is nothing to draw from at all
        policy = VisualDirectionPolicy.from_dict(
            {
                "composition_weights": {
                    role: {name: 0.0 for name in COMPOSITIONS}
                    for role in VisualDirectionPolicy().composition_weights
                },
                "motion_weights": {
                    role: {name: 0.0 for name in MOTIONS}
                    for role in VisualDirectionPolicy().motion_weights
                },
            }
        )
        plan = plan_visual_direction(_inputs(4), policy=policy, seed=0)
        for direction in plan.directions:
            self.assertEqual(direction.composition, "fullscreen")
            self.assertEqual(direction.motion, "static_hold")

    def test_the_grade_of_each_shot_comes_from_its_own_measured_luma(self):
        shots = [
            _input("scene_01_shot_01", asset_luma=0.03),
            _input("scene_01_shot_02", asset_luma=0.40),
            _input("scene_01_shot_03", asset_luma=0.95),
            _input("scene_01_shot_04", asset_luma=None),
        ]
        plan = plan_visual_direction(shots, seed=0)
        self.assertEqual(
            [d.grade for d in plan.directions],
            ["subtle", "standard", "strong", "standard"],
        )

    def test_every_direction_carries_a_rationale_naming_its_reasons(self):
        plan = plan_visual_direction(_inputs(8), seed=0)
        for direction in plan.directions:
            self.assertIn("role:", direction.rationale)
            self.assertIn("comp:", direction.rationale)
            self.assertIn("motion:", direction.rationale)
            self.assertIn("grade:", direction.rationale)

    def test_an_unknown_visual_role_is_refused(self):
        with self.assertRaises(DirectionError):
            plan_visual_direction([_input(visual_role="sell")], seed=0)

    def test_an_empty_shot_list_is_refused(self):
        with self.assertRaises(DirectionError):
            plan_visual_direction([], seed=0)


# --------------------------------------------------------------------------- #
# 4. composition — the filter grammar
# --------------------------------------------------------------------------- #
class CompositionGraphTests(unittest.TestCase):
    def _graph(self, composition, **kwargs):
        values = dict(
            composition=composition,
            fit="cover",
            crop_bias="center",
            text_zone="top",
            width=1920,
            height=1080,
            spec=DEFAULT_DIRECTION_SPEC,
            node="d0",
        )
        values.update(kwargs)
        return _composition_graph("0:v:0", "out", **values)

    def test_every_composition_produces_a_graph_ending_on_the_output_label(self):
        for composition in ADAPTER_COMPOSITIONS:
            with self.subTest(composition=composition):
                graph = self._graph(composition)
                self.assertTrue(graph)
                self.assertTrue(graph[-1].endswith("[out]"))
                self.assertTrue(graph[0].startswith("[0:v:0]"))

    def test_fullscreen_is_the_plain_fit_chain(self):
        cover = self._graph("fullscreen", fit="cover")[0]
        contain = self._graph("fullscreen", fit="contain")[0]
        self.assertIn("crop=1920:1080", cover)
        self.assertIn("pad=1920:1080", contain)

    def test_extreme_crop_scales_past_the_canvas_before_cropping(self):
        chain = self._graph("extreme_crop")[0]
        self.assertIn("scale=2976:1674", chain)  # 1920x1080 * 1.55
        self.assertIn("crop=1920:1080", chain)

    def test_a_crop_bias_moves_the_window(self):
        left = self._graph("extreme_crop", crop_bias="left")[0]
        right = self._graph("extreme_crop", crop_bias="right")[0]
        top = self._graph("extreme_crop", crop_bias="top")[0]
        self.assertIn("crop=1920:1080:0:", left)
        self.assertIn("crop=1920:1080:iw-1920:", right)
        self.assertIn("crop=1920:1080:(iw-1920)/2:0,", top)

    def test_inset_and_layered_build_a_treated_background_from_the_same_source(self):
        for composition in ("inset", "layered"):
            with self.subTest(composition=composition):
                graph = self._graph(composition)
                joined = "\n".join(graph)
                self.assertIn("split=2", joined)
                self.assertIn("gblur=sigma=", joined)
                self.assertIn("curves=all=", joined)
                self.assertIn("overlay=", joined)

    def test_inset_holds_the_asset_smaller_than_the_canvas(self):
        graph = self._graph("inset")
        self.assertIn("scale=1344:756", "\n".join(graph))  # 1920x1080 * 0.70

    def test_layered_holds_a_wide_band_inside_the_frame(self):
        graph = self._graph("layered")
        self.assertIn("crop=1920:804", "\n".join(graph))  # 1920 / 2.39

    def test_split_places_two_regions_of_the_same_frame_side_by_side(self):
        graph = self._graph("split")
        joined = "\n".join(graph)
        self.assertIn("split=2", joined)
        self.assertIn("crop=952:1080:0:", joined)  # left region
        self.assertIn("crop=952:1080:iw-952:", joined)  # right region
        self.assertIn("overlay=968:0", joined)

    def test_text_focus_darkens_the_band_the_type_will_sit_in(self):
        top = self._graph("text_focus", text_zone="top")[0]
        lower = self._graph("text_focus", text_zone="lower")[0]
        self.assertIn("drawbox=x=0:y=0:", top)
        self.assertIn("color=black@", top)
        # the lower band starts near the bottom of the frame, not at the top
        self.assertNotIn("drawbox=x=0:y=0:", lower)

    def test_an_unknown_composition_or_bias_is_refused(self):
        with self.assertRaises(FFmpegError):
            self._graph("kenburns")
        with self.assertRaises(FFmpegError):
            self._graph("extreme_crop", crop_bias="diagonal")
        with self.assertRaises(FFmpegError):
            self._graph("text_focus", text_zone="everywhere")


# --------------------------------------------------------------------------- #
# 5. motion
# --------------------------------------------------------------------------- #
class DirectionMotionTests(unittest.TestCase):
    def _filter(self, motion, bias="center", spec=DEFAULT_DIRECTION_SPEC):
        return _direction_motion_filter(motion, bias, 1920, 1080, 90, spec)

    def test_a_static_hold_emits_nothing_at_all(self):
        self.assertEqual(self._filter("static_hold"), "")

    def test_every_move_emits_a_zoompan_pinned_to_the_canvas(self):
        for motion in ADAPTER_MOTIONS:
            if motion == "static_hold":
                continue
            with self.subTest(motion=motion):
                chain = self._filter(motion)
                self.assertIn("zoompan=", chain)
                self.assertIn("s=1920x1080", chain)
                self.assertIn("fps=30", chain)

    def test_a_push_and_a_pull_travel_in_opposite_directions(self):
        push = self._filter("slow_push_in")
        pull = self._filter("slow_pull_out")
        self.assertIn("z=1+0.075*", push)
        self.assertIn("z=1.075-0.075*", pull)

    def test_a_detail_push_travels_further_than_a_slow_push(self):
        spec = DEFAULT_DIRECTION_SPEC
        self.assertGreater(spec.detail_push_travel, spec.push_travel)
        self.assertIn(f"z=1+{spec.detail_push_travel}", self._filter("detail_push"))

    def test_the_bias_decides_which_way_a_drift_travels(self):
        left = self._filter("lateral_drift", "left")
        right = self._filter("lateral_drift", "right")
        self.assertIn("(1-on/89)", left)
        self.assertNotIn("(1-on/89)", right)

    def test_every_travel_stays_a_small_fraction_of_the_frame(self):
        spec = DEFAULT_DIRECTION_SPEC
        for travel in (spec.push_travel, spec.detail_push_travel, spec.drift_travel):
            self.assertGreater(travel, 0.0)
            self.assertLess(travel, 0.20)

    def test_a_zero_travel_policy_produces_no_move(self):
        spec = DirectionSpec(push_travel=0.0)
        self.assertEqual(self._filter("slow_push_in", spec=spec), "")

    def test_an_unknown_motion_is_refused(self):
        with self.assertRaises(FFmpegError):
            self._filter("zoom_in")


# --------------------------------------------------------------------------- #
# 6. grade
# --------------------------------------------------------------------------- #
class GradeChainTests(unittest.TestCase):
    def test_the_none_intensity_leaves_the_picture_alone(self):
        self.assertEqual(_grade_chain("none", DEFAULT_DIRECTION_SPEC), "")
        self.assertEqual(_grade_chain(None, DEFAULT_DIRECTION_SPEC), "")

    def test_the_standard_grade_uses_only_filters_in_the_lgpl_core(self):
        chain = _grade_chain("standard", DEFAULT_DIRECTION_SPEC)
        for filter_name in ("hue=s=", "curves=all=", "colorbalance=", "noise=", "vignette="):
            self.assertIn(filter_name, chain)
        # eq is missing from some builds, so it must never appear
        self.assertNotIn("eq=", chain)

    def test_a_stronger_intensity_desaturates_and_darkens_further(self):
        def saturation(name):
            chain = _grade_chain(name, DEFAULT_DIRECTION_SPEC)
            return float(chain.split("hue=s=")[1].split(",")[0])

        self.assertGreater(saturation("subtle"), saturation("standard"))
        self.assertGreater(saturation("standard"), saturation("strong"))

    def test_the_curve_keeps_its_points_ordered_and_inside_the_range(self):
        for name in GRADE_INTENSITIES:
            chain = _grade_chain(name, DEFAULT_DIRECTION_SPEC)
            if "curves=all=" not in chain:
                continue
            points = chain.split("curves=all='")[1].split("'")[0].split()
            values = [tuple(float(v) for v in point.split("/")) for point in points]
            xs = [x for x, _ in values]
            ys = [y for _, y in values]
            self.assertEqual(xs, sorted(xs))
            self.assertEqual(ys, sorted(ys))
            self.assertTrue(all(0.0 <= y <= 1.0 for y in ys))

    def test_a_luminance_pull_darkens_the_whole_range_not_only_highlights(self):
        plain = DirectionSpec()
        pulled = DirectionSpec(luminance_pull=0.12)

        def _points(spec, name):
            chain = _grade_chain(name, spec)
            body = chain.split("curves=all='")[1].split("'")[0]
            return [float(point.split("/")[1]) for point in body.split()]

        before = _points(plain, "standard")
        after = _points(pulled, "standard")
        # every point moves down, including the mid-tones
        for a, b in zip(before, after):
            self.assertLessEqual(b, a)
        self.assertLess(after[1], before[1])
        self.assertLess(after[-1], before[-1])

    def test_the_scrim_uses_enough_steps_to_read_as_a_gradient(self):
        from video_generator.adapters.ffmpeg import _scrim_chain

        chain = _scrim_chain(1920, 1080, "top", DEFAULT_DIRECTION_SPEC)
        opacities = [
            float(part.split("color=black@")[1].split(":")[0])
            for part in chain.split(",")
            if "color=black@" in part
        ]
        self.assertGreaterEqual(len(opacities), 12)
        self.assertEqual(opacities, sorted(opacities, reverse=True))
        # no single step may change the picture enough to be seen as a bar
        for first, second in zip(opacities, opacities[1:]):
            self.assertLess(first - second, 0.10)

    def test_an_unknown_intensity_is_refused(self):
        with self.assertRaises(FFmpegError):
            _grade_chain("extreme", DEFAULT_DIRECTION_SPEC)

    def test_the_operation_carries_the_policy_the_renderer_needs(self):
        policy = VisualDirectionPolicy()
        operation = visual_direction_operation(policy)
        self.assertEqual(operation.kind, "visual_direction")
        spec = _direction_spec(operation.parameters)
        self.assertAlmostEqual(spec.saturation, policy.grade.saturation)
        self.assertAlmostEqual(spec.extreme_crop_zoom, policy.extreme_crop_zoom)
        self.assertEqual(spec.intensities["strong"], policy.grade_intensities["strong"])

    def test_a_malformed_direction_operation_is_refused(self):
        for payload in (
            {"grade": {"nope": 1}},
            {"intensities": {"none": 1.0, "subtle": 1, "standard": 1, "strong": 1}},
            {"intensities": {"none": 0.0}},
            {"unknown": 1},
            {"grade": "loud"},
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(SequenceWorkflowError):
                    _direction_spec(payload)


# --------------------------------------------------------------------------- #
# 7. emphasis
# --------------------------------------------------------------------------- #
def _beat(beat_id, narration, importance, role="evidence"):
    return NarrationBeat(
        beat_id=beat_id,
        narration=narration,
        concept="estudo",
        entities=(),
        emotion=None,
        visual_intent="mostrar: estudo",
        asset_queries=("a study",),
        importance=importance,
        editorial_role=role,
    )


class EmphasisLayerTests(unittest.TestCase):
    def _layer(self, count=40, importance=0.9, **kwargs):
        beats = [
            _beat(f"b{index}", f"Em 19{50 + index} algo mudou de vez.", importance)
            for index in range(count)
        ]
        timings = {f"b{index}": (index * 5.0, index * 5.0 + 4.0) for index in range(count)}
        return plan_text_events(beats, timings, **kwargs)

    def test_the_layer_stays_sparse_even_when_every_beat_qualifies(self):
        events = self._layer()
        self.assertLessEqual(len(events), DEFAULT_EDITORIAL_POLICY.text_event_max_events)
        self.assertGreater(len(events), 0)

    def test_events_never_overlap_and_arrive_in_time_order(self):
        events = self._layer()
        starts = [event.start_seconds for event in events]
        self.assertEqual(starts, sorted(starts))
        for first, second in zip(events, events[1:]):
            self.assertGreaterEqual(second.start_seconds, first.end_seconds)

    def test_the_body_gap_is_respected(self):
        policy = EditorialPolicy(text_event_min_gap_seconds=30.0)
        events = self._layer(policy=policy, hook_policy=None)
        for first, second in zip(events, events[1:]):
            self.assertGreaterEqual(second.start_seconds - first.end_seconds, 30.0)

    def test_the_body_keeps_the_most_important_beats_not_the_earliest(self):
        beats = [_beat("b0", "Uma frase qualquer sobre o tema.", 0.61)]
        beats.append(_beat("b1", "Outra frase qualquer sobre o tema.", 0.99))
        timings = {"b0": (100.0, 104.0), "b1": (108.0, 112.0)}
        events = plan_text_events(
            beats,
            timings,
            policy=EditorialPolicy(text_event_min_gap_seconds=30.0),
            hook_policy=None,
        )
        self.assertEqual([event.beat_id for event in events], ["b1"])

    def test_a_beat_below_the_importance_bar_gets_nothing(self):
        self.assertEqual(self._layer(importance=0.1), ())

    def test_the_hook_gets_its_own_lower_bar_and_tighter_gap(self):
        beats = [_beat(f"b{i}", f"Em 19{60 + i} tudo mudou.", 0.5) for i in range(4)]
        timings = {f"b{i}": (i * 6.0, i * 6.0 + 4.0) for i in range(4)}
        with_hook = plan_text_events(
            beats, timings, hook_policy=HookPolicy(hook_seconds=40.0)
        )
        without = plan_text_events(beats, timings, hook_policy=None)
        self.assertGreater(len(with_hook), len(without))

    def test_an_event_carries_the_shot_the_highlight_and_the_reason(self):
        events = self._layer()
        for event in events:
            self.assertTrue(event.shot_id)
            self.assertTrue(event.rationale)
            self.assertIn(event.category, event.rationale)

    def test_a_highlight_is_a_real_span_inside_the_text(self):
        for event in self._layer():
            if event.highlight is None:
                continue
            start, end = event.highlight
            self.assertTrue(0 <= start < end <= len(event.text))
            self.assertTrue(event.highlight_text.strip())

    def test_a_highlight_outside_the_text_is_refused(self):
        with self.assertRaises(Exception):
            TextEvent(
                event_id="text_001",
                text="CURTO",
                start_seconds=0.0,
                end_seconds=1.0,
                category="keyword",
                importance=0.9,
                position="top",
                animation="highlight",
                highlight=(0, 99),
            )

    def test_an_event_round_trips_with_its_new_fields(self):
        event = TextEvent(
            event_id="text_001",
            text="SEU CEREBRO ESCOLHE",
            start_seconds=1.0,
            end_seconds=3.0,
            category="keyword",
            importance=0.8,
            position="top",
            animation="highlight",
            shot_id="scene_02_shot_01",
            highlight=(4, 11),
            rationale="body - keyword",
        )
        again = TextEvent.from_dict(json.loads(json.dumps(event.to_dict())))
        self.assertEqual(again, event)

    def test_the_highlight_animation_does_not_repaint_the_whole_line(self):
        from video_generator.adapters.ffmpeg import _event_override

        style = TextStyleSpec(accent="&H003CA3E5")
        cue = TextEventCue("UMA LINHA", 0.0, 2.0, "top", "highlight", False)
        override = _event_override(cue, 1920, 1080, style)
        # the outline must stay neutral: the accent belongs to one run inside
        # the text, not to a ring around every letter
        self.assertNotIn("\3c", override)

    def test_only_the_highlighted_run_takes_the_accent(self):
        style = TextStyleSpec(accent="&H003CA3E5", foreground="&H00EEF3F5")
        cue = TextEventCue("SEU CEREBRO ESCOLHE", 0.0, 2.0, "top", "highlight", False, (4, 11))
        rendered = _event_text(cue, style)
        self.assertEqual(rendered.count("\\c"), 2)
        self.assertIn("&H3CA3E5&}CEREBRO", rendered)
        # an already-accented emphasis cue is left alone: one accent per line
        emphasised = TextEventCue("1895", 0.0, 2.0, "middle", "pop", True, None)
        self.assertEqual(_event_text(emphasised, style), "1895")


# --------------------------------------------------------------------------- #
# 8. the hard editorial veto
# --------------------------------------------------------------------------- #
class HardVetoTests(unittest.TestCase):
    def _veto(self, words, *, intent="metaphorical", asked=()):
        return hard_veto_reasons(
            frozenset(words.split()),
            visual_intent_class=intent,
            query_terms=asked,
        )

    def test_every_veto_reason_is_declared(self):
        for reason in HARD_VETO_REASONS:
            self.assertIn(reason, REJECTION_REASONS)

    def test_the_abstract_render_that_ruined_the_last_cut_is_refused(self):
        for words in (
            "abstract 3d geometric waveform earthy tones",
            "glowing brain neurons neural network",
            "abstract blue maze geometric patterns",
            "3d ai imaging",
        ):
            with self.subTest(words=words):
                self.assertIn("hard_veto_cgi", self._veto(words))

    def test_cartoon_children_cheer_neon_and_fantasy_are_refused(self):
        cases = (
            ("cartoon illustration of a man", "hard_veto_cartoon"),
            ("students raising hands kids classroom", "hard_veto_children"),
            ("friends celebration confetti party", "hard_veto_cheerful"),
            ("neon cyberpunk city robot", "hard_veto_neon_scifi"),
            ("dragon wizard magic castle", "hard_veto_fantasy"),
            ("advertising promo luxury model posing", "hard_veto_commercial"),
        )
        for words, reason in cases:
            with self.subTest(reason=reason):
                self.assertIn(reason, self._veto(words))

    def test_an_animal_nobody_asked_for_is_refused(self):
        self.assertIn(
            "hard_veto_unasked_animal",
            self._veto("a lizard walking on the chessboard", asked=("chess", "board")),
        )
        self.assertNotIn(
            "hard_veto_unasked_animal",
            self._veto("a lizard on a rock", asked=("lizard", "rock")),
        )

    def test_a_beat_that_asked_for_children_still_gets_them(self):
        self.assertEqual(
            self._veto(
                "students in a classroom", asked=("classroom", "students", "exam")
            ),
            (),
        )

    def test_a_legitimate_dark_documentary_frame_survives(self):
        for words in (
            "empty corridor at night shadow",
            "hands flipping through old documents",
            "silhouette of a man looking out of a window",
            "crowd walking in a city street",
        ):
            with self.subTest(words=words):
                self.assertEqual(self._veto(words, intent="tension_or_suspense"), ())

    def test_a_real_metaphor_is_still_allowed(self):
        self.assertEqual(
            self._veto("symbolic silhouette in fog", asked=("symbolic", "silhouette")),
            (),
        )

    def test_a_scientific_beat_may_still_use_a_scientific_render(self):
        self.assertEqual(self._veto("neuron microscope render", intent="scientific"), ())

    def test_the_veto_can_be_switched_off_for_a_reproduction_run(self):
        policy = RelevancePolicy(hard_veto=False)
        self.assertEqual(
            hard_veto_reasons(
                frozenset({"abstract", "render"}),
                visual_intent_class="metaphorical",
                policy=policy,
            ),
            (),
        )

    def test_the_veto_reaches_the_candidate_assessment(self):
        assessment = assess_candidate(
            frozenset({"abstract", "render", "digital"}),
            visual_intent_class="metaphorical",
            visual_role="symbolize",
        )
        self.assertTrue(assessment.rejected)
        self.assertIn("hard_veto_cgi", assessment.rejection_reasons)

    def test_a_plan_without_relevance_labels_is_never_vetoed(self):
        assessment = assess_candidate(
            frozenset({"abstract", "render", "cartoon"}),
            visual_intent_class=None,
            visual_role=None,
        )
        self.assertEqual(assessment.rejection_reasons, ())

    def test_a_relevance_policy_round_trips_through_a_partial_document(self):
        policy = RelevancePolicy.from_dict({"hard_veto": False, "max_query_words": 5})
        self.assertFalse(policy.hard_veto)
        self.assertEqual(policy.max_query_words, 5)
        self.assertEqual(
            policy.veto_cartoon_lexicon, DEFAULT_RELEVANCE_POLICY.veto_cartoon_lexicon
        )


class QueryRefinementGuardTests(unittest.TestCase):
    """Refinement sharpens a visual phrase; it must not manufacture one."""

    def test_a_bare_noun_is_returned_untouched(self):
        self.assertEqual(
            refine_query(
                "informacao",
                visual_intent_class="metaphorical",
                visual_role="symbolize",
            ),
            "informacao",
        )

    def test_a_real_phrase_is_still_refined(self):
        refined = refine_query(
            "long office corridor",
            visual_intent_class="tension_or_suspense",
            visual_role="build_tension",
        )
        self.assertTrue(refined.endswith("long office corridor"))
        self.assertGreater(len(refined.split()), 3)


# --------------------------------------------------------------------------- #
# 9. overrides stay local
# --------------------------------------------------------------------------- #
class OverrideLocalityTests(unittest.TestCase):
    def _plan(self):
        script = NarrativeScript.from_text("s1", SCRIPT, total_duration_seconds=120.0)
        scene_plan = plan_scenes(script)
        shot_plan, assets = plan_shots(
            scene_plan, seed=0, semantic=True, visual_relevance=True
        )
        return script, scene_plan, shot_plan, assets

    def test_an_override_changes_one_field_of_one_shot_and_nothing_else(self):
        script, scene_plan, shot_plan, assets = self._plan()
        target = shot_plan.shots[2]
        _, patched, _ = apply_overrides(
            scene_plan,
            shot_plan,
            assets,
            {"shots": {target.shot_id: {"visual_query": "a lit corridor at night"}}},
            script=script,
        )
        by_id = {shot.shot_id: shot for shot in patched.shots}
        for shot in shot_plan.shots:
            if shot.shot_id == target.shot_id:
                continue
            self.assertEqual(
                by_id[shot.shot_id].to_dict(),
                shot.to_dict(),
                f"{shot.shot_id} changed although it was never overridden",
            )

    def test_the_patched_shot_keeps_its_whole_semantic_reading(self):
        script, scene_plan, shot_plan, assets = self._plan()
        target = shot_plan.shots[1]
        self.assertIsNotNone(target.visual_intent_class)
        _, patched, requirements = apply_overrides(
            scene_plan,
            shot_plan,
            assets,
            {"shots": {target.shot_id: {"visual_query": "a lit corridor at night"}}},
            script=script,
        )
        shot = {s.shot_id: s for s in patched.shots}[target.shot_id]
        self.assertEqual(shot.visual_query, "a lit corridor at night")
        self.assertEqual(shot.visual_intent_class, target.visual_intent_class)
        self.assertEqual(shot.visual_role, target.visual_role)
        self.assertEqual(shot.refined_query, target.refined_query)
        self.assertEqual(shot.editorial_role, target.editorial_role)
        self.assertEqual(shot.beat_concept, target.beat_concept)
        # the authored query leads, the reading's own queries stay behind it
        self.assertEqual(shot.asset_queries[0], "a lit corridor at night")
        for query in target.asset_queries:
            self.assertIn(query, shot.asset_queries)

    def test_the_relevance_labels_reach_every_requirement_after_an_override(self):
        script, scene_plan, shot_plan, assets = self._plan()
        target = shot_plan.shots[0]
        _, _, requirements = apply_overrides(
            scene_plan,
            shot_plan,
            assets,
            {"shots": {target.shot_id: {"visual_query": "a lit corridor at night"}}},
            script=script,
        )
        for requirement in requirements.requirements:
            self.assertIsNotNone(
                requirement.visual_intent_class,
                f"{requirement.asset_id} lost its visual intent class",
            )
            self.assertIsNotNone(requirement.visual_role)
            self.assertTrue(requirement.queries)


# --------------------------------------------------------------------------- #
# 10. the plan reaches the renderer
# --------------------------------------------------------------------------- #
class EditPlanIntegrationTests(unittest.TestCase):
    def _plan_with_direction(self, tmp):
        script = NarrativeScript.from_text("s1", SCRIPT, total_duration_seconds=120.0)
        scene_plan = plan_scenes(script)
        shot_plan, _ = plan_shots(
            scene_plan, seed=0, semantic=True, visual_relevance=True
        )
        directions = plan_shot_visual_direction(shot_plan, seed=0)
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
        )
        return shot_plan, directions, plan

    def test_every_segment_carries_its_direction(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            shot_plan, directions, plan = self._plan_with_direction(tmp)
            by_shot = directions.by_shot()
            segments = [
                op for op in plan.operations if op.kind in ("image_clip", "sequence_clip")
            ]
            self.assertEqual(len(segments), len(shot_plan.shots))
            for operation in segments:
                direction = by_shot[operation.operation_id]
                self.assertEqual(
                    operation.parameters["composition"], direction.composition
                )
                self.assertEqual(operation.parameters["grade"], direction.grade)
                self.assertEqual(
                    operation.parameters["crop_bias"], direction.crop_bias
                )
                if operation.kind == "image_clip":
                    self.assertEqual(operation.parameters["motion"], direction.motion)

    def test_the_workflow_reads_the_direction_back_out_of_the_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            _, directions, plan = self._plan_with_direction(tmp)
            parsed = _operations_from_plan(plan)
            self.assertIsNotNone(parsed.direction)
            by_shot = directions.by_shot()
            for segment, shot_id in zip(parsed.segments, by_shot):
                self.assertEqual(
                    segment.composition, by_shot[shot_id].composition
                )

    def test_a_plan_without_direction_is_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            script = NarrativeScript.from_text("s1", SCRIPT, total_duration_seconds=120.0)
            scene_plan = plan_scenes(script)
            shot_plan, _ = plan_shots(scene_plan, seed=0)
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
            )
            for operation in plan.operations:
                self.assertNotIn("composition", operation.parameters)
                self.assertNotIn("grade", operation.parameters)
            parsed = _operations_from_plan(plan)
            self.assertIsNone(parsed.direction)
            for segment in parsed.segments:
                self.assertIsNone(segment.composition)
                self.assertIsNone(segment.grade)

    def test_a_still_only_composition_is_refused_on_moving_footage(self):
        for parameters in (
            {"composition": "inset"},
            {"composition": "layered"},
            {"composition": "split"},
        ):
            with self.subTest(parameters=parameters):
                composition, _bias, _zone, _grade = _segment_direction(parameters)
                self.assertEqual(composition, parameters["composition"])

    def test_a_malformed_segment_direction_is_refused(self):
        for parameters in (
            {"composition": "kenburns"},
            {"crop_bias": "diagonal"},
            {"text_zone": "everywhere"},
            {"grade": "extreme"},
            {"composition": "text_focus"},
        ):
            with self.subTest(parameters=parameters):
                with self.assertRaises(SequenceWorkflowError):
                    _segment_direction(parameters)


# --------------------------------------------------------------------------- #
# 11. manifest validation understands the whole tail
# --------------------------------------------------------------------------- #
def _segment(index, source):
    return EditOperation(
        operation_id=f"clip_{index}",
        kind="image_clip",
        source=source,
        parameters={
            "duration_seconds": 4.0,
            "fit": "cover",
            "composition": "extreme_crop",
            "crop_bias": "left",
            "grade": "standard",
            "motion": "slow_push_in",
        },
    )


def _plan_with(tail, tmp):
    source = str(tmp / "a.jpg")
    sources = [source]
    operations = [_segment(1, source), _segment(2, source)]
    for operation in tail:
        operations.append(operation)
        if operation.source is not None and operation.source not in sources:
            sources.append(operation.source)
    return EditPlan(
        plan_id="p",
        brief_id="b",
        sources=tuple(sources),
        output_path=str(tmp / "out.mp4"),
        operations=tuple(operations),
        target_format=TargetFormat(1920, 1080, "cover"),
    )


_TEXT_EVENTS = EditOperation(
    operation_id="text_events",
    kind="text_events",
    parameters={
        "items": [
            {
                "text": "VOCE NAO LEMBRA",
                "start_seconds": 0.5,
                "end_seconds": 2.5,
                "position": "top",
                "animation": "highlight",
                "emphasis": False,
                "highlight": [0, 4],
            }
        ],
        "style": {
            "font_name": "Sans",
            "foreground": "&H00EEF3F5",
            "accent": "&H003CA3E5",
            "emphasis_scale": 1.6,
            "safe_margin_fraction": 0.06,
        },
    },
)
_VISUAL_DIRECTION = visual_direction_operation(VisualDirectionPolicy())


class ManifestValidationTailTests(unittest.TestCase):
    """The bug this fixes: a legitimate semantic plan failed on form alone."""

    def test_a_plan_carrying_text_events_is_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            self.assertTrue(_sequence_plan_matches(_plan_with([_TEXT_EVENTS], tmp)))

    def test_a_plan_carrying_a_visual_direction_is_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            self.assertTrue(
                _sequence_plan_matches(_plan_with([_VISUAL_DIRECTION], tmp))
            )

    def test_the_whole_tail_together_is_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            music = str(tmp / "m.wav")
            narration = str(tmp / "n.wav")
            plan = _plan_with(
                [
                    _VISUAL_DIRECTION,
                    EditOperation(
                        operation_id="captions",
                        kind="captions",
                        parameters={
                            "style": "bottom_box",
                            "items": [
                                {"text": "ola", "start_seconds": 0.0, "end_seconds": 1.0}
                            ],
                        },
                    ),
                    _TEXT_EVENTS,
                    EditOperation(
                        operation_id="fade",
                        kind="fade",
                        parameters={"from_black_seconds": 0.5, "to_black_seconds": 1.0},
                    ),
                    EditOperation(
                        operation_id="music",
                        kind="music",
                        source=music,
                        parameters={
                            "duration_policy": "loop_to_timeline",
                            "gain_db": -20.0,
                        },
                    ),
                    EditOperation(
                        operation_id="narration",
                        kind="narration",
                        source=narration,
                        parameters={"duration_policy": "match_timeline"},
                    ),
                ],
                tmp,
            )
            self.assertTrue(_sequence_plan_matches(plan))

    def test_a_malformed_text_events_operation_is_still_refused(self):
        bad = (
            EditOperation(
                operation_id="text_events",
                kind="text_events",
                parameters={"items": []},
            ),
            EditOperation(
                operation_id="text_events",
                kind="text_events",
                parameters={
                    "items": [
                        {"text": "X", "start_seconds": 0.0, "end_seconds": 999.0}
                    ]
                },
            ),
            EditOperation(
                operation_id="text_events",
                kind="text_events",
                parameters={
                    "items": [
                        {
                            "text": "X",
                            "start_seconds": 0.0,
                            "end_seconds": 1.0,
                            "position": "sideways",
                        }
                    ]
                },
            ),
            EditOperation(
                operation_id="text_events",
                kind="text_events",
                parameters={
                    "items": [
                        {"text": "AB", "start_seconds": 0.0, "end_seconds": 1.0,
                         "highlight": [0, 9]}
                    ]
                },
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            for index, operation in enumerate(bad):
                with self.subTest(case=index):
                    self.assertFalse(
                        _sequence_plan_matches(_plan_with([operation], tmp))
                    )

    def test_an_unknown_operation_kind_is_still_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            plan = _plan_with(
                [EditOperation(operation_id="x", kind="transition", parameters={})], tmp
            )
            self.assertFalse(_sequence_plan_matches(plan))

    def test_a_segment_with_an_impossible_direction_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            source = str(tmp / "a.mp4")
            plan = EditPlan(
                plan_id="p",
                brief_id="b",
                sources=(source,),
                output_path=str(tmp / "out.mp4"),
                operations=(
                    EditOperation(
                        operation_id="c1",
                        kind="sequence_clip",
                        source=source,
                        start_seconds=0.0,
                        end_seconds=2.0,
                        parameters={"fit": "cover", "composition": "inset"},
                    ),
                    EditOperation(
                        operation_id="c2",
                        kind="sequence_clip",
                        source=source,
                        start_seconds=2.0,
                        end_seconds=4.0,
                        parameters={"fit": "cover"},
                    ),
                ),
                target_format=TargetFormat(1920, 1080, "cover"),
            )
            self.assertFalse(_sequence_plan_matches(plan))

    def test_a_plan_from_before_the_layer_is_still_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            source = str(tmp / "a.jpg")
            plan = EditPlan(
                plan_id="p",
                brief_id="b",
                sources=(source,),
                output_path=str(tmp / "out.mp4"),
                operations=(
                    EditOperation(
                        operation_id="c1",
                        kind="image_clip",
                        source=source,
                        parameters={"duration_seconds": 3.0, "motion": "zoom_in"},
                    ),
                    EditOperation(
                        operation_id="c2",
                        kind="image_clip",
                        source=source,
                        parameters={"duration_seconds": 3.0},
                    ),
                ),
                target_format=TargetFormat(1920, 1080, "contain"),
            )
            self.assertTrue(_sequence_plan_matches(plan))


# --------------------------------------------------------------------------- #
# 12. the adapter refuses an impossible timeline before FFmpeg runs
# --------------------------------------------------------------------------- #
class ComposeGuardTests(unittest.TestCase):
    def _compose(self, tmp, clips, **kwargs):
        from video_generator.adapters.ffmpeg import compose_video_sequence

        return compose_video_sequence(clips, str(tmp / "out.mp4"), **kwargs)

    def test_a_direction_without_a_canvas_is_refused(self):
        # two moving segments and no fit: nothing else in the graph needs a
        # canvas, so only the direction guard can be the one that fires
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            source = tmp / "a.mp4"
            source.write_bytes(b"x")
            clips = [
                SequenceClip(str(source), 0.0, 2.0, None, "extreme_crop"),
                SequenceClip(str(source), 2.0, 4.0, None, "extreme_crop"),
            ]
            with self.assertRaisesRegex(FFmpegError, "visual direction requires"):
                self._compose(tmp, clips)

    def test_a_still_only_composition_on_a_video_segment_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            source = tmp / "a.mp4"
            source.write_bytes(b"x")
            clips = [
                SequenceClip(str(source), 0.0, 2.0, "cover", "inset"),
                SequenceClip(str(source), 2.0, 4.0, "cover"),
            ]
            with self.assertRaisesRegex(FFmpegError, "only available for a still"):
                self._compose(tmp, clips, canvas=(1920, 1080))

    def test_an_unknown_grade_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            source = tmp / "a.jpg"
            source.write_bytes(b"x")
            clips = [
                SequenceImage(str(source), 2.0, "cover", None, "fullscreen", "center", None, "loud"),
                SequenceImage(str(source), 2.0, "cover"),
            ]
            with self.assertRaisesRegex(FFmpegError, "grade must be one of"):
                self._compose(tmp, clips, canvas=(1920, 1080))


if __name__ == "__main__":
    unittest.main()


# --------------------------------------------------------------------------- #
# 13. an asset too bright for the channel never reaches the timeline
# --------------------------------------------------------------------------- #
class BrightnessCeilingTests(unittest.TestCase):
    """The one property no provider publishes and no lexicon can infer."""

    def _validate(self, ceiling, measured):
        from video_generator.domain.assets import AssetScoringPolicy
        from video_generator.resolve import _validate_acquired

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "asset.jpg"
            path.write_bytes(b"x" * 32)
            return _validate_acquired(
                path,
                media_type="image",
                duration_needed=0.0,
                policy=AssetScoringPolicy(max_mean_luma=ceiling),
                probe=None,
                luma=lambda _p: measured,
            )

    def test_a_white_desk_is_refused(self):
        reason = self._validate(0.60, 0.87)
        self.assertIsNotNone(reason)
        self.assertIn("brightness", reason)

    def test_a_dark_frame_passes(self):
        self.assertIsNone(self._validate(0.60, 0.21))

    def test_an_unmeasurable_file_is_not_punished_for_it(self):
        self.assertIsNone(self._validate(0.60, None))

    def test_without_a_ceiling_the_check_never_runs(self):
        from video_generator.domain.assets import AssetScoringPolicy
        from video_generator.resolve import _validate_acquired

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "asset.jpg"
            path.write_bytes(b"x" * 32)
            called = []
            self.assertIsNone(
                _validate_acquired(
                    path,
                    media_type="image",
                    duration_needed=0.0,
                    policy=AssetScoringPolicy(),
                    probe=None,
                    luma=lambda p: called.append(p) or 0.99,
                )
            )
            self.assertEqual(called, [])

    def test_the_ceiling_is_a_policy_field_that_round_trips(self):
        from video_generator.domain.assets import AssetResolutionError, AssetScoringPolicy

        policy = AssetScoringPolicy.from_dict({"max_mean_luma": 0.6})
        self.assertEqual(policy.max_mean_luma, 0.6)
        self.assertEqual(
            AssetScoringPolicy.from_dict(policy.to_dict()).max_mean_luma, 0.6
        )
        self.assertIsNone(AssetScoringPolicy().max_mean_luma)
        for bad in (0.0, -1.0, 1.4, "bright"):
            with self.subTest(bad=bad):
                with self.assertRaises(AssetResolutionError):
                    AssetScoringPolicy(max_mean_luma=bad)
