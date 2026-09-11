"""Turn a list of approved cut intervals into the ranges to keep.

Pure and stdlib-only: no I/O, no FFmpeg. The transcription/analysis stage
(:mod:`video_generator.domain.takes`) decides *what* to cut and a person
approves it; this module only does the interval arithmetic that turns "remove
these spans" into "concatenate those spans", conservatively.

Conservative means: a configurable safety margin *shrinks* every cut inward so a
dry cut never clips a word, and a cut that the margin would empty is dropped
rather than guessed at. Nothing here removes silence on its own -- a pause is
cut only because it was on the approved list.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

# A kept sliver shorter than this (a handful of frames at most) between two cuts
# is treated as noise and absorbed: emitting it would concatenate a sub-frame
# chunk. Everything else is preserved exactly.
MIN_KEEP_SECONDS = 0.04
# After the margin is applied, a cut left shorter than this is dropped: the
# approved span was so tight that trimming it safely removes nothing.
MIN_CUT_SECONDS = 0.02
DEFAULT_PAD_MS = 50

# An "aside" is a kept span the presenter wants to *signal* as a digression
# instead of removing: it stays in the timeline, mildly sped up, in black and
# white, with a small on-screen marker. Unlike a cut, it gets no safety margin
# -- its boundaries are an editorial choice, not a speech-safety guess -- and
# two asides must not overlap each other or an approved cut.
DEFAULT_ASIDE_SPEED = 1.17
DEFAULT_ASIDE_LABEL = "desvio rápido"
MAX_ASIDE_LABEL_CHARS = 40

_CLOCK_RE = re.compile(r"^(?:(?P<h>\d+):)?(?P<m>\d{1,2}):(?P<s>\d{1,2})(?:\.(?P<ms>\d{1,3}))?$")


class CutsError(ValueError):
    """Raised when an approved-cuts list cannot be turned into a keep list."""


def _finite_nonneg(value: object, name: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
    ):
        raise CutsError(f"{name} must be a finite, non-negative number")
    return float(value)


def parse_clock(value: object) -> float:
    """Seconds from ``MM:SS``, ``MM:SS.mmm``, ``HH:MM:SS.mmm`` or a number."""

    if isinstance(value, bool):
        raise CutsError("a timestamp cannot be a boolean")
    if isinstance(value, (int, float)):
        return _finite_nonneg(value, "timestamp")
    if not isinstance(value, str) or not value.strip():
        raise CutsError(f"invalid timestamp: {value!r}")
    match = _CLOCK_RE.match(value.strip())
    if match is None:
        raise CutsError(f"invalid timestamp: {value!r}")
    hours = int(match.group("h") or 0)
    minutes = int(match.group("m"))
    seconds = int(match.group("s"))
    millis = int((match.group("ms") or "0").ljust(3, "0")[:3])
    if minutes > 59 or seconds > 59:
        raise CutsError(f"minutes and seconds must be below 60: {value!r}")
    return hours * 3600 + minutes * 60 + seconds + millis / 1000


def _clock(seconds: float) -> str:
    total_ms = int(round(max(0.0, seconds) * 1000))
    minutes, remainder = divmod(total_ms // 1000, 60)
    return f"{minutes:02d}:{remainder:02d}.{total_ms % 1000:03d}"


@dataclass(frozen=True, slots=True)
class Interval:
    """A closed time span, in seconds, with ``start < end``."""

    start_seconds: float
    end_seconds: float

    def __post_init__(self) -> None:
        _finite_nonneg(self.start_seconds, "interval start")
        _finite_nonneg(self.end_seconds, "interval end")
        if self.end_seconds <= self.start_seconds:
            raise CutsError("interval end must be after its start")

    @property
    def duration_seconds(self) -> float:
        return self.end_seconds - self.start_seconds

    def to_dict(self) -> dict[str, object]:
        return {
            "start": _clock(self.start_seconds),
            "end": _clock(self.end_seconds),
            "seconds": round(self.duration_seconds, 3),
        }


@dataclass(frozen=True, slots=True)
class Aside:
    """A kept span presented as a deliberate, marked digression.

    Not a cut: nothing here is removed. ``speed`` re-times the span (>1.0
    speeds it up); ``label`` is a short marker burned on screen for its
    duration. Boundaries are exact -- no safety margin is applied, since they
    are an editorial choice about *where the digression is*, not a guess about
    where a word might get clipped.
    """

    start_seconds: float
    end_seconds: float
    speed: float = DEFAULT_ASIDE_SPEED
    label: str | None = DEFAULT_ASIDE_LABEL

    def __post_init__(self) -> None:
        _finite_nonneg(self.start_seconds, "aside start")
        _finite_nonneg(self.end_seconds, "aside end")
        if self.end_seconds <= self.start_seconds:
            raise CutsError("aside end must be after its start")
        if (
            isinstance(self.speed, bool)
            or not isinstance(self.speed, (int, float))
            or not math.isfinite(self.speed)
            or self.speed <= 0
        ):
            raise CutsError("aside speed must be a finite positive number")
        if self.label is not None:
            if not isinstance(self.label, str) or not self.label.strip():
                raise CutsError("aside label must be a non-empty string or None")
            if "\n" in self.label or "\r" in self.label:
                raise CutsError("aside label must be a single line")
            if len(self.label) > MAX_ASIDE_LABEL_CHARS:
                raise CutsError(
                    f"aside label must be at most {MAX_ASIDE_LABEL_CHARS} characters"
                )

    @property
    def duration_seconds(self) -> float:
        return self.end_seconds - self.start_seconds

    def to_dict(self) -> dict[str, object]:
        return {
            "start": _clock(self.start_seconds),
            "end": _clock(self.end_seconds),
            "seconds": round(self.duration_seconds, 3),
            "speed": round(float(self.speed), 3),
            "label": self.label,
        }


TIMELINE_KINDS = ("keep", "aside")


@dataclass(frozen=True, slots=True)
class TimelineSegment:
    """One ordered piece of the rendered timeline: a plain keep, or an aside."""

    start_seconds: float
    end_seconds: float
    kind: str
    speed: float = 1.0
    label: str | None = None

    def __post_init__(self) -> None:
        _finite_nonneg(self.start_seconds, "segment start")
        _finite_nonneg(self.end_seconds, "segment end")
        if self.end_seconds <= self.start_seconds:
            raise CutsError("segment end must be after its start")
        if self.kind not in TIMELINE_KINDS:
            raise CutsError(f"segment kind must be one of {TIMELINE_KINDS}")
        if self.kind == "keep" and (self.speed != 1.0 or self.label is not None):
            raise CutsError("a plain keep segment carries no speed change or label")

    @property
    def duration_seconds(self) -> float:
        return self.end_seconds - self.start_seconds

    def to_dict(self) -> dict[str, object]:
        return {
            "start": _clock(self.start_seconds),
            "end": _clock(self.end_seconds),
            "seconds": round(self.duration_seconds, 3),
            "kind": self.kind,
            "speed": round(float(self.speed), 3),
            "label": self.label,
        }


def _raw_aside(entry: object) -> Aside:
    if isinstance(entry, Aside):
        return entry
    if not isinstance(entry, dict) or "start" not in entry or "end" not in entry:
        raise CutsError("each aside needs a 'start' and an 'end'")
    kwargs: dict[str, object] = {
        "start_seconds": parse_clock(entry["start"]),
        "end_seconds": parse_clock(entry["end"]),
    }
    if "speed" in entry:
        kwargs["speed"] = entry["speed"]
    if "label" in entry:
        kwargs["label"] = entry["label"]
    return Aside(**kwargs)


def consolidate_asides(
    raw_asides: object,
    *,
    duration_seconds: float,
    default_speed: float = DEFAULT_ASIDE_SPEED,
    default_label: str | None = DEFAULT_ASIDE_LABEL,
) -> tuple[Aside, ...]:
    """Normalise an asides list into ordered, non-overlapping :class:`Aside` values.

    Every entry is parsed (``speed``/``label`` default when absent), clamped to
    ``[0, duration_seconds]``, and sorted. Unlike cuts, asides are never
    merged: two that genuinely overlap raise :class:`CutsError` so the
    conflict is visible rather than silently resolved -- each one carries its
    own label and speed, and guessing which wins would hide the mistake. Two
    asides that only touch (one ends exactly where the next starts) are fine
    and stay separate, each keeping its own treatment.
    """

    duration = _finite_nonneg(duration_seconds, "duration_seconds")
    if duration == 0:
        raise CutsError("duration_seconds must be greater than zero")

    if raw_asides is None:
        entries: list[object] = []
    elif isinstance(raw_asides, (str, bytes)):
        raise CutsError("raw_asides must be a list of asides, not a string")
    else:
        try:
            entries = list(raw_asides)
        except TypeError as exc:
            raise CutsError("raw_asides must be iterable") from exc

    asides: list[Aside] = []
    for entry in entries:
        if isinstance(entry, dict):
            entry = dict(entry)
            entry.setdefault("speed", default_speed)
            entry.setdefault("label", default_label)
        aside = _raw_aside(entry)
        start = min(max(aside.start_seconds, 0.0), duration)
        end = min(max(aside.end_seconds, 0.0), duration)
        if end - start < MIN_CUT_SECONDS:
            raise CutsError("an aside must not fall entirely outside the video")
        asides.append(Aside(start, end, aside.speed, aside.label))

    asides.sort(key=lambda aside: aside.start_seconds)
    for previous, current in zip(asides, asides[1:]):
        if current.start_seconds < previous.end_seconds - 1e-9:
            raise CutsError("asides must not overlap one another")
    return tuple(asides)


def build_timeline(
    keeps: "tuple[Interval, ...]", asides: "tuple[Aside, ...]"
) -> tuple[TimelineSegment, ...]:
    """Slice ``keeps`` at every aside boundary, tagging each resulting piece.

    ``keeps`` is the complement of the approved cuts (:func:`keep_intervals`).
    Every aside must lie entirely within one keep interval; one that overlaps
    an approved cut, or straddles a cut boundary, raises :class:`CutsError`
    instead of being silently trimmed or dropped -- that conflict is the
    person's to resolve, not the tool's to guess at.
    """

    if not isinstance(keeps, tuple) or not all(isinstance(k, Interval) for k in keeps):
        raise CutsError("keeps must be a tuple of Interval values")
    if not isinstance(asides, tuple) or not all(isinstance(a, Aside) for a in asides):
        raise CutsError("asides must be a tuple of Aside values")

    segments: list[TimelineSegment] = []
    matched = 0
    for keep in keeps:
        local = [
            aside
            for aside in asides
            if aside.start_seconds >= keep.start_seconds - 1e-9
            and aside.end_seconds <= keep.end_seconds + 1e-9
        ]
        cursor = keep.start_seconds
        for aside in local:
            if aside.start_seconds > cursor + 1e-9:
                segments.append(TimelineSegment(cursor, aside.start_seconds, "keep"))
            segments.append(
                TimelineSegment(
                    aside.start_seconds, aside.end_seconds, "aside", aside.speed, aside.label
                )
            )
            cursor = aside.end_seconds
            matched += 1
        if keep.end_seconds > cursor + 1e-9:
            segments.append(TimelineSegment(cursor, keep.end_seconds, "keep"))

    if matched != len(asides):
        raise CutsError(
            "an aside overlaps an approved cut or crosses a cut boundary"
        )
    return tuple(segments)


@dataclass(frozen=True, slots=True)
class CutOutcome:
    """The headline numbers for an applied-cuts pass."""

    original_seconds: float
    removed_seconds: float
    final_seconds: float
    cuts_applied: int

    def to_dict(self) -> dict[str, object]:
        return {
            "original_duration": _clock(self.original_seconds),
            "removed_duration": _clock(self.removed_seconds),
            "final_duration": _clock(self.final_seconds),
            "cuts_applied": self.cuts_applied,
        }


def _raw_pair(entry: object) -> tuple[float, float]:
    if isinstance(entry, Interval):
        return entry.start_seconds, entry.end_seconds
    if isinstance(entry, dict):
        if "start" not in entry or "end" not in entry:
            raise CutsError("each cut needs a 'start' and an 'end'")
        return parse_clock(entry["start"]), parse_clock(entry["end"])
    if isinstance(entry, (list, tuple)) and len(entry) == 2:
        return parse_clock(entry[0]), parse_clock(entry[1])
    raise CutsError(f"unsupported cut entry: {entry!r}")


def consolidate_cuts(
    raw_cuts: object,
    *,
    duration_seconds: float,
    pad_before_seconds: float = DEFAULT_PAD_MS / 1000,
    pad_after_seconds: float = DEFAULT_PAD_MS / 1000,
) -> tuple[Interval, ...]:
    """Normalise an approved-cuts list into ordered, disjoint intervals.

    Every cut is parsed, the safety margin is applied *inward*
    (``start += pad_before``, ``end -= pad_after``), the result is clamped to
    ``[0, duration_seconds]``, and cuts left shorter than
    :data:`MIN_CUT_SECONDS` are dropped. What survives is sorted and any
    overlapping or touching spans are merged. Raises :class:`CutsError` on a
    malformed entry or a non-positive duration; an empty input yields ``()``.
    """

    duration = _finite_nonneg(duration_seconds, "duration_seconds")
    if duration == 0:
        raise CutsError("duration_seconds must be greater than zero")
    before = _finite_nonneg(pad_before_seconds, "pad_before_seconds")
    after = _finite_nonneg(pad_after_seconds, "pad_after_seconds")

    if raw_cuts is None:
        entries: list[object] = []
    elif isinstance(raw_cuts, (str, bytes)):
        raise CutsError("raw_cuts must be a list of intervals, not a string")
    else:
        try:
            entries = list(raw_cuts)
        except TypeError as exc:
            raise CutsError("raw_cuts must be iterable") from exc

    raw: list[tuple[float, float]] = []
    for entry in entries:
        start, end = _raw_pair(entry)
        if end <= start:
            raise CutsError(f"cut end must be after its start: {entry!r}")
        start = min(max(start, 0.0), duration)
        end = min(max(end, 0.0), duration)
        if end - start <= 0:
            # entirely past the video (or before it): nothing to remove
            continue
        raw.append((start, end))

    # Merge overlapping and touching cuts *before* the margin is applied, so two
    # approved cuts split only by a transcription boundary (end == next start)
    # become one cut instead of leaving a padding-width sliver between them.
    raw.sort()
    merged_raw: list[list[float]] = []
    for start, end in raw:
        if merged_raw and start <= merged_raw[-1][1] + 1e-6:
            merged_raw[-1][1] = max(merged_raw[-1][1], end)
        else:
            merged_raw.append([start, end])

    padded: list[Interval] = []
    for start, end in merged_raw:
        trimmed_start = min(max(start + before, 0.0), duration)
        trimmed_end = min(max(end - after, 0.0), duration)
        if trimmed_end - trimmed_start < MIN_CUT_SECONDS:
            continue
        padded.append(Interval(trimmed_start, trimmed_end))
    return tuple(padded)


def keep_intervals(
    cuts: object, duration_seconds: float
) -> tuple[Interval, ...]:
    """The ordered spans of ``[0, duration_seconds]`` that ``cuts`` leave behind.

    ``cuts`` must already be consolidated (sorted, disjoint, within the
    duration) -- pass the output of :func:`consolidate_cuts`. A kept span
    shorter than :data:`MIN_KEEP_SECONDS` between two cuts is absorbed rather
    than emitted. With no cuts the whole video is one kept span.
    """

    duration = _finite_nonneg(duration_seconds, "duration_seconds")
    if duration == 0:
        raise CutsError("duration_seconds must be greater than zero")
    ordered = tuple(cuts)
    previous_end = 0.0
    for interval in ordered:
        if not isinstance(interval, Interval):
            raise CutsError("cuts must be Interval values")
        if interval.start_seconds + 1e-9 < previous_end:
            raise CutsError("cuts must be consolidated (sorted and disjoint)")
        if interval.end_seconds > duration + 1e-9:
            raise CutsError("a cut runs past the end of the video")
        previous_end = interval.end_seconds

    keeps: list[Interval] = []
    cursor = 0.0
    for interval in ordered:
        if interval.start_seconds - cursor >= MIN_KEEP_SECONDS:
            keeps.append(Interval(cursor, interval.start_seconds))
        cursor = max(cursor, interval.end_seconds)
    if duration - cursor >= MIN_KEEP_SECONDS:
        keeps.append(Interval(cursor, duration))
    return tuple(keeps)


def plan_cuts(
    raw_cuts: object,
    *,
    duration_seconds: float,
    pad_before_seconds: float = DEFAULT_PAD_MS / 1000,
    pad_after_seconds: float = DEFAULT_PAD_MS / 1000,
) -> tuple[tuple[Interval, ...], tuple[Interval, ...], CutOutcome]:
    """Consolidate ``raw_cuts``, compute the keep list, and tally the outcome.

    Returns ``(consolidated_cuts, keep_intervals, outcome)``. The removed
    duration is the sum of the kept-out spans (``original - final``), so it
    already accounts for merged and clamped cuts.
    """

    duration = _finite_nonneg(duration_seconds, "duration_seconds")
    consolidated = consolidate_cuts(
        raw_cuts,
        duration_seconds=duration,
        pad_before_seconds=pad_before_seconds,
        pad_after_seconds=pad_after_seconds,
    )
    keeps = keep_intervals(consolidated, duration)
    final_seconds = sum(interval.duration_seconds for interval in keeps)
    outcome = CutOutcome(
        original_seconds=duration,
        removed_seconds=max(0.0, duration - final_seconds),
        final_seconds=final_seconds,
        cuts_applied=len(consolidated),
    )
    return consolidated, keeps, outcome
