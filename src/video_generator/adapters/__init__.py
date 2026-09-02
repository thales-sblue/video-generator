"""Local tool adapters for audiovisual operations."""

from video_generator.adapters.kokoro import (
    KokoroError,
    NarrationArtifact,
    synthesize_narration,
)
from video_generator.adapters.ffmpeg import (
    AudioArtifact,
    CaptionCue,
    FFmpegError,
    SegmentArtifact,
    SequenceArtifact,
    SequenceClip,
    SequenceImage,
    compose_video_sequence,
    detect_silences,
    extract_audio,
    extract_segment,
)
from video_generator.adapters.ffprobe import MediaProbe, ProbeError, StreamProbe, probe_media

__all__ = [
    "AudioArtifact",
    "CaptionCue",
    "FFmpegError",
    "KokoroError",
    "MediaProbe",
    "NarrationArtifact",
    "ProbeError",
    "SegmentArtifact",
    "SequenceArtifact",
    "SequenceClip",
    "SequenceImage",
    "StreamProbe",
    "extract_audio",
    "extract_segment",
    "compose_video_sequence",
    "detect_silences",
    "probe_media",
    "synthesize_narration",
]
