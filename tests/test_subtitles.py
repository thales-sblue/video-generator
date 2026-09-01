import unittest

from video_generator.subtitles import (
    CAPTION_CHUNK_MAX_CHARS,
    SubtitleParseError,
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
