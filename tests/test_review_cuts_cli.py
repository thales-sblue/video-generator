"""`review-cuts`: raw take -> transcription -> suggested (never made) cuts."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from video_generator.adapters import AlignerError, MediaProbe, TranscribedSegment
from video_generator.cli import main


def _probe(seconds=12.0):
    return MediaProbe(
        source_path="take.mp4",
        file_size_bytes=4321,
        format_name="mov,mp4",
        duration_seconds=seconds,
        bit_rate_bps=None,
        streams=(),
    )


def _segments():
    spans = [
        ("Bem-vindo, hoje eu mostro o projeto.", 0.0, 3.0),
        ("Então o argparse recebe o subcomando.", 3.0, 6.0),
        ("Então o argparse recebe o subcomando.", 6.5, 9.5),
        ("E ele despacha para o runner certo.", 9.5, 12.0),
    ]
    return tuple(
        TranscribedSegment(text=t, start_seconds=s, end_seconds=e) for t, s, e in spans
    )


class ReviewCutsCliTests(unittest.TestCase):
    def _run(self, directory, extra=(), segments=None, silences=()):
        stdout = io.StringIO()
        with patch("video_generator.cli.probe_media", return_value=_probe()), patch(
            "video_generator.cli.extract_audio", return_value=None
        ), patch(
            "video_generator.cli.transcribe_segments",
            return_value=_segments() if segments is None else segments,
        ), patch(
            "video_generator.cli.detect_silences", return_value=silences
        ), contextlib.redirect_stdout(stdout):
            code = main(
                [
                    "review-cuts",
                    __file__,
                    "--out-dir",
                    str(directory),
                    "--json",
                    *extra,
                ]
            )
        return code, stdout.getvalue()

    def test_writes_json_and_markdown_and_flags_the_repeat(self):
        with tempfile.TemporaryDirectory() as directory:
            code, output = self._run(directory)
            self.assertEqual(code, 0)
            summary = json.loads(output)
            review = json.loads(
                Path(summary["review_path"]).read_text(encoding="utf-8")
            )
            self.assertEqual(review["schema_version"], 1)
            self.assertEqual(review["language"], "pt")
            self.assertEqual(len(review["segments"]), 4)
            suggestions = [s["suggestion"] for s in review["segments"]]
            self.assertEqual(suggestions[1], "CUT")  # the earlier of the two takes
            self.assertEqual(suggestions[2], "KEEP")
            markdown = Path(summary["markdown_path"]).read_text(encoding="utf-8")
            self.assertIn("# Revisão de cortes", markdown)
            self.assertIn("Motivo:", markdown)
            self.assertEqual(summary["cut"], 1)

    def test_long_silence_from_ffmpeg_becomes_a_cut_row(self):
        with tempfile.TemporaryDirectory() as directory:
            code, output = self._run(directory, silences=((3.0, 5.0),))
            self.assertEqual(code, 0)
            summary = json.loads(output)
            review = json.loads(
                Path(summary["review_path"]).read_text(encoding="utf-8")
            )
            silence_rows = [s for s in review["segments"] if "silêncio" in s["text"]]
            self.assertEqual(len(silence_rows), 1)
            self.assertEqual(silence_rows[0]["suggestion"], "CUT")

    def test_refuses_to_overwrite_an_existing_review(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "cut-review.json").write_text("{}", encoding="utf-8")
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                code, _ = self._run(directory)
            self.assertEqual(code, 2)
            self.assertIn("already exists", stderr.getvalue())

    def test_reports_a_missing_whisper_model(self):
        with tempfile.TemporaryDirectory() as directory:
            stderr = io.StringIO()
            stdout = io.StringIO()
            with patch(
                "video_generator.cli.probe_media", return_value=_probe()
            ), patch(
                "video_generator.cli.extract_audio", return_value=None
            ), patch(
                "video_generator.cli.transcribe_segments",
                side_effect=AlignerError("Whisper model files not found"),
            ), contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(stdout):
                code = main(
                    ["review-cuts", __file__, "--out-dir", str(directory)]
                )
            self.assertEqual(code, 2)
            self.assertIn("Whisper model files not found", stderr.getvalue())
            self.assertFalse((Path(directory) / "cut-review.json").exists())

    def test_rejects_a_missing_video(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = main(
                ["review-cuts", "nope.mp4", "--out-dir", "whatever"]
            )
        self.assertEqual(code, 2)
        self.assertIn("does not exist", stderr.getvalue())

    def test_rejects_a_bad_project_slug(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = main(["review-cuts", __file__, "--project", "../escape"])
        self.assertEqual(code, 2)
        self.assertIn("invalid project slug", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
