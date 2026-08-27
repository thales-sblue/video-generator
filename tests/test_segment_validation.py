import json
import unittest
from pathlib import Path

from video_generator.adapters import MediaProbe, ProbeError, SegmentArtifact, StreamProbe
from video_generator.validation import validate_segment_artifact


def artifact() -> SegmentArtifact:
    return SegmentArtifact(
        source_path=str(Path("inputs/source.mp4").resolve()),
        output_path=str(Path("output/segment.mp4").resolve()),
        start_seconds=0.5,
        end_seconds=1.5,
        file_size_bytes=100,
    )


def media_probe(*, duration: float | None = 1, size: int = 100, streams: bool = True) -> MediaProbe:
    return MediaProbe(
        source_path=artifact().output_path,
        file_size_bytes=size,
        format_name="mov,mp4",
        duration_seconds=duration,
        bit_rate_bps=800000,
        streams=(
            StreamProbe(0, "video", "h264", duration, 1920, 1080, None, None),
        )
        if streams
        else (),
    )


class SegmentValidationTests(unittest.TestCase):
    def test_accepts_non_empty_segment_with_duration_in_tolerance(self):
        report = validate_segment_artifact(artifact(), probe=lambda path: media_probe(duration=1.05))

        self.assertTrue(report.valid)
        self.assertEqual(report.issues, ())
        self.assertEqual(report.expected_duration_seconds, 1)
        self.assertEqual(json.loads(report.to_json())["actual_duration_seconds"], 1.05)

    def test_rejects_keyframe_duration_mismatch(self):
        report = validate_segment_artifact(artifact(), probe=lambda path: media_probe(duration=1.133333))

        self.assertFalse(report.valid)
        self.assertEqual(report.issues[0].code, "duration_mismatch")

    def test_rejects_changed_empty_or_unverifiable_output(self):
        changed = validate_segment_artifact(
            artifact(),
            probe=lambda path: media_probe(duration=None, size=50, streams=False),
        )

        def unavailable(path):
            raise ProbeError(f"source does not exist: {path}")

        missing = validate_segment_artifact(artifact(), probe=unavailable)

        self.assertEqual(
            {issue.code for issue in changed.issues},
            {"artifact_size_changed", "no_media_streams", "output_duration_unknown"},
        )
        self.assertEqual(missing.issues[0].code, "output_unavailable")
        self.assertFalse(changed.valid)
        self.assertFalse(missing.valid)

    def test_rejects_invalid_tolerance_before_probing(self):
        called = False

        def probe(path):
            nonlocal called
            called = True
            return media_probe()

        with self.assertRaises(ValueError):
            validate_segment_artifact(artifact(), duration_tolerance_seconds=float("nan"), probe=probe)

        self.assertFalse(called)


if __name__ == "__main__":
    unittest.main()
