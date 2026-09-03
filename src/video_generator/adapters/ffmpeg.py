"""Non-destructive low-level media operations through local FFmpeg."""

from __future__ import annotations

import math
import os
import re
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
    fit: str | None = None


@dataclass(frozen=True, slots=True)
class SequenceImage:
    source_path: str
    duration_seconds: float
    fit: str | None = None
    motion: str | None = None


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
    narration_text_sha256: str | None = None
    music_fade_in_seconds: float = 0.0
    music_fade_out_seconds: float = 0.0
    video_fade_in_seconds: float = 0.0
    video_fade_out_seconds: float = 0.0
    narration_lead_in_seconds: float = 0.0
    music_duck_db: float | None = None


IMAGE_TIMELINE_FPS = 30
# How long the music bed takes to reach the ducked level and to come back. The
# attack lands exactly on the first word (it ramps over the silence before it)
# and the release starts when the voice track ends.
MUSIC_DUCK_RAMP_SECONDS = 0.35


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


def _ass_timestamp(seconds: float) -> str:
    total_centiseconds = round(seconds * 100)
    hours, remainder = divmod(total_centiseconds, 360_000)
    minutes, remainder = divmod(remainder, 6_000)
    whole_seconds, centiseconds = divmod(remainder, 100)
    return f"{hours:d}:{minutes:02d}:{whole_seconds:02d}.{centiseconds:02d}"


# Advanced SubStation script rendered by libass. PlayResX/Y are pinned to the
# real frame so FontSize and every margin below are plain 1280x720 pixels: one
# readable line, held inside a platform-safe band (MarginV ~10% of the height)
# and kept clear of the frame edges. The text is drawn with a thick outline plus
# a soft shadow (BorderStyle 1) instead of an opaque box, so it stays legible on
# dark footage without a black bar across the frame. Caption text carries no
# override braces (the workflow already rejects "<>{}"), so it can never become
# script syntax.
# The caption style is expressed as fractions of the delivery canvas, not fixed
# pixels: a 32 px line that reads well at 720p is only 3% of a 1080p frame and
# disappears on a phone. The fractions are calibrated so a 1280x720 canvas keeps
# the numbers this project shipped with, and every larger canvas scales with it.
_CAPTION_FONT_FRACTION = 0.04444  # 32 px at 720p, 48 px at 1080p
_CAPTION_SIDE_MARGIN_FRACTION = 0.109375  # 140 px at 1280 wide
_CAPTION_BOTTOM_MARGIN_FRACTION = 0.1  # 72 px at 720 tall

_CAPTION_ASS_HEADER = (
    "[Script Info]\n"
    "ScriptType: v4.00+\n"
    "WrapStyle: 2\n"
    "ScaledBorderAndShadow: yes\n"
    "PlayResX: {width}\n"
    "PlayResY: {height}\n"
    "\n"
    "[V4+ Styles]\n"
    "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
    "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
    "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
    "MarginR, MarginV, Encoding\n"
    "Style: Caption,Sans,{font_size},&H00FFFFFF,&H00FFFFFF,&H00101010,&H80000000,"
    "0,0,0,0,100,100,0,0,1,{outline},{shadow},2,{margin_x},{margin_x},{margin_y},1\n"
    "\n"
    "[Events]\n"
    "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
)


def _caption_ass_header(width: int, height: int) -> str:
    """Render the caption style scaled to the delivery canvas.

    A burned caption competes with whatever is behind it, so the outline and the
    shadow grow with the frame too: white text over a bright photo is only
    readable because of them.
    """

    return _CAPTION_ASS_HEADER.format(
        width=width,
        height=height,
        font_size=max(12, round(height * _CAPTION_FONT_FRACTION)),
        outline=max(2, round(height / 240)),
        shadow=max(1, round(height / 720)),
        margin_x=max(8, round(width * _CAPTION_SIDE_MARGIN_FRACTION)),
        margin_y=max(8, round(height * _CAPTION_BOTTOM_MARGIN_FRACTION)),
    )


def _fit_filter(fit: str, width: int, height: int) -> str:
    """Deterministic scale-to-canvas chain for an explicit target format.

    ``contain`` fits the whole frame and pads the remainder with black
    (letterbox/pillarbox); ``cover`` fills the canvas and crops the overflow
    from the centre. Both end on ``setsar=1`` so the concat sees square pixels.
    """

    if fit == "contain":
        return (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease:"
            f"force_divisible_by=2,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1"
        )
    if fit == "cover":
        return (
            f"scale={width}:{height}:force_original_aspect_ratio=increase:"
            f"force_divisible_by=2,"
            f"crop={width}:{height}:(iw-{width})/2:(ih-{height})/2,setsar=1"
        )
    raise FFmpegError('fit must be "contain" or "cover"')


KEN_BURNS_MOTIONS = ("zoom_in", "zoom_out", "pan_left", "pan_right", "pan_up", "pan_down")
# Fractional travel over the clip: zooms cover 1.0 <-> 1.12, pans hold 1.12 and
# translate across the crop margin that zoom opens up. Fixed on purpose so the
# move is a pure function of (motion, canvas, duration).
_KEN_BURNS_TRAVEL = 0.12
_KEN_BURNS_PAN_ZOOM = 1.0 + _KEN_BURNS_TRAVEL
_KEN_BURNS_UPSCALE = 4


def _ken_burns_filter(motion: str, width: int, height: int, frames: int) -> str:
    """A deterministic ``zoompan`` pass for a still frame.

    The frame is pre-upscaled (``scale=iw*4:ih*4``) so the sub-pixel zoom steps
    do not jitter, then ``zoompan`` walks a window across it — one output frame
    per input frame (``d=1``) — and rescales back to the canvas (``s=WxH``).
    ``on`` is the cumulative output frame index, so ``on/(frames-1)`` ramps
    linearly from 0 to 1 across the clip.
    """

    if motion not in KEN_BURNS_MOTIONS:
        raise FFmpegError("motion must be one of " + ", ".join(KEN_BURNS_MOTIONS))
    progress = f"on/{max(frames - 1, 1)}"
    travel = format(_KEN_BURNS_TRAVEL, ".15g")
    pan_zoom = format(_KEN_BURNS_PAN_ZOOM, ".15g")
    centre_x = "iw/2-(iw/zoom/2)"
    centre_y = "ih/2-(ih/zoom/2)"
    if motion == "zoom_in":
        zoom, pan_x, pan_y = f"1+{travel}*{progress}", centre_x, centre_y
    elif motion == "zoom_out":
        zoom, pan_x, pan_y = f"{pan_zoom}-{travel}*{progress}", centre_x, centre_y
    elif motion == "pan_left":
        zoom = pan_zoom
        pan_x, pan_y = f"(iw-iw/zoom)*(1-{progress})", "(ih-ih/zoom)/2"
    elif motion == "pan_right":
        zoom = pan_zoom
        pan_x, pan_y = f"(iw-iw/zoom)*({progress})", "(ih-ih/zoom)/2"
    elif motion == "pan_up":
        zoom = pan_zoom
        pan_x, pan_y = "(iw-iw/zoom)/2", f"(ih-ih/zoom)*(1-{progress})"
    else:  # pan_down
        zoom = pan_zoom
        pan_x, pan_y = "(iw-iw/zoom)/2", f"(ih-ih/zoom)*({progress})"
    return (
        f"scale=iw*{_KEN_BURNS_UPSCALE}:ih*{_KEN_BURNS_UPSCALE},"
        f"zoompan=z={zoom}:x={pan_x}:y={pan_y}:d=1:s={width}x{height}:"
        f"fps={IMAGE_TIMELINE_FPS}"
    )


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


MAX_JOINED_SEGMENTS = 400


def join_audio_segments(
    segments: Sequence[tuple[str | Path, float]],
    output_path: str | Path,
    *,
    lead_in_seconds: float = 0.0,
    timeout_seconds: float = 600,
) -> AudioArtifact:
    """Join narration segments into one 48 kHz stereo WAV with explicit pauses.

    ``segments`` is an ordered sequence of ``(wav_path, pause_after_seconds)``
    pairs: each file is decoded, padded with exactly its own silence, and the
    padded pieces are concatenated. ``lead_in_seconds`` prepends silence before
    the first word so the timeline can open on an image before the voice starts.

    The pauses are the whole point: a script synthesised as one block gets the
    engine's own uniform spacing, while joining per-unit renders lets the caller
    decide where the voice breathes. Every path is passed to FFmpeg as a
    structured argument; nothing is interpolated into a shell or a filter path.
    """

    items = list(segments)
    if not items:
        raise FFmpegError("joining audio requires at least one segment")
    if len(items) > MAX_JOINED_SEGMENTS:
        raise FFmpegError(f"at most {MAX_JOINED_SEGMENTS} segments can be joined")
    lead_in = _time(lead_in_seconds, "lead_in_seconds")
    timeout = _time(timeout_seconds, "timeout_seconds")
    if timeout == 0:
        raise FFmpegError("timeout_seconds must be greater than zero")

    output = Path(output_path).expanduser().resolve()
    if output.suffix.lower() != ".wav":
        raise FFmpegError("joining audio requires a .wav output_path")
    if output.exists():
        raise FFmpegError(f"output already exists: {output}")

    sources: list[Path] = []
    gaps: list[float] = []
    for index, item in enumerate(items):
        try:
            raw_path, raw_gap = item
        except (TypeError, ValueError) as exc:
            raise FFmpegError(
                f"segment {index} must be a (path, pause_after_seconds) pair"
            ) from exc
        source = Path(raw_path).expanduser().resolve()
        if not source.is_file():
            raise FFmpegError(f"segment {index} source does not exist: {source}")
        if os.path.normcase(str(source)) == os.path.normcase(str(output)):
            raise FFmpegError("output_path must not overwrite a segment source")
        gap = _time(raw_gap, f"segment {index} pause_after_seconds")
        if gap > 30:
            raise FFmpegError(f"segment {index} pause must be at most 30 seconds")
        sources.append(source)
        gaps.append(gap)

    try:
        executable = resolve_media_tool("ffmpeg", path_lookup=shutil.which)
    except ToolResolutionError as exc:
        raise FFmpegError(str(exc)) from exc
    if executable is None:
        raise FFmpegError("ffmpeg is not available locally or on PATH")

    steps: list[str] = []
    labels: list[str] = []
    for index, gap in enumerate(gaps):
        label = f"s{index}"
        chain = f"[{index}:a]aformat=sample_fmts=s16:sample_rates=48000:channel_layouts=stereo"
        if gap > 0:
            chain += f",apad=pad_dur={gap:.3f}"
        steps.append(f"{chain}[{label}]")
        labels.append(label)
    joined = "".join(f"[{label}]" for label in labels)
    steps.append(f"{joined}concat=n={len(labels)}:v=0:a=1[voice]")
    final_label = "voice"
    if lead_in > 0:
        delay_ms = int(round(lead_in * 1000))
        steps.append(f"[voice]adelay={delay_ms}:all=1[out]")
        final_label = "out"
    filter_complex = ";".join(steps)

    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            prefix=f".{output.stem}-join-",
            suffix=".wav",
            dir=output.parent,
            delete=False,
        ) as reserved:
            temporary = Path(reserved.name)
    except OSError as exc:
        raise FFmpegError(f"could not prepare output path: {output}") from exc

    command = [executable, "-v", "error", "-nostdin", "-y"]
    for source in sources:
        command.extend(["-i", str(source)])
    command.extend(
        [
            "-filter_complex",
            filter_complex,
            "-map",
            f"[{final_label}]",
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
        raise FFmpegError("ffmpeg timed out while joining narration segments") from exc
    except OSError as exc:
        _cleanup(temporary)
        raise FFmpegError(
            f"ffmpeg could not join narration segments: {type(exc).__name__}"
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
    return AudioArtifact(str(sources[0]), str(output), 48000, 2, size)


# Defaults for :func:`detect_silences`. -35 dBFS sits below a synthesised voice
# but above the noise floor of a clean TTS render, and 0.12 s is long enough to
# skip the stops inside a word while still catching a sentence break.
SILENCE_NOISE_DB = -35.0
SILENCE_MIN_SECONDS = 0.12
_SILENCE_START = re.compile(r"silence_start:\s*(-?\d+(?:\.\d+)?)")
_SILENCE_END = re.compile(r"silence_end:\s*(-?\d+(?:\.\d+)?)")


def detect_silences(
    source_path: str | Path,
    *,
    noise_db: float = SILENCE_NOISE_DB,
    min_duration_seconds: float = SILENCE_MIN_SECONDS,
    timeout_seconds: float = 120,
) -> tuple[tuple[float, float], ...]:
    """Report the quiet spans of a local audio file, in seconds.

    A read-only ``silencedetect`` pass: nothing is decoded to disk and the
    source is never touched. Spans come back ordered and closed; a silence that
    runs to the end of the file has no ``silence_end`` and is dropped, since it
    marks where the audio stops rather than a pause inside it.
    """

    source = Path(source_path).expanduser().resolve()
    if not source.exists() or not source.is_file():
        raise FFmpegError(f"source does not exist or is not a file: {source}")
    if (
        isinstance(noise_db, bool)
        or not isinstance(noise_db, (int, float))
        or not math.isfinite(noise_db)
        or noise_db < -90
        or noise_db >= 0
    ):
        raise FFmpegError("noise_db must be a finite number from -90 to less than 0")
    minimum = _time(min_duration_seconds, "min_duration_seconds")
    if minimum == 0:
        raise FFmpegError("min_duration_seconds must be greater than zero")
    timeout = _time(timeout_seconds, "timeout_seconds")
    if timeout == 0:
        raise FFmpegError("timeout_seconds must be greater than zero")

    try:
        executable = resolve_media_tool("ffmpeg", path_lookup=shutil.which)
    except ToolResolutionError as exc:
        raise FFmpegError(str(exc)) from exc
    if executable is None:
        raise FFmpegError("ffmpeg is not available locally or on PATH")

    command = [
        executable,
        "-hide_banner",
        "-nostdin",
        "-i",
        str(source),
        "-map",
        "0:a:0",
        "-af",
        f"silencedetect=noise={format(float(noise_db), '.15g')}dB:"
        f"d={format(minimum, '.15g')}",
        "-f",
        "null",
        "-",
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
        raise FFmpegError("ffmpeg timed out while detecting silences") from exc
    except OSError as exc:
        raise FFmpegError(f"ffmpeg could not detect silences: {type(exc).__name__}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        suffix = f": {detail}" if detail else ""
        raise FFmpegError(f"ffmpeg exited with {completed.returncode}{suffix}")

    spans: list[tuple[float, float]] = []
    pending: float | None = None
    for line in (completed.stderr or "").splitlines():
        started = _SILENCE_START.search(line)
        if started is not None:
            pending = max(float(started.group(1)), 0.0)
            continue
        ended = _SILENCE_END.search(line)
        if ended is not None and pending is not None:
            finish = float(ended.group(1))
            if finish > pending:
                spans.append((pending, finish))
            pending = None
    return tuple(spans)


def compose_video_sequence(
    clips: Sequence[SequenceClip],
    output_path: str | Path,
    *,
    narration_path: str | Path | None = None,
    narration_lead_in_seconds: float = 0.0,
    captions: Sequence[CaptionCue] = (),
    music_path: str | Path | None = None,
    music_gain_db: float | None = None,
    music_fade_in_seconds: float = 0.0,
    music_fade_out_seconds: float = 0.0,
    music_duck_db: float | None = None,
    narration_duration_seconds: float | None = None,
    video_fade_in_seconds: float = 0.0,
    video_fade_out_seconds: float = 0.0,
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

    When any segment carries a ``fit`` (``"contain"`` or ``"cover"``) the
    timeline is an explicit target format: ``canvas`` is the delivery
    resolution, sources of different sizes and aspect ratios are accepted, and
    every segment is deterministically scaled to the canvas (``contain``
    letterboxes, ``cover`` centre-crops) then pinned to ``IMAGE_TIMELINE_FPS``,
    ``setsar=1`` and ``yuv420p``. With no ``fit`` on any segment the legacy
    filter graph is emitted unchanged.

    ``narration_lead_in_seconds`` delays the voice so the timeline can open on
    picture and music alone; it requires ``narration_path`` and must be shorter
    than the timeline.

    ``music_duck_db`` attenuates the bed by exactly that many decibels while the
    voice runs, ramping over ``MUSIC_DUCK_RAMP_SECONDS`` at each edge; it
    requires both a ``music_path`` and a ``narration_path``. The ducked span
    starts at the lead-in and ends after ``narration_duration_seconds`` (the
    voice track's own length), so the bed comes back up for the tail; without
    that length the duck holds to the end of the timeline.
    """

    if isinstance(clips, (str, bytes)) or not isinstance(clips, Sequence):
        raise FFmpegError("clips must be a sequence of SequenceClip or SequenceImage values")
    normalized_clips = tuple(clips)
    if len(normalized_clips) < 2:
        raise FFmpegError("video sequence requires at least two timeline segments")
    if not all(isinstance(clip, (SequenceClip, SequenceImage)) for clip in normalized_clips):
        raise FFmpegError("clips must contain only SequenceClip or SequenceImage values")
    if (
        not any(isinstance(clip, SequenceClip) for clip in normalized_clips)
        and canvas is None
    ):
        raise FFmpegError(
            "an all-image video sequence requires a (width, height) canvas"
        )
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
    resolved_fits: list[str | None] = []
    resolved_motions: list[str | None] = []
    for clip in normalized_clips:
        source = Path(clip.source_path).expanduser().resolve()
        motion: str | None = None
        if isinstance(clip, SequenceImage):
            span = _time(clip.duration_seconds, "image duration_seconds")
            if span == 0:
                raise FFmpegError("image duration_seconds must be greater than zero")
            kind, start, end = "image", 0.0, span
            motion = clip.motion
            if motion is not None and motion not in KEN_BURNS_MOTIONS:
                raise FFmpegError("motion must be one of " + ", ".join(KEN_BURNS_MOTIONS))
        else:
            start = _time(clip.start_seconds, "start_seconds")
            end = _time(clip.end_seconds, "end_seconds")
            if end <= start:
                raise FFmpegError("end_seconds must be greater than start_seconds")
            kind = "clip"
        fit = clip.fit
        if fit is not None and fit not in ("contain", "cover"):
            raise FFmpegError('fit must be "contain" or "cover"')
        if not source.exists() or not source.is_file():
            raise FFmpegError(f"source does not exist or is not a file: {source}")
        if os.path.normcase(str(source)) == os.path.normcase(str(output)):
            raise FFmpegError("output_path must not overwrite a source")
        resolved_clips.append((kind, source, start, end))
        resolved_fits.append(fit)
        resolved_motions.append(motion)
    duration = sum(end - start for _, _, start, end in resolved_clips)
    has_images = any(kind == "image" for kind, _, _, _ in resolved_clips)
    # An explicit target format: every segment is deterministically scaled to
    # the canvas (contain/cover), so heterogeneous sources can share the concat.
    normalize_to_canvas = any(fit is not None for fit in resolved_fits)
    canvas_size: tuple[int, int] | None = None
    if canvas is not None:
        if (
            not isinstance(canvas, tuple)
            or len(canvas) != 2
            or any(
                isinstance(value, bool) or not isinstance(value, int) or value <= 0
                for value in canvas
            )
        ):
            raise FFmpegError("canvas must be a positive (width, height) tuple")
        canvas_size = (int(canvas[0]), int(canvas[1]))
    if has_images and canvas_size is None:
        raise FFmpegError("a timeline with images requires a positive (width, height) canvas")
    if normalize_to_canvas and canvas_size is None:
        raise FFmpegError("a segment fit requires a positive (width, height) canvas")
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
    if resolved_captions and canvas_size is None:
        raise FFmpegError("captions require a (width, height) canvas for pixel-accurate layout")
    narration: Path | None = None
    narration_lead_in = _time(narration_lead_in_seconds, "narration_lead_in_seconds")
    if narration_path is None:
        if narration_lead_in:
            raise FFmpegError("narration_lead_in_seconds requires a narration_path")
    else:
        narration = Path(narration_path).expanduser().resolve()
        if not narration.exists() or not narration.is_file():
            raise FFmpegError(f"narration does not exist or is not a file: {narration}")
        if os.path.normcase(str(narration)) == os.path.normcase(str(output)):
            raise FFmpegError("output_path must not overwrite the narration source")
        if narration_lead_in >= duration:
            raise FFmpegError("narration lead-in must be shorter than the sequence duration")
    music: Path | None = None
    gain: float | None = None
    fade_in = _time(music_fade_in_seconds, "music_fade_in_seconds")
    fade_out = _time(music_fade_out_seconds, "music_fade_out_seconds")
    if music_path is None:
        if music_gain_db is not None:
            raise FFmpegError("music_gain_db requires a music_path")
        if fade_in or fade_out:
            raise FFmpegError("music fades require a music_path")
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
        if fade_in + fade_out > duration:
            raise FFmpegError("music fades must not exceed the sequence duration")
    duck: float | None = None
    duck_start = 0.0
    duck_end = duration
    if music_duck_db is None:
        if narration_duration_seconds is not None:
            raise FFmpegError("narration_duration_seconds requires a music_duck_db")
    else:
        if music is None or narration is None:
            raise FFmpegError("music_duck_db requires both a music_path and a narration_path")
        if (
            isinstance(music_duck_db, bool)
            or not isinstance(music_duck_db, (int, float))
            or not math.isfinite(music_duck_db)
            or music_duck_db < -60
            or music_duck_db >= 0
        ):
            raise FFmpegError("music_duck_db must be a finite number from -60 to less than 0")
        duck = float(music_duck_db)
        duck_start = narration_lead_in
        if narration_duration_seconds is not None:
            voice = _time(narration_duration_seconds, "narration_duration_seconds")
            if voice == 0:
                raise FFmpegError("narration_duration_seconds must be greater than zero")
            # a synthesised voice may run a few milliseconds past the timeline;
            # the duck then simply never releases.
            duck_end = min(narration_lead_in + voice, duration)
    video_fade_in = _time(video_fade_in_seconds, "video_fade_in_seconds")
    video_fade_out = _time(video_fade_out_seconds, "video_fade_out_seconds")
    if video_fade_in + video_fade_out > duration:
        raise FFmpegError("video fades must not exceed the sequence duration")
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
            caption_width, caption_height = canvas_size  # type: ignore[misc]
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                prefix=f".{output.stem}-captions-",
                suffix=".ass",
                dir=output.parent,
                delete=False,
            ) as caption_stream:
                caption_file = Path(caption_stream.name)
                caption_stream.write(
                    _caption_ass_header(caption_width, caption_height)
                )
                for text, start, end in resolved_captions:
                    caption_stream.write(
                        f"Dialogue: 0,{_ass_timestamp(start)},{_ass_timestamp(end)},"
                        f"Caption,,0,0,0,,{text}\n"
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

        def _motion_chain(width: int, height: int, _motion=resolved_motions[index],
                          _span=end - start) -> str:
            """The optional Ken Burns ``zoompan`` pass for this still, or ``""``."""
            if _motion is None:
                return ""
            frames = round(_span * IMAGE_TIMELINE_FPS)
            return "," + _ken_burns_filter(_motion, width, height, frames)

        if normalize_to_canvas:
            width, height = canvas_size  # type: ignore[misc]
            # An explicit target format falls back to contain for any segment
            # that did not state a fit, so a mixed timeline still normalises.
            fit = resolved_fits[index] or "contain"
            scale_chain = _fit_filter(fit, width, height)
            if kind == "image":
                filters.append(
                    f"[{index}:v:0]{scale_chain},"
                    f"fps={IMAGE_TIMELINE_FPS}{_motion_chain(width, height)},format=yuv420p,"
                    f"trim=duration={format(end - start, '.15g')},"
                    f"setpts=PTS-STARTPTS[{label}]"
                )
            else:
                filters.append(
                    f"[{index}:v:0]trim=start={format(start, '.15g')}:"
                    f"end={format(end, '.15g')},setpts=PTS-STARTPTS,"
                    f"{scale_chain},fps={IMAGE_TIMELINE_FPS},format=yuv420p[{label}]"
                )
        elif kind == "image":
            width, height = canvas_size  # type: ignore[misc]
            filters.append(
                f"[{index}:v:0]scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
                f"fps={IMAGE_TIMELINE_FPS},setsar=1{_motion_chain(width, height)},format=yuv420p,"
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
    has_video_fades = video_fade_in > 0 or video_fade_out > 0
    concat_label = "basev" if (caption_file is not None or has_video_fades) else "outv"
    filters.append(
        f"{''.join(labels)}concat=n={len(labels)}:v=1:a=0[{concat_label}]"
    )
    current_video = concat_label
    if caption_file is not None:
        caption_path = _escape_filter_path(caption_file)
        caption_label = "capv" if has_video_fades else "outv"
        # The .ass script already carries the style and a frame-matched PlayRes,
        # so libass lays the text out in real pixels with no force_style guesswork.
        filters.append(
            f"[{current_video}]subtitles=filename='{caption_path}'[{caption_label}]"
        )
        current_video = caption_label
    if has_video_fades:
        # A gentle open from black and close to black over the whole picture,
        # captions included. loudnorm already gives the audio its own fades.
        fade_parts = []
        if video_fade_in > 0:
            fade_parts.append(f"fade=t=in:st=0:d={format(video_fade_in, '.15g')}")
        if video_fade_out > 0:
            fade_parts.append(
                f"fade=t=out:st={format(duration - video_fade_out, '.15g')}:"
                f"d={format(video_fade_out, '.15g')}"
            )
        filters.append(f"[{current_video}]{','.join(fade_parts)}[outv]")
    narration_index = len(resolved_clips) if narration is not None else None
    music_index = len(resolved_clips) + (1 if narration is not None else 0) if music is not None else None
    if narration_index is not None:
        # A lead-in holds the voice back so the timeline can open on atmosphere
        # (and the music bed) alone; apad/atrim still fill out to the timeline.
        delay = (
            f"adelay={round(narration_lead_in * 1000)}:all=1," if narration_lead_in else ""
        )
        filters.append(
            f"[{narration_index}:a:0]aresample=48000,"
            f"aformat=sample_fmts=fltp:channel_layouts=stereo,{delay}apad,"
            f"atrim=duration={format(duration, '.15g')},asetpts=PTS-STARTPTS[voice]"
        )
    if music_index is not None and gain is not None:
        bed = (
            f"[{music_index}:a:0]aresample=48000,"
            "aformat=sample_fmts=fltp:channel_layouts=stereo,"
            f"volume={format(gain, '.15g')}dB"
        )
        if fade_in > 0:
            bed += f",afade=t=in:st=0:d={format(fade_in, '.15g')}"
        if fade_out > 0:
            bed += (
                f",afade=t=out:st={format(duration - fade_out, '.15g')}:"
                f"d={format(fade_out, '.15g')}"
            )
        if duck is not None:
            # An exact, plan-derived envelope instead of a signal-driven
            # sidechain: the dip is always the stated number of decibels, however
            # loud the voice source happens to be, and it stays reproducible from
            # the plan alone. The attack ramp finishes as the voice starts and
            # the release ramp begins when the voice track ends.
            level = format(10 ** (duck / 20), ".15g")
            ramp = format(MUSIC_DUCK_RAMP_SECONDS, ".15g")
            attack = format(duck_start - MUSIC_DUCK_RAMP_SECONDS, ".15g")
            release = format(duck_end + MUSIC_DUCK_RAMP_SECONDS, ".15g")
            bed += (
                f",volume=volume='1-(1-{level})"
                f"*clip((t-({attack}))/{ramp},0,1)"
                f"*clip(({release}-t)/{ramp},0,1)':eval=frame"
            )
        bed += f",atrim=duration={format(duration, '.15g')},asetpts=PTS-STARTPTS[bed]"
        filters.append(bed)
    # Master chain shared by every audio branch: a short fade-in kills any start
    # click, then EBU R128 loudness normalisation brings the mix to a comfortable
    # online-publishing target (-14 LUFS, true peak -1.5 dBTP) with no clipping.
    # loudnorm resamples internally, so pin 48 kHz again afterwards.
    master = "" if duration <= 1.0 else "afade=t=in:st=0:d=0.3,"
    master += "loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000"
    if narration_index is not None and music_index is not None:
        filters.append(
            "[voice][bed]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,"
            f"alimiter=limit=0.95:latency=1,{master}[outa]"
        )
    elif narration_index is not None:
        filters.append(f"[voice]{master}[outa]")
    elif music_index is not None:
        filters.append(f"[bed]alimiter=limit=0.95:latency=1,{master}[outa]")
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
            # libopenh264 has no CRF mode; a generous 720p bitrate keeps smooth
            # dark gradients from blocking after the clip -> timeline re-encode.
            "-b:v",
            "10M",
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
        music_fade_in_seconds=fade_in if music is not None else 0.0,
        music_fade_out_seconds=fade_out if music is not None else 0.0,
        video_fade_in_seconds=video_fade_in,
        video_fade_out_seconds=video_fade_out,
        narration_lead_in_seconds=narration_lead_in,
        music_duck_db=duck,
    )
