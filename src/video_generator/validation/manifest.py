"""Read-only integrity validation for persisted RenderManifest contracts."""

from __future__ import annotations

import json
import os
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


def _sequence_plan_matches(plan: EditPlan) -> bool:
    clips = tuple(operation for operation in plan.operations if operation.kind == "sequence_clip")
    narrations = tuple(operation for operation in plan.operations if operation.kind == "narration")
    if len(clips) < 2 or len(narrations) > 1:
        return False
    if len(clips) + len(narrations) != len(plan.operations):
        return False
    if not narrations:
        return True
    narration = narrations[0]
    return (
        plan.operations[-1] == narration
        and narration.source is not None
        and narration.start_seconds is None
        and narration.end_seconds is None
        and dict(narration.parameters) == {"duration_policy": "match_timeline"}
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
                "video-sequence manifest requires at least two sequence_clip operations "
                "and at most one final match_timeline narration",
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
