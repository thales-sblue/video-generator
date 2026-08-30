import contextlib
import io
import json
import unittest
from unittest.mock import patch

from video_generator.adapters import AudioArtifact, FFmpegError
from video_generator.cli import main


class AudioExtractionCliTests(unittest.TestCase):
    def test_extracts_audio_and_emits_reusable_metadata(self):
        artifact = AudioArtifact("source.mp4", "audio.wav", 48000, 2, 1234)
        stdout = io.StringIO()

        with patch("video_generator.cli.extract_audio", return_value=artifact) as extract, \
                contextlib.redirect_stdout(stdout):
            exit_code = main(
                ["extract-audio", "source.mp4", "audio.wav", "--timeout-seconds", "30", "--json"]
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            json.loads(stdout.getvalue()),
            {
                "source_path": "source.mp4",
                "output_path": "audio.wav",
                "sample_rate_hz": 48000,
                "channels": 2,
                "file_size_bytes": 1234,
            },
        )
        extract.assert_called_once_with("source.mp4", "audio.wav", timeout_seconds=30)

    def test_reports_failure_without_claiming_an_artifact(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch(
            "video_generator.cli.extract_audio",
            side_effect=FFmpegError("source has no audio stream"),
        ), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exit_code = main(["extract-audio", "source.mp4", "audio.wav"])

        self.assertEqual(exit_code, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("Audio extraction error: source has no audio stream", stderr.getvalue())

    def test_human_output_describes_the_pcm_artifact(self):
        artifact = AudioArtifact("source.mp4", "audio.wav", 48000, 2, 1234)
        stdout = io.StringIO()
        with patch("video_generator.cli.extract_audio", return_value=artifact), \
                contextlib.redirect_stdout(stdout):
            exit_code = main(["extract-audio", "source.mp4", "audio.wav"])

        self.assertEqual(exit_code, 0)
        self.assertIn("Artifact: audio.wav", stdout.getvalue())
        self.assertIn("PCM 16-bit; 48000 Hz; 2 channels", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
