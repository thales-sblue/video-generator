"""Compose a persisted clip sequence, optionally with matched narration."""

from __future__ import annotations

import json
import math
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from video_generator.adapters import (
    FFmpegError,
    MediaProbe,
    SequenceArtifact,
    SequenceClip,
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
NARRATION_KIND = "narration"
NARRATION_PARAMETERS = {"duration_policy": "match_timeline"}


class SequenceWorkflowError(RuntimeError):
    """Raised when a sequence plan cannot safely produce a workflow report."""


@dataclass(frozen=True, slots=True)
class SequenceWorkflowReport:
    plan_id: str
    operation_ids: tuple[str, ...]
    preflight: PreflightReport
    artifact: SequenceArtifact
    validation: SequenceValidationReport

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


def _operations_from_plan(plan: EditPlan) -> tuple[tuple[SequenceClip, ...], str | None]:
    if len(plan.operations) < 2:
        raise SequenceWorkflowError("video-sequence requires at least two operations")
    if Path(plan.output_path).suffix.lower() != ".mp4":
        raise SequenceWorkflowError("video-sequence requires an .mp4 output_path")
    clips = []
    used_sources = set()
    narration_path: str | None = None
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
        if operation.kind != OPERATION_KIND:
            raise SequenceWorkflowError(
                f"video-sequence does not support operation kind: {operation.kind}"
            )
        if operation.source is None:
            raise SequenceWorkflowError("sequence_clip must declare a source")
        if operation.start_seconds is None or operation.end_seconds is None:
            raise SequenceWorkflowError("sequence_clip requires start_seconds and end_seconds")
        if operation.parameters:
            raise SequenceWorkflowError("sequence_clip does not accept parameters in v1")
        clips.append(
            SequenceClip(operation.source, operation.start_seconds, operation.end_seconds)
        )
        used_sources.add(operation.source)
    if len(clips) < 2:
        raise SequenceWorkflowError("video-sequence requires at least two sequence_clip operations")
    if used_sources != set(plan.sources):
        raise SequenceWorkflowError("every declared source must be used by the sequence")
    return tuple(clips), narration_path


def _source_probes(report: PreflightReport, plan: EditPlan) -> dict[str, MediaProbe]:
    if len(report.sources) != len(plan.sources):
        raise SequenceWorkflowError("preflight did not inspect every sequence source")
    return {_normalized(source.source_path): source for source in report.sources}


def _video_shape(
    probes: dict[str, MediaProbe], clips: tuple[SequenceClip, ...]
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
    clips, narration_path = _operations_from_plan(plan)
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
    _video_shape(probes, clips)
    expected_duration = sum(clip.end_seconds - clip.start_seconds for clip in clips)
    if narration_path is not None:
        _validate_narration(probes, narration_path, expected_duration, tolerance)
    if before_compose is not None:
        before_compose(plan)

    create_artifact = compose or compose_video_sequence
    try:
        compose_kwargs = {"timeout_seconds": timeout}
        if narration_path is not None:
            compose_kwargs["narration_path"] = narration_path
        artifact = create_artifact(clips, plan.output_path, **compose_kwargs)
    except FFmpegError as exc:
        raise SequenceWorkflowError(f"video sequence composition failed: {exc}") from exc
    if not isinstance(artifact, SequenceArtifact):
        raise TypeError("compose must return a SequenceArtifact")
    if _normalized(artifact.output_path) != _normalized(plan.output_path):
        raise SequenceWorkflowError("compose returned an artifact at an unexpected output path")
    expected_sources = tuple(_normalized(clip.source_path) for clip in clips)
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
