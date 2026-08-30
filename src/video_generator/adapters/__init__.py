"""Local tool adapters for audiovisual operations."""

from video_generator.adapters.ffmpeg import (
    AudioArtifact,
    CaptionCue,
    FFmpegError,
    SegmentArtifact,
    SequenceArtifact,
    SequenceClip,
    SequenceImage,
    compose_video_sequence,
    extract_audio,
    extract_segment,
)
from video_generator.adapters.ffprobe import MediaProbe, ProbeError, StreamProbe, probe_media

__all__ = [
    "AudioArtifact",
    "CaptionCue",
    "FFmpegError",
    "MediaProbe",
    "ProbeError",
    "SegmentArtifact",
    "SequenceArtifact",
    "SequenceClip",
    "SequenceImage",
    "StreamProbe",
    "extract_audio",
    "extract_segment",
    "compose_video_sequence",
    "probe_media",
]
