"""The Python -> Remotion contract for the motion-graphics layer.

The editorial decisions already exist:
:mod:`video_generator.domain.typography` reads the narration and decides *what*
goes on screen, *when*, *why* and *how important* each block is, emitting
:class:`~video_generator.domain.typography.MotionTextEvent` values. Remotion then
decides *how it looks* — layout, hierarchy, composition, motion — and renders a
transparent overlay that FFmpeg composites over the finished video.

This module is the bridge: it turns a sequence of ``MotionTextEvent`` into the
small JSON document Remotion consumes as input props. It is deliberately thin —
no new editorial judgement, just a faithful, deterministic translation — and,
like the rest of ``domain/``, pure stdlib with no I/O.

The shape it produces (see ``remotion/src/schema.ts`` for the consuming side)::

    {
      "schema_version": 1,
      "composition": {"width", "height", "fps", "durationInSeconds"},
      "theme": {"foreground", "accent", "muted", "background"},
      "events": [
        {"id", "start", "duration", "role", "layout", "variant", "motion",
         "blocks": [{"text", "importance", "accent"}]}
      ]
    }
"""

from __future__ import annotations

import json
import re
from typing import Any, Mapping, Sequence

from video_generator.domain.typography import (
    BLOCK_WEIGHTS,
    TEXT_MOTIONS,
    TEXT_ROLES,
    MotionTextEvent,
    TextBlock,
)

SCHEMA_VERSION = 1


class MotionGraphicsError(Exception):
    """Raised when a motion-graphics scene cannot be built or is malformed."""


# The five Remotion layouts. Fewer than the domain's seven compositions on
# purpose: several domain layouts are the same picture with a different anchor,
# so they collapse onto one Remotion component plus a ``variant`` string.
REMOTION_LAYOUTS = (
    "dominant-word",
    "small-plus-massive",
    "stacked-editorial",
    "split-contrast",
    "poster-statement",
)

# domain layout -> (remotion layout, variant)
_LAYOUT_MAP: Mapping[str, tuple[str, str | None]] = {
    "dominant_word": ("dominant-word", None),
    "small_plus_massive": ("small-plus-massive", None),
    "stacked_hierarchy": ("stacked-editorial", None),
    "edge_aligned": ("stacked-editorial", "edge"),
    "split_statement": ("split-contrast", None),
    "contrast_pair": ("split-contrast", "pair"),
    "centered_poster": ("poster-statement", None),
}

# The typographic weight the domain assigned becomes a coarse importance the
# layout reads: it decides which block is the one the eye lands on.
_WEIGHT_IMPORTANCE: Mapping[str, str] = {
    "micro": "support",
    "small": "support",
    "large": "secondary",
    "massive": "dominant",
}

IMPORTANCE_VALUES = ("dominant", "secondary", "support")

_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")

DEFAULT_THEME: Mapping[str, str] = {
    "foreground": "#EEF3F5",
    "accent": "#3CA3E5",
    "muted": "#CEC8C4",
}


def _hex_colour(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _HEX.match(value.strip()):
        raise MotionGraphicsError(f"{field} must be a '#RRGGBB' colour, got {value!r}")
    return value.strip().upper()


def resolve_theme(
    *,
    foreground: str | None = None,
    accent: str | None = None,
    muted: str | None = None,
    background: str | None = None,
) -> dict[str, Any]:
    """Build the ``theme`` block, falling back to the dark channel's palette.

    ``background`` is ``None`` for the real overlay (Remotion renders it
    transparent and FFmpeg composites it); a ``'#RRGGBB'`` value is only for
    stills and studio review, so a paused frame can be judged in context.
    """

    theme: dict[str, Any] = {
        "foreground": _hex_colour(foreground or DEFAULT_THEME["foreground"], "foreground"),
        "accent": _hex_colour(accent or DEFAULT_THEME["accent"], "accent"),
        "muted": _hex_colour(muted or DEFAULT_THEME["muted"], "muted"),
        "background": None if background is None else _hex_colour(background, "background"),
    }
    return theme


def _event_dict(event: MotionTextEvent, timeline_end: float) -> dict[str, Any]:
    if not isinstance(event, MotionTextEvent):
        raise MotionGraphicsError("events must be MotionTextEvent values")
    if event.layout not in _LAYOUT_MAP:
        raise MotionGraphicsError(f"no Remotion layout maps domain layout {event.layout!r}")
    if event.role not in TEXT_ROLES:
        raise MotionGraphicsError(f"unknown role {event.role!r}")
    if event.motion not in TEXT_MOTIONS:
        raise MotionGraphicsError(f"unknown motion {event.motion!r}")

    layout, variant = _LAYOUT_MAP[event.layout]

    start = round(float(event.start_seconds), 3)
    end = round(float(event.end_seconds), 3)
    if timeline_end > 0:
        end = min(end, timeline_end)
    duration = round(end - start, 3)
    if duration <= 0:
        raise MotionGraphicsError(
            f"event {event.event_id} has non-positive duration after clamping"
        )

    blocks: list[dict[str, Any]] = []
    for block in event.blocks:
        if block.weight not in BLOCK_WEIGHTS:
            raise MotionGraphicsError(f"unknown block weight {block.weight!r}")
        blocks.append(
            {
                "text": block.text,
                "importance": _WEIGHT_IMPORTANCE[block.weight],
                "accent": bool(block.accent),
            }
        )
    if not blocks:
        raise MotionGraphicsError(f"event {event.event_id} has no blocks")

    return {
        "id": event.event_id,
        "start": start,
        "duration": duration,
        "role": event.role,
        "layout": layout,
        "variant": variant,
        "motion": event.motion,
        "blocks": blocks,
    }


def build_motion_graphics_scene(
    events: Sequence[MotionTextEvent],
    *,
    width: int,
    height: int,
    fps: float,
    duration_seconds: float,
    foreground: str | None = None,
    accent: str | None = None,
    muted: str | None = None,
    background: str | None = None,
) -> dict[str, Any]:
    """Translate planned typographic events into a Remotion scene document.

    Raises :class:`MotionGraphicsError` for any structural problem (unknown
    layout, empty event, a duration that clamps to zero, a bad colour); it never
    silently drops an event.
    """

    for name, value in (("width", width), ("height", height)):
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise MotionGraphicsError(f"{name} must be a positive integer")
    if isinstance(fps, bool) or not isinstance(fps, (int, float)) or fps <= 0:
        raise MotionGraphicsError("fps must be a positive number")
    if (
        isinstance(duration_seconds, bool)
        or not isinstance(duration_seconds, (int, float))
        or duration_seconds <= 0
    ):
        raise MotionGraphicsError("duration_seconds must be a positive number")

    ordered = list(events)
    if len(ordered) != len(events):
        raise MotionGraphicsError("events must be a concrete sequence")

    timeline_end = float(duration_seconds)
    event_dicts = [_event_dict(event, timeline_end) for event in ordered]
    # keep the wire order stable and by time, so two runs diff cleanly
    event_dicts.sort(key=lambda item: (item["start"], item["id"]))

    seen: set[str] = set()
    for item in event_dicts:
        if item["id"] in seen:
            raise MotionGraphicsError(f"duplicate event id {item['id']!r}")
        seen.add(item["id"])

    return {
        "schema_version": SCHEMA_VERSION,
        "composition": {
            "width": int(width),
            "height": int(height),
            "fps": float(fps),
            "durationInSeconds": round(float(duration_seconds), 3),
        },
        "theme": resolve_theme(
            foreground=foreground, accent=accent, muted=muted, background=background
        ),
        "events": event_dicts,
    }


def scene_to_json(scene: Mapping[str, Any]) -> str:
    """Serialise a scene as compact, deterministic JSON (UTF-8, sorted keys)."""

    return json.dumps(scene, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def scene_from_typography_operation(
    parameters: Mapping[str, Any],
    *,
    width: int,
    height: int,
    fps: float,
    duration_seconds: float,
    background: str | None = None,
) -> dict[str, Any]:
    """Build a scene straight from a ``motion_typography`` operation's parameters.

    Lets a plan that already carries the ASS-oriented ``motion_typography``
    operation be rendered through Remotion instead, without re-running the
    planner. ``parameters`` is the same ``{"items": [...], "style": {...}}`` the
    FFmpeg path consumes.
    """

    items = parameters.get("items")
    if isinstance(items, (str, bytes)) or not isinstance(items, (list, tuple)) or not items:
        raise MotionGraphicsError("motion_typography parameters need a non-empty items list")

    events = tuple(
        MotionTextEvent(
            event_id=f"type_{index:03d}",
            blocks=tuple(
                TextBlock(
                    text=block["text"],
                    weight=block["weight"],
                    accent=bool(block.get("accent", False)),
                )
                for block in item["blocks"]
            ),
            role=item.get("role", "statement"),
            layout=item["layout"],
            motion=item["motion"],
            start_seconds=item["start_seconds"],
            end_seconds=item["end_seconds"],
        )
        for index, item in enumerate(items, start=1)
    )

    style = parameters.get("style") or {}
    return build_motion_graphics_scene(
        events,
        width=width,
        height=height,
        fps=fps,
        duration_seconds=duration_seconds,
        foreground=_from_ass(style.get("foreground")),
        accent=_from_ass(style.get("accent")),
        muted=_from_ass(style.get("muted")),
        background=background,
    )


def _from_ass(value: Any) -> str | None:
    """``&H00BBGGRR`` (or ``#RRGGBB``) -> ``#RRGGBB``; ``None`` passes through."""

    if value is None:
        return None
    if isinstance(value, str) and value.startswith("#"):
        return value
    if isinstance(value, str) and value.upper().startswith("&H"):
        digits = value[2:]
        if len(digits) == 8:
            digits = digits[2:]
        if len(digits) == 6:
            bb, gg, rr = digits[0:2], digits[2:4], digits[4:6]
            return f"#{rr}{gg}{bb}".upper()
    raise MotionGraphicsError(f"cannot read colour {value!r}")
