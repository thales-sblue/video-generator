"""Read-only integrity validation for persisted RenderManifest contracts."""

from __future__ import annotations

import json
import math
import os
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path

from video_generator.domain import EditPlan, FileFingerprint, RenderManifest
from video_generator.manifests import ManifestError, fingerprint_file, fingerprint_plan


@dataclass(frozen=True, slots=True)
class ManifestValidationIssue:
    code: str
    message: str
    path: str | None = None


@dataclass(frozen=True, slots=True)
class ManifestValidationReport:
    manifest_id: str
    integrity_valid: bool
    technical_validation_valid: bool
    editorial_review: str
    issues: tuple[ManifestValidationIssue, ...]
    recorded_technical_issues: tuple[str, ...]

    @property
    def technically_ready(self) -> bool:
        return self.integrity_valid and self.technical_validation_valid

    def to_dict(self) -> dict[str, object]:
        return {
            "manifest_id": self.manifest_id,
            "integrity_valid": self.integrity_valid,
            "technical_validation_valid": self.technical_validation_valid,
            "technically_ready": self.technically_ready,
            "editorial_review": self.editorial_review,
            "issues": [asdict(issue) for issue in self.issues],
            "recorded_technical_issues": list(self.recorded_technical_issues),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _normalized(path: str | Path) -> str:
    return os.path.normcase(str(Path(path).expanduser().resolve()))


def _check_fingerprint(
    expected: FileFingerprint,
    issues: list[ManifestValidationIssue],
) -> None:
    try:
        actual = fingerprint_file(expected.path)
    except ManifestError as exc:
        issues.append(ManifestValidationIssue("file_unavailable", str(exc), expected.path))
        return
    if actual.file_size_bytes != expected.file_size_bytes:
        issues.append(
            ManifestValidationIssue(
                "file_size_mismatch",
                f"file size changed from {expected.file_size_bytes} to {actual.file_size_bytes} bytes",
                expected.path,
            )
        )
    if actual.sha256 != expected.sha256:
        issues.append(
            ManifestValidationIssue(
                "file_hash_mismatch",
                "file content no longer matches its recorded SHA-256",
                expected.path,
            )
        )


_SUBTITLE_SUFFIXES = {".srt", ".vtt"}

# The trailing operations a video-sequence plan may carry, in the only order
# the workflow accepts them. Each is optional; the walk below takes them
# strictly in this sequence, so a plan whose tail is out of order is still
# refused for what it is rather than passing by accident.
_SEGMENT_KINDS = ("sequence_clip", "image_clip")
_KEN_BURNS_MOTIONS = (
    "zoom_in", "zoom_out", "pan_left", "pan_right", "pan_up", "pan_down",
)
_DIRECTION_MOTIONS = (
    "static_hold", "slow_push_in", "slow_pull_out", "lateral_drift", "detail_push",
)
_COMPOSITIONS = (
    "fullscreen", "extreme_crop", "inset", "layered", "split", "text_focus",
)
_STILL_ONLY_COMPOSITIONS = ("inset", "layered", "split")
_CROP_BIASES = ("center", "top", "bottom", "left", "right")
_TEXT_ZONES = ("top", "middle", "lower")
_GRADE_INTENSITIES = ("none", "subtle", "standard", "strong")
_SEGMENT_DIRECTION_KEYS = {"composition", "crop_bias", "text_zone", "grade"}
_TEXT_EVENT_POSITIONS = ("top", "middle", "lower")
_TEXT_EVENT_ANIMATIONS = ("fade", "pop", "slide", "highlight")
_TEXT_EVENT_STYLE_KEYS = {
    "font_name", "foreground", "accent", "emphasis_scale", "safe_margin_fraction",
}
_ASS_COLOUR = re.compile(r"^&H[0-9A-Fa-f]{6}(?:[0-9A-Fa-f]{2})?$")
_VISUAL_DIRECTION_GRADE_KEYS = {
    "saturation", "shadow_density", "highlight_gain", "highlight_ceiling",
    "cool_shift", "grain", "vignette_angle", "luminance_pull",
}
_VISUAL_DIRECTION_GEOMETRY_KEYS = {
    "background_blur_sigma", "background_darkening", "inset_scale",
    "extreme_crop_zoom", "split_gap_fraction", "scrim_opacity",
    "scrim_height_fraction", "push_travel", "detail_push_travel", "drift_travel",
}


def _finite(value: object, *, allow_negative: bool = False) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
        and (allow_negative or value >= 0)
    )


def _segment_direction_ok(parameters: Mapping[str, object], *, is_image: bool) -> bool:
    """Whether a segment's optional Visual Direction keys are well-formed.

    The same four names the workflow and the adapter check, checked here too:
    a manifest that vouches for a plan has to understand every key that plan
    can change the picture with.
    """

    composition = parameters.get("composition")
    if composition is not None:
        if composition not in _COMPOSITIONS:
            return False
        if not is_image and composition in _STILL_ONLY_COMPOSITIONS:
            return False
        if composition == "text_focus" and parameters.get("text_zone") is None:
            return False
    if parameters.get("crop_bias", "center") not in _CROP_BIASES:
        return False
    zone = parameters.get("text_zone")
    if zone is not None and zone not in _TEXT_ZONES:
        return False
    grade = parameters.get("grade")
    if grade is not None and grade not in _GRADE_INTENSITIES:
        return False
    return True


def _visual_direction_matches(operation) -> bool:
    parameters = dict(operation.parameters)
    if (
        operation.source is not None
        or operation.start_seconds is not None
        or operation.end_seconds is not None
    ):
        return False
    allowed = {"style_id", "grade", "intensities"} | _VISUAL_DIRECTION_GEOMETRY_KEYS
    if set(parameters) - allowed:
        return False
    style_id = parameters.get("style_id", "visual-direction-v1")
    if not isinstance(style_id, str) or not style_id.strip():
        return False
    grade = parameters.get("grade", {})
    if not isinstance(grade, Mapping) or set(grade) - _VISUAL_DIRECTION_GRADE_KEYS:
        return False
    if any(not _finite(value) for value in grade.values()):
        return False
    if any(
        not _finite(parameters[key])
        for key in _VISUAL_DIRECTION_GEOMETRY_KEYS
        if key in parameters
    ):
        return False
    intensities = parameters.get("intensities")
    if intensities is not None:
        if (
            not isinstance(intensities, Mapping)
            or set(intensities) != set(_GRADE_INTENSITIES)
            or any(not _finite(value) for value in intensities.values())
            or intensities["none"] != 0
        ):
            return False
    return True


_MOTION_TEXT_LAYOUTS = (
    "dominant_word", "stacked_hierarchy", "small_plus_massive", "split_statement",
    "edge_aligned", "centered_poster", "contrast_pair",
)
_MOTION_TEXT_MOTIONS = ("fade_rise", "scale_in", "masked_reveal", "stagger_rise")
_MOTION_TEXT_WEIGHTS = ("micro", "small", "large", "massive")
_MOTION_TEXT_STYLE_KEYS = {
    "font_name", "support_font_name", "foreground", "accent", "muted",
    "safe_margin_fraction",
}


def _motion_typography_match(operation, timeline_duration: float) -> bool:
    """Whether a ``motion_typography`` operation is the shape the workflow accepts.

    Same job as :func:`_text_events_match` for the newer layer: a plan that
    carries a well-formed typographic layer must not fail validation on form.
    """

    parameters = dict(operation.parameters)
    if (
        operation.source is not None
        or operation.start_seconds is not None
        or operation.end_seconds is not None
        or set(parameters) - {"items", "style"}
    ):
        return False
    items = parameters.get("items")
    if (
        isinstance(items, (str, bytes))
        or not isinstance(items, (list, tuple))
        or not items
        or len(items) > 60
    ):
        return False
    previous_end = 0.0
    for item in items:
        if not isinstance(item, Mapping):
            return False
        if {"blocks", "start_seconds", "end_seconds"} - set(item):
            return False
        if set(item) - {"blocks", "start_seconds", "end_seconds", "layout", "motion"}:
            return False
        blocks = item["blocks"]
        if (
            isinstance(blocks, (str, bytes))
            or not isinstance(blocks, (list, tuple))
            or not 1 <= len(blocks) <= 3
        ):
            return False
        for block in blocks:
            if not isinstance(block, Mapping) or set(block) - {
                "text", "weight", "accent"
            }:
                return False
            text = block.get("text")
            if (
                not isinstance(text, str)
                or not text.strip()
                or len(text.strip()) > 40
                or any(ord(ch) < 32 or ch in "<>{}" for ch in text)
                or block.get("weight", "massive") not in _MOTION_TEXT_WEIGHTS
                or not isinstance(block.get("accent", False), bool)
            ):
                return False
        start = item["start_seconds"]
        end = item["end_seconds"]
        if (
            not _finite(start)
            or not _finite(end)
            or start < previous_end
            or end - start < 0.2
            or end > timeline_duration
            or item.get("layout", "stacked_hierarchy") not in _MOTION_TEXT_LAYOUTS
            or item.get("motion", "fade_rise") not in _MOTION_TEXT_MOTIONS
        ):
            return False
        previous_end = float(end)
    style = parameters.get("style")
    if style is None:
        return True
    if not isinstance(style, Mapping) or set(style) - _MOTION_TEXT_STYLE_KEYS:
        return False
    for name in ("font_name", "support_font_name"):
        value = style.get(name, "Sans")
        if not isinstance(value, str):
            return False
    for name in ("foreground", "accent", "muted"):
        value = style.get(name, "&H00FFFFFF")
        if not isinstance(value, str) or not _ASS_COLOUR.match(value):
            return False
    margin = style.get("safe_margin_fraction", 0.06)
    return bool(_finite(margin) and 0 <= margin <= 0.2)


_MOTION_GRAPHICS_LAYOUTS = frozenset({
    "dominant-word", "small-plus-massive", "stacked-editorial",
    "split-contrast", "poster-statement",
})
_MOTION_GRAPHICS_IMPORTANCE = frozenset({"dominant", "secondary", "support"})
_HEX_COLOUR = re.compile(r"^#[0-9A-Fa-f]{6}$")


def _motion_graphics_match(operation, timeline_duration: float) -> bool:
    """Whether a ``motion_graphics`` operation carries a well-formed Remotion scene.

    Same job as :func:`_motion_typography_match` for the overlay-composed variant:
    a plan whose typographic layer is sound in form must not fail validation on
    form. The operation's parameters are the whole scene document.
    """

    parameters = dict(operation.parameters)
    if (
        operation.source is not None
        or operation.start_seconds is not None
        or operation.end_seconds is not None
        or set(parameters) - {"schema_version", "composition", "theme", "events"}
    ):
        return False
    if parameters.get("schema_version") != 1:
        return False
    composition = parameters.get("composition")
    if not isinstance(composition, Mapping) or set(composition) != {
        "width", "height", "fps", "durationInSeconds"
    }:
        return False
    for key in ("width", "height", "fps", "durationInSeconds"):
        value = composition[key]
        if isinstance(value, bool) or not _finite(value) or value <= 0:
            return False
    theme = parameters.get("theme")
    if not isinstance(theme, Mapping) or set(theme) - {
        "foreground", "accent", "muted", "background"
    }:
        return False
    for key in ("foreground", "accent", "muted"):
        if not isinstance(theme.get(key), str) or not _HEX_COLOUR.match(theme[key]):
            return False
    background = theme.get("background")
    if background is not None and (
        not isinstance(background, str) or not _HEX_COLOUR.match(background)
    ):
        return False
    events = parameters.get("events")
    if (
        isinstance(events, (str, bytes))
        or not isinstance(events, (list, tuple))
        or not events
        or len(events) > 60
    ):
        return False
    previous_end = 0.0
    for event in events:
        if not isinstance(event, Mapping):
            return False
        if {"id", "start", "duration", "role", "layout", "motion", "blocks"} - set(event):
            return False
        if set(event) - {
            "id", "start", "duration", "role", "layout", "variant", "motion", "blocks"
        }:
            return False
        if event["layout"] not in _MOTION_GRAPHICS_LAYOUTS:
            return False
        start = event["start"]
        duration = event["duration"]
        if (
            not _finite(start)
            or not _finite(duration)
            or start < previous_end - 1e-6
            or duration < 0.2
            or start + duration > timeline_duration + 1e-6
        ):
            return False
        previous_end = float(start)
        blocks = event["blocks"]
        if (
            isinstance(blocks, (str, bytes))
            or not isinstance(blocks, (list, tuple))
            or not 1 <= len(blocks) <= 3
        ):
            return False
        for block in blocks:
            if not isinstance(block, Mapping) or set(block) - {
                "text", "importance", "accent"
            }:
                return False
            text = block.get("text")
            if (
                not isinstance(text, str)
                or not text.strip()
                or len(text.strip()) > 40
                or any(ord(ch) < 32 or ch in "<>{}" for ch in text)
                or block.get("importance") not in _MOTION_GRAPHICS_IMPORTANCE
                or not isinstance(block.get("accent", False), bool)
            ):
                return False
    return True


def _text_events_match(operation, timeline_duration: float) -> bool:
    """Whether a ``text_events`` operation is the shape the workflow accepts.

    This is the operation the semantic planner emits for the emphasis layer.
    Before it was understood here, every plan that carried one failed manifest
    validation on form alone, however sound the render was.
    """

    parameters = dict(operation.parameters)
    if (
        operation.source is not None
        or operation.start_seconds is not None
        or operation.end_seconds is not None
        or set(parameters) - {"items", "style"}
    ):
        return False
    items = parameters.get("items")
    if (
        isinstance(items, (str, bytes))
        or not isinstance(items, (list, tuple))
        or not items
        or len(items) > 200
    ):
        return False
    previous_end = 0.0
    for item in items:
        if not isinstance(item, Mapping):
            return False
        if {"text", "start_seconds", "end_seconds"} - set(item):
            return False
        if set(item) - {
            "text", "start_seconds", "end_seconds", "position", "animation",
            "emphasis", "highlight",
        }:
            return False
        highlight = item.get("highlight")
        if highlight is not None and (
            isinstance(highlight, (str, bytes))
            or not isinstance(highlight, (list, tuple))
            or len(highlight) != 2
            or any(isinstance(v, bool) or not isinstance(v, int) for v in highlight)
            or not 0 <= highlight[0] < highlight[1] <= len(str(item["text"]).strip())
        ):
            return False
        text = item["text"]
        start = item["start_seconds"]
        end = item["end_seconds"]
        if (
            not isinstance(text, str)
            or not text.strip()
            or len(text.strip()) > 48
            or any(ord(character) < 32 or character in "<>{}" for character in text)
            or not _finite(start)
            or not _finite(end)
            or start < previous_end
            or end <= start
            or round(end * 1000) <= round(start * 1000)
            or end > timeline_duration
            or item.get("position", "top") not in _TEXT_EVENT_POSITIONS
            or item.get("animation", "fade") not in _TEXT_EVENT_ANIMATIONS
            or not isinstance(item.get("emphasis", False), bool)
        ):
            return False
        previous_end = float(end)
    style = parameters.get("style")
    if style is None:
        return True
    if not isinstance(style, Mapping) or set(style) - _TEXT_EVENT_STYLE_KEYS:
        return False
    for name in ("font_name",):
        value = style.get(name, "Sans")
        if not isinstance(value, str) or not value.strip():
            return False
    for name in ("foreground", "accent"):
        value = style.get(name, "&H00FFFFFF")
        if not isinstance(value, str) or not _ASS_COLOUR.match(value):
            return False
    scale = style.get("emphasis_scale", 1.6)
    if not _finite(scale) or not 1.0 <= scale <= 3.0:
        return False
    margin = style.get("safe_margin_fraction", 0.06)
    if not _finite(margin) or margin > 0.2:
        return False
    return True


def _fade_matches(operation, timeline_duration: float) -> bool:
    parameters = dict(operation.parameters)
    if (
        operation.source is not None
        or operation.start_seconds is not None
        or operation.end_seconds is not None
        or not set(parameters) <= {"from_black_seconds", "to_black_seconds"}
    ):
        return False
    values = [parameters.get(key, 0.0) for key in ("from_black_seconds", "to_black_seconds")]
    if any(not _finite(value) for value in values):
        return False
    return sum(float(value) for value in values) <= timeline_duration



def _sequence_plan_matches(plan: EditPlan) -> bool:
    operations = plan.operations
    index = 0
    timeline_duration = 0.0
    clip_count = 0
    used_sources = set()
    music_source = None
    while index < len(operations) and operations[index].kind in ("sequence_clip", "image_clip"):
        segment = operations[index]
        if segment.source is None:
            return False
        if segment.kind == "image_clip":
            parameters = dict(segment.parameters)
            duration = parameters.get("duration_seconds")
            if (
                segment.start_seconds is not None
                or segment.end_seconds is not None
                or "duration_seconds" not in parameters
                or set(parameters)
                - ({"duration_seconds", "fit", "motion"} | _SEGMENT_DIRECTION_KEYS)
                or parameters.get("fit") not in (None, "contain", "cover")
                or parameters.get("motion")
                not in (None,) + _KEN_BURNS_MOTIONS + _DIRECTION_MOTIONS
                or not _segment_direction_ok(parameters, is_image=True)
                or isinstance(duration, bool)
                or not isinstance(duration, (int, float))
                or not math.isfinite(duration)
                or duration <= 0
                or duration > 600
            ):
                return False
            timeline_duration += float(duration)
        else:
            parameters = dict(segment.parameters)
            if (
                segment.start_seconds is None
                or segment.end_seconds is None
                or set(parameters) - ({"fit"} | _SEGMENT_DIRECTION_KEYS)
                or parameters.get("fit") not in (None, "contain", "cover")
                or ("fit" in parameters and plan.target_format is None)
                or not _segment_direction_ok(parameters, is_image=False)
            ):
                return False
            timeline_duration += segment.end_seconds - segment.start_seconds
            clip_count += 1
        used_sources.add(segment.source)
        index += 1
    if index < 2 or (clip_count < 1 and plan.target_format is None):
        # An all-image timeline is only coherent when the plan pins an explicit
        # delivery canvas via target_format.
        return False
    if index < len(operations) and operations[index].kind == "visual_direction":
        if not _visual_direction_matches(operations[index]):
            return False
        index += 1
    if index < len(operations) and operations[index].kind == "captions":
        captions = operations[index]
        parameters = dict(captions.parameters)
        items = parameters.get("items")
        if captions.start_seconds is not None or captions.end_seconds is not None:
            return False
        if captions.source is not None:
            if (
                Path(captions.source).suffix.lower() not in _SUBTITLE_SUFFIXES
                or set(parameters) != {"style"}
                or parameters.get("style") != "bottom_box"
            ):
                return False
            used_sources.add(captions.source)
            items = ()
        elif parameters == {"style": "bottom_box", "from": "narration"}:
            items = ()
        elif (
            set(parameters) != {"style", "items"}
            or parameters.get("style") != "bottom_box"
            or isinstance(items, (str, bytes))
            or not isinstance(items, (list, tuple))
            or not items
            or len(items) > 500
            or any(
                not isinstance(item, Mapping)
                or set(item) != {"text", "start_seconds", "end_seconds"}
                for item in items
            )
        ):
            return False
        previous_end = 0.0
        for item in items:
            text = item["text"]
            start = item["start_seconds"]
            end = item["end_seconds"]
            if (
                not isinstance(text, str)
                or not text.strip()
                or len(text.strip()) > 160
                or any(ord(character) < 32 or character in "<>{}" for character in text)
                or isinstance(start, bool)
                or not isinstance(start, (int, float))
                or not math.isfinite(start)
                or start < previous_end
                or isinstance(end, bool)
                or not isinstance(end, (int, float))
                or not math.isfinite(end)
                or end <= start
                or round(end * 1000) <= round(start * 1000)
                or end > timeline_duration
            ):
                return False
            previous_end = float(end)
        index += 1
    if index < len(operations) and operations[index].kind == "text_events":
        if not _text_events_match(operations[index], timeline_duration):
            return False
        index += 1
    if index < len(operations) and operations[index].kind == "motion_typography":
        if not _motion_typography_match(operations[index], timeline_duration):
            return False
        index += 1
    if index < len(operations) and operations[index].kind == "motion_graphics":
        if not _motion_graphics_match(operations[index], timeline_duration):
            return False
        index += 1
    if index < len(operations) and operations[index].kind == "fade":
        if not _fade_matches(operations[index], timeline_duration):
            return False
        index += 1
    if index < len(operations) and operations[index].kind == "music":
        music = operations[index]
        parameters = dict(music.parameters)
        gain = parameters.get("gain_db")
        duck = parameters.get("duck_db")
        fades = [parameters[key] for key in ("fade_in_seconds", "fade_out_seconds") if key in parameters]
        if (
            music.source is None
            or music.start_seconds is not None
            or music.end_seconds is not None
            or not {"duration_policy", "gain_db"} <= set(parameters)
            or set(parameters)
            - {"duration_policy", "gain_db", "fade_in_seconds", "fade_out_seconds", "duck_db"}
            or parameters.get("duration_policy") != "loop_to_timeline"
            or isinstance(gain, bool)
            or not isinstance(gain, (int, float))
            or not math.isfinite(gain)
            or gain < -60
            or gain > 0
            or (
                duck is not None
                and (
                    isinstance(duck, bool)
                    or not isinstance(duck, (int, float))
                    or not math.isfinite(duck)
                    or duck < -60
                    or duck >= 0
                )
            )
            or any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
                for value in fades
            )
        ):
            return False
        used_sources.add(music.source)
        music_source = music.source
        index += 1
    if index < len(operations) and operations[index].kind == "narration":
        narration = operations[index]
        params = dict(narration.parameters)
        if narration.start_seconds is not None or narration.end_seconds is not None:
            return False
        if params.get("duration_policy") != "match_timeline":
            return False
        if narration.source is not None:
            if set(params) != {"duration_policy"}:
                return False
            if music_source is not None and _normalized(narration.source) == _normalized(
                music_source
            ):
                return False
            used_sources.add(narration.source)
        else:
            if set(params) - {
                "duration_policy",
                "text",
                "voice",
                "speed",
                "lang",
                "lead_in_seconds",
            }:
                return False
            text = params.get("text")
            if not isinstance(text, str) or not text.strip():
                return False
        index += 1
    return (
        index == len(operations)
        and used_sources == set(plan.sources)
        and Path(plan.output_path).suffix.lower() == ".mp4"
    )


def validate_render_manifest(
    manifest: RenderManifest,
    plan: EditPlan,
) -> ManifestValidationReport:
    """Verify contract linkage and current file integrity without modifying state."""

    if not isinstance(manifest, RenderManifest):
        raise TypeError("manifest must be a RenderManifest")
    if not isinstance(plan, EditPlan):
        raise TypeError("plan must be an EditPlan")
    issues: list[ManifestValidationIssue] = []
    if manifest.plan_id != plan.plan_id:
        issues.append(ManifestValidationIssue("plan_id_mismatch", "manifest plan_id does not match the plan"))
    if manifest.brief_id != plan.brief_id:
        issues.append(ManifestValidationIssue("brief_id_mismatch", "manifest brief_id does not match the plan"))
    if manifest.plan_sha256 != fingerprint_plan(plan):
        issues.append(
            ManifestValidationIssue(
                "plan_hash_mismatch",
                "plan content no longer matches the manifest",
            )
        )
    if manifest.workflow not in {"segment-extract", "video-sequence"}:
        issues.append(
            ManifestValidationIssue(
                "unsupported_workflow",
                f"manifest workflow is not supported: {manifest.workflow}",
            )
        )
    elif manifest.workflow == "segment-extract" and (
        len(plan.operations) != 1 or plan.operations[0].kind != "extract_segment"
    ):
        issues.append(
            ManifestValidationIssue(
                "workflow_plan_mismatch",
                "segment-extract manifest requires one extract_segment operation",
            )
        )
    elif manifest.workflow == "video-sequence" and not _sequence_plan_matches(plan):
        issues.append(
            ManifestValidationIssue(
                "workflow_plan_mismatch",
                "video-sequence manifest requires at least two timeline segments followed "
                "by optional visual_direction, captions, text_events, "
                "motion_typography, motion_graphics, fade, "
                "looped music and matched narration, in that order",
            )
        )

    expected_sources = {_normalized(source) for source in plan.sources}
    recorded_sources = {_normalized(source.path) for source in manifest.sources}
    if recorded_sources != expected_sources:
        issues.append(ManifestValidationIssue("source_set_mismatch", "manifest sources do not match the plan"))
    recorded_outputs = {_normalized(output.path) for output in manifest.outputs}
    if manifest.workflow in {"segment-extract", "video-sequence"} and recorded_outputs != {
        _normalized(plan.output_path)
    }:
        issues.append(
            ManifestValidationIssue(
                "output_set_mismatch",
                "manifest outputs do not match the plan",
            )
        )

    for fingerprint in (*manifest.sources, *manifest.outputs):
        _check_fingerprint(fingerprint, issues)
    return ManifestValidationReport(
        manifest_id=manifest.manifest_id,
        integrity_valid=not issues,
        technical_validation_valid=manifest.technical_validation_valid,
        editorial_review=manifest.editorial_review,
        issues=tuple(issues),
        recorded_technical_issues=manifest.technical_validation_issues,
    )
