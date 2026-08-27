"""Read-only technical preflight for persisted edit plans."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from video_generator.adapters import MediaProbe, ProbeError, probe_media
from video_generator.domain import EditOperation, EditPlan


@dataclass(frozen=True, slots=True)
class PreflightIssue:
    code: str
    message: str
    source: str | None = None
    operation_id: str | None = None


@dataclass(frozen=True, slots=True)
class PreflightReport:
    plan_id: str
    valid: bool
    issues: tuple[PreflightIssue, ...]
    sources: tuple[MediaProbe, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "plan_id": self.plan_id,
            "valid": self.valid,
            "issues": [asdict(issue) for issue in self.issues],
            "sources": [source.to_dict() for source in self.sources],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _timing_issue(operation: EditOperation, probe: MediaProbe) -> PreflightIssue | None:
    if operation.start_seconds is None and operation.end_seconds is None:
        return None
    if probe.duration_seconds is None:
        return PreflightIssue(
            code="source_duration_unknown",
            message="timed operation cannot be validated because source duration is unavailable",
            source=operation.source,
            operation_id=operation.operation_id,
        )
    if operation.start_seconds is not None and operation.start_seconds >= probe.duration_seconds:
        return PreflightIssue(
            code="start_out_of_range",
            message=(
                f"start_seconds {operation.start_seconds} must be less than source duration "
                f"{probe.duration_seconds}"
            ),
            source=operation.source,
            operation_id=operation.operation_id,
        )
    if operation.end_seconds is not None and operation.end_seconds > probe.duration_seconds:
        return PreflightIssue(
            code="end_out_of_range",
            message=(
                f"end_seconds {operation.end_seconds} exceeds source duration "
                f"{probe.duration_seconds}"
            ),
            source=operation.source,
            operation_id=operation.operation_id,
        )
    return None


def preflight_edit_plan(
    plan: EditPlan,
    *,
    probe: Callable[[str | Path], MediaProbe] | None = None,
) -> PreflightReport:
    """Probe declared sources and fail closed on unverifiable timed operations."""

    inspect_source = probe or probe_media
    issues: list[PreflightIssue] = []
    probes: list[MediaProbe] = []
    probes_by_source: dict[str, MediaProbe] = {}
    for source in plan.sources:
        try:
            result = inspect_source(source)
        except ProbeError as exc:
            issues.append(
                PreflightIssue(
                    code="source_unavailable",
                    message=str(exc),
                    source=source,
                )
            )
            continue
        probes.append(result)
        probes_by_source[source] = result

    for operation in plan.operations:
        if operation.source is None:
            if operation.start_seconds is not None or operation.end_seconds is not None:
                issues.append(
                    PreflightIssue(
                        code="timed_operation_without_source",
                        message="timed operation must declare a source",
                        operation_id=operation.operation_id,
                    )
                )
            continue
        source_probe = probes_by_source.get(operation.source)
        if source_probe is None:
            continue
        issue = _timing_issue(operation, source_probe)
        if issue is not None:
            issues.append(issue)

    return PreflightReport(
        plan_id=plan.plan_id,
        valid=not issues,
        issues=tuple(issues),
        sources=tuple(probes),
    )
