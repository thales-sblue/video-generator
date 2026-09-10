"""`domain.takes`: suggest cut points in a recorded take, never make them."""

import json
import unittest
from pathlib import Path

from video_generator.domain.takes import (
    CutReview,
    ReviewedSegment,
    SilenceSpan,
    TakesError,
    TranscriptSegment,
    analyze_take,
    render_review_markdown,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _seg(text, start, end):
    return TranscriptSegment(text=text, start_seconds=start, end_seconds=end)


class TranscriptSegmentContractTests(unittest.TestCase):
    def test_rejects_empty_text_and_bad_spans(self):
        for kwargs in (
            {"text": "  ", "start_seconds": 0.0, "end_seconds": 1.0},
            {"text": "ok", "start_seconds": -1.0, "end_seconds": 1.0},
            {"text": "ok", "start_seconds": 2.0, "end_seconds": 2.0},
            {"text": "ok", "start_seconds": 3.0, "end_seconds": 1.0},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(TakesError):
                    TranscriptSegment(**kwargs)


class AnalyzeTakeTests(unittest.TestCase):
    def test_clean_take_is_all_keep_and_preserves_order(self):
        segments = [
            _seg("Bem-vindo ao canal.", 0.0, 2.0),
            _seg("Hoje eu mostro o projeto.", 2.0, 4.5),
            _seg("Vamos começar pelo comando doctor.", 4.5, 7.0),
        ]
        review = analyze_take(segments, source_path="v.mp4", duration_seconds=7.0)
        self.assertEqual([s.suggestion for s in review.segments], ["KEEP", "KEEP", "KEEP"])
        self.assertEqual(review.counts(), {"KEEP": 3, "REVIEW": 0, "CUT": 0})
        self.assertEqual([s.reason for s in review.segments], ["", "", ""])
        self.assertEqual([s.confidence for s in review.segments], [0.0, 0.0, 0.0])
        self.assertEqual(review.to_dict()["duration"], "00:07")

    def test_long_silence_becomes_its_own_cut_row_in_time_order(self):
        segments = [
            _seg("Primeira frase completa.", 0.0, 3.0),
            _seg("Segunda frase completa aqui.", 6.0, 9.0),
        ]
        silences = [SilenceSpan(start_seconds=3.1, end_seconds=5.9)]
        review = analyze_take(
            segments, silences, source_path="v.mp4", duration_seconds=9.0
        )
        self.assertEqual(len(review.segments), 3)
        middle = review.segments[1]
        self.assertEqual(middle.suggestion, "CUT")
        self.assertIn("silêncio", middle.text)
        self.assertIn("pausa longa", middle.reason)
        self.assertGreater(middle.confidence, 0.5)

    def test_head_and_tail_silence_is_not_flagged(self):
        segments = [_seg("Só uma frase.", 5.0, 8.0)]
        silences = [
            SilenceSpan(start_seconds=0.0, end_seconds=5.0),
            SilenceSpan(start_seconds=8.0, end_seconds=12.0),
        ]
        review = analyze_take(
            segments, silences, source_path="v.mp4", duration_seconds=12.0
        )
        self.assertEqual(len(review.segments), 1)
        self.assertEqual(review.segments[0].suggestion, "KEEP")

    def test_repeated_phrase_cuts_the_earlier_attempt(self):
        segments = [
            _seg("Então o argparse recebe o subcomando.", 0.0, 3.0),
            _seg("Então o argparse recebe o subcomando.", 3.0, 6.2),
            _seg("E ele despacha para o runner certo.", 6.2, 9.0),
        ]
        review = analyze_take(segments, source_path="v.mp4", duration_seconds=9.0)
        self.assertEqual(review.segments[0].suggestion, "CUT")
        self.assertEqual(review.segments[1].suggestion, "KEEP")
        self.assertIn("repet", review.segments[0].reason.lower())

    def test_restart_marker_reviews_itself_and_cuts_the_run_up(self):
        segments = [
            _seg("O domínio depende de", 0.0, 1.4),
            _seg("Na verdade, deixa eu explicar a camada primeiro.", 1.4, 5.0),
        ]
        review = analyze_take(segments, source_path="v.mp4", duration_seconds=5.0)
        self.assertEqual(review.segments[0].suggestion, "CUT")
        self.assertEqual(review.segments[1].suggestion, "REVIEW")
        self.assertIn("recomeço", review.segments[1].reason)

    def test_excessive_filler_is_reviewed_not_cut(self):
        segments = [
            _seg("tipo, né, tipo assim, sabe, tipo, o que eu queria dizer", 0.0, 5.0),
        ]
        review = analyze_take(segments, source_path="v.mp4", duration_seconds=5.0)
        self.assertEqual(review.segments[0].suggestion, "REVIEW")
        self.assertIn("vícios de linguagem", review.segments[0].reason)

    def test_abandoned_fragment_is_reviewed(self):
        segments = [
            _seg("E aí quando o usuário", 0.0, 1.5),
            _seg("O comando doctor só inspeciona, nunca escreve.", 1.5, 5.0),
        ]
        review = analyze_take(segments, source_path="v.mp4", duration_seconds=5.0)
        self.assertEqual(review.segments[0].suggestion, "REVIEW")
        self.assertIn("abandon", review.segments[0].reason.lower())

    def test_cut_outranks_review_on_the_same_segment(self):
        # both a repeat (CUT) and a trailing-fragment (REVIEW) point at seg 0
        segments = [
            _seg("o render usa o ffmpeg", 0.0, 2.0),
            _seg("o render usa o ffmpeg", 2.0, 4.0),
            _seg("Depois o manifesto fixa o SHA.", 4.0, 7.0),
        ]
        review = analyze_take(segments, source_path="v.mp4", duration_seconds=7.0)
        self.assertEqual(review.segments[0].suggestion, "CUT")

    def test_duration_shorter_than_transcript_is_rejected(self):
        with self.assertRaises(TakesError):
            analyze_take(
                [_seg("uma frase", 0.0, 9.0)],
                source_path="v.mp4",
                duration_seconds=5.0,
            )

    def test_unordered_segments_are_rejected(self):
        with self.assertRaises(TakesError):
            analyze_take(
                [_seg("b", 5.0, 6.0), _seg("a", 0.0, 1.0)],
                source_path="v.mp4",
            )

    def test_rejects_empty_input_and_bad_types(self):
        with self.assertRaises(TakesError):
            analyze_take([], source_path="v.mp4")
        with self.assertRaises(TakesError):
            analyze_take(["not a segment"], source_path="v.mp4")
        with self.assertRaises(TakesError):
            analyze_take(
                [_seg("a", 0.0, 1.0)], [("bad", "silence")], source_path="v.mp4"
            )
        with self.assertRaises(TakesError):
            analyze_take(
                [_seg("a", 0.0, 1.0)],
                source_path="v.mp4",
                long_silence_seconds=0,
            )


class SerialisationTests(unittest.TestCase):
    def test_to_dict_matches_the_published_schema_shape(self):
        schema = json.loads(
            (REPOSITORY_ROOT / "schemas" / "cut-review-v1.schema.json").read_text(
                encoding="utf-8"
            )
        )
        review = analyze_take(
            [
                _seg("Então o argparse recebe o subcomando.", 0.0, 3.0),
                _seg("Então o argparse recebe o subcomando.", 3.0, 6.0),
            ],
            [SilenceSpan(start_seconds=3.0, end_seconds=3.2)],
            source_path="/abs/v.mp4",
            language="pt",
            duration_seconds=6.0,
        )
        payload = review.to_dict()
        self.assertEqual(
            set(payload), set(schema["required"])
        )
        self.assertEqual(payload["schema_version"], 1)
        self.assertRegex(payload["duration"], schema["properties"]["duration"]["pattern"])
        item_schema = schema["properties"]["segments"]["items"]
        for item in payload["segments"]:
            self.assertEqual(set(item), set(item_schema["required"]))
            self.assertRegex(item["start"], item_schema["properties"]["start"]["pattern"])
            self.assertRegex(item["end"], item_schema["properties"]["end"]["pattern"])
            self.assertIn(item["suggestion"], ("KEEP", "REVIEW", "CUT"))
            self.assertGreaterEqual(item["confidence"], 0.0)
            self.assertLessEqual(item["confidence"], 1.0)
        # round-trips through JSON unchanged
        self.assertEqual(json.loads(json.dumps(payload)), payload)

    def test_markdown_has_a_summary_and_flags_the_reasons(self):
        review = analyze_take(
            [
                _seg("uma introducao limpa aqui.", 0.0, 2.0),
                _seg("o render usa o ffmpeg", 2.0, 4.0),
                _seg("o render usa o ffmpeg", 4.0, 6.0),
            ],
            source_path="v.mp4",
            duration_seconds=6.0,
        )
        text = render_review_markdown(review)
        self.assertIn("# Revisão de cortes — 00:06", text)
        self.assertIn("CUT", text)
        self.assertIn("Motivo:", text)
        self.assertIn("Confiança:", text)
        self.assertIn("00:04.000 → 00:06.000", text)

    def test_markdown_rejects_non_review(self):
        with self.assertRaises(TakesError):
            render_review_markdown({"not": "a review"})


class ReviewedSegmentContractTests(unittest.TestCase):
    def test_keep_carries_no_reason_and_flag_needs_one(self):
        with self.assertRaises(TakesError):
            ReviewedSegment(0.0, 1.0, "t", "KEEP", "why", 0.0)
        with self.assertRaises(TakesError):
            ReviewedSegment(0.0, 1.0, "t", "CUT", "", 0.5)
        with self.assertRaises(TakesError):
            ReviewedSegment(0.0, 1.0, "t", "MAYBE", "", 0.0)
        with self.assertRaises(TakesError):
            ReviewedSegment(0.0, 1.0, "t", "CUT", "why", 1.5)

    def test_cut_review_rejects_out_of_order_rows(self):
        with self.assertRaises(TakesError):
            CutReview(
                source_path="v.mp4",
                language="pt",
                duration_seconds=10.0,
                segments=(
                    ReviewedSegment(5.0, 6.0, "b", "KEEP", "", 0.0),
                    ReviewedSegment(0.0, 1.0, "a", "KEEP", "", 0.0),
                ),
            )


if __name__ == "__main__":
    unittest.main()
