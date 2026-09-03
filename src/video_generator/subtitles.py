"""Turn caption text into plain (text, start, end) cues.

Stdlib only. This module does no I/O and no rendering: it parses persisted
SRT/WebVTT files, or lays narration text out over a duration, producing ordered
timing triples that the sequence workflow validates with the same rules as
inline caption items before FFmpeg burns them.
"""

from __future__ import annotations

import difflib
import re
import unicodedata

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

# Closed-class function words (pt-BR plus common English) that should not be left
# at the end of a caption line: the eye expects the next word, so a break here
# reads as broken. Used to nudge a lone trailing article/preposition/conjunction
# onto the following line when there is room.
_WEAK_TRAILING_WORDS = frozenset(
    {
        "a", "o", "e", "ou", "de", "do", "da", "dos", "das", "no", "na", "nos",
        "nas", "em", "num", "numa", "um", "uma", "uns", "umas", "que", "se",
        "com", "por", "para", "pra", "ao", "aos", "à", "às", "seu", "sua",
        "seus", "suas", "meu", "minha", "nosso", "nossa",
        "contra", "sobre", "entre", "sem", "sob", "após", "apos", "desde",
        "até", "ate", "perante", "mas", "porque", "pelo", "pela", "pelos",
        "pelas", "nem", "como",
        "the", "an", "of", "to", "in", "on", "and", "or", "for", "with", "that",
        "as", "at", "by",
    }
)


def _rebalance_weak_breaks(pieces: list[str], max_chars: int) -> list[str]:
    """Shift a lone trailing function word onto the next piece when it fits.

    A line or chunk that ends on a bare article, preposition or conjunction
    ("...olha para" | "o céu") reads as broken. Pieces that already end on
    sentence or clause punctuation are left alone, and a word is only moved when
    the receiving piece stays within ``max_chars`` and the donor keeps a word.
    Runs a few left-to-right passes so a short run of function words unwinds.
    """

    for _ in range(3):
        changed = False
        for index in range(len(pieces) - 1):
            words = pieces[index].split()
            if len(words) < 2:
                continue
            tail = words[-1]
            if tail.rstrip(_ENDERS + _CLAUSE_MARKS) != tail:
                continue  # ends on punctuation, not a bare word
            if tail.lower() not in _WEAK_TRAILING_WORDS:
                continue
            moved = f"{tail} {pieces[index + 1]}"
            if len(moved) > max_chars:
                continue
            pieces[index] = " ".join(words[:-1])
            pieces[index + 1] = moved
            changed = True
        if not changed:
            break
    return pieces


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
    return _rebalance_weak_breaks(fixed, CAPTION_CHUNK_MAX_CHARS)


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
    (never wider than :data:`CAPTION_CHUNK_MAX_CHARS`), then a lone article,
    preposition or conjunction left at a chunk's end is nudged onto the next
    chunk when it fits, so a break never lands on a function word. Each chunk's
    slice of the timeline is proportional to an estimated speaking time --
    syllable count plus a pause allowance for sentence- and clause-final
    punctuation -- so a slow, punctuated line holds longer than a short brisk
    one. The last chunk always
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
    chunks = _rebalance_weak_breaks(chunks, CAPTION_CHUNK_MAX_CHARS)
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


# --- captions measured against the rendered voice -------------------------

# How long a cue may stay on screen after its last word, while the speaker
# pauses. Holding the line reads better than blinking it out and back in; more
# than this and a stale caption sits over the next shot.
CAPTION_HOLD_SECONDS = 1.2
# Cues must not touch: the workflow rejects overlapping items.
_CUE_EPSILON = 0.002


def _normalize_word(word: str) -> str:
    """Fold a word to the form used for matching script text to a transcript."""

    decomposed = unicodedata.normalize("NFKD", word)
    stripped = "".join(
        character
        for character in decomposed
        if not unicodedata.combining(character) and (character.isalnum() or character.isspace())
    )
    return stripped.casefold().strip()


def _script_chunks(text: str) -> list[str]:
    """Split ``text`` into the same display chunks :func:`captions_from_text` uses."""

    sentences = [part.strip() for part in _SENTENCE_SPLIT.split(text.strip()) if part.strip()]
    chunks: list[str] = []
    for sentence in sentences:
        chunks.extend(_pack_sentence(sentence))
    if not chunks:
        raise SubtitleParseError("narration text has no caption-able content")
    chunks = _rebalance_weak_breaks(chunks, CAPTION_CHUNK_MAX_CHARS)
    if any(len(chunk) > CAPTION_MAX_CHARS for chunk in chunks):
        raise SubtitleParseError("a derived caption chunk exceeds the 160-character ceiling")
    return chunks


def _match_script_to_transcript(
    script_words: list[str], spoken: tuple[tuple[str, float, float], ...]
) -> dict[int, tuple[float, float]]:
    """Map script word positions onto measured spans, by longest-match diffing.

    The transcript is the model's own reading of the audio: it drops words,
    merges them, and spells numbers differently. Only the stretches that agree
    with the script are trusted; everything else is left unmapped for the caller
    to interpolate across.
    """

    left = [_normalize_word(word) for word in script_words]
    right = [_normalize_word(word) for word, _, _ in spoken]
    matcher = difflib.SequenceMatcher(a=left, b=right, autojunk=False)
    mapped: dict[int, tuple[float, float]] = {}
    for block in matcher.get_matching_blocks():
        for offset in range(block.size):
            _, start, end = spoken[block.b + offset]
            mapped[block.a + offset] = (start, end)
    return mapped


def cues_from_word_timings(
    text: str,
    spoken_words: tuple[tuple[str, float, float], ...],
    *,
    total_seconds: float,
) -> tuple[tuple[str, float, float], ...]:
    """Time ``text`` as caption cues against words actually heard in the audio.

    ``spoken_words`` are ``(word, start, end)`` triples measured in the rendered
    narration WAV. The script is split into the same short display chunks as
    :func:`captions_from_text`, then each chunk is anchored to the measured span
    of its own first and last words: a cue starts when its first word is spoken
    and ends when its last one does. Chunks whose words the transcript missed are
    interpolated between the anchors on either side, so a recognition gap shifts
    nothing outside it.

    Unlike :func:`captions_from_text`, this does not model tempo -- it reads it
    off the audio. Cues may leave gaps where the speaker pauses; a cue holds at
    most :data:`CAPTION_HOLD_SECONDS` past its last word.
    """

    if (
        isinstance(total_seconds, bool)
        or not isinstance(total_seconds, (int, float))
        or total_seconds <= 0
    ):
        raise SubtitleParseError("caption duration must be positive")
    if not isinstance(spoken_words, tuple) or not spoken_words:
        raise SubtitleParseError("alignment requires at least one measured word")
    previous = 0.0
    for word, start, end in spoken_words:
        if not isinstance(word, str) or not word.strip():
            raise SubtitleParseError("every measured word needs text")
        if end < start or start < previous - 1e-9:
            raise SubtitleParseError("measured words must be ordered and non-overlapping")
        previous = end

    chunks = _script_chunks(text)
    counts = [len(chunk.split()) for chunk in chunks]
    script_words = [word for chunk in chunks for word in chunk.split()]
    mapped = _match_script_to_transcript(script_words, spoken_words)
    if not mapped:
        raise SubtitleParseError("no script word could be matched to the measured audio")

    # per-chunk anchors from the first and last word of the chunk that matched
    spans: list[tuple[float | None, float | None]] = []
    position = 0
    for count in counts:
        indices = [index for index in range(position, position + count) if index in mapped]
        if indices:
            spans.append((mapped[indices[0]][0], mapped[indices[-1]][1]))
        else:
            spans.append((None, None))
        position += count

    # interpolate across the chunks the transcript never reached
    known = [index for index, span in enumerate(spans) if span[0] is not None]
    first_known, last_known = known[0], known[-1]
    resolved: list[tuple[float, float]] = []
    for index, (start, end) in enumerate(spans):
        if start is not None and end is not None:
            resolved.append((float(start), float(end)))
            continue
        before = max((item for item in known if item < index), default=None)
        after = min((item for item in known if item > index), default=None)
        if before is None:
            resolved.append((0.0, float(spans[first_known][0] or 0.0)))
        elif after is None:
            resolved.append((float(spans[last_known][1] or 0.0), float(total_seconds)))
        else:
            low = float(spans[before][1] or 0.0)
            high = float(spans[after][0] or total_seconds)
            step = (high - low) / (after - before)
            resolved.append(
                (low + step * (index - before), low + step * (index - before + 1))
            )

    # make the track monotonic, hold each line a little into the pause after it,
    # and keep every cue inside the timeline
    cues: list[tuple[str, float, float]] = []
    limit = float(total_seconds)
    floor = 0.0
    for index, (chunk, (start, end)) in enumerate(zip(chunks, resolved)):
        start = min(max(start, floor), limit)
        end = min(max(end, start + _CUE_EPSILON), limit)
        next_start = resolved[index + 1][0] if index + 1 < len(resolved) else limit
        hold_to = min(next_start - _CUE_EPSILON, end + CAPTION_HOLD_SECONDS, limit)
        if hold_to > end:
            end = hold_to
        if end <= start:
            raise SubtitleParseError("measured audio is too short to hold every caption chunk")
        cues.append((chunk, start, end))
        floor = end + _CUE_EPSILON
        if floor >= limit and index + 1 < len(chunks):
            raise SubtitleParseError("measured audio is too short to hold every caption chunk")
    return tuple(cues)


def _srt_timestamp(seconds: float) -> str:
    total_ms = int(round(seconds * 1000))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{whole:02d},{milliseconds:03d}"


def render_srt(cues: tuple[tuple[str, float, float], ...]) -> str:
    """Serialise ordered cues as SubRip text the parser accepts back.

    Written so an alignment can be persisted, reviewed and re-read by the
    sequence workflow instead of living only inside one render.
    """

    if not isinstance(cues, tuple) or not cues:
        raise SubtitleParseError("rendering SRT requires at least one cue")
    blocks: list[str] = []
    previous_end = None
    for index, (text, start, end) in enumerate(cues, start=1):
        clean = " ".join(str(text).split())
        if not clean:
            raise SubtitleParseError("cue has no text")
        if any(character in clean for character in "<>{}"):
            raise SubtitleParseError("cue text must not contain markup characters")
        if end <= start or (previous_end is not None and start < previous_end):
            raise SubtitleParseError("cues must be ordered and non-overlapping")
        previous_end = end
        blocks.append(
            f"{index}\n{_srt_timestamp(start)} {_ARROW} {_srt_timestamp(end)}\n{clean}\n"
        )
    return "\n".join(blocks)


CUE_ALIGN_TOLERANCE_SECONDS = 0.5
_MIN_CUE_SECONDS = 0.001
_EDGE_EPSILON = 1e-9


def align_cues_to_silences(
    cues: tuple[tuple[str, float, float], ...],
    silences: tuple[tuple[float, float], ...],
    *,
    tolerance_seconds: float = CUE_ALIGN_TOLERANCE_SECONDS,
) -> tuple[tuple[str, float, float], ...]:
    """Move cue boundaries onto the pauses actually heard in the narration.

    ``cues`` are contiguous cues over the voice (as :func:`captions_from_text`
    lays them out) and ``silences`` are the quiet spans measured in that same
    audio. Every internal boundary is offered the closest silence within
    ``tolerance_seconds``, best match first, each silence serving one boundary;
    a boundary lands on the middle of its silence, so the line stays up across
    the pause instead of blinking out in it. Boundaries with no silence nearby
    keep their estimated time, and the first start and the last end are never
    moved -- the track still begins and ends with the voice.

    This measures where the speaker stopped, not which word was said: it sharpens
    the estimate, it is not forced alignment.
    """

    if not isinstance(cues, tuple) or not cues:
        raise SubtitleParseError("alignment requires at least one cue")
    if (
        isinstance(tolerance_seconds, bool)
        or not isinstance(tolerance_seconds, (int, float))
        or tolerance_seconds <= 0
    ):
        raise SubtitleParseError("alignment tolerance must be positive")
    previous_end = None
    for _, start, end in cues:
        if end <= start or (previous_end is not None and abs(start - previous_end) > 1e-9):
            raise SubtitleParseError("alignment requires ordered, contiguous cues")
        previous_end = end
    if len(cues) == 1 or not silences:
        return cues

    # boundary i separates cue i - 1 from cue i; the outer edges stay put
    edges = [cues[0][1], *(cue[1] for cue in cues[1:]), cues[-1][2]]
    # Head and tail quiet is where the voice has not started or has already
    # stopped, not a pause between two lines, so only silences that both begin
    # and end inside the spoken span can anchor a boundary.
    anchors = [
        (start + end) / 2
        for start, end in silences
        if start > edges[0] + _EDGE_EPSILON and end < edges[-1] - _EDGE_EPSILON
    ]
    if not anchors:
        return cues

    candidates = sorted(
        (
            (abs(anchor - edges[boundary]), boundary, index)
            for boundary in range(1, len(edges) - 1)
            for index, anchor in enumerate(anchors)
            if abs(anchor - edges[boundary]) <= tolerance_seconds
        ),
        # distance decides; the indices only keep ties deterministic
        key=lambda candidate: (candidate[0], candidate[1], candidate[2]),
    )
    moved: set[int] = set()
    used: set[int] = set()
    for _, boundary, index in candidates:
        if boundary in moved or index in used:
            continue
        anchor = anchors[index]
        # never let a snap cross a neighbouring boundary or squeeze a cue away
        if (
            anchor - edges[boundary - 1] < _MIN_CUE_SECONDS
            or edges[boundary + 1] - anchor < _MIN_CUE_SECONDS
        ):
            continue
        edges[boundary] = anchor
        moved.add(boundary)
        used.add(index)
    return tuple(
        (text, edges[index], edges[index + 1]) for index, (text, _, _) in enumerate(cues)
    )
