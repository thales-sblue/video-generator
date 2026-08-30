import unittest
from pathlib import Path

from video_generator.adapters import MediaProbe, SequenceArtifact, StreamProbe
from video_generator.validation import validate_sequence_artifact


class SequenceValidationTests(unittest.TestCase):
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
