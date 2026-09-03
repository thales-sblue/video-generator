"""Local tool adapters for audiovisual operations."""

from video_generator.adapters.aligner import (
    AlignerError,
    WordTiming,
    transcribe_words,
)
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
    join_audio_segments,
)
from video_generator.adapters.ffprobe import MediaProbe, ProbeError, StreamProbe, probe_media

__all__ = [
    "AlignerError",
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
    "WordTiming",
    "extract_audio",
    "extract_segment",
    "compose_video_sequence",
    "detect_silences",
    "join_audio_segments",
    "transcribe_words",
    "probe_media",
    "synthesize_narration",
]
