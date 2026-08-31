"""Parse persisted SRT/WebVTT caption files into plain (text, start, end) cues.

Stdlib only. This module does no I/O and no rendering: it turns already-read
subtitle text into ordered timing triples that the sequence workflow validates
with the same rules as inline caption items before FFmpeg burns them.
"""

from __future__ import annotations

import re

SUPPORTED_FORMATS = ("srt", "vtt")

_TIMESTAMP = re.compile(
    r"^(?:(?P<h>\d+):)?(?P<m>[0-5]?\d):(?P<s>[0-5]?\d)[.,](?P<ms>\d{1,3})$"
)
_ARROW = "-->"
_VTT_BLOCK_KEYWORDS = ("NOTE", "STYLE", "REGION")


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
