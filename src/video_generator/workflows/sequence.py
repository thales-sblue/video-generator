"""Compose a persisted clip sequence with captions and planned audio."""

from __future__ import annotations

import json
import math
import os
import tempfile
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Callable

from video_generator.adapters import (
    CaptionCue,
    FFmpegError,
    MediaProbe,
    SequenceArtifact,
    SequenceClip,
    SequenceImage,
    compose_video_sequence,
)
from video_generator.domain import EditPlan
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
NARRATION_KIND = "narration"
NARRATION_PARAMETERS = {"duration_policy": "match_timeline"}
CAPTIONS_KIND = "captions"
CAPTIONS_STYLE = "bottom_box"
MUSIC_KIND = "music"
MUSIC_DURATION_POLICY = "loop_to_timeline"


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


def _caption_cues(parameters: Mapping[str, object]) -> tuple[CaptionCue, ...]:
    values = dict(parameters)
    if set(values) != {"style", "items"} or values["style"] != CAPTIONS_STYLE:
        raise SequenceWorkflowError(
            "captions require style=bottom_box and an items array"
        )
    items = values["items"]
    if isinstance(items, (str, bytes)) or not isinstance(items, (list, tuple)):
        raise SequenceWorkflowError("captions items must be an array")
    if not items or len(items) > 500:
        raise SequenceWorkflowError("captions require between 1 and 500 items")
    cues = []
    previous_end = 0.0
    for index, item in enumerate(items):
        if not isinstance(item, Mapping) or set(item) != {
            "text", "start_seconds", "end_seconds"
        }:
            raise SequenceWorkflowError(
                f"caption item {index} requires text, start_seconds and end_seconds"
            )
        text = item["text"]
        if not isinstance(text, str) or not text.strip() or len(text.strip()) > 160:
            raise SequenceWorkflowError(
                f"caption item {index} text must contain 1 to 160 characters"
            )
        if any(ord(character) < 32 for character in text.strip()):
            raise SequenceWorkflowError(
                f"caption item {index} text must not contain control characters"
            )
        if any(character in text for character in "<>{}"):
            raise SequenceWorkflowError(
                f"caption item {index} text must not contain subtitle markup characters"
            )
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
        if end <= start or round(end * 1000) <= round(start * 1000):
            raise SequenceWorkflowError(
                f"caption item {index} must last at least 1 ms"
            )
        if start < previous_end:
            raise SequenceWorkflowError("caption items must be ordered and non-overlapping")
        cues.append(CaptionCue(text.strip(), start, end))
        previous_end = end
    return tuple(cues)


def _music_gain(parameters: Mapping[str, object]) -> float:
    values = dict(parameters)
    if set(values) != {"duration_policy", "gain_db"}:
        raise SequenceWorkflowError(
            "music requires duration_policy=loop_to_timeline and gain_db"
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
    return float(gain)


def _image_duration(parameters: Mapping[str, object]) -> float:
    values = dict(parameters)
    if set(values) != {"duration_seconds"}:
        raise SequenceWorkflowError("image_clip requires only a duration_seconds parameter")
    duration = _runtime_number(
        values["duration_seconds"], "image_clip duration_seconds", allow_zero=False
    )
    if duration > IMAGE_MAX_DURATION_SECONDS:
        raise SequenceWorkflowError(
            f"image_clip duration_seconds must not exceed {IMAGE_MAX_DURATION_SECONDS} seconds"
        )
    return duration


def _segment_duration(segment: SequenceClip | SequenceImage) -> float:
    if isinstance(segment, SequenceImage):
        return segment.duration_seconds
    return segment.end_seconds - segment.start_seconds


def _operations_from_plan(
    plan: EditPlan,
) -> tuple[
    tuple[SequenceClip | SequenceImage, ...],
    str | None,
    tuple[CaptionCue, ...],
    str | None,
    float | None,
]:
    if len(plan.operations) < 2:
        raise SequenceWorkflowError("video-sequence requires at least two operations")
    if Path(plan.output_path).suffix.lower() != ".mp4":
        raise SequenceWorkflowError("video-sequence requires an .mp4 output_path")
    segments: list[SequenceClip | SequenceImage] = []
    used_sources = set()
    narration_path: str | None = None
    captions: tuple[CaptionCue, ...] = ()
    captions_seen = False
    music_path: str | None = None
    music_gain_db: float | None = None
    for index, operation in enumerate(plan.operations):
        if operation.kind == NARRATION_KIND:
            if index != len(plan.operations) - 1:
                raise SequenceWorkflowError("narration must be the final operation")
            if narration_path is not None:
                raise SequenceWorkflowError("video-sequence accepts at most one narration")
            if operation.source is None:
                raise SequenceWorkflowError("narration must declare a source")
            if operation.start_seconds is not None or operation.end_seconds is not None:
                raise SequenceWorkflowError("narration timing is fixed at zero in v1")
            if dict(operation.parameters) != NARRATION_PARAMETERS:
                raise SequenceWorkflowError(
                    "narration requires duration_policy=match_timeline"
                )
            narration_path = operation.source
            used_sources.add(operation.source)
            continue
        if operation.kind == CAPTIONS_KIND:
            if captions_seen:
                raise SequenceWorkflowError("video-sequence accepts at most one captions operation")
            if len(segments) < 2:
                raise SequenceWorkflowError("captions must follow all timeline segments")
            if music_path is not None:
                raise SequenceWorkflowError("captions must precede music")
            if operation.source is not None:
                raise SequenceWorkflowError("captions must not declare a source")
            if operation.start_seconds is not None or operation.end_seconds is not None:
                raise SequenceWorkflowError("captions timing belongs to its items")
            captions = _caption_cues(operation.parameters)
            captions_seen = True
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
            music_gain_db = _music_gain(operation.parameters)
            music_path = operation.source
            used_sources.add(operation.source)
            continue
        if operation.kind == IMAGE_KIND:
            if captions_seen or music_path is not None:
                raise SequenceWorkflowError(
                    "all timeline segments must precede captions and music"
                )
            if operation.source is None:
                raise SequenceWorkflowError("image_clip must declare a source")
            if operation.start_seconds is not None or operation.end_seconds is not None:
                raise SequenceWorkflowError(
                    "image_clip has no timeline range; use a duration_seconds parameter"
                )
            segments.append(
                SequenceImage(operation.source, _image_duration(operation.parameters))
            )
            used_sources.add(operation.source)
            continue
        if operation.kind != OPERATION_KIND:
            raise SequenceWorkflowError(
                f"video-sequence does not support operation kind: {operation.kind}"
            )
        if captions_seen or music_path is not None:
            raise SequenceWorkflowError(
                "all timeline segments must precede captions and music"
            )
        if operation.source is None:
            raise SequenceWorkflowError("sequence_clip must declare a source")
        if operation.start_seconds is None or operation.end_seconds is None:
            raise SequenceWorkflowError("sequence_clip requires start_seconds and end_seconds")
        if operation.parameters:
            raise SequenceWorkflowError("sequence_clip does not accept parameters in v1")
        segments.append(
            SequenceClip(operation.source, operation.start_seconds, operation.end_seconds)
        )
        used_sources.add(operation.source)
    if len(segments) < 2:
        raise SequenceWorkflowError("video-sequence requires at least two timeline segments")
    if not any(isinstance(segment, SequenceClip) for segment in segments):
        raise SequenceWorkflowError("video-sequence requires at least one sequence_clip")
    if used_sources != set(plan.sources):
        raise SequenceWorkflowError("every declared source must be used by the sequence")
    if (
        narration_path is not None
        and music_path is not None
        and _normalized(narration_path) == _normalized(music_path)
    ):
        raise SequenceWorkflowError("narration and music must use distinct sources")
    return tuple(segments), narration_path, captions, music_path, music_gain_db


def _source_probes(report: PreflightReport, plan: EditPlan) -> dict[str, MediaProbe]:
    if len(report.sources) != len(plan.sources):
        raise SequenceWorkflowError("preflight did not inspect every sequence source")
    return {_normalized(source.source_path): source for source in report.sources}


def _video_shape(
    probes: dict[str, MediaProbe],
    clips: tuple[SequenceClip | SequenceImage, ...],
) -> tuple[int, int]:
    shapes = []
    for clip in clips:
        source = probes.get(_normalized(clip.source_path))
        if source is None:
            raise SequenceWorkflowError(f"preflight omitted sequence source: {clip.source_path}")
        video_streams = tuple(stream for stream in source.streams if stream.codec_type == "video")
        if not video_streams:
            raise SequenceWorkflowError(f"sequence source has no video stream: {source.source_path}")
        stream = video_streams[0]
        if stream.width is None or stream.height is None:
            raise SequenceWorkflowError(
                f"sequence source dimensions are unavailable: {source.source_path}"
            )
        shapes.append((stream.width, stream.height))
    if len(set(shapes)) != 1:
        raise SequenceWorkflowError("video-sequence v1 requires matching source dimensions")
    return shapes[0]


def _validate_narration(
    probes: dict[str, MediaProbe], narration_path: str, expected_duration: float, tolerance: float
) -> None:
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
    segments, narration_path, captions, music_path, music_gain_db = _operations_from_plan(plan)
    expected_duration = sum(_segment_duration(segment) for segment in segments)
    expected_image_count = sum(1 for segment in segments if isinstance(segment, SequenceImage))
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
    _video_shape(probes, segments)
    if narration_path is not None:
        _validate_narration(probes, narration_path, expected_duration, tolerance)
    if music_path is not None:
        _validate_music(probes, music_path)
    if before_compose is not None:
        before_compose(plan)

    create_artifact = compose or compose_video_sequence
    try:
        compose_kwargs = {"timeout_seconds": timeout}
        if narration_path is not None:
            compose_kwargs["narration_path"] = narration_path
        if captions:
            compose_kwargs["captions"] = captions
        if music_path is not None and music_gain_db is not None:
            compose_kwargs["music_path"] = music_path
            compose_kwargs["music_gain_db"] = music_gain_db
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
    if actual_narration != expected_narration:
        raise SequenceWorkflowError("compose returned unexpected narration metadata")
    if artifact.caption_count != len(captions):
        raise SequenceWorkflowError("compose returned unexpected caption metadata")
    if artifact.image_count != expected_image_count:
        raise SequenceWorkflowError("compose returned unexpected image metadata")
    expected_music = _normalized(music_path) if music_path is not None else None
    actual_music = (
        _normalized(artifact.music_source_path)
        if artifact.music_source_path is not None
        else None
    )
    if actual_music != expected_music or artifact.music_gain_db != music_gain_db:
        raise SequenceWorkflowError("compose returned unexpected music metadata")
    if artifact.duration_seconds != expected_duration or artifact.file_size_bytes <= 0:
        raise SequenceWorkflowError("compose returned inconsistent artifact metadata")

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
