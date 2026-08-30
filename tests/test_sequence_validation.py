import unittest
from pathlib import Path

from video_generator.adapters import MediaProbe, SequenceArtifact, StreamProbe
from video_generator.validation import validate_sequence_artifact


class SequenceValidationTests(unittest.TestCase):
    def test_accepts_exactly_one_h264_video_and_one_aac_narration_stream(self):
        output = str(Path("narrated.mp4").resolve())
        artifact = SequenceArtifact(
            ("a.mp4", "b.mp4"), output, 3.5, 2400, "narration.wav"
        )
        probe = MediaProbe(
            output,
            2400,
            "mov,mp4",
            3.5,
            1100000,
            (
                StreamProbe(0, "video", "h264", 3.5, 1280, 720, None, None),
                StreamProbe(1, "audio", "aac", 3.5, None, None, 48000, 2),
            ),
        )

        report = validate_sequence_artifact(artifact, probe=lambda _: probe)

        self.assertTrue(report.valid)
        self.assertEqual(report.issues, ())

    def test_rejects_missing_or_wrong_final_streams_for_narrated_output(self):
        output = str(Path("narrated.mp4").resolve())
        artifact = SequenceArtifact(
            ("a.mp4", "b.mp4"), output, 3.5, 2400, "narration.wav"
        )
        probe = MediaProbe(
            output,
            2400,
            "mov,mp4",
            3.5,
            1000000,
            (StreamProbe(0, "video", "vp9", 3.5, 1280, 720, None, None),),
        )

        report = validate_sequence_artifact(artifact, probe=lambda _: probe)

        self.assertEqual(
            {issue.code for issue in report.issues},
            {"unexpected_video_codec", "unexpected_audio_stream_count"},
        )

    def test_rejects_non_normalized_aac_narration(self):
        output = str(Path("narrated.mp4").resolve())
        artifact = SequenceArtifact(
            ("a.mp4", "b.mp4"), output, 3.5, 2400, "narration.wav"
        )
        probe = MediaProbe(
            output,
            2400,
            "mov,mp4",
            3.5,
            1000000,
            (
                StreamProbe(0, "video", "h264", 3.5, 1280, 720, None, None),
                StreamProbe(1, "audio", "aac", 3.5, None, None, 44100, 1),
            ),
        )

        report = validate_sequence_artifact(artifact, probe=lambda _: probe)

        self.assertEqual(
            {issue.code for issue in report.issues},
            {"unexpected_audio_sample_rate", "unexpected_audio_channel_count"},
        )

    def test_accepts_one_silent_video_stream_with_expected_duration(self):
        output = str(Path("timeline.mp4").resolve())
        artifact = SequenceArtifact(("a.mp4", "b.mp4"), output, 3.5, 2000)
        probe = MediaProbe(
            output,
            2000,
            "mov,mp4",
            3.48,
            1000000,
            (StreamProbe(0, "video", "h264", 3.48, 1280, 720, None, None),),
        )

        report = validate_sequence_artifact(artifact, probe=lambda _: probe)

        self.assertTrue(report.valid)
        self.assertEqual(report.issues, ())

    def test_rejects_duration_file_identity_and_stream_mismatches(self):
        output = str(Path("timeline.mp4").resolve())
        artifact = SequenceArtifact(("a.mp4", "b.mp4"), output, 3.5, 2000)
        probe = MediaProbe(
            output,
            1999,
            "mov,mp4",
            4.0,
            1000000,
            (
                StreamProbe(0, "video", "h264", 4.0, 1280, 720, None, None),
                StreamProbe(1, "audio", "aac", 4.0, None, None, 48000, 2),
            ),
        )

        report = validate_sequence_artifact(artifact, probe=lambda _: probe)

        self.assertFalse(report.valid)
        self.assertEqual(
            {issue.code for issue in report.issues},
            {"artifact_size_changed", "unexpected_non_video_streams", "duration_mismatch"},
        )


if __name__ == "__main__":
    unittest.main()
