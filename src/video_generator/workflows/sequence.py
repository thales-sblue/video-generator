"""Compose a persisted clip sequence with captions and planned audio."""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import tempfile
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Callable, NamedTuple

from video_generator.adapters import (
    CaptionCue,
    COMPOSITIONS,
    CROP_BIASES,
    DIRECTION_MOTIONS,
    DirectionSpec,
    GRADE_INTENSITIES,
    TEXT_EVENT_ANIMATIONS,
    TEXT_EVENT_POSITIONS,
    TEXT_ZONES,
    TextEventCue,
    TextStyleSpec,
    FFmpegError,
    KokoroError,
    MediaProbe,
    ProbeError,
    SequenceArtifact,
    SequenceClip,
    SequenceImage,
    compose_video_sequence,
    detect_silences,
    probe_media,
    synthesize_narration,
)
from video_generator.domain import EditPlan
from video_generator.subtitles import (
    SubtitleParseError,
    align_cues_to_silences,
    captions_from_text,
    parse_subtitle_cues,
)
from video_generator.validation import (
    PreflightReport,
    SequenceValidationReport,
    preflight_edit_plan,
    validate_sequence_artifact,
)


WORKFLOW_NAME = "video-sequence"
OPERATION_KIND = "sequence_clip"
IMAGE_KIND = "image_clip"
IMAGE_MAX_DURATION_SECONDS = 600.0
IMAGE_MOTIONS = ("zoom_in", "zoom_out", "pan_left", "pan_right", "pan_up", "pan_down")
NARRATION_KIND = "narration"
NARRATION_PARAMETERS = {"duration_policy": "match_timeline"}
NARRATION_TEXT_KEYS = {
    "duration_policy",
    "text",
    "voice",
    "speed",
    "lang",
    "lead_in_seconds",
}
NARRATION_DEFAULT_VOICE = "af_heart"
NARRATION_DEFAULT_SPEED = 1.0
NARRATION_DEFAULT_LANG = "en-us"
CAPTIONS_KIND = "captions"
CAPTIONS_STYLE = "bottom_box"
CAPTION_SUBTITLE_FORMATS = {".srt": "srt", ".vtt": "vtt"}
# An ASS colour literal: &H then 6 (BGR) or 8 (ABGR) hex digits.
_ASS_COLOUR = re.compile(r"^&H[0-9A-Fa-f]{6}(?:[0-9A-Fa-f]{2})?$")
TEXT_EVENTS_KIND = "text_events"
TEXT_EVENT_ITEM_KEYS = {"text", "start_seconds", "end_seconds"}
TEXT_EVENT_ITEM_OPTIONAL = {"position", "animation", "emphasis", "highlight"}
TEXT_EVENT_STYLE_KEYS = {
    "font_name", "foreground", "accent", "emphasis_scale", "safe_margin_fraction",
}
VISUAL_DIRECTION_KIND = "visual_direction"
# The framing / movement / grade keys a timeline segment may carry. All
# optional: a segment that states none of them is composed exactly as before.
SEGMENT_DIRECTION_KEYS = {"composition", "crop_bias", "text_zone", "grade"}
VISUAL_DIRECTION_GRADE_KEYS = {
    "saturation", "shadow_density", "highlight_gain", "highlight_ceiling",
    "cool_shift", "grain", "vignette_angle", "luminance_pull",
}
VISUAL_DIRECTION_GEOMETRY_KEYS = {
    "background_blur_sigma", "background_darkening", "inset_scale",
    "extreme_crop_zoom", "split_gap_fraction", "scrim_opacity",
    "scrim_height_fraction", "push_travel", "detail_push_travel", "drift_travel",
}
MUSIC_KIND = "music"
MUSIC_DURATION_POLICY = "loop_to_timeline"
FADE_KIND = "fade"
FADE_KEYS = {"from_black_seconds", "to_black_seconds"}


@dataclass(frozen=True, slots=True)
class NarrationTextSpec:
    text: str
    voice: str
    speed: float
    lang: str
    lead_in_seconds: float = 0.0


@dataclass(frozen=True, slots=True)
class MusicSpec:
    gain_db: float
    fade_in_seconds: float
    fade_out_seconds: float
    duck_db: float | None = None


@dataclass(frozen=True, slots=True)
class FadeSpec:
    from_black_seconds: float
    to_black_seconds: float


class SequenceWorkflowError(RuntimeError):
    """Raised when a sequence plan cannot safely produce a workflow report."""


@dataclass(frozen=True, slots=True)
class SequenceWorkflowReport:
    plan_id: str
    operation_ids: tuple[str, ...]
    preflight: PreflightReport
    artifact: SequenceArtifact
    validation: SequenceValidationReport
    publication: str = "working"

    def __post_init__(self) -> None:
        if self.publication not in {"working", "final"}:
            raise ValueError("publication must be working or final")
        if (
            self.publication == "final"
            and Path(self.artifact.output_path).name.lower() != "final.mp4"
        ):
            raise ValueError("final publication artifact must be named final.mp4")

    @property
    def valid(self) -> bool:
        return self.preflight.valid and self.validation.valid

    def to_dict(self) -> dict[str, object]:
        return {
            "workflow": WORKFLOW_NAME,
            "plan_id": self.plan_id,
            "operation_ids": list(self.operation_ids),
            "valid": self.valid,
            "preflight": self.preflight.to_dict(),
            "artifact": asdict(self.artifact),
            "validation": self.validation.to_dict(),
            "publication": self.publication,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _runtime_number(value: object, name: str, *, allow_zero: bool) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
        or (value == 0 and not allow_zero)
    ):
        qualifier = "non-negative" if allow_zero else "positive"
        raise SequenceWorkflowError(f"{name} must be a finite {qualifier} number")
    return float(value)


def _inline_caption_triples(
    parameters: Mapping[str, object]
) -> tuple[tuple[str, float, float], ...]:
    values = dict(parameters)
    if set(values) != {"style", "items"} or values["style"] != CAPTIONS_STYLE:
        raise SequenceWorkflowError(
            "captions require style=bottom_box and an items array"
        )
    items = values["items"]
    if isinstance(items, (str, bytes)) or not isinstance(items, (list, tuple)):
        raise SequenceWorkflowError("captions items must be an array")
    triples: list[tuple[str, float, float]] = []
    for index, item in enumerate(items):
        if not isinstance(item, Mapping) or set(item) != {
            "text", "start_seconds", "end_seconds"
        }:
            raise SequenceWorkflowError(
                f"caption item {index} requires text, start_seconds and end_seconds"
            )
        text = item["text"]
        if not isinstance(text, str):
            raise SequenceWorkflowError(f"caption item {index} text must be a string")
        start = _runtime_number(
            item["start_seconds"],
            f"caption item {index} start_seconds",
            allow_zero=True,
        )
        end = _runtime_number(
            item["end_seconds"],
            f"caption item {index} end_seconds",
            allow_zero=False,
        )
        triples.append((text, start, end))
    return tuple(triples)


def _validate_caption_cues(
    raw: tuple[tuple[str, float, float], ...]
) -> tuple[CaptionCue, ...]:
    if not raw or len(raw) > 500:
        raise SequenceWorkflowError("captions require between 1 and 500 items")
    cues = []
    previous_end = 0.0
    for index, (text, start, end) in enumerate(raw):
        stripped = text.strip()
        if not stripped or len(stripped) > 160:
            raise SequenceWorkflowError(
                f"caption item {index} text must contain 1 to 160 characters"
            )
        if any(ord(character) < 32 for character in stripped):
            raise SequenceWorkflowError(
                f"caption item {index} text must not contain control characters"
            )
        if any(character in stripped for character in "<>{}"):
            raise SequenceWorkflowError(
                f"caption item {index} text must not contain subtitle markup characters"
            )
        if end <= start or round(end * 1000) <= round(start * 1000):
            raise SequenceWorkflowError(
                f"caption item {index} must last at least 1 ms"
            )
        if start < previous_end:
            raise SequenceWorkflowError("caption items must be ordered and non-overlapping")
        cues.append(CaptionCue(stripped, start, end))
        previous_end = end
    return tuple(cues)


def _caption_cues_from_file(source: str) -> tuple[CaptionCue, ...]:
    path = Path(source).expanduser()
    source_format = CAPTION_SUBTITLE_FORMATS.get(path.suffix.lower())
    if source_format is None:
        raise SequenceWorkflowError("captions source must be a .srt or .vtt file")
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SequenceWorkflowError(f"cannot read captions file: {path}") from exc
    except UnicodeDecodeError as exc:
        raise SequenceWorkflowError(f"captions file must be UTF-8 text: {path}") from exc
    try:
        raw = parse_subtitle_cues(content, source_format=source_format)
    except SubtitleParseError as exc:
        raise SequenceWorkflowError(f"invalid captions file: {exc}") from exc
    return _validate_caption_cues(raw)


def _text_event_cues(
    parameters: Mapping[str, object]
) -> "tuple[tuple[TextEventCue, ...], TextStyleSpec | None]":
    """Parse a ``text_events`` operation into cues plus an optional style.

    Emphasis is a separate layer from the captions: it may sit anywhere on the
    frame and it does not transcribe the voice, so it gets its own operation
    rather than more keys on the caption one.
    """

    values = dict(parameters)
    unknown = set(values) - {"items", "style"}
    if unknown:
        raise SequenceWorkflowError(
            f"text_events does not accept: {', '.join(sorted(unknown))}"
        )
    items = values.get("items")
    if isinstance(items, (str, bytes)) or not isinstance(items, (list, tuple)):
        raise SequenceWorkflowError("text_events items must be an array")
    if not items or len(items) > 200:
        raise SequenceWorkflowError("text_events require between 1 and 200 items")

    cues: list[TextEventCue] = []
    previous_end = 0.0
    for index, item in enumerate(items):
        if not isinstance(item, Mapping):
            raise SequenceWorkflowError(f"text event {index} must be an object")
        missing = TEXT_EVENT_ITEM_KEYS - set(item)
        if missing:
            raise SequenceWorkflowError(
                f"text event {index} requires {', '.join(sorted(missing))}"
            )
        extra = set(item) - TEXT_EVENT_ITEM_KEYS - TEXT_EVENT_ITEM_OPTIONAL
        if extra:
            raise SequenceWorkflowError(
                f"text event {index} does not accept: {', '.join(sorted(extra))}"
            )
        text = item["text"]
        if not isinstance(text, str):
            raise SequenceWorkflowError(f"text event {index} text must be a string")
        stripped = text.strip()
        if not stripped or len(stripped) > 48:
            raise SequenceWorkflowError(
                f"text event {index} text must contain 1 to 48 characters"
            )
        if any(ord(character) < 32 for character in stripped):
            raise SequenceWorkflowError(
                f"text event {index} text must not contain control characters"
            )
        if any(character in stripped for character in "<>{}"):
            raise SequenceWorkflowError(
                f"text event {index} text must not contain subtitle markup characters"
            )
        start = _runtime_number(
            item["start_seconds"], f"text event {index} start_seconds", allow_zero=True
        )
        end = _runtime_number(
            item["end_seconds"], f"text event {index} end_seconds", allow_zero=False
        )
        if end <= start or round(end * 1000) <= round(start * 1000):
            raise SequenceWorkflowError(f"text event {index} must last at least 1 ms")
        if start < previous_end:
            raise SequenceWorkflowError("text events must be ordered and non-overlapping")
        position = item.get("position", "top")
        animation = item.get("animation", "fade")
        emphasis = item.get("emphasis", False)
        if not isinstance(position, str) or position not in TEXT_EVENT_POSITIONS:
            raise SequenceWorkflowError(
                f"text event {index} position must be one of {TEXT_EVENT_POSITIONS}"
            )
        if not isinstance(animation, str) or animation not in TEXT_EVENT_ANIMATIONS:
            raise SequenceWorkflowError(
                f"text event {index} animation must be one of {TEXT_EVENT_ANIMATIONS}"
            )
        if not isinstance(emphasis, bool):
            raise SequenceWorkflowError(f"text event {index} emphasis must be a boolean")
        highlight = item.get("highlight")
        if highlight is not None:
            if (
                isinstance(highlight, (str, bytes))
                or not isinstance(highlight, (list, tuple))
                or len(highlight) != 2
                or any(
                    isinstance(v, bool) or not isinstance(v, int) for v in highlight
                )
                or not 0 <= highlight[0] < highlight[1] <= len(stripped)
            ):
                raise SequenceWorkflowError(
                    f"text event {index} highlight must be a span inside the text"
                )
            highlight = (int(highlight[0]), int(highlight[1]))
        cues.append(
            TextEventCue(stripped, start, end, position, animation, emphasis, highlight)
        )
        previous_end = end

    style_values = values.get("style")
    if style_values is None:
        return tuple(cues), None
    if not isinstance(style_values, Mapping):
        raise SequenceWorkflowError("text_events style must be an object")
    unknown_style = set(style_values) - TEXT_EVENT_STYLE_KEYS
    if unknown_style:
        raise SequenceWorkflowError(
            f"text_events style does not accept: {', '.join(sorted(unknown_style))}"
        )
    defaults = TextStyleSpec()
    font_name = style_values.get("font_name", defaults.font_name)
    foreground = style_values.get("foreground", defaults.foreground)
    accent = style_values.get("accent", defaults.accent)
    for name, value in (("font_name", font_name), ("foreground", foreground), ("accent", accent)):
        if not isinstance(value, str) or not value.strip():
            raise SequenceWorkflowError(f"text_events style {name} must be a non-empty string")
    for name, value in (("foreground", foreground), ("accent", accent)):
        if not _ASS_COLOUR.match(value):
            raise SequenceWorkflowError(
                f"text_events style {name} must be an ASS colour like &H00BBGGRR"
            )
    scale = _runtime_number(
        style_values.get("emphasis_scale", defaults.emphasis_scale),
        "text_events style emphasis_scale",
        allow_zero=False,
    )
    if not 1.0 <= scale <= 3.0:
        raise SequenceWorkflowError("text_events style emphasis_scale must lie in [1, 3]")
    margin = _runtime_number(
        style_values.get("safe_margin_fraction", defaults.safe_margin_fraction),
        "text_events style safe_margin_fraction",
        allow_zero=True,
    )
    if margin > 0.2:
        raise SequenceWorkflowError(
            "text_events style safe_margin_fraction must lie in [0, 0.2]"
        )
    return tuple(cues), TextStyleSpec(font_name, foreground, accent, scale, margin)


def _music_spec(parameters: Mapping[str, object]) -> MusicSpec:
    values = dict(parameters)
    required = {"duration_policy", "gain_db"}
    allowed = required | {"fade_in_seconds", "fade_out_seconds", "duck_db"}
    if not required <= set(values) or set(values) - allowed:
        raise SequenceWorkflowError(
            "music requires duration_policy=loop_to_timeline and gain_db, "
            "with optional fade_in_seconds/fade_out_seconds/duck_db"
        )
    if values["duration_policy"] != MUSIC_DURATION_POLICY:
        raise SequenceWorkflowError("music duration_policy must be loop_to_timeline")
    gain = values["gain_db"]
    if (
        isinstance(gain, bool)
        or not isinstance(gain, (int, float))
        or not math.isfinite(gain)
        or gain < -60
        or gain > 0
    ):
        raise SequenceWorkflowError("music gain_db must be a finite number from -60 to 0")
    fade_in = _runtime_number(
        values.get("fade_in_seconds", 0.0), "music fade_in_seconds", allow_zero=True
    )
    fade_out = _runtime_number(
        values.get("fade_out_seconds", 0.0), "music fade_out_seconds", allow_zero=True
    )
    duck = values.get("duck_db")
    if duck is not None and (
        isinstance(duck, bool)
        or not isinstance(duck, (int, float))
        or not math.isfinite(duck)
        or duck < -60
        or duck >= 0
    ):
        raise SequenceWorkflowError(
            "music duck_db must be a finite number from -60 to less than 0"
        )
    return MusicSpec(
        float(gain), fade_in, fade_out, float(duck) if duck is not None else None
    )


def _fade_spec(parameters: Mapping[str, object]) -> FadeSpec:
    values = dict(parameters)
    if not values or set(values) - FADE_KEYS:
        raise SequenceWorkflowError(
            "fade accepts only from_black_seconds and to_black_seconds"
        )
    from_black = _runtime_number(
        values.get("from_black_seconds", 0.0), "fade from_black_seconds", allow_zero=True
    )
    to_black = _runtime_number(
        values.get("to_black_seconds", 0.0), "fade to_black_seconds", allow_zero=True
    )
    if from_black == 0.0 and to_black == 0.0:
        raise SequenceWorkflowError(
            "fade requires a positive from_black_seconds or to_black_seconds"
        )
    return FadeSpec(from_black, to_black)


def _segment_fit(operation, target_format) -> str | None:
    """Resolve a timeline segment's fit: its own override, else the target format.

    A per-segment ``fit`` is only meaningful once the plan declares a
    ``target_format``; without one it is a planning error.
    """

    raw = operation.parameters.get("fit")
    if raw is not None:
        if raw not in ("contain", "cover"):
            raise SequenceWorkflowError('fit must be "contain" or "cover"')
        if target_format is None:
            raise SequenceWorkflowError(
                "a segment fit requires the plan to declare a target_format"
            )
        return raw
    return target_format.fit if target_format is not None else None


def _segment_direction(
    parameters: Mapping[str, object]
) -> "tuple[str | None, str, str | None, str | None]":
    """The Visual Direction a timeline segment carries, if any.

    Four independent strings rather than a nested object: the same four names
    have to survive the EditPlan, the manifest validator and the adapter, and a
    flat vocabulary is one that all three can check identically.
    """

    composition = parameters.get("composition")
    if composition is not None and (
        not isinstance(composition, str) or composition not in COMPOSITIONS
    ):
        raise SequenceWorkflowError(
            "composition must be one of " + ", ".join(COMPOSITIONS)
        )
    bias = parameters.get("crop_bias", "center")
    if not isinstance(bias, str) or bias not in CROP_BIASES:
        raise SequenceWorkflowError("crop_bias must be one of " + ", ".join(CROP_BIASES))
    zone = parameters.get("text_zone")
    if zone is not None and (not isinstance(zone, str) or zone not in TEXT_ZONES):
        raise SequenceWorkflowError("text_zone must be one of " + ", ".join(TEXT_ZONES))
    if composition == "text_focus" and zone is None:
        raise SequenceWorkflowError("a text_focus composition requires a text_zone")
    grade = parameters.get("grade")
    if grade is not None and (
        not isinstance(grade, str) or grade not in GRADE_INTENSITIES
    ):
        raise SequenceWorkflowError(
            "grade must be one of " + ", ".join(GRADE_INTENSITIES)
        )
    return composition, bias, zone, grade


def _direction_spec(parameters: Mapping[str, object]) -> DirectionSpec:
    """Parse the plan-level ``visual_direction`` operation into adapter numbers.

    The grade lives once on the plan rather than on all sixty segments: a
    segment names an *intensity*, this says what that intensity means.
    """

    values = dict(parameters)
    allowed = (
        {"style_id", "grade", "intensities"} | VISUAL_DIRECTION_GEOMETRY_KEYS
    )
    unknown = set(values) - allowed
    if unknown:
        raise SequenceWorkflowError(
            f"visual_direction does not accept: {', '.join(sorted(unknown))}"
        )
    style_id = values.get("style_id", "visual-direction-v1")
    if not isinstance(style_id, str) or not style_id.strip():
        raise SequenceWorkflowError("visual_direction style_id must be a non-empty string")
    kwargs: dict[str, object] = {}
    grade = values.get("grade", {})
    if not isinstance(grade, Mapping):
        raise SequenceWorkflowError("visual_direction grade must be an object")
    unknown_grade = set(grade) - VISUAL_DIRECTION_GRADE_KEYS
    if unknown_grade:
        raise SequenceWorkflowError(
            f"visual_direction grade does not accept: {', '.join(sorted(unknown_grade))}"
        )
    for key, value in grade.items():
        kwargs[key] = _runtime_number(value, f"visual_direction grade {key}", allow_zero=True)
    for key in VISUAL_DIRECTION_GEOMETRY_KEYS:
        if key in values:
            kwargs[key] = _runtime_number(
                values[key], f"visual_direction {key}", allow_zero=True
            )
    intensities = values.get("intensities")
    if intensities is not None:
        if not isinstance(intensities, Mapping):
            raise SequenceWorkflowError("visual_direction intensities must be an object")
        if set(intensities) != set(GRADE_INTENSITIES):
            raise SequenceWorkflowError(
                "visual_direction intensities must define "
                + ", ".join(GRADE_INTENSITIES)
            )
        parsed = {
            name: _runtime_number(
                value, f"visual_direction intensity {name}", allow_zero=True
            )
            for name, value in intensities.items()
        }
        if parsed["none"] != 0.0:
            raise SequenceWorkflowError('visual_direction intensity "none" must be 0')
        kwargs["intensities"] = parsed
    try:
        return DirectionSpec(**kwargs)  # type: ignore[arg-type]
    except FFmpegError as exc:
        raise SequenceWorkflowError(str(exc)) from exc


def _image_duration(parameters: Mapping[str, object]) -> float:
    values = dict(parameters)
    if "duration_seconds" not in values or set(values) - (
        {"duration_seconds", "fit", "motion"} | SEGMENT_DIRECTION_KEYS
    ):
        raise SequenceWorkflowError(
            "image_clip requires a duration_seconds parameter and an optional fit, "
            "motion and visual direction"
        )
    duration = _runtime_number(
        values["duration_seconds"], "image_clip duration_seconds", allow_zero=False
    )
    if duration > IMAGE_MAX_DURATION_SECONDS:
        raise SequenceWorkflowError(
            f"image_clip duration_seconds must not exceed {IMAGE_MAX_DURATION_SECONDS} seconds"
        )
    return duration


def _image_motion(parameters: Mapping[str, object]) -> str | None:
    """Resolve an ``image_clip``'s optional Ken Burns motion.

    Absent leaves the still frozen (current behaviour). A value must be one of
    the fixed set the adapter knows how to translate into a ``zoompan`` pass.
    """

    raw = parameters.get("motion")
    if raw is None:
        return None
    if raw not in IMAGE_MOTIONS + DIRECTION_MOTIONS:
        raise SequenceWorkflowError(
            "image_clip motion must be one of "
            + ", ".join(IMAGE_MOTIONS + DIRECTION_MOTIONS)
        )
    return raw


def _segment_duration(segment: SequenceClip | SequenceImage) -> float:
    if isinstance(segment, SequenceImage):
        return segment.duration_seconds
    return segment.end_seconds - segment.start_seconds


class PlanOperations(NamedTuple):
    """Everything a video-sequence plan declares, read once and named.

    A named tuple rather than a bare one: this has grown past the point where
    a reader can count positions, and the layers keep arriving (captions, then
    emphasis, now visual direction).
    """

    segments: tuple[SequenceClip | SequenceImage, ...]
    narration_path: str | None
    narration_text: NarrationTextSpec | None
    captions: tuple[CaptionCue, ...]
    caption_source: str | None
    captions_from_narration: bool
    music_path: str | None
    music: MusicSpec | None
    fade: FadeSpec | None
    text_events: tuple[TextEventCue, ...]
    text_style: TextStyleSpec | None
    direction: DirectionSpec | None


def _operations_from_plan(plan: EditPlan) -> PlanOperations:
    if len(plan.operations) < 2:
        raise SequenceWorkflowError("video-sequence requires at least two operations")
    if Path(plan.output_path).suffix.lower() != ".mp4":
        raise SequenceWorkflowError("video-sequence requires an .mp4 output_path")
    segments: list[SequenceClip | SequenceImage] = []
    used_sources = set()
    narration_path: str | None = None
    narration_text: NarrationTextSpec | None = None
    captions: tuple[CaptionCue, ...] = ()
    caption_source: str | None = None
    captions_from_narration = False
    captions_seen = False
    text_events: tuple[TextEventCue, ...] = ()
    text_style: TextStyleSpec | None = None
    text_events_seen = False
    direction: DirectionSpec | None = None
    music_path: str | None = None
    music: MusicSpec | None = None
    fade: FadeSpec | None = None
    for index, operation in enumerate(plan.operations):
        if operation.kind == NARRATION_KIND:
            if index != len(plan.operations) - 1:
                raise SequenceWorkflowError("narration must be the final operation")
            if narration_path is not None or narration_text is not None:
                raise SequenceWorkflowError("video-sequence accepts at most one narration")
            if operation.start_seconds is not None or operation.end_seconds is not None:
                raise SequenceWorkflowError("narration timing is fixed at zero in v1")
            params = dict(operation.parameters)
            if params.get("duration_policy") != "match_timeline":
                raise SequenceWorkflowError(
                    "narration requires duration_policy=match_timeline"
                )
            if operation.source is not None:
                if set(params) != {"duration_policy"}:
                    raise SequenceWorkflowError(
                        "narration from a source accepts only duration_policy=match_timeline"
                    )
                narration_path = operation.source
                used_sources.add(operation.source)
            else:
                unknown = set(params) - NARRATION_TEXT_KEYS
                if unknown:
                    raise SequenceWorkflowError(
                        "narration text accepts only text, voice, speed, lang "
                        "and lead_in_seconds"
                    )
                text = params.get("text")
                if not isinstance(text, str) or not text.strip():
                    raise SequenceWorkflowError("narration text must be a non-empty string")
                lead_in = _runtime_number(
                    params.get("lead_in_seconds", 0.0),
                    "narration lead_in_seconds",
                    allow_zero=True,
                )
                narration_text = NarrationTextSpec(
                    text=text,
                    voice=params.get("voice", NARRATION_DEFAULT_VOICE),
                    speed=params.get("speed", NARRATION_DEFAULT_SPEED),
                    lang=params.get("lang", NARRATION_DEFAULT_LANG),
                    lead_in_seconds=lead_in,
                )
            continue
        if operation.kind == CAPTIONS_KIND:
            if captions_seen:
                raise SequenceWorkflowError("video-sequence accepts at most one captions operation")
            if len(segments) < 2:
                raise SequenceWorkflowError("captions must follow all timeline segments")
            if music_path is not None:
                raise SequenceWorkflowError("captions must precede music")
            if operation.start_seconds is not None or operation.end_seconds is not None:
                raise SequenceWorkflowError("captions timing belongs to its items")
            if operation.source is not None:
                suffix = Path(operation.source).suffix.lower()
                if suffix not in CAPTION_SUBTITLE_FORMATS:
                    raise SequenceWorkflowError(
                        "captions source must be a .srt or .vtt file"
                    )
                if dict(operation.parameters) != {"style": CAPTIONS_STYLE}:
                    raise SequenceWorkflowError(
                        "a captions file accepts only style=bottom_box, no items"
                    )
                caption_source = operation.source
                used_sources.add(operation.source)
            elif dict(operation.parameters) == {"style": CAPTIONS_STYLE, "from": "narration"}:
                captions_from_narration = True
            else:
                captions = _validate_caption_cues(
                    _inline_caption_triples(operation.parameters)
                )
            captions_seen = True
            continue
        if operation.kind == TEXT_EVENTS_KIND:
            if text_events_seen:
                raise SequenceWorkflowError(
                    "video-sequence accepts at most one text_events operation"
                )
            if len(segments) < 2:
                raise SequenceWorkflowError("text_events must follow all timeline segments")
            if music_path is not None:
                raise SequenceWorkflowError("text_events must precede music")
            if operation.source is not None:
                raise SequenceWorkflowError("text_events does not take a source")
            if operation.start_seconds is not None or operation.end_seconds is not None:
                raise SequenceWorkflowError("text_events timing belongs to its items")
            text_events, text_style = _text_event_cues(operation.parameters)
            text_events_seen = True
            continue
        if operation.kind == VISUAL_DIRECTION_KIND:
            if direction is not None:
                raise SequenceWorkflowError(
                    "video-sequence accepts at most one visual_direction operation"
                )
            if len(segments) < 2:
                raise SequenceWorkflowError(
                    "visual_direction must follow all timeline segments"
                )
            if operation.source is not None:
                raise SequenceWorkflowError("visual_direction does not take a source")
            if operation.start_seconds is not None or operation.end_seconds is not None:
                raise SequenceWorkflowError("visual_direction has no timeline range")
            direction = _direction_spec(operation.parameters)
            continue
        if operation.kind == MUSIC_KIND:
            if music_path is not None:
                raise SequenceWorkflowError("video-sequence accepts at most one music operation")
            if len(segments) < 2:
                raise SequenceWorkflowError("music must follow all timeline segments")
            if operation.source is None:
                raise SequenceWorkflowError("music must declare a source")
            if operation.start_seconds is not None or operation.end_seconds is not None:
                raise SequenceWorkflowError("music timing is fixed at zero in v1")
            music = _music_spec(operation.parameters)
            music_path = operation.source
            used_sources.add(operation.source)
            continue
        if operation.kind == FADE_KIND:
            if fade is not None:
                raise SequenceWorkflowError("video-sequence accepts at most one fade operation")
            if len(segments) < 2:
                raise SequenceWorkflowError("fade must follow all timeline segments")
            if operation.source is not None:
                raise SequenceWorkflowError("fade does not take a source")
            if operation.start_seconds is not None or operation.end_seconds is not None:
                raise SequenceWorkflowError("fade has no timeline range")
            fade = _fade_spec(operation.parameters)
            continue
        if operation.kind == IMAGE_KIND:
            if (
                captions_seen
                or text_events_seen
                or music_path is not None
                or fade is not None
                or direction is not None
            ):
                raise SequenceWorkflowError(
                    "all timeline segments must precede captions, music and fades"
                )
            if operation.source is None:
                raise SequenceWorkflowError("image_clip must declare a source")
            if operation.start_seconds is not None or operation.end_seconds is not None:
                raise SequenceWorkflowError(
                    "image_clip has no timeline range; use a duration_seconds parameter"
                )
            composition, bias, zone, grade = _segment_direction(operation.parameters)
            segments.append(
                SequenceImage(
                    operation.source,
                    _image_duration(operation.parameters),
                    _segment_fit(operation, plan.target_format),
                    _image_motion(operation.parameters),
                    composition,
                    bias,
                    zone,
                    grade,
                )
            )
            used_sources.add(operation.source)
            continue
        if operation.kind != OPERATION_KIND:
            raise SequenceWorkflowError(
                f"video-sequence does not support operation kind: {operation.kind}"
            )
        if (
            captions_seen
            or text_events_seen
            or music_path is not None
            or fade is not None
            or direction is not None
        ):
            raise SequenceWorkflowError(
                "all timeline segments must precede captions, music and fades"
            )
        if operation.source is None:
            raise SequenceWorkflowError("sequence_clip must declare a source")
        if operation.start_seconds is None or operation.end_seconds is None:
            raise SequenceWorkflowError("sequence_clip requires start_seconds and end_seconds")
        if set(operation.parameters) - ({"fit"} | SEGMENT_DIRECTION_KEYS):
            raise SequenceWorkflowError(
                "sequence_clip accepts only an optional fit and visual direction"
            )
        composition, bias, zone, grade = _segment_direction(operation.parameters)
        if composition in ("inset", "layered", "split"):
            raise SequenceWorkflowError(
                f"composition {composition} is only available for an image_clip"
            )
        segments.append(
            SequenceClip(
                operation.source,
                operation.start_seconds,
                operation.end_seconds,
                _segment_fit(operation, plan.target_format),
                composition,
                bias,
                zone,
                grade,
            )
        )
        used_sources.add(operation.source)
    if len(segments) < 2:
        raise SequenceWorkflowError("video-sequence requires at least two timeline segments")
    if plan.target_format is None and not any(
        isinstance(segment, SequenceClip) for segment in segments
    ):
        # Without an explicit target_format the canvas is inferred from the video
        # clips, so an all-image timeline has nothing to size itself against.
        raise SequenceWorkflowError(
            "an all-image video-sequence requires the plan to declare a target_format"
        )
    if captions_from_narration and narration_text is None:
        raise SequenceWorkflowError(
            "captions from=narration require a narration text operation"
        )
    if used_sources != set(plan.sources):
        raise SequenceWorkflowError("every declared source must be used by the sequence")
    if (
        narration_path is not None
        and music_path is not None
        and _normalized(narration_path) == _normalized(music_path)
    ):
        raise SequenceWorkflowError("narration and music must use distinct sources")
    return PlanOperations(
        tuple(segments),
        narration_path,
        narration_text,
        captions,
        caption_source,
        captions_from_narration,
        music_path,
        music,
        fade,
        text_events,
        text_style,
        direction,
    )


def _source_probes(report: PreflightReport, plan: EditPlan) -> dict[str, MediaProbe]:
    if len(report.sources) != len(plan.sources):
        raise SequenceWorkflowError("preflight did not inspect every sequence source")
    return {_normalized(source.source_path): source for source in report.sources}


def _segment_dimensions(probes: dict[str, MediaProbe], source_path: str) -> tuple[int, int]:
    source = probes.get(_normalized(source_path))
    if source is None:
        raise SequenceWorkflowError(f"preflight omitted sequence source: {source_path}")
    video_streams = tuple(stream for stream in source.streams if stream.codec_type == "video")
    if not video_streams:
        raise SequenceWorkflowError(f"sequence source has no video stream: {source.source_path}")
    stream = video_streams[0]
    if stream.width is None or stream.height is None:
        raise SequenceWorkflowError(
            f"sequence source dimensions are unavailable: {source.source_path}"
        )
    return stream.width, stream.height


def _video_shape(
    probes: dict[str, MediaProbe],
    segments: tuple[SequenceClip | SequenceImage, ...],
    target_format=None,
) -> tuple[int, int]:
    """Return the timeline canvas.

    Every source must expose a readable video stream. With a ``target_format``
    the canvas is that explicit delivery resolution and heterogeneous clip
    sizes are allowed; without one the legacy rule stands — the video clips
    must all share one pixel size, which becomes the canvas.
    """

    for segment in segments:
        _segment_dimensions(probes, segment.source_path)
    if target_format is not None:
        return target_format.width, target_format.height
    clip_shapes = {
        _segment_dimensions(probes, segment.source_path)
        for segment in segments
        if isinstance(segment, SequenceClip)
    }
    if len(clip_shapes) != 1:
        raise SequenceWorkflowError("video-sequence v1 requires matching clip dimensions")
    return next(iter(clip_shapes))


def _synthesise_narration(
    spec: NarrationTextSpec,
    output_path: str,
    expected_duration: float,
    tolerance: float,
    timeout: float,
) -> tuple[str, str, str, float]:
    """Render narration text to a temp WAV; return (path, text_sha256, temp_dir, seconds)."""

    parent = Path(output_path).expanduser().resolve().parent
    parent.mkdir(parents=True, exist_ok=True)
    synth_dir = tempfile.mkdtemp(prefix=f".{Path(output_path).stem}-tts-", dir=str(parent))
    try:
        target = Path(synth_dir) / "narration.wav"
        try:
            artifact = synthesize_narration(
                spec.text,
                target,
                voice=spec.voice,
                speed=spec.speed,
                lang=spec.lang,
                timeout_seconds=timeout,
            )
        except KokoroError as exc:
            raise SequenceWorkflowError(f"narration synthesis failed: {exc}") from exc
        try:
            probe = probe_media(artifact.output_path)
        except ProbeError as exc:
            raise SequenceWorkflowError(
                f"could not inspect synthesised narration: {exc}"
            ) from exc
        seconds = probe.duration_seconds
        if seconds is None or seconds <= 0:
            raise SequenceWorkflowError("synthesised narration has no usable duration")
        if spec.lead_in_seconds + seconds > expected_duration + tolerance:
            raise SequenceWorkflowError(
                f"a {spec.lead_in_seconds:.3f}s lead-in plus {seconds:.3f}s of narration "
                f"overruns the {expected_duration:.3f}s timeline; shorten the narration text"
            )
        return artifact.output_path, artifact.text_sha256, synth_dir, float(seconds)
    except BaseException:
        shutil.rmtree(synth_dir, ignore_errors=True)
        raise


def _validate_narration(
    probes: dict[str, MediaProbe], narration_path: str, expected_duration: float, tolerance: float
) -> float:
    source = probes.get(_normalized(narration_path))
    if source is None:
        raise SequenceWorkflowError("preflight omitted the narration source")
    audio_streams = tuple(stream for stream in source.streams if stream.codec_type == "audio")
    non_audio_streams = tuple(stream for stream in source.streams if stream.codec_type != "audio")
    if len(audio_streams) != 1 or non_audio_streams:
        raise SequenceWorkflowError(
            "narration v1 requires exactly one audio stream and no other streams"
        )
    if source.duration_seconds is None or source.duration_seconds <= 0:
        raise SequenceWorkflowError("narration duration is unavailable or invalid")
    if abs(source.duration_seconds - expected_duration) > tolerance:
        raise SequenceWorkflowError(
            f"narration duration {source.duration_seconds} must match timeline duration "
            f"{expected_duration} within {tolerance} seconds"
        )
    return source.duration_seconds


def _validate_music(probes: dict[str, MediaProbe], music_path: str) -> None:
    source = probes.get(_normalized(music_path))
    if source is None:
        raise SequenceWorkflowError("preflight omitted the music source")
    audio_streams = tuple(stream for stream in source.streams if stream.codec_type == "audio")
    non_audio_streams = tuple(stream for stream in source.streams if stream.codec_type != "audio")
    if len(audio_streams) != 1 or non_audio_streams:
        raise SequenceWorkflowError(
            "music v1 requires exactly one audio stream and no other streams"
        )
    if source.duration_seconds is None or source.duration_seconds <= 0:
        raise SequenceWorkflowError("music duration is unavailable or invalid")


def _normalized(value: str) -> str:
    return os.path.normcase(str(Path(value).expanduser().resolve()))


def run_sequence_workflow(
    plan: EditPlan,
    *,
    timeout_seconds: float = 300,
    duration_tolerance_seconds: float = 0.15,
    preflight: Callable[[EditPlan], PreflightReport] | None = None,
    before_compose: Callable[[EditPlan], None] | None = None,
    compose: Callable[..., SequenceArtifact] | None = None,
    validate: Callable[..., SequenceValidationReport] | None = None,
) -> SequenceWorkflowReport:
    """Execute one strict ordered sequence plan without modifying its sources."""

    if not isinstance(plan, EditPlan):
        raise TypeError("plan must be an EditPlan")
    timeout = _runtime_number(timeout_seconds, "timeout_seconds", allow_zero=False)
    tolerance = _runtime_number(
        duration_tolerance_seconds,
        "duration_tolerance_seconds",
        allow_zero=True,
    )
    if Path(plan.output_path).name.lower() == "final.mp4":
        raise SequenceWorkflowError(
            "final.mp4 requires run_final_sequence_workflow"
        )
    (
        segments,
        narration_path,
        narration_text,
        captions,
        caption_source,
        captions_from_narration,
        music_path,
        music,
        fade,
        text_events,
        text_style,
        direction,
    ) = _operations_from_plan(plan)
    if caption_source is not None:
        captions = _caption_cues_from_file(caption_source)
    expected_duration = sum(_segment_duration(segment) for segment in segments)
    expected_image_count = sum(1 for segment in segments if isinstance(segment, SequenceImage))
    if (
        fade is not None
        and fade.from_black_seconds + fade.to_black_seconds > expected_duration
    ):
        raise SequenceWorkflowError(
            "fade must not be longer than the sequence duration"
        )
    if music is not None and music.fade_in_seconds + music.fade_out_seconds > expected_duration:
        raise SequenceWorkflowError(
            "music fades must not be longer than the sequence duration"
        )
    if (
        music is not None
        and music.duck_db is not None
        and narration_path is None
        and narration_text is None
    ):
        raise SequenceWorkflowError("music duck_db requires a narration operation")
    if captions and captions[-1].end_seconds > expected_duration:
        raise SequenceWorkflowError(
            "caption end_seconds must not exceed the sequence duration"
        )
    inspect_plan = preflight or preflight_edit_plan
    preflight_report = inspect_plan(plan)
    if not isinstance(preflight_report, PreflightReport):
        raise TypeError("preflight must return a PreflightReport")
    if preflight_report.plan_id != plan.plan_id:
        raise SequenceWorkflowError("preflight returned a report for an unexpected plan")
    if not preflight_report.valid:
        issue_codes = ", ".join(issue.code for issue in preflight_report.issues) or "unknown"
        raise SequenceWorkflowError(f"preflight rejected plan {plan.plan_id}: {issue_codes}")
    probes = _source_probes(preflight_report, plan)
    canvas = _video_shape(probes, segments, plan.target_format)
    synth_dir: str | None = None
    narration_text_sha256: str | None = None
    # the voice track's own length, so a ducked bed knows when to come back up
    narration_duration: float | None = None
    # a lead-in belongs to text-mode narration only; a narration file carries
    # any leading silence itself and must still match the timeline duration
    narration_lead_in = (
        narration_text.lead_in_seconds if narration_text is not None else 0.0
    )
    if narration_lead_in >= expected_duration:
        raise SequenceWorkflowError(
            "narration lead_in_seconds must be shorter than the sequence duration"
        )
    try:
        if narration_text is not None:
            (
                narration_path,
                narration_text_sha256,
                synth_dir,
                narration_seconds,
            ) = _synthesise_narration(
                narration_text, plan.output_path, expected_duration, tolerance, timeout
            )
            narration_duration = narration_seconds
            if captions_from_narration:
                lead_in = narration_text.lead_in_seconds
                span = min(narration_seconds, expected_duration - lead_in)
                # where the synthesised voice actually stops, so a line break can
                # land on a real pause instead of on an estimated one
                try:
                    silences = detect_silences(narration_path, timeout_seconds=timeout)
                except FFmpegError as exc:
                    raise SequenceWorkflowError(
                        f"could not measure the narration pauses: {exc}"
                    ) from exc
                try:
                    # cues are laid out over the spoken span, pulled onto the
                    # measured pauses, then shifted so the track starts with the
                    # voice rather than with the timeline
                    captions = _validate_caption_cues(
                        tuple(
                            (text, start + lead_in, end + lead_in)
                            for text, start, end in align_cues_to_silences(
                                captions_from_text(narration_text.text, span),
                                silences,
                            )
                        )
                    )
                except SubtitleParseError as exc:
                    raise SequenceWorkflowError(
                        f"could not derive captions from the narration: {exc}"
                    ) from exc
                if captions and captions[-1].end_seconds > expected_duration:
                    raise SequenceWorkflowError(
                        "derived caption end_seconds must not exceed the sequence duration"
                    )
        elif narration_path is not None:
            narration_duration = _validate_narration(
                probes, narration_path, expected_duration, tolerance
            )
        if music_path is not None:
            _validate_music(probes, music_path)
        if before_compose is not None:
            before_compose(plan)

        create_artifact = compose or compose_video_sequence
        try:
            compose_kwargs = {"timeout_seconds": timeout}
            if narration_path is not None:
                compose_kwargs["narration_path"] = narration_path
            if narration_lead_in:
                compose_kwargs["narration_lead_in_seconds"] = narration_lead_in
            if captions:
                compose_kwargs["captions"] = captions
            if text_events:
                compose_kwargs["text_events"] = text_events
                if text_style is not None:
                    compose_kwargs["text_style"] = text_style
            if music_path is not None and music is not None:
                compose_kwargs["music_path"] = music_path
                compose_kwargs["music_gain_db"] = music.gain_db
                compose_kwargs["music_fade_in_seconds"] = music.fade_in_seconds
                compose_kwargs["music_fade_out_seconds"] = music.fade_out_seconds
                if music.duck_db is not None:
                    compose_kwargs["music_duck_db"] = music.duck_db
                    if narration_duration is not None:
                        compose_kwargs["narration_duration_seconds"] = narration_duration
            if fade is not None:
                compose_kwargs["video_fade_in_seconds"] = fade.from_black_seconds
                compose_kwargs["video_fade_out_seconds"] = fade.to_black_seconds
            if direction is not None:
                compose_kwargs["direction"] = direction
            # the clip canvas is always forwarded: images letter-box onto it and
            # captions use it as the pixel-accurate layout frame.
            compose_kwargs["canvas"] = canvas
            artifact = create_artifact(segments, plan.output_path, **compose_kwargs)
        except FFmpegError as exc:
            raise SequenceWorkflowError(f"video sequence composition failed: {exc}") from exc
        if not isinstance(artifact, SequenceArtifact):
            raise TypeError("compose must return a SequenceArtifact")
        if _normalized(artifact.output_path) != _normalized(plan.output_path):
            raise SequenceWorkflowError("compose returned an artifact at an unexpected output path")
        expected_sources = tuple(_normalized(clip.source_path) for clip in segments)
        actual_sources = tuple(_normalized(source) for source in artifact.source_paths)
        if actual_sources != expected_sources:
            raise SequenceWorkflowError("compose returned an artifact for an unexpected clip order")
        expected_narration = _normalized(narration_path) if narration_path is not None else None
        actual_narration = (
            _normalized(artifact.narration_source_path)
            if artifact.narration_source_path is not None
            else None
        )
        if (
            actual_narration != expected_narration
            or artifact.narration_lead_in_seconds != narration_lead_in
        ):
            raise SequenceWorkflowError("compose returned unexpected narration metadata")
        if artifact.caption_count != len(captions):
            raise SequenceWorkflowError("compose returned unexpected caption metadata")
        if artifact.text_event_count != len(text_events):
            raise SequenceWorkflowError("compose returned unexpected text event metadata")
        if artifact.image_count != expected_image_count:
            raise SequenceWorkflowError("compose returned unexpected image metadata")
        expected_music = _normalized(music_path) if music_path is not None else None
        actual_music = (
            _normalized(artifact.music_source_path)
            if artifact.music_source_path is not None
            else None
        )
        expected_gain = music.gain_db if music is not None else None
        expected_fade_in = music.fade_in_seconds if music is not None else 0.0
        expected_fade_out = music.fade_out_seconds if music is not None else 0.0
        expected_duck = music.duck_db if music is not None else None
        if (
            actual_music != expected_music
            or artifact.music_gain_db != expected_gain
            or artifact.music_fade_in_seconds != expected_fade_in
            or artifact.music_fade_out_seconds != expected_fade_out
            or artifact.music_duck_db != expected_duck
        ):
            raise SequenceWorkflowError("compose returned unexpected music metadata")
        expected_video_fade_in = fade.from_black_seconds if fade is not None else 0.0
        expected_video_fade_out = fade.to_black_seconds if fade is not None else 0.0
        if (
            artifact.video_fade_in_seconds != expected_video_fade_in
            or artifact.video_fade_out_seconds != expected_video_fade_out
        ):
            raise SequenceWorkflowError("compose returned unexpected fade metadata")
        if artifact.duration_seconds != expected_duration or artifact.file_size_bytes <= 0:
            raise SequenceWorkflowError("compose returned inconsistent artifact metadata")

        if narration_text_sha256 is not None:
            # the narration audio is a transient synthesis; the text digest and
            # the recorded voice/speed/lang keep the render reproducible.
            artifact = replace(
                artifact,
                narration_source_path=None,
                narration_text_sha256=narration_text_sha256,
            )

        inspect_artifact = validate or validate_sequence_artifact
        validation = inspect_artifact(
            artifact,
            duration_tolerance_seconds=tolerance,
        )
        if not isinstance(validation, SequenceValidationReport):
            raise TypeError("validate must return a SequenceValidationReport")
        if validation.artifact != artifact:
            raise SequenceWorkflowError("validation returned a report for an unexpected artifact")
        return SequenceWorkflowReport(
            plan_id=plan.plan_id,
            operation_ids=tuple(operation.operation_id for operation in plan.operations),
            preflight=preflight_report,
            artifact=artifact,
            validation=validation,
        )
    finally:
        if synth_dir is not None:
            shutil.rmtree(synth_dir, ignore_errors=True)


def run_final_sequence_workflow(
    plan: EditPlan,
    *,
    timeout_seconds: float = 300,
    duration_tolerance_seconds: float = 0.15,
    preflight: Callable[[EditPlan], PreflightReport] | None = None,
    before_compose: Callable[[EditPlan], None] | None = None,
    compose: Callable[..., SequenceArtifact] | None = None,
    validate: Callable[..., SequenceValidationReport] | None = None,
) -> SequenceWorkflowReport:
    """Render in staging and publish final.mp4 only after technical validation."""

    if not isinstance(plan, EditPlan):
        raise TypeError("plan must be an EditPlan")
    final_path = Path(plan.output_path).expanduser().resolve()
    if final_path.name.lower() != "final.mp4":
        raise SequenceWorkflowError("final sequence output_path must be named final.mp4")
    if final_path.exists():
        raise SequenceWorkflowError(f"final output already exists: {final_path}")
    try:
        final_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise SequenceWorkflowError(
            f"could not create final output directory: {final_path.parent}"
        ) from exc

    published = False
    try:
        with tempfile.TemporaryDirectory(
            prefix=f".{final_path.stem}-staging-",
            dir=final_path.parent,
        ) as staging_directory:
            staged_path = Path(staging_directory) / "render.mp4"
            staged_plan = EditPlan(
                plan.plan_id,
                plan.brief_id,
                plan.sources,
                str(staged_path),
                plan.operations,
                plan.target_format,
            )
            staged_report = run_sequence_workflow(
                staged_plan,
                timeout_seconds=timeout_seconds,
                duration_tolerance_seconds=duration_tolerance_seconds,
                preflight=preflight,
                before_compose=(
                    (lambda _staged_plan: before_compose(plan))
                    if before_compose is not None
                    else None
                ),
                compose=compose,
                validate=validate,
            )
            if not staged_report.valid:
                issue_codes = ", ".join(
                    issue.code for issue in staged_report.validation.issues
                ) or "unknown"
                raise SequenceWorkflowError(
                    f"final render failed technical validation: {issue_codes}"
                )
            try:
                os.link(staged_path, final_path)
                published = True
            except FileExistsError as exc:
                raise SequenceWorkflowError(
                    f"final output already exists: {final_path}"
                ) from exc
            except OSError as exc:
                raise SequenceWorkflowError(
                    f"could not publish validated final output: {final_path}"
                ) from exc

            final_artifact = replace(
                staged_report.artifact,
                output_path=str(final_path),
                file_size_bytes=final_path.stat().st_size,
            )
            inspect_artifact = validate or validate_sequence_artifact
            final_validation = inspect_artifact(
                final_artifact,
                duration_tolerance_seconds=staged_report.validation.duration_tolerance_seconds,
            )
            if not isinstance(final_validation, SequenceValidationReport):
                raise TypeError("validate must return a SequenceValidationReport")
            if final_validation.artifact != final_artifact:
                raise SequenceWorkflowError(
                    "validation returned a report for an unexpected final artifact"
                )
            if not final_validation.valid:
                issue_codes = ", ".join(
                    issue.code for issue in final_validation.issues
                ) or "unknown"
                raise SequenceWorkflowError(
                    f"published final failed technical validation: {issue_codes}"
                )
            return SequenceWorkflowReport(
                plan_id=plan.plan_id,
                operation_ids=staged_report.operation_ids,
                preflight=staged_report.preflight,
                artifact=final_artifact,
                validation=final_validation,
                publication="final",
            )
    except Exception:
        if published:
            try:
                final_path.unlink(missing_ok=True)
            except OSError as cleanup_error:
                raise SequenceWorkflowError(
                    f"could not remove rejected final output: {final_path}"
                ) from cleanup_error
        raise
