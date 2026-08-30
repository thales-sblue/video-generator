"""Build and exclusively publish local RenderManifest contracts."""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Iterable

from video_generator.doctor import DoctorReport
from video_generator.domain import EditPlan, FileFingerprint, RenderManifest, ToolRecord

if TYPE_CHECKING:
    from video_generator.workflows import SegmentWorkflowReport
    from video_generator.workflows import SequenceWorkflowReport


class ManifestError(RuntimeError):
    """Raised when a trustworthy manifest cannot be built or published."""


def _normalized(path: str | Path) -> str:
    return os.path.normcase(str(Path(path).expanduser().resolve()))


def default_manifest_path(output_path: str | Path) -> Path:
    output = Path(output_path).expanduser().resolve()
    return output.with_name(f"{output.name}.manifest.json")


def validate_manifest_target(
    manifest_path: str | Path,
    *,
    forbidden_paths: Iterable[str | Path],
) -> Path:
    target = Path(manifest_path).expanduser().resolve()
    if target.suffix.lower() != ".json":
        raise ManifestError("manifest path must use a .json extension")
    if target.exists():
        raise ManifestError(f"manifest already exists: {target}")
    forbidden = {_normalized(path) for path in forbidden_paths}
    if _normalized(target) in forbidden:
        raise ManifestError("manifest path must not overwrite a source or output artifact")
    return target


def fingerprint_file(path: str | Path) -> FileFingerprint:
    source = Path(path).expanduser().resolve()
    if not source.exists() or not source.is_file():
        raise ManifestError(f"cannot fingerprint missing file: {source}")
    try:
        before = source.stat()
        digest = hashlib.sha256()
        with source.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        after = source.stat()
    except OSError as exc:
        raise ManifestError(f"could not fingerprint file: {source}") from exc
    if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
        raise ManifestError(f"file changed while being fingerprinted: {source}")
    return FileFingerprint(str(source), digest.hexdigest(), after.st_size)


def fingerprint_plan(plan: EditPlan) -> str:
    if not isinstance(plan, EditPlan):
        raise TypeError("plan must be an EditPlan")
    return hashlib.sha256(plan.to_json(indent=None).encode("utf-8")).hexdigest()


def build_segment_render_manifest(
    plan: EditPlan,
    report: SegmentWorkflowReport,
    doctor: DoctorReport,
    source_fingerprints: tuple[FileFingerprint, ...],
) -> RenderManifest:
    if report.plan_id != plan.plan_id:
        raise ManifestError("workflow report does not match the plan")
    if report.operation_id not in {operation.operation_id for operation in plan.operations}:
        raise ManifestError("workflow report operation does not match the plan")
    if _normalized(report.artifact.output_path) != _normalized(plan.output_path):
        raise ManifestError("workflow artifact output does not match the plan")
    source_paths = {_normalized(source) for source in plan.sources}
    if _normalized(report.artifact.source_path) not in source_paths:
        raise ManifestError("workflow artifact source does not match the plan")
    if report.validation.artifact != report.artifact:
        raise ManifestError("workflow validation does not match the artifact")
    if not doctor.local_only or doctor.external_services_allowed or not doctor.preserve_sources:
        raise ManifestError("unsafe runtime configuration cannot produce a manifest")
    if len(source_fingerprints) != len(plan.sources):
        raise ManifestError("source fingerprints do not match the plan")
    for source, fingerprint in zip(plan.sources, source_fingerprints, strict=True):
        if _normalized(source) != _normalized(fingerprint.path):
            raise ManifestError("source fingerprint path does not match the plan")
        if fingerprint_file(source) != fingerprint:
            raise ManifestError(f"source changed during workflow execution: {source}")
    statuses = {tool.name: tool for tool in doctor.tools}
    tool_records = []
    for name in ("FFmpeg", "ffprobe"):
        status = statuses.get(name)
        if status is None or not status.available or not status.path:
            raise ManifestError(f"required tool metadata is unavailable: {name}")
        tool_records.append(ToolRecord(name, status.path, status.version))
    return RenderManifest(
        manifest_id=f"manifest-{plan.plan_id}",
        plan_id=plan.plan_id,
        brief_id=plan.brief_id,
        workflow="segment-extract",
        plan_sha256=fingerprint_plan(plan),
        sources=source_fingerprints,
        outputs=(fingerprint_file(report.artifact.output_path),),
        tools=tuple(tool_records),
        technical_validation_valid=report.validation.valid,
        technical_validation_issues=tuple(issue.code for issue in report.validation.issues),
    )


def build_sequence_render_manifest(
    plan: EditPlan,
    report: SequenceWorkflowReport,
    doctor: DoctorReport,
    source_fingerprints: tuple[FileFingerprint, ...],
) -> RenderManifest:
    if report.plan_id != plan.plan_id:
        raise ManifestError("workflow report does not match the plan")
    if report.operation_ids != tuple(operation.operation_id for operation in plan.operations):
        raise ManifestError("workflow report operations do not match the plan")
    if _normalized(report.artifact.output_path) != _normalized(plan.output_path):
        raise ManifestError("workflow artifact output does not match the plan")
    clip_sources = tuple(
        _normalized(operation.source)
        for operation in plan.operations
        if operation.kind == "sequence_clip" and operation.source is not None
    )
    if tuple(_normalized(source) for source in report.artifact.source_paths) != clip_sources:
        raise ManifestError("workflow artifact clip sources do not match the plan")
    narration_sources = tuple(
        _normalized(operation.source)
        for operation in plan.operations
        if operation.kind == "narration" and operation.source is not None
    )
    artifact_narration = (
        _normalized(report.artifact.narration_source_path)
        if report.artifact.narration_source_path is not None
        else None
    )
    expected_narration = narration_sources[0] if len(narration_sources) == 1 else None
    if artifact_narration != expected_narration:
        raise ManifestError("workflow artifact narration does not match the plan")
    if report.validation.artifact != report.artifact:
        raise ManifestError("workflow validation does not match the artifact")
    if not doctor.local_only or doctor.external_services_allowed or not doctor.preserve_sources:
        raise ManifestError("unsafe runtime configuration cannot produce a manifest")
    if len(source_fingerprints) != len(plan.sources):
        raise ManifestError("source fingerprints do not match the plan")
    for source, fingerprint in zip(plan.sources, source_fingerprints, strict=True):
        if _normalized(source) != _normalized(fingerprint.path):
            raise ManifestError("source fingerprint path does not match the plan")
        if fingerprint_file(source) != fingerprint:
            raise ManifestError(f"source changed during workflow execution: {source}")
    statuses = {tool.name: tool for tool in doctor.tools}
    tool_records = []
    for name in ("FFmpeg", "ffprobe"):
        status = statuses.get(name)
        if status is None or not status.available or not status.path:
            raise ManifestError(f"required tool metadata is unavailable: {name}")
        tool_records.append(ToolRecord(name, status.path, status.version))
    return RenderManifest(
        manifest_id=f"manifest-{plan.plan_id}",
        plan_id=plan.plan_id,
        brief_id=plan.brief_id,
        workflow="video-sequence",
        plan_sha256=fingerprint_plan(plan),
        sources=source_fingerprints,
        outputs=(fingerprint_file(report.artifact.output_path),),
        tools=tuple(tool_records),
        technical_validation_valid=report.validation.valid,
        technical_validation_issues=tuple(issue.code for issue in report.validation.issues),
    )


def publish_render_manifest(manifest: RenderManifest, manifest_path: str | Path) -> Path:
    if not isinstance(manifest, RenderManifest):
        raise TypeError("manifest must be a RenderManifest")
    target = validate_manifest_target(manifest_path, forbidden_paths=())
    temporary: Path | None = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix=f".{target.name}-",
            suffix=".tmp",
            dir=target.parent,
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(manifest.to_json())
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, target)
    except OSError as exc:
        raise ManifestError(f"could not publish manifest without overwriting: {target}") from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
    return target
