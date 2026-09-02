import unittest

from video_generator.subtitles import (
    CAPTION_CHUNK_MAX_CHARS,
    SubtitleParseError,
    align_cues_to_silences,
    captions_from_text,
    parse_subtitle_cues,
)


class CaptionsFromTextTests(unittest.TestCase):
    def test_produces_short_ordered_chunks_spanning_the_whole_duration(self):
        cues = captions_from_text(
            "Hello dark world. This is a longer second sentence with more words. End.",
            10.0,
        )
        self.assertGreaterEqual(len(cues), 2)
        self.assertEqual(cues[0][1], 0.0)
        # the last chunk ends exactly at the narration length: nothing lingers past it
        self.assertAlmostEqual(cues[-1][2], 10.0, places=6)
        prev = 0.0
        for text, start, end in cues:
            self.assertGreaterEqual(start, prev)
            self.assertGreater(end, start)
            self.assertLessEqual(len(text), CAPTION_CHUNK_MAX_CHARS)
            prev = end

    def test_breaks_long_sentences_at_clause_boundaries(self):
        cues = captions_from_text(
            "It is full of eyes that have learned not to shine, "
            "and every civilization made the same quiet discovery.",
            8.0,
        )
        self.assertTrue(all(len(text) <= CAPTION_CHUNK_MAX_CHARS for text, _, _ in cues))
        # the clause comma is a natural split point, so a chunk ends on it
        self.assertTrue(any(text.rstrip().endswith(",") for text, _, _ in cues))

    def test_timing_follows_speaking_time_not_character_count(self):
        # two chunks with similar character counts but very different syllable
        # load: the denser line must be given more of the timeline.
        cues = captions_from_text("Individualisation immediately. Go now sir.", 10.0)
        self.assertEqual(len(cues), 2)
        heavy = cues[0][2] - cues[0][1]
        light = cues[1][2] - cues[1][1]
        self.assertGreater(heavy, light)

    def test_wraps_a_run_on_sentence_with_no_punctuation(self):
        long_sentence = " ".join(["word"] * 60) + "."  # ~305 chars, no enders
        cues = captions_from_text(long_sentence, 12.0)
        self.assertGreater(len(cues), 1)
        self.assertTrue(all(len(text) <= CAPTION_CHUNK_MAX_CHARS for text, _, _ in cues))

    def test_does_not_end_a_chunk_on_a_trailing_function_word(self):
        # the two weak breaks flagged by the first real production's review:
        # "...olha para" | "o céu..." and "...só uma" | "sala...".
        narration = (
            "Toda civilização que olha para o céu chega à mesma conclusão: "
            "ser visto é ser um alvo. O céu que chamamos de calmo é só uma "
            "sala cheia de gente prendendo a respiração."
        )
        cues = captions_from_text(narration, 20.0)
        weak = {"para", "o", "a", "de", "que", "uma", "um", "e", "à"}
        for text, _, _ in cues:
            last = text.rstrip().rstrip(".,;:!?…").split()[-1].lower()
            self.assertNotIn(last, weak, f"chunk ends on a function word: {text!r}")
        self.assertTrue(all(len(text) <= CAPTION_CHUNK_MAX_CHARS for text, _, _ in cues))

    def test_keeps_a_trailing_function_word_when_the_next_chunk_is_full(self):
        # no room to shift: the layout must still be valid, not crash.
        narration = " ".join(["alpha"] * 9 + ["de"] + ["bravo"] * 9) + "."
        cues = captions_from_text(narration, 12.0)
        self.assertTrue(all(len(text) <= CAPTION_CHUNK_MAX_CHARS for text, _, _ in cues))
        self.assertAlmostEqual(cues[-1][2], 12.0, places=6)

    def test_rejects_empty_text_bad_duration_and_overpacking(self):
        with self.assertRaisesRegex(SubtitleParseError, "no caption-able content"):
            captions_from_text("   ", 5.0)
        with self.assertRaisesRegex(SubtitleParseError, "duration must be positive"):
            captions_from_text("Hi there.", 0)
        with self.assertRaisesRegex(SubtitleParseError, "duration must be positive"):
            captions_from_text("Hi there.", True)
        crowded = " ".join(
            ["This sentence is clearly longer than forty characters."] * 5
        )
        with self.assertRaisesRegex(SubtitleParseError, "more cues than its duration"):
            captions_from_text(crowded, 0.002)


class AlignCuesToSilencesTests(unittest.TestCase):
    def cues(self):
        return (
            ("First line.", 0.0, 1.6),
            ("Second line.", 1.6, 3.2),
            ("Third line.", 3.2, 5.0),
        )

    def test_pulls_a_boundary_onto_the_middle_of_a_measured_pause(self):
        aligned = align_cues_to_silences(self.cues(), ((1.9, 2.1),))

        # the estimate said 1.6; the voice actually stopped from 1.9 to 2.1, and
        # the line stays up across the pause instead of blinking out inside it
        self.assertEqual(aligned[0][2], 2.0)
        self.assertEqual(aligned[1][1], 2.0)
        # untouched boundaries and the outer edges keep their estimated times
        self.assertEqual(aligned[1][2], 3.2)
        self.assertEqual((aligned[0][1], aligned[-1][2]), (0.0, 5.0))
        self.assertEqual([cue[0] for cue in aligned], [cue[0] for cue in self.cues()])

    def test_ignores_pauses_beyond_the_tolerance_or_outside_the_cues(self):
        cues = self.cues()
        # 2.5 is 0.7 s from the nearest boundary, past the 0.5 s tolerance
        self.assertEqual(align_cues_to_silences(cues, ((2.4, 2.6),)), cues)
        self.assertEqual(align_cues_to_silences(cues, ()), cues)
        self.assertEqual(align_cues_to_silences(cues, ((5.2, 5.6),)), cues)

    def test_ignores_the_quiet_before_and_after_the_voice(self):
        cues = (("A", 0.0, 1.6), ("B", 1.6, 2.0))
        # the tail quiet of the recording reaches the end of the span: snapping
        # the break into it would leave the last line with almost no time
        self.assertEqual(align_cues_to_silences(cues, ((1.9, 2.0),)), cues)
        self.assertEqual(align_cues_to_silences(cues, ((0.0, 0.2),)), cues)

    def test_gives_a_pause_to_one_boundary_only(self):
        cues = (("A", 0.0, 1.6), ("B", 1.6, 2.0), ("C", 2.0, 4.0))
        # 1.8 sits the same distance from both boundaries: the earlier one takes
        # it and the other keeps its estimate rather than collapsing onto it
        aligned = align_cues_to_silences(cues, ((1.75, 1.85),))

        self.assertEqual(aligned[0][2], 1.8)
        self.assertEqual(aligned[1][2], 2.0)

    def test_refuses_a_pause_that_would_cross_a_neighbouring_boundary(self):
        cues = (("A", 0.0, 1.0), ("B", 1.0, 2.0), ("C", 2.0, 3.0))
        # both pauses precede the second boundary; taking the far one would put
        # it before the first, so that boundary keeps its estimated time
        aligned = align_cues_to_silences(
            cues, ((0.85, 0.95), (0.9, 1.0)), tolerance_seconds=1.5
        )

        self.assertEqual(aligned[0][2], 0.95)
        self.assertEqual(aligned[1][2], 2.0)
        starts = [cue[1] for cue in aligned]
        self.assertEqual(starts, sorted(starts))
        for _, start, end in aligned:
            self.assertGreater(end, start)

    def test_rejects_unusable_input(self):
        with self.assertRaisesRegex(SubtitleParseError, "at least one cue"):
            align_cues_to_silences((), ((1.0, 1.2),))
        with self.assertRaisesRegex(SubtitleParseError, "ordered, contiguous"):
            align_cues_to_silences((("A", 0.0, 1.0), ("B", 1.5, 2.0)), ((1.2, 1.3),))
        with self.assertRaisesRegex(SubtitleParseError, "tolerance must be positive"):
            align_cues_to_silences(self.cues(), ((1.9, 2.1),), tolerance_seconds=0)


class SubtitleParsingTests(unittest.TestCase):
    def test_parses_a_basic_srt_file(self):
        content = (
            "1\n00:00:00,000 --> 00:00:01,500\nFirst thought\n\n"
            "2\n00:00:01,500 --> 00:00:03,250\nSecond thought\n"
        )
        self.assertEqual(
            parse_subtitle_cues(content, source_format="srt"),
            (("First thought", 0.0, 1.5), ("Second thought", 1.5, 3.25)),
        )

    def test_joins_multiline_cue_text_and_tolerates_crlf(self):
        content = "1\r\n00:00:00,000 --> 00:00:02,000\r\nline one\r\nline two\r\n"
        self.assertEqual(
            parse_subtitle_cues(content, source_format="srt"),
            (("line one line two", 0.0, 2.0),),
        )

    def test_parses_webvtt_with_header_notes_and_short_timestamps(self):
        content = (
            "WEBVTT\n\nNOTE this block is ignored\n\n"
            "intro\n00:00.000 --> 00:01.000 align:start\nHello\n\n"
            "00:01.000 --> 00:02.500\nWorld\n"
        )
        self.assertEqual(
            parse_subtitle_cues(content, source_format="vtt"),
            (("Hello", 0.0, 1.0), ("World", 1.0, 2.5)),
        )

    def test_rejects_markup_bad_timing_and_empty_input(self):
        with self.assertRaisesRegex(SubtitleParseError, "markup"):
            parse_subtitle_cues(
                "1\n00:00:00,000 --> 00:00:01,000\n<b>bold</b>\n", source_format="srt"
            )
        with self.assertRaisesRegex(SubtitleParseError, "after its start"):
            parse_subtitle_cues(
                "1\n00:00:02,000 --> 00:00:01,000\ntext\n", source_format="srt"
            )
        with self.assertRaisesRegex(SubtitleParseError, "invalid timestamp"):
            parse_subtitle_cues("1\n0:0 --> 1:0\ntext\n", source_format="srt")
        with self.assertRaisesRegex(SubtitleParseError, "no cues"):
            parse_subtitle_cues("   \n\n", source_format="srt")
        with self.assertRaisesRegex(SubtitleParseError, "unsupported subtitle format"):
            parse_subtitle_cues("x", source_format="ass")

    def test_rejects_a_block_without_a_timing_line(self):
        with self.assertRaisesRegex(SubtitleParseError, "no '-->'"):
            parse_subtitle_cues("1\njust text, no arrow\n", source_format="srt")


if __name__ == "__main__":
    unittest.main()
