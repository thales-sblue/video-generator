"""Measure where each word is actually spoken, using a local Whisper model.

This adapter is opt-in and fails closed with :class:`AlignerError` when the
``align`` extra or the local CTranslate2 model directory is missing, so
contracts, planning, rendering and ``doctor`` keep working without it. Nothing
leaves the machine: the model is loaded from ``.local-tools/whisper/`` (or
``WHISPER_HOME``) with downloads disabled.

It transcribes; it does not judge. Deciding that the resulting captions read
well is still a human review.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from video_generator.tooling import ToolResolutionError, resolve_whisper_model

DEFAULT_LANGUAGE = "pt"
DEFAULT_BEAM_SIZE = 5
DEFAULT_COMPUTE_TYPE = "int8"
MAX_WORDS = 20_000


class AlignerError(RuntimeError):
    """Raised when local word-level alignment cannot produce timings."""


@dataclass(frozen=True, slots=True)
class WordTiming:
    """One recognised word and the span of audio it was heard in."""

    text: str
    start_seconds: float
    end_seconds: float

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or not self.text.strip():
            raise AlignerError("word text must be a non-empty string")
        for value, name in ((self.start_seconds, "start"), (self.end_seconds, "end")):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
            ):
                raise AlignerError(f"word {name} must be a finite, non-negative number")
        if self.end_seconds < self.start_seconds:
            raise AlignerError("word end must not precede its start")

    def to_dict(self) -> dict[str, object]:
        return {
            "text": self.text,
            "start_seconds": round(float(self.start_seconds), 3),
            "end_seconds": round(float(self.end_seconds), 3),
        }


def _load_whisper():
    try:
        from faster_whisper import WhisperModel  # type: ignore import-not-found
    except ImportError as exc:  # pragma: no cover - exercised via patched imports
        raise AlignerError(
            "the 'align' extra is not installed: pip install -e .[align]"
        ) from exc
    return WhisperModel


def transcribe_words(
    audio_path: str | Path,
    *,
    language: str = DEFAULT_LANGUAGE,
    model_name: str | None = None,
    beam_size: int = DEFAULT_BEAM_SIZE,
    compute_type: str = DEFAULT_COMPUTE_TYPE,
) -> tuple[WordTiming, ...]:
    """Return ordered word timings measured in ``audio_path``.

    The audio is the only source of truth here: no text is supplied to the
    model, so the timings describe what was actually said and when, not what the
    script expected. Callers map these onto their own script separately.
    """

    source = Path(audio_path).expanduser().resolve()
    if not source.is_file():
        raise AlignerError(f"audio source does not exist: {source}")
    if not isinstance(language, str) or not language.strip():
        raise AlignerError("language must be a non-empty string")
    if isinstance(beam_size, bool) or not isinstance(beam_size, int) or beam_size < 1:
        raise AlignerError("beam_size must be a positive integer")
    if not isinstance(compute_type, str) or not compute_type.strip():
        raise AlignerError("compute_type must be a non-empty string")

    try:
        model_dir = resolve_whisper_model(model_name)
    except ToolResolutionError as exc:
        raise AlignerError(f"local Whisper model rejected: {exc}") from exc
    if model_dir is None:
        raise AlignerError(
            "Whisper model files not found under .local-tools/whisper/ (or WHISPER_HOME)"
        )

    WhisperModel = _load_whisper()
    try:
        model = WhisperModel(
            model_dir,
            device="cpu",
            compute_type=compute_type,
            local_files_only=True,
        )
        segments, _info = model.transcribe(
            str(source),
            language=language,
            beam_size=beam_size,
            word_timestamps=True,
            vad_filter=False,
            condition_on_previous_text=False,
        )
        words: list[WordTiming] = []
        for segment in segments:
            for word in getattr(segment, "words", None) or ():
                text = str(getattr(word, "word", "")).strip()
                if not text:
                    continue
                start = getattr(word, "start", None)
                end = getattr(word, "end", None)
                if start is None or end is None:
                    continue
                words.append(
                    WordTiming(
                        text=text,
                        start_seconds=max(0.0, float(start)),
                        end_seconds=max(0.0, float(end)),
                    )
                )
                if len(words) > MAX_WORDS:
                    raise AlignerError(f"transcript exceeds {MAX_WORDS} words")
    except AlignerError:
        raise
    except Exception as exc:  # noqa: BLE001 - third-party failure surface is broad
        raise AlignerError(f"Whisper alignment failed: {type(exc).__name__}: {exc}") from exc

    if not words:
        raise AlignerError("Whisper produced no word timings for this audio")
    # a decoder can emit a word that starts before the previous one ended;
    # captions need a monotonic clock, so clamp rather than reorder.
    fixed: list[WordTiming] = []
    previous_end = 0.0
    for word in words:
        start = max(word.start_seconds, previous_end)
        end = max(word.end_seconds, start)
        previous_end = end
        fixed.append(WordTiming(text=word.text, start_seconds=start, end_seconds=end))
    return tuple(fixed)
