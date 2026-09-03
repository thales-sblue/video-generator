"""Speaking a script unit by unit, and measuring where each unit landed."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from video_generator.adapters import KokoroError, MediaProbe, NarrationArtifact
from video_generator.cli import main
from video_generator.narration import NarrationError, render_prosodic_narration

SCRIPT = (
    "Uma primeira frase suficientemente longa para nao virar um beat curto.\n\n"
    "Mas parece verdade.\n\n"
    "E uma terceira frase, tambem longa o bastante para fechar o roteiro."
)


def _artifact(path):
    return NarrationArtifact(
        output_path=str(path),
        voice="pm_alex",
        speed=1.0,
        lang="pt-br",
        sample_rate_hz=48000,
        channels=2,
        text_sha256="a" * 64,
        file_size_bytes=1000,
    )


class RenderProsodicNarrationTests(unittest.TestCase):
    def _fake_stack(self, unit_seconds=2.0, joined_seconds=None):
        """Patch synthesis, probing and joining; record what each was asked for."""

        calls = {"speeds": [], "gaps": [], "lead_in": None}

        def synth(text, path, *, voice, speed, lang, timeout_seconds):
            Path(path).write_bytes(b"wav")
            calls["speeds"].append(round(speed, 3))
            return _artifact(path)

        def probe(path):
            seconds = unit_seconds
            if joined_seconds is not None and Path(path).name == "narration.wav":
                seconds = joined_seconds
            return MediaProbe(
                source_path=str(path),
                file_size_bytes=1000,
                format_name="wav",
                duration_seconds=seconds,
                bit_rate_bps=None,
                streams=(),
            )

        def join(segments, output, *, lead_in_seconds, timeout_seconds):
            calls["gaps"] = [round(gap, 3) for _, gap in segments]
            calls["lead_in"] = lead_in_seconds
            Path(output).write_bytes(b"joined")
            return None

        return calls, synth, probe, join

    def test_speaks_every_unit_and_reports_where_each_one_lands(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "narration.wav"
            calls, synth, probe, join = self._fake_stack(
                unit_seconds=2.0, joined_seconds=99.0
            )
            with patch("video_generator.narration.synthesize_narration", synth), patch(
                "video_generator.narration.probe_media", probe
            ), patch("video_generator.narration.join_audio_segments", join):
                result = render_prosodic_narration(
                    SCRIPT, output, voice="pm_alex", lang="pt-br", lead_in_seconds=0.5
                )

            self.assertEqual(len(result.units), 3)
            self.assertEqual(calls["lead_in"], 0.5)
            self.assertEqual(result.total_seconds, 99.0)
            self.assertEqual(result.spoken_seconds, 6.0)
            # the beat is the middle unit: slower, and silence on both sides
            self.assertEqual(result.units[1].role, "beat")
            self.assertLess(calls["speeds"][1], calls["speeds"][0])
            self.assertGreater(calls["gaps"][0], 0.0)
            self.assertGreater(calls["gaps"][1], 0.0)
            self.assertEqual(calls["gaps"][-1], 0.0)
            # each unit starts after the lead-in plus everything already spoken
            self.assertEqual(result.units[0].start_seconds, 0.5)
            self.assertEqual(
                result.units[1].start_seconds, 0.5 + 2.0 + result.units[0].pause_after_seconds
            )

    def test_base_speed_scales_every_unit_and_stays_in_range(self):
        with tempfile.TemporaryDirectory() as directory:
            calls, synth, probe, join = self._fake_stack()
            with patch("video_generator.narration.synthesize_narration", synth), patch(
                "video_generator.narration.probe_media", probe
            ), patch("video_generator.narration.join_audio_segments", join):
                render_prosodic_narration(
                    SCRIPT, Path(directory) / "narration.wav", base_speed=0.9
                )
            self.assertTrue(all(0.5 <= speed <= 2.0 for speed in calls["speeds"]))
            self.assertLess(max(calls["speeds"]), 1.0)

    def test_refuses_an_empty_script_a_non_wav_or_an_existing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            taken = Path(directory) / "narration.wav"
            taken.write_bytes(b"x")
            for call in (
                lambda: render_prosodic_narration("  ", Path(directory) / "a.wav"),
                lambda: render_prosodic_narration(SCRIPT, Path(directory) / "a.mp3"),
                lambda: render_prosodic_narration(SCRIPT, taken),
            ):
                with self.subTest(call=call):
                    with self.assertRaises(NarrationError):
                        call()

    def test_a_failing_unit_fails_the_whole_narration(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch(
                "video_generator.narration.synthesize_narration",
                side_effect=KokoroError("model missing"),
            ):
                with self.assertRaises(NarrationError) as caught:
                    render_prosodic_narration(SCRIPT, Path(directory) / "narration.wav")
            self.assertIn("model missing", str(caught.exception))


class NarrateProsodyCliTests(unittest.TestCase):
    def test_prosody_mode_prints_and_persists_the_measured_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "narration.wav"
            units = Path(directory) / "units.json"
            stdout = io.StringIO()

            class _Result:
                def to_dict(self):
                    return {"output_path": str(output), "unit_count": 3, "units": []}

            with patch(
                "video_generator.cli.render_prosodic_narration", return_value=_Result()
            ) as render, contextlib.redirect_stdout(stdout):
                code = main(
                    [
                        "narrate", str(output), "--text", SCRIPT, "--prosody",
                        "--voice", "pm_alex", "--lang", "pt-br", "--speed", "0.97",
                        "--lead-in-seconds", "0.6", "--units-out", str(units), "--json",
                    ]
                )

            self.assertEqual(code, 0)
            self.assertEqual(render.call_args.kwargs["base_speed"], 0.97)
            self.assertEqual(render.call_args.kwargs["lead_in_seconds"], 0.6)
            self.assertEqual(json.loads(stdout.getvalue())["unit_count"], 3)
            self.assertEqual(json.loads(units.read_text(encoding="utf-8"))["unit_count"], 3)

    def test_reports_a_narration_failure_as_exit_two(self):
        with tempfile.TemporaryDirectory() as directory:
            stderr = io.StringIO()
            with patch(
                "video_generator.cli.render_prosodic_narration",
                side_effect=NarrationError("unit 4 could not be synthesised"),
            ), contextlib.redirect_stderr(stderr):
                code = main(
                    ["narrate", str(Path(directory) / "n.wav"), "--text", SCRIPT, "--prosody"]
                )
            self.assertEqual(code, 2)
            self.assertIn("unit 4", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
