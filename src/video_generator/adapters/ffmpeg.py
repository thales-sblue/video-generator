"""Non-destructive low-level media operations through local FFmpeg."""

from __future__ import annotations

import math
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from video_generator.tooling import ToolResolutionError, resolve_media_tool


class FFmpegError(RuntimeError):
    """Raised when a local FFmpeg operation cannot produce its artifact."""


@dataclass(frozen=True, slots=True)
class SegmentArtifact:
    source_path: str
    output_path: str
    start_seconds: float
    end_seconds: float
    file_size_bytes: int
    mode: str = "copy"


@dataclass(frozen=True, slots=True)
class AudioArtifact:
    source_path: str
    output_path: str
    sample_rate_hz: int
    channels: int
    file_size_bytes: int


@dataclass(frozen=True, slots=True)
class SequenceClip:
    source_path: str
    start_seconds: float
    end_seconds: float


@dataclass(frozen=True, slots=True)
class SequenceArtifact:
    source_paths: tuple[str, ...]
    output_path: str
    duration_seconds: float
    file_size_bytes: int


def _time(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FFmpegError(f"{name} must be a finite non-negative number")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise FFmpegError(f"{name} must be a finite non-negative number")
    return result


def _cleanup(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def extract_segment(
    source_path: str | Path,
    output_path: str | Path,
    *,
    start_seconds: float,
    end_seconds: float,
    timeout_seconds: float = 300,
    mode: str = "copy",
) -> SegmentArtifact:
    """Extract one time range into a new artifact without modifying the source."""

    source = Path(source_path).expanduser().resolve()
    output = Path(output_path).expanduser().resolve()
    start = _time(start_seconds, "start_seconds")
    end = _time(end_seconds, "end_seconds")
    timeout = _time(timeout_seconds, "timeout_seconds")
    if not isinstance(mode, str) or mode not in {"copy", "precise"}:
        raise FFmpegError("mode must be 'copy' or 'precise'")
    if end <= start:
        raise FFmpegError("end_seconds must be greater than start_seconds")
    if timeout == 0:
        raise FFmpegError("timeout_seconds must be greater than zero")
    if not source.exists():
        raise FFmpegError(f"source does not exist: {source}")
    if not source.is_file():
        raise FFmpegError(f"source is not a file: {source}")
    if os.path.normcase(str(source)) == os.path.normcase(str(output)):
        raise FFmpegError("output_path must not overwrite the source")
    if output.exists():
        raise FFmpegError(f"output already exists: {output}")
    if not output.suffix:
        raise FFmpegError("output_path must include a media file extension")
    if mode == "precise" and output.suffix.lower() != ".mp4":
        raise FFmpegError("precise mode requires an .mp4 output_path")

    try:
        executable = resolve_media_tool("ffmpeg", path_lookup=shutil.which)
    except ToolResolutionError as exc:
        raise FFmpegError(str(exc)) from exc
    if executable is None:
        raise FFmpegError("ffmpeg is not available locally or on PATH")

    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            prefix=f".{output.stem}-",
            suffix=output.suffix,
            dir=output.parent,
            delete=False,
        ) as reserved:
            temporary = Path(reserved.name)
    except OSError as exc:
        raise FFmpegError(f"could not prepare output path: {output}") from exc
    duration = end - start
    command = [
        executable,
        "-v",
        "error",
        "-nostdin",
        "-y",
    ]
    if mode == "copy":
        command.extend(
            [
                "-ss",
                format(start, ".15g"),
                "-i",
                str(source),
                "-t",
                format(duration, ".15g"),
                "-map",
                "0",
                "-c",
                "copy",
            ]
        )
    else:
        command.extend(
            [
                "-i",
                str(source),
                "-ss",
                format(start, ".15g"),
                "-t",
                format(duration, ".15g"),
                "-map",
                "0:v:0?",
                "-map",
                "0:a:0?",
                "-sn",
                "-dn",
                "-c:v",
                "libopenh264",
                "-b:v",
                "5M",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-movflags",
                "+faststart",
            ]
        )
    command.append(str(temporary))
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        _cleanup(temporary)
        raise FFmpegError(f"ffmpeg timed out while extracting: {source}") from exc
    except OSError as exc:
        _cleanup(temporary)
        raise FFmpegError(f"ffmpeg could not extract {source}: {type(exc).__name__}") from exc

    if completed.returncode != 0:
        _cleanup(temporary)
        detail = (completed.stderr or completed.stdout).strip()
        suffix = f": {detail}" if detail else ""
        raise FFmpegError(f"ffmpeg exited with {completed.returncode}{suffix}")
    if not temporary.is_file() or temporary.stat().st_size == 0:
        _cleanup(temporary)
        raise FFmpegError("ffmpeg reported success without creating a non-empty artifact")
    try:
        os.link(temporary, output)
    except OSError as exc:
        _cleanup(temporary)
        raise FFmpegError(f"could not publish output without overwriting: {output}") from exc
    _cleanup(temporary)
    try:
        size = output.stat().st_size
    except OSError as exc:
        raise FFmpegError(f"could not inspect published output: {output}") from exc
    return SegmentArtifact(str(source), str(output), start, end, size, mode)


def extract_audio(
    source_path: str | Path,
    output_path: str | Path,
    *,
    timeout_seconds: float = 300,
) -> AudioArtifact:
    """Extract the first audio stream as deterministic 48 kHz stereo PCM WAV."""

    source = Path(source_path).expanduser().resolve()
    output = Path(output_path).expanduser().resolve()
    timeout = _time(timeout_seconds, "timeout_seconds")
    if timeout == 0:
        raise FFmpegError("timeout_seconds must be greater than zero")
    if not source.exists():
        raise FFmpegError(f"source does not exist: {source}")
    if not source.is_file():
        raise FFmpegError(f"source is not a file: {source}")
    if os.path.normcase(str(source)) == os.path.normcase(str(output)):
        raise FFmpegError("output_path must not overwrite the source")
    if output.exists():
        raise FFmpegError(f"output already exists: {output}")
    if output.suffix.lower() != ".wav":
        raise FFmpegError("audio extraction requires a .wav output_path")

    try:
        executable = resolve_media_tool("ffmpeg", path_lookup=shutil.which)
    except ToolResolutionError as exc:
        raise FFmpegError(str(exc)) from exc
    if executable is None:
        raise FFmpegError("ffmpeg is not available locally or on PATH")

    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            prefix=f".{output.stem}-",
            suffix=output.suffix,
            dir=output.parent,
            delete=False,
        ) as reserved:
            temporary = Path(reserved.name)
    except OSError as exc:
        raise FFmpegError(f"could not prepare output path: {output}") from exc

    command = [
        executable,
        "-v",
        "error",
        "-nostdin",
        "-y",
        "-i",
        str(source),
        "-map",
        "0:a:0",
        "-vn",
        "-sn",
        "-dn",
        "-c:a",
        "pcm_s16le",
        "-ar",
        "48000",
        "-ac",
        "2",
        str(temporary),
    ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        _cleanup(temporary)
        raise FFmpegError(f"ffmpeg timed out while extracting audio: {source}") from exc
    except OSError as exc:
        _cleanup(temporary)
        raise FFmpegError(
            f"ffmpeg could not extract audio from {source}: {type(exc).__name__}"
        ) from exc

    if completed.returncode != 0:
        _cleanup(temporary)
        detail = (completed.stderr or completed.stdout).strip()
        suffix = f": {detail}" if detail else ""
        raise FFmpegError(f"ffmpeg exited with {completed.returncode}{suffix}")
    if not temporary.is_file() or temporary.stat().st_size == 0:
        _cleanup(temporary)
        raise FFmpegError("ffmpeg reported success without creating a non-empty audio artifact")
    try:
        os.link(temporary, output)
    except OSError as exc:
        _cleanup(temporary)
        raise FFmpegError(f"could not publish output without overwriting: {output}") from exc
    _cleanup(temporary)
    try:
        size = output.stat().st_size
    except OSError as exc:
        raise FFmpegError(f"could not inspect published output: {output}") from exc
    return AudioArtifact(str(source), str(output), 48000, 2, size)


def compose_video_sequence(
    clips: Sequence[SequenceClip],
    output_path: str | Path,
    *,
    timeout_seconds: float = 300,
) -> SequenceArtifact:
    """Trim and concatenate video clips into one silent H.264 MP4 timeline."""

    if isinstance(clips, (str, bytes)) or not isinstance(clips, Sequence):
        raise FFmpegError("clips must be a sequence of SequenceClip values")
    normalized_clips = tuple(clips)
    if len(normalized_clips) < 2:
        raise FFmpegError("video sequence requires at least two clips")
    if not all(isinstance(clip, SequenceClip) for clip in normalized_clips):
        raise FFmpegError("clips must contain only SequenceClip values")

    output = Path(output_path).expanduser().resolve()
    timeout = _time(timeout_seconds, "timeout_seconds")
    if timeout == 0:
        raise FFmpegError("timeout_seconds must be greater than zero")
    if output.suffix.lower() != ".mp4":
        raise FFmpegError("video sequence requires an .mp4 output_path")

    resolved_clips: list[tuple[Path, float, float]] = []
    for clip in normalized_clips:
        source = Path(clip.source_path).expanduser().resolve()
        start = _time(clip.start_seconds, "start_seconds")
        end = _time(clip.end_seconds, "end_seconds")
        if end <= start:
            raise FFmpegError("end_seconds must be greater than start_seconds")
        if not source.exists() or not source.is_file():
            raise FFmpegError(f"source does not exist or is not a file: {source}")
        if os.path.normcase(str(source)) == os.path.normcase(str(output)):
            raise FFmpegError("output_path must not overwrite a source")
        resolved_clips.append((source, start, end))
    if output.exists():
        raise FFmpegError(f"output already exists: {output}")

    try:
        executable = resolve_media_tool("ffmpeg", path_lookup=shutil.which)
    except ToolResolutionError as exc:
        raise FFmpegError(str(exc)) from exc
    if executable is None:
        raise FFmpegError("ffmpeg is not available locally or on PATH")

    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            prefix=f".{output.stem}-",
            suffix=output.suffix,
            dir=output.parent,
            delete=False,
        ) as reserved:
            temporary = Path(reserved.name)
    except OSError as exc:
        raise FFmpegError(f"could not prepare output path: {output}") from exc

    command = [executable, "-v", "error", "-nostdin", "-y"]
    for source, _, _ in resolved_clips:
        command.extend(["-i", str(source)])
    filters = []
    labels = []
    for index, (_, start, end) in enumerate(resolved_clips):
        label = f"v{index}"
        filters.append(
            f"[{index}:v:0]trim=start={format(start, '.15g')}:end={format(end, '.15g')},"
            f"setpts=PTS-STARTPTS[{label}]"
        )
        labels.append(f"[{label}]")
    filters.append(f"{''.join(labels)}concat=n={len(labels)}:v=1:a=0[outv]")
    command.extend(
        [
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[outv]",
            "-an",
            "-sn",
            "-dn",
            "-c:v",
            "libopenh264",
            "-b:v",
            "5M",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(temporary),
        ]
    )
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        _cleanup(temporary)
        raise FFmpegError("ffmpeg timed out while composing the video sequence") from exc
    except OSError as exc:
        _cleanup(temporary)
        raise FFmpegError(f"ffmpeg could not compose the video sequence: {type(exc).__name__}") from exc

    if completed.returncode != 0:
        _cleanup(temporary)
        detail = (completed.stderr or completed.stdout).strip()
        suffix = f": {detail}" if detail else ""
        raise FFmpegError(f"ffmpeg exited with {completed.returncode}{suffix}")
    if not temporary.is_file() or temporary.stat().st_size == 0:
        _cleanup(temporary)
        raise FFmpegError("ffmpeg reported success without creating a non-empty sequence")
    try:
        os.link(temporary, output)
    except OSError as exc:
        _cleanup(temporary)
        raise FFmpegError(f"could not publish output without overwriting: {output}") from exc
    _cleanup(temporary)
    try:
        size = output.stat().st_size
    except OSError as exc:
        raise FFmpegError(f"could not inspect published output: {output}") from exc
    return SequenceArtifact(
        source_paths=tuple(str(source) for source, _, _ in resolved_clips),
        output_path=str(output),
        duration_seconds=sum(end - start for _, start, end in resolved_clips),
        file_size_bytes=size,
    )
