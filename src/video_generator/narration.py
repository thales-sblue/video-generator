"""Render a script as narration with deliberate prosody, then measure it.

Orchestration only: :mod:`video_generator.domain.prosody` decides the units and
the pauses, the Kokoro adapter speaks each one, and the FFmpeg adapter joins them
with exactly the silence the unit asked for. Nothing here talks to a network,
and the script file is never written to.

Synthesising a whole script in one call is what makes local TTS sound flat: the
engine picks one tempo and one inter-sentence gap and holds them for four
minutes. Speaking unit by unit lets a punchline slow down and a list speed up.
Technical success is still not an auditory review.
"""

from __future__ import annotations

import hashlib
import tempfile
from dataclasses import dataclass
from pathlib import Path

from video_generator.adapters import (
    FFmpegError,
    KokoroError,
    ProbeError,
    join_audio_segments,
    probe_media,
    synthesize_narration,
)
from video_generator.adapters.kokoro import MAX_SPEED, MIN_SPEED
from video_generator.domain.prosody import NarrationUnit, ProsodyError, plan_narration_units


class NarrationError(RuntimeError):
    """Raised when a prosodic narration cannot be produced."""


@dataclass(frozen=True, slots=True)
class RenderedUnit:
    """One spoken unit, with the time it actually occupies in the joined WAV."""

    index: int
    text: str
    role: str
    speed: float
    pause_after_seconds: float
    start_seconds: float
    spoken_seconds: float

    def to_dict(self) -> dict[str, object]:
        return {
            "index": self.index,
            "text": self.text,
            "role": self.role,
            "speed": round(self.speed, 3),
            "pause_after_seconds": round(self.pause_after_seconds, 3),
            "start_seconds": round(self.start_seconds, 3),
            "spoken_seconds": round(self.spoken_seconds, 3),
        }


@dataclass(frozen=True, slots=True)
class ProsodicNarration:
    output_path: str
    voice: str
    lang: str
    base_speed: float
    lead_in_seconds: float
    total_seconds: float
    spoken_seconds: float
    pause_seconds: float
    text_sha256: str
    units: tuple[RenderedUnit, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "output_path": self.output_path,
            "voice": self.voice,
            "lang": self.lang,
            "base_speed": round(self.base_speed, 3),
            "lead_in_seconds": round(self.lead_in_seconds, 3),
            "total_seconds": round(self.total_seconds, 3),
            "spoken_seconds": round(self.spoken_seconds, 3),
            "pause_seconds": round(self.pause_seconds, 3),
            "text_sha256": self.text_sha256,
            "unit_count": len(self.units),
            "units": [unit.to_dict() for unit in self.units],
        }


def _unit_speed(unit: NarrationUnit, base_speed: float) -> float:
    return min(MAX_SPEED, max(MIN_SPEED, round(base_speed * unit.speed, 3)))


def render_prosodic_narration(
    text: str,
    output_path: str | Path,
    *,
    voice: str = "pf_dora",
    lang: str = "pt-br",
    base_speed: float = 1.0,
    lead_in_seconds: float = 0.0,
    timeout_seconds: float = 600,
) -> ProsodicNarration:
    """Speak ``text`` unit by unit and join the units into one WAV.

    Returns where every unit landed in the joined audio, measured with ffprobe --
    not estimated -- so a caller can drive captions and shot timing off the same
    numbers the listener will hear.
    """

    if not isinstance(text, str) or not text.strip():
        raise NarrationError("narration text must be a non-empty string")
    output = Path(output_path).expanduser().resolve()
    if output.suffix.lower() != ".wav":
        raise NarrationError("output_path must use the .wav extension")
    if output.exists():
        raise NarrationError(f"output already exists: {output}")
    try:
        units = plan_narration_units(text)
    except ProsodyError as exc:
        raise NarrationError(f"cannot lay out narration units: {exc}") from exc

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".narration-units-", dir=output.parent) as staging:
        segments: list[tuple[Path, float]] = []
        durations: list[float] = []
        for index, unit in enumerate(units):
            piece = Path(staging) / f"unit-{index:03d}.wav"
            try:
                synthesize_narration(
                    unit.text,
                    piece,
                    voice=voice,
                    speed=_unit_speed(unit, base_speed),
                    lang=lang,
                    timeout_seconds=timeout_seconds,
                )
            except KokoroError as exc:
                raise NarrationError(f"unit {index} could not be synthesised: {exc}") from exc
            try:
                probe = probe_media(piece)
            except ProbeError as exc:
                raise NarrationError(f"could not measure unit {index}: {exc}") from exc
            if probe.duration_seconds is None or probe.duration_seconds <= 0:
                raise NarrationError(f"unit {index} has no usable duration")
            durations.append(float(probe.duration_seconds))
            segments.append((piece, unit.pause_after_seconds))
        try:
            join_audio_segments(
                segments,
                output,
                lead_in_seconds=lead_in_seconds,
                timeout_seconds=timeout_seconds,
            )
        except FFmpegError as exc:
            raise NarrationError(f"could not join narration units: {exc}") from exc

    try:
        joined = probe_media(output)
    except ProbeError as exc:
        raise NarrationError(f"could not measure the joined narration: {exc}") from exc
    if joined.duration_seconds is None or joined.duration_seconds <= 0:
        raise NarrationError("the joined narration has no usable duration")

    rendered: list[RenderedUnit] = []
    cursor = float(lead_in_seconds)
    for index, (unit, spoken) in enumerate(zip(units, durations)):
        rendered.append(
            RenderedUnit(
                index=index,
                text=unit.text,
                role=unit.role,
                speed=_unit_speed(unit, base_speed),
                pause_after_seconds=unit.pause_after_seconds,
                start_seconds=cursor,
                spoken_seconds=spoken,
            )
        )
        cursor += spoken + unit.pause_after_seconds

    return ProsodicNarration(
        output_path=str(output),
        voice=voice,
        lang=lang,
        base_speed=float(base_speed),
        lead_in_seconds=float(lead_in_seconds),
        total_seconds=float(joined.duration_seconds),
        spoken_seconds=round(sum(durations), 3),
        pause_seconds=round(sum(unit.pause_after_seconds for unit in units), 3),
        text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        units=tuple(rendered),
    )
