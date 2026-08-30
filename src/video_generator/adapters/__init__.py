"""Local tool adapters for audiovisual operations."""

from video_generator.adapters.ffmpeg import (
    AudioArtifact,
    FFmpegError,
    SegmentArtifact,
    extract_audio,
    extract_segment,
)
from video_generator.adapters.ffprobe import MediaProbe, ProbeError, StreamProbe, probe_media

__all__ = [
    "AudioArtifact",
    "FFmpegError",
    "MediaProbe",
    "ProbeError",
    "SegmentArtifact",
    "StreamProbe",
    "extract_audio",
    "extract_segment",
    "probe_media",
]
