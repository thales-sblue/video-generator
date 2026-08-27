"""Technical validation at audiovisual execution boundaries."""

from video_generator.validation.preflight import PreflightIssue, PreflightReport, preflight_edit_plan
from video_generator.validation.segment import (
    SegmentValidationIssue,
    SegmentValidationReport,
    validate_segment_artifact,
)

__all__ = [
    "PreflightIssue",
    "PreflightReport",
    "SegmentValidationIssue",
    "SegmentValidationReport",
    "preflight_edit_plan",
    "validate_segment_artifact",
]
