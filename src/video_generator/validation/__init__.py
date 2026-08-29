"""Technical validation at audiovisual execution boundaries."""

from video_generator.validation.preflight import PreflightIssue, PreflightReport, preflight_edit_plan
from video_generator.validation.manifest import (
    ManifestValidationIssue,
    ManifestValidationReport,
    validate_render_manifest,
)
from video_generator.validation.segment import (
    SegmentValidationIssue,
    SegmentValidationReport,
    validate_segment_artifact,
)

__all__ = [
    "PreflightIssue",
    "PreflightReport",
    "ManifestValidationIssue",
    "ManifestValidationReport",
    "SegmentValidationIssue",
    "SegmentValidationReport",
    "preflight_edit_plan",
    "validate_render_manifest",
    "validate_segment_artifact",
]
