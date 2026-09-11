"""`domain.cuts`: turn approved cut ranges into the ranges to keep."""

import unittest

from video_generator.domain.cuts import (
    Aside,
    CutOutcome,
    CutsError,
    Interval,
    TimelineSegment,
    build_timeline,
    consolidate_asides,
    consolidate_cuts,
    keep_intervals,
    parse_clock,
    plan_cuts,
)

NP = {"pad_before_seconds": 0.0, "pad_after_seconds": 0.0}


def _spans(intervals):
    return [(round(i.start_seconds, 3), round(i.end_seconds, 3)) for i in intervals]


class ParseClockTests(unittest.TestCase):
    def test_accepts_the_supported_forms(self):
        self.assertAlmostEqual(parse_clock("00:22.640"), 22.64)
        self.assertAlmostEqual(parse_clock("02:37.840"), 157.84)
        self.assertAlmostEqual(parse_clock("1:02:03.500"), 3723.5)
        self.assertAlmostEqual(parse_clock("00:05"), 5.0)
        self.assertAlmostEqual(parse_clock(12.5), 12.5)
        self.assertAlmostEqual(parse_clock(0), 0.0)

    def test_rejects_junk(self):
        for value in ("", "abc", "1:2:3:4", "00:75.000", None, True, -3, float("nan")):
            with self.subTest(value=value):
                with self.assertRaises(CutsError):
                    parse_clock(value)


class ConsolidateTests(unittest.TestCase):
    def test_single_cut_passes_through(self):
        cuts = consolidate_cuts(
            [{"start": "00:10", "end": "00:20"}], duration_seconds=60, **NP
        )
        self.assertEqual(_spans(cuts), [(10.0, 20.0)])

    def test_orders_and_merges_overlapping_and_touching(self):
        cuts = consolidate_cuts(
            [
                {"start": 30, "end": 40},
                {"start": 10, "end": 20},
                {"start": 18, "end": 25},   # overlaps 10-20
                {"start": 40, "end": 45},   # touches 30-40
            ],
            duration_seconds=60,
            **NP,
        )
        self.assertEqual(_spans(cuts), [(10.0, 25.0), (30.0, 45.0)])

    def test_clamps_to_duration_and_drops_cuts_past_the_end(self):
        cuts = consolidate_cuts(
            [{"start": 55, "end": 90}, {"start": 200, "end": 210}],
            duration_seconds=60,
            **NP,
        )
        self.assertEqual(_spans(cuts), [(55.0, 60.0)])

    def test_safety_margin_shrinks_each_cut_inward(self):
        cuts = consolidate_cuts(
            [{"start": 10.0, "end": 20.0}],
            duration_seconds=60,
            pad_before_seconds=0.05,
            pad_after_seconds=0.05,
        )
        self.assertEqual(_spans(cuts), [(10.05, 19.95)])

    def test_contiguous_cuts_merge_before_the_margin_no_sliver(self):
        # a transcription splits one 40 s digression into consecutive cuts;
        # the margin must not leave 0.1 s keep slivers between them
        raw = [
            {"start": 100.0, "end": 106.0},
            {"start": 106.0, "end": 112.0},
            {"start": 112.0, "end": 140.0},
        ]
        cuts = consolidate_cuts(
            raw, duration_seconds=300, pad_before_seconds=0.05, pad_after_seconds=0.05
        )
        self.assertEqual(_spans(cuts), [(100.05, 139.95)])
        keeps = keep_intervals(cuts, 300)
        self.assertEqual(_spans(keeps), [(0.0, 100.05), (139.95, 300.0)])

    def test_margin_that_would_empty_a_cut_drops_it(self):
        cuts = consolidate_cuts(
            [{"start": 10.0, "end": 10.03}],
            duration_seconds=60,
            pad_before_seconds=0.05,
            pad_after_seconds=0.05,
        )
        self.assertEqual(cuts, ())

    def test_rejects_invalid_intervals_and_bad_input(self):
        for raw in (
            [{"start": 20, "end": 10}],
            [{"start": 5, "end": 5}],
            [{"start": -1, "end": 4}],
            [{"start": "oops", "end": "00:10"}],
            [{"start": 1}],
            "10-20",
        ):
            with self.subTest(raw=raw):
                with self.assertRaises(CutsError):
                    consolidate_cuts(raw, duration_seconds=60, **NP)
        with self.assertRaises(CutsError):
            consolidate_cuts([], duration_seconds=0, **NP)

    def test_empty_list_yields_no_cuts(self):
        self.assertEqual(consolidate_cuts([], duration_seconds=60, **NP), ())


class KeepIntervalTests(unittest.TestCase):
    def test_no_cuts_keeps_the_whole_video(self):
        self.assertEqual(_spans(keep_intervals((), 60)), [(0.0, 60.0)])

    def test_multiple_cuts_leave_the_gaps(self):
        cuts = (Interval(10, 20), Interval(30, 40))
        self.assertEqual(_spans(keep_intervals(cuts, 60)), [(0.0, 10.0), (20.0, 30.0), (40.0, 60.0)])

    def test_cut_at_the_start_drops_the_leading_piece(self):
        self.assertEqual(_spans(keep_intervals((Interval(0, 8),), 60)), [(8.0, 60.0)])

    def test_cut_near_the_end_drops_the_trailing_piece(self):
        self.assertEqual(_spans(keep_intervals((Interval(52, 60),), 60)), [(0.0, 52.0)])

    def test_consecutive_cuts_produce_one_keep_between_far_ones(self):
        cuts = (Interval(10, 20), Interval(20, 30))  # already merged upstream normally
        self.assertEqual(_spans(keep_intervals(cuts, 60)), [(0.0, 10.0), (30.0, 60.0)])

    def test_a_sub_frame_keep_sliver_is_absorbed(self):
        cuts = (Interval(10, 20), Interval(20.02, 30))
        self.assertEqual(_spans(keep_intervals(cuts, 60)), [(0.0, 10.0), (30.0, 60.0)])

    def test_rejects_unconsolidated_or_out_of_range_cuts(self):
        with self.assertRaises(CutsError):
            keep_intervals((Interval(30, 40), Interval(10, 20)), 60)
        with self.assertRaises(CutsError):
            keep_intervals((Interval(10, 70),), 60)
        with self.assertRaises(CutsError):
            keep_intervals(("nope",), 60)


class PlanCutsTests(unittest.TestCase):
    def test_duration_is_preserved_across_the_split(self):
        cuts, keeps, outcome = plan_cuts(
            [{"start": "00:22.640", "end": "00:31.400"}, {"start": "02:37.840", "end": "03:20.000"}],
            duration_seconds=288.0,
            pad_before_seconds=0.0,
            pad_after_seconds=0.0,
        )
        removed = sum(i.duration_seconds for i in cuts)
        kept = sum(i.duration_seconds for i in keeps)
        self.assertAlmostEqual(kept + removed, 288.0, places=6)
        self.assertAlmostEqual(outcome.final_seconds, kept, places=6)
        self.assertAlmostEqual(outcome.removed_seconds, removed, places=3)
        self.assertEqual(outcome.cuts_applied, 2)

    def test_overlapping_cuts_count_once_in_the_outcome(self):
        _cuts, _keeps, outcome = plan_cuts(
            [{"start": 10, "end": 30}, {"start": 20, "end": 40}],
            duration_seconds=100,
            pad_before_seconds=0.0,
            pad_after_seconds=0.0,
        )
        self.assertEqual(outcome.cuts_applied, 1)
        self.assertAlmostEqual(outcome.removed_seconds, 30.0, places=3)
        self.assertAlmostEqual(outcome.final_seconds, 70.0, places=3)

    def test_outcome_to_dict_is_clock_formatted(self):
        outcome = CutOutcome(288.0, 51.0, 237.0, 2)
        self.assertEqual(
            outcome.to_dict(),
            {
                "original_duration": "04:48.000",
                "removed_duration": "00:51.000",
                "final_duration": "03:57.000",
                "cuts_applied": 2,
            },
        )


class AsideContractTests(unittest.TestCase):
    def test_defaults_and_rejects_bad_values(self):
        aside = Aside(10.0, 20.0)
        self.assertEqual(aside.speed, 1.17)
        self.assertEqual(aside.label, "desvio rápido")
        for kwargs in (
            {"start_seconds": 20.0, "end_seconds": 10.0},
            {"start_seconds": 0.0, "end_seconds": 1.0, "speed": 0},
            {"start_seconds": 0.0, "end_seconds": 1.0, "speed": -1.0},
            {"start_seconds": 0.0, "end_seconds": 1.0, "label": "  "},
            {"start_seconds": 0.0, "end_seconds": 1.0, "label": "linha um\nlinha dois"},
            {"start_seconds": 0.0, "end_seconds": 1.0, "label": "x" * 41},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(CutsError):
                    Aside(**kwargs)

    def test_label_may_be_none(self):
        aside = Aside(0.0, 1.0, label=None)
        self.assertIsNone(aside.label)


class ConsolidateAsidesTests(unittest.TestCase):
    def test_parses_defaults_speed_and_label(self):
        asides = consolidate_asides(
            [{"start": "02:37.840", "end": "03:21.160"}], duration_seconds=300
        )
        self.assertEqual(len(asides), 1)
        self.assertAlmostEqual(asides[0].start_seconds, 157.84)
        self.assertEqual(asides[0].speed, 1.17)
        self.assertEqual(asides[0].label, "desvio rápido")

    def test_honours_a_per_aside_override(self):
        asides = consolidate_asides(
            [{"start": "00:10", "end": "00:20", "speed": 1.5, "label": "voltando já"}],
            duration_seconds=60,
        )
        self.assertEqual(asides[0].speed, 1.5)
        self.assertEqual(asides[0].label, "voltando já")

    def test_two_non_overlapping_asides_sort_by_start(self):
        asides = consolidate_asides(
            [{"start": "00:40", "end": "00:50"}, {"start": "00:10", "end": "00:20"}],
            duration_seconds=60,
        )
        self.assertEqual(
            [(a.start_seconds, a.end_seconds) for a in asides],
            [(10.0, 20.0), (40.0, 50.0)],
        )

    def test_overlapping_asides_are_rejected(self):
        with self.assertRaises(CutsError):
            consolidate_asides(
                [{"start": 10.0, "end": 20.0}, {"start": 15.0, "end": 30.0}],
                duration_seconds=60,
            )

    def test_asides_that_only_touch_are_allowed(self):
        asides = consolidate_asides(
            [{"start": 10.0, "end": 20.0}, {"start": 20.0, "end": 30.0}],
            duration_seconds=60,
        )
        self.assertEqual(len(asides), 2)

    def test_empty_list_yields_no_asides(self):
        self.assertEqual(consolidate_asides([], duration_seconds=60), ())

    def test_rejects_an_aside_entirely_outside_the_video(self):
        with self.assertRaises(CutsError):
            consolidate_asides([{"start": 100, "end": 110}], duration_seconds=60)


class BuildTimelineTests(unittest.TestCase):
    def test_slices_a_keep_interval_around_two_asides(self):
        keeps = (Interval(0.0, 300.0),)
        asides = consolidate_asides(
            [
                {"start": "02:37.840", "end": "03:21.160"},
                {"start": "03:42.120", "end": "04:28.200"},
            ],
            duration_seconds=300,
        )
        timeline = build_timeline(keeps, asides)
        kinds = [segment.kind for segment in timeline]
        self.assertEqual(kinds, ["keep", "aside", "keep", "aside", "keep"])
        self.assertAlmostEqual(timeline[0].end_seconds, 157.84)
        self.assertEqual(timeline[1].speed, 1.17)
        self.assertEqual(timeline[3].label, "desvio rápido")

    def test_no_asides_returns_the_keeps_unchanged(self):
        keeps = (Interval(0.0, 10.0), Interval(20.0, 30.0))
        timeline = build_timeline(keeps, ())
        self.assertEqual([(s.start_seconds, s.end_seconds, s.kind) for s in timeline],
                         [(0.0, 10.0, "keep"), (20.0, 30.0, "keep")])

    def test_aside_overlapping_a_cut_gap_is_rejected(self):
        # a cut removed [10, 20); an aside that reaches into it is ambiguous
        keeps = (Interval(0.0, 10.0), Interval(20.0, 60.0))
        asides = (Aside(5.0, 25.0),)
        with self.assertRaises(CutsError):
            build_timeline(keeps, asides)

    def test_aside_fully_inside_a_cut_gap_is_rejected(self):
        keeps = (Interval(0.0, 10.0), Interval(20.0, 60.0))
        asides = (Aside(12.0, 18.0),)
        with self.assertRaises(CutsError):
            build_timeline(keeps, asides)

    def test_timeline_segment_rejects_speed_or_label_on_a_plain_keep(self):
        with self.assertRaises(CutsError):
            TimelineSegment(0.0, 1.0, "keep", speed=1.5)
        with self.assertRaises(CutsError):
            TimelineSegment(0.0, 1.0, "keep", label="x")


if __name__ == "__main__":
    unittest.main()
