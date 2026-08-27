"""Technical validation for extracted segment artifacts."""

from __future__ import annotations

import json
import math
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from video_generator.adapters import MediaProbe, ProbeError, SegmentArtifact, probe_media


@dataclass(frozen=True, slots=True)
class SegmentValidationIssue:
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class SegmentValidationReport:
    valid: bool
    artifact: SegmentArtifact
    expected_duration_seconds: float
    actual_duration_seconds: float | None
    duration_tolerance_seconds: float
    issues: tuple[SegmentValidationIssue, ...]
    probe: MediaProbe | None

    def to_dict(self) -> dict[str, object]:
        return {
            "valid": self.valid,
            "artifact": asdict(self.artifact),
            "expected_duration_seconds": self.expected_duration_seconds,
            "actual_duration_seconds": self.actual_duration_seconds,
            "duration_tolerance_seconds": self.duration_tolerance_seconds,
            "issues": [asdict(issue) for issue in self.issues],
            "probe": self.probe.to_dict() if self.probe is not None else None,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def validate_segment_artifact(
    artifact: SegmentArtifact,
    *,
    duration_tolerance_seconds: float = 0.1,
    probe: Callable[[str | Path], MediaProbe] | None = None,
) -> SegmentValidationReport:
    """Check that an extracted artifact still matches its declared time range."""

    if not isinstance(artifact, SegmentArtifact):
        raise TypeError("artifact must be a SegmentArtifact")
    if (
        isinstance(duration_tolerance_seconds, bool)
        or not isinstance(duration_tolerance_seconds, (int, float))
        or not math.isfinite(duration_tolerance_seconds)
        or duration_tolerance_seconds < 0
    ):
        raise ValueError("duration_tolerance_seconds must be a finite non-negative number")
    tolerance = float(duration_tolerance_seconds)
    expected_duration = artifact.end_seconds - artifact.start_seconds
    issues: list[SegmentValidationIssue] = []
    media_probe: MediaProbe | None = None
    inspect = probe or probe_media
    try:
        media_probe = inspect(artifact.output_path)
    except ProbeError as exc:
        issues.append(SegmentValidationIssue("output_unavailable", str(exc)))

    if media_probe is not None:
        expected_path = os.path.normcase(str(Path(artifact.output_path).expanduser().resolve()))
        probed_path = os.path.normcase(str(Path(media_probe.source_path).expanduser().resolve()))
        if probed_path != expected_path:
            issues.append(
                SegmentValidationIssue(
                    "unexpected_output_path",
                    f"probe inspected {media_probe.source_path} instead of {artifact.output_path}",
                )
            )
        if media_probe.file_size_bytes != artifact.file_size_bytes:
            issues.append(
                SegmentValidationIssue(
                    "artifact_size_changed",
                    f"artifact size changed from {artifact.file_size_bytes} to {media_probe.file_size_bytes} bytes",
                )
            )
        if not media_probe.streams:
            issues.append(SegmentValidationIssue("no_media_streams", "output contains no media streams"))
        if media_probe.duration_seconds is None:
            issues.append(SegmentValidationIssue("output_duration_unknown", "output duration is unavailable"))
        elif abs(media_probe.duration_seconds - expected_duration) > tolerance:
            issues.append(
                SegmentValidationIssue(
                    "duration_mismatch",
                    (
                        f"output duration {media_probe.duration_seconds} differs from expected "
                        f"{expected_duration} by more than {tolerance} seconds"
                    ),
                )
            )

    return SegmentValidationReport(
        valid=not issues,
        artifact=artifact,
        expected_duration_seconds=expected_duration,
        actual_duration_seconds=media_probe.duration_seconds if media_probe is not None else None,
        duration_tolerance_seconds=tolerance,
        issues=tuple(issues),
        probe=media_probe,
    )
