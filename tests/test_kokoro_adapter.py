import tempfile
import unittest
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch

from video_generator.adapters import AudioArtifact, KokoroError, synthesize_narration
from video_generator.adapters import kokoro
from video_generator.tooling import ToolResolutionError


class _FakeKokoro:
    def __init__(self, model, voices):
        self.model = model
        self.voices = voices

    def create(self, text, *, voice, speed, lang):
        return [0.0] * 240, 24000


class _FakeSoundfile:
    def __init__(self):
        self.calls = []

    def write(self, path, samples, sample_rate):
        Path(path).write_bytes(b"RIFF-native")
        self.calls.append((path, len(samples), sample_rate))


def _fake_extract_audio(native, output, *, timeout_seconds):
    resolved = Path(output).expanduser().resolve()
    resolved.write_bytes(b"RIFF-48k-stereo")
    return AudioArtifact(
        source_path=str(Path(native).resolve()),
        output_path=str(resolved),
        sample_rate_hz=48000,
        channels=2,
        file_size_bytes=resolved.stat().st_size,
    )


class KokoroAdapterTests(unittest.TestCase):
    def test_rejects_bad_arguments_before_touching_the_model(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            existing = root / "taken.wav"
            existing.write_bytes(b"x")
            cases = (
                ("", root / "a.wav", {}, "non-empty"),
                ("x" * 20_001, root / "b.wav", {}, "at most"),
                ("hi", root / "c.wav", {"voice": "Bad Voice"}, "af_heart"),
                ("hi", root / "d.wav", {"speed": 5}, "between"),
                ("hi", root / "e.wav", {"lang": "kl"}, "lang must be one of"),
                ("hi", root / "f.mp3", {}, ".wav"),
                ("hi", existing, {}, "already exists"),
                ("hi", root / "g.wav", {"timeout_seconds": 0}, "greater than zero"),
            )
            for text, output, kwargs, message in cases:
                with self.subTest(message=message):
                    with patch.object(kokoro, "resolve_kokoro_assets") as resolve:
                        with self.assertRaisesRegex(KokoroError, message):
                            synthesize_narration(text, output, **kwargs)
                    resolve.assert_not_called()

    def test_fails_closed_when_the_local_model_is_absent_or_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "voice.wav"
            with patch.object(kokoro, "resolve_kokoro_assets", return_value=None):
                with self.assertRaisesRegex(KokoroError, "model files not found"):
                    synthesize_narration("hello", output)
            with patch.object(
                kokoro, "resolve_kokoro_assets", side_effect=ToolResolutionError("empty")
            ):
                with self.assertRaisesRegex(KokoroError, "model rejected"):
                    synthesize_narration("hello", output)

    def test_reports_a_missing_tts_extra(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "voice.wav"
            with patch.object(
                kokoro, "resolve_kokoro_assets", return_value=("m.onnx", "v.bin")
            ), patch.object(
                kokoro,
                "_load_kokoro",
                side_effect=KokoroError("the 'tts' extra is not installed: pip install -e .[tts]"),
            ):
                with self.assertRaisesRegex(KokoroError, "tts' extra"):
                    synthesize_narration("hello", output)

    def test_synthesises_and_normalises_a_deterministic_wav(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "narration.wav"
            fake_sf = _FakeSoundfile()
            with patch.object(
                kokoro, "resolve_kokoro_assets", return_value=("m.onnx", "v.bin")
            ), patch.object(
                kokoro, "_load_kokoro", return_value=(_FakeKokoro, fake_sf)
            ), patch.object(
                kokoro, "extract_audio", side_effect=_fake_extract_audio
            ):
                artifact = synthesize_narration(
                    "Hello dark world.", output, voice="am_adam", speed=1.25, lang="en-gb"
                )

            self.assertEqual(artifact.output_path, str(output.resolve()))
            self.assertEqual(artifact.voice, "am_adam")
            self.assertEqual(artifact.speed, 1.25)
            self.assertEqual(artifact.lang, "en-gb")
            self.assertEqual((artifact.sample_rate_hz, artifact.channels), (48000, 2))
            self.assertEqual(
                artifact.text_sha256, sha256(b"Hello dark world.").hexdigest()
            )
            self.assertEqual(artifact.file_size_bytes, len(b"RIFF-48k-stereo"))
            # the native-rate scratch wav is removed
            self.assertEqual(list(output.parent.glob(".narration-tts-*")), [])
            self.assertEqual(fake_sf.calls[0][2], 24000)

    def test_wraps_a_synthesis_failure_without_leaving_an_output(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "narration.wav"

            class _Boom(_FakeKokoro):
                def create(self, *a, **k):
                    raise RuntimeError("onnx blew up")

            with patch.object(
                kokoro, "resolve_kokoro_assets", return_value=("m.onnx", "v.bin")
            ), patch.object(kokoro, "_load_kokoro", return_value=(_Boom, _FakeSoundfile())):
                with self.assertRaisesRegex(KokoroError, "synthesis failed"):
                    synthesize_narration("hello", output)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
