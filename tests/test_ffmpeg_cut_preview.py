"""`render_cut_preview`: one filter_complex pass that concatenates the KEEP ranges."""

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from video_generator.adapters import FFmpegError, render_cut_preview


def _succeed(command, **kwargs):
    Path(command[-1]).write_bytes(b"fake encoded mp4")
    return subprocess.CompletedProcess(command, 0, "", "")


class RenderCutPreviewTests(unittest.TestCase):
    def _render(self, keeps, capture=None):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "original.mp4"
            source.write_bytes(b"source video bytes")
            output = Path(directory) / "original_edited_preview.mp4"
            with patch(
                "video_generator.adapters.ffmpeg.resolve_media_tool", return_value="ffmpeg"
            ), patch(
                "video_generator.adapters.ffmpeg._probe_frame_rate",
                return_value="30000/1001",
            ), patch(
                "video_generator.adapters.ffmpeg.subprocess.run", side_effect=_succeed
            ) as execute:
                artifact = render_cut_preview(source, output, keeps)
            if capture is not None:
                capture["command"] = execute.call_args.args[0]
                capture["source"] = source
                capture["output"] = output
            return artifact

    def test_builds_the_concat_graph_at_the_source_frame_rate(self):
        cap = {}
        artifact = self._render([(0.0, 10.0), (20.0, 30.0), (40.0, 60.0)], cap)
        command = cap["command"]
        self.assertEqual(command[command.index("-i") + 1], str(cap["source"].resolve()))
        graph = command[command.index("-filter_complex") + 1]
        self.assertEqual(graph.count("[0:v]trim=start="), 3)
        self.assertEqual(graph.count("[0:a]atrim=start="), 3)
        self.assertIn("concat=n=3:v=1:a=1[outv][outa]", graph)
        self.assertIn("fps=30000/1001", graph)
        self.assertEqual(command[command.index("-r") + 1], "30000/1001")
        self.assertIn("cfr", command)
        self.assertEqual(command[command.index("-c:v") + 1], "libopenh264")
        self.assertEqual(command[command.index("-c:a") + 1], "aac")
        self.assertEqual(command[command.index("-map") + 1], "[outv]")
        self.assertEqual(artifact.segment_count, 3)
        self.assertEqual(artifact.frame_rate, "30000/1001")
        self.assertAlmostEqual(artifact.kept_seconds, 40.0)

    def test_audio_microfade_only_on_internal_joins_never_the_outer_edges(self):
        cap = {}
        self._render([(0.0, 10.0), (20.0, 30.0), (40.0, 60.0)], cap)
        graph = cap["command"][cap["command"].index("-filter_complex") + 1]
        pieces = graph.split(";")
        first_audio = next(p for p in pieces if "[a0]" in p)
        middle_audio = next(p for p in pieces if "[a1]" in p)
        last_audio = next(p for p in pieces if "[a2]" in p)
        self.assertNotIn("afade=t=in", first_audio)   # nothing before the first piece
        self.assertIn("afade=t=out", first_audio)     # a join follows it
        self.assertIn("afade=t=in", middle_audio)
        self.assertIn("afade=t=out", middle_audio)
        self.assertIn("afade=t=in", last_audio)
        self.assertNotIn("afade=t=out", last_audio)   # nothing after the last piece
        # no video fade anywhere
        self.assertNotIn("fade=t=", graph.replace("afade=t=", ""))

    def test_single_keep_interval_has_no_fades_and_still_concats(self):
        cap = {}
        self._render([(5.0, 25.0)], cap)
        graph = cap["command"][cap["command"].index("-filter_complex") + 1]
        self.assertIn("concat=n=1:v=1:a=1", graph)
        self.assertNotIn("afade", graph)

    def test_rejects_bad_intervals_and_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "v.mp4"
            source.write_bytes(b"x")
            with patch(
                "video_generator.adapters.ffmpeg.resolve_media_tool", return_value="ffmpeg"
            ):
                with self.assertRaises(FFmpegError):
                    render_cut_preview(source, source, [(0.0, 1.0)])  # overwrites source
                with self.assertRaises(FFmpegError):
                    render_cut_preview(source, Path(directory) / "o.mkv", [(0.0, 1.0)])
                with self.assertRaises(FFmpegError):
                    render_cut_preview(source, Path(directory) / "o.mp4", [])
                with self.assertRaises(FFmpegError):
                    render_cut_preview(
                        source, Path(directory) / "o.mp4", [(10.0, 5.0)]
                    )
                with self.assertRaises(FFmpegError):
                    render_cut_preview(
                        source, Path(directory) / "o.mp4", [(0.0, 10.0), (5.0, 15.0)]
                    )

    def test_refuses_an_existing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "v.mp4"
            source.write_bytes(b"x")
            output = Path(directory) / "o.mp4"
            output.write_bytes(b"already here")
            with patch(
                "video_generator.adapters.ffmpeg.resolve_media_tool", return_value="ffmpeg"
            ):
                with self.assertRaises(FFmpegError):
                    render_cut_preview(source, output, [(0.0, 1.0)])


if __name__ == "__main__":
    unittest.main()
