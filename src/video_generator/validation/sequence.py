"""Technical validation for a composed video sequence."""

from __future__ import annotations

import json
import math
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from video_generator.adapters import MediaProbe, ProbeError, SequenceArtifact, probe_media


@dataclass(frozen=True, slots=True)
class SequenceValidationIssue:
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class SequenceValidationReport:
    valid: bool
    artifact: SequenceArtifact
    actual_duration_seconds: float | None
    duration_tolerance_seconds: float
    issues: tuple[SequenceValidationIssue, ...]
    probe: MediaProbe | None

    def to_dict(self) -> dict[str, object]:
        return {
            "valid": self.valid,
            "artifact": asdict(self.artifact),
            "actual_duration_seconds": self.actual_duration_seconds,
            "duration_tolerance_seconds": self.duration_tolerance_seconds,
            "issues": [asdict(issue) for issue in self.issues],
            "probe": self.probe.to_dict() if self.probe is not None else None,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def validate_sequence_artifact(
    artifact: SequenceArtifact,
    *,
    duration_tolerance_seconds: float = 0.15,
    probe: Callable[[str | Path], MediaProbe] | None = None,
) -> SequenceValidationReport:
    """Verify output identity, duration, and the single-video-stream shape."""

    if not isinstance(artifact, SequenceArtifact):
        raise TypeError("artifact must be a SequenceArtifact")
    if (
        isinstance(duration_tolerance_seconds, bool)
        or not isinstance(duration_tolerance_seconds, (int, float))
        or not math.isfinite(duration_tolerance_seconds)
        or duration_tolerance_seconds < 0
    ):
        raise ValueError("duration_tolerance_seconds must be a finite non-negative number")
    tolerance = float(duration_tolerance_seconds)
    issues: list[SequenceValidationIssue] = []
    media_probe: MediaProbe | None = None
    inspect = probe or probe_media
    try:
        media_probe = inspect(artifact.output_path)
    except ProbeError as exc:
        issues.append(SequenceValidationIssue("output_unavailable", str(exc)))

    if media_probe is not None:
        expected_path = os.path.normcase(str(Path(artifact.output_path).expanduser().resolve()))
        actual_path = os.path.normcase(str(Path(media_probe.source_path).expanduser().resolve()))
        if actual_path != expected_path:
            issues.append(
                SequenceValidationIssue(
                    "unexpected_output_path",
                    f"probe inspected {media_probe.source_path} instead of {artifact.output_path}",
                )
            )
        if media_probe.file_size_bytes != artifact.file_size_bytes:
            issues.append(
                SequenceValidationIssue(
                    "artifact_size_changed",
                    f"artifact size changed from {artifact.file_size_bytes} to "
                    f"{media_probe.file_size_bytes} bytes",
                )
            )
        video_streams = tuple(stream for stream in media_probe.streams if stream.codec_type == "video")
        non_video_streams = tuple(stream for stream in media_probe.streams if stream.codec_type != "video")
        if len(video_streams) != 1:
            issues.append(
                SequenceValidationIssue(
                    "unexpected_video_stream_count",
                    f"expected exactly one video stream, found {len(video_streams)}",
                )
            )
        if non_video_streams:
            issues.append(
                SequenceValidationIssue(
                    "unexpected_non_video_streams",
                    f"expected a silent timeline, found {len(non_video_streams)} non-video stream(s)",
                )
            )
        if media_probe.duration_seconds is None:
            issues.append(SequenceValidationIssue("output_duration_unknown", "output duration is unavailable"))
        elif abs(media_probe.duration_seconds - artifact.duration_seconds) > tolerance:
            issues.append(
                SequenceValidationIssue(
                    "duration_mismatch",
                    f"output duration {media_probe.duration_seconds} differs from expected "
                    f"{artifact.duration_seconds} by more than {tolerance} seconds",
                )
            )

    return SequenceValidationReport(
        valid=not issues,
        artifact=artifact,
        actual_duration_seconds=media_probe.duration_seconds if media_probe is not None else None,
        duration_tolerance_seconds=tolerance,
        issues=tuple(issues),
        probe=media_probe,
    )
