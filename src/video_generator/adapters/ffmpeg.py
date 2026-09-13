"""Non-destructive low-level media operations through local FFmpeg."""

from __future__ import annotations

import math
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Sequence

from video_generator.tooling import ToolResolutionError, resolve_media_tool


class FFmpegError(RuntimeError):
    """Raised when a local FFmpeg operation cannot produce its artifact."""


@dataclass(frozen=True, slots=True)
class SegmentArtifact:
    source_path: str
    output_path: str
    start_seconds: float
    end_seconds: float
    file_size_bytes: int
    mode: str = "copy"


@dataclass(frozen=True, slots=True)
class AudioArtifact:
    source_path: str
    output_path: str
    sample_rate_hz: int
    channels: int
    file_size_bytes: int


# Visual Direction v1. A segment may state *how* it appears, not only which
# file it is: the framing grammar, the editorial move and how hard the channel
# grade is applied. All optional and all appended last, so an older caller that
# builds these positionally is untouched.
COMPOSITIONS = (
    "fullscreen",
    "extreme_crop",
    "inset",
    "layered",
    "split",
    "text_focus",
)
DIRECTION_MOTIONS = (
    "static_hold",
    "slow_push_in",
    "slow_pull_out",
    "lateral_drift",
    "detail_push",
)
GRADE_INTENSITIES = ("none", "subtle", "standard", "strong")
CROP_BIASES = ("center", "top", "bottom", "left", "right")
TEXT_ZONES = ("top", "middle", "lower")


@dataclass(frozen=True, slots=True)
class SequenceClip:
    source_path: str
    start_seconds: float
    end_seconds: float
    fit: str | None = None
    composition: str | None = None
    crop_bias: str = "center"
    text_zone: str | None = None
    grade: str | None = None


@dataclass(frozen=True, slots=True)
class SequenceImage:
    source_path: str
    duration_seconds: float
    fit: str | None = None
    motion: str | None = None
    composition: str | None = None
    crop_bias: str = "center"
    text_zone: str | None = None
    grade: str | None = None


@dataclass(frozen=True, slots=True)
class CaptionCue:
    text: str
    start_seconds: float
    end_seconds: float


TEXT_EVENT_POSITIONS = ("top", "middle", "lower")
TEXT_EVENT_ANIMATIONS = ("fade", "pop", "slide", "highlight")


@dataclass(frozen=True, slots=True)
class TextEventCue:
    """One piece of on-screen editorial emphasis, burned alongside the captions.

    Not a caption: it does not transcribe the voice, it may overlap a caption
    in time, and it carries its own placement, motion and weight.
    """

    text: str
    start_seconds: float
    end_seconds: float
    position: str = "top"
    animation: str = "fade"
    emphasis: bool = False
    # The one run inside the line that carries the channel accent, as a
    # ``(start, end)`` character span. Only meaningful on a quiet keyword cue:
    # an emphasis cue is already accented end to end, and two accents in one
    # line is no accent at all.
    highlight: "tuple[int, int] | None" = None


@dataclass(frozen=True, slots=True)
class TextStyleSpec:
    """The typographic half of a video's visual identity.

    Plain values, not a domain object: the adapter stays a boundary and the
    brand kit that produced these lives in the domain.
    """

    font_name: str = "Sans"
    foreground: str = "&H00EEF3F5"
    accent: str = "&H003CA3E5"
    emphasis_scale: float = 1.6
    safe_margin_fraction: float = 0.06
    # Appended, never inserted: this dataclass is constructed positionally in
    # the workflow, so a new field goes on the end or every argument shifts.
    # --- Editorial Motion Typography only ---------------------------------- #
    # The second face. Motion typography rests on setting a quiet line against
    # a heavy word, and one family cannot carry that contrast on its own; the
    # default repeats ``font_name`` so nothing changes for existing callers.
    support_font_name: str = ""
    # The support line's colour. Dimmer than the foreground on purpose: at
    # equal weight the small line competes with the word it is introducing.
    muted: str = "&H00CEC8C4"


# What a font name may contain before it is written into an ASS ``\fn``
# override. Anything else could close the override block and turn a style value
# into subtitle syntax.
_FONT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}$")


@dataclass(frozen=True, slots=True)
class MotionTextBlockCue:
    """One run of type inside a motion-typography event."""

    text: str
    weight: str = "massive"
    accent: bool = False


MOTION_TEXT_WEIGHTS = ("micro", "small", "large", "massive")
MOTION_TEXT_LAYOUTS = (
    "dominant_word",
    "stacked_hierarchy",
    "small_plus_massive",
    "split_statement",
    "edge_aligned",
    "centered_poster",
    "contrast_pair",
)
MOTION_TEXT_MOTIONS = ("fade_rise", "scale_in", "masked_reveal", "stagger_rise")


@dataclass(frozen=True, slots=True)
class MotionTextCue:
    """One typographic intervention: several blocks composed into a frame.

    Not a caption and not a :class:`TextEventCue` either. A caption cue is one
    line in a fixed band; this is a small piece of art direction — several
    blocks at different sizes, placed by a named layout, arriving under a named
    motion. The adapter owns the pixels; the composition that produced them
    lives in ``video_generator.domain.typography``.
    """

    blocks: "tuple[MotionTextBlockCue, ...]"
    start_seconds: float
    end_seconds: float
    layout: str = "stacked_hierarchy"
    motion: str = "fade_rise"


@dataclass(frozen=True, slots=True)
class DirectionSpec:
    """The numbers a Visual Direction plan hands to the filter graph.

    Plain values, not a domain object: this is a boundary, and the policy that
    produced them lives in ``video_generator.domain.direction``. The defaults
    describe a restrained documentary treatment, so a caller that supplies only
    a composition still gets a coherent picture.
    """

    # --- grade, at intensity "standard" ------------------------------------ #
    saturation: float = 0.80
    shadow_density: float = 0.060
    highlight_gain: float = 0.030
    highlight_ceiling: float = 0.045
    cool_shift: float = 0.035
    grain: float = 4.0
    vignette_angle: float = 0.42
    # A multiplicative pull on the whole curve, not only on the highlights.
    # Trimming the top of the range cannot bring a photograph of a white desk
    # into a dark piece; pulling the whole range can. Zero by default so an
    # existing caller's grade is unchanged.
    luminance_pull: float = 0.0
    intensities: "Mapping[str, float]" = field(
        default_factory=lambda: {
            "none": 0.0,
            "subtle": 0.55,
            "standard": 1.0,
            "strong": 1.35,
        }
    )
    # --- composition geometry ---------------------------------------------- #
    extreme_crop_zoom: float = 1.55
    inset_scale: float = 0.70
    background_blur_sigma: float = 26.0
    background_darkening: float = 0.45
    split_gap_fraction: float = 0.008
    scrim_opacity: float = 0.55
    scrim_height_fraction: float = 0.34
    # --- motion travel over the whole clip --------------------------------- #
    push_travel: float = 0.075
    detail_push_travel: float = 0.115
    drift_travel: float = 0.090

    def scale_for(self, intensity: str | None) -> float:
        if intensity is None:
            return 0.0
        if intensity not in self.intensities:
            raise FFmpegError(f"unknown grade intensity: {intensity}")
        return float(self.intensities[intensity])


DEFAULT_DIRECTION_SPEC = DirectionSpec()


@dataclass(frozen=True, slots=True)
class SequenceArtifact:
    source_paths: tuple[str, ...]
    output_path: str
    duration_seconds: float
    file_size_bytes: int
    narration_source_path: str | None = None
    caption_count: int = 0
    music_source_path: str | None = None
    music_gain_db: float | None = None
    image_count: int = 0
    narration_text_sha256: str | None = None
    music_fade_in_seconds: float = 0.0
    music_fade_out_seconds: float = 0.0
    video_fade_in_seconds: float = 0.0
    video_fade_out_seconds: float = 0.0
    narration_lead_in_seconds: float = 0.0
    music_duck_db: float | None = None
    # Appended, never inserted: this dataclass is built positionally in places,
    # so a new field goes on the end or it silently shifts every argument.
    text_event_count: int = 0
    directed_segment_count: int = 0
    motion_text_count: int = 0


@dataclass(frozen=True, slots=True)
class CutPreviewArtifact:
    """A preview cut from one source by concatenating the kept time ranges."""

    source_path: str
    output_path: str
    segment_count: int
    kept_seconds: float
    file_size_bytes: int
    frame_rate: str | None = None
    aside_count: int = 0


@dataclass(frozen=True, slots=True)
class CutSegment:
    """One piece of the rendered timeline: a plain keep, or a marked aside.

    A plain ``(start, end)`` tuple is accepted wherever :class:`CutSegment` is
    expected -- it means a keep at 1x, no treatment. ``speed`` re-times the
    segment (video and audio together, in sync); ``grayscale`` desaturates it;
    ``label`` burns a short, single-line marker on screen for its duration. All
    three are ``render_cut_preview``'s only visual/audio departure from a plain
    cut-and-concat -- there is no fade, no music and no transition here.
    """

    start_seconds: float
    end_seconds: float
    speed: float = 1.0
    grayscale: bool = False
    label: str | None = None


IMAGE_TIMELINE_FPS = 30
# Above this many characters the filter graph is handed to FFmpeg as a file.
# Well under the ~32k Windows command-line ceiling, and low enough that the
# file path is exercised by ordinary directed timelines rather than only by
# pathological ones.
_FILTER_GRAPH_INLINE_LIMIT = 4000
# How many boxes make up a text scrim. See _scrim_chain for why it is not five.
_SCRIM_STEPS = 18
# How long the music bed takes to reach the ducked level and to come back. The
# attack lands exactly on the first word (it ramps over the silence before it)
# and the release starts when the voice track ends.
MUSIC_DUCK_RAMP_SECONDS = 0.35


def _time(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FFmpegError(f"{name} must be a finite non-negative number")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise FFmpegError(f"{name} must be a finite non-negative number")
    return result


def _cleanup(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _ass_timestamp(seconds: float) -> str:
    total_centiseconds = round(seconds * 100)
    hours, remainder = divmod(total_centiseconds, 360_000)
    minutes, remainder = divmod(remainder, 6_000)
    whole_seconds, centiseconds = divmod(remainder, 100)
    return f"{hours:d}:{minutes:02d}:{whole_seconds:02d}.{centiseconds:02d}"


# Advanced SubStation script rendered by libass. PlayResX/Y are pinned to the
# real frame so FontSize and every margin below are plain 1280x720 pixels: one
# readable line, held inside a platform-safe band (MarginV ~10% of the height)
# and kept clear of the frame edges. The text is drawn with a thick outline plus
# a soft shadow (BorderStyle 1) instead of an opaque box, so it stays legible on
# dark footage without a black bar across the frame. Caption text carries no
# override braces (the workflow already rejects "<>{}"), so it can never become
# script syntax.
# The caption style is expressed as fractions of the delivery canvas, not fixed
# pixels: a 32 px line that reads well at 720p is only 3% of a 1080p frame and
# disappears on a phone. The fractions are calibrated so a 1280x720 canvas keeps
# the numbers this project shipped with, and every larger canvas scales with it.
_CAPTION_FONT_FRACTION = 0.04444  # 32 px at 720p, 48 px at 1080p
_CAPTION_SIDE_MARGIN_FRACTION = 0.109375  # 140 px at 1280 wide
_CAPTION_BOTTOM_MARGIN_FRACTION = 0.1  # 72 px at 720 tall

_CAPTION_ASS_HEADER = (
    "[Script Info]\n"
    "ScriptType: v4.00+\n"
    "WrapStyle: 2\n"
    "ScaledBorderAndShadow: yes\n"
    "PlayResX: {width}\n"
    "PlayResY: {height}\n"
    "\n"
    "[V4+ Styles]\n"
    "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
    "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
    "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
    "MarginR, MarginV, Encoding\n"
    "Style: Caption,Sans,{font_size},&H00FFFFFF,&H00FFFFFF,&H00101010,&H80000000,"
    "0,0,0,0,100,100,0,0,1,{outline},{shadow},2,{margin_x},{margin_x},{margin_y},1\n"
    "\n"
    "[Events]\n"
    "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
)

# The emphasis layer rides in the same script as the captions: one libass pass,
# one set of pixel coordinates, and no second filter competing for the frame.
# Two weights only — an accented headline and a quieter keyword — because a
# hierarchy nobody can read at a glance is not a hierarchy.
_EMPHASIS_STYLE_TEMPLATE = (
    "Style: Emphasis,{font},{emphasis_size},{accent},{accent},&H00101010,&H80000000,"
    "1,0,0,0,100,100,0,0,1,{outline},{shadow},{align},{margin_x},{margin_x},{margin_y},1\n"
    "Style: Keyword,{font},{keyword_size},{foreground},{foreground},&H00101010,&H80000000,"
    "0,0,0,0,100,100,0,0,1,{outline},{shadow},{align},{margin_x},{margin_x},{margin_y},1\n"
)

# ASS alignment numbers for the three bands an event may occupy. "lower" sits
# above the caption band rather than in it, so the two layers never collide.
_POSITION_ALIGNMENT = {"top": 8, "middle": 5, "lower": 2}


def _inline_colour(style_colour: str) -> str:
    """``&H00BBGGRR`` (a style value) -> ``&HBBGGRR&`` (an override value)."""

    digits = style_colour.removeprefix("&H").removesuffix("&")
    if len(digits) == 8:
        digits = digits[2:]
    return f"&H{digits}&"


def _emphasis_styles(width: int, height: int, style: "TextStyleSpec") -> str:
    """The Emphasis and Keyword styles, scaled to the delivery canvas."""

    base = max(12, round(height * _CAPTION_FONT_FRACTION))
    margin_x = max(8, round(width * style.safe_margin_fraction))
    margin_y = max(8, round(height * style.safe_margin_fraction))
    return _EMPHASIS_STYLE_TEMPLATE.format(
        font=style.font_name,
        emphasis_size=max(14, round(base * style.emphasis_scale)),
        keyword_size=max(12, round(base * 1.15)),
        accent=style.accent,
        foreground=style.foreground,
        outline=max(2, round(height / 240)),
        shadow=max(1, round(height / 720)),
        align=8,
        margin_x=margin_x,
        margin_y=margin_y,
    )


def _event_override(
    cue: "TextEventCue", width: int, height: int, style: "TextStyleSpec"
) -> str:
    """The ASS override run that places and animates one emphasis event.

    Four presets, each doing one legible thing. Nothing here is decoration for
    its own sake: a number should land, a question should breathe in.
    """

    alignment = _POSITION_ALIGNMENT.get(cue.position, 8)
    parts = [f"\\an{alignment}"]
    if cue.animation == "pop":
        # arrive slightly small and settle: reads as emphasis, not as bounce
        parts.append("\\fad(90,160)\\fscx88\\fscy88")
        parts.append("\\t(0,150,\\fscx104\\fscy104)\\t(150,260,\\fscx100\\fscy100)")
    elif cue.animation == "slide":
        margin_x = max(8, round(width * style.safe_margin_fraction))
        margin_y = max(8, round(height * style.safe_margin_fraction))
        centre_x = width // 2
        y = {8: margin_y, 5: height // 2, 2: height - margin_y}.get(alignment, margin_y)
        travel = max(12, round(width * 0.03))
        parts.append(
            f"\\move({centre_x - travel},{y},{centre_x},{y},0,220)\\fad(120,180)"
        )
    elif cue.animation == "highlight":
        # No accent on the outline. A thick accented outline around every
        # letter is read as an accented *line*, which is the opposite of what
        # a highlight is for — the accent belongs to the one run inside the
        # text that `_event_text` paints, and to nothing else.
        parts.append("\\fad(110,160)")
    else:  # fade
        parts.append("\\fad(200,220)")
    return "{" + "".join(parts) + "}"


def _event_text(cue: "TextEventCue", style: "TextStyleSpec") -> str:
    """The Dialogue body for one emphasis cue, with its accented run.

    The accent marks *one word*, not the line: a keyword cue is set in the
    foreground colour and only the highlighted span flips to the channel
    accent, which is what keeps the colour a highlight rather than a look.
    """

    text = cue.text.strip()
    if cue.highlight is None or cue.emphasis:
        return text
    start, end = cue.highlight
    if not 0 <= start < end <= len(text):
        raise FFmpegError("text event highlight must be a span inside the text")
    accent = _inline_colour(style.accent)
    base = _inline_colour(style.foreground)
    return (
        f"{text[:start]}{{\\c{accent}}}{text[start:end]}"
        f"{{\\c{base}}}{text[end:]}"
    )


# --------------------------------------------------------------------------- #
# Editorial Motion Typography — pixels for a composition the domain designed
# --------------------------------------------------------------------------- #
# The typographic scale, as multiples of the same base the captions use. The
# jump from "small" to "large" is deliberately violent: a scale that steps
# evenly reads as one size badly printed, and the whole point of this layer is
# that a viewer sees the hierarchy before reading a word.
_MOTION_WEIGHT_SCALE = {"micro": 0.70, "small": 0.90, "large": 2.30, "massive": 4.10}
# Mean glyph advance of an uppercase grotesque, in ems. Used only to shrink a
# block that would otherwise run off the frame — libass does the real
# typesetting, this just has to be conservative enough never to overflow.
_MOTION_ADVANCE = 0.605
# Letter-spacing, in ems. Wide tracking is what makes a small line read as a
# label rather than as a caption that got lost.
_MOTION_TRACKING = {"micro": 0.20, "small": 0.17, "large": 0.0, "massive": -0.01}
_MOTION_LINE_HEIGHT = 1.14
# How far a block travels as it arrives, as a fraction of its own size, and how
# long each motion takes. Small numbers: the type should look placed, not flown
# in.
_MOTION_RISE = 0.26
_MOTION_STAGGER_MS = 150
_MOTION_LEAD_MS = 55
_MOTION_ALIGNMENTS = {"left": 7, "right": 9, "centre": 8}


def _motion_style(width: int, height: int, style: "TextStyleSpec") -> str:
    """The single ASS style every motion block overrides from."""

    return (
        f"Style: Motion,{style.font_name},"
        f"{max(12, round(height * _CAPTION_FONT_FRACTION))},"
        f"{style.foreground},{style.foreground},&H000A0A0C,&HA0000000,"
        f"0,0,0,0,100,100,0,0,1,{max(2, round(height / 300))},"
        f"{max(1, round(height / 640))},7,0,0,0,1\n"
    )


def _motion_size(
    text: str, weight: str, height: int, available: float
) -> float:
    """The pixel size for one block, shrunk if it would leave the frame."""

    size = height * _CAPTION_FONT_FRACTION * _MOTION_WEIGHT_SCALE[weight]
    advance = _MOTION_ADVANCE + max(0.0, _MOTION_TRACKING[weight])
    estimated = max(1, len(text)) * advance * size
    if estimated > available > 0:
        size *= available / estimated
    return max(10.0, size)


def _motion_placements(
    cue: "MotionTextCue", width: int, height: int, style: "TextStyleSpec"
) -> "list[tuple[str, float, float, float, MotionTextBlockCue]]":
    """Where every block of one event sits, in real pixels.

    Seven compositions, each answering "where does the eye land first" a
    different way. They share one stacking routine and differ only in their
    anchor, which is what keeps them a family rather than seven templates: the
    asymmetry, the negative space and the flush edges are in the anchors.
    """

    margin_x = max(8, round(width * style.safe_margin_fraction))
    margin_y = max(8, round(height * style.safe_margin_fraction))
    layout = cue.layout
    blocks = list(cue.blocks)

    # a tighter left margin: type that touches the edge reads as part of the
    # frame rather than as something laid on top of it
    left = round(margin_x * 0.42) if layout == "edge_aligned" else margin_x
    indent = round(width * 0.17) if layout == "contrast_pair" else 0
    available = width - left - margin_x - indent

    sizes = [
        _motion_size(block.text, block.weight, height, available) for block in blocks
    ]
    heights = [size * _MOTION_LINE_HEIGHT for size in sizes]
    gaps = [
        max(sizes[index], sizes[index + 1]) * (0.34 if layout == "contrast_pair" else 0.10)
        for index in range(len(sizes) - 1)
    ]
    total = sum(heights) + sum(gaps)

    def _stack(anchor: str, x: float, top: float, shift: float = 0.0) -> list:
        placements = []
        cursor = top
        for index, block in enumerate(blocks):
            placements.append(
                (anchor, x + (shift if index else 0.0), cursor, sizes[index], block)
            )
            cursor += heights[index] + (gaps[index] if index < len(gaps) else 0.0)
        return placements

    if layout == "dominant_word":
        # off-centre and low: the frame keeps its picture, the word takes the
        # weight
        return _stack("left", left, height * 0.61 - heights[0] / 2)
    if layout == "stacked_hierarchy":
        return _stack("left", left, height * 0.27)
    if layout == "small_plus_massive":
        return _stack("left", left, height * 0.56 - total / 2)
    if layout == "edge_aligned":
        return _stack("left", left, height * 0.86 - total)
    if layout == "centered_poster":
        return _stack("centre", width / 2, height * 0.46 - total / 2)
    if layout == "contrast_pair":
        return _stack("left", left, height * 0.26, shift=indent)
    # split_statement: one block high-left, the other low-right, and as much
    # empty frame as possible between them
    first, second = blocks[0], blocks[-1]
    return [
        ("left", left, height * 0.14, sizes[0], first),
        (
            "right",
            width - margin_x,
            height * 0.84 - sizes[-1] * _MOTION_LINE_HEIGHT,
            sizes[-1],
            second,
        ),
    ]


def _motion_override(
    anchor: str,
    x: float,
    y: float,
    size: float,
    block: "MotionTextBlockCue",
    cue: "MotionTextCue",
    index: int,
    width: int,
    style: "TextStyleSpec",
) -> str:
    """The ASS override run that sets, places and animates one block."""

    support = block.weight in ("micro", "small")
    colour = _inline_colour(
        style.accent if block.accent else (style.muted if support else style.foreground)
    )
    tracking = _MOTION_TRACKING[block.weight] * size
    face = (style.support_font_name or style.font_name) if support else style.font_name
    parts = [
        f"\\an{_MOTION_ALIGNMENTS[anchor]}",
        f"\\fn{face}",
        f"\\fs{size:.1f}",
        f"\\b{0 if support else 1}",
        f"\\c{colour}",
        "\\3c&H000000&",
        # The support line needs a *relatively* heavier outline than the word
        # it introduces. At 28 px a 3 % outline is under a pixel, which is why
        # the small type kept disappearing into a crowd or a bright wall while
        # the display word beside it stayed perfectly readable.
        f"\\bord{max(3.0, size * 0.13) if support else max(2.0, size * 0.030):.1f}",
        f"\\shad{max(1.6, size * 0.055) if support else max(1.2, size * 0.022):.1f}",
        "\\4a&H40&",
        "\\be1",
    ]
    if abs(tracking) >= 0.5:
        parts.append(f"\\fsp{tracking:.1f}")
    rise = size * _MOTION_RISE
    if cue.motion == "scale_in":
        parts.append(
            f"\\pos({x:.0f},{y:.0f})\\fad(140,220)"
            "\\fscx84\\fscy84\\t(0,260,\\fscx100\\fscy100)"
        )
    elif cue.motion == "masked_reveal":
        # A rectangular clip that grows downward while the block rises into it:
        # the word is uncovered rather than moved, which is the one motion that
        # reads as editing rather than as animation.
        top = int(y - size * 0.34)
        bottom = int(y + size * _MOTION_LINE_HEIGHT + size * 0.06)
        parts.append(
            f"\\move({x:.0f},{y + rise:.0f},{x:.0f},{y:.0f},0,340)\\fad(0,220)"
            f"\\clip(0,{top},{width},{bottom})"
        )
    else:  # fade_rise and stagger_rise share the arrival, not the timing
        parts.append(
            f"\\move({x:.0f},{y + rise:.0f},{x:.0f},{y:.0f},0,320)\\fad(200,240)"
        )
    return "{" + "".join(parts) + "}"


def _motion_dialogues(
    cue: "MotionTextCue",
    start: float,
    end: float,
    width: int,
    height: int,
    style: "TextStyleSpec",
) -> "list[str]":
    """Every Dialogue line for one event, one per block.

    A block per line is what makes the stagger real: the support line is
    already legible when the display word lands, which is the hierarchy being
    revealed in time rather than merely printed.
    """

    step = _MOTION_STAGGER_MS if cue.motion == "stagger_rise" else _MOTION_LEAD_MS
    lines = []
    for index, (anchor, x, y, size, block) in enumerate(
        _motion_placements(cue, width, height, style)
    ):
        override = _motion_override(
            anchor, x, y, size, block, cue, index, width, style
        )
        begin = min(start + index * step / 1000.0, max(start, end - 0.2))
        lines.append(
            f"Dialogue: 1,{_ass_timestamp(begin)},{_ass_timestamp(end)},"
            f"Motion,,0,0,0,,{override}{block.text}"
        )
    return lines


def _caption_ass_header(width: int, height: int) -> str:
    """Render the caption style scaled to the delivery canvas.

    A burned caption competes with whatever is behind it, so the outline and the
    shadow grow with the frame too: white text over a bright photo is only
    readable because of them.
    """

    return _CAPTION_ASS_HEADER.format(
        width=width,
        height=height,
        font_size=max(12, round(height * _CAPTION_FONT_FRACTION)),
        outline=max(2, round(height / 240)),
        shadow=max(1, round(height / 720)),
        margin_x=max(8, round(width * _CAPTION_SIDE_MARGIN_FRACTION)),
        margin_y=max(8, round(height * _CAPTION_BOTTOM_MARGIN_FRACTION)),
    )


def _fit_filter(fit: str, width: int, height: int) -> str:
    """Deterministic scale-to-canvas chain for an explicit target format.

    ``contain`` fits the whole frame and pads the remainder with black
    (letterbox/pillarbox); ``cover`` fills the canvas and crops the overflow
    from the centre. Both end on ``setsar=1`` so the concat sees square pixels.
    """

    if fit == "contain":
        return (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease:"
            f"force_divisible_by=2,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1"
        )
    if fit == "cover":
        return (
            f"scale={width}:{height}:force_original_aspect_ratio=increase:"
            f"force_divisible_by=2,"
            f"crop={width}:{height}:(iw-{width})/2:(ih-{height})/2,setsar=1"
        )
    raise FFmpegError('fit must be "contain" or "cover"')


KEN_BURNS_MOTIONS = ("zoom_in", "zoom_out", "pan_left", "pan_right", "pan_up", "pan_down")
# Fractional travel over the clip: zooms cover 1.0 <-> 1.12, pans hold 1.12 and
# translate across the crop margin that zoom opens up. Fixed on purpose so the
# move is a pure function of (motion, canvas, duration).
_KEN_BURNS_TRAVEL = 0.12
_KEN_BURNS_PAN_ZOOM = 1.0 + _KEN_BURNS_TRAVEL
_KEN_BURNS_UPSCALE = 4


def _ken_burns_filter(motion: str, width: int, height: int, frames: int) -> str:
    """A deterministic ``zoompan`` pass for a still frame.

    The frame is pre-upscaled (``scale=iw*4:ih*4``) so the sub-pixel zoom steps
    do not jitter, then ``zoompan`` walks a window across it — one output frame
    per input frame (``d=1``) — and rescales back to the canvas (``s=WxH``).
    ``on`` is the cumulative output frame index, so ``on/(frames-1)`` ramps
    linearly from 0 to 1 across the clip.
    """

    if motion not in KEN_BURNS_MOTIONS:
        raise FFmpegError("motion must be one of " + ", ".join(KEN_BURNS_MOTIONS))
    progress = f"on/{max(frames - 1, 1)}"
    travel = format(_KEN_BURNS_TRAVEL, ".15g")
    pan_zoom = format(_KEN_BURNS_PAN_ZOOM, ".15g")
    centre_x = "iw/2-(iw/zoom/2)"
    centre_y = "ih/2-(ih/zoom/2)"
    if motion == "zoom_in":
        zoom, pan_x, pan_y = f"1+{travel}*{progress}", centre_x, centre_y
    elif motion == "zoom_out":
        zoom, pan_x, pan_y = f"{pan_zoom}-{travel}*{progress}", centre_x, centre_y
    elif motion == "pan_left":
        zoom = pan_zoom
        pan_x, pan_y = f"(iw-iw/zoom)*(1-{progress})", "(ih-ih/zoom)/2"
    elif motion == "pan_right":
        zoom = pan_zoom
        pan_x, pan_y = f"(iw-iw/zoom)*({progress})", "(ih-ih/zoom)/2"
    elif motion == "pan_up":
        zoom = pan_zoom
        pan_x, pan_y = "(iw-iw/zoom)/2", f"(ih-ih/zoom)*(1-{progress})"
    else:  # pan_down
        zoom = pan_zoom
        pan_x, pan_y = "(iw-iw/zoom)/2", f"(ih-ih/zoom)*({progress})"
    return (
        f"scale=iw*{_KEN_BURNS_UPSCALE}:ih*{_KEN_BURNS_UPSCALE},"
        f"zoompan=z={zoom}:x={pan_x}:y={pan_y}:d=1:s={width}x{height}:"
        f"fps={IMAGE_TIMELINE_FPS}"
    )


# --------------------------------------------------------------------------- #
# Visual Direction v1 - composition, editorial motion and the channel grade
# --------------------------------------------------------------------------- #
def _even(value: float) -> int:
    """Nearest even integer >= 2: every codec here wants even dimensions."""

    return max(2, int(round(value / 2.0)) * 2)


def _fmt(value: float) -> str:
    return format(float(value), ".15g")


def _crop_offsets(bias: str, width: int, height: int) -> tuple[str, str]:
    """Where a cover crop takes its window from, in ``crop`` expressions."""

    if bias not in CROP_BIASES:
        raise FFmpegError("crop_bias must be one of " + ", ".join(CROP_BIASES))
    x = f"(iw-{width})/2"
    y = f"(ih-{height})/2"
    if bias == "top":
        y = "0"
    elif bias == "bottom":
        y = f"ih-{height}"
    elif bias == "left":
        x = "0"
    elif bias == "right":
        x = f"iw-{width}"
    return x, y


def _cover_chain(width: int, height: int, bias: str = "center", zoom: float = 1.0) -> str:
    """Fill a ``width`` x ``height`` window from a source of any shape.

    ``zoom`` above 1 scales past the window first, so the crop lands *inside*
    the picture - which is what makes ``extreme_crop`` a closer framing rather
    than the same framing at a different size.
    """

    target_w = _even(width * zoom)
    target_h = _even(height * zoom)
    x, y = _crop_offsets(bias, width, height)
    return (
        f"scale={target_w}:{target_h}:force_original_aspect_ratio=increase:"
        f"force_divisible_by=2,crop={width}:{height}:{x}:{y},setsar=1"
    )


def _treated_background(width: int, height: int, spec: "DirectionSpec") -> str:
    """The blurred, darkened, desaturated copy an inset or a band sits on.

    Built from the shot's own asset on purpose: a derived background belongs to
    the picture, while an arbitrary colour behind it is a slide.
    """

    level = _fmt(max(0.05, 1.0 - spec.background_darkening))
    parts = [_cover_chain(width, height)]
    if spec.background_blur_sigma > 0:
        parts.append(f"gblur=sigma={_fmt(spec.background_blur_sigma)}")
    parts.append("curves=all='0/0 1/" + level + "'")
    parts.append("hue=s=0.25")
    return ",".join(parts)


def _scrim_chain(width: int, height: int, zone: str, spec: "DirectionSpec") -> str:
    """A stepped darkening under the band where on-screen type will sit.

    Many thin decreasing boxes rather than one thick bar. The step count is
    high on purpose: five steps are individually visible as grey bars on a
    flat bright surface (measured on a render), while at this count each step
    changes the picture by about one part in 256, which is under the banding
    threshold of the encoder that follows.
    """

    if zone not in TEXT_ZONES:
        raise FFmpegError("text_zone must be one of " + ", ".join(TEXT_ZONES))
    band = _even(height * spec.scrim_height_fraction)
    if zone == "top":
        top = 0
        descending = True
    elif zone == "lower":
        top = height - band
        descending = False
    else:
        top = (height - band) // 2
        descending = True
    steps = _SCRIM_STEPS
    step_h = max(2, band // steps)
    boxes = []
    for index in range(steps):
        linear = (steps - index) / steps if descending else (index + 1) / steps
        # squared, so the far end of the scrim reaches the picture at nearly
        # zero instead of ending on a visible edge
        opacity = spec.scrim_opacity * linear * linear
        if opacity <= 0.01:
            continue
        y = top + index * step_h
        boxes.append(
            f"drawbox=x=0:y={y}:w={width}:h={step_h}:"
            f"color=black@{_fmt(round(opacity, 3))}:t=fill"
        )
    return ",".join(boxes)


def _grade_chain(intensity: "str | None", spec: "DirectionSpec") -> str:
    """The channel's colour treatment, scaled by one intensity factor.

    ``eq`` is absent from some FFmpeg builds, so contrast and level live in
    ``curves`` and saturation in ``hue`` - both are in the LGPL core. The curve
    is a gentle S: shadows pushed down, highlights lifted a little and then
    capped, which is the whole of "documentary, not crushed".
    """

    scale = spec.scale_for(intensity)
    if scale <= 0.0:
        return ""
    parts: list[str] = []
    saturation = 1.0 + (spec.saturation - 1.0) * scale
    if abs(saturation - 1.0) > 1e-6:
        parts.append(f"hue=s={_fmt(round(max(0.0, saturation), 4))}")
    shadow = min(0.22, spec.shadow_density * scale)
    highlight = min(0.22, spec.highlight_gain * scale)
    ceiling = min(0.35, spec.highlight_ceiling * scale)
    pull = max(0.0, min(0.6, spec.luminance_pull * scale))
    if shadow > 1e-6 or highlight > 1e-6 or ceiling > 1e-6 or pull > 1e-6:
        keep = 1.0 - pull
        p1 = round(max(0.01, (0.25 - shadow) * keep), 4)
        p2 = round(min(0.97, (0.75 + highlight) * keep), 4)
        p3 = round(max(0.20, (1.0 - ceiling) * keep), 4)
        parts.append(
            "curves=all='0/0 0.25/" + _fmt(p1) + " 0.75/" + _fmt(p2)
            + " 1/" + _fmt(p3) + "'"
        )
    cool = min(0.4, spec.cool_shift * scale)
    if cool > 1e-6:
        parts.append(
            f"colorbalance=rs=-{_fmt(round(cool, 4))}:bs={_fmt(round(cool, 4))}:"
            f"rm=-{_fmt(round(cool / 2, 4))}:bm={_fmt(round(cool / 2, 4))}"
        )
    grain = int(round(spec.grain * scale))
    if grain >= 1:
        parts.append(f"noise=alls={min(grain, 40)}:allf=t+u")
    angle = spec.vignette_angle * scale
    if angle > 0.01:
        parts.append(f"vignette=a={_fmt(round(angle, 4))}")
    return ",".join(parts)


def _direction_motion_filter(
    motion: str,
    crop_bias: str,
    width: int,
    height: int,
    frames: int,
    spec: "DirectionSpec",
) -> str:
    """An editorial move as a deterministic ``zoompan`` pass.

    The travel is small on purpose - a push you can *see* moving is a zoom, and
    a zoom is not direction. ``static_hold`` returns nothing at all, because
    the absence of a move is a decision this vocabulary can express.
    """

    if motion not in DIRECTION_MOTIONS:
        raise FFmpegError("motion must be one of " + ", ".join(DIRECTION_MOTIONS))
    if motion == "static_hold":
        return ""
    if crop_bias not in CROP_BIASES:
        raise FFmpegError("crop_bias must be one of " + ", ".join(CROP_BIASES))
    progress = f"on/{max(frames - 1, 1)}"
    centre_x = "iw/2-(iw/zoom/2)"
    centre_y = "ih/2-(ih/zoom/2)"
    if motion == "slow_push_in":
        travel = spec.push_travel
        zoom = f"1+{_fmt(travel)}*{progress}"
        pan_x, pan_y = centre_x, centre_y
    elif motion == "slow_pull_out":
        travel = spec.push_travel
        zoom = f"{_fmt(1.0 + travel)}-{_fmt(travel)}*{progress}"
        pan_x, pan_y = centre_x, centre_y
    elif motion == "detail_push":
        travel = spec.detail_push_travel
        zoom = f"1+{_fmt(travel)}*{progress}"
        pan_x, pan_y = centre_x, centre_y
    else:  # lateral_drift - the bias decides which way the frame travels
        travel = spec.drift_travel
        zoom = _fmt(1.0 + travel)
        if crop_bias in ("top", "bottom"):
            pan_x = "(iw-iw/zoom)/2"
            forward = crop_bias == "bottom"
            pan_y = (
                f"(ih-ih/zoom)*({progress})"
                if forward
                else f"(ih-ih/zoom)*(1-{progress})"
            )
        else:
            forward = crop_bias != "left"
            pan_x = (
                f"(iw-iw/zoom)*({progress})"
                if forward
                else f"(iw-iw/zoom)*(1-{progress})"
            )
            pan_y = "(ih-ih/zoom)/2"
    if travel <= 0.0:
        return ""
    return (
        f"scale=iw*{_KEN_BURNS_UPSCALE}:ih*{_KEN_BURNS_UPSCALE},"
        f"zoompan=z={zoom}:x={pan_x}:y={pan_y}:d=1:s={width}x{height}:"
        f"fps={IMAGE_TIMELINE_FPS}"
    )


def _composition_graph(
    source_label: str,
    output_label: str,
    *,
    composition: str,
    fit: str,
    crop_bias: str,
    text_zone: "str | None",
    width: int,
    height: int,
    spec: "DirectionSpec",
    node: str,
) -> "list[str]":
    """The filter statements that turn one decoded source into a framed canvas.

    Returns a list because half the grammar needs more than a linear chain: an
    inset, a band and a split each hold two treatments of the same picture at
    once, which in FFmpeg means ``split`` plus ``overlay``.
    """

    if composition not in COMPOSITIONS:
        raise FFmpegError("composition must be one of " + ", ".join(COMPOSITIONS))
    if composition == "fullscreen":
        return [f"[{source_label}]{_fit_filter(fit, width, height)}[{output_label}]"]
    if composition == "extreme_crop":
        chain = _cover_chain(width, height, crop_bias, spec.extreme_crop_zoom)
        return [f"[{source_label}]{chain}[{output_label}]"]
    if composition == "text_focus":
        chain = _cover_chain(width, height, crop_bias)
        scrim = _scrim_chain(width, height, text_zone or "top", spec)
        if scrim:
            chain = f"{chain},{scrim}"
        return [f"[{source_label}]{chain}[{output_label}]"]
    if composition == "inset":
        inner_w = _even(width * spec.inset_scale)
        inner_h = _even(height * spec.inset_scale)
        border = max(2, _even(height * 0.004))
        return [
            f"[{source_label}]split=2[{node}bg][{node}fg]",
            f"[{node}bg]{_treated_background(width, height, spec)}[{node}bgo]",
            f"[{node}fg]scale={inner_w}:{inner_h}:"
            f"force_original_aspect_ratio=decrease:force_divisible_by=2,"
            f"pad=iw+{border}:ih+{border}:{border // 2}:{border // 2}:color=black,"
            f"setsar=1[{node}fgo]",
            f"[{node}bgo][{node}fgo]overlay=(W-w)/2:(H-h)/2:format=auto"
            f"[{output_label}]",
        ]
    if composition == "layered":
        # a 2.39:1 band of the asset held inside a treated full-frame copy: the
        # answer to material whose own shape fullscreen would butcher
        band = _even(min(height - 4, width / 2.39))
        return [
            f"[{source_label}]split=2[{node}bg][{node}fg]",
            f"[{node}bg]{_treated_background(width, height, spec)}[{node}bgo]",
            f"[{node}fg]{_cover_chain(width, band, crop_bias)}[{node}fgo]",
            f"[{node}bgo][{node}fgo]overlay=0:(H-h)/2:format=auto[{output_label}]",
        ]
    # split - two regions of the same frame held against each other
    gap = _even(width * spec.split_gap_fraction)
    half = _even((width - gap) / 2)
    return [
        f"[{source_label}]split=2[{node}l][{node}r]",
        f"[{node}l]{_cover_chain(half, height, 'left')},"
        f"pad={width}:{height}:0:0:color=black[{node}base]",
        f"[{node}r]{_cover_chain(half, height, 'right')}[{node}ro]",
        f"[{node}base][{node}ro]overlay={width - half}:0:format=auto[{output_label}]",
    ]


def _escape_filter_path(path: Path) -> str:
    value = path.as_posix()
    for character in ("\\", "'", ":", ",", ";", "[", "]"):
        value = value.replace(character, f"\\{character}")
    return value


def extract_segment(
    source_path: str | Path,
    output_path: str | Path,
    *,
    start_seconds: float,
    end_seconds: float,
    timeout_seconds: float = 300,
    mode: str = "copy",
) -> SegmentArtifact:
    """Extract one time range into a new artifact without modifying the source."""

    source = Path(source_path).expanduser().resolve()
    output = Path(output_path).expanduser().resolve()
    start = _time(start_seconds, "start_seconds")
    end = _time(end_seconds, "end_seconds")
    timeout = _time(timeout_seconds, "timeout_seconds")
    if not isinstance(mode, str) or mode not in {"copy", "precise"}:
        raise FFmpegError("mode must be 'copy' or 'precise'")
    if end <= start:
        raise FFmpegError("end_seconds must be greater than start_seconds")
    if timeout == 0:
        raise FFmpegError("timeout_seconds must be greater than zero")
    if not source.exists():
        raise FFmpegError(f"source does not exist: {source}")
    if not source.is_file():
        raise FFmpegError(f"source is not a file: {source}")
    if os.path.normcase(str(source)) == os.path.normcase(str(output)):
        raise FFmpegError("output_path must not overwrite the source")
    if output.exists():
        raise FFmpegError(f"output already exists: {output}")
    if not output.suffix:
        raise FFmpegError("output_path must include a media file extension")
    if mode == "precise" and output.suffix.lower() != ".mp4":
        raise FFmpegError("precise mode requires an .mp4 output_path")

    try:
        executable = resolve_media_tool("ffmpeg", path_lookup=shutil.which)
    except ToolResolutionError as exc:
        raise FFmpegError(str(exc)) from exc
    if executable is None:
        raise FFmpegError("ffmpeg is not available locally or on PATH")

    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            prefix=f".{output.stem}-",
            suffix=output.suffix,
            dir=output.parent,
            delete=False,
        ) as reserved:
            temporary = Path(reserved.name)
    except OSError as exc:
        raise FFmpegError(f"could not prepare output path: {output}") from exc
    duration = end - start
    command = [
        executable,
        "-v",
        "error",
        "-nostdin",
        "-y",
    ]
    if mode == "copy":
        command.extend(
            [
                "-ss",
                format(start, ".15g"),
                "-i",
                str(source),
                "-t",
                format(duration, ".15g"),
                "-map",
                "0",
                "-c",
                "copy",
            ]
        )
    else:
        command.extend(
            [
                "-i",
                str(source),
                "-ss",
                format(start, ".15g"),
                "-t",
                format(duration, ".15g"),
                "-map",
                "0:v:0?",
                "-map",
                "0:a:0?",
                "-sn",
                "-dn",
                "-c:v",
                "libopenh264",
                "-b:v",
                "5M",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-movflags",
                "+faststart",
            ]
        )
    command.append(str(temporary))
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        _cleanup(temporary)
        raise FFmpegError(f"ffmpeg timed out while extracting: {source}") from exc
    except OSError as exc:
        _cleanup(temporary)
        raise FFmpegError(f"ffmpeg could not extract {source}: {type(exc).__name__}") from exc

    if completed.returncode != 0:
        _cleanup(temporary)
        detail = (completed.stderr or completed.stdout).strip()
        suffix = f": {detail}" if detail else ""
        raise FFmpegError(f"ffmpeg exited with {completed.returncode}{suffix}")
    if not temporary.is_file() or temporary.stat().st_size == 0:
        _cleanup(temporary)
        raise FFmpegError("ffmpeg reported success without creating a non-empty artifact")
    try:
        os.link(temporary, output)
    except OSError as exc:
        _cleanup(temporary)
        raise FFmpegError(f"could not publish output without overwriting: {output}") from exc
    _cleanup(temporary)
    try:
        size = output.stat().st_size
    except OSError as exc:
        raise FFmpegError(f"could not inspect published output: {output}") from exc
    return SegmentArtifact(str(source), str(output), start, end, size, mode)


def extract_audio(
    source_path: str | Path,
    output_path: str | Path,
    *,
    timeout_seconds: float = 300,
) -> AudioArtifact:
    """Extract the first audio stream as deterministic 48 kHz stereo PCM WAV."""

    source = Path(source_path).expanduser().resolve()
    output = Path(output_path).expanduser().resolve()
    timeout = _time(timeout_seconds, "timeout_seconds")
    if timeout == 0:
        raise FFmpegError("timeout_seconds must be greater than zero")
    if not source.exists():
        raise FFmpegError(f"source does not exist: {source}")
    if not source.is_file():
        raise FFmpegError(f"source is not a file: {source}")
    if os.path.normcase(str(source)) == os.path.normcase(str(output)):
        raise FFmpegError("output_path must not overwrite the source")
    if output.exists():
        raise FFmpegError(f"output already exists: {output}")
    if output.suffix.lower() != ".wav":
        raise FFmpegError("audio extraction requires a .wav output_path")

    try:
        executable = resolve_media_tool("ffmpeg", path_lookup=shutil.which)
    except ToolResolutionError as exc:
        raise FFmpegError(str(exc)) from exc
    if executable is None:
        raise FFmpegError("ffmpeg is not available locally or on PATH")

    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            prefix=f".{output.stem}-",
            suffix=output.suffix,
            dir=output.parent,
            delete=False,
        ) as reserved:
            temporary = Path(reserved.name)
    except OSError as exc:
        raise FFmpegError(f"could not prepare output path: {output}") from exc

    command = [
        executable,
        "-v",
        "error",
        "-nostdin",
        "-y",
        "-i",
        str(source),
        "-map",
        "0:a:0",
        "-vn",
        "-sn",
        "-dn",
        "-c:a",
        "pcm_s16le",
        "-ar",
        "48000",
        "-ac",
        "2",
        str(temporary),
    ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        _cleanup(temporary)
        raise FFmpegError(f"ffmpeg timed out while extracting audio: {source}") from exc
    except OSError as exc:
        _cleanup(temporary)
        raise FFmpegError(
            f"ffmpeg could not extract audio from {source}: {type(exc).__name__}"
        ) from exc

    if completed.returncode != 0:
        _cleanup(temporary)
        detail = (completed.stderr or completed.stdout).strip()
        suffix = f": {detail}" if detail else ""
        raise FFmpegError(f"ffmpeg exited with {completed.returncode}{suffix}")
    if not temporary.is_file() or temporary.stat().st_size == 0:
        _cleanup(temporary)
        raise FFmpegError("ffmpeg reported success without creating a non-empty audio artifact")
    try:
        os.link(temporary, output)
    except OSError as exc:
        _cleanup(temporary)
        raise FFmpegError(f"could not publish output without overwriting: {output}") from exc
    _cleanup(temporary)
    try:
        size = output.stat().st_size
    except OSError as exc:
        raise FFmpegError(f"could not inspect published output: {output}") from exc
    return AudioArtifact(str(source), str(output), 48000, 2, size)


MAX_JOINED_SEGMENTS = 400


def join_audio_segments(
    segments: Sequence[tuple[str | Path, float]],
    output_path: str | Path,
    *,
    lead_in_seconds: float = 0.0,
    timeout_seconds: float = 600,
) -> AudioArtifact:
    """Join narration segments into one 48 kHz stereo WAV with explicit pauses.

    ``segments`` is an ordered sequence of ``(wav_path, pause_after_seconds)``
    pairs: each file is decoded, padded with exactly its own silence, and the
    padded pieces are concatenated. ``lead_in_seconds`` prepends silence before
    the first word so the timeline can open on an image before the voice starts.

    The pauses are the whole point: a script synthesised as one block gets the
    engine's own uniform spacing, while joining per-unit renders lets the caller
    decide where the voice breathes. Every path is passed to FFmpeg as a
    structured argument; nothing is interpolated into a shell or a filter path.
    """

    items = list(segments)
    if not items:
        raise FFmpegError("joining audio requires at least one segment")
    if len(items) > MAX_JOINED_SEGMENTS:
        raise FFmpegError(f"at most {MAX_JOINED_SEGMENTS} segments can be joined")
    lead_in = _time(lead_in_seconds, "lead_in_seconds")
    timeout = _time(timeout_seconds, "timeout_seconds")
    if timeout == 0:
        raise FFmpegError("timeout_seconds must be greater than zero")

    output = Path(output_path).expanduser().resolve()
    if output.suffix.lower() != ".wav":
        raise FFmpegError("joining audio requires a .wav output_path")
    if output.exists():
        raise FFmpegError(f"output already exists: {output}")

    sources: list[Path] = []
    gaps: list[float] = []
    for index, item in enumerate(items):
        try:
            raw_path, raw_gap = item
        except (TypeError, ValueError) as exc:
            raise FFmpegError(
                f"segment {index} must be a (path, pause_after_seconds) pair"
            ) from exc
        source = Path(raw_path).expanduser().resolve()
        if not source.is_file():
            raise FFmpegError(f"segment {index} source does not exist: {source}")
        if os.path.normcase(str(source)) == os.path.normcase(str(output)):
            raise FFmpegError("output_path must not overwrite a segment source")
        gap = _time(raw_gap, f"segment {index} pause_after_seconds")
        if gap > 30:
            raise FFmpegError(f"segment {index} pause must be at most 30 seconds")
        sources.append(source)
        gaps.append(gap)

    try:
        executable = resolve_media_tool("ffmpeg", path_lookup=shutil.which)
    except ToolResolutionError as exc:
        raise FFmpegError(str(exc)) from exc
    if executable is None:
        raise FFmpegError("ffmpeg is not available locally or on PATH")

    steps: list[str] = []
    labels: list[str] = []
    for index, gap in enumerate(gaps):
        label = f"s{index}"
        chain = f"[{index}:a]aformat=sample_fmts=s16:sample_rates=48000:channel_layouts=stereo"
        if gap > 0:
            chain += f",apad=pad_dur={gap:.3f}"
        steps.append(f"{chain}[{label}]")
        labels.append(label)
    joined = "".join(f"[{label}]" for label in labels)
    steps.append(f"{joined}concat=n={len(labels)}:v=0:a=1[voice]")
    final_label = "voice"
    if lead_in > 0:
        delay_ms = int(round(lead_in * 1000))
        steps.append(f"[voice]adelay={delay_ms}:all=1[out]")
        final_label = "out"
    filter_complex = ";".join(steps)

    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            prefix=f".{output.stem}-join-",
            suffix=".wav",
            dir=output.parent,
            delete=False,
        ) as reserved:
            temporary = Path(reserved.name)
    except OSError as exc:
        raise FFmpegError(f"could not prepare output path: {output}") from exc

    command = [executable, "-v", "error", "-nostdin", "-y"]
    for source in sources:
        command.extend(["-i", str(source)])
    command.extend(
        [
            "-filter_complex",
            filter_complex,
            "-map",
            f"[{final_label}]",
            "-vn",
            "-sn",
            "-dn",
            "-c:a",
            "pcm_s16le",
            "-ar",
            "48000",
            "-ac",
            "2",
            str(temporary),
        ]
    )
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        _cleanup(temporary)
        raise FFmpegError("ffmpeg timed out while joining narration segments") from exc
    except OSError as exc:
        _cleanup(temporary)
        raise FFmpegError(
            f"ffmpeg could not join narration segments: {type(exc).__name__}"
        ) from exc

    if completed.returncode != 0:
        _cleanup(temporary)
        detail = (completed.stderr or completed.stdout).strip()
        suffix = f": {detail}" if detail else ""
        raise FFmpegError(f"ffmpeg exited with {completed.returncode}{suffix}")
    if not temporary.is_file() or temporary.stat().st_size == 0:
        _cleanup(temporary)
        raise FFmpegError("ffmpeg reported success without creating a non-empty audio artifact")
    try:
        os.link(temporary, output)
    except OSError as exc:
        _cleanup(temporary)
        raise FFmpegError(f"could not publish output without overwriting: {output}") from exc
    _cleanup(temporary)
    try:
        size = output.stat().st_size
    except OSError as exc:
        raise FFmpegError(f"could not inspect published output: {output}") from exc
    return AudioArtifact(str(sources[0]), str(output), 48000, 2, size)


# Defaults for :func:`detect_silences`. -35 dBFS sits below a synthesised voice
# but above the noise floor of a clean TTS render, and 0.12 s is long enough to
# skip the stops inside a word while still catching a sentence break.
SILENCE_NOISE_DB = -35.0
SILENCE_MIN_SECONDS = 0.12
_SILENCE_START = re.compile(r"silence_start:\s*(-?\d+(?:\.\d+)?)")
_SILENCE_END = re.compile(r"silence_end:\s*(-?\d+(?:\.\d+)?)")


def detect_silences(
    source_path: str | Path,
    *,
    noise_db: float = SILENCE_NOISE_DB,
    min_duration_seconds: float = SILENCE_MIN_SECONDS,
    timeout_seconds: float = 120,
) -> tuple[tuple[float, float], ...]:
    """Report the quiet spans of a local audio file, in seconds.

    A read-only ``silencedetect`` pass: nothing is decoded to disk and the
    source is never touched. Spans come back ordered and closed; a silence that
    runs to the end of the file has no ``silence_end`` and is dropped, since it
    marks where the audio stops rather than a pause inside it.
    """

    source = Path(source_path).expanduser().resolve()
    if not source.exists() or not source.is_file():
        raise FFmpegError(f"source does not exist or is not a file: {source}")
    if (
        isinstance(noise_db, bool)
        or not isinstance(noise_db, (int, float))
        or not math.isfinite(noise_db)
        or noise_db < -90
        or noise_db >= 0
    ):
        raise FFmpegError("noise_db must be a finite number from -90 to less than 0")
    minimum = _time(min_duration_seconds, "min_duration_seconds")
    if minimum == 0:
        raise FFmpegError("min_duration_seconds must be greater than zero")
    timeout = _time(timeout_seconds, "timeout_seconds")
    if timeout == 0:
        raise FFmpegError("timeout_seconds must be greater than zero")

    try:
        executable = resolve_media_tool("ffmpeg", path_lookup=shutil.which)
    except ToolResolutionError as exc:
        raise FFmpegError(str(exc)) from exc
    if executable is None:
        raise FFmpegError("ffmpeg is not available locally or on PATH")

    command = [
        executable,
        "-hide_banner",
        "-nostdin",
        "-i",
        str(source),
        "-map",
        "0:a:0",
        "-af",
        f"silencedetect=noise={format(float(noise_db), '.15g')}dB:"
        f"d={format(minimum, '.15g')}",
        "-f",
        "null",
        "-",
    ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise FFmpegError("ffmpeg timed out while detecting silences") from exc
    except OSError as exc:
        raise FFmpegError(f"ffmpeg could not detect silences: {type(exc).__name__}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        suffix = f": {detail}" if detail else ""
        raise FFmpegError(f"ffmpeg exited with {completed.returncode}{suffix}")

    spans: list[tuple[float, float]] = []
    pending: float | None = None
    for line in (completed.stderr or "").splitlines():
        started = _SILENCE_START.search(line)
        if started is not None:
            pending = max(float(started.group(1)), 0.0)
            continue
        ended = _SILENCE_END.search(line)
        if ended is not None and pending is not None:
            finish = float(ended.group(1))
            if finish > pending:
                spans.append((pending, finish))
            pending = None
    return tuple(spans)


def measure_luma(
    source_path: "str | Path",
    *,
    frames: int = 5,
    sample_interval_seconds: float = 1.0,
    timeout_seconds: float = 30,
) -> "float | None":
    """The mean brightness of a local image or video, in ``0.0..1.0``.

    Scaling to a single pixel with the ``area`` scaler *is* the average, so the
    measurement is one cheap decode and one byte per frame rather than a
    statistics filter. Visual Direction uses it to decide how hard the channel
    grade may push an asset: a photograph that is already black must not be
    crushed, and one that arrived bright has to be pulled into the same world.

    For a video the frames are taken ``sample_interval_seconds`` apart rather
    than consecutively: reading the first five frames measures 0.00 for any
    clip that opens on black, and grades it as if it were already dark. A
    still image has one frame, which the rate filter drops, so the second
    attempt below — the original single-pass measurement — is what answers for
    images. Two cheap decodes at worst, and never a wrong number.

    Returns ``None`` when FFmpeg is unavailable or the file cannot be read, so
    a missing measurement degrades the grade to its default instead of failing
    a plan.
    """

    source = Path(source_path).expanduser()
    if not source.is_file():
        return None
    if isinstance(frames, bool) or not isinstance(frames, int) or frames < 1:
        raise FFmpegError("frames must be a positive integer")
    interval = sample_interval_seconds
    if isinstance(interval, bool) or not isinstance(interval, (int, float)):
        raise FFmpegError("sample_interval_seconds must be a number")
    if interval <= 0:
        raise FFmpegError("sample_interval_seconds must be greater than zero")
    timeout = _time(timeout_seconds, "timeout_seconds")
    if timeout == 0:
        raise FFmpegError("timeout_seconds must be greater than zero")
    try:
        executable = resolve_media_tool("ffmpeg", path_lookup=shutil.which)
    except ToolResolutionError:
        return None
    if executable is None:
        return None
    spread = f"fps=1/{float(interval):g},scale=1:1:flags=area"
    for filter_chain in (spread, "scale=1:1:flags=area"):
        command = [
            executable, "-v", "error", "-nostdin",
            "-i", str(source.resolve()),
            "-frames:v", str(frames),
            "-vf", filter_chain,
            "-pix_fmt", "gray",
            "-f", "rawvideo",
            "-",
        ]
        try:
            completed = subprocess.run(
                command, check=False, capture_output=True, timeout=timeout, shell=False
            )
        except (subprocess.TimeoutExpired, OSError):
            return None
        if completed.returncode == 0 and completed.stdout:
            samples = completed.stdout
            return round(sum(samples) / (len(samples) * 255.0), 4)
    return None


def compose_video_sequence(
    clips: Sequence[SequenceClip],
    output_path: str | Path,
    *,
    narration_path: str | Path | None = None,
    narration_lead_in_seconds: float = 0.0,
    captions: Sequence[CaptionCue] = (),
    text_events: Sequence[TextEventCue] = (),
    motion_text: "Sequence[MotionTextCue]" = (),
    motion_overlay: "str | Path | None" = None,
    text_style: "TextStyleSpec | None" = None,
    direction: "DirectionSpec | None" = None,
    music_path: str | Path | None = None,
    music_gain_db: float | None = None,
    music_fade_in_seconds: float = 0.0,
    music_fade_out_seconds: float = 0.0,
    music_duck_db: float | None = None,
    narration_duration_seconds: float | None = None,
    video_fade_in_seconds: float = 0.0,
    video_fade_out_seconds: float = 0.0,
    canvas: tuple[int, int] | None = None,
    timeout_seconds: float = 300,
) -> SequenceArtifact:
    """Compose video clips and still images with optional captions, narration, and music.

    ``clips`` is an ordered timeline of ``SequenceClip`` (a trimmed range of a
    local video) and ``SequenceImage`` (a local still shown for a fixed
    duration). At least two segments and at least one ``SequenceClip`` are
    required. Video clips must already share pixel dimensions; when the timeline
    contains an image, ``canvas`` (the ``(width, height)`` of those clips) is
    required and every image is scaled to fit and letter-boxed onto it, then
    every segment is normalised to ``IMAGE_TIMELINE_FPS`` and ``yuv420p`` so the
    concat is deterministic.

    When any segment carries a ``fit`` (``"contain"`` or ``"cover"``) the
    timeline is an explicit target format: ``canvas`` is the delivery
    resolution, sources of different sizes and aspect ratios are accepted, and
    every segment is deterministically scaled to the canvas (``contain``
    letterboxes, ``cover`` centre-crops) then pinned to ``IMAGE_TIMELINE_FPS``,
    ``setsar=1`` and ``yuv420p``. With no ``fit`` on any segment the legacy
    filter graph is emitted unchanged.

    A segment may additionally carry a Visual Direction: a ``composition``
    (how much of the frame the asset occupies and what surrounds it), a
    ``crop_bias``, a ``text_zone`` for the scrim under on-screen type, and a
    ``grade`` intensity naming how hard the channel's colour treatment is
    applied to this asset. ``direction`` supplies the numbers behind those
    names; a segment that states none of them is composed exactly as before.

    ``narration_lead_in_seconds`` delays the voice so the timeline can open on
    picture and music alone; it requires ``narration_path`` and must be shorter
    than the timeline.

    ``music_duck_db`` attenuates the bed by exactly that many decibels while the
    voice runs, ramping over ``MUSIC_DUCK_RAMP_SECONDS`` at each edge; it
    requires both a ``music_path`` and a ``narration_path``. The ducked span
    starts at the lead-in and ends after ``narration_duration_seconds`` (the
    voice track's own length), so the bed comes back up for the tail; without
    that length the duck holds to the end of the timeline.
    """

    if isinstance(clips, (str, bytes)) or not isinstance(clips, Sequence):
        raise FFmpegError("clips must be a sequence of SequenceClip or SequenceImage values")
    normalized_clips = tuple(clips)
    if len(normalized_clips) < 2:
        raise FFmpegError("video sequence requires at least two timeline segments")
    if not all(isinstance(clip, (SequenceClip, SequenceImage)) for clip in normalized_clips):
        raise FFmpegError("clips must contain only SequenceClip or SequenceImage values")
    if (
        not any(isinstance(clip, SequenceClip) for clip in normalized_clips)
        and canvas is None
    ):
        raise FFmpegError(
            "an all-image video sequence requires a (width, height) canvas"
        )
    if isinstance(captions, (str, bytes)) or not isinstance(captions, Sequence):
        raise FFmpegError("captions must be a sequence of CaptionCue values")
    normalized_captions = tuple(captions)
    if len(normalized_captions) > 500:
        raise FFmpegError("video sequence accepts at most 500 caption cues")
    if not all(isinstance(cue, CaptionCue) for cue in normalized_captions):
        raise FFmpegError("captions must contain only CaptionCue values")

    output = Path(output_path).expanduser().resolve()
    timeout = _time(timeout_seconds, "timeout_seconds")
    if timeout == 0:
        raise FFmpegError("timeout_seconds must be greater than zero")
    if output.suffix.lower() != ".mp4":
        raise FFmpegError("video sequence requires an .mp4 output_path")

    direction_spec = direction if direction is not None else DEFAULT_DIRECTION_SPEC
    if direction is not None and not isinstance(direction, DirectionSpec):
        raise FFmpegError("direction must be a DirectionSpec")
    resolved_clips: list[tuple[str, Path, float, float]] = []
    resolved_fits: list[str | None] = []
    resolved_motions: list[str | None] = []
    resolved_compositions: list[str | None] = []
    resolved_biases: list[str] = []
    resolved_zones: list[str | None] = []
    resolved_grades: list[str | None] = []
    for clip in normalized_clips:
        source = Path(clip.source_path).expanduser().resolve()
        motion: str | None = None
        if isinstance(clip, SequenceImage):
            span = _time(clip.duration_seconds, "image duration_seconds")
            if span == 0:
                raise FFmpegError("image duration_seconds must be greater than zero")
            kind, start, end = "image", 0.0, span
            motion = clip.motion
            if motion is not None and motion not in KEN_BURNS_MOTIONS + DIRECTION_MOTIONS:
                raise FFmpegError(
                    "motion must be one of "
                    + ", ".join(KEN_BURNS_MOTIONS + DIRECTION_MOTIONS)
                )
        else:
            start = _time(clip.start_seconds, "start_seconds")
            end = _time(clip.end_seconds, "end_seconds")
            if end <= start:
                raise FFmpegError("end_seconds must be greater than start_seconds")
            kind = "clip"
        fit = clip.fit
        if fit is not None and fit not in ("contain", "cover"):
            raise FFmpegError('fit must be "contain" or "cover"')
        composition = getattr(clip, "composition", None)
        if composition is not None and composition not in COMPOSITIONS:
            raise FFmpegError("composition must be one of " + ", ".join(COMPOSITIONS))
        bias = getattr(clip, "crop_bias", "center") or "center"
        if bias not in CROP_BIASES:
            raise FFmpegError("crop_bias must be one of " + ", ".join(CROP_BIASES))
        zone = getattr(clip, "text_zone", None)
        if zone is not None and zone not in TEXT_ZONES:
            raise FFmpegError("text_zone must be one of " + ", ".join(TEXT_ZONES))
        if composition == "text_focus" and zone is None:
            raise FFmpegError("a text_focus composition requires a text_zone")
        grade = getattr(clip, "grade", None)
        if grade is not None and grade not in direction_spec.intensities:
            raise FFmpegError("grade must be one of " + ", ".join(GRADE_INTENSITIES))
        if kind == "clip" and composition in ("inset", "layered", "split"):
            raise FFmpegError(
                f"composition {composition} is only available for a still image"
            )
        if not source.exists() or not source.is_file():
            raise FFmpegError(f"source does not exist or is not a file: {source}")
        if os.path.normcase(str(source)) == os.path.normcase(str(output)):
            raise FFmpegError("output_path must not overwrite a source")
        resolved_clips.append((kind, source, start, end))
        resolved_fits.append(fit)
        resolved_motions.append(motion)
        resolved_compositions.append(composition)
        resolved_biases.append(bias)
        resolved_zones.append(zone)
        resolved_grades.append(grade)
    duration = sum(end - start for _, _, start, end in resolved_clips)
    has_images = any(kind == "image" for kind, _, _, _ in resolved_clips)
    # An explicit target format: every segment is deterministically scaled to
    # the canvas (contain/cover), so heterogeneous sources can share the concat.
    normalize_to_canvas = any(fit is not None for fit in resolved_fits)
    canvas_size: tuple[int, int] | None = None
    if canvas is not None:
        if (
            not isinstance(canvas, tuple)
            or len(canvas) != 2
            or any(
                isinstance(value, bool) or not isinstance(value, int) or value <= 0
                for value in canvas
            )
        ):
            raise FFmpegError("canvas must be a positive (width, height) tuple")
        canvas_size = (int(canvas[0]), int(canvas[1]))
    if has_images and canvas_size is None:
        raise FFmpegError("a timeline with images requires a positive (width, height) canvas")
    if normalize_to_canvas and canvas_size is None:
        raise FFmpegError("a segment fit requires a positive (width, height) canvas")
    # A direction is expressed in real pixels (a crop window, a scrim band, an
    # inset), so it can only be composed against a known delivery canvas.
    directed_indices = [
        index
        for index in range(len(resolved_clips))
        if resolved_compositions[index] is not None
        or resolved_grades[index] is not None
        or resolved_motions[index] in DIRECTION_MOTIONS
    ]
    if directed_indices and canvas_size is None:
        raise FFmpegError(
            "a segment visual direction requires a positive (width, height) canvas"
        )
    resolved_captions: list[tuple[str, float, float]] = []
    previous_end = 0.0
    for cue in normalized_captions:
        if not isinstance(cue.text, str) or not cue.text.strip():
            raise FFmpegError("caption text must be a non-empty string")
        text = cue.text.strip()
        if len(text) > 160:
            raise FFmpegError("caption text must contain at most 160 characters")
        if any(ord(character) < 32 for character in text):
            raise FFmpegError("caption text must not contain control characters")
        if any(character in text for character in "<>{}"):
            raise FFmpegError("caption text must not contain subtitle markup characters")
        start = _time(cue.start_seconds, "caption start_seconds")
        end = _time(cue.end_seconds, "caption end_seconds")
        if end <= start or round(end * 1000) <= round(start * 1000):
            raise FFmpegError("caption end_seconds must be at least 1 ms after start_seconds")
        if start < previous_end:
            raise FFmpegError("caption cues must be ordered and non-overlapping")
        if end > duration:
            raise FFmpegError("caption end_seconds must not exceed the sequence duration")
        resolved_captions.append((text, start, end))
        previous_end = end
    if isinstance(text_events, (str, bytes)) or not isinstance(text_events, Sequence):
        raise FFmpegError("text_events must be a sequence of TextEventCue values")
    normalized_events = tuple(text_events)
    if len(normalized_events) > 200:
        raise FFmpegError("video sequence accepts at most 200 text events")
    if not all(isinstance(cue, TextEventCue) for cue in normalized_events):
        raise FFmpegError("text_events must contain only TextEventCue values")
    resolved_events: list[tuple[TextEventCue, float, float]] = []
    for cue in normalized_events:
        if not isinstance(cue.text, str) or not cue.text.strip():
            raise FFmpegError("text event text must be a non-empty string")
        text = cue.text.strip()
        if len(text) > 48:
            raise FFmpegError("text event text must contain at most 48 characters")
        if any(ord(character) < 32 for character in text):
            raise FFmpegError("text event text must not contain control characters")
        if any(character in text for character in "<>{}"):
            # the same rule the captions obey: emphasis text is data and must
            # never be able to become ASS override syntax
            raise FFmpegError("text event text must not contain subtitle markup characters")
        if cue.position not in TEXT_EVENT_POSITIONS:
            raise FFmpegError(f"text event position must be one of {TEXT_EVENT_POSITIONS}")
        if cue.animation not in TEXT_EVENT_ANIMATIONS:
            raise FFmpegError(f"text event animation must be one of {TEXT_EVENT_ANIMATIONS}")
        start = _time(cue.start_seconds, "text event start_seconds")
        end = _time(cue.end_seconds, "text event end_seconds")
        if end <= start or round(end * 1000) <= round(start * 1000):
            raise FFmpegError("text event end_seconds must be at least 1 ms after start_seconds")
        if end > duration:
            raise FFmpegError("text event end_seconds must not exceed the sequence duration")
        if cue.highlight is not None:
            span = tuple(cue.highlight)
            if (
                len(span) != 2
                or any(isinstance(v, bool) or not isinstance(v, int) for v in span)
                or not 0 <= span[0] < span[1] <= len(text)
            ):
                raise FFmpegError("text event highlight must be a span inside the text")
        resolved_events.append((cue, start, end))
    # Text events may overlap each other and the captions by design, but two
    # emphases on screen at once is noise, so they are ordered and disjoint.
    for (_a, _s1, e1), (_b, s2, _e2) in zip(resolved_events, resolved_events[1:]):
        if s2 < e1:
            raise FFmpegError("text events must be ordered and non-overlapping")
    if resolved_captions and canvas_size is None:
        raise FFmpegError("captions require a (width, height) canvas for pixel-accurate layout")
    if resolved_events and canvas_size is None:
        raise FFmpegError(
            "text events require a (width, height) canvas for pixel-accurate layout"
        )
    if isinstance(motion_text, (str, bytes)) or not isinstance(motion_text, Sequence):
        raise FFmpegError("motion_text must be a sequence of MotionTextCue values")
    normalized_motion = tuple(motion_text)
    if len(normalized_motion) > 60:
        raise FFmpegError("video sequence accepts at most 60 motion text events")
    if not all(isinstance(cue, MotionTextCue) for cue in normalized_motion):
        raise FFmpegError("motion_text must contain only MotionTextCue values")
    overlay_path: Path | None = None
    if motion_overlay is not None:
        if normalized_motion:
            raise FFmpegError(
                "motion_overlay and motion_text are mutually exclusive: the "
                "typographic layer is either composed by Remotion or drawn by libass"
            )
        overlay_path = Path(motion_overlay).expanduser().resolve()
        if not overlay_path.is_file():
            raise FFmpegError(f"motion_overlay file not found: {overlay_path}")
        if overlay_path.suffix.lower() not in (".mov", ".webm", ".mkv", ".mp4"):
            raise FFmpegError(
                "motion_overlay must be a .mov/.webm/.mkv/.mp4 clip with an alpha channel"
            )
        if canvas_size is None:
            raise FFmpegError(
                "motion_overlay requires a (width, height) canvas so the overlay "
                "can be pinned to the delivery resolution"
            )
    resolved_motion: list[tuple[MotionTextCue, float, float]] = []
    for cue in normalized_motion:
        if cue.layout not in MOTION_TEXT_LAYOUTS:
            raise FFmpegError(f"motion text layout must be one of {MOTION_TEXT_LAYOUTS}")
        if cue.motion not in MOTION_TEXT_MOTIONS:
            raise FFmpegError(f"motion text motion must be one of {MOTION_TEXT_MOTIONS}")
        blocks = tuple(cue.blocks)
        if not blocks or len(blocks) > 3:
            raise FFmpegError("a motion text event holds between one and three blocks")
        if not all(isinstance(block, MotionTextBlockCue) for block in blocks):
            raise FFmpegError("motion text blocks must be MotionTextBlockCue values")
        for block in blocks:
            if not isinstance(block.text, str) or not block.text.strip():
                raise FFmpegError("motion text block text must be a non-empty string")
            text = block.text.strip()
            if len(text) > 40:
                raise FFmpegError("motion text block must contain at most 40 characters")
            if any(ord(character) < 32 for character in text):
                raise FFmpegError("motion text must not contain control characters")
            if any(character in text for character in "<>{}"):
                # the rule every text layer here obeys: copy is data and must
                # never be able to become ASS override syntax
                raise FFmpegError(
                    "motion text must not contain subtitle markup characters"
                )
            if block.weight not in MOTION_TEXT_WEIGHTS:
                raise FFmpegError(f"motion text weight must be one of {MOTION_TEXT_WEIGHTS}")
            if not isinstance(block.accent, bool):
                raise FFmpegError("motion text accent must be a boolean")
        start = _time(cue.start_seconds, "motion text start_seconds")
        end = _time(cue.end_seconds, "motion text end_seconds")
        if end - start < 0.2:
            raise FFmpegError("a motion text event must last at least 200 ms")
        if end > duration:
            raise FFmpegError(
                "motion text end_seconds must not exceed the sequence duration"
            )
        resolved_motion.append((cue, start, end))
    # Two compositions on screen at once is not a hierarchy, it is a collision.
    for (_a, _s1, e1), (_b, s2, _e2) in zip(resolved_motion, resolved_motion[1:]):
        if s2 < e1:
            raise FFmpegError("motion text events must be ordered and non-overlapping")
    if resolved_motion and canvas_size is None:
        raise FFmpegError(
            "motion text requires a (width, height) canvas for pixel-accurate layout"
        )
    narration: Path | None = None
    narration_lead_in = _time(narration_lead_in_seconds, "narration_lead_in_seconds")
    if narration_path is None:
        if narration_lead_in:
            raise FFmpegError("narration_lead_in_seconds requires a narration_path")
    else:
        narration = Path(narration_path).expanduser().resolve()
        if not narration.exists() or not narration.is_file():
            raise FFmpegError(f"narration does not exist or is not a file: {narration}")
        if os.path.normcase(str(narration)) == os.path.normcase(str(output)):
            raise FFmpegError("output_path must not overwrite the narration source")
        if narration_lead_in >= duration:
            raise FFmpegError("narration lead-in must be shorter than the sequence duration")
    music: Path | None = None
    gain: float | None = None
    fade_in = _time(music_fade_in_seconds, "music_fade_in_seconds")
    fade_out = _time(music_fade_out_seconds, "music_fade_out_seconds")
    if music_path is None:
        if music_gain_db is not None:
            raise FFmpegError("music_gain_db requires a music_path")
        if fade_in or fade_out:
            raise FFmpegError("music fades require a music_path")
    else:
        music = Path(music_path).expanduser().resolve()
        if not music.exists() or not music.is_file():
            raise FFmpegError(f"music does not exist or is not a file: {music}")
        if os.path.normcase(str(music)) == os.path.normcase(str(output)):
            raise FFmpegError("output_path must not overwrite the music source")
        if (
            isinstance(music_gain_db, bool)
            or not isinstance(music_gain_db, (int, float))
            or not math.isfinite(music_gain_db)
            or music_gain_db < -60
            or music_gain_db > 0
        ):
            raise FFmpegError("music_gain_db must be a finite number from -60 to 0")
        gain = float(music_gain_db)
        if fade_in + fade_out > duration:
            raise FFmpegError("music fades must not exceed the sequence duration")
    duck: float | None = None
    duck_start = 0.0
    duck_end = duration
    if music_duck_db is None:
        if narration_duration_seconds is not None:
            raise FFmpegError("narration_duration_seconds requires a music_duck_db")
    else:
        if music is None or narration is None:
            raise FFmpegError("music_duck_db requires both a music_path and a narration_path")
        if (
            isinstance(music_duck_db, bool)
            or not isinstance(music_duck_db, (int, float))
            or not math.isfinite(music_duck_db)
            or music_duck_db < -60
            or music_duck_db >= 0
        ):
            raise FFmpegError("music_duck_db must be a finite number from -60 to less than 0")
        duck = float(music_duck_db)
        duck_start = narration_lead_in
        if narration_duration_seconds is not None:
            voice = _time(narration_duration_seconds, "narration_duration_seconds")
            if voice == 0:
                raise FFmpegError("narration_duration_seconds must be greater than zero")
            # a synthesised voice may run a few milliseconds past the timeline;
            # the duck then simply never releases.
            duck_end = min(narration_lead_in + voice, duration)
    video_fade_in = _time(video_fade_in_seconds, "video_fade_in_seconds")
    video_fade_out = _time(video_fade_out_seconds, "video_fade_out_seconds")
    if video_fade_in + video_fade_out > duration:
        raise FFmpegError("video fades must not exceed the sequence duration")
    if output.exists():
        raise FFmpegError(f"output already exists: {output}")

    try:
        executable = resolve_media_tool("ffmpeg", path_lookup=shutil.which)
    except ToolResolutionError as exc:
        raise FFmpegError(str(exc)) from exc
    if executable is None:
        raise FFmpegError("ffmpeg is not available locally or on PATH")

    temporary: Path | None = None
    caption_file: Path | None = None
    filter_file: Path | None = None
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            prefix=f".{output.stem}-",
            suffix=output.suffix,
            dir=output.parent,
            delete=False,
        ) as reserved:
            temporary = Path(reserved.name)
        if resolved_captions or resolved_events or resolved_motion:
            caption_width, caption_height = canvas_size  # type: ignore[misc]
            style = text_style or TextStyleSpec()
            for name in (style.font_name, style.support_font_name or style.font_name):
                if not _FONT_NAME.match(name):
                    raise FFmpegError(f"font name is not a plain family name: {name}")
            header = _caption_ass_header(caption_width, caption_height)
            extra_styles = ""
            if resolved_events:
                extra_styles += _emphasis_styles(caption_width, caption_height, style)
            if resolved_motion:
                extra_styles += _motion_style(caption_width, caption_height, style)
            if extra_styles:
                # the extra styles belong in the [V4+ Styles] block, which ends
                # where the [Events] block begins
                header = header.replace(
                    "\n\n[Events]\n", "\n" + extra_styles + "\n[Events]\n", 1
                )
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                prefix=f".{output.stem}-captions-",
                suffix=".ass",
                dir=output.parent,
                delete=False,
            ) as caption_stream:
                caption_file = Path(caption_stream.name)
                caption_stream.write(header)
                for text, start, end in resolved_captions:
                    caption_stream.write(
                        f"Dialogue: 0,{_ass_timestamp(start)},{_ass_timestamp(end)},"
                        f"Caption,,0,0,0,,{text}\n"
                    )
                for cue, start, end in resolved_events:
                    override = _event_override(cue, caption_width, caption_height, style)
                    name = "Emphasis" if cue.emphasis else "Keyword"
                    # layer 1: emphasis draws over a caption when they coincide
                    caption_stream.write(
                        f"Dialogue: 1,{_ass_timestamp(start)},{_ass_timestamp(end)},"
                        f"{name},,0,0,0,,{override}{_event_text(cue, style)}\n"
                    )
                for cue, start, end in resolved_motion:
                    for line in _motion_dialogues(
                        cue, start, end, caption_width, caption_height, style
                    ):
                        caption_stream.write(line + "\n")
    except OSError as exc:
        if temporary is not None:
            _cleanup(temporary)
        if caption_file is not None:
            _cleanup(caption_file)
        raise FFmpegError(f"could not prepare output path: {output}") from exc

    command = [executable, "-v", "error", "-nostdin", "-y"]
    for kind, source, start, end in resolved_clips:
        if kind == "image":
            command.extend(
                ["-loop", "1", "-t", format(end - start, ".15g"), "-i", str(source)]
            )
        else:
            command.extend(["-i", str(source)])
    if narration is not None:
        command.extend(["-i", str(narration)])
    if music is not None:
        command.extend(["-stream_loop", "-1", "-i", str(music)])
    overlay_index = (
        len(resolved_clips)
        + (1 if narration is not None else 0)
        + (1 if music is not None else 0)
        if overlay_path is not None
        else None
    )
    if overlay_path is not None:
        # last input, so the narration/music stream indices below are unaffected
        command.extend(["-i", str(overlay_path)])
    filters = []
    labels = []
    for index, (kind, _, start, end) in enumerate(resolved_clips):
        label = f"v{index}"

        def _motion_chain(width: int, height: int, _motion=resolved_motions[index],
                          _span=end - start) -> str:
            """The optional Ken Burns ``zoompan`` pass for this still, or ``""``."""
            if _motion is None:
                return ""
            frames = round(_span * IMAGE_TIMELINE_FPS)
            return "," + _ken_burns_filter(_motion, width, height, frames)

        if index in directed_indices:
            width, height = canvas_size  # type: ignore[misc]
            composition = resolved_compositions[index] or "fullscreen"
            grade_name = resolved_grades[index]
            fit = resolved_fits[index] or "contain"
            node = f"d{index}"
            span = end - start
            statements: list[str] = []
            if kind == "image":
                entry = f"{index}:v:0"
            else:
                entry = f"{node}t"
                statements.append(
                    f"[{index}:v:0]trim=start={format(start, '.15g')}:"
                    f"end={format(end, '.15g')},setpts=PTS-STARTPTS[{entry}]"
                )
            statements.extend(
                _composition_graph(
                    entry,
                    f"{node}c",
                    composition=composition,
                    fit=fit,
                    crop_bias=resolved_biases[index],
                    text_zone=resolved_zones[index],
                    width=width,
                    height=height,
                    spec=direction_spec,
                    node=node,
                )
            )
            tail = [f"fps={IMAGE_TIMELINE_FPS}"]
            motion_value = resolved_motions[index]
            if motion_value in DIRECTION_MOTIONS:
                move = _direction_motion_filter(
                    motion_value,
                    resolved_biases[index],
                    width,
                    height,
                    round(span * IMAGE_TIMELINE_FPS),
                    direction_spec,
                )
                if move:
                    tail.append(move)
            elif motion_value in KEN_BURNS_MOTIONS:
                tail.append(
                    _ken_burns_filter(
                        motion_value, width, height, round(span * IMAGE_TIMELINE_FPS)
                    )
                )
            treatment = _grade_chain(grade_name, direction_spec)
            if treatment:
                tail.append(treatment)
            tail.append("format=yuv420p")
            if kind == "image":
                tail.append(f"trim=duration={format(span, '.15g')}")
                tail.append("setpts=PTS-STARTPTS")
            statements.append(f"[{node}c]{','.join(tail)}[{label}]")
            filters.extend(statements)
        elif normalize_to_canvas:
            width, height = canvas_size  # type: ignore[misc]
            # An explicit target format falls back to contain for any segment
            # that did not state a fit, so a mixed timeline still normalises.
            fit = resolved_fits[index] or "contain"
            scale_chain = _fit_filter(fit, width, height)
            if kind == "image":
                filters.append(
                    f"[{index}:v:0]{scale_chain},"
                    f"fps={IMAGE_TIMELINE_FPS}{_motion_chain(width, height)},format=yuv420p,"
                    f"trim=duration={format(end - start, '.15g')},"
                    f"setpts=PTS-STARTPTS[{label}]"
                )
            else:
                filters.append(
                    f"[{index}:v:0]trim=start={format(start, '.15g')}:"
                    f"end={format(end, '.15g')},setpts=PTS-STARTPTS,"
                    f"{scale_chain},fps={IMAGE_TIMELINE_FPS},format=yuv420p[{label}]"
                )
        elif kind == "image":
            width, height = canvas_size  # type: ignore[misc]
            filters.append(
                f"[{index}:v:0]scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
                f"fps={IMAGE_TIMELINE_FPS},setsar=1{_motion_chain(width, height)},format=yuv420p,"
                f"trim=duration={format(end - start, '.15g')},setpts=PTS-STARTPTS[{label}]"
            )
        else:
            chain = (
                f"[{index}:v:0]trim=start={format(start, '.15g')}:end={format(end, '.15g')},"
                "setpts=PTS-STARTPTS"
            )
            if has_images:
                chain += f",fps={IMAGE_TIMELINE_FPS},setsar=1,format=yuv420p"
            filters.append(f"{chain}[{label}]")
        labels.append(f"[{label}]")
    has_video_fades = video_fade_in > 0 or video_fade_out > 0
    # The picture passes through up to three optional stages after the concat —
    # libass text, the Remotion motion-graphics overlay, then the black fades —
    # and whichever one runs last must be the node named [outv].
    stages_remaining = (
        (1 if caption_file is not None else 0)
        + (1 if overlay_index is not None else 0)
        + (1 if has_video_fades else 0)
    )
    concat_label = "outv" if stages_remaining == 0 else "basev"
    filters.append(
        f"{''.join(labels)}concat=n={len(labels)}:v=1:a=0[{concat_label}]"
    )
    current_video = concat_label
    stage = 0

    def _next_label(preferred: str) -> str:
        nonlocal stage
        stage += 1
        return "outv" if stage == stages_remaining else preferred

    if caption_file is not None:
        caption_path = _escape_filter_path(caption_file)
        # The .ass script already carries the style and a frame-matched PlayRes,
        # so libass lays the text out in real pixels with no force_style guesswork.
        nxt = _next_label("capv")
        filters.append(
            f"[{current_video}]subtitles=filename='{caption_path}'[{nxt}]"
        )
        current_video = nxt
    if overlay_index is not None:
        # The typographic layer, composed by Remotion as a transparent clip the
        # size of the canvas. It is pinned to the delivery resolution and to the
        # timeline fps first; ``format=auto`` then keeps the overlay's own alpha,
        # so the base picture is untouched wherever the overlay is clear.
        ov_w, ov_h = canvas_size  # type: ignore[misc]
        filters.append(
            f"[{overlay_index}:v:0]scale={ov_w}:{ov_h},setsar=1,"
            f"fps={IMAGE_TIMELINE_FPS},format=yuva420p[ovl]"
        )
        nxt = _next_label("mgv")
        filters.append(
            f"[{current_video}][ovl]overlay=format=auto:eof_action=pass[{nxt}]"
        )
        current_video = nxt
    if has_video_fades:
        # A gentle open from black and close to black over the whole picture,
        # text included. loudnorm already gives the audio its own fades.
        fade_parts = []
        if video_fade_in > 0:
            fade_parts.append(f"fade=t=in:st=0:d={format(video_fade_in, '.15g')}")
        if video_fade_out > 0:
            fade_parts.append(
                f"fade=t=out:st={format(duration - video_fade_out, '.15g')}:"
                f"d={format(video_fade_out, '.15g')}"
            )
        nxt = _next_label("fadev")
        filters.append(f"[{current_video}]{','.join(fade_parts)}[{nxt}]")
        current_video = nxt
    narration_index = len(resolved_clips) if narration is not None else None
    music_index = len(resolved_clips) + (1 if narration is not None else 0) if music is not None else None
    if narration_index is not None:
        # A lead-in holds the voice back so the timeline can open on atmosphere
        # (and the music bed) alone; apad/atrim still fill out to the timeline.
        delay = (
            f"adelay={round(narration_lead_in * 1000)}:all=1," if narration_lead_in else ""
        )
        filters.append(
            f"[{narration_index}:a:0]aresample=48000,"
            f"aformat=sample_fmts=fltp:channel_layouts=stereo,{delay}apad,"
            f"atrim=duration={format(duration, '.15g')},asetpts=PTS-STARTPTS[voice]"
        )
    if music_index is not None and gain is not None:
        bed = (
            f"[{music_index}:a:0]aresample=48000,"
            "aformat=sample_fmts=fltp:channel_layouts=stereo,"
            f"volume={format(gain, '.15g')}dB"
        )
        if fade_in > 0:
            bed += f",afade=t=in:st=0:d={format(fade_in, '.15g')}"
        if fade_out > 0:
            bed += (
                f",afade=t=out:st={format(duration - fade_out, '.15g')}:"
                f"d={format(fade_out, '.15g')}"
            )
        if duck is not None:
            # An exact, plan-derived envelope instead of a signal-driven
            # sidechain: the dip is always the stated number of decibels, however
            # loud the voice source happens to be, and it stays reproducible from
            # the plan alone. The attack ramp finishes as the voice starts and
            # the release ramp begins when the voice track ends.
            level = format(10 ** (duck / 20), ".15g")
            ramp = format(MUSIC_DUCK_RAMP_SECONDS, ".15g")
            attack = format(duck_start - MUSIC_DUCK_RAMP_SECONDS, ".15g")
            release = format(duck_end + MUSIC_DUCK_RAMP_SECONDS, ".15g")
            bed += (
                f",volume=volume='1-(1-{level})"
                f"*clip((t-({attack}))/{ramp},0,1)"
                f"*clip(({release}-t)/{ramp},0,1)':eval=frame"
            )
        bed += f",atrim=duration={format(duration, '.15g')},asetpts=PTS-STARTPTS[bed]"
        filters.append(bed)
    # Master chain shared by every audio branch: a short fade-in kills any start
    # click, then EBU R128 loudness normalisation brings the mix to a comfortable
    # online-publishing target (-14 LUFS, true peak -1.5 dBTP) with no clipping.
    # loudnorm resamples internally, so pin 48 kHz again afterwards.
    master = "" if duration <= 1.0 else "afade=t=in:st=0:d=0.3,"
    master += "loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000"
    if narration_index is not None and music_index is not None:
        filters.append(
            "[voice][bed]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,"
            f"alimiter=limit=0.95:latency=1,{master}[outa]"
        )
    elif narration_index is not None:
        filters.append(f"[voice]{master}[outa]")
    elif music_index is not None:
        filters.append(f"[bed]alimiter=limit=0.95:latency=1,{master}[outa]")
    filter_graph = ";".join(filters)
    # A directed timeline builds several filter nodes per segment, and sixty of
    # those overflow the operating system's command-line limit (which surfaces
    # as a bare FileNotFoundError from CreateProcess, not as an FFmpeg error).
    # Past a conservative threshold the graph travels in a file instead.
    if len(filter_graph) > _FILTER_GRAPH_INLINE_LIMIT:
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                prefix=f".{output.stem}-filter-",
                suffix=".txt",
                dir=output.parent,
                delete=False,
            ) as handle:
                filter_file = Path(handle.name)
                handle.write(filter_graph)
        except OSError as exc:
            _cleanup(temporary)
            if caption_file is not None:
                _cleanup(caption_file)
            raise FFmpegError(
                f"could not write the filter graph beside: {output}"
            ) from exc
        command.extend(["-filter_complex_script", str(filter_file)])
    else:
        command.extend(["-filter_complex", filter_graph])
    command.extend(["-map", "[outv]"])
    if narration is None and music is None:
        command.append("-an")
    else:
        command.extend(["-map", "[outa]", "-c:a", "aac", "-b:a", "192k"])
    command.extend(
        [
            "-sn",
            "-dn",
            "-c:v",
            "libopenh264",
            # libopenh264 has no CRF mode; a generous 720p bitrate keeps smooth
            # dark gradients from blocking after the clip -> timeline re-encode.
            "-b:v",
            "10M",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(temporary),
        ]
    )
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        _cleanup(temporary)
        raise FFmpegError("ffmpeg timed out while composing the video sequence") from exc
    except OSError as exc:
        _cleanup(temporary)
        raise FFmpegError(f"ffmpeg could not compose the video sequence: {type(exc).__name__}") from exc
    finally:
        if caption_file is not None:
            _cleanup(caption_file)
        if filter_file is not None:
            _cleanup(filter_file)

    if completed.returncode != 0:
        _cleanup(temporary)
        detail = (completed.stderr or completed.stdout).strip()
        suffix = f": {detail}" if detail else ""
        raise FFmpegError(f"ffmpeg exited with {completed.returncode}{suffix}")
    if not temporary.is_file() or temporary.stat().st_size == 0:
        _cleanup(temporary)
        raise FFmpegError("ffmpeg reported success without creating a non-empty sequence")
    try:
        os.link(temporary, output)
    except OSError as exc:
        _cleanup(temporary)
        raise FFmpegError(f"could not publish output without overwriting: {output}") from exc
    _cleanup(temporary)
    try:
        size = output.stat().st_size
    except OSError as exc:
        raise FFmpegError(f"could not inspect published output: {output}") from exc
    return SequenceArtifact(
        source_paths=tuple(str(source) for _, source, _, _ in resolved_clips),
        output_path=str(output),
        duration_seconds=duration,
        file_size_bytes=size,
        narration_source_path=str(narration) if narration is not None else None,
        caption_count=len(resolved_captions),
        text_event_count=len(resolved_events),
        motion_text_count=len(resolved_motion),
        music_source_path=str(music) if music is not None else None,
        music_gain_db=gain,
        image_count=sum(1 for kind, _, _, _ in resolved_clips if kind == "image"),
        music_fade_in_seconds=fade_in if music is not None else 0.0,
        music_fade_out_seconds=fade_out if music is not None else 0.0,
        video_fade_in_seconds=video_fade_in,
        video_fade_out_seconds=video_fade_out,
        narration_lead_in_seconds=narration_lead_in,
        music_duck_db=duck,
        directed_segment_count=len(directed_indices),
    )


# Audio-only micro-fade at each internal join, long enough to kill a click and
# short enough to be inaudible. No video fade -- the picture cut is hard.
_CUT_JOIN_FADE_SECONDS = 0.010

# The aside marker: a small, single-line, deliberately informal tag -- not a
# caption. A real system font (not fontconfig-dependent) keeps this working on
# the same Windows box every other drawtext call in this project already runs
# on (see adapters/screens.py).
_ASIDE_FONT = "Comic Sans MS"
_ASIDE_FONT_HEIGHT_FRACTION = 0.028
_ASIDE_FONT_SIZE_FALLBACK = 28
_ASIDE_LABEL_MARGIN = 24


def _probe_video_height(source: Path, executable_lookup=shutil.which) -> int | None:
    """The source's video height in pixels, or ``None``. See :func:`_probe_frame_rate`."""

    try:
        probe = resolve_media_tool("ffprobe", path_lookup=executable_lookup)
    except ToolResolutionError:
        return None
    if probe is None:
        return None
    try:
        completed = subprocess.run(
            [
                probe,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=height",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(source),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
            shell=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if completed.returncode != 0:
        return None
    value = (completed.stdout or "").strip().splitlines()[0:1]
    try:
        height = int(value[0]) if value else 0
    except ValueError:
        return None
    return height if height > 0 else None


def _probe_frame_rate(source: Path, executable_lookup=shutil.which) -> str | None:
    """The source's ``r_frame_rate`` (e.g. ``30000/1001``), or ``None``.

    A tiny standalone ffprobe call: the shared ``StreamProbe`` is built
    positionally in many callers, so its shape is left alone.
    """

    try:
        probe = resolve_media_tool("ffprobe", path_lookup=executable_lookup)
    except ToolResolutionError:
        return None
    if probe is None:
        return None
    try:
        completed = subprocess.run(
            [
                probe,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=r_frame_rate",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(source),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
            shell=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if completed.returncode != 0:
        return None
    value = (completed.stdout or "").strip().splitlines()[0:1]
    rate = value[0].strip() if value else ""
    if not re.fullmatch(r"\d+(?:/\d+)?", rate) or rate in ("0", "0/0", "0/1"):
        return None
    return rate


def render_cut_preview(
    source_path: str | Path,
    output_path: str | Path,
    keep_intervals: "Sequence[tuple[float, float] | CutSegment]",
    *,
    video_bitrate: str = "5M",
    audio_bitrate: str = "192k",
    timeout_seconds: float = 1800,
) -> CutPreviewArtifact:
    """Concatenate the kept time ranges of one video into a new preview file.

    ``keep_intervals`` is an ordered, non-overlapping sequence of segments to
    keep, in source time -- the complement of the approved cuts (see
    :func:`video_generator.domain.cuts.keep_intervals` /
    :func:`~video_generator.domain.cuts.build_timeline`). Each item is either a
    plain ``(start_seconds, end_seconds)`` pair or a :class:`CutSegment` that
    additionally re-times the span, desaturates it, and/or burns a short label
    on it -- an "aside" the presenter chose to mark and keep rather than cut.

    One FFmpeg pass with a ``filter_complex``: each range is ``trim``/``atrim``
    with its PTS reset (and re-timed together with ``atempo`` when an aside
    changes speed, so video and audio stay the same length), a 10 ms audio fade
    is laid on both sides of every internal join (never on the outer edges,
    never on the picture -- an aside starts and ends on a hard cut, not a
    dissolve), and the ranges are ``concat``-ed. Video is re-encoded with
    ``libopenh264`` at the source frame rate and audio to AAC, so the cuts are
    frame-accurate and the A/V stays in sync with no black frames or dropped
    frames between segments. A label never enters the filter graph as text: it
    is written to its own UTF-8 file in a private temporary directory (the
    process runs with that directory as its cwd), the same way
    :mod:`video_generator.adapters.screens` keeps card text out of the graph.
    The source is never modified and an existing ``output_path`` is refused.
    """

    source = Path(source_path).expanduser().resolve()
    output = Path(output_path).expanduser().resolve()
    timeout = _time(timeout_seconds, "timeout_seconds")
    if timeout == 0:
        raise FFmpegError("timeout_seconds must be greater than zero")
    if not source.exists() or not source.is_file():
        raise FFmpegError(f"source does not exist or is not a file: {source}")
    if os.path.normcase(str(source)) == os.path.normcase(str(output)):
        raise FFmpegError("output_path must not overwrite the source")
    if output.exists():
        raise FFmpegError(f"output already exists: {output}")
    if output.suffix.lower() != ".mp4":
        raise FFmpegError("cut preview requires an .mp4 output_path")

    segments: list[CutSegment] = []
    previous_end = 0.0
    for item in keep_intervals:
        if isinstance(item, CutSegment):
            segment = item
        elif isinstance(item, (tuple, list)) and len(item) == 2:
            segment = CutSegment(item[0], item[1])
        else:
            raise FFmpegError(
                "each keep interval must be a (start, end) pair or a CutSegment"
            )
        start = _time(segment.start_seconds, "keep start")
        end = _time(segment.end_seconds, "keep end")
        if end <= start:
            raise FFmpegError("keep interval end must be after its start")
        if start + 1e-9 < previous_end:
            raise FFmpegError("keep intervals must be ordered and non-overlapping")
        if (
            isinstance(segment.speed, bool)
            or not isinstance(segment.speed, (int, float))
            or not math.isfinite(segment.speed)
            or segment.speed <= 0
        ):
            raise FFmpegError("segment speed must be a finite positive number")
        if segment.label is not None and (
            not isinstance(segment.label, str)
            or not segment.label.strip()
            or "\n" in segment.label
            or "\r" in segment.label
        ):
            raise FFmpegError("segment label must be a single non-empty line")
        previous_end = end
        segments.append(segment)
    if not segments:
        raise FFmpegError("at least one keep interval is required")

    try:
        executable = resolve_media_tool("ffmpeg", path_lookup=shutil.which)
    except ToolResolutionError as exc:
        raise FFmpegError(str(exc)) from exc
    if executable is None:
        raise FFmpegError("ffmpeg is not available locally or on PATH")

    frame_rate = _probe_frame_rate(source)
    fps_step = f"fps={frame_rate}," if frame_rate else ""
    labelled = [segment for segment in segments if segment.label is not None]
    font_size = _ASIDE_FONT_SIZE_FALLBACK
    if labelled:
        height = _probe_video_height(source)
        if height:
            font_size = max(16, round(height * _ASIDE_FONT_HEIGHT_FRACTION))

    filters: list[str] = []
    concat_labels: list[str] = []
    label_files: dict[int, str] = {}
    last = len(segments) - 1
    for index, segment in enumerate(segments):
        start, end = segment.start_seconds, segment.end_seconds
        s = _fmt(start)
        e = _fmt(end)
        span = end - start
        speed = float(segment.speed)
        effective_span = span / speed

        video_steps = [f"trim=start={s}:end={e}"]
        if speed != 1.0:
            video_steps.append(f"setpts=(PTS-STARTPTS)/{_fmt(speed)}")
        else:
            video_steps.append("setpts=PTS-STARTPTS")
        if segment.grayscale:
            video_steps.append("hue=s=0")
        if segment.label is not None:
            name = f"aside{index:03d}.txt"
            label_files[index] = name
            video_steps.append(
                "drawtext="
                f"font={_ASIDE_FONT}:textfile={name}:"
                f"fontcolor=white:fontsize={font_size}:"
                f"x={_ASIDE_LABEL_MARGIN}:y=h-th-{_ASIDE_LABEL_MARGIN}:"
                "box=1:boxcolor=black@0.55:boxborderw=10"
            )
        video_steps.append(f"{fps_step}setsar=1" if fps_step else "setsar=1")
        filters.append(f"[0:v]{','.join(video_steps)}[v{index}]")

        audio_steps = [f"atrim=start={s}:end={e}", "asetpts=PTS-STARTPTS"]
        if speed != 1.0:
            audio_steps.append(f"atempo={_fmt(speed)}")
        if index != 0 and effective_span > 2 * _CUT_JOIN_FADE_SECONDS:
            audio_steps.append(f"afade=t=in:st=0:d={_fmt(_CUT_JOIN_FADE_SECONDS)}")
        if index != last and effective_span > 2 * _CUT_JOIN_FADE_SECONDS:
            audio_steps.append(
                f"afade=t=out:st={_fmt(effective_span - _CUT_JOIN_FADE_SECONDS)}"
                f":d={_fmt(_CUT_JOIN_FADE_SECONDS)}"
            )
        filters.append(f"[0:a]{','.join(audio_steps)}[a{index}]")
        concat_labels.append(f"[v{index}][a{index}]")
    filters.append(
        f"{''.join(concat_labels)}concat=n={len(segments)}:v=1:a=1[outv][outa]"
    )
    filter_graph = ";".join(filters)

    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            prefix=f".{output.stem}-",
            suffix=output.suffix,
            dir=output.parent,
            delete=False,
        ) as reserved:
            temporary = Path(reserved.name)
    except OSError as exc:
        raise FFmpegError(f"could not prepare output path: {output}") from exc

    command = [executable, "-v", "error", "-nostdin", "-y", "-i", str(source)]
    filter_file: Path | None = None
    workspace_dir = tempfile.TemporaryDirectory(prefix=f".{output.stem}-labels-")
    try:
        for index, name in label_files.items():
            (Path(workspace_dir.name) / name).write_text(
                segments[index].label, encoding="utf-8"
            )
    except OSError as exc:
        workspace_dir.cleanup()
        _cleanup(temporary)
        raise FFmpegError(f"could not write an aside label beside: {output}") from exc
    if len(filter_graph) > _FILTER_GRAPH_INLINE_LIMIT:
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                prefix=f".{output.stem}-filter-",
                suffix=".txt",
                dir=output.parent,
                delete=False,
            ) as handle:
                filter_file = Path(handle.name)
                handle.write(filter_graph)
        except OSError as exc:
            workspace_dir.cleanup()
            _cleanup(temporary)
            raise FFmpegError(f"could not write the filter graph beside: {output}") from exc
        command.extend(["-filter_complex_script", str(filter_file)])
    else:
        command.extend(["-filter_complex", filter_graph])
    command.extend(["-map", "[outv]", "-map", "[outa]"])
    if frame_rate:
        command.extend(["-r", frame_rate])
    command.extend(
        [
            "-fps_mode",
            "cfr",
            "-c:v",
            "libopenh264",
            "-b:v",
            str(video_bitrate),
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            str(audio_bitrate),
            "-movflags",
            "+faststart",
            str(temporary),
        ]
    )

    try:
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
                shell=False,
                cwd=workspace_dir.name,
            )
        except subprocess.TimeoutExpired as exc:
            _cleanup(temporary)
            if filter_file is not None:
                _cleanup(filter_file)
            raise FFmpegError("ffmpeg timed out while rendering the cut preview") from exc
        except OSError as exc:
            _cleanup(temporary)
            if filter_file is not None:
                _cleanup(filter_file)
            raise FFmpegError(
                f"ffmpeg could not render the cut preview: {type(exc).__name__}"
            ) from exc
        if filter_file is not None:
            _cleanup(filter_file)

        if completed.returncode != 0:
            _cleanup(temporary)
            detail = (completed.stderr or completed.stdout).strip()
            suffix = f": {detail}" if detail else ""
            raise FFmpegError(f"ffmpeg exited with {completed.returncode}{suffix}")
        if not temporary.is_file() or temporary.stat().st_size == 0:
            _cleanup(temporary)
            raise FFmpegError("ffmpeg reported success without creating a non-empty preview")
    finally:
        workspace_dir.cleanup()
    try:
        os.link(temporary, output)
    except OSError as exc:
        _cleanup(temporary)
        raise FFmpegError(f"could not publish output without overwriting: {output}") from exc
    _cleanup(temporary)
    try:
        size = output.stat().st_size
    except OSError as exc:
        raise FFmpegError(f"could not inspect published output: {output}") from exc
    return CutPreviewArtifact(
        source_path=str(source),
        output_path=str(output),
        segment_count=len(segments),
        kept_seconds=sum(segment.end_seconds - segment.start_seconds for segment in segments),
        file_size_bytes=size,
        frame_rate=frame_rate,
        aside_count=len(labelled),
    )


# --- editorial visual layer: punch-in zooms and freeze frames -----------------
#
# Applied to an already-cut preview (``render_cut_preview``'s output), never to
# raw footage. Both are deliberately crude: a zoom is a plain crop+scale, a
# freeze is a held frame with an optional Comic-Sans label, matching the
# channel's hand-made, unpolished visual identity rather than a slick editing
# look.


@dataclass(frozen=True, slots=True)
class ZoomEvent:
    """A punch-in over an existing span: no cut, the picture scales in and back."""

    start_seconds: float
    end_seconds: float
    scale: float = 1.08


@dataclass(frozen=True, slots=True)
class FreezeEvent:
    """Hold the frame at ``at_seconds`` for ``freeze_seconds``, optionally labelled."""

    at_seconds: float
    freeze_seconds: float = 1.0
    label: str | None = None


@dataclass(frozen=True, slots=True)
class VisualEffectsArtifact:
    source_path: str
    output_path: str
    zoom_count: int
    freeze_count: int
    file_size_bytes: int
    duration_seconds: float


_FREEZE_FRAME_EPS = 0.05
MIN_KEEP_SECONDS_FOR_VISUAL_EFFECTS = 0.04


def _probe_video_width(source: Path, executable_lookup=shutil.which) -> int | None:
    """The source's video width in pixels, or ``None``. See :func:`_probe_video_height`."""

    try:
        probe = resolve_media_tool("ffprobe", path_lookup=executable_lookup)
    except ToolResolutionError:
        return None
    if probe is None:
        return None
    try:
        completed = subprocess.run(
            [
                probe,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(source),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
            shell=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if completed.returncode != 0:
        return None
    value = (completed.stdout or "").strip().splitlines()[0:1]
    try:
        width = int(value[0]) if value else 0
    except ValueError:
        return None
    return width if width > 0 else None


def _probe_duration_seconds(source: Path, executable_lookup=shutil.which) -> float | None:
    """The source container's total duration in seconds, or ``None``."""

    try:
        probe = resolve_media_tool("ffprobe", path_lookup=executable_lookup)
    except ToolResolutionError:
        return None
    if probe is None:
        return None
    try:
        completed = subprocess.run(
            [
                probe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(source),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
            shell=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if completed.returncode != 0:
        return None
    value = (completed.stdout or "").strip().splitlines()[0:1]
    try:
        duration = float(value[0]) if value else 0.0
    except ValueError:
        return None
    return duration if duration > 0 else None


def _probe_audio_format(source: Path, executable_lookup=shutil.which) -> tuple[int, str]:
    """``(sample_rate_hz, channel_layout)`` for the source's audio, defaulted on failure.

    Used only to synthesize matching silence for a freeze's audio gap -- the
    default (48 kHz stereo) is never wrong in a way that breaks the render, it
    just means the concat below normalises every branch through the same
    ``aformat`` regardless.
    """

    try:
        probe = resolve_media_tool("ffprobe", path_lookup=executable_lookup)
    except ToolResolutionError:
        return 48000, "stereo"
    if probe is None:
        return 48000, "stereo"
    try:
        completed = subprocess.run(
            [
                probe,
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=sample_rate,channels",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(source),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
            shell=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return 48000, "stereo"
    if completed.returncode != 0:
        return 48000, "stereo"
    lines = (completed.stdout or "").strip().splitlines()
    try:
        sample_rate = int(lines[0]) if lines else 48000
        channels = int(lines[1]) if len(lines) > 1 else 2
    except ValueError:
        return 48000, "stereo"
    layout = "mono" if channels == 1 else "stereo"
    return (sample_rate if sample_rate > 0 else 48000), layout


@dataclass(frozen=True, slots=True)
class VideoDimensions:
    width: int
    height: int
    frame_rate: str
    fps: float
    duration_seconds: float


def probe_video_dimensions(source_path: str | Path) -> VideoDimensions:
    """Width, height, frame rate and duration a Remotion scene needs to match a video.

    Fails closed with :class:`FFmpegError` rather than guessing a canvas size
    that would silently letterbox or mis-time the overlay.
    """

    source = Path(source_path).expanduser().resolve()
    if not source.exists() or not source.is_file():
        raise FFmpegError(f"source does not exist or is not a file: {source}")
    width = _probe_video_width(source)
    height = _probe_video_height(source)
    frame_rate = _probe_frame_rate(source)
    duration = _probe_duration_seconds(source)
    if width is None or height is None or frame_rate is None or duration is None:
        raise FFmpegError(f"could not probe width/height/frame rate/duration for: {source}")
    if "/" in frame_rate:
        num, _, den = frame_rate.partition("/")
        fps = float(num) / float(den)
    else:
        fps = float(frame_rate)
    return VideoDimensions(
        width=width, height=height, frame_rate=frame_rate, fps=fps, duration_seconds=duration
    )


def render_visual_effects(
    source_path: str | Path,
    output_path: str | Path,
    *,
    zoom_events: Sequence[ZoomEvent] = (),
    freeze_events: Sequence[FreezeEvent] = (),
    video_bitrate: str = "5M",
    audio_bitrate: str = "192k",
    timeout_seconds: float = 1800,
) -> VisualEffectsArtifact:
    """Apply punch-in zooms and freeze frames to an already-cut preview.

    One FFmpeg pass: the source is split at every freeze's timestamp, each
    plain span keeps its own picture (with a crop+scale zoom baked in wherever
    a :class:`ZoomEvent` falls fully inside it) and each freeze span holds a
    single frame for its duration with a short silent audio gap in its place,
    optionally labelled the same way an ``aside`` is (a Comic-Sans, boxed,
    single-line ``drawtext``, read from a private text file, never from the
    filter graph). The pieces are re-concatenated with the same 10 ms audio
    micro-fades ``render_cut_preview`` uses at internal joins. The source is
    never modified and an existing ``output_path`` is refused.
    """

    source = Path(source_path).expanduser().resolve()
    output = Path(output_path).expanduser().resolve()
    timeout = _time(timeout_seconds, "timeout_seconds")
    if timeout == 0:
        raise FFmpegError("timeout_seconds must be greater than zero")
    if not source.exists() or not source.is_file():
        raise FFmpegError(f"source does not exist or is not a file: {source}")
    if os.path.normcase(str(source)) == os.path.normcase(str(output)):
        raise FFmpegError("output_path must not overwrite the source")
    if output.exists():
        raise FFmpegError(f"output already exists: {output}")
    if output.suffix.lower() != ".mp4":
        raise FFmpegError("visual effects preview requires an .mp4 output_path")

    zooms = sorted(zoom_events, key=lambda z: z.start_seconds)
    for previous, current in zip(zooms, zooms[1:]):
        if current.start_seconds < previous.end_seconds:
            raise FFmpegError("zoom events must not overlap")
    for zoom in zooms:
        if not (1.0 < zoom.scale <= 1.6):
            raise FFmpegError("zoom scale must be in (1.0, 1.6]")
        if zoom.end_seconds <= zoom.start_seconds:
            raise FFmpegError("zoom end must be after its start")

    freezes = sorted(freeze_events, key=lambda f: f.at_seconds)
    for previous, current in zip(freezes, freezes[1:]):
        if current.at_seconds <= previous.at_seconds:
            raise FFmpegError("freeze events must be at distinct, ordered timestamps")
    for freeze in freezes:
        if freeze.freeze_seconds <= 0:
            raise FFmpegError("freeze_seconds must be positive")

    if not zooms and not freezes:
        raise FFmpegError("at least one zoom or freeze event is required")

    try:
        executable = resolve_media_tool("ffmpeg", path_lookup=shutil.which)
    except ToolResolutionError as exc:
        raise FFmpegError(str(exc)) from exc
    if executable is None:
        raise FFmpegError("ffmpeg is not available locally or on PATH")

    duration = _probe_duration_seconds(source)
    if duration is None:
        raise FFmpegError(f"could not determine the source duration: {source}")
    for freeze in freezes:
        if freeze.at_seconds >= duration:
            raise FFmpegError("freeze timestamp is at or past the end of the source")
    for zoom in zooms:
        if zoom.end_seconds > duration:
            raise FFmpegError("zoom window extends past the end of the source")

    frame_rate = _probe_frame_rate(source)
    fps_step = f"fps={frame_rate}," if frame_rate else ""
    sample_rate, channel_layout = _probe_audio_format(source)

    height = _probe_video_height(source)
    width = _probe_video_width(source)
    if zooms and (height is None or width is None):
        raise FFmpegError(
            "a zoom event needs the source's width and height, and at least "
            "one could not be probed"
        )

    labelled = [freeze for freeze in freezes if freeze.label is not None]
    font_size = _ASIDE_FONT_SIZE_FALLBACK
    if labelled and height:
        font_size = max(16, round(height * _ASIDE_FONT_HEIGHT_FRACTION))

    # A punch-in here is a hard cut to a tighter, constant framing and a hard
    # cut back -- not an eased ramp. FFmpeg's crop filter cannot read the
    # timestamp in its w/h expressions (only x/y can), so an eased zoom would
    # need zoompan, whose per-frame resampling does not stay 1:1 with a plain
    # video source. A deliberately un-animated punch-in sidesteps that
    # entirely and, for a channel whose whole identity is hand-made and a
    # little rough, is arguably the more honest choice anyway.
    zoom_by_window: dict[tuple[float, float], ZoomEvent] = {
        (zoom.start_seconds, zoom.end_seconds): zoom for zoom in zooms
    }
    freeze_by_at: dict[float, FreezeEvent] = {freeze.at_seconds: freeze for freeze in freezes}

    cut_points = sorted(
        {0.0, duration, *freeze_by_at, *(w[0] for w in zoom_by_window), *(w[1] for w in zoom_by_window)}
    )

    filters: list[str] = []
    concat_labels: list[str] = []
    label_files: dict[str, str] = {}
    piece_index = 0
    freeze_index = 0
    for point_index in range(len(cut_points) - 1):
        a, b = cut_points[point_index], cut_points[point_index + 1]

        freeze = freeze_by_at.get(a)
        if freeze is not None:
            hold_start = min(a, max(duration - _FREEZE_FRAME_EPS, 0.0))
            hold_end = min(hold_start + _FREEZE_FRAME_EPS, duration)
            hs, he = _fmt(hold_start), _fmt(hold_end)
            grabbed = max(hold_end - hold_start, 1e-6)
            stop_duration = max(freeze.freeze_seconds - grabbed, 0.0)
            freeze_video_steps = [
                f"trim=start={hs}:end={he}",
                "setpts=PTS-STARTPTS",
                f"tpad=stop_mode=clone:stop_duration={_fmt(stop_duration)}",
            ]
            if freeze.label is not None:
                name = f"freeze{freeze_index:03d}.txt"
                label_files[name] = freeze.label
                freeze_video_steps.append(
                    "drawtext="
                    f"font={_ASIDE_FONT}:textfile={name}:"
                    f"fontcolor=white:fontsize={font_size}:"
                    f"x={_ASIDE_LABEL_MARGIN}:y=h-th-{_ASIDE_LABEL_MARGIN}:"
                    "box=1:boxcolor=black@0.55:boxborderw=10"
                )
            freeze_video_steps.append(f"{fps_step}setsar=1" if fps_step else "setsar=1")
            fvlabel = f"p{piece_index}v"
            falabel = f"p{piece_index}a"
            filters.append(f"[0:v]{','.join(freeze_video_steps)}[{fvlabel}]")
            filters.append(
                f"anullsrc=r={sample_rate}:cl={channel_layout}:d={_fmt(freeze.freeze_seconds)}[{falabel}]"
            )
            concat_labels.append(f"[{fvlabel}][{falabel}]")
            piece_index += 1
            freeze_index += 1

        if b - a < MIN_KEEP_SECONDS_FOR_VISUAL_EFFECTS:
            continue
        s, e = _fmt(a), _fmt(b)
        video_steps = [f"trim=start={s}:end={e}", "setpts=PTS-STARTPTS"]
        zoom = zoom_by_window.get((a, b))
        if zoom is not None:
            crop_w = _even(width / zoom.scale)
            crop_h = _even(height / zoom.scale)
            crop_x = (width - crop_w) // 2
            crop_y = (height - crop_h) // 2
            video_steps.append(
                f"crop={crop_w}:{crop_h}:{crop_x}:{crop_y},scale={width}:{height}"
            )
        video_steps.append(f"{fps_step}setsar=1" if fps_step else "setsar=1")
        vlabel = f"p{piece_index}v"
        alabel = f"p{piece_index}a"
        filters.append(f"[0:v]{','.join(video_steps)}[{vlabel}]")
        filters.append(
            f"[0:a]atrim=start={s}:end={e},asetpts=PTS-STARTPTS,"
            f"aformat=sample_rates={sample_rate}:channel_layouts={channel_layout}[{alabel}]"
        )
        concat_labels.append(f"[{vlabel}][{alabel}]")
        piece_index += 1

    if not concat_labels:
        raise FFmpegError("visual effects plan produced an empty timeline")

    filters.append(
        f"{''.join(concat_labels)}concat=n={len(concat_labels)}:v=1:a=1[outv][outa]"
    )
    filter_graph = ";".join(filters)

    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            prefix=f".{output.stem}-",
            suffix=output.suffix,
            dir=output.parent,
            delete=False,
        ) as reserved:
            temporary = Path(reserved.name)
    except OSError as exc:
        raise FFmpegError(f"could not prepare output path: {output}") from exc

    command = [executable, "-v", "error", "-nostdin", "-y", "-i", str(source)]
    filter_file: Path | None = None
    workspace_dir = tempfile.TemporaryDirectory(prefix=f".{output.stem}-labels-")
    try:
        for name, text in label_files.items():
            (Path(workspace_dir.name) / name).write_text(text, encoding="utf-8")
    except OSError as exc:
        workspace_dir.cleanup()
        _cleanup(temporary)
        raise FFmpegError(f"could not write a freeze label beside: {output}") from exc

    if len(filter_graph) > _FILTER_GRAPH_INLINE_LIMIT:
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                prefix=f".{output.stem}-filter-",
                suffix=".txt",
                dir=output.parent,
                delete=False,
            ) as handle:
                filter_file = Path(handle.name)
                handle.write(filter_graph)
        except OSError as exc:
            workspace_dir.cleanup()
            _cleanup(temporary)
            raise FFmpegError(f"could not write the filter graph beside: {output}") from exc
        command.extend(["-filter_complex_script", str(filter_file)])
    else:
        command.extend(["-filter_complex", filter_graph])
    command.extend(["-map", "[outv]", "-map", "[outa]"])
    if frame_rate:
        command.extend(["-r", frame_rate])
    command.extend(
        [
            "-fps_mode",
            "cfr",
            "-c:v",
            "libopenh264",
            "-b:v",
            str(video_bitrate),
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            str(audio_bitrate),
            "-movflags",
            "+faststart",
            str(temporary),
        ]
    )

    try:
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
                shell=False,
                cwd=workspace_dir.name,
            )
        except subprocess.TimeoutExpired as exc:
            _cleanup(temporary)
            if filter_file is not None:
                _cleanup(filter_file)
            raise FFmpegError("ffmpeg timed out while rendering visual effects") from exc
        except OSError as exc:
            _cleanup(temporary)
            if filter_file is not None:
                _cleanup(filter_file)
            raise FFmpegError(
                f"ffmpeg could not render visual effects: {type(exc).__name__}"
            ) from exc
        if filter_file is not None:
            _cleanup(filter_file)

        if completed.returncode != 0:
            _cleanup(temporary)
            detail = (completed.stderr or completed.stdout).strip()
            suffix = f": {detail}" if detail else ""
            raise FFmpegError(f"ffmpeg exited with {completed.returncode}{suffix}")
        if not temporary.is_file() or temporary.stat().st_size == 0:
            _cleanup(temporary)
            raise FFmpegError("ffmpeg reported success without creating a non-empty output")
    finally:
        workspace_dir.cleanup()
    try:
        os.link(temporary, output)
    except OSError as exc:
        _cleanup(temporary)
        raise FFmpegError(f"could not publish output without overwriting: {output}") from exc
    _cleanup(temporary)
    try:
        size = output.stat().st_size
    except OSError as exc:
        raise FFmpegError(f"could not inspect published output: {output}") from exc

    total_duration = duration + sum(freeze.freeze_seconds for freeze in freezes)
    return VisualEffectsArtifact(
        source_path=str(source),
        output_path=str(output),
        zoom_count=len(zooms),
        freeze_count=len(freezes),
        file_size_bytes=size,
        duration_seconds=round(total_duration, 3),
    )


@dataclass(frozen=True, slots=True)
class OverlayCompositeArtifact:
    base_path: str
    overlay_path: str
    output_path: str
    file_size_bytes: int


def composite_overlay(
    base_video_path: str | Path,
    overlay_clip_path: str | Path,
    output_path: str | Path,
    *,
    video_bitrate: str = "5M",
    audio_bitrate: str = "192k",
    timeout_seconds: float = 1800,
) -> OverlayCompositeArtifact:
    """Composite a transparent motion-graphics overlay over a base video.

    ``overlay_clip_path`` is a Remotion-rendered alpha clip (see
    :func:`video_generator.adapters.remotion.render_motion_overlay`) already
    timed against the base video's own timeline -- it is placed at ``0:0`` for
    its own duration and simply lets the base picture show through wherever it
    is transparent (``overlay=format=auto``). Audio passes through untouched.
    """

    base = Path(base_video_path).expanduser().resolve()
    overlay = Path(overlay_clip_path).expanduser().resolve()
    output = Path(output_path).expanduser().resolve()
    timeout = _time(timeout_seconds, "timeout_seconds")
    if timeout == 0:
        raise FFmpegError("timeout_seconds must be greater than zero")
    if not base.exists() or not base.is_file():
        raise FFmpegError(f"base video does not exist or is not a file: {base}")
    if not overlay.exists() or not overlay.is_file():
        raise FFmpegError(f"overlay clip does not exist or is not a file: {overlay}")
    if output.exists():
        raise FFmpegError(f"output already exists: {output}")
    if output.suffix.lower() != ".mp4":
        raise FFmpegError("overlay composite requires an .mp4 output_path")

    try:
        executable = resolve_media_tool("ffmpeg", path_lookup=shutil.which)
    except ToolResolutionError as exc:
        raise FFmpegError(str(exc)) from exc
    if executable is None:
        raise FFmpegError("ffmpeg is not available locally or on PATH")

    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            prefix=f".{output.stem}-",
            suffix=output.suffix,
            dir=output.parent,
            delete=False,
        ) as reserved:
            temporary = Path(reserved.name)
    except OSError as exc:
        raise FFmpegError(f"could not prepare output path: {output}") from exc

    command = [
        executable,
        "-v",
        "error",
        "-nostdin",
        "-y",
        "-i",
        str(base),
        "-i",
        str(overlay),
        "-filter_complex",
        "[0:v][1:v]overlay=0:0:format=auto:eof_action=pass[outv]",
        "-map",
        "[outv]",
        "-map",
        "0:a",
        "-c:v",
        "libopenh264",
        "-b:v",
        str(video_bitrate),
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        str(audio_bitrate),
        "-shortest",
        "-movflags",
        "+faststart",
        str(temporary),
    ]

    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        _cleanup(temporary)
        raise FFmpegError("ffmpeg timed out compositing the overlay") from exc
    except OSError as exc:
        _cleanup(temporary)
        raise FFmpegError(f"could not launch ffmpeg: {type(exc).__name__}") from exc

    if completed.returncode != 0:
        _cleanup(temporary)
        detail = (completed.stderr or completed.stdout).strip()
        suffix = f": {detail}" if detail else ""
        raise FFmpegError(f"ffmpeg exited with {completed.returncode}{suffix}")
    if not temporary.is_file() or temporary.stat().st_size == 0:
        _cleanup(temporary)
        raise FFmpegError("ffmpeg reported success without creating a non-empty composite")

    try:
        os.link(temporary, output)
    except OSError as exc:
        _cleanup(temporary)
        raise FFmpegError(f"could not publish output without overwriting: {output}") from exc
    _cleanup(temporary)
    try:
        size = output.stat().st_size
    except OSError as exc:
        raise FFmpegError(f"could not inspect published output: {output}") from exc

    return OverlayCompositeArtifact(
        base_path=str(base),
        overlay_path=str(overlay),
        output_path=str(output),
        file_size_bytes=size,
    )
