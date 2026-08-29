import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from video_generator.adapters.ffprobe import ProbeError, probe_media
from video_generator.cli import main


FFPROBE_PAYLOAD = {
    "format": {
        "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
        "duration": "12.500000",
        "bit_rate": "800000",
    },
    "streams": [
        {
            "index": 0,
            "codec_type": "video",
            "codec_name": "h264",
            "duration": "12.5",
            "width": 1920,
            "height": 1080,
        },
        {
            "index": 1,
            "codec_type": "audio",
            "codec_name": "aac",
            "sample_rate": "48000",
            "channels": 2,
        },
    ],
}


class FFprobeAdapterTests(unittest.TestCase):
    def test_inspects_file_with_structured_read_only_command(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            source = Path(temporary_directory) / "source clip.mp4"
            source.write_bytes(b"immutable-media")
            completed = subprocess.CompletedProcess(
                args=[], returncode=0, stdout=json.dumps(FFPROBE_PAYLOAD), stderr=""
            )
            with patch("video_generator.adapters.ffprobe.resolve_media_tool", return_value="ffprobe"), patch(
                "video_generator.adapters.ffprobe.subprocess.run", return_value=completed
            ) as run:
                result = probe_media(source)
                self.assertEqual(result.file_size_bytes, 15)
                self.assertEqual(result.duration_seconds, 12.5)
                self.assertEqual(result.streams[0].width, 1920)
                self.assertEqual(result.streams[1].sample_rate_hz, 48000)
                command = run.call_args.args[0]
                self.assertEqual(
                    command[:7],
                    ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json"],
                )
                self.assertEqual(Path(command[-1]), source.resolve())
                self.assertFalse(run.call_args.kwargs["shell"])
                self.assertEqual(source.read_bytes(), b"immutable-media")

    def test_rejects_missing_source_before_starting_subprocess(self):
        with patch("video_generator.adapters.ffprobe.subprocess.run") as run:
            with self.assertRaisesRegex(ProbeError, "does not exist"):
                probe_media("missing.mp4")
        run.assert_not_called()

    def test_reports_missing_tool_and_invalid_probe_output(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            source = Path(temporary_directory) / "source.mp4"
            source.touch()
            with patch("video_generator.adapters.ffprobe.resolve_media_tool", return_value=None):
                with self.assertRaisesRegex(ProbeError, "not available"):
                    probe_media(source)

            completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="not-json", stderr="")
            with patch("video_generator.adapters.ffprobe.resolve_media_tool", return_value="ffprobe"), patch(
                "video_generator.adapters.ffprobe.subprocess.run", return_value=completed
            ):
                with self.assertRaisesRegex(ProbeError, "invalid JSON"):
                    probe_media(source)

    def test_rejects_invalid_timeout_before_starting_subprocess(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            source = Path(temporary_directory) / "source.mp4"
            source.touch()
            with patch("video_generator.adapters.ffprobe.subprocess.run") as run:
                with self.assertRaisesRegex(ProbeError, "timeout_seconds"):
                    probe_media(source, timeout_seconds="30")  # type: ignore[arg-type]
        run.assert_not_called()

    def test_cli_emits_normalized_json(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            source = Path(temporary_directory) / "source.mp4"
            source.write_bytes(b"media")
            completed = subprocess.CompletedProcess(
                args=[], returncode=0, stdout=json.dumps(FFPROBE_PAYLOAD), stderr=""
            )
            stdout = io.StringIO()
            with patch("video_generator.adapters.ffprobe.resolve_media_tool", return_value="ffprobe"), patch(
                "video_generator.adapters.ffprobe.subprocess.run", return_value=completed
            ), contextlib.redirect_stdout(stdout):
                exit_code = main(["inspect", str(source), "--json"])

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["format_name"], "mov,mp4,m4a,3gp,3g2,mj2")
        self.assertEqual(len(payload["streams"]), 2)


if __name__ == "__main__":
    unittest.main()
