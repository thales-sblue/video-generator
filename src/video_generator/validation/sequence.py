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
    """Verify output identity, duration, and expected video/audio stream shape."""

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
        audio_streams = tuple(stream for stream in media_probe.streams if stream.codec_type == "audio")
        other_streams = tuple(
            stream for stream in media_probe.streams
            if stream.codec_type not in {"video", "audio"}
        )
        if len(video_streams) != 1:
            issues.append(
                SequenceValidationIssue(
                    "unexpected_video_stream_count",
                    f"expected exactly one video stream, found {len(video_streams)}",
                )
            )
        elif video_streams[0].codec_name != "h264":
            issues.append(
                SequenceValidationIssue(
                    "unexpected_video_codec",
                    f"expected H.264 video, found {video_streams[0].codec_name or 'unknown'}",
                )
            )
        has_planned_audio = (
            artifact.narration_source_path is not None
            or artifact.narration_text_sha256 is not None
            or artifact.music_source_path is not None
        )
        if not has_planned_audio:
            if audio_streams or other_streams:
                issues.append(
                    SequenceValidationIssue(
                        "unexpected_non_video_streams",
                        "expected a silent timeline, found "
                        f"{len(audio_streams) + len(other_streams)} non-video stream(s)",
                    )
                )
        else:
            if len(audio_streams) != 1:
                issues.append(
                    SequenceValidationIssue(
                        "unexpected_audio_stream_count",
                        f"expected exactly one mixed audio stream, found {len(audio_streams)}",
                    )
                )
            elif audio_streams[0].codec_name != "aac":
                issues.append(
                    SequenceValidationIssue(
                        "unexpected_audio_codec",
                        f"expected AAC audio, found {audio_streams[0].codec_name or 'unknown'}",
                    )
                )
            else:
                if audio_streams[0].sample_rate_hz != 48000:
                    issues.append(
                        SequenceValidationIssue(
                            "unexpected_audio_sample_rate",
                            "expected 48000 Hz audio, found "
                            f"{audio_streams[0].sample_rate_hz or 'unknown'}",
                        )
                    )
                if audio_streams[0].channels != 2:
                    issues.append(
                        SequenceValidationIssue(
                            "unexpected_audio_channel_count",
                            f"expected stereo audio, found {audio_streams[0].channels or 'unknown'} channels",
                        )
                    )
            if other_streams:
                issues.append(
                    SequenceValidationIssue(
                        "unexpected_other_streams",
                        f"expected only video and audio, found {len(other_streams)} other stream(s)",
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
