"""Plan a light editorial-visual layer on top of an already-cut recording.

This is the "identidade editorial" layer requested for the developer-video
channel: sparse, hand-made-looking interventions (a keyword in big type, a
short diagram, a "conflict" callout, a punch-in zoom, a freeze frame) laid
over a real screen recording that :mod:`video_generator.domain.cuts` has
already trimmed. It never invents editorial judgement on its own for the
things that need a human's sense of humour or narrative shape (a diagram, a
conflict beat, a freeze-worthy joke, a zoom on a punchline) -- those come from
an explicit ``manual`` list a person writes after watching the cut. The one
thing it *does* decide on its own is spotting a small fixed vocabulary of
keywords in the transcript, because that is mechanical, not editorial.

Pure stdlib, no I/O, no FFmpeg/Remotion -- consistent with the rest of
``domain/``. Timestamps in and out are in the *cut preview's* timeline (the
video ``apply-cuts`` already produced), not the original recording's.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from video_generator.domain.cuts import TimelineSegment, parse_clock
from video_generator.domain.typography import MotionTextEvent, TextBlock

VISUAL_KINDS = ("zoom", "keyword", "diagram", "conflict", "freeze")

# The vocabulary from the channel's own brief: short, concrete, screen-visible
# nouns a beginner needs anchored on screen. Matched case-insensitively as
# whole words against the transcript text already produced by review-cuts.
DEFAULT_KEYWORDS: tuple[str, ...] = (
    "local",
    "servidor",
    "clone",
    "push",
    "pull",
    "commit",
    "branch",
    "merge",
    "conflito",
    "produção",
    "repositório",
    "qa",
)

DEFAULT_ZOOM_SCALE = 1.08
DEFAULT_ZOOM_SECONDS = 1.6
DEFAULT_KEYWORD_SECONDS = 1.4
DEFAULT_FREEZE_SECONDS = 1.0
_MAX_LABEL_CHARS = 60


class VisualPlanError(ValueError):
    """Raised when a visual intervention or plan cannot be built."""


def _finite_positive(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise VisualPlanError(f"{name} must be a finite positive number")
    return float(value)


def _clean_text(value: object, name: str, *, max_chars: int = _MAX_LABEL_CHARS) -> str:
    if not isinstance(value, str) or not value.strip():
        raise VisualPlanError(f"{name} must be a non-empty string")
    text = value.strip()
    if "\n" in text or "\r" in text:
        raise VisualPlanError(f"{name} must be a single line")
    if len(text) > max_chars:
        raise VisualPlanError(f"{name} must be at most {max_chars} characters")
    return text


@dataclass(frozen=True, slots=True)
class VisualEvent:
    """One planned visual intervention, in the cut preview's own timeline."""

    kind: str
    start_seconds: float
    end_seconds: float
    text: tuple[str, ...] = ()
    scale: float | None = None
    freeze_seconds: float | None = None
    label: str | None = None
    source: str = "manual"

    def __post_init__(self) -> None:
        if self.kind not in VISUAL_KINDS:
            raise VisualPlanError(f"kind must be one of {VISUAL_KINDS}, got {self.kind!r}")
        if (
            isinstance(self.start_seconds, bool)
            or not isinstance(self.start_seconds, (int, float))
            or not math.isfinite(self.start_seconds)
            or self.start_seconds < 0
        ):
            raise VisualPlanError("start_seconds must be a finite, non-negative number")
        object.__setattr__(self, "start_seconds", float(self.start_seconds))
        end = _finite_positive(self.end_seconds, "end_seconds")
        if end <= self.start_seconds:
            raise VisualPlanError("end_seconds must be after start_seconds")
        if self.kind in ("keyword", "diagram", "conflict"):
            if not self.text or not all(isinstance(t, str) and t.strip() for t in self.text):
                raise VisualPlanError(f"{self.kind} needs a non-empty text/lines list")
        if self.kind == "zoom":
            scale = self.scale if self.scale is not None else DEFAULT_ZOOM_SCALE
            if not isinstance(scale, (int, float)) or isinstance(scale, bool) or not (1.0 < scale <= 1.6):
                raise VisualPlanError("zoom scale must be a number in (1.0, 1.6]")
            object.__setattr__(self, "scale", float(scale))
        if self.kind == "freeze":
            freeze = self.freeze_seconds if self.freeze_seconds is not None else DEFAULT_FREEZE_SECONDS
            _finite_positive(freeze, "freeze_seconds")
            if freeze > 5.0:
                raise VisualPlanError("freeze_seconds over 5s is almost certainly a mistake")
            object.__setattr__(self, "freeze_seconds", float(freeze))
        if self.label is not None:
            object.__setattr__(self, "label", _clean_text(self.label, "label"))

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "kind": self.kind,
            "start_seconds": round(self.start_seconds, 3),
            "end_seconds": round(self.end_seconds, 3),
            "source": self.source,
        }
        if self.text:
            payload["text"] = list(self.text)
        if self.scale is not None:
            payload["scale"] = round(self.scale, 4)
        if self.freeze_seconds is not None:
            payload["freeze_seconds"] = round(self.freeze_seconds, 3)
        if self.label is not None:
            payload["label"] = self.label
        return payload


@dataclass(frozen=True, slots=True)
class VisualPlan:
    events: tuple[VisualEvent, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "event_count": len(self.events),
            "events": [event.to_dict() for event in self.events],
        }


def _word_pattern(word: str) -> "re.Pattern[str]":
    return re.compile(rf"(?<!\w){re.escape(word)}(?!\w)", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class ReviewedSegment:
    """The slice of ``cut-review.json`` this module actually needs."""

    start_seconds: float
    end_seconds: float
    text: str
    suggestion: str


def detect_keyword_events(
    segments: "tuple[ReviewedSegment, ...] | list[ReviewedSegment]",
    *,
    keywords: "tuple[str, ...]" = DEFAULT_KEYWORDS,
    duration_seconds: float = DEFAULT_KEYWORD_SECONDS,
) -> tuple[VisualEvent, ...]:
    """One KEYWORD event for the first mention of each keyword in a kept segment.

    Only ``KEEP`` segments are scanned -- text about to be cut should not seed
    an on-screen callout. Each keyword fires at most once, at its first
    mention, so the channel's short vocabulary reads as punctuation rather
    than a running gloss.
    """

    duration = _finite_positive(duration_seconds, "duration_seconds")
    patterns = [(word, _word_pattern(word)) for word in keywords]
    seen: set[str] = set()
    events: list[VisualEvent] = []
    for segment in segments:
        if segment.suggestion != "KEEP":
            continue
        for word, pattern in patterns:
            if word in seen:
                continue
            match = pattern.search(segment.text)
            if match is None:
                continue
            seen.add(word)
            span = max(segment.end_seconds - segment.start_seconds, 0.001)
            fraction = match.start() / max(len(segment.text), 1)
            start = segment.start_seconds + span * fraction
            end = min(start + duration, segment.end_seconds + duration)
            events.append(
                VisualEvent(
                    kind="keyword",
                    start_seconds=start,
                    end_seconds=end,
                    text=(word.upper(),),
                    source="auto",
                )
            )
    events.sort(key=lambda event: event.start_seconds)
    return tuple(events)


def _manual_event_from_dict(item: object) -> VisualEvent:
    if not isinstance(item, dict):
        raise VisualPlanError("each manual event must be an object")
    kind = item.get("kind")
    if kind not in VISUAL_KINDS:
        raise VisualPlanError(f"manual event kind must be one of {VISUAL_KINDS}, got {kind!r}")

    if "start" in item:
        start = parse_clock(item["start"])
    elif "start_seconds" in item:
        raw_start = item["start_seconds"]
        if isinstance(raw_start, bool) or not isinstance(raw_start, (int, float)) or not math.isfinite(raw_start) or raw_start < 0:
            raise VisualPlanError("start_seconds must be a finite, non-negative number")
        start = float(raw_start)
    else:
        raise VisualPlanError("manual event needs 'start' or 'start_seconds'")
    if kind == "freeze":
        freeze_seconds = item.get("freeze_seconds", DEFAULT_FREEZE_SECONDS)
        end = start + _finite_positive(freeze_seconds, "freeze_seconds")
    elif "end" in item:
        end = parse_clock(item["end"])
    elif "end_seconds" in item:
        end = _finite_positive(item["end_seconds"], "end")
    else:
        default_span = DEFAULT_ZOOM_SECONDS if kind == "zoom" else DEFAULT_KEYWORD_SECONDS
        end = start + default_span

    text: tuple[str, ...] = ()
    if kind in ("keyword", "diagram", "conflict"):
        raw = item.get("lines") if "lines" in item else item.get("text")
        if isinstance(raw, str):
            text = (raw,)
        elif isinstance(raw, (list, tuple)):
            text = tuple(str(line) for line in raw)
        else:
            raise VisualPlanError(f"{kind} manual event needs 'text' or 'lines'")

    return VisualEvent(
        kind=kind,
        start_seconds=start,
        end_seconds=end,
        text=text,
        scale=item.get("scale"),
        freeze_seconds=item.get("freeze_seconds") if kind == "freeze" else None,
        label=item.get("label"),
        source="manual",
    )


def load_manual_events(payload: object) -> tuple[VisualEvent, ...]:
    """Parse the small hand-written JSON list of diagram/conflict/freeze/zoom cues.

    ``payload`` is the already-parsed JSON value (a list of objects), each
    naming a ``kind`` in :data:`VISUAL_KINDS`, a ``start`` (clock string or
    ``start_seconds``) and, for ``keyword``/``diagram``/``conflict``, a
    ``text`` string or ``lines`` list. This is the only place a person's
    editorial judgement about a diagram, a conflict beat, a joke worth a
    freeze, or a punchline worth a zoom enters the plan.
    """

    if not isinstance(payload, (list, tuple)):
        raise VisualPlanError("manual events must be a JSON list")
    return tuple(_manual_event_from_dict(item) for item in payload)


def remap_to_timeline(
    original_seconds: float, timeline: "tuple[TimelineSegment, ...]"
) -> float | None:
    """Map a timestamp in the *source* recording to the *cut preview's* timeline.

    ``timeline`` is the ordered list :func:`video_generator.domain.cuts.build_timeline`
    produced for the same render -- each piece still carries its *original*
    start/end, in source time. Returns ``None`` when the timestamp falls
    inside a removed cut (it has no place in the preview).
    """

    cursor = 0.0
    for piece in timeline:
        if piece.start_seconds <= original_seconds <= piece.end_seconds:
            offset = (original_seconds - piece.start_seconds) / piece.speed
            return cursor + offset
        cursor += (piece.end_seconds - piece.start_seconds) / piece.speed
    return None


def build_visual_plan(
    segments: "tuple[ReviewedSegment, ...]",
    *,
    timeline: "tuple[TimelineSegment, ...] | None" = None,
    manual_events: "tuple[VisualEvent, ...]" = (),
    keywords: "tuple[str, ...]" = DEFAULT_KEYWORDS,
) -> VisualPlan:
    """Combine automatic keyword spotting with a person's manual cues.

    When ``timeline`` is given, automatic keyword timestamps (computed against
    ``segments``, which are in *source* time) are remapped into the cut
    preview's timeline; a keyword whose mention was cut is dropped rather than
    guessed at. ``manual_events`` are assumed to already be authored against
    the cut preview (a person watches that video to place them) and pass
    through unchanged.
    """

    auto = detect_keyword_events(segments, keywords=keywords)
    if timeline is not None:
        remapped: list[VisualEvent] = []
        for event in auto:
            new_start = remap_to_timeline(event.start_seconds, timeline)
            if new_start is None:
                continue
            new_end = remap_to_timeline(min(event.end_seconds, timeline[-1].end_seconds), timeline)
            if new_end is None or new_end <= new_start:
                new_end = new_start + (event.end_seconds - event.start_seconds)
            remapped.append(
                VisualEvent(
                    kind=event.kind,
                    start_seconds=new_start,
                    end_seconds=new_end,
                    text=event.text,
                    source="auto",
                )
            )
        auto = tuple(remapped)

    all_events = sorted((*auto, *manual_events), key=lambda event: event.start_seconds)
    _check_no_overlaps(all_events)
    return VisualPlan(events=tuple(all_events))


def shift_overlay_events_after_freezes(
    plan: VisualPlan,
) -> tuple[VisualEvent, ...]:
    """Slide keyword/diagram/conflict events past every freeze that precedes them.

    A freeze inserts real duration into the rendered video (the frame it holds
    is not in the source at all past that instant), so every overlay event
    timed against the *pre-freeze* preview needs to land later in the video
    the freeze pass actually produces. Zoom and freeze events themselves need
    no shift: the FFmpeg pass that inserts freezes computes zoom windows
    against the *source* segment it is already trimming.
    """

    freezes = sorted(
        (event for event in plan.events if event.kind == "freeze"),
        key=lambda event: event.start_seconds,
    )
    shifted: list[VisualEvent] = []
    for event in plan.events:
        if event.kind not in ("keyword", "diagram", "conflict"):
            shifted.append(event)
            continue
        offset = sum(
            freeze.freeze_seconds for freeze in freezes if freeze.start_seconds <= event.start_seconds
        )
        if offset == 0:
            shifted.append(event)
            continue
        shifted.append(
            VisualEvent(
                kind=event.kind,
                start_seconds=event.start_seconds + offset,
                end_seconds=event.end_seconds + offset,
                text=event.text,
                source=event.source,
            )
        )
    return tuple(shifted)


def _event_from_dict(item: object) -> VisualEvent:
    if not isinstance(item, dict):
        raise VisualPlanError("each plan event must be an object")
    kind = item.get("kind")
    if kind not in VISUAL_KINDS:
        raise VisualPlanError(f"event kind must be one of {VISUAL_KINDS}, got {kind!r}")
    text = item.get("text")
    return VisualEvent(
        kind=kind,
        start_seconds=item["start_seconds"],
        end_seconds=item["end_seconds"],
        text=tuple(text) if text else (),
        scale=item.get("scale"),
        freeze_seconds=item.get("freeze_seconds"),
        label=item.get("label"),
        source=item.get("source", "manual"),
    )


def plan_from_dict(payload: object) -> VisualPlan:
    """The inverse of :meth:`VisualPlan.to_dict` -- reload a saved ``visual-plan.json``."""

    if not isinstance(payload, dict) or "events" not in payload:
        raise VisualPlanError("a visual plan must be an object with an 'events' list")
    events = payload["events"]
    if not isinstance(events, list):
        raise VisualPlanError("'events' must be a list")
    return VisualPlan(events=tuple(_event_from_dict(item) for item in events))


def overlay_events_to_motion_text(
    events: "tuple[VisualEvent, ...]",
) -> tuple[MotionTextEvent, ...]:
    """Translate the on-screen keyword/diagram/conflict cues into Remotion's
    typographic events, reusing the editorial-typography layer wholesale
    instead of inventing a second renderer for a handful of extra layouts.

    A single line becomes a ``dominant_word`` (the same treatment a keyword
    callout gets); two or three lines become a ``stacked_hierarchy``, with the
    last line promoted to the heaviest weight -- for a diagram that is the
    resolution (``SEU PC``), for a conflict it is the punchline (``CONFLITO!``).
    """

    motion_events: list[MotionTextEvent] = []
    for index, event in enumerate(events):
        if event.kind not in ("keyword", "diagram", "conflict"):
            continue
        lines = event.text
        is_keyword = event.kind == "keyword"
        if len(lines) == 1:
            layout = "dominant_word"
            blocks: tuple[TextBlock, ...] = (
                TextBlock(text=lines[0], weight="massive", accent=not is_keyword),
            )
        else:
            layout = "stacked_hierarchy"
            blocks = tuple(TextBlock(text=line, weight="large") for line in lines[:-1]) + (
                TextBlock(text=lines[-1], weight="massive", accent=(event.kind == "conflict")),
            )
        motion_events.append(
            MotionTextEvent(
                event_id=f"visual_{index:03d}",
                blocks=blocks,
                role="keyword" if is_keyword else "statement",
                layout=layout,
                motion="scale_in" if is_keyword else "masked_reveal",
                start_seconds=event.start_seconds,
                end_seconds=event.end_seconds,
                intent="impact_word" if is_keyword else "visual_interruption",
                surface="bare" if is_keyword else "card",
            )
        )
    return tuple(motion_events)


def _check_no_overlaps(events: "list[VisualEvent] | tuple[VisualEvent, ...]") -> None:
    """A freeze or a zoom changes the base timeline/picture; they must not overlap
    each other, and nothing should stack on top of a freeze."""

    blocking = sorted(
        (event for event in events if event.kind in ("zoom", "freeze")),
        key=lambda event: event.start_seconds,
    )
    for previous, current in zip(blocking, blocking[1:]):
        if current.start_seconds < previous.end_seconds:
            raise VisualPlanError(
                "zoom/freeze events must not overlap: "
                f"{previous.kind}@{previous.start_seconds:.2f} vs "
                f"{current.kind}@{current.start_seconds:.2f}"
            )
