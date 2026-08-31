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
class SequenceImage:
    source_path: str
    duration_seconds: float


@dataclass(frozen=True, slots=True)
class CaptionCue:
    text: str
    start_seconds: float
    end_seconds: float


@dataclass(frozen=True, slots=True)
class SequenceArtifact:
    source_paths: tuple[str, ...]
    output_path: str
    duration_seconds: float
    file_size_bytes: int
    narration_source_path: str | None = None
    caption_count: int = 0
    music_source_path: str | None = None
    music_gain_db: float | None = None
    image_count: int = 0


IMAGE_TIMELINE_FPS = 30


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


def _srt_timestamp(seconds: float) -> str:
    total_milliseconds = round(seconds * 1000)
    hours, remainder = divmod(total_milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d},{milliseconds:03d}"


def _escape_filter_path(path: Path) -> str:
    value = path.as_posix()
    for character in ("\\", "'", ":", ",", ";", "[", "]"):
        value = value.replace(character, f"\\{character}")
    return value


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
    narration_path: str | Path | None = None,
    captions: Sequence[CaptionCue] = (),
    music_path: str | Path | None = None,
    music_gain_db: float | None = None,
    canvas: tuple[int, int] | None = None,
    timeout_seconds: float = 300,
) -> SequenceArtifact:
    """Compose video clips and still images with optional captions, narration, and music.

    ``clips`` is an ordered timeline of ``SequenceClip`` (a trimmed range of a
    local video) and ``SequenceImage`` (a local still shown for a fixed
    duration). At least two segments and at least one ``SequenceClip`` are
    required. Video clips must already share pixel dimensions; when the timeline
    contains an image, ``canvas`` (the ``(width, height)`` of those clips) is
    required and every image is scaled to fit and letter-boxed onto it, then
    every segment is normalised to ``IMAGE_TIMELINE_FPS`` and ``yuv420p`` so the
    concat is deterministic.
    """

    if isinstance(clips, (str, bytes)) or not isinstance(clips, Sequence):
        raise FFmpegError("clips must be a sequence of SequenceClip or SequenceImage values")
    normalized_clips = tuple(clips)
    if len(normalized_clips) < 2:
        raise FFmpegError("video sequence requires at least two timeline segments")
    if not all(isinstance(clip, (SequenceClip, SequenceImage)) for clip in normalized_clips):
        raise FFmpegError("clips must contain only SequenceClip or SequenceImage values")
    if not any(isinstance(clip, SequenceClip) for clip in normalized_clips):
        raise FFmpegError("video sequence requires at least one video SequenceClip")
    if isinstance(captions, (str, bytes)) or not isinstance(captions, Sequence):
        raise FFmpegError("captions must be a sequence of CaptionCue values")
    normalized_captions = tuple(captions)
    if len(normalized_captions) > 500:
        raise FFmpegError("video sequence accepts at most 500 caption cues")
    if not all(isinstance(cue, CaptionCue) for cue in normalized_captions):
        raise FFmpegError("captions must contain only CaptionCue values")

    output = Path(output_path).expanduser().resolve()
    timeout = _time(timeout_seconds, "timeout_seconds")
    if timeout == 0:
        raise FFmpegError("timeout_seconds must be greater than zero")
    if output.suffix.lower() != ".mp4":
        raise FFmpegError("video sequence requires an .mp4 output_path")

    resolved_clips: list[tuple[str, Path, float, float]] = []
    for clip in normalized_clips:
        source = Path(clip.source_path).expanduser().resolve()
        if isinstance(clip, SequenceImage):
            span = _time(clip.duration_seconds, "image duration_seconds")
            if span == 0:
                raise FFmpegError("image duration_seconds must be greater than zero")
            kind, start, end = "image", 0.0, span
        else:
            start = _time(clip.start_seconds, "start_seconds")
            end = _time(clip.end_seconds, "end_seconds")
            if end <= start:
                raise FFmpegError("end_seconds must be greater than start_seconds")
            kind = "clip"
        if not source.exists() or not source.is_file():
            raise FFmpegError(f"source does not exist or is not a file: {source}")
        if os.path.normcase(str(source)) == os.path.normcase(str(output)):
            raise FFmpegError("output_path must not overwrite a source")
        resolved_clips.append((kind, source, start, end))
    duration = sum(end - start for _, _, start, end in resolved_clips)
    has_images = any(kind == "image" for kind, _, _, _ in resolved_clips)
    canvas_size: tuple[int, int] | None = None
    if has_images:
        if (
            not isinstance(canvas, tuple)
            or len(canvas) != 2
            or any(
                isinstance(value, bool) or not isinstance(value, int) or value <= 0
                for value in canvas
            )
        ):
            raise FFmpegError("a timeline with images requires a positive (width, height) canvas")
        canvas_size = (int(canvas[0]), int(canvas[1]))
    resolved_captions: list[tuple[str, float, float]] = []
    previous_end = 0.0
    for cue in normalized_captions:
        if not isinstance(cue.text, str) or not cue.text.strip():
            raise FFmpegError("caption text must be a non-empty string")
        text = cue.text.strip()
        if len(text) > 160:
            raise FFmpegError("caption text must contain at most 160 characters")
        if any(ord(character) < 32 for character in text):
            raise FFmpegError("caption text must not contain control characters")
        if any(character in text for character in "<>{}"):
            raise FFmpegError("caption text must not contain subtitle markup characters")
        start = _time(cue.start_seconds, "caption start_seconds")
        end = _time(cue.end_seconds, "caption end_seconds")
        if end <= start or round(end * 1000) <= round(start * 1000):
            raise FFmpegError("caption end_seconds must be at least 1 ms after start_seconds")
        if start < previous_end:
            raise FFmpegError("caption cues must be ordered and non-overlapping")
        if end > duration:
            raise FFmpegError("caption end_seconds must not exceed the sequence duration")
        resolved_captions.append((text, start, end))
        previous_end = end
    narration: Path | None = None
    if narration_path is not None:
        narration = Path(narration_path).expanduser().resolve()
        if not narration.exists() or not narration.is_file():
            raise FFmpegError(f"narration does not exist or is not a file: {narration}")
        if os.path.normcase(str(narration)) == os.path.normcase(str(output)):
            raise FFmpegError("output_path must not overwrite the narration source")
    music: Path | None = None
    gain: float | None = None
    if music_path is None:
        if music_gain_db is not None:
            raise FFmpegError("music_gain_db requires a music_path")
    else:
        music = Path(music_path).expanduser().resolve()
        if not music.exists() or not music.is_file():
            raise FFmpegError(f"music does not exist or is not a file: {music}")
        if os.path.normcase(str(music)) == os.path.normcase(str(output)):
            raise FFmpegError("output_path must not overwrite the music source")
        if (
            isinstance(music_gain_db, bool)
            or not isinstance(music_gain_db, (int, float))
            or not math.isfinite(music_gain_db)
            or music_gain_db < -60
            or music_gain_db > 0
        ):
            raise FFmpegError("music_gain_db must be a finite number from -60 to 0")
        gain = float(music_gain_db)
    if output.exists():
        raise FFmpegError(f"output already exists: {output}")

    try:
        executable = resolve_media_tool("ffmpeg", path_lookup=shutil.which)
    except ToolResolutionError as exc:
        raise FFmpegError(str(exc)) from exc
    if executable is None:
        raise FFmpegError("ffmpeg is not available locally or on PATH")

    temporary: Path | None = None
    caption_file: Path | None = None
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            prefix=f".{output.stem}-",
            suffix=output.suffix,
            dir=output.parent,
            delete=False,
        ) as reserved:
            temporary = Path(reserved.name)
        if resolved_captions:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                prefix=f".{output.stem}-captions-",
                suffix=".srt",
                dir=output.parent,
                delete=False,
            ) as caption_stream:
                caption_file = Path(caption_stream.name)
                for index, (text, start, end) in enumerate(resolved_captions, start=1):
                    caption_stream.write(
                        f"{index}\n{_srt_timestamp(start)} --> {_srt_timestamp(end)}\n{text}\n\n"
                    )
    except OSError as exc:
        if temporary is not None:
            _cleanup(temporary)
        if caption_file is not None:
            _cleanup(caption_file)
        raise FFmpegError(f"could not prepare output path: {output}") from exc

    command = [executable, "-v", "error", "-nostdin", "-y"]
    for kind, source, start, end in resolved_clips:
        if kind == "image":
            command.extend(
                ["-loop", "1", "-t", format(end - start, ".15g"), "-i", str(source)]
            )
        else:
            command.extend(["-i", str(source)])
    if narration is not None:
        command.extend(["-i", str(narration)])
    if music is not None:
        command.extend(["-stream_loop", "-1", "-i", str(music)])
    filters = []
    labels = []
    for index, (kind, _, start, end) in enumerate(resolved_clips):
        label = f"v{index}"
        if kind == "image":
            width, height = canvas_size  # type: ignore[misc]
            filters.append(
                f"[{index}:v:0]scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
                f"fps={IMAGE_TIMELINE_FPS},setsar=1,format=yuv420p,"
                f"trim=duration={format(end - start, '.15g')},setpts=PTS-STARTPTS[{label}]"
            )
        else:
            chain = (
                f"[{index}:v:0]trim=start={format(start, '.15g')}:end={format(end, '.15g')},"
                "setpts=PTS-STARTPTS"
            )
            if has_images:
                chain += f",fps={IMAGE_TIMELINE_FPS},setsar=1,format=yuv420p"
            filters.append(f"{chain}[{label}]")
        labels.append(f"[{label}]")
    video_output_label = "basev" if caption_file is not None else "outv"
    filters.append(
        f"{''.join(labels)}concat=n={len(labels)}:v=1:a=0[{video_output_label}]"
    )
    if caption_file is not None:
        caption_path = _escape_filter_path(caption_file)
        style = (
            "FontName=Sans,FontSize=24,PrimaryColour=&H00FFFFFF,"
            "OutlineColour=&H00000000,BackColour=&H99000000,"
            "BorderStyle=3,Outline=1,Shadow=0,Alignment=2,MarginV=24"
        )
        filters.append(
            f"[basev]subtitles=filename='{caption_path}':force_style='{style}'[outv]"
        )
    narration_index = len(resolved_clips) if narration is not None else None
    music_index = len(resolved_clips) + (1 if narration is not None else 0) if music is not None else None
    if narration_index is not None:
        filters.append(
            f"[{narration_index}:a:0]aresample=48000,"
            "aformat=sample_fmts=fltp:channel_layouts=stereo,apad,"
            f"atrim=duration={format(duration, '.15g')},asetpts=PTS-STARTPTS[voice]"
        )
    if music_index is not None and gain is not None:
        filters.append(
            f"[{music_index}:a:0]aresample=48000,"
            "aformat=sample_fmts=fltp:channel_layouts=stereo,"
            f"volume={format(gain, '.15g')}dB,"
            f"atrim=duration={format(duration, '.15g')},asetpts=PTS-STARTPTS[bed]"
        )
    if narration_index is not None and music_index is not None:
        filters.append(
            "[voice][bed]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,"
            "alimiter=limit=0.95:latency=1[outa]"
        )
    elif narration_index is not None:
        filters.append("[voice]anull[outa]")
    elif music_index is not None:
        filters.append("[bed]alimiter=limit=0.95:latency=1[outa]")
    command.extend(["-filter_complex", ";".join(filters), "-map", "[outv]"])
    if narration is None and music is None:
        command.append("-an")
    else:
        command.extend(["-map", "[outa]", "-c:a", "aac", "-b:a", "192k"])
    command.extend(
        [
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
    finally:
        if caption_file is not None:
            _cleanup(caption_file)

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
        source_paths=tuple(str(source) for _, source, _, _ in resolved_clips),
        output_path=str(output),
        duration_seconds=duration,
        file_size_bytes=size,
        narration_source_path=str(narration) if narration is not None else None,
        caption_count=len(resolved_captions),
        music_source_path=str(music) if music is not None else None,
        music_gain_db=gain,
        image_count=sum(1 for kind, _, _, _ in resolved_clips if kind == "image"),
    )
