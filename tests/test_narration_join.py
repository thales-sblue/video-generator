"""Joining narration units into one WAV with exactly the planned silence.

Argument validation runs everywhere; the timing checks need the hash-locked
FFmpeg under ``.local-tools`` and are skipped when it is absent.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from video_generator.adapters import FFmpegError, join_audio_segments
from video_generator.tooling import ToolResolutionError, _validated_local_bin


class JoinAudioValidationTests(unittest.TestCase):
    def test_refuses_an_empty_or_oversized_segment_list(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "joined.wav"
            with self.assertRaises(FFmpegError):
                join_audio_segments([], out)
            with self.assertRaises(FFmpegError):
                join_audio_segments([("missing.wav", 0.0)] * 401, out)

    def test_refuses_a_non_wav_or_existing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            existing = Path(directory) / "joined.wav"
            existing.write_bytes(b"x")
            with self.assertRaises(FFmpegError):
                join_audio_segments([(existing, 0.0)], Path(directory) / "joined.mp3")
            with self.assertRaises(FFmpegError):
                join_audio_segments([(existing, 0.0)], existing)

    def test_refuses_a_missing_source_or_an_impossible_pause(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "unit.wav"
            source.write_bytes(b"x")
            out = Path(directory) / "joined.wav"
            with self.assertRaises(FFmpegError):
                join_audio_segments([(Path(directory) / "nope.wav", 0.0)], out)
            with self.assertRaises(FFmpegError):
                join_audio_segments([(source, -1.0)], out)
            with self.assertRaises(FFmpegError):
                join_audio_segments([(source, 99.0)], out)
            with self.assertRaises(FFmpegError):
                join_audio_segments([(source,)], out)


try:
    _LOCKED_BIN = _validated_local_bin()
except ToolResolutionError as exc:  # pragma: no cover - depends on local install
    _LOCKED_BIN = None

if _LOCKED_BIN is None:  # pragma: no cover - depends on local install
    _SKIP = "locked FFmpeg toolchain is not installed under .local-tools"
else:
    _SKIP = ""


@unittest.skipIf(_SKIP, _SKIP)
class JoinAudioTimingTests(unittest.TestCase):
    def _tone(self, path: Path, seconds: float) -> None:
        subprocess.run(
            [
                str(_LOCKED_BIN / "ffmpeg.exe"), "-v", "error", "-nostdin", "-y",
                "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}:sample_rate=48000",
                "-ac", "2", "-c:a", "pcm_s16le", str(path),
            ],
            capture_output=True, text=True, timeout=120, check=True,
        )

    def _duration(self, path: Path) -> float:
        completed = subprocess.run(
            [
                str(_LOCKED_BIN / "ffprobe.exe"), "-v", "error",
                "-show_entries", "format=duration", "-of", "json", str(path),
            ],
            capture_output=True, text=True, timeout=120, check=True,
        )
        return float(json.loads(completed.stdout)["format"]["duration"])

    def test_the_joined_length_is_the_units_plus_their_planned_pauses(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first, second = root / "a.wav", root / "b.wav"
            self._tone(first, 1.0)
            self._tone(second, 0.5)
            out = root / "joined.wav"
            artifact = join_audio_segments(
                [(first, 0.75), (second, 0.0)], out, lead_in_seconds=0.25
            )
            self.assertEqual(artifact.output_path, str(out.resolve()))
            self.assertEqual(artifact.sample_rate_hz, 48000)
            self.assertEqual(artifact.channels, 2)
            # 0.25 lead-in + 1.0 + 0.75 pause + 0.5
            self.assertAlmostEqual(self._duration(out), 2.5, delta=0.05)

    def test_a_longer_pause_makes_a_longer_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            unit = root / "a.wav"
            self._tone(unit, 0.5)
            short = root / "short.wav"
            long = root / "long.wav"
            join_audio_segments([(unit, 0.1), (unit, 0.0)], short)
            join_audio_segments([(unit, 1.1), (unit, 0.0)], long)
            self.assertAlmostEqual(self._duration(long) - self._duration(short), 1.0, delta=0.05)


if __name__ == "__main__":
    unittest.main()
