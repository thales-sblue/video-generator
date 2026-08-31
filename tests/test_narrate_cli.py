import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from video_generator.adapters import KokoroError, NarrationArtifact
from video_generator.cli import main


def _artifact(output="narration.wav"):
    return NarrationArtifact(
        output_path=output,
        voice="af_heart",
        speed=1.0,
        lang="en-us",
        sample_rate_hz=48000,
        channels=2,
        text_sha256="a" * 64,
        file_size_bytes=4321,
    )


class NarrateCliTests(unittest.TestCase):
    def test_synthesises_from_inline_text_and_emits_reusable_metadata(self):
        stdout = io.StringIO()
        with patch(
            "video_generator.cli.synthesize_narration", return_value=_artifact()
        ) as synth, contextlib.redirect_stdout(stdout):
            exit_code = main(
                ["narrate", "narration.wav", "--text", "Hello.", "--voice", "am_adam",
                 "--speed", "1.1", "--lang", "en-gb", "--json"]
            )

        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["text_sha256"], "a" * 64)
        self.assertEqual(payload["sample_rate_hz"], 48000)
        synth.assert_called_once_with(
            "Hello.",
            "narration.wav",
            voice="am_adam",
            speed=1.1,
            lang="en-gb",
            timeout_seconds=300,
        )

    def test_reads_text_from_a_utf8_file(self):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "script.txt"
            script.write_text("Longer narration body.", encoding="utf-8")
            with patch(
                "video_generator.cli.synthesize_narration", return_value=_artifact()
            ) as synth, contextlib.redirect_stdout(io.StringIO()):
                exit_code = main(["narrate", "out.wav", "--text-file", str(script)])

        self.assertEqual(exit_code, 0)
        self.assertEqual(synth.call_args.args[0], "Longer narration body.")

    def test_requires_exactly_one_text_source(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit):
            main(["narrate", "out.wav"])
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            main(["narrate", "out.wav", "--text", "x", "--text-file", "y.txt"])

    def test_reports_a_kokoro_failure_without_claiming_an_artifact(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch(
            "video_generator.cli.synthesize_narration",
            side_effect=KokoroError("Kokoro model files not found under .local-tools/kokoro/"),
        ), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exit_code = main(["narrate", "out.wav", "--text", "hi"])

        self.assertEqual(exit_code, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("Narration error: Kokoro model files not found", stderr.getvalue())

    def test_human_output_marks_auditory_review_pending(self):
        stdout = io.StringIO()
        with patch(
            "video_generator.cli.synthesize_narration", return_value=_artifact()
        ), contextlib.redirect_stdout(stdout):
            exit_code = main(["narrate", "narration.wav", "--text", "hi"])

        self.assertEqual(exit_code, 0)
        self.assertIn("Auditory review: not_performed", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
