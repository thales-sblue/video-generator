import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from video_generator.adapters import FFmpegError, extract_segment


class FFmpegAdapterTests(unittest.TestCase):
    def test_extracts_segment_with_structured_stream_copy_command(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            output = root / "nested" / "segment.mp4"
            source.write_bytes(b"immutable source")

            def run(command, **kwargs):
                Path(command[-1]).write_bytes(b"segment")
                return subprocess.CompletedProcess(command, 0, "", "")

            with patch("video_generator.adapters.ffmpeg.shutil.which", return_value="C:/tools/ffmpeg.exe"), patch(
                "video_generator.adapters.ffmpeg.subprocess.run", side_effect=run
            ) as execute:
                artifact = extract_segment(source, output, start_seconds=1.5, end_seconds=4)

            command = execute.call_args.args[0]
            self.assertEqual(command[:5], ["C:/tools/ffmpeg.exe", "-v", "error", "-nostdin", "-y"])
            self.assertEqual(command[command.index("-ss") + 1], "1.5")
            self.assertEqual(command[command.index("-t") + 1], "2.5")
            self.assertEqual(command[command.index("-c") + 1], "copy")
            self.assertFalse(execute.call_args.kwargs["shell"])
            self.assertEqual(source.read_bytes(), b"immutable source")
            self.assertEqual(output.read_bytes(), b"segment")
            self.assertEqual(artifact.file_size_bytes, 7)
            self.assertEqual(artifact.output_path, str(output.resolve()))

    def test_rejects_source_overwrite_existing_output_and_invalid_times(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.mp4"
            output = Path(directory) / "output.mp4"
            source.write_bytes(b"source")
            output.write_bytes(b"existing")

            with patch("video_generator.adapters.ffmpeg.subprocess.run") as execute:
                with self.assertRaisesRegex(FFmpegError, "must not overwrite"):
                    extract_segment(source, source, start_seconds=0, end_seconds=1)
                with self.assertRaisesRegex(FFmpegError, "already exists"):
                    extract_segment(source, output, start_seconds=0, end_seconds=1)
                with self.assertRaisesRegex(FFmpegError, "greater than"):
                    extract_segment(source, Path(directory) / "new.mp4", start_seconds=2, end_seconds=1)

            execute.assert_not_called()

    def test_reports_missing_tool_without_creating_output_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.mp4"
            output = Path(directory) / "new" / "segment.mp4"
            source.write_bytes(b"source")

            with patch("video_generator.adapters.ffmpeg.shutil.which", return_value=None):
                with self.assertRaisesRegex(FFmpegError, "not available"):
                    extract_segment(source, output, start_seconds=0, end_seconds=1)

            self.assertFalse(output.parent.exists())

    def test_removes_partial_artifact_when_ffmpeg_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            output = root / "output.mp4"
            source.write_bytes(b"source")

            def fail(command, **kwargs):
                Path(command[-1]).write_bytes(b"partial")
                return subprocess.CompletedProcess(command, 1, "", "bad input")

            with patch("video_generator.adapters.ffmpeg.shutil.which", return_value="ffmpeg"), patch(
                "video_generator.adapters.ffmpeg.subprocess.run", side_effect=fail
            ):
                with self.assertRaisesRegex(FFmpegError, "bad input"):
                    extract_segment(source, output, start_seconds=0, end_seconds=1)

            self.assertFalse(output.exists())
            self.assertEqual(list(root.glob(".*.mp4")), [])

    def test_rejects_empty_artifact_when_ffmpeg_reports_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            output = root / "output.mp4"
            source.write_bytes(b"source")

            with patch("video_generator.adapters.ffmpeg.shutil.which", return_value="ffmpeg"), patch(
                "video_generator.adapters.ffmpeg.subprocess.run",
                return_value=subprocess.CompletedProcess([], 0, "", ""),
            ):
                with self.assertRaisesRegex(FFmpegError, "non-empty artifact"):
                    extract_segment(source, output, start_seconds=0, end_seconds=1)

            self.assertFalse(output.exists())
            self.assertEqual(list(root.glob(".*.mp4")), [])

    def test_removes_reserved_artifact_after_timeout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            output = root / "output.mp4"
            source.write_bytes(b"source")

            with patch("video_generator.adapters.ffmpeg.shutil.which", return_value="ffmpeg"), patch(
                "video_generator.adapters.ffmpeg.subprocess.run",
                side_effect=subprocess.TimeoutExpired(["ffmpeg"], 1),
            ):
                with self.assertRaisesRegex(FFmpegError, "timed out"):
                    extract_segment(source, output, start_seconds=0, end_seconds=1, timeout_seconds=1)

            self.assertFalse(output.exists())
            self.assertEqual(list(root.glob(".*.mp4")), [])


if __name__ == "__main__":
    unittest.main()
