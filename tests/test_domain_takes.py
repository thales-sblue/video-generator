"""`domain.takes`: read a take like an editor, suggest cuts, never make them."""

import json
import unittest
from pathlib import Path

from video_generator.domain.takes import (
    CutReview,
    EditSummary,
    RemovableBlock,
    ReviewedSegment,
    SilenceSpan,
    TakesError,
    TranscriptSegment,
    analyze_take,
    render_review_markdown,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads(
    (REPOSITORY_ROOT / "schemas" / "cut-review-v1.schema.json").read_text(
        encoding="utf-8"
    )
)


def _seg(text, start, end):
    return TranscriptSegment(text=text, start_seconds=start, end_seconds=end)


def _by_time(review):
    return {round(s.start_seconds, 3): s for s in review.segments}


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
    def test_clean_take_is_all_keep_with_no_blocks(self):
        segments = [
            _seg("Bem-vindo ao canal.", 0.0, 2.0),
            _seg("Hoje eu mostro o comando doctor do projeto.", 2.0, 5.0),
            _seg("Ele inspeciona o ambiente e nunca escreve nada.", 5.0, 8.0),
        ]
        review = analyze_take(segments, source_path="v.mp4", duration_seconds=8.0)
        self.assertEqual([s.suggestion for s in review.segments], ["KEEP"] * 3)
        self.assertEqual([s.category for s in review.segments], ["NONE"] * 3)
        self.assertEqual(review.blocks, ())
        self.assertEqual(review.provenance, "heuristic")
        self.assertEqual(review.summary.estimated_duration_seconds, 8.0)
        self.assertIn("ajuste fino", review.summary.rhythm_note)

    def test_long_silence_row_is_a_long_pause_cut(self):
        segments = [
            _seg("Primeira frase completa aqui.", 0.0, 3.0),
            _seg("Segunda frase completa também.", 6.0, 9.0),
        ]
        silences = [SilenceSpan(start_seconds=3.1, end_seconds=5.9)]
        review = analyze_take(
            segments, silences, source_path="v.mp4", duration_seconds=9.0
        )
        pause = review.segments[1]
        self.assertEqual((pause.suggestion, pause.category), ("CUT", "LONG_PAUSE"))
        self.assertIn("silêncio", pause.text)

    def test_repetition_across_non_adjacent_segments(self):
        segments = [
            _seg("O código do projeto fica salvo no servidor do Git.", 0.0, 4.0),
            _seg("Você clona esse repositório para a sua máquina local.", 4.0, 8.0),
            _seg("Enfim, o código do projeto fica guardado no servidor do Git.", 8.0, 12.0),
        ]
        review = analyze_take(segments, source_path="v.mp4", duration_seconds=12.0)
        third = review.segments[2]
        self.assertIn(third.suggestion, ("REVIEW", "CUT"))
        self.assertEqual(third.category, "REPETITION")

    def test_self_commentary_is_flagged(self):
        segments = [
            _seg("Então o servidor guarda o código de todo mundo.", 0.0, 4.0),
            _seg("Está uma bagunça aqui, mas enfim, acho que deu pra entender.", 4.0, 8.0),
            _seg("O cliente acessa outro servidor, o de produção.", 8.0, 12.0),
        ]
        review = analyze_take(segments, source_path="v.mp4", duration_seconds=12.0)
        self.assertEqual(review.segments[1].category, "SELF_COMMENTARY")
        self.assertIn(review.segments[1].suggestion, ("REVIEW", "CUT"))

    def test_tangent_run_with_a_scope_widening_marker(self):
        segments = [
            _seg("O código do projeto fica salvo no servidor do Git.", 0.0, 4.0),
            _seg("Você clona o código do servidor para a sua máquina local.", 4.0, 8.0),
            _seg("Você trabalha no código local e devolve para o servidor.", 8.0, 12.0),
            _seg("Todo mundo do time puxa e empurra código do mesmo servidor.", 12.0, 16.0),
            _seg("A gente consegue deixar mais complexo isso se entrar em CI CD.", 16.0, 21.0),
            _seg("Aí tem pipeline, ambiente de homologação, várias etapas de infra.", 21.0, 26.0),
            _seg("Mas você não precisa entender isso agora, pode esquecer.", 26.0, 30.0),
            _seg("Voltando: o código local e o código no servidor, só isso.", 30.0, 34.0),
        ]
        review = analyze_take(segments, source_path="v.mp4", duration_seconds=34.0)
        flagged = [s for s in review.segments if s.category in ("TANGENT", "OFF_TOPIC")]
        self.assertGreaterEqual(len(flagged), 2)
        self.assertTrue(any(b.category in ("TANGENT", "OFF_TOPIC") for b in review.blocks))

    def test_failed_take_restart_marker(self):
        segments = [
            _seg("O domínio depende de", 0.0, 1.4),
            _seg("Na verdade, deixa eu explicar a camada de adapters primeiro.", 1.4, 5.0),
        ]
        review = analyze_take(segments, source_path="v.mp4", duration_seconds=5.0)
        self.assertEqual(review.segments[0].suggestion, "CUT")
        self.assertEqual(review.segments[0].category, "FAILED_TAKE")
        self.assertEqual(review.segments[1].category, "FAILED_TAKE")

    def test_blocks_group_adjacent_flagged_rows_and_feed_the_summary(self):
        segments = [
            _seg("O git guarda o código no servidor da empresa.", 0.0, 4.0),
            _seg("Está uma bagunça aqui, mas enfim.", 4.0, 6.0),
            _seg("Deixa eu tentar de novo essa parte.", 6.0, 9.0),
            _seg("Enfim, o git guarda o código no servidor da empresa.", 9.0, 13.0),
            _seg("Você clona, trabalha e devolve pro servidor.", 13.0, 17.0),
        ]
        review = analyze_take(segments, source_path="v.mp4", duration_seconds=17.0)
        self.assertTrue(review.blocks)
        block = review.blocks[0]
        self.assertLessEqual(block.start_seconds, 4.0)
        self.assertGreaterEqual(block.end_seconds, 9.0)
        self.assertLess(
            review.summary.estimated_duration_seconds, review.duration_seconds
        )
        self.assertTrue(review.summary.top_removable)

    def test_duration_shorter_than_transcript_is_rejected(self):
        with self.assertRaises(TakesError):
            analyze_take(
                [_seg("uma frase", 0.0, 9.0)],
                source_path="v.mp4",
                duration_seconds=5.0,
            )

    def test_unordered_or_empty_or_bad_input_is_rejected(self):
        with self.assertRaises(TakesError):
            analyze_take(
                [_seg("b", 5.0, 6.0), _seg("a", 0.0, 1.0)], source_path="v.mp4"
            )
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
                [_seg("a", 0.0, 1.0)], source_path="v.mp4", long_silence_seconds=0
            )


class SerialisationTests(unittest.TestCase):
    def _review(self):
        return analyze_take(
            [
                _seg("O código do projeto fica no servidor do Git da empresa.", 0.0, 4.0),
                _seg("Você clona o repositório para a máquina local e trabalha.", 4.0, 8.0),
                _seg("Enfim, o código do projeto fica no servidor do Git da empresa.", 8.0, 12.0),
                _seg("Está uma bagunça, mas acho que deu pra entender.", 12.0, 15.0),
            ],
            [SilenceSpan(start_seconds=8.0, end_seconds=8.2)],
            source_path="/abs/v.mp4",
            language="pt",
            duration_seconds=15.0,
        )

    def test_to_dict_matches_the_published_schema_shape(self):
        payload = self._review().to_dict()
        self.assertEqual(set(payload), set(SCHEMA["required"]))
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["provenance"], "heuristic")
        self.assertRegex(payload["duration"], SCHEMA["properties"]["duration"]["pattern"])

        seg_props = SCHEMA["$defs"]["reviewed_segment"]["properties"]
        seg_required = set(SCHEMA["$defs"]["reviewed_segment"]["required"])
        categories = set(SCHEMA["$defs"]["category"]["enum"])
        for item in payload["segments"]:
            self.assertEqual(set(item), seg_required)
            self.assertRegex(item["start"], seg_props["start"]["pattern"])
            self.assertRegex(item["end"], seg_props["end"]["pattern"])
            self.assertIn(item["suggestion"], ("KEEP", "REVIEW", "CUT"))
            self.assertIn(item["category"], categories)
            self.assertGreaterEqual(item["confidence"], 0.0)
            self.assertLessEqual(item["confidence"], 1.0)

        block_required = set(SCHEMA["$defs"]["removable_block"]["required"])
        for block in payload["blocks"]:
            self.assertEqual(set(block), block_required)
            self.assertIn(block["suggestion"], ("REVIEW", "CUT"))

        summary_required = set(SCHEMA["properties"]["summary"]["required"])
        self.assertEqual(set(payload["summary"]), summary_required)
        self.assertLessEqual(len(payload["summary"]["top_removable"]), 5)

        self.assertEqual(json.loads(json.dumps(payload)), payload)

    def test_round_trips_through_from_dict(self):
        review = self._review()
        again = CutReview.from_dict(review.to_dict())
        self.assertEqual(again.to_dict(), review.to_dict())

    def test_from_dict_rejects_a_bad_payload(self):
        for payload in (
            "not a dict",
            {"schema_version": 2},
            {"schema_version": 1, "source_path": "v", "language": "pt"},
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(TakesError):
                    CutReview.from_dict(payload)

    def test_markdown_has_summary_blocks_and_per_segment_sections(self):
        text = render_review_markdown(self._review())
        self.assertIn("# Revisão de cortes — 00:15", text)
        self.assertIn("## Resumo da edição", text)
        self.assertIn("Duração original:", text)
        self.assertIn("Duração estimada após cortes:", text)
        self.assertIn("## Trecho a trecho", text)
        self.assertIn("Motivo:", text)

    def test_markdown_rejects_non_review(self):
        with self.assertRaises(TakesError):
            render_review_markdown({"not": "a review"})


class DataclassContractTests(unittest.TestCase):
    def test_reviewed_segment_keep_and_flag_rules(self):
        ReviewedSegment(0.0, 1.0, "t", "KEEP", "NONE", "", 0.0)  # ok
        with self.assertRaises(TakesError):
            ReviewedSegment(0.0, 1.0, "t", "KEEP", "REPETITION", "", 0.0)
        with self.assertRaises(TakesError):
            ReviewedSegment(0.0, 1.0, "t", "KEEP", "NONE", "why", 0.0)
        with self.assertRaises(TakesError):
            ReviewedSegment(0.0, 1.0, "t", "CUT", "NONE", "why", 0.5)
        with self.assertRaises(TakesError):
            ReviewedSegment(0.0, 1.0, "t", "CUT", "REPETITION", "", 0.5)
        with self.assertRaises(TakesError):
            ReviewedSegment(0.0, 1.0, "t", "CUT", "REPETITION", "why", 1.5)

    def test_removable_block_rules(self):
        RemovableBlock(0.0, 6.0, "CUT", "TANGENT", "desvio", 0.6)  # ok
        with self.assertRaises(TakesError):
            RemovableBlock(0.0, 6.0, "KEEP", "TANGENT", "desvio", 0.6)
        with self.assertRaises(TakesError):
            RemovableBlock(0.0, 6.0, "CUT", "NONE", "desvio", 0.6)
        with self.assertRaises(TakesError):
            RemovableBlock(0.0, 6.0, "CUT", "TANGENT", "  ", 0.6)

    def test_edit_summary_cannot_grow_the_video(self):
        with self.assertRaises(TakesError):
            EditSummary(10.0, 12.0, (), "nota")
        with self.assertRaises(TakesError):
            EditSummary(10.0, 5.0, (), "  ")

    def test_cut_review_rejects_out_of_order_rows(self):
        summary = EditSummary(10.0, 9.0, (), "nota de ritmo")
        with self.assertRaises(TakesError):
            CutReview(
                source_path="v.mp4",
                language="pt",
                provenance="agent",
                duration_seconds=10.0,
                segments=(
                    ReviewedSegment(5.0, 6.0, "b", "KEEP", "NONE", "", 0.0),
                    ReviewedSegment(0.0, 1.0, "a", "KEEP", "NONE", "", 0.0),
                ),
                blocks=(),
                summary=summary,
            )

    def test_cut_review_rejects_bad_provenance(self):
        summary = EditSummary(1.0, 1.0, (), "nota")
        with self.assertRaises(TakesError):
            CutReview(
                source_path="v.mp4",
                language="pt",
                provenance="human",
                duration_seconds=1.0,
                segments=(ReviewedSegment(0.0, 1.0, "a", "KEEP", "NONE", "", 0.0),),
                blocks=(),
                summary=summary,
            )


if __name__ == "__main__":
    unittest.main()
