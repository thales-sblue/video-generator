import unittest

from video_generator.subtitles import SubtitleParseError, parse_subtitle_cues


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
