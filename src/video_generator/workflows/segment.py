"""Execute one persisted segment extraction plan from preflight to validation."""

from __future__ import annotations

import json
import math
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from video_generator.adapters import FFmpegError, SegmentArtifact, extract_segment
from video_generator.domain import EditOperation, EditPlan
from video_generator.validation import (
    PreflightReport,
    SegmentValidationReport,
    preflight_edit_plan,
    validate_segment_artifact,
)


WORKFLOW_NAME = "segment-extract"
OPERATION_KIND = "extract_segment"


class SegmentWorkflowError(RuntimeError):
    """Raised before a segment plan can produce a workflow report."""


@dataclass(frozen=True, slots=True)
class SegmentWorkflowReport:
    plan_id: str
    operation_id: str
    preflight: PreflightReport
    artifact: SegmentArtifact
    validation: SegmentValidationReport

    @property
    def valid(self) -> bool:
        return self.preflight.valid and self.validation.valid

    def to_dict(self) -> dict[str, object]:
        return {
            "workflow": WORKFLOW_NAME,
            "plan_id": self.plan_id,
            "operation_id": self.operation_id,
            "valid": self.valid,
            "preflight": self.preflight.to_dict(),
            "artifact": asdict(self.artifact),
            "validation": self.validation.to_dict(),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _supported_operation(plan: EditPlan) -> EditOperation:
    if len(plan.sources) != 1:
        raise SegmentWorkflowError("segment-extract requires exactly one declared source")
    if len(plan.operations) != 1:
        raise SegmentWorkflowError("segment-extract requires exactly one operation")
    operation = plan.operations[0]
    if operation.kind != OPERATION_KIND:
        raise SegmentWorkflowError(
            f"segment-extract does not support operation kind: {operation.kind}"
        )
    if operation.source is None:
        raise SegmentWorkflowError("extract_segment must declare its source")
    if operation.source != plan.sources[0]:
        raise SegmentWorkflowError("extract_segment must use the plan's only source")
    if operation.start_seconds is None or operation.end_seconds is None:
        raise SegmentWorkflowError("extract_segment requires start_seconds and end_seconds")
    if operation.parameters:
        raise SegmentWorkflowError("extract_segment does not accept parameters in schema v1")
    return operation


def _runtime_number(value: object, name: str, *, allow_zero: bool) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
        or (value == 0 and not allow_zero)
    ):
        qualifier = "non-negative" if allow_zero else "positive"
        raise SegmentWorkflowError(f"{name} must be a finite {qualifier} number")
    return float(value)


def _normalized_path(value: str) -> str:
    return os.path.normcase(str(Path(value).expanduser().resolve()))


def _verify_artifact(
    artifact: SegmentArtifact,
    plan: EditPlan,
    operation: EditOperation,
) -> None:
    if _normalized_path(artifact.source_path) != _normalized_path(operation.source or ""):
        raise SegmentWorkflowError("extract returned an artifact for an unexpected source")
    if _normalized_path(artifact.output_path) != _normalized_path(plan.output_path):
        raise SegmentWorkflowError("extract returned an artifact at an unexpected output path")
    if (
        artifact.start_seconds != operation.start_seconds
        or artifact.end_seconds != operation.end_seconds
    ):
        raise SegmentWorkflowError("extract returned an artifact for an unexpected time range")
    if artifact.file_size_bytes <= 0:
        raise SegmentWorkflowError("extract returned an empty artifact")


def run_segment_workflow(
    plan: EditPlan,
    *,
    timeout_seconds: float = 300,
    duration_tolerance_seconds: float = 0.1,
    preflight: Callable[[EditPlan], PreflightReport] | None = None,
    before_extract: Callable[[EditPlan], None] | None = None,
    extract: Callable[..., SegmentArtifact] | None = None,
    validate: Callable[..., SegmentValidationReport] | None = None,
) -> SegmentWorkflowReport:
    """Execute one strictly supported segment plan without modifying its source."""

    if not isinstance(plan, EditPlan):
        raise TypeError("plan must be an EditPlan")
    timeout = _runtime_number(timeout_seconds, "timeout_seconds", allow_zero=False)
    tolerance = _runtime_number(
        duration_tolerance_seconds,
        "duration_tolerance_seconds",
        allow_zero=True,
    )
    operation = _supported_operation(plan)
    inspect_plan = preflight or preflight_edit_plan
    preflight_report = inspect_plan(plan)
    if not isinstance(preflight_report, PreflightReport):
        raise TypeError("preflight must return a PreflightReport")
    if preflight_report.plan_id != plan.plan_id:
        raise SegmentWorkflowError("preflight returned a report for an unexpected plan")
    if not preflight_report.valid:
        issue_codes = ", ".join(issue.code for issue in preflight_report.issues) or "unknown"
        raise SegmentWorkflowError(f"preflight rejected plan {plan.plan_id}: {issue_codes}")
    if before_extract is not None:
        before_extract(plan)

    create_artifact = extract or extract_segment
    try:
        artifact = create_artifact(
            operation.source,
            plan.output_path,
            start_seconds=operation.start_seconds,
            end_seconds=operation.end_seconds,
            timeout_seconds=timeout,
        )
    except FFmpegError as exc:
        raise SegmentWorkflowError(f"segment extraction failed: {exc}") from exc
    if not isinstance(artifact, SegmentArtifact):
        raise TypeError("extract must return a SegmentArtifact")
    _verify_artifact(artifact, plan, operation)

    inspect_artifact = validate or validate_segment_artifact
    validation = inspect_artifact(
        artifact,
        duration_tolerance_seconds=tolerance,
    )
    if not isinstance(validation, SegmentValidationReport):
        raise TypeError("validate must return a SegmentValidationReport")
    if validation.artifact != artifact:
        raise SegmentWorkflowError("validation returned a report for an unexpected artifact")
    return SegmentWorkflowReport(
        plan_id=plan.plan_id,
        operation_id=operation.operation_id,
        preflight=preflight_report,
        artifact=artifact,
        validation=validation,
    )
