"""`align-captions`: measure a rendered narration, write cues that match it."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from video_generator.adapters import AlignerError, MediaProbe, WordTiming
from video_generator.cli import main


SCRIPT = "Alfa bravo charlie. Delta echo foxtrot."


def _probe(seconds=4.0):
    return MediaProbe(
        source_path="narration.wav",
        file_size_bytes=1234,
        format_name="wav",
        duration_seconds=seconds,
        bit_rate_bps=None,
        streams=(),
    )


def _words():
    return tuple(
        WordTiming(text=word, start_seconds=index * 0.5, end_seconds=index * 0.5 + 0.4)
        for index, word in enumerate(
            ["alfa", "bravo", "charlie", "delta", "echo", "foxtrot"]
        )
    )


class AlignCaptionsCliTests(unittest.TestCase):
    def _run(self, directory, extra=(), silences=(), words=None):
        out = Path(directory) / "captions.srt"
        stdout = io.StringIO()
        with patch("video_generator.cli.probe_media", return_value=_probe()), patch(
            "video_generator.cli.detect_silences", return_value=silences
        ), patch(
            "video_generator.cli.transcribe_words",
            return_value=_words() if words is None else words,
        ), contextlib.redirect_stdout(stdout):
            code = main(
                [
                    "align-captions", "narration.wav",
                    "--text", SCRIPT,
                    "--out", str(out),
                    "--json",
                    *extra,
                ]
            )
        return code, out, stdout.getvalue()

    def test_writes_an_srt_timed_to_the_measured_words(self):
        with tempfile.TemporaryDirectory() as directory:
            code, out, output = self._run(directory)
            self.assertEqual(code, 0)
            payload = json.loads(output)
            self.assertEqual(payload["measured_words"], 6)
            self.assertEqual(payload["cue_count"], 2)
            self.assertEqual(payload["audio_seconds"], 4.0)
            body = out.read_text(encoding="utf-8")
            self.assertIn("00:00:00,000 --> ", body)
            self.assertIn("Alfa bravo charlie.", body)

    def test_never_starts_a_cue_before_the_voice_does(self):
        with tempfile.TemporaryDirectory() as directory:
            # the decoder claims word one starts at 0.0; the take opens on 0.6 s
            # of measured silence, and the first cue must respect that
            code, _out, output = self._run(directory, silences=((0.0, 0.6),))
            self.assertEqual(code, 0)
            payload = json.loads(output)
            self.assertEqual(payload["voice_start_seconds"], 0.6)
            self.assertGreaterEqual(payload["first_cue_start_seconds"], 0.6)

    def test_ignores_a_silence_that_is_not_at_the_head(self):
        with tempfile.TemporaryDirectory() as directory:
            code, _out, output = self._run(directory, silences=((1.4, 2.0),))
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output)["voice_start_seconds"], 0.0)

    def test_also_persists_the_raw_word_timings_when_asked(self):
        with tempfile.TemporaryDirectory() as directory:
            words_path = Path(directory) / "words.json"
            code, _out, _ = self._run(directory, extra=["--words-out", str(words_path)])
            self.assertEqual(code, 0)
            payload = json.loads(words_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema_version"], 1)
            self.assertEqual(len(payload["words"]), 6)
            self.assertEqual(payload["words"][0]["text"], "alfa")

    def test_refuses_a_non_srt_or_existing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            taken = Path(directory) / "captions.srt"
            taken.write_text("x", encoding="utf-8")
            for out in (str(Path(directory) / "captions.vtt"), str(taken)):
                with self.subTest(out=out):
                    stderr = io.StringIO()
                    with contextlib.redirect_stderr(stderr):
                        code = main(
                            ["align-captions", "narration.wav", "--text", SCRIPT, "--out", out]
                        )
                    self.assertEqual(code, 2)

    def test_reports_a_missing_aligner_without_writing_anything(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "captions.srt"
            stderr = io.StringIO()
            with patch("video_generator.cli.probe_media", return_value=_probe()), patch(
                "video_generator.cli.transcribe_words",
                side_effect=AlignerError("Whisper model files not found"),
            ), contextlib.redirect_stderr(stderr):
                code = main(
                    ["align-captions", "narration.wav", "--text", SCRIPT, "--out", str(out)]
                )
            self.assertEqual(code, 2)
            self.assertIn("Whisper model files not found", stderr.getvalue())
            self.assertFalse(out.exists())

    def test_reports_a_narration_with_no_usable_duration(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "captions.srt"
            stderr = io.StringIO()
            with patch(
                "video_generator.cli.probe_media", return_value=_probe(seconds=None)
            ), contextlib.redirect_stderr(stderr):
                code = main(
                    ["align-captions", "narration.wav", "--text", SCRIPT, "--out", str(out)]
                )
            self.assertEqual(code, 2)
            self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main()
