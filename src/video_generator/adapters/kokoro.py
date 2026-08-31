"""Local text-to-speech narration through the optional Kokoro ONNX model.

This adapter is opt-in. It fails closed with :class:`KokoroError` when the model
files or the ``tts`` extra are missing, so contracts, planning, inspection and
``doctor`` keep working without it. Technical success here is not an auditory
review: the caller still owes a human listen before treating a narration as
approved.
"""

from __future__ import annotations

import hashlib
import math
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from video_generator.adapters.ffmpeg import FFmpegError, extract_audio
from video_generator.tooling import ToolResolutionError, resolve_kokoro_assets

MAX_TEXT_CHARS = 20_000
VOICE_PATTERN = re.compile(r"^[a-z]{2}_[a-z]+$")
SUPPORTED_LANGS = (
    "en-us",
    "en-gb",
    "pt-br",
    "es",
    "fr-fr",
    "hi",
    "it",
    "ja",
    "zh",
)
MIN_SPEED = 0.5
MAX_SPEED = 2.0


class KokoroError(RuntimeError):
    """Raised when local Kokoro narration cannot produce its artifact."""


@dataclass(frozen=True, slots=True)
class NarrationArtifact:
    output_path: str
    voice: str
    speed: float
    lang: str
    sample_rate_hz: int
    channels: int
    text_sha256: str
    file_size_bytes: int


def _load_kokoro():
    try:
        from kokoro_onnx import Kokoro  # type: ignore import-not-found
        import soundfile  # type: ignore import-not-found
    except ImportError as exc:  # pragma: no cover - exercised via patched imports
        raise KokoroError(
            "the 'tts' extra is not installed: pip install -e .[tts]"
        ) from exc
    return Kokoro, soundfile


def synthesize_narration(
    text: str,
    output_path: str | Path,
    *,
    voice: str = "af_heart",
    speed: float = 1.0,
    lang: str = "en-us",
    timeout_seconds: float = 300,
) -> NarrationArtifact:
    """Synthesise ``text`` locally into a deterministic 48 kHz stereo PCM WAV."""

    if not isinstance(text, str) or not text.strip():
        raise KokoroError("text must be a non-empty string")
    if len(text) > MAX_TEXT_CHARS:
        raise KokoroError(f"text must be at most {MAX_TEXT_CHARS} characters")
    if not isinstance(voice, str) or not VOICE_PATTERN.match(voice):
        raise KokoroError("voice must look like 'af_heart'")
    if isinstance(speed, bool) or not isinstance(speed, (int, float)) or not math.isfinite(speed):
        raise KokoroError("speed must be a finite number")
    if not MIN_SPEED <= float(speed) <= MAX_SPEED:
        raise KokoroError(f"speed must be between {MIN_SPEED} and {MAX_SPEED}")
    if lang not in SUPPORTED_LANGS:
        raise KokoroError(f"lang must be one of: {', '.join(SUPPORTED_LANGS)}")
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)):
        raise KokoroError("timeout_seconds must be a number")
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise KokoroError("timeout_seconds must be greater than zero")

    output = Path(output_path).expanduser().resolve()
    if output.suffix.lower() != ".wav":
        raise KokoroError("output_path must use the .wav extension")
    if output.exists():
        raise KokoroError(f"output already exists: {output}")

    try:
        assets = resolve_kokoro_assets()
    except ToolResolutionError as exc:
        raise KokoroError(f"local Kokoro model rejected: {exc}") from exc
    if assets is None:
        raise KokoroError(
            "Kokoro model files not found under .local-tools/kokoro/ (or KOKORO_HOME)"
        )
    model_path, voices_path = assets

    Kokoro, soundfile = _load_kokoro()
    try:
        engine = Kokoro(model_path, voices_path)
        samples, sample_rate = engine.create(text, voice=voice, speed=float(speed), lang=lang)
    except KokoroError:
        raise
    except Exception as exc:  # noqa: BLE001 - third-party failure surface is broad
        raise KokoroError(f"Kokoro synthesis failed: {type(exc).__name__}: {exc}") from exc
    if sample_rate is None or not isinstance(sample_rate, int) or sample_rate <= 0:
        raise KokoroError("Kokoro returned an invalid sample rate")
    if samples is None or len(samples) == 0:
        raise KokoroError("Kokoro returned no audio samples")

    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            prefix=f".{output.stem}-tts-",
            suffix=".wav",
            dir=output.parent,
            delete=False,
        ) as reserved:
            native_wav = Path(reserved.name)
    except OSError as exc:
        raise KokoroError(f"could not prepare output path: {output}") from exc

    try:
        soundfile.write(str(native_wav), samples, sample_rate)
        try:
            normalized = extract_audio(native_wav, output, timeout_seconds=float(timeout_seconds))
        except FFmpegError as exc:
            raise KokoroError(f"could not normalise narration audio: {exc}") from exc
    finally:
        try:
            native_wav.unlink(missing_ok=True)
        except OSError:
            pass

    return NarrationArtifact(
        output_path=normalized.output_path,
        voice=voice,
        speed=float(speed),
        lang=lang,
        sample_rate_hz=normalized.sample_rate_hz,
        channels=normalized.channels,
        text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        file_size_bytes=normalized.file_size_bytes,
    )
