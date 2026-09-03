"""Break a narration script into prosodic units with deliberate pauses.

Pure and stdlib-only: no I/O, no synthesis, no adapters. Synthesising a long
script as one homogeneous block is what makes local TTS sound like an audiobook
reader -- every sentence gets the same tempo and the same pause. This module
decides, from punctuation and paragraph shape alone, where the voice should
breathe, where it should slow down before a punchline, and where a short run of
sentences should be spoken as one quick list.

The adapter layer synthesises each :class:`NarrationUnit` separately and joins
them with the pause the unit asks for. Timing here is intent, not measurement:
the real durations only exist once the audio is rendered.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MIN_SPEED = 0.5
MAX_SPEED = 2.0
MAX_PAUSE_SECONDS = 5.0

# A "beat" is a standalone short paragraph -- the one-line turn that carries a
# punchline. It is spoken slower and gets silence on both sides.
BEAT_MAX_CHARS = 62
# Consecutive short sentences inside one paragraph are one breath, not three.
RUN_SENTENCE_MAX_CHARS = 46
RUN_MERGED_MAX_CHARS = 110
# Two short sentences standing alone are a punchline pair ("A informacao e a
# mesma. O cerebro que recebe, nao."); three or more are a list, and a list read
# slowly loses the rhythm that makes it work.
BEAT_MAX_SENTENCES = 2

PAUSE_PARAGRAPH = 0.48
PAUSE_SENTENCE = 0.20
PAUSE_BEFORE_BEAT = 0.68
PAUSE_AFTER_BEAT = 0.78
PAUSE_QUESTION_BONUS = 0.22
PAUSE_COLON = 0.32

SPEED_BEAT = 0.93
SPEED_RUN = 1.06
SPEED_NORMAL = 1.0

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])\s+")
_PARAGRAPH_SPLIT = re.compile(r"\n[ \t]*\n+")

ROLES = ("beat", "run", "line")


class ProsodyError(ValueError):
    """Raised when a script cannot be laid out as narration units."""


@dataclass(frozen=True, slots=True)
class NarrationUnit:
    """One synthesis call: what to say, how fast, and how long to wait after."""

    text: str
    speed: float
    pause_after_seconds: float
    role: str

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or not self.text.strip():
            raise ProsodyError("unit text must be a non-empty string")
        if self.text != self.text.strip():
            raise ProsodyError("unit text must not have leading or trailing whitespace")
        if (
            isinstance(self.speed, bool)
            or not isinstance(self.speed, (int, float))
            or not MIN_SPEED <= float(self.speed) <= MAX_SPEED
        ):
            raise ProsodyError(f"unit speed must be between {MIN_SPEED} and {MAX_SPEED}")
        if (
            isinstance(self.pause_after_seconds, bool)
            or not isinstance(self.pause_after_seconds, (int, float))
            or not 0.0 <= float(self.pause_after_seconds) <= MAX_PAUSE_SECONDS
        ):
            raise ProsodyError(
                f"unit pause must be between 0 and {MAX_PAUSE_SECONDS} seconds"
            )
        if self.role not in ROLES:
            raise ProsodyError(f"unit role must be one of: {', '.join(ROLES)}")

    def to_dict(self) -> dict[str, object]:
        return {
            "text": self.text,
            "speed": round(float(self.speed), 3),
            "pause_after_seconds": round(float(self.pause_after_seconds), 3),
            "role": self.role,
        }


def _sentences(paragraph: str) -> list[str]:
    return [part.strip() for part in _SENTENCE_SPLIT.split(paragraph.strip()) if part.strip()]


def _group_sentences(sentences: list[str]) -> list[tuple[str, int]]:
    """Merge runs of short sentences into one breath.

    Returns ``(text, sentence_count)`` pairs. A group of more than one sentence
    is spoken slightly faster, because a staccato list ("Estudou. Tem uma
    carreira. Resolve problemas.") loses its rhythm when each fragment is
    synthesised and padded separately.
    """

    groups: list[tuple[str, int]] = []
    buffer: list[str] = []

    def flush() -> None:
        if buffer:
            groups.append((" ".join(buffer), len(buffer)))
            buffer.clear()

    for sentence in sentences:
        if len(sentence) > RUN_SENTENCE_MAX_CHARS:
            flush()
            groups.append((sentence, 1))
            continue
        candidate = " ".join([*buffer, sentence])
        if buffer and len(candidate) > RUN_MERGED_MAX_CHARS:
            flush()
            candidate = sentence
        buffer.append(sentence)
        if len(candidate) > RUN_MERGED_MAX_CHARS:
            flush()
    flush()
    return groups


def _base_pause(text: str, *, end_of_paragraph: bool) -> float:
    pause = PAUSE_PARAGRAPH if end_of_paragraph else PAUSE_SENTENCE
    stripped = text.rstrip()
    if stripped.endswith("?"):
        pause += PAUSE_QUESTION_BONUS
    elif stripped.endswith(":"):
        pause = max(pause, PAUSE_COLON)
    return pause


def plan_narration_units(text: str) -> tuple[NarrationUnit, ...]:
    """Lay ``text`` out as ordered narration units.

    Paragraphs (blank-line separated) are the outer rhythm; sentences are the
    inner one. A paragraph that is a single short line is treated as a *beat*:
    spoken slower, with a longer silence before and after it, so a punchline
    lands instead of sliding past. Every other unit gets a sentence-length pause
    inside its paragraph and a longer one at the paragraph break.

    Raises :class:`ProsodyError` when the script has no speakable content.
    """

    if not isinstance(text, str) or not text.strip():
        raise ProsodyError("narration text must be a non-empty string")
    paragraphs = [
        block.strip()
        for block in _PARAGRAPH_SPLIT.split(text.replace("\r\n", "\n").replace("\r", "\n"))
        if block.strip()
    ]
    if not paragraphs:
        raise ProsodyError("narration text has no speakable content")

    # (text, is_run, is_beat, end_of_paragraph)
    flattened: list[tuple[str, bool, bool, bool]] = []
    for paragraph in paragraphs:
        groups = _group_sentences(_sentences(paragraph))
        if not groups:
            continue
        # a beat is a short paragraph that stands alone: one sentence, or a pair
        # that lands together. Three or more short sentences are a list instead.
        is_beat = (
            len(groups) == 1
            and groups[0][1] <= BEAT_MAX_SENTENCES
            and len(groups[0][0]) <= BEAT_MAX_CHARS
        )
        for index, (group_text, count) in enumerate(groups):
            flattened.append((group_text, count > 1, is_beat, index == len(groups) - 1))
    if not flattened:
        raise ProsodyError("narration text has no speakable content")

    units: list[NarrationUnit] = []
    for index, (unit_text, is_run, is_beat, ends_paragraph) in enumerate(flattened):
        if is_beat:
            role, speed = "beat", SPEED_BEAT
            pause = PAUSE_AFTER_BEAT
        else:
            role, speed = ("run", SPEED_RUN) if is_run else ("line", SPEED_NORMAL)
            pause = _base_pause(unit_text, end_of_paragraph=ends_paragraph)
        # silence *before* a punchline is what makes it read as one
        next_is_beat = index + 1 < len(flattened) and flattened[index + 1][2]
        if next_is_beat:
            pause = max(pause, PAUSE_BEFORE_BEAT)
        units.append(
            NarrationUnit(
                text=unit_text,
                speed=speed,
                pause_after_seconds=round(pause, 3),
                role=role,
            )
        )
    # nothing should hang after the last word; the timeline owns the tail
    last = units[-1]
    units[-1] = NarrationUnit(
        text=last.text, speed=last.speed, pause_after_seconds=0.0, role=last.role
    )
    return tuple(units)
