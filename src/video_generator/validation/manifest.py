"""Read-only integrity validation for persisted RenderManifest contracts."""

from __future__ import annotations

import json
import math
import os
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
                or set(parameters) != {"duration_seconds"}
                or isinstance(duration, bool)
                or not isinstance(duration, (int, float))
                or not math.isfinite(duration)
                or duration <= 0
                or duration > 600
            ):
                return False
            timeline_duration += float(duration)
        else:
            if (
                segment.start_seconds is None
                or segment.end_seconds is None
                or segment.parameters
            ):
                return False
            timeline_duration += segment.end_seconds - segment.start_seconds
            clip_count += 1
        used_sources.add(segment.source)
        index += 1
    if index < 2 or clip_count < 1:
        return False
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
    if index < len(operations) and operations[index].kind == "music":
        music = operations[index]
        parameters = dict(music.parameters)
        gain = parameters.get("gain_db")
        if (
            music.source is None
            or music.start_seconds is not None
            or music.end_seconds is not None
            or set(parameters) != {"duration_policy", "gain_db"}
            or parameters.get("duration_policy") != "loop_to_timeline"
            or isinstance(gain, bool)
            or not isinstance(gain, (int, float))
            or not math.isfinite(gain)
            or gain < -60
            or gain > 0
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
            if set(params) - {"duration_policy", "text", "voice", "speed", "lang"}:
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
                "video-sequence manifest requires at least two sequence_clip operations "
                "followed by optional captions, looped music and matched narration",
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
