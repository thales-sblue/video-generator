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
