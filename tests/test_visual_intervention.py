import unittest

from video_generator.domain.cuts import TimelineSegment
from video_generator.domain.visual_intervention import (
    ReviewedSegment,
    VisualEvent,
    VisualPlan,
    VisualPlanError,
    build_visual_plan,
    detect_keyword_events,
    load_manual_events,
    overlay_events_to_motion_text,
    plan_from_dict,
    remap_to_timeline,
    shift_overlay_events_after_freezes,
)


class DetectKeywordEventsTests(unittest.TestCase):
    def test_finds_first_mention_only_in_keep_segments(self) -> None:
        segments = (
            ReviewedSegment(0.0, 5.0, "hoje vamos falar de git e servidor", "KEEP"),
            ReviewedSegment(5.0, 10.0, "servidor de novo, e local também", "KEEP"),
            ReviewedSegment(10.0, 12.0, "isso aqui vai ser cortado, servidor", "CUT"),
        )
        events = detect_keyword_events(segments, keywords=("servidor", "local"))
        kinds = [(e.text, round(e.start_seconds, 2)) for e in events]
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].text, ("SERVIDOR",))
        self.assertLess(events[0].start_seconds, 5.0)
        self.assertEqual(events[1].text, ("LOCAL",))
        self.assertGreaterEqual(events[1].start_seconds, 5.0)

    def test_ignores_partial_word_matches(self) -> None:
        segments = (ReviewedSegment(0.0, 3.0, "isso é localizado ali", "KEEP"),)
        events = detect_keyword_events(segments, keywords=("local",))
        self.assertEqual(events, ())


class VisualEventValidationTests(unittest.TestCase):
    def test_keyword_needs_text(self) -> None:
        with self.assertRaises(VisualPlanError):
            VisualEvent(kind="keyword", start_seconds=0, end_seconds=1)

    def test_zoom_scale_bounds(self) -> None:
        with self.assertRaises(VisualPlanError):
            VisualEvent(kind="zoom", start_seconds=0, end_seconds=1, scale=2.0)
        event = VisualEvent(kind="zoom", start_seconds=0, end_seconds=1, scale=1.1)
        self.assertAlmostEqual(event.scale, 1.1)

    def test_freeze_seconds_cap(self) -> None:
        with self.assertRaises(VisualPlanError):
            VisualEvent(kind="freeze", start_seconds=0, end_seconds=10, freeze_seconds=9)

    def test_start_can_be_zero(self) -> None:
        event = VisualEvent(kind="freeze", start_seconds=0.0, end_seconds=1.0, freeze_seconds=1.0)
        self.assertEqual(event.start_seconds, 0.0)


class ManualEventsTests(unittest.TestCase):
    def test_loads_diagram_and_conflict(self) -> None:
        payload = [
            {"kind": "diagram", "start": "01:00.000", "lines": ["SEU PC", "SERVIDOR"]},
            {
                "kind": "conflict",
                "start_seconds": 30.0,
                "end_seconds": 33.0,
                "lines": ["DEV A + DEV B", "CONFLITO!"],
            },
            {"kind": "freeze", "start": "00:45.000", "freeze_seconds": 1.2, "label": "boniteza zero"},
            {"kind": "zoom", "start_seconds": 12.0, "end_seconds": 14.0, "scale": 1.1},
        ]
        events = load_manual_events(payload)
        self.assertEqual(len(events), 4)
        diagram = events[0]
        self.assertEqual(diagram.kind, "diagram")
        self.assertEqual(diagram.text, ("SEU PC", "SERVIDOR"))
        freeze = events[2]
        self.assertEqual(freeze.freeze_seconds, 1.2)
        self.assertAlmostEqual(freeze.end_seconds - freeze.start_seconds, 1.2)

    def test_rejects_unknown_kind(self) -> None:
        with self.assertRaises(VisualPlanError):
            load_manual_events([{"kind": "sparkle", "start_seconds": 1, "end_seconds": 2}])

    def test_diagram_needs_text(self) -> None:
        with self.assertRaises(VisualPlanError):
            load_manual_events([{"kind": "diagram", "start_seconds": 1, "end_seconds": 2}])


class RemapToTimelineTests(unittest.TestCase):
    def test_remaps_after_a_removed_cut(self) -> None:
        # source: [0,10) kept, [10,15) cut, [15,20) kept
        timeline = (
            TimelineSegment(0.0, 10.0, "keep"),
            TimelineSegment(15.0, 20.0, "keep"),
        )
        self.assertAlmostEqual(remap_to_timeline(5.0, timeline), 5.0)
        self.assertAlmostEqual(remap_to_timeline(17.0, timeline), 12.0)
        self.assertIsNone(remap_to_timeline(12.0, timeline))

    def test_remaps_through_a_sped_up_aside(self) -> None:
        timeline = (
            TimelineSegment(0.0, 10.0, "keep"),
            TimelineSegment(10.0, 20.0, "aside", speed=2.0),
            TimelineSegment(20.0, 30.0, "keep"),
        )
        # 5s into the aside (source time) -> 2.5s of screen time, offset by the
        # first 10s keep segment
        self.assertAlmostEqual(remap_to_timeline(15.0, timeline), 12.5)
        # 5s into the final keep segment -> 10 + 5 (aside's 5s effective) + 5
        self.assertAlmostEqual(remap_to_timeline(25.0, timeline), 20.0)


class BuildVisualPlanTests(unittest.TestCase):
    def test_combines_auto_and_manual_and_sorts(self) -> None:
        segments = (ReviewedSegment(0.0, 5.0, "vamos falar de push agora", "KEEP"),)
        manual = load_manual_events(
            [{"kind": "diagram", "start_seconds": 1.0, "end_seconds": 2.0, "lines": ["X"]}]
        )
        plan = build_visual_plan(segments, manual_events=manual, keywords=("push",))
        self.assertEqual(len(plan.events), 2)
        self.assertEqual([e.start_seconds for e in plan.events], sorted(e.start_seconds for e in plan.events))

    def test_drops_auto_keyword_cut_from_the_timeline(self) -> None:
        segments = (
            ReviewedSegment(0.0, 5.0, "push aqui", "KEEP"),
        )
        # the whole segment was actually cut out of the preview
        timeline = (TimelineSegment(5.0, 10.0, "keep"),)
        plan = build_visual_plan(segments, timeline=timeline, keywords=("push",))
        self.assertEqual(plan.events, ())

    def test_rejects_overlapping_zoom_and_freeze(self) -> None:
        segments = ()
        manual = load_manual_events(
            [
                {"kind": "zoom", "start_seconds": 1.0, "end_seconds": 3.0, "scale": 1.1},
                {"kind": "freeze", "start_seconds": 2.0, "freeze_seconds": 1.0},
            ]
        )
        with self.assertRaises(VisualPlanError):
            build_visual_plan(segments, manual_events=manual)


class PlanRoundTripTests(unittest.TestCase):
    def test_to_dict_and_back(self) -> None:
        manual = load_manual_events(
            [{"kind": "diagram", "start_seconds": 1.0, "end_seconds": 2.0, "lines": ["A", "B"]}]
        )
        plan = build_visual_plan((), manual_events=manual)
        reloaded = plan_from_dict(plan.to_dict())
        self.assertEqual(len(reloaded.events), 1)
        self.assertEqual(reloaded.events[0].text, ("A", "B"))
        self.assertEqual(reloaded.events[0].kind, "diagram")


class ShiftOverlayAfterFreezesTests(unittest.TestCase):
    def test_shifts_only_overlay_events_past_preceding_freezes(self) -> None:
        events = (
            VisualEvent(kind="freeze", start_seconds=5.0, end_seconds=6.0, freeze_seconds=1.0),
            VisualEvent(kind="keyword", start_seconds=10.0, end_seconds=11.0, text=("PUSH",)),
            VisualEvent(kind="zoom", start_seconds=2.0, end_seconds=3.0, scale=1.1),
        )
        plan = VisualPlan(events=events)
        shifted = shift_overlay_events_after_freezes(plan)
        by_kind = {e.kind: e for e in shifted}
        self.assertAlmostEqual(by_kind["keyword"].start_seconds, 11.0)
        self.assertAlmostEqual(by_kind["zoom"].start_seconds, 2.0)
        self.assertAlmostEqual(by_kind["freeze"].start_seconds, 5.0)


class OverlayEventsToMotionTextTests(unittest.TestCase):
    def test_single_line_becomes_dominant_word(self) -> None:
        events = (VisualEvent(kind="keyword", start_seconds=1.0, end_seconds=2.0, text=("PUSH",)),)
        motion = overlay_events_to_motion_text(events)
        self.assertEqual(len(motion), 1)
        self.assertEqual(motion[0].layout, "dominant_word")
        self.assertEqual(len(motion[0].blocks), 1)
        self.assertEqual(motion[0].blocks[0].weight, "massive")

    def test_multi_line_conflict_promotes_last_line_and_accents_it(self) -> None:
        events = (
            VisualEvent(
                kind="conflict",
                start_seconds=1.0,
                end_seconds=3.0,
                text=("DEV A + DEV B", "CONFLITO!"),
            ),
        )
        motion = overlay_events_to_motion_text(events)
        self.assertEqual(motion[0].layout, "stacked_hierarchy")
        self.assertEqual(motion[0].blocks[-1].text, "CONFLITO!")
        self.assertEqual(motion[0].blocks[-1].weight, "massive")
        self.assertTrue(motion[0].blocks[-1].accent)
        self.assertEqual(motion[0].blocks[0].weight, "large")

    def test_zoom_and_freeze_are_skipped(self) -> None:
        events = (
            VisualEvent(kind="zoom", start_seconds=1.0, end_seconds=2.0, scale=1.1),
            VisualEvent(kind="freeze", start_seconds=1.0, end_seconds=2.0, freeze_seconds=1.0),
        )
        self.assertEqual(overlay_events_to_motion_text(events), ())


if __name__ == "__main__":
    unittest.main()
