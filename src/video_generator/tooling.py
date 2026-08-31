"""Fail-closed resolution of approved local audiovisual tools."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from functools import lru_cache
from pathlib import Path
from typing import Callable


PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOCK_PATH = PROJECT_ROOT / "config" / "ffmpeg-lock.json"
LOCAL_ROOT = PROJECT_ROOT / ".local-tools" / "ffmpeg"
SUPPORTED_TOOLS = {"ffmpeg", "ffprobe"}

KOKORO_LOCAL_ROOT = PROJECT_ROOT / ".local-tools" / "kokoro"
KOKORO_ASSET_NAMES = ("kokoro-v1.0.onnx", "voices-v1.0.bin")


class ToolResolutionError(RuntimeError):
    """Raised when a project-local tool exists but fails its integrity lock."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@lru_cache(maxsize=1)
def _validated_local_bin() -> Path | None:
    if not LOCAL_ROOT.exists():
        return None
    try:
        lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
        if lock["schema_version"] != 1 or not isinstance(lock["bin_files"], dict):
            raise ToolResolutionError("unsupported FFmpeg integrity lock")
        install = LOCAL_ROOT / lock["install_directory"] / "bin"
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ToolResolutionError("cannot load the FFmpeg integrity lock") from exc

    local_root = LOCAL_ROOT.resolve()
    bin_path = install.resolve()
    try:
        bin_path.relative_to(local_root)
    except ValueError as exc:
        raise ToolResolutionError("FFmpeg lock resolves outside .local-tools") from exc
    if not bin_path.is_dir():
        raise ToolResolutionError(f"locked FFmpeg bin directory is missing: {bin_path}")

    expected = lock["bin_files"]
    try:
        entries = tuple(bin_path.iterdir())
        if any(item.is_symlink() or not item.is_file() for item in entries):
            raise ToolResolutionError("FFmpeg bin contains a non-regular file")
        actual_names = {item.name for item in entries}
        if actual_names != set(expected):
            raise ToolResolutionError("FFmpeg bin file set differs from the integrity lock")
        for name, fingerprint in expected.items():
            path = bin_path / name
            size = fingerprint["size"]
            expected_hash = fingerprint["sha256"]
            if path.stat().st_size != size or _sha256(path) != expected_hash:
                raise ToolResolutionError(f"FFmpeg integrity check failed: {name}")
    except ToolResolutionError:
        raise
    except (OSError, KeyError, TypeError) as exc:
        raise ToolResolutionError("cannot verify the FFmpeg installation") from exc
    return bin_path


def kokoro_assets_root() -> Path:
    """Return the directory that should hold the local Kokoro model files."""

    override = os.environ.get("KOKORO_HOME")
    return Path(override).expanduser() if override else KOKORO_LOCAL_ROOT


def resolve_kokoro_assets() -> tuple[str, str] | None:
    """Return ``(model_path, voices_path)`` for the local Kokoro ONNX model.

    Returns ``None`` when the assets directory does not exist at all. Fails
    closed with :class:`ToolResolutionError` when the directory exists but a
    required file is missing, empty, or not a regular file, so a partial or
    tampered install never silently degrades a narration render.
    """

    root = kokoro_assets_root()
    if not root.exists():
        return None
    resolved: list[str] = []
    for name in KOKORO_ASSET_NAMES:
        candidate = root / name
        if candidate.is_symlink() or not candidate.is_file():
            raise ToolResolutionError(f"Kokoro asset is missing or not a regular file: {name}")
        if candidate.stat().st_size == 0:
            raise ToolResolutionError(f"Kokoro asset is empty: {name}")
        resolved.append(str(candidate.resolve()))
    return resolved[0], resolved[1]


def resolve_media_tool(
    name: str,
    *,
    path_lookup: Callable[[str], str | None] = shutil.which,
) -> str | None:
    """Prefer the hash-locked project install, then use an executable on PATH."""

    if name not in SUPPORTED_TOOLS:
        raise ValueError(f"unsupported media tool: {name}")
    local_bin = _validated_local_bin()
    if local_bin is not None:
        executable = local_bin / f"{name}.exe"
        if not executable.is_file():
            raise ToolResolutionError(f"locked executable is missing: {executable.name}")
        return str(executable)
    return path_lookup(name)
