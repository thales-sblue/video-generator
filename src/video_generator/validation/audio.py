"""Technical validation for extracted PCM audio artifacts."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from video_generator.adapters import AudioArtifact, MediaProbe, ProbeError, probe_media


@dataclass(frozen=True, slots=True)
class AudioValidationIssue:
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class AudioValidationReport:
    valid: bool
    artifact: AudioArtifact
    issues: tuple[AudioValidationIssue, ...]
    probe: MediaProbe | None

    def to_dict(self) -> dict[str, object]:
        return {
            "valid": self.valid,
            "artifact": asdict(self.artifact),
            "issues": [asdict(issue) for issue in self.issues],
            "probe": self.probe.to_dict() if self.probe is not None else None,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def validate_audio_artifact(
    artifact: AudioArtifact,
    *,
    probe: Callable[[str | Path], MediaProbe] | None = None,
) -> AudioValidationReport:
    """Check that an extracted WAV still matches its recorded PCM properties."""

    if not isinstance(artifact, AudioArtifact):
        raise TypeError("artifact must be an AudioArtifact")
    if artifact.file_size_bytes <= 0:
        raise ValueError("file_size_bytes must be positive")
    if artifact.sample_rate_hz <= 0:
        raise ValueError("sample_rate_hz must be positive")
    if artifact.channels <= 0:
        raise ValueError("channels must be positive")

    issues: list[AudioValidationIssue] = []
    media_probe: MediaProbe | None = None
    inspect = probe or probe_media
    try:
        media_probe = inspect(artifact.output_path)
    except ProbeError as exc:
        issues.append(AudioValidationIssue("output_unavailable", str(exc)))

    if media_probe is not None:
        expected_path = os.path.normcase(str(Path(artifact.output_path).expanduser().resolve()))
        probed_path = os.path.normcase(str(Path(media_probe.source_path).expanduser().resolve()))
        if probed_path != expected_path:
            issues.append(
                AudioValidationIssue(
                    "unexpected_output_path",
                    f"probe inspected {media_probe.source_path} instead of {artifact.output_path}",
                )
            )
        if media_probe.file_size_bytes != artifact.file_size_bytes:
            issues.append(
                AudioValidationIssue(
                    "artifact_size_changed",
                    f"artifact size changed from {artifact.file_size_bytes} to "
                    f"{media_probe.file_size_bytes} bytes",
                )
            )
        if media_probe.format_name != "wav":
            issues.append(
                AudioValidationIssue(
                    "unexpected_container",
                    f"expected WAV container, found {media_probe.format_name or 'unknown'}",
                )
            )
        audio_streams = tuple(
            stream for stream in media_probe.streams if stream.codec_type == "audio"
        )
        non_audio_streams = tuple(
            stream for stream in media_probe.streams if stream.codec_type != "audio"
        )
        if len(audio_streams) != 1:
            issues.append(
                AudioValidationIssue(
                    "unexpected_audio_stream_count",
                    f"expected exactly one audio stream, found {len(audio_streams)}",
                )
            )
        if non_audio_streams:
            issues.append(
                AudioValidationIssue(
                    "unexpected_non_audio_streams",
                    f"output contains {len(non_audio_streams)} non-audio stream(s)",
                )
            )
        if len(audio_streams) == 1:
            stream = audio_streams[0]
            if stream.codec_name != "pcm_s16le":
                issues.append(
                    AudioValidationIssue(
                        "unexpected_audio_codec",
                        f"expected pcm_s16le, found {stream.codec_name or 'unknown'}",
                    )
                )
            if stream.sample_rate_hz != artifact.sample_rate_hz:
                issues.append(
                    AudioValidationIssue(
                        "sample_rate_mismatch",
                        f"expected {artifact.sample_rate_hz} Hz, found "
                        f"{stream.sample_rate_hz if stream.sample_rate_hz is not None else 'unknown'}",
                    )
                )
            if stream.channels != artifact.channels:
                issues.append(
                    AudioValidationIssue(
                        "channel_count_mismatch",
                        f"expected {artifact.channels} channels, found "
                        f"{stream.channels if stream.channels is not None else 'unknown'}",
                    )
                )
        if media_probe.duration_seconds is None or media_probe.duration_seconds <= 0:
            issues.append(
                AudioValidationIssue(
                    "invalid_audio_duration",
                    "output duration must be available and greater than zero",
                )
            )

    return AudioValidationReport(
        valid=not issues,
        artifact=artifact,
        issues=tuple(issues),
        probe=media_probe,
    )
