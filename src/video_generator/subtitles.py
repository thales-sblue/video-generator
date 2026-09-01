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


# A derived caption is one short line on a 1280x720 frame: keep it well under the
# 160-char contract ceiling so libass never needs more than two lines, and merge
# only genuinely tiny fragments so cues stay quick to read without becoming terse.
CAPTION_CHUNK_MAX_CHARS = 50
CAPTION_CHUNK_SOFT_MIN = 26

_CLAUSE_SPLIT = re.compile(r"(?<=[,;:—–])\s+")
_VOWEL_GROUP = re.compile(r"[aeiouy]+")
_ENDERS = ".!?…"
_CLAUSE_MARKS = ",;:—–"

# Weights are in "syllable-equivalents": a spoken pause after a sentence or a
# clause costs roughly this many syllables of time.
_PAUSE_ENDER = 3.0
_PAUSE_CLAUSE = 1.2


def _estimate_syllables(word: str) -> int:
    letters = "".join(character for character in word if character.isalpha()).lower()
    if not letters:
        return 0
    groups = len(_VOWEL_GROUP.findall(letters))
    if groups > 1 and letters.endswith("e"):
        groups -= 1  # trailing "e" is usually silent
    return max(1, groups)


def _chunk_weight(chunk: str) -> float:
    syllables = sum(_estimate_syllables(word) for word in chunk.split()) or 1
    weight = float(syllables)
    last = chunk.rstrip()[-1:]
    if last in _ENDERS:
        weight += _PAUSE_ENDER
    elif last in _CLAUSE_MARKS:
        weight += _PAUSE_CLAUSE
    return weight


def _wrap_words(piece: str) -> list[str]:
    """Split ``piece`` into balanced lines, each within the chunk limit.

    The number of lines is fixed at the minimum that fits, then words are packed
    to an even target width so the last line is never a lone orphan word.
    """

    words = piece.split()
    if not words:
        return []
    lines_needed = max(1, -(-len(piece) // CAPTION_CHUNK_MAX_CHARS))
    target = -(-len(piece) // lines_needed)
    parts: list[str] = []
    current = ""
    for word in words:
        limit = max(target, len(word))
        if current and len(current) + 1 + len(word) > limit:
            parts.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        parts.append(current)
    # a balanced target can still overshoot on very long words: hard-wrap those.
    fixed: list[str] = []
    for part in parts:
        while len(part) > CAPTION_CHUNK_MAX_CHARS and " " in part:
            head, _, tail = part[:CAPTION_CHUNK_MAX_CHARS].rpartition(" ")
            fixed.append(head)
            part = f"{tail}{part[CAPTION_CHUNK_MAX_CHARS:]}"
        fixed.append(part)
    return fixed


def _pack_sentence(sentence: str) -> list[str]:
    """Break one sentence into short display chunks at natural boundaries."""

    units: list[str] = []
    for clause in _CLAUSE_SPLIT.split(sentence.strip()):
        clause = clause.strip()
        if not clause:
            continue
        if len(clause) <= CAPTION_CHUNK_MAX_CHARS:
            units.append(clause)
        else:
            units.extend(_wrap_words(clause))
    packed: list[str] = []
    for unit in units:
        if (
            packed
            and (len(packed[-1]) < CAPTION_CHUNK_SOFT_MIN or len(unit) < CAPTION_CHUNK_SOFT_MIN)
            and len(packed[-1]) + 1 + len(unit) <= CAPTION_CHUNK_MAX_CHARS
        ):
            packed[-1] = f"{packed[-1]} {unit}"
        else:
            packed.append(unit)
    return packed


def captions_from_text(
    text: str, total_seconds: float
) -> tuple[tuple[str, float, float], ...]:
    """Lay ``text`` out as short cues spanning ``[0, total_seconds]``.

    The text is split into one-line chunks at sentence and clause boundaries
    (never wider than :data:`CAPTION_CHUNK_MAX_CHARS`). Each chunk's slice of the
    timeline is proportional to an estimated speaking time -- syllable count plus
    a pause allowance for sentence- and clause-final punctuation -- so a slow,
    punctuated line holds longer than a short brisk one. The last chunk always
    ends exactly at ``total_seconds`` (the real narration length passed by the
    workflow), so no caption lingers past the voice.

    Timing stays approximate: it models tempo, it does not measure the rendered
    audio. Forced alignment (local Whisper) is a later increment. Raises when the
    text is empty or a chunk would be shorter than 1 ms.
    """

    if (
        isinstance(total_seconds, bool)
        or not isinstance(total_seconds, (int, float))
        or total_seconds <= 0
    ):
        raise SubtitleParseError("caption duration must be positive")
    sentences = [part.strip() for part in _SENTENCE_SPLIT.split(text.strip()) if part.strip()]
    chunks: list[str] = []
    for sentence in sentences:
        chunks.extend(_pack_sentence(sentence))
    if not chunks:
        raise SubtitleParseError("narration text has no caption-able content")
    if any(len(chunk) > CAPTION_MAX_CHARS for chunk in chunks):
        raise SubtitleParseError("a derived caption chunk exceeds the 160-character ceiling")
    weights = [_chunk_weight(chunk) for chunk in chunks]
    total_weight = sum(weights)
    result: list[tuple[str, float, float]] = []
    consumed = 0.0
    for chunk, weight in zip(chunks, weights):
        start = consumed / total_weight * total_seconds
        consumed += weight
        end = consumed / total_weight * total_seconds
        if round(end * 1000) <= round(start * 1000):
            raise SubtitleParseError(
                "narration text produces more cues than its duration can hold"
            )
        result.append((chunk, start, end))
    return tuple(result)
