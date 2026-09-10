"""Read a recorded take like a content editor and suggest, never make, cuts.

Pure and stdlib-only: no I/O, no transcription, no FFmpeg. Something upstream
measures the take -- a local Whisper model for the phrases and their timestamps,
``silencedetect`` for the pauses -- and this module reads the whole transcript at
once, comparing each stretch with what came before and after, and marks it
``KEEP``, ``REVIEW`` or ``CUT`` with a semantic ``category``, a human-readable
reason and a confidence in ``0..1``.

It never removes anything and it never rewrites the transcript. ``CUT`` is a
suggestion; the final decision is a person. The deterministic heuristics here
propose candidates -- especially whole ``removable_blocks`` -- and cannot judge
whether a line carries personality or humour: that stays with the editor (the
orchestrating agent, or a future opt-in local model), and a review can be
re-authored with ``provenance = "agent"``.
"""

from __future__ import annotations

import difflib
import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass

from video_generator.domain import takes_lexicon as lex

CATEGORIES = (
    "REPETITION",
    "TANGENT",
    "FILLER",
    "SELF_COMMENTARY",
    "OFF_TOPIC",
    "FAILED_TAKE",
    "REDUNDANT_EXAMPLE",
    "LONG_PAUSE",
    "NONE",
)
SUGGESTIONS = ("KEEP", "REVIEW", "CUT")
PROVENANCE = ("heuristic", "agent")
_RANK = {"KEEP": 0, "REVIEW": 1, "CUT": 2}

# A pause longer than this, wholly inside the spoken span, earns its own row.
LONG_SILENCE_SECONDS = 1.5

# How many earlier phrases to scan when deciding a phrase is a restatement.
REPEAT_WINDOW = 8
REPEAT_RESTATE_SIMILARITY = 0.60
REPEAT_NEAR_DUPLICATE_SIMILARITY = 0.85

# Rolling window for filler density, and the share that counts as "too much".
FILLER_WINDOW_SECONDS = 25.0
FILLER_MIN_SHARE = 0.15
FILLER_MIN_IN_SEGMENT = 2

# After the presenter signals a digression, it keeps running while its overlap
# with the video's dominant vocabulary stays under this; at or above it, the
# talk is back on subject.
TANGENT_RETURN_OVERLAP = 0.30
DOMINANT_VOCAB_SIZE = 15

# Two example scaffolds closer than this, the second adding almost no new
# content, is a redundant example.
REDUNDANT_EXAMPLE_MAX_GAP_SECONDS = 40.0

# The take should be "in the subject" by here; trailing wind-down past here reads
# as a long outro.
INTRO_GRACE_SECONDS = 25.0
OUTRO_GRACE_SECONDS = 20.0

_TERMINAL_PUNCT = ".!?…"
_WORD_RE = re.compile(r"[a-z0-9]+")


class TakesError(ValueError):
    """Raised when a take cannot be reviewed for cut points."""


def _finite_nonneg(value: object, name: str) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or value != value  # NaN
        or value in (float("inf"), float("-inf"))
        or value < 0
    ):
        raise TakesError(f"{name} must be a finite, non-negative number")


def _normalize(text: str) -> str:
    """Lowercase, accent-free form, internal spacing kept, for matching."""

    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return stripped.casefold().strip()


def _tokens(text: str) -> list[str]:
    return _WORD_RE.findall(_normalize(text))


_STOPWORDS = frozenset(_normalize(word) for word in lex.STOPWORDS)
_FILLER_WORDS = frozenset(_normalize(word) for word in lex.FILLER_WORDS)
_FILLER_BIGRAMS = frozenset(
    (_normalize(a), _normalize(b)) for a, b in lex.FILLER_BIGRAMS
)
_SELF_COMMENTARY = tuple(_normalize(p) for p in lex.SELF_COMMENTARY_PHRASES)
_SCOPE_WIDENING = tuple(_normalize(p) for p in lex.SCOPE_WIDENING_MARKERS)
_RESTART_MARKERS = tuple(_normalize(p) for p in lex.RESTART_MARKERS)
_EXAMPLE_MARKERS = tuple(_normalize(p) for p in lex.EXAMPLE_MARKERS)


def _content_bag(text: str) -> Counter[str]:
    """Token multiset with stopwords and one-letter tokens removed."""

    return Counter(
        token
        for token in _tokens(text)
        if len(token) > 1 and token not in _STOPWORDS
    )


def _cosine(a: Counter[str], b: Counter[str]) -> float:
    if not a or not b:
        return 0.0
    shared = set(a) & set(b)
    dot = sum(a[t] * b[t] for t in shared)
    if dot == 0:
        return 0.0
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb)


def _count_fillers(tokens: list[str]) -> int:
    hits = sum(1 for token in tokens if token in _FILLER_WORDS)
    hits += sum(
        1
        for first, second in zip(tokens, tokens[1:])
        if (first, second) in _FILLER_BIGRAMS
    )
    return hits


def _clock(seconds: float, *, millis: bool) -> str:
    seconds = max(0.0, float(seconds))
    if millis:
        total_ms = int(round(seconds * 1000))
        minutes, remainder = divmod(total_ms // 1000, 60)
        return f"{minutes:02d}:{remainder:02d}.{total_ms % 1000:03d}"
    minutes, remainder = divmod(int(round(seconds)), 60)
    return f"{minutes:02d}:{remainder:02d}"


def _seconds_from_clock(text: str) -> float:
    if not isinstance(text, str):
        raise TakesError("a clock value must be a string")
    body, _, millis = text.partition(".")
    minutes, _, secs = body.partition(":")
    try:
        total = int(minutes) * 60 + int(secs)
    except ValueError as exc:
        raise TakesError(f"invalid clock value: {text!r}") from exc
    if millis:
        total += int(millis.ljust(3, "0")[:3]) / 1000
    return float(total)


def _union_seconds(spans: list[tuple[float, float]]) -> float:
    total = 0.0
    current_start: float | None = None
    current_end: float | None = None
    for start, end in sorted(spans):
        if current_end is None or start > current_end:
            if current_end is not None:
                total += current_end - current_start
            current_start, current_end = start, end
        else:
            current_end = max(current_end, end)
    if current_end is not None:
        total += current_end - current_start
    return total


@dataclass(frozen=True, slots=True)
class TranscriptSegment:
    """One stretch of speech the transcriber returned, and when it was said."""

    text: str
    start_seconds: float
    end_seconds: float

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or not self.text.strip():
            raise TakesError("segment text must be a non-empty string")
        _finite_nonneg(self.start_seconds, "segment start")
        _finite_nonneg(self.end_seconds, "segment end")
        if self.end_seconds <= self.start_seconds:
            raise TakesError("segment end must be after its start")


@dataclass(frozen=True, slots=True)
class SilenceSpan:
    """A quiet span measured in the same audio, in seconds."""

    start_seconds: float
    end_seconds: float

    def __post_init__(self) -> None:
        _finite_nonneg(self.start_seconds, "silence start")
        _finite_nonneg(self.end_seconds, "silence end")
        if self.end_seconds <= self.start_seconds:
            raise TakesError("silence end must be after its start")

    @property
    def duration_seconds(self) -> float:
        return self.end_seconds - self.start_seconds


@dataclass(frozen=True, slots=True)
class ReviewedSegment:
    """One row of the review: a span, its text, and the suggested action."""

    start_seconds: float
    end_seconds: float
    text: str
    suggestion: str
    category: str
    reason: str
    confidence: float

    def __post_init__(self) -> None:
        _finite_nonneg(self.start_seconds, "reviewed start")
        _finite_nonneg(self.end_seconds, "reviewed end")
        if self.end_seconds <= self.start_seconds:
            raise TakesError("reviewed end must be after its start")
        if self.suggestion not in SUGGESTIONS:
            raise TakesError(f"suggestion must be one of {SUGGESTIONS}")
        if self.category not in CATEGORIES:
            raise TakesError(f"category must be one of {CATEGORIES}")
        if not isinstance(self.text, str) or not self.text.strip():
            raise TakesError("reviewed text must be a non-empty string")
        if not isinstance(self.reason, str):
            raise TakesError("reason must be a string")
        if self.suggestion == "KEEP":
            if self.reason or self.category != "NONE":
                raise TakesError("a KEEP segment carries no reason or category")
        else:
            if not self.reason.strip() or self.category == "NONE":
                raise TakesError("a flagged segment needs a reason and a category")
        _finite_nonneg(self.confidence, "confidence")
        if self.confidence > 1:
            raise TakesError("confidence must be within 0..1")

    def to_dict(self) -> dict[str, object]:
        return {
            "start": _clock(self.start_seconds, millis=True),
            "end": _clock(self.end_seconds, millis=True),
            "text": self.text,
            "suggestion": self.suggestion,
            "category": self.category,
            "reason": self.reason,
            "confidence": round(float(self.confidence), 2),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "ReviewedSegment":
        try:
            return cls(
                start_seconds=_seconds_from_clock(payload["start"]),
                end_seconds=_seconds_from_clock(payload["end"]),
                text=str(payload["text"]),
                suggestion=str(payload["suggestion"]),
                category=str(payload.get("category", "NONE")),
                reason=str(payload.get("reason", "")),
                confidence=float(payload.get("confidence", 0.0)),
            )
        except (KeyError, TypeError) as exc:
            raise TakesError(f"malformed reviewed segment: {exc}") from exc


@dataclass(frozen=True, slots=True)
class RemovableBlock:
    """A run of adjacent flagged rows worth pulling out as one edit."""

    start_seconds: float
    end_seconds: float
    suggestion: str
    category: str
    reason: str
    confidence: float

    def __post_init__(self) -> None:
        _finite_nonneg(self.start_seconds, "block start")
        _finite_nonneg(self.end_seconds, "block end")
        if self.end_seconds <= self.start_seconds:
            raise TakesError("block end must be after its start")
        if self.suggestion not in ("REVIEW", "CUT"):
            raise TakesError("a block is REVIEW or CUT")
        if self.category not in CATEGORIES or self.category == "NONE":
            raise TakesError("a block needs a real category")
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise TakesError("a block needs a reason")
        _finite_nonneg(self.confidence, "block confidence")
        if self.confidence > 1:
            raise TakesError("block confidence must be within 0..1")

    @property
    def seconds(self) -> float:
        return self.end_seconds - self.start_seconds

    def to_dict(self) -> dict[str, object]:
        return {
            "start": _clock(self.start_seconds, millis=True),
            "end": _clock(self.end_seconds, millis=True),
            "seconds": round(self.seconds, 1),
            "suggestion": self.suggestion,
            "category": self.category,
            "reason": self.reason,
            "confidence": round(float(self.confidence), 2),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "RemovableBlock":
        try:
            return cls(
                start_seconds=_seconds_from_clock(payload["start"]),
                end_seconds=_seconds_from_clock(payload["end"]),
                suggestion=str(payload["suggestion"]),
                category=str(payload["category"]),
                reason=str(payload["reason"]),
                confidence=float(payload.get("confidence", 0.0)),
            )
        except (KeyError, TypeError) as exc:
            raise TakesError(f"malformed removable block: {exc}") from exc


@dataclass(frozen=True, slots=True)
class EditSummary:
    """The headline numbers and a plain-language note on the pacing."""

    original_duration_seconds: float
    estimated_duration_seconds: float
    top_removable: tuple[RemovableBlock, ...]
    rhythm_note: str

    def __post_init__(self) -> None:
        _finite_nonneg(self.original_duration_seconds, "original duration")
        _finite_nonneg(self.estimated_duration_seconds, "estimated duration")
        if self.estimated_duration_seconds > self.original_duration_seconds + 1e-6:
            raise TakesError("estimated duration cannot exceed the original")
        if not isinstance(self.top_removable, tuple):
            raise TakesError("top_removable must be a tuple")
        if not isinstance(self.rhythm_note, str) or not self.rhythm_note.strip():
            raise TakesError("rhythm_note must be a non-empty string")

    def to_dict(self) -> dict[str, object]:
        removed = max(
            0.0, self.original_duration_seconds - self.estimated_duration_seconds
        )
        return {
            "original_duration": _clock(self.original_duration_seconds, millis=False),
            "estimated_duration_after_cuts": _clock(
                self.estimated_duration_seconds, millis=False
            ),
            "removed_seconds_estimate": round(removed, 1),
            "top_removable": [block.to_dict() for block in self.top_removable],
            "rhythm_note": self.rhythm_note,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "EditSummary":
        try:
            return cls(
                original_duration_seconds=_seconds_from_clock(
                    payload["original_duration"]
                ),
                estimated_duration_seconds=_seconds_from_clock(
                    payload["estimated_duration_after_cuts"]
                ),
                top_removable=tuple(
                    RemovableBlock.from_dict(item)
                    for item in payload.get("top_removable", ())
                ),
                rhythm_note=str(payload["rhythm_note"]),
            )
        except (KeyError, TypeError) as exc:
            raise TakesError(f"malformed edit summary: {exc}") from exc


@dataclass(frozen=True, slots=True)
class CutReview:
    """The reviewable output: every row, the blocks, and the edit summary."""

    source_path: str
    language: str
    provenance: str
    duration_seconds: float
    segments: tuple[ReviewedSegment, ...]
    blocks: tuple[RemovableBlock, ...]
    summary: EditSummary

    def __post_init__(self) -> None:
        if not isinstance(self.source_path, str) or not self.source_path.strip():
            raise TakesError("source_path must be a non-empty string")
        if not isinstance(self.language, str) or not self.language.strip():
            raise TakesError("language must be a non-empty string")
        if self.provenance not in PROVENANCE:
            raise TakesError(f"provenance must be one of {PROVENANCE}")
        _finite_nonneg(self.duration_seconds, "duration")
        if not isinstance(self.segments, tuple) or not self.segments:
            raise TakesError("a review needs at least one segment")
        previous_start = 0.0
        for segment in self.segments:
            if not isinstance(segment, ReviewedSegment):
                raise TakesError("every segment must be a ReviewedSegment")
            if segment.start_seconds + 1e-6 < previous_start:
                raise TakesError("reviewed segments must be in time order")
            previous_start = segment.start_seconds
        if not isinstance(self.blocks, tuple):
            raise TakesError("blocks must be a tuple")
        for block in self.blocks:
            if not isinstance(block, RemovableBlock):
                raise TakesError("every block must be a RemovableBlock")
        if not isinstance(self.summary, EditSummary):
            raise TakesError("summary must be an EditSummary")

    def counts(self) -> dict[str, int]:
        tally = {name: 0 for name in SUGGESTIONS}
        for segment in self.segments:
            tally[segment.suggestion] += 1
        return tally

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "provenance": self.provenance,
            "source_path": self.source_path,
            "language": self.language,
            "duration": _clock(self.duration_seconds, millis=False),
            "segments": [segment.to_dict() for segment in self.segments],
            "blocks": [block.to_dict() for block in self.blocks],
            "summary": self.summary.to_dict(),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "CutReview":
        if not isinstance(payload, dict):
            raise TakesError("a review payload must be an object")
        if payload.get("schema_version") != 1:
            raise TakesError("unsupported cut-review schema_version")
        try:
            return cls(
                source_path=str(payload["source_path"]),
                language=str(payload["language"]),
                provenance=str(payload.get("provenance", "agent")),
                duration_seconds=_seconds_from_clock(payload["duration"]),
                segments=tuple(
                    ReviewedSegment.from_dict(item) for item in payload["segments"]
                ),
                blocks=tuple(
                    RemovableBlock.from_dict(item)
                    for item in payload.get("blocks", ())
                ),
                summary=EditSummary.from_dict(payload["summary"]),
            )
        except (KeyError, TypeError) as exc:
            raise TakesError(f"malformed cut review: {exc}") from exc


# --- detectors -----------------------------------------------------------
# Each returns proposals as (index, suggestion, category, reason, confidence).

_Proposal = tuple[int, str, str, str, float]


def _detect_failed_takes(segments: tuple[TranscriptSegment, ...]) -> list[_Proposal]:
    out: list[_Proposal] = []
    for i, segment in enumerate(segments):
        norm = _normalize(segment.text)
        if any(norm.startswith(marker) for marker in _RESTART_MARKERS):
            out.append((i, "REVIEW", "FAILED_TAKE", "recomeço de explicação", 0.6))
            if i > 0:
                out.append(
                    (
                        i - 1,
                        "CUT",
                        "FAILED_TAKE",
                        "recomeço logo em seguida; provável tentativa abandonada",
                        0.6,
                    )
                )
        # short fragment, no terminal punctuation, words barely reused next
        if i < len(segments) - 1 and segment.text.rstrip()[-1:] not in _TERMINAL_PUNCT:
            here = set(_tokens(segment.text))
            after = set(_tokens(segments[i + 1].text))
            if here and after and len(here) <= 6:
                if len(here & after) / len(here) <= 0.3:
                    out.append(
                        (i, "REVIEW", "FAILED_TAKE", "frase iniciada e abandonada", 0.5)
                    )
    return out


def _detect_repetition(segments: tuple[TranscriptSegment, ...]) -> list[_Proposal]:
    bags = [_content_bag(segment.text) for segment in segments]
    token_lists = [_tokens(segment.text) for segment in segments]
    out: list[_Proposal] = []
    for i in range(len(segments)):
        if sum(bags[i].values()) < 3:
            continue
        best = 0.0
        best_j = -1
        near_identical = False
        for j in range(max(0, i - REPEAT_WINDOW), i):
            if sum(bags[j].values()) < 3:
                continue
            cosine = _cosine(bags[i], bags[j])
            ratio = difflib.SequenceMatcher(
                a=token_lists[j], b=token_lists[i], autojunk=False
            ).ratio()
            similarity = max(cosine, ratio)
            if similarity > best:
                best, best_j = similarity, j
                # only a re-take when word bag *and* word order both say so;
                # the token ratio alone over-scores short phrases
                near_identical = min(cosine, ratio) >= REPEAT_NEAR_DUPLICATE_SIMILARITY
        if best_j < 0:
            continue
        if near_identical:
            # near-identical is almost always a re-take: the later pass is the
            # clean one, so cut the earlier attempt.
            out.append(
                (
                    best_j,
                    "CUT",
                    "REPETITION",
                    "regravado logo depois quase igual; a versão à frente é a boa",
                    round(best, 2),
                )
            )
        elif best >= REPEAT_RESTATE_SIMILARITY:
            # a looser echo is a restatement: the point was already made, so the
            # later phrasing is the redundant one.
            out.append(
                (
                    i,
                    "REVIEW",
                    "REPETITION",
                    "reafirma um ponto que já ficou claro antes",
                    round(best, 2),
                )
            )
    return out


def _detect_filler(segments: tuple[TranscriptSegment, ...]) -> list[_Proposal]:
    out: list[_Proposal] = []
    for i, segment in enumerate(segments):
        low = segment.start_seconds - FILLER_WINDOW_SECONDS / 2
        high = segment.end_seconds + FILLER_WINDOW_SECONDS / 2
        window = [
            other
            for other in segments
            if other.end_seconds > low and other.start_seconds < high
        ]
        tokens = [token for other in window for token in _tokens(other.text)]
        if len(tokens) < 12:
            continue
        own = _count_fillers(_tokens(segment.text))
        share = _count_fillers(tokens) / len(tokens)
        if share >= FILLER_MIN_SHARE and own >= FILLER_MIN_IN_SEGMENT:
            out.append(
                (
                    i,
                    "REVIEW",
                    "FILLER",
                    f"muita muleta de linguagem nesta parte ({own} neste trecho)",
                    round(min(0.4 + 0.08 * own, 0.85), 2),
                )
            )
    return out


def _detect_self_commentary(segments: tuple[TranscriptSegment, ...]) -> list[_Proposal]:
    out: list[_Proposal] = []
    for i, segment in enumerate(segments):
        norm = _normalize(segment.text)
        if any(phrase in norm for phrase in _SELF_COMMENTARY):
            content = sum(_content_bag(segment.text).values())
            level = "CUT" if content <= 6 else "REVIEW"
            out.append(
                (
                    i,
                    level,
                    "SELF_COMMENTARY",
                    "comentário do apresentador sobre a própria fala, não sobre o tema",
                    0.6,
                )
            )
    return out


def _dominant_vocab(bags: list[Counter[str]]) -> set[str]:
    """The words that recur across the take, not the ones a tangent mentions once.

    A term counts as dominant only if it appears in several *distinct* segments,
    so a digression's own vocabulary (said a lot, but only within the
    digression) never joins it. The most-frequent of those, capped at
    :data:`DOMINANT_VOCAB_SIZE`, is the video's subject.
    """

    doc_freq: Counter[str] = Counter()
    for bag in bags:
        doc_freq.update(set(bag))
    threshold = max(2, round(len(bags) * 0.15))
    recurring = {word for word, freq in doc_freq.items() if freq >= threshold}
    if not recurring:
        return set()
    total: Counter[str] = Counter()
    for bag in bags:
        total.update(bag)
    ranked = [word for word, _ in total.most_common() if word in recurring]
    return set(ranked[:DOMINANT_VOCAB_SIZE])


def _detect_tangents(segments: tuple[TranscriptSegment, ...]) -> list[_Proposal]:
    bags = [_content_bag(segment.text) for segment in segments]
    dominant = _dominant_vocab(bags)
    if len(dominant) < 3:
        return []
    overlap: list[float] = []
    for bag in bags:
        size = sum(bag.values())
        overlap.append(
            sum(count for word, count in bag.items() if word in dominant) / size
            if size
            else 1.0
        )
    # Anchor on the presenter announcing a widening of scope, then extend the
    # digression forward only while the talk stays off the dominant vocabulary.
    # No marker, no flag: a marker-less "low topic overlap" run is just how
    # natural speech phrases things, and flagging it buries the real signal.
    out: list[_Proposal] = []
    index = 0
    while index < len(segments):
        norm = _normalize(segments[index].text)
        if any(marker in norm for marker in _SCOPE_WIDENING):
            end = index
            while (
                end + 1 < len(segments)
                and overlap[end + 1] < TANGENT_RETURN_OVERLAP
                and end - index < 10
            ):
                end += 1
            confidence = round(min(0.6 + 0.03 * (end - index), 0.85), 2)
            for k in range(index, end + 1):
                out.append(
                    (
                        k,
                        "REVIEW",
                        "TANGENT",
                        "desvio do tema central sinalizado pelo próprio apresentador",
                        confidence,
                    )
                )
            index = end + 1
        else:
            index += 1
    return out


def _detect_redundant_examples(
    segments: tuple[TranscriptSegment, ...]
) -> list[_Proposal]:
    marked = [
        i
        for i, segment in enumerate(segments)
        if any(marker in _normalize(segment.text) for marker in _EXAMPLE_MARKERS)
    ]
    out: list[_Proposal] = []
    for earlier, later in zip(marked, marked[1:]):
        gap = segments[later].start_seconds - segments[earlier].start_seconds
        if gap > REDUNDANT_EXAMPLE_MAX_GAP_SECONDS:
            continue
        fresh = _content_bag(segments[later].text) - _content_bag(segments[earlier].text)
        if sum(fresh.values()) <= 2:
            out.append(
                (
                    later,
                    "REVIEW",
                    "REDUNDANT_EXAMPLE",
                    "segundo exemplo seguido que não acrescenta ideia nova",
                    0.45,
                )
            )
    return out


def _detect_long_intro_outro(
    segments: tuple[TranscriptSegment, ...]
) -> list[_Proposal]:
    bags = [_content_bag(segment.text) for segment in segments]
    dominant = _dominant_vocab(bags)
    if len(dominant) < 3 or len(segments) < 6:
        return []

    def touches_topic(index: int) -> bool:
        return any(word in dominant for word in bags[index])

    out: list[_Proposal] = []
    # a leading run where the subject is not named once, longer than the grace
    first = next((i for i in range(len(segments)) if touches_topic(i)), None)
    if (
        first is not None
        and first >= 3
        and segments[first].start_seconds > INTRO_GRACE_SECONDS
    ):
        for i in range(first):
            out.append(
                (
                    i,
                    "REVIEW",
                    "SELF_COMMENTARY",
                    "introdução longa antes de o assunto aparecer",
                    0.4,
                )
            )
    last = next(
        (i for i in range(len(segments) - 1, -1, -1) if touches_topic(i)), None
    )
    if (
        last is not None
        and last <= len(segments) - 4
        and segments[-1].end_seconds - segments[last].end_seconds > OUTRO_GRACE_SECONDS
    ):
        for i in range(last + 1, len(segments)):
            out.append(
                (
                    i,
                    "REVIEW",
                    "SELF_COMMENTARY",
                    "encerramento se estende depois de o assunto terminar",
                    0.4,
                )
            )
    return out


_DETECTORS = (
    _detect_failed_takes,
    _detect_repetition,
    _detect_filler,
    _detect_self_commentary,
    _detect_tangents,
    _detect_redundant_examples,
    _detect_long_intro_outro,
)


def _silence_rows(
    segments: tuple[TranscriptSegment, ...],
    silences: tuple[SilenceSpan, ...],
    long_silence_seconds: float,
) -> list[ReviewedSegment]:
    span_start = segments[0].start_seconds
    span_end = segments[-1].end_seconds
    rows: list[ReviewedSegment] = []
    for silence in silences:
        seconds = silence.duration_seconds
        if seconds + 1e-9 < long_silence_seconds:
            continue
        if (
            silence.start_seconds <= span_start + 1e-9
            or silence.end_seconds >= span_end - 1e-9
        ):
            continue
        confidence = min(0.5 + (seconds - long_silence_seconds) * 0.15, 0.95)
        rows.append(
            ReviewedSegment(
                start_seconds=silence.start_seconds,
                end_seconds=silence.end_seconds,
                text=f"[silêncio {seconds:.1f} s]",
                suggestion="CUT",
                category="LONG_PAUSE",
                reason=f"pausa longa de {seconds:.1f} s",
                confidence=round(confidence, 2),
            )
        )
    return rows


_BLOCK_JOIN_GAP_SECONDS = 1.5
_BLOCK_MIN_SECONDS = 6.0
_BLOCK_MIN_ROWS = 3


def _block_reason(category: str, seconds: float, rows: int) -> str:
    label = {
        "REPETITION": "reafirma pontos já ditos",
        "TANGENT": "desvio do tema central",
        "FILLER": "muita enrolação",
        "SELF_COMMENTARY": "apartes do apresentador sobre a própria fala",
        "OFF_TOPIC": "assunto fora do foco do vídeo",
        "FAILED_TAKE": "tentativas recomeçadas",
        "REDUNDANT_EXAMPLE": "exemplos que se repetem",
        "LONG_PAUSE": "pausas longas",
    }.get(category, "trecho removível")
    return f"{seconds:.0f} s, {rows} trecho(s): {label}"


def _build_blocks(reviewed: list[ReviewedSegment]) -> tuple[RemovableBlock, ...]:
    runs: list[list[ReviewedSegment]] = []
    current: list[ReviewedSegment] = []
    for row in reviewed:
        if row.suggestion == "KEEP":
            if current:
                runs.append(current)
                current = []
            continue
        if current and row.start_seconds - current[-1].end_seconds <= _BLOCK_JOIN_GAP_SECONDS:
            current.append(row)
        else:
            if current:
                runs.append(current)
            current = [row]
    if current:
        runs.append(current)

    blocks: list[RemovableBlock] = []
    for run in runs:
        start = run[0].start_seconds
        end = max(row.end_seconds for row in run)
        span = end - start
        if span < _BLOCK_MIN_SECONDS and len(run) < _BLOCK_MIN_ROWS:
            continue
        by_category: Counter[str] = Counter(row.category for row in run)
        dominant = max(
            by_category,
            key=lambda category: (
                by_category[category],
                sum(row.confidence for row in run if row.category == category),
            ),
        )
        suggestion = "CUT" if any(row.suggestion == "CUT" for row in run) else "REVIEW"
        confidence = round(sum(row.confidence for row in run) / len(run), 2)
        blocks.append(
            RemovableBlock(
                start_seconds=start,
                end_seconds=end,
                suggestion=suggestion,
                category=dominant,
                reason=_block_reason(dominant, span, len(run)),
                confidence=confidence,
            )
        )
    return tuple(blocks)


def _rhythm_note(
    reviewed: list[ReviewedSegment],
    blocks: tuple[RemovableBlock, ...],
    original: float,
    estimated: float,
) -> str:
    parts: list[str] = []
    if blocks:
        biggest = max(blocks, key=lambda block: block.seconds)
        parts.append(
            f"O maior arrasto vai de {_clock(biggest.start_seconds, millis=False)} "
            f"a {_clock(biggest.end_seconds, millis=False)} "
            f"({biggest.seconds:.0f} s, {biggest.category})."
        )
    else:
        parts.append("Nenhum bloco longo removível; o corte é de ajuste fino.")
    restatements = sum(1 for row in reviewed if row.category == "REPETITION")
    if restatements:
        parts.append(f"{restatements} trecho(s) reafirmam algo já dito.")
    asides = sum(1 for row in reviewed if row.category == "SELF_COMMENTARY")
    if asides:
        parts.append(
            f"{asides} aparte(s) do apresentador sobre a própria fala enfraquecem o ritmo."
        )
    pauses = sum(1 for row in reviewed if row.category == "LONG_PAUSE")
    if pauses:
        parts.append(f"{pauses} pausa(s) longa(s) para remover.")
    parts.append(
        f"Aplicando as sugestões, o vídeo cai de {_clock(original, millis=False)} "
        f"para cerca de {_clock(estimated, millis=False)}."
    )
    return " ".join(parts)


def _build_summary(
    reviewed: list[ReviewedSegment],
    blocks: tuple[RemovableBlock, ...],
    duration_seconds: float,
) -> EditSummary:
    cut_spans = [
        (row.start_seconds, row.end_seconds)
        for row in reviewed
        if row.suggestion == "CUT"
    ]
    review_spans = [
        (row.start_seconds, row.end_seconds)
        for row in reviewed
        if row.suggestion == "REVIEW"
    ]
    removed = _union_seconds(cut_spans) + 0.5 * _union_seconds(review_spans)
    estimated = max(0.0, min(duration_seconds, duration_seconds - removed))
    top = tuple(
        sorted(
            blocks,
            key=lambda block: block.seconds * max(block.confidence, 0.1),
            reverse=True,
        )[:5]
    )
    return EditSummary(
        original_duration_seconds=duration_seconds,
        estimated_duration_seconds=estimated,
        top_removable=top,
        rhythm_note=_rhythm_note(reviewed, blocks, duration_seconds, estimated),
    )


def analyze_take(
    segments: object,
    silences: object = (),
    *,
    source_path: str,
    language: str = "pt",
    duration_seconds: float | None = None,
    long_silence_seconds: float = LONG_SILENCE_SECONDS,
) -> CutReview:
    """Review a transcribed take as a content editor and suggest, never make, cuts.

    ``segments`` are ordered, non-overlapping :class:`TranscriptSegment` values
    (touching is fine). ``silences`` are :class:`SilenceSpan` values measured in
    the same audio; each at least ``long_silence_seconds`` long and wholly inside
    the spoken span becomes its own ``LONG_PAUSE`` row. Every phrase is compared
    with its neighbours, not judged alone: restatements, digressions, filler
    runs, the presenter's asides about their own delivery, redundant examples,
    failed takes and long intro/outro all move a row up the
    ``KEEP -> REVIEW -> CUT`` ladder. Adjacent flagged rows are grouped into
    ``blocks`` and an :class:`EditSummary` reports the numbers.

    ``provenance`` is ``"heuristic"``. Raises :class:`TakesError` on bad input.
    """

    segment_tuple = tuple(segments) if not isinstance(segments, tuple) else segments
    if not segment_tuple:
        raise TakesError("a take needs at least one transcript segment")
    previous_end = 0.0
    for segment in segment_tuple:
        if not isinstance(segment, TranscriptSegment):
            raise TakesError("every segment must be a TranscriptSegment")
        if segment.start_seconds + 1e-6 < previous_end:
            raise TakesError("transcript segments must be ordered and non-overlapping")
        previous_end = segment.end_seconds

    silence_tuple = tuple(silences) if not isinstance(silences, tuple) else silences
    for silence in silence_tuple:
        if not isinstance(silence, SilenceSpan):
            raise TakesError("every silence must be a SilenceSpan")

    _finite_nonneg(long_silence_seconds, "long_silence_seconds")
    if long_silence_seconds <= 0:
        raise TakesError("long_silence_seconds must be positive")
    if not isinstance(language, str) or not language.strip():
        raise TakesError("language must be a non-empty string")
    if not isinstance(source_path, str) or not source_path.strip():
        raise TakesError("source_path must be a non-empty string")

    if duration_seconds is None:
        duration_seconds = segment_tuple[-1].end_seconds
    _finite_nonneg(duration_seconds, "duration_seconds")
    if duration_seconds + 1e-6 < segment_tuple[-1].end_seconds:
        raise TakesError("duration_seconds is shorter than the transcript")

    verdicts: dict[int, tuple[str, str, str, float]] = {
        index: ("KEEP", "NONE", "", 0.0) for index in range(len(segment_tuple))
    }
    for detector in _DETECTORS:
        for index, suggestion, category, reason, confidence in detector(segment_tuple):
            current = verdicts[index]
            if _RANK[suggestion] > _RANK[current[0]]:
                verdicts[index] = (suggestion, category, reason, float(confidence))
            elif _RANK[suggestion] == _RANK[current[0]] and suggestion != "KEEP":
                merged_reason = (
                    current[2]
                    if reason in current[2]
                    else f"{current[2]}; {reason}"
                )
                verdicts[index] = (
                    suggestion,
                    current[1],  # first category wins, deterministically
                    merged_reason,
                    max(current[3], float(confidence)),
                )

    reviewed = [
        ReviewedSegment(
            start_seconds=segment.start_seconds,
            end_seconds=segment.end_seconds,
            text=segment.text.strip(),
            suggestion=verdicts[index][0],
            category=verdicts[index][1],
            reason=verdicts[index][2],
            confidence=verdicts[index][3],
        )
        for index, segment in enumerate(segment_tuple)
    ]
    reviewed.extend(_silence_rows(segment_tuple, silence_tuple, long_silence_seconds))
    reviewed.sort(key=lambda row: (row.start_seconds, row.end_seconds))

    blocks = _build_blocks(reviewed)
    summary = _build_summary(reviewed, blocks, float(duration_seconds))

    return CutReview(
        source_path=source_path,
        language=language,
        provenance="heuristic",
        duration_seconds=float(duration_seconds),
        segments=tuple(reviewed),
        blocks=blocks,
        summary=summary,
    )


def render_review_markdown(review: CutReview) -> str:
    """Render a :class:`CutReview` as a Markdown sheet to read beside the video:
    the edit summary, the removable blocks, then one block per row."""

    if not isinstance(review, CutReview):
        raise TakesError("render_review_markdown needs a CutReview")
    tally = review.counts()
    summary = review.summary
    lines = [
        f"# Revisão de cortes — {_clock(review.duration_seconds, millis=False)}",
        "",
        f"Fonte: `{review.source_path}` · idioma: {review.language} · "
        f"origem da análise: {review.provenance}",
        "",
        (
            f"{len(review.segments)} trechos · {tally['CUT']} CUT · "
            f"{tally['REVIEW']} REVIEW · {tally['KEEP']} KEEP"
        ),
        "",
        (
            "> `CUT` e `REVIEW` são sugestões. A decisão final é sua: manter, "
            "cortar, ou ajustar início/fim do corte."
        ),
        "",
        "## Resumo da edição",
        "",
        f"- Duração original: **{_clock(summary.original_duration_seconds, millis=False)}**",
        (
            f"- Duração estimada após cortes: "
            f"**{_clock(summary.estimated_duration_seconds, millis=False)}** "
            f"(REVIEW conta como meio corte)"
        ),
        f"- Ritmo: {summary.rhythm_note}",
        "",
    ]
    if summary.top_removable:
        lines.append("Principais trechos removíveis:")
        lines.append("")
        for block in summary.top_removable:
            lines.append(
                f"- {_clock(block.start_seconds, millis=True)} → "
                f"{_clock(block.end_seconds, millis=True)}  ·  {block.suggestion}  ·  "
                f"{block.category} — {block.reason}"
            )
        lines.append("")

    if review.blocks:
        lines.append("## Blocos removíveis")
        lines.append("")
        for block in review.blocks:
            lines.append(
                f"### {_clock(block.start_seconds, millis=True)} → "
                f"{_clock(block.end_seconds, millis=True)}  ·  {block.suggestion}  ·  "
                f"{block.category}"
            )
            lines.append("")
            lines.append(f"{block.reason} · confiança {block.confidence:.2f}")
            lines.append("")

    lines.append("## Trecho a trecho")
    lines.append("")
    for segment in review.segments:
        header = (
            f"### {_clock(segment.start_seconds, millis=True)} → "
            f"{_clock(segment.end_seconds, millis=True)}  ·  {segment.suggestion}"
        )
        if segment.category != "NONE":
            header += f"  ·  {segment.category}"
        lines.append(header)
        lines.append("")
        lines.append(f"> {segment.text}")
        lines.append("")
        if segment.suggestion != "KEEP":
            lines.append(f"Motivo: {segment.reason}")
            lines.append(f"Confiança: {segment.confidence:.2f}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"
