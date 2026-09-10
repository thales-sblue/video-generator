"""Flag likely cut points in a recorded take, without ever cutting.

Pure and stdlib-only: no I/O, no transcription, no FFmpeg. Something upstream
measures the take -- a local Whisper model for the words and their timestamps,
``silencedetect`` for the pauses -- and this module reads those measurements and
marks each stretch ``KEEP``, ``REVIEW`` or ``CUT`` with a human-readable reason
and a confidence in ``0..1``.

It never removes anything and it never rewrites the transcript. ``CUT`` is a
suggestion; the final decision is a person watching the video and choosing to
keep, cut, or nudge the in/out of a cut. Every heuristic here can only move a
segment *up* the ``KEEP -> REVIEW -> CUT`` ladder, never silence a signal.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass

SUGGESTIONS = ("KEEP", "REVIEW", "CUT")
_RANK = {"KEEP": 0, "REVIEW": 1, "CUT": 2}

# A pause longer than this, wholly inside the spoken span, earns its own row in
# the review instead of being folded into a neighbouring line.
LONG_SILENCE_SECONDS = 1.5

# Conservative pt-BR filler lexicon: only tokens that are almost always
# discourse filler. "é", "então", "aí", "assim" are deliberately left out --
# they are too often load-bearing words, and this layer must not cry wolf.
FILLER_WORDS = frozenset(
    {"né", "tipo", "hum", "hmm", "ã", "ãã", "ããã", "aham", "ahn", "mano", "sabe"}
)
FILLER_MIN_COUNT = 3
FILLER_MIN_RATIO = 0.18

# Sentence-initial markers that mean "scrap that, starting over".
RESTART_MARKERS = (
    "na verdade",
    "deixa eu",
    "deixem eu",
    "quer dizer",
    "ou melhor",
    "melhor dizendo",
    "vou refazer",
    "deixa eu refazer",
    "recomeçando",
    "de novo",
    "espera",
    "espere",
    "peraí",
    "pera",
    "calma",
)

# How far ahead to look for a cleaner retake of the same phrase.
REPEAT_WINDOW = 3
REPEAT_MIN_RATIO = 0.85
REPEAT_MIN_WORDS = 3

# A short fragment with no terminal punctuation whose words barely reappear in
# the next segment reads as a sentence the speaker dropped.
ABANDON_MAX_WORDS = 6
ABANDON_MAX_OVERLAP = 0.3
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
    """Fold ``text`` to lowercase, accent-free form for matching."""

    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return stripped.casefold().strip()


def _tokens(text: str) -> list[str]:
    return _WORD_RE.findall(_normalize(text))


_FILLER_NORM = frozenset(_normalize(word) for word in FILLER_WORDS)
_RESTART_NORM = tuple(_normalize(marker) for marker in RESTART_MARKERS)


def _clock(seconds: float, *, millis: bool) -> str:
    seconds = max(0.0, float(seconds))
    if millis:
        total_ms = int(round(seconds * 1000))
        minutes, remainder = divmod(total_ms // 1000, 60)
        return f"{minutes:02d}:{remainder:02d}.{total_ms % 1000:03d}"
    minutes, remainder = divmod(int(round(seconds)), 60)
    return f"{minutes:02d}:{remainder:02d}"


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
    reason: str
    confidence: float

    def __post_init__(self) -> None:
        _finite_nonneg(self.start_seconds, "reviewed start")
        _finite_nonneg(self.end_seconds, "reviewed end")
        if self.end_seconds <= self.start_seconds:
            raise TakesError("reviewed end must be after its start")
        if self.suggestion not in SUGGESTIONS:
            raise TakesError(f"suggestion must be one of {SUGGESTIONS}")
        if not isinstance(self.text, str) or not self.text.strip():
            raise TakesError("reviewed text must be a non-empty string")
        if not isinstance(self.reason, str):
            raise TakesError("reason must be a string")
        if self.suggestion == "KEEP" and self.reason:
            raise TakesError("a KEEP segment carries no reason")
        if self.suggestion != "KEEP" and not self.reason.strip():
            raise TakesError("a flagged segment needs a reason")
        _finite_nonneg(self.confidence, "confidence")
        if self.confidence > 1:
            raise TakesError("confidence must be within 0..1")

    def to_dict(self) -> dict[str, object]:
        return {
            "start": _clock(self.start_seconds, millis=True),
            "end": _clock(self.end_seconds, millis=True),
            "text": self.text,
            "suggestion": self.suggestion,
            "reason": self.reason,
            "confidence": round(float(self.confidence), 2),
        }


@dataclass(frozen=True, slots=True)
class CutReview:
    """The reviewable output: every segment, in time order, with a suggestion."""

    source_path: str
    language: str
    duration_seconds: float
    segments: tuple[ReviewedSegment, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.source_path, str) or not self.source_path.strip():
            raise TakesError("source_path must be a non-empty string")
        if not isinstance(self.language, str) or not self.language.strip():
            raise TakesError("language must be a non-empty string")
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

    def counts(self) -> dict[str, int]:
        tally = {name: 0 for name in SUGGESTIONS}
        for segment in self.segments:
            tally[segment.suggestion] += 1
        return tally

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "source_path": self.source_path,
            "language": self.language,
            "duration": _clock(self.duration_seconds, millis=False),
            "segments": [segment.to_dict() for segment in self.segments],
        }


_Proposal = tuple[int, str, str, float]


def _detect_repeats(segments: tuple[TranscriptSegment, ...]) -> list[_Proposal]:
    tokens = [_tokens(segment.text) for segment in segments]
    out: list[_Proposal] = []
    for i in range(len(segments)):
        if len(tokens[i]) < REPEAT_MIN_WORDS:
            continue
        for j in range(i + 1, min(i + 1 + REPEAT_WINDOW, len(segments))):
            if len(tokens[j]) < REPEAT_MIN_WORDS:
                continue
            ratio = difflib.SequenceMatcher(
                a=tokens[i], b=tokens[j], autojunk=False
            ).ratio()
            if ratio >= REPEAT_MIN_RATIO:
                out.append(
                    (
                        i,
                        "CUT",
                        "repetida logo em seguida; a tentativa à frente é a boa",
                        round(ratio, 2),
                    )
                )
                break
    return out


def _detect_restarts(segments: tuple[TranscriptSegment, ...]) -> list[_Proposal]:
    out: list[_Proposal] = []
    for i, segment in enumerate(segments):
        norm = _normalize(segment.text)
        if any(norm.startswith(marker) for marker in _RESTART_NORM):
            out.append((i, "REVIEW", "recomeço de explicação", 0.6))
            if i > 0:
                out.append(
                    (
                        i - 1,
                        "CUT",
                        "recomeço logo em seguida; provável tentativa abandonada",
                        0.6,
                    )
                )
    return out


def _detect_fillers(segments: tuple[TranscriptSegment, ...]) -> list[_Proposal]:
    out: list[_Proposal] = []
    for i, segment in enumerate(segments):
        tokens = _tokens(segment.text)
        if not tokens:
            continue
        hits = [token for token in tokens if token in _FILLER_NORM]
        if (
            len(hits) >= FILLER_MIN_COUNT
            and len(hits) / len(tokens) >= FILLER_MIN_RATIO
        ):
            unique = ", ".join(sorted(set(hits)))
            out.append(
                (
                    i,
                    "REVIEW",
                    f"vícios de linguagem em excesso ({len(hits)}: {unique})",
                    round(min(0.4 + 0.1 * len(hits), 0.9), 2),
                )
            )
    return out


def _detect_abandoned(segments: tuple[TranscriptSegment, ...]) -> list[_Proposal]:
    out: list[_Proposal] = []
    for i, segment in enumerate(segments):
        if i == len(segments) - 1:
            continue
        if segment.text.rstrip()[-1:] in _TERMINAL_PUNCT:
            continue
        tokens = set(_tokens(segment.text))
        if not tokens or len(tokens) > ABANDON_MAX_WORDS:
            continue
        following = set(_tokens(segments[i + 1].text))
        if not following:
            continue
        overlap = len(tokens & following) / len(tokens)
        if overlap <= ABANDON_MAX_OVERLAP:
            out.append((i, "REVIEW", "frase iniciada e abandonada", 0.5))
    return out


_DETECTORS = (_detect_repeats, _detect_restarts, _detect_fillers, _detect_abandoned)


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
        # head and tail quiet is the take not having started or already
        # finished, not a pause worth flagging as a cut.
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
                reason=f"pausa longa de {seconds:.1f} s",
                confidence=round(confidence, 2),
            )
        )
    return rows


def analyze_take(
    segments: object,
    silences: object = (),
    *,
    source_path: str,
    language: str = "pt",
    duration_seconds: float | None = None,
    long_silence_seconds: float = LONG_SILENCE_SECONDS,
) -> CutReview:
    """Review a transcribed take and suggest, but never make, cuts.

    ``segments`` are ordered, non-overlapping :class:`TranscriptSegment` values
    (touching is fine). ``silences`` are :class:`SilenceSpan` values measured in
    the same audio; each one at least ``long_silence_seconds`` long and wholly
    inside the spoken span becomes its own ``CUT`` row. ``duration_seconds``
    defaults to the end of the last segment and must not be shorter than it.

    Returns a :class:`CutReview` whose segments are in time order. Raises
    :class:`TakesError` on malformed input.
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

    verdicts: dict[int, tuple[str, str, float]] = {
        index: ("KEEP", "", 0.0) for index in range(len(segment_tuple))
    }
    for detector in _DETECTORS:
        for index, suggestion, reason, confidence in detector(segment_tuple):
            current_suggestion, current_reason, current_confidence = verdicts[index]
            if _RANK[suggestion] > _RANK[current_suggestion]:
                verdicts[index] = (suggestion, reason, float(confidence))
            elif (
                _RANK[suggestion] == _RANK[current_suggestion]
                and suggestion != "KEEP"
            ):
                merged = (
                    current_reason
                    if reason in current_reason
                    else f"{current_reason}; {reason}"
                )
                verdicts[index] = (
                    suggestion,
                    merged,
                    max(current_confidence, float(confidence)),
                )

    reviewed = [
        ReviewedSegment(
            start_seconds=segment.start_seconds,
            end_seconds=segment.end_seconds,
            text=segment.text.strip(),
            suggestion=verdicts[index][0],
            reason=verdicts[index][1],
            confidence=verdicts[index][2],
        )
        for index, segment in enumerate(segment_tuple)
    ]
    reviewed.extend(_silence_rows(segment_tuple, silence_tuple, long_silence_seconds))
    reviewed.sort(key=lambda row: (row.start_seconds, row.end_seconds))

    return CutReview(
        source_path=source_path,
        language=language,
        duration_seconds=float(duration_seconds),
        segments=tuple(reviewed),
    )


def render_review_markdown(review: CutReview) -> str:
    """Render a :class:`CutReview` as a Markdown sheet meant to be read beside
    the video: one block per segment, flagged blocks carrying their reason."""

    if not isinstance(review, CutReview):
        raise TakesError("render_review_markdown needs a CutReview")
    tally = review.counts()
    lines = [
        f"# Revisão de cortes — {_clock(review.duration_seconds, millis=False)}",
        "",
        f"Fonte: `{review.source_path}` · idioma: {review.language}",
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
        "---",
        "",
    ]
    for segment in review.segments:
        lines.append(
            f"### {_clock(segment.start_seconds, millis=True)} → "
            f"{_clock(segment.end_seconds, millis=True)}  ·  {segment.suggestion}"
        )
        lines.append("")
        lines.append(f"> {segment.text}")
        lines.append("")
        if segment.suggestion != "KEEP":
            lines.append(f"Motivo: {segment.reason}")
            lines.append(f"Confiança: {segment.confidence:.2f}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"
