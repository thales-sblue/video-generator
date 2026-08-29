"""Read-only traceability validation for a persisted audiovisual project chain."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from video_generator.domain import EditPlan, RenderManifest, VideoBrief, VideoRequest
from video_generator.validation.manifest import ManifestValidationReport, validate_render_manifest


@dataclass(frozen=True, slots=True)
class ProjectValidationIssue:
    code: str
    message: str
    path: str | None = None


@dataclass(frozen=True, slots=True)
class ProjectValidationReport:
    request_id: str
    brief_id: str
    plan_id: str
    manifest_id: str
    trace_valid: bool
    issues: tuple[ProjectValidationIssue, ...]
    manifest_validation: ManifestValidationReport

    @property
    def technically_ready(self) -> bool:
        return self.trace_valid and self.manifest_validation.technically_ready

    @property
    def editorial_review(self) -> str:
        return self.manifest_validation.editorial_review

    def to_dict(self) -> dict[str, object]:
        return {
            "request_id": self.request_id,
            "brief_id": self.brief_id,
            "plan_id": self.plan_id,
            "manifest_id": self.manifest_id,
            "trace_valid": self.trace_valid,
            "technically_ready": self.technically_ready,
            "editorial_review": self.editorial_review,
            "issues": [asdict(issue) for issue in self.issues],
            "manifest_validation": self.manifest_validation.to_dict(),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _normalized(path: str | Path) -> str:
    return os.path.normcase(str(Path(path).expanduser().resolve()))


def validate_project_chain(
    request: VideoRequest,
    brief: VideoBrief,
    plan: EditPlan,
    manifest: RenderManifest,
) -> ProjectValidationReport:
    """Validate traceability and current artifact integrity without writing files."""

    expected_types = (
        ("request", request, VideoRequest),
        ("brief", brief, VideoBrief),
        ("plan", plan, EditPlan),
        ("manifest", manifest, RenderManifest),
    )
    for name, value, expected_type in expected_types:
        if not isinstance(value, expected_type):
            raise TypeError(f"{name} must be a {expected_type.__name__}")

    issues: list[ProjectValidationIssue] = []
    if brief.request_id != request.request_id:
        issues.append(
            ProjectValidationIssue(
                "request_id_mismatch",
                "brief request_id does not match the request",
            )
        )
    if plan.brief_id != brief.brief_id:
        issues.append(
            ProjectValidationIssue(
                "brief_id_mismatch",
                "plan brief_id does not match the brief",
            )
        )
    if manifest.plan_id != plan.plan_id:
        issues.append(
            ProjectValidationIssue(
                "manifest_plan_id_mismatch",
                "manifest plan_id does not match the plan",
            )
        )
    if manifest.brief_id != brief.brief_id:
        issues.append(
            ProjectValidationIssue(
                "manifest_brief_id_mismatch",
                "manifest brief_id does not match the brief",
            )
        )
    if request.platform is not None and request.platform != brief.platform:
        issues.append(
            ProjectValidationIssue(
                "platform_mismatch",
                "brief platform does not match the requested platform",
            )
        )
    if request.workflow is not None and request.workflow != brief.workflow:
        issues.append(
            ProjectValidationIssue(
                "editorial_workflow_mismatch",
                "brief workflow does not match the requested workflow",
            )
        )

    declared_sources = {_normalized(source) for source in request.sources}
    for source in plan.sources:
        if _normalized(source) not in declared_sources:
            issues.append(
                ProjectValidationIssue(
                    "undeclared_plan_source",
                    "plan source is not declared by the request",
                    source,
                )
            )

    manifest_validation = validate_render_manifest(manifest, plan)
    return ProjectValidationReport(
        request_id=request.request_id,
        brief_id=brief.brief_id,
        plan_id=plan.plan_id,
        manifest_id=manifest.manifest_id,
        trace_valid=not issues,
        issues=tuple(issues),
        manifest_validation=manifest_validation,
    )
