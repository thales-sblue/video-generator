import contextlib
import io
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from video_generator.adapters import AudioArtifact, MediaProbe, ProbeError, StreamProbe
from video_generator.cli import main
from video_generator.validation import validate_audio_artifact


def audio_probe(path: str = "audio.wav") -> MediaProbe:
    return MediaProbe(
        source_path=str(Path(path).resolve()),
        file_size_bytes=1234,
        format_name="wav",
        duration_seconds=2.0,
        bit_rate_bps=1536000,
        streams=(StreamProbe(0, "audio", "pcm_s16le", 2.0, None, None, 48000, 2),),
    )


class AudioValidationTests(unittest.TestCase):
    def test_accepts_the_recorded_pcm_wav_shape(self):
        artifact = AudioArtifact("source.mp4", str(Path("audio.wav").resolve()), 48000, 2, 1234)

        report = validate_audio_artifact(artifact, probe=lambda _: audio_probe())

        self.assertTrue(report.valid)
        self.assertEqual(report.issues, ())

    def test_rejects_changed_or_unexpected_audio(self):
        artifact = AudioArtifact("source.mp4", str(Path("audio.wav").resolve()), 48000, 2, 1234)
        bad_probe = MediaProbe(
            source_path=artifact.output_path,
            file_size_bytes=1200,
            format_name="mp3",
            duration_seconds=0,
            bit_rate_bps=None,
            streams=(StreamProbe(0, "audio", "mp3", 0, None, None, 44100, 1),),
        )

        report = validate_audio_artifact(artifact, probe=lambda _: bad_probe)

        self.assertFalse(report.valid)
        self.assertEqual(
            {issue.code for issue in report.issues},
            {
                "artifact_size_changed",
                "unexpected_container",
                "unexpected_audio_codec",
                "sample_rate_mismatch",
                "channel_count_mismatch",
                "invalid_audio_duration",
            },
        )

    def test_reports_an_unavailable_output(self):
        artifact = AudioArtifact("source.mp4", str(Path("audio.wav").resolve()), 48000, 2, 1234)

        def fail(_):
            raise ProbeError("missing output")

        report = validate_audio_artifact(artifact, probe=fail)

        self.assertFalse(report.valid)
        self.assertEqual(report.issues[0].code, "output_unavailable")

    def test_cli_emits_a_machine_readable_report(self):
        stdout = io.StringIO()
        with patch("video_generator.cli.validate_audio_artifact") as validate, \
                contextlib.redirect_stdout(stdout):
            artifact = AudioArtifact(
                str(Path("source.mp4").resolve()),
                str(Path("audio.wav").resolve()),
                48000,
                2,
                1234,
            )
            validate.return_value = validate_audio_artifact(
                artifact,
                probe=lambda _: audio_probe(),
            )
            exit_code = main(
                [
                    "validate-audio",
                    "audio.wav",
                    "--source",
                    "source.mp4",
                    "--file-size-bytes",
                    "1234",
                    "--json",
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertTrue(json.loads(stdout.getvalue())["valid"])
        validate.assert_called_once_with(artifact)


if __name__ == "__main__":
    unittest.main()
