"""`render_visual_effects` (punch-in zoom + freeze frame) and `composite_overlay`."""

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from video_generator.adapters import (
    FFmpegError,
    FreezeEvent,
    ZoomEvent,
    composite_overlay,
    render_visual_effects,
)


def _succeed(command, **kwargs):
    Path(command[-1]).write_bytes(b"fake encoded mp4")
    return subprocess.CompletedProcess(command, 0, "", "")


class RenderVisualEffectsTests(unittest.TestCase):
    def _render(self, *, zoom_events=(), freeze_events=(), duration=60.0, capture=None):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "cut_preview.mp4"
            source.write_bytes(b"source video bytes")
            output = Path(directory) / "visual_effects_preview.mp4"
            with patch(
                "video_generator.adapters.ffmpeg.resolve_media_tool", return_value="ffmpeg"
            ), patch(
                "video_generator.adapters.ffmpeg._probe_frame_rate", return_value="30/1"
            ), patch(
                "video_generator.adapters.ffmpeg._probe_duration_seconds", return_value=duration
            ), patch(
                "video_generator.adapters.ffmpeg._probe_audio_format",
                return_value=(48000, "stereo"),
            ), patch(
                "video_generator.adapters.ffmpeg._probe_video_height", return_value=1080
            ), patch(
                "video_generator.adapters.ffmpeg._probe_video_width", return_value=1920
            ), patch(
                "video_generator.adapters.ffmpeg.subprocess.run", side_effect=_succeed
            ) as execute:
                artifact = render_visual_effects(
                    source, output, zoom_events=zoom_events, freeze_events=freeze_events
                )
            if capture is not None:
                capture["command"] = execute.call_args.args[0]
                capture["source"] = source
                capture["output"] = output
            return artifact

    def test_zoom_only_builds_one_span_with_a_crop_expression(self):
        cap = {}
        artifact = self._render(
            zoom_events=[ZoomEvent(10.0, 12.0, scale=1.1)], capture=cap
        )
        graph = cap["command"][cap["command"].index("-filter_complex") + 1]
        self.assertIn("crop=", graph)
        self.assertIn("scale=1920:1080", graph)
        # split into before / zoomed / after around the [10, 12) window
        self.assertIn("concat=n=3:v=1:a=1", graph)
        self.assertEqual(artifact.zoom_count, 1)
        self.assertEqual(artifact.freeze_count, 0)
        self.assertAlmostEqual(artifact.duration_seconds, 60.0)

    def test_freeze_only_splits_into_three_pieces_and_holds_a_frame(self):
        cap = {}
        artifact = self._render(
            freeze_events=[FreezeEvent(20.0, freeze_seconds=1.0, label="boniteza zero")],
            capture=cap,
        )
        graph = cap["command"][cap["command"].index("-filter_complex") + 1]
        self.assertIn("tpad=stop_mode=clone", graph)
        self.assertIn("anullsrc=r=48000:cl=stereo:d=1", graph)
        self.assertIn("concat=n=3:v=1:a=1", graph)
        self.assertIn("drawtext=", graph)
        self.assertEqual(artifact.freeze_count, 1)
        self.assertAlmostEqual(artifact.duration_seconds, 61.0)

    def test_freeze_at_the_very_start_still_renders(self):
        cap = {}
        self._render(freeze_events=[FreezeEvent(0.0, freeze_seconds=0.5)], capture=cap)
        graph = cap["command"][cap["command"].index("-filter_complex") + 1]
        self.assertIn("anullsrc=r=48000:cl=stereo:d=0.5", graph)

    def test_overlapping_zooms_are_rejected(self):
        with self.assertRaises(FFmpegError):
            self._render(
                zoom_events=[ZoomEvent(10.0, 12.0, 1.1), ZoomEvent(11.0, 13.0, 1.1)]
            )

    def test_zoom_scale_out_of_range_is_rejected(self):
        with self.assertRaises(FFmpegError):
            self._render(zoom_events=[ZoomEvent(10.0, 12.0, scale=2.0)])

    def test_freeze_past_the_end_is_rejected(self):
        with self.assertRaises(FFmpegError):
            self._render(freeze_events=[FreezeEvent(65.0)], duration=60.0)

    def test_no_events_is_rejected(self):
        with self.assertRaises(FFmpegError):
            self._render()

    def test_zoom_without_probeable_dimensions_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "cut_preview.mp4"
            source.write_bytes(b"source video bytes")
            output = Path(directory) / "out.mp4"
            with patch(
                "video_generator.adapters.ffmpeg.resolve_media_tool", return_value="ffmpeg"
            ), patch(
                "video_generator.adapters.ffmpeg._probe_frame_rate", return_value="30/1"
            ), patch(
                "video_generator.adapters.ffmpeg._probe_duration_seconds", return_value=60.0
            ), patch(
                "video_generator.adapters.ffmpeg._probe_audio_format",
                return_value=(48000, "stereo"),
            ), patch(
                "video_generator.adapters.ffmpeg._probe_video_height", return_value=None
            ), patch(
                "video_generator.adapters.ffmpeg._probe_video_width", return_value=None
            ):
                with self.assertRaises(FFmpegError):
                    render_visual_effects(
                        source, output, zoom_events=[ZoomEvent(1.0, 2.0, 1.1)]
                    )

    def test_existing_output_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "cut_preview.mp4"
            source.write_bytes(b"source")
            output = Path(directory) / "out.mp4"
            output.write_bytes(b"already here")
            with patch(
                "video_generator.adapters.ffmpeg.resolve_media_tool", return_value="ffmpeg"
            ):
                with self.assertRaises(FFmpegError):
                    render_visual_effects(
                        source, output, zoom_events=[ZoomEvent(1.0, 2.0, 1.1)]
                    )


class CompositeOverlayTests(unittest.TestCase):
    def test_overlays_at_origin_and_keeps_base_audio(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / "base.mp4"
            base.write_bytes(b"base")
            overlay = Path(directory) / "overlay.mov"
            overlay.write_bytes(b"overlay")
            output = Path(directory) / "final.mp4"
            with patch(
                "video_generator.adapters.ffmpeg.resolve_media_tool", return_value="ffmpeg"
            ), patch(
                "video_generator.adapters.ffmpeg.subprocess.run", side_effect=_succeed
            ) as execute:
                artifact = composite_overlay(base, overlay, output)
            command = execute.call_args.args[0]
            graph = command[command.index("-filter_complex") + 1]
            self.assertEqual(graph, "[0:v][1:v]overlay=0:0:format=auto:eof_action=pass[outv]")
            self.assertEqual(command[command.index("-map") + 1], "[outv]")
            self.assertIn("0:a", command)
            self.assertEqual(artifact.output_path, str(output.resolve()))

    def test_missing_overlay_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / "base.mp4"
            base.write_bytes(b"base")
            with patch(
                "video_generator.adapters.ffmpeg.resolve_media_tool", return_value="ffmpeg"
            ):
                with self.assertRaises(FFmpegError):
                    composite_overlay(
                        base, Path(directory) / "missing.mov", Path(directory) / "out.mp4"
                    )


if __name__ == "__main__":
    unittest.main()
