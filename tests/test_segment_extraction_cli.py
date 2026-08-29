import contextlib
import io
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from video_generator.adapters import FFmpegError, SegmentArtifact
from video_generator.cli import main


class SegmentExtractionCliTests(unittest.TestCase):
    def test_extracts_segment_and_emits_reusable_metadata(self):
        source = str(Path("inputs/source.mp4").resolve())
        output = str(Path("output/segment.mp4").resolve())
        artifact = SegmentArtifact(source, output, 1.25, 4.5, 12345)
        stdout = io.StringIO()

        with patch(
            "video_generator.cli.extract_segment",
            return_value=artifact,
        ) as extract, contextlib.redirect_stdout(stdout):
            exit_code = main(
                [
                    "extract-segment",
                    source,
                    output,
                    "--start-seconds",
                    "1.25",
                    "--end-seconds",
                    "4.5",
                    "--timeout-seconds",
                    "30",
                    "--json",
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            json.loads(stdout.getvalue()),
            {
                "source_path": source,
                "output_path": output,
                "start_seconds": 1.25,
                "end_seconds": 4.5,
                "file_size_bytes": 12345,
                "mode": "copy",
            },
        )
        extract.assert_called_once_with(
            source,
            output,
            start_seconds=1.25,
            end_seconds=4.5,
            timeout_seconds=30,
            mode="copy",
        )

    def test_forwards_precise_mode(self):
        artifact = SegmentArtifact("source.mov", "segment.mp4", 0, 1, 100, "precise")
        stdout = io.StringIO()
        with patch(
            "video_generator.cli.extract_segment",
            return_value=artifact,
        ) as extract, contextlib.redirect_stdout(stdout):
            exit_code = main(
                [
                    "extract-segment",
                    "source.mov",
                    "segment.mp4",
                    "--start-seconds",
                    "0",
                    "--end-seconds",
                    "1",
                    "--mode",
                    "precise",
                    "--json",
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(extract.call_args.kwargs["mode"], "precise")

    def test_reports_adapter_failure_without_claiming_an_artifact(self):
        stdout = io.StringIO()
        stderr = io.StringIO()

        with patch(
            "video_generator.cli.extract_segment",
            side_effect=FFmpegError("output already exists: output/segment.mp4"),
        ), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exit_code = main(
                [
                    "extract-segment",
                    "inputs/source.mp4",
                    "output/segment.mp4",
                    "--start-seconds",
                    "0",
                    "--end-seconds",
                    "1",
                ]
            )

        self.assertEqual(exit_code, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("Extraction error: output already exists", stderr.getvalue())

    def test_human_output_distinguishes_source_and_new_artifact(self):
        artifact = SegmentArtifact("source.mp4", "segment.mp4", 0, 2, 500)
        stdout = io.StringIO()

        with patch(
            "video_generator.cli.extract_segment",
            return_value=artifact,
        ), contextlib.redirect_stdout(stdout):
            exit_code = main(
                [
                    "extract-segment",
                    "source.mp4",
                    "segment.mp4",
                    "--start-seconds",
                    "0",
                    "--end-seconds",
                    "2",
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertIn("Source: source.mp4", stdout.getvalue())
        self.assertIn("Artifact: segment.mp4", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
