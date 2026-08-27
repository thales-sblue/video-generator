"""Non-destructive low-level media operations through local FFmpeg."""

from __future__ import annotations

import math
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


class FFmpegError(RuntimeError):
    """Raised when a local FFmpeg operation cannot produce its artifact."""


@dataclass(frozen=True, slots=True)
class SegmentArtifact:
    source_path: str
    output_path: str
    start_seconds: float
    end_seconds: float
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
) -> SegmentArtifact:
    """Copy one time range into a new artifact without modifying the source."""

    source = Path(source_path).expanduser().resolve()
    output = Path(output_path).expanduser().resolve()
    start = _time(start_seconds, "start_seconds")
    end = _time(end_seconds, "end_seconds")
    timeout = _time(timeout_seconds, "timeout_seconds")
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

    executable = shutil.which("ffmpeg")
    if executable is None:
        raise FFmpegError("ffmpeg is not available on PATH; no installation was attempted")

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
    return SegmentArtifact(str(source), str(output), start, end, size)
