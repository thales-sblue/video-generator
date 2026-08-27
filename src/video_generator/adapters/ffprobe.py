"""Read-only media inspection through the local ffprobe executable."""

from __future__ import annotations

import json
import math
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


class ProbeError(RuntimeError):
    """Raised when a local media source cannot be inspected."""


@dataclass(frozen=True, slots=True)
class StreamProbe:
    index: int
    codec_type: str | None
    codec_name: str | None
    duration_seconds: float | None
    width: int | None
    height: int | None
    sample_rate_hz: int | None
    channels: int | None


@dataclass(frozen=True, slots=True)
class MediaProbe:
    source_path: str
    file_size_bytes: int
    format_name: str | None
    duration_seconds: float | None
    bit_rate_bps: int | None
    streams: tuple[StreamProbe, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "source_path": self.source_path,
            "file_size_bytes": self.file_size_bytes,
            "format_name": self.format_name,
            "duration_seconds": self.duration_seconds,
            "bit_rate_bps": self.bit_rate_bps,
            "streams": [asdict(stream) for stream in self.streams],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _optional_text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _optional_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        result = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None
    return result if result >= 0 else None


def _optional_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) and result >= 0 else None


def _stream_from_payload(payload: object) -> StreamProbe:
    if not isinstance(payload, dict):
        raise ProbeError("ffprobe returned a non-object stream entry")
    index = _optional_int(payload.get("index"))
    if index is None:
        raise ProbeError("ffprobe returned a stream without a valid index")
    return StreamProbe(
        index=index,
        codec_type=_optional_text(payload.get("codec_type")),
        codec_name=_optional_text(payload.get("codec_name")),
        duration_seconds=_optional_float(payload.get("duration")),
        width=_optional_int(payload.get("width")),
        height=_optional_int(payload.get("height")),
        sample_rate_hz=_optional_int(payload.get("sample_rate")),
        channels=_optional_int(payload.get("channels")),
    )


def _parse_probe(payload: object, source: Path) -> MediaProbe:
    if not isinstance(payload, dict):
        raise ProbeError("ffprobe returned a non-object response")
    raw_format = payload.get("format", {})
    raw_streams = payload.get("streams", [])
    if not isinstance(raw_format, dict):
        raise ProbeError("ffprobe returned an invalid format object")
    if not isinstance(raw_streams, list):
        raise ProbeError("ffprobe returned an invalid streams array")
    streams = tuple(_stream_from_payload(item) for item in raw_streams)
    return MediaProbe(
        source_path=str(source),
        file_size_bytes=source.stat().st_size,
        format_name=_optional_text(raw_format.get("format_name")),
        duration_seconds=_optional_float(raw_format.get("duration")),
        bit_rate_bps=_optional_int(raw_format.get("bit_rate")),
        streams=streams,
    )


def probe_media(source_path: str | Path, *, timeout_seconds: float = 30) -> MediaProbe:
    """Inspect one immutable local file without writing media or metadata."""

    source = Path(source_path).expanduser().resolve()
    if not source.exists():
        raise ProbeError(f"source does not exist: {source}")
    if not source.is_file():
        raise ProbeError(f"source is not a file: {source}")
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or timeout_seconds <= 0
        or not math.isfinite(timeout_seconds)
    ):
        raise ProbeError("timeout_seconds must be a positive finite number")

    executable = shutil.which("ffprobe")
    if executable is None:
        raise ProbeError("ffprobe is not available on PATH; no installation was attempted")
    command = [
        executable,
        "-v",
        "error",
        "-show_format",
        "-show_streams",
        "-of",
        "json",
        str(source),
    ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ProbeError(f"ffprobe timed out while inspecting: {source}") from exc
    except OSError as exc:
        raise ProbeError(f"ffprobe could not inspect {source}: {type(exc).__name__}") from exc

    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        suffix = f": {detail}" if detail else ""
        raise ProbeError(f"ffprobe exited with {completed.returncode}{suffix}")
    try:
        payload: Any = json.loads(completed.stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ProbeError("ffprobe returned invalid JSON") from exc
    return _parse_probe(payload, source)
