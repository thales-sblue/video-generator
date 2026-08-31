"""Turn caption text into plain (text, start, end) cues.

Stdlib only. This module does no I/O and no rendering: it parses persisted
SRT/WebVTT files, or lays narration text out over a duration, producing ordered
timing triples that the sequence workflow validates with the same rules as
inline caption items before FFmpeg burns them.
"""

from __future__ import annotations

import re

SUPPORTED_FORMATS = ("srt", "vtt")
CAPTION_MAX_CHARS = 160

_TIMESTAMP = re.compile(
    r"^(?:(?P<h>\d+):)?(?P<m>[0-5]?\d):(?P<s>[0-5]?\d)[.,](?P<ms>\d{1,3})$"
)
_ARROW = "-->"
_VTT_BLOCK_KEYWORDS = ("NOTE", "STYLE", "REGION")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])\s+|\n+")


class SubtitleParseError(ValueError):
    """Raised when a subtitle file cannot be parsed into clean cues."""


def _seconds(token: str) -> float:
    match = _TIMESTAMP.match(token.strip())
    if match is None:
        raise SubtitleParseError(f"invalid timestamp: {token!r}")
    hours = int(match.group("h") or 0)
    minutes = int(match.group("m"))
    seconds = int(match.group("s"))
    milliseconds = int(match.group("ms").ljust(3, "0"))
    return hours * 3600 + minutes * 60 + seconds + milliseconds / 1000


def parse_subtitle_cues(content: str, *, source_format: str) -> tuple[tuple[str, float, float], ...]:
    """Return ordered ``(text, start_seconds, end_seconds)`` triples from ``content``.

    Only plain cue text is accepted: any ``<``, ``>``, ``{`` or ``}`` (styling
    tags, positioning, or subtitle markup) is rejected so the burned-in text can
    never be interpreted as filter-graph or libass syntax.
    """

    if source_format not in SUPPORTED_FORMATS:
        raise SubtitleParseError(f"unsupported subtitle format: {source_format!r}")
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    blocks = [block for block in re.split(r"\n[ \t]*\n", normalized) if block.strip()]
    if not blocks:
        raise SubtitleParseError("subtitle file contains no cues")

    cues: list[tuple[str, float, float]] = []
    for block in blocks:
        lines = [line for line in block.split("\n") if line.strip() != ""]
        if not lines:
            continue
        if source_format == "vtt" and (
            lines[0].strip().upper().startswith("WEBVTT")
            or lines[0].strip().split(" ", 1)[0] in _VTT_BLOCK_KEYWORDS
        ):
            continue

        arrow_index = next((i for i, line in enumerate(lines) if _ARROW in line), None)
        if arrow_index is None:
            raise SubtitleParseError(f"cue block has no '{_ARROW}' timing line")
        if arrow_index > 1:
            raise SubtitleParseError("cue block has unexpected lines before its timing")

        left, _, right = lines[arrow_index].partition(_ARROW)
        start = _seconds(left)
        end = _seconds(right.strip().split(" ", 1)[0])
        if end <= start:
            raise SubtitleParseError("cue end must be after its start")

        text_lines = [line.strip() for line in lines[arrow_index + 1 :]]
        text = " ".join(part for part in text_lines if part).strip()
        if not text:
            raise SubtitleParseError("cue has no text")
        if any(character in text for character in "<>{}"):
            raise SubtitleParseError("cue text must not contain markup characters")
        cues.append((text, start, end))

    if not cues:
        raise SubtitleParseError("subtitle file contains no cues")
    return tuple(cues)


_MERGE_SHORT_BELOW = 40


def _pack_sentences(text: str) -> list[str]:
    """One cue per sentence; wrap a >160-char sentence, absorb tiny fragments."""

    parts = [part.strip() for part in _SENTENCE_SPLIT.split(text.strip()) if part.strip()]
    packed: list[str] = []
    for sentence in parts:
        if len(sentence) > CAPTION_MAX_CHARS:
            current = ""
            for word in sentence.split():
                if current and len(current) + 1 + len(word) > CAPTION_MAX_CHARS:
                    packed.append(current)
                    current = word
                else:
                    current = f"{current} {word}".strip()
            if current:
                packed.append(current)
        elif (
            packed
            and len(sentence) < _MERGE_SHORT_BELOW
            and len(packed[-1]) + 1 + len(sentence) <= CAPTION_MAX_CHARS
        ):
            packed[-1] = f"{packed[-1]} {sentence}"
        else:
            packed.append(sentence)
    return packed


def captions_from_text(
    text: str, total_seconds: float
) -> tuple[tuple[str, float, float], ...]:
    """Lay ``text`` out as cues spanning ``[0, total_seconds]`` by character weight.

    Sentences are packed into <=160-char cues; each cue's slice of the timeline
    is proportional to its length. Timing is approximate (it follows text length,
    not measured speech); real alignment is a later increment. Raises when the
    text is empty or a cue would be shorter than 1 ms.
    """

    if not isinstance(total_seconds, (int, float)) or total_seconds <= 0:
        raise SubtitleParseError("caption duration must be positive")
    cues_text = _pack_sentences(text)
    if not cues_text:
        raise SubtitleParseError("narration text has no caption-able content")
    lengths = [len(cue) for cue in cues_text]
    total_chars = sum(lengths)
    result: list[tuple[str, float, float]] = []
    consumed = 0
    for cue, length in zip(cues_text, lengths):
        start = consumed / total_chars * total_seconds
        consumed += length
        end = consumed / total_chars * total_seconds
        if round(end * 1000) <= round(start * 1000):
            raise SubtitleParseError(
                "narration text produces more cues than its duration can hold"
            )
        result.append((cue, start, end))
    return tuple(result)
