"""Captions timed against words actually measured in the rendered narration."""

import unittest
from unittest import mock

from video_generator.adapters.aligner import (
    AlignerError,
    TranscribedSegment,
    WordTiming,
    transcribe_segments,
    transcribe_words,
)
from video_generator.subtitles import (
    CAPTION_HOLD_SECONDS,
    SubtitleParseError,
    cues_from_word_timings,
    parse_subtitle_cues,
    render_srt,
)


def _even_words(sentence, *, step=0.4, spoken=0.3, offset=0.0):
    return tuple(
        (word, offset + index * step, offset + index * step + spoken)
        for index, word in enumerate(sentence.split())
    )


class WordTimingContractTests(unittest.TestCase):
    def test_rejects_empty_text_and_impossible_spans(self):
        for kwargs in (
            {"text": "  "},
            {"start_seconds": -1.0},
            {"end_seconds": float("inf")},
            {"start_seconds": 2.0, "end_seconds": 1.0},
            {"start_seconds": True},
        ):
            base = {"text": "palavra", "start_seconds": 1.0, "end_seconds": 1.5}
            base.update(kwargs)
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(AlignerError):
                    WordTiming(**base)


class CuesFromWordTimingsTests(unittest.TestCase):
    def test_cue_starts_when_its_first_word_is_spoken(self):
        text = "Uma frase curta. Outra frase curta."
        words = _even_words("uma frase curta outra frase curta")
        cues = cues_from_word_timings(text, words, total_seconds=4.0)
        self.assertEqual(len(cues), 2)
        self.assertAlmostEqual(cues[0][1], 0.0, places=3)
        # the second cue opens on "outra", the fourth measured word
        self.assertAlmostEqual(cues[1][1], words[3][1], places=3)

    def test_a_cue_never_shows_a_word_before_it_is_spoken(self):
        text = "Primeira frase aqui. Segunda frase aqui."
        words = _even_words("primeira frase aqui segunda frase aqui", step=0.9, spoken=0.5)
        cues = cues_from_word_timings(text, words, total_seconds=6.0)
        for (_, start, _), first_word_index in zip(cues, (0, 3)):
            self.assertLessEqual(start, words[first_word_index][1] + 1e-6)
            self.assertGreaterEqual(start + 1e-6, words[max(0, first_word_index - 1)][1])

    def test_accents_and_case_do_not_break_the_match(self):
        text = "Inteligência não é vacina."
        words = _even_words("inteligencia nao e vacina")
        cues = cues_from_word_timings(text, words, total_seconds=3.0)
        self.assertEqual(len(cues), 1)
        self.assertAlmostEqual(cues[0][1], 0.0, places=3)

    def test_words_the_transcript_missed_are_interpolated_locally(self):
        text = "Alfa bravo charlie. Delta echo foxtrot. Golf hotel india."
        spoken = _even_words("alfa bravo charlie zzz zzz zzz golf hotel india")
        cues = cues_from_word_timings(text, spoken, total_seconds=4.0)
        self.assertEqual(len(cues), 3)
        # the unmatched middle sits between its neighbours, and the ends are untouched
        self.assertAlmostEqual(cues[0][1], spoken[0][1], places=3)
        # the non-overlap guard may nudge a start by one epsilon, no more
        self.assertAlmostEqual(cues[2][1], spoken[6][1], delta=0.005)
        self.assertGreater(cues[1][1], cues[0][1])
        self.assertLess(cues[1][2], cues[2][2])

    def test_cues_stay_ordered_non_overlapping_and_inside_the_timeline(self):
        text = "Alfa bravo. Charlie delta. Echo foxtrot. Golf hotel."
        words = _even_words("alfa bravo charlie delta echo foxtrot golf hotel")
        total = 4.0
        cues = cues_from_word_timings(text, words, total_seconds=total)
        previous_end = 0.0
        for _, start, end in cues:
            self.assertGreaterEqual(start + 1e-9, previous_end - 1e-9)
            self.assertGreater(end, start)
            self.assertLessEqual(end, total + 1e-9)
            previous_end = end

    def test_a_cue_holds_into_a_pause_but_not_indefinitely(self):
        text = "Alfa bravo. Charlie delta."
        words = (
            ("alfa", 0.0, 0.3),
            ("bravo", 0.3, 0.6),
            ("charlie", 9.0, 9.3),
            ("delta", 9.3, 9.6),
        )
        cues = cues_from_word_timings(text, words, total_seconds=10.0)
        self.assertLessEqual(cues[0][2], 0.6 + CAPTION_HOLD_SECONDS + 1e-6)
        self.assertLess(cues[0][2], words[2][1])

    def test_rejects_unusable_input(self):
        words = _even_words("alfa bravo")
        for call in (
            lambda: cues_from_word_timings("Alfa bravo.", words, total_seconds=0),
            lambda: cues_from_word_timings("Alfa bravo.", words, total_seconds=True),
            lambda: cues_from_word_timings("Alfa bravo.", (), total_seconds=2.0),
            lambda: cues_from_word_timings("   ", words, total_seconds=2.0),
            lambda: cues_from_word_timings(
                "Alfa bravo.", (("alfa", 2.0, 1.0),), total_seconds=2.0
            ),
            lambda: cues_from_word_timings("Zulu yankee.", words, total_seconds=2.0),
        ):
            with self.subTest(call=call):
                with self.assertRaises(SubtitleParseError):
                    call()


class RenderSrtTests(unittest.TestCase):
    def test_round_trips_through_the_parser(self):
        cues = (("Primeira linha", 0.0, 1.25), ("Segunda linha", 1.25, 3.5))
        self.assertEqual(parse_subtitle_cues(render_srt(cues), source_format="srt"), cues)

    def test_refuses_markup_and_disordered_cues(self):
        for cues in (
            (("<b>ola</b>", 0.0, 1.0),),
            (("ola", 1.0, 1.0),),
            (("ola", 0.0, 2.0), ("mundo", 1.0, 3.0)),
            (),
        ):
            with self.subTest(cues=cues):
                with self.assertRaises(SubtitleParseError):
                    render_srt(cues)


class TranscribeWordsTests(unittest.TestCase):
    def test_fails_closed_when_the_model_is_not_installed(self):
        with mock.patch(
            "video_generator.adapters.aligner.resolve_whisper_model", return_value=None
        ):
            with self.assertRaises(AlignerError) as caught:
                transcribe_words(__file__)
        self.assertIn("not found", str(caught.exception))

    def test_rejects_a_missing_audio_source(self):
        with self.assertRaises(AlignerError):
            transcribe_words("does-not-exist.wav")

    def test_rejects_invalid_options(self):
        for kwargs in ({"language": " "}, {"beam_size": 0}, {"beam_size": True}, {"compute_type": ""}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(AlignerError):
                    transcribe_words(__file__, **kwargs)

    def test_clamps_overlapping_decoder_output_into_a_monotonic_clock(self):
        class _Word:
            def __init__(self, word, start, end):
                self.word = word
                self.start = start
                self.end = end

        class _Segment:
            def __init__(self, words):
                self.words = words

        fake_model = mock.MagicMock()
        fake_model.transcribe.return_value = (
            [_Segment([_Word(" alfa", 0.0, 1.0), _Word(" bravo", 0.5, 1.4)])],
            None,
        )
        with mock.patch(
            "video_generator.adapters.aligner.resolve_whisper_model", return_value="model-dir"
        ), mock.patch(
            "video_generator.adapters.aligner._load_whisper",
            return_value=lambda *a, **k: fake_model,
        ):
            words = transcribe_words(__file__)
        self.assertEqual([word.text for word in words], ["alfa", "bravo"])
        self.assertEqual(words[1].start_seconds, 1.0)


class TranscribeSegmentsTests(unittest.TestCase):
    def test_fails_closed_when_the_model_is_not_installed(self):
        with mock.patch(
            "video_generator.adapters.aligner.resolve_whisper_model", return_value=None
        ):
            with self.assertRaises(AlignerError):
                transcribe_segments(__file__)

    def test_keeps_phrase_text_and_clamps_to_a_monotonic_clock(self):
        class _Segment:
            def __init__(self, text, start, end):
                self.text = text
                self.start = start
                self.end = end

        fake_model = mock.MagicMock()
        fake_model.transcribe.return_value = (
            [
                _Segment(" primeira frase ", 0.0, 2.0),
                _Segment("", 2.0, 2.0),  # dropped
                _Segment("segunda frase", 1.5, 3.4),  # starts before previous end
            ],
            None,
        )
        with mock.patch(
            "video_generator.adapters.aligner.resolve_whisper_model",
            return_value="model-dir",
        ), mock.patch(
            "video_generator.adapters.aligner._load_whisper",
            return_value=lambda *a, **k: fake_model,
        ):
            segments = transcribe_segments(__file__)
        self.assertEqual([s.text for s in segments], ["primeira frase", "segunda frase"])
        self.assertTrue(all(isinstance(s, TranscribedSegment) for s in segments))
        self.assertGreaterEqual(segments[1].start_seconds, segments[0].end_seconds)


if __name__ == "__main__":
    unittest.main()
