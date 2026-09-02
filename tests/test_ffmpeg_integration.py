"""Real FFmpeg composition checks that run only against the locked toolchain.

These exercise ``compose_video_sequence`` end to end with an explicit
``TargetFormat`` so heterogeneous sources are proven to normalise onto the
requested canvas. They never fall back to a system FFmpeg/ffprobe: if the
hash-locked install under ``.local-tools/ffmpeg`` is absent the whole module
is skipped. Fixtures are tiny lavfi renders at reduced resolutions so the
suite stays fast; verification is metadata only (no pixel comparison).
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from video_generator.adapters import FFmpegError, SequenceClip, SequenceImage, compose_video_sequence
from video_generator.tooling import ToolResolutionError, _validated_local_bin


try:
    _LOCKED_BIN = _validated_local_bin()
except ToolResolutionError as exc:  # pragma: no cover - depends on local install
    raise unittest.SkipTest(f"locked FFmpeg failed its integrity lock: {exc}")

if _LOCKED_BIN is None:  # pragma: no cover - depends on local install
    raise unittest.SkipTest("locked FFmpeg toolchain is not installed under .local-tools")

_FFMPEG = _LOCKED_BIN / "ffmpeg.exe"
_FFPROBE = _LOCKED_BIN / "ffprobe.exe"


def _run(*args: str) -> None:
    completed = subprocess.run(
        [str(_FFMPEG), "-v", "error", "-nostdin", "-y", *args],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if completed.returncode != 0:
        raise unittest.SkipTest(f"locked ffmpeg could not build a fixture: {completed.stderr.strip()}")


def _make_clip(path: Path, *, size: str, seconds: float, colour: str) -> Path:
    _run(
        "-f", "lavfi", "-i", f"color=c={colour}:s={size}:r=30:d={seconds}",
        "-c:v", "libopenh264", "-b:v", "2M", "-pix_fmt", "yuv420p", str(path),
    )
    return path


def _make_image(path: Path, *, size: str, colour: str) -> Path:
    _run("-f", "lavfi", "-i", f"color=c={colour}:s={size}:d=1", "-frames:v", "1", str(path))
    return path


def _make_audio(path: Path, *, seconds: float, freq: int, channels: int) -> Path:
    _run(
        "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}",
        "-ac", str(channels), str(path),
    )
    return path


def _probe(path: Path) -> dict:
    completed = subprocess.run(
        [
            str(_FFPROBE), "-v", "error", "-show_streams", "-show_format",
            "-of", "json", str(path),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(f"locked ffprobe failed: {completed.stderr.strip()}")
    return json.loads(completed.stdout)


class FFmpegTargetFormatIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _assert_video(self, path: Path, width: int, height: int, *, expect_audio: bool) -> dict:
        self.assertTrue(path.is_file())
        self.assertGreater(path.stat().st_size, 0)
        info = _probe(path)
        streams = info["streams"]
        video = [s for s in streams if s["codec_type"] == "video"]
        audio = [s for s in streams if s["codec_type"] == "audio"]
        self.assertEqual(len(video), 1)
        self.assertEqual(video[0]["codec_name"], "h264")
        self.assertEqual((video[0]["width"], video[0]["height"]), (width, height))
        self.assertEqual(str(video[0].get("sample_aspect_ratio", "1:1")) in ("1:1", "N/A"), True)
        if expect_audio:
            self.assertEqual(len(audio), 1)
            self.assertEqual(audio[0]["codec_name"], "aac")
            self.assertEqual(int(audio[0]["channels"]), 2)
        else:
            self.assertEqual(len(audio), 0)
        return info

    def test_landscape_contain_with_differently_shaped_sources(self):
        a = _make_clip(self.root / "a.mp4", size="640x360", seconds=1.0, colour="red")
        b = _make_clip(self.root / "b.mp4", size="480x480", seconds=1.0, colour="green")
        out = self.root / "out.mp4"
        compose_video_sequence(
            (SequenceClip(str(a), 0, 1.0, fit="contain"), SequenceClip(str(b), 0, 1.0, fit="contain")),
            out,
            canvas=(640, 360),
        )
        info = self._assert_video(out, 640, 360, expect_audio=False)
        self.assertAlmostEqual(float(info["format"]["duration"]), 2.0, delta=0.5)

    def test_portrait_cover_with_differently_shaped_sources(self):
        a = _make_clip(self.root / "a.mp4", size="640x360", seconds=1.0, colour="red")
        b = _make_clip(self.root / "b.mp4", size="480x480", seconds=1.0, colour="blue")
        out = self.root / "out.mp4"
        compose_video_sequence(
            (SequenceClip(str(a), 0, 1.0, fit="cover"), SequenceClip(str(b), 0, 1.0, fit="cover")),
            out,
            canvas=(360, 640),
        )
        self._assert_video(out, 360, 640, expect_audio=False)

    def test_portrait_contain_with_a_clip_and_an_image(self):
        a = _make_clip(self.root / "a.mp4", size="640x360", seconds=1.0, colour="red")
        card = _make_image(self.root / "card.png", size="500x500", colour="white")
        out = self.root / "out.mp4"
        compose_video_sequence(
            (SequenceClip(str(a), 0, 1.0, fit="contain"), SequenceImage(str(card), 1.0, fit="contain")),
            out,
            canvas=(360, 640),
        )
        self._assert_video(out, 360, 640, expect_audio=False)

    def test_portrait_cover_with_a_clip_and_an_image(self):
        a = _make_clip(self.root / "a.mp4", size="640x360", seconds=1.0, colour="red")
        card = _make_image(self.root / "card.png", size="800x400", colour="yellow")
        out = self.root / "out.mp4"
        compose_video_sequence(
            (SequenceClip(str(a), 0, 1.0, fit="cover"), SequenceImage(str(card), 1.0, fit="cover")),
            out,
            canvas=(360, 640),
        )
        self._assert_video(out, 360, 640, expect_audio=False)

    def test_still_with_ken_burns_zoom_renders_to_the_canvas(self):
        a = _make_clip(self.root / "a.mp4", size="640x360", seconds=1.0, colour="red")
        card = _make_image(self.root / "card.png", size="900x900", colour="white")
        out = self.root / "out.mp4"
        compose_video_sequence(
            (
                SequenceClip(str(a), 0, 1.0, fit="cover"),
                SequenceImage(str(card), 1.5, motion="zoom_in", fit="cover"),
            ),
            out,
            canvas=(480, 480),
        )
        info = self._assert_video(out, 480, 480, expect_audio=False)
        self.assertAlmostEqual(float(info["format"]["duration"]), 2.5, delta=0.5)
        video = next(s for s in info["streams"] if s["codec_type"] == "video")
        num, _, den = video["r_frame_rate"].partition("/")
        self.assertEqual(int(num) / int(den or 1), 30)

    def test_each_ken_burns_motion_renders_without_ffmpeg_error(self):
        a = _make_clip(self.root / "a.mp4", size="640x360", seconds=1.0, colour="red")
        card = _make_image(self.root / "card.png", size="900x600", colour="white")
        for motion in ("zoom_out", "pan_left", "pan_right", "pan_up", "pan_down"):
            with self.subTest(motion=motion):
                out = self.root / f"{motion}.mp4"
                compose_video_sequence(
                    (
                        SequenceClip(str(a), 0, 1.0, fit="contain"),
                        SequenceImage(str(card), 1.0, motion=motion, fit="contain"),
                    ),
                    out,
                    canvas=(640, 360),
                )
                self._assert_video(out, 640, 360, expect_audio=False)

    def test_target_format_timeline_with_narration_and_music(self):
        a = _make_clip(self.root / "a.mp4", size="640x360", seconds=1.0, colour="red")
        b = _make_clip(self.root / "b.mp4", size="480x480", seconds=1.0, colour="green")
        voice = _make_audio(self.root / "voice.wav", seconds=2.0, freq=220, channels=1)
        bed = _make_audio(self.root / "bed.wav", seconds=1.0, freq=440, channels=2)
        out = self.root / "out.mp4"
        compose_video_sequence(
            (SequenceClip(str(a), 0, 1.0, fit="contain"), SequenceClip(str(b), 0, 1.0, fit="contain")),
            out,
            canvas=(640, 360),
            narration_path=str(voice),
            music_path=str(bed),
            music_gain_db=-18.0,
        )
        info = self._assert_video(out, 640, 360, expect_audio=True)
        self.assertAlmostEqual(float(info["format"]["duration"]), 2.0, delta=0.5)


if __name__ == "__main__":
    unittest.main()
