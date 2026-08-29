"""Fail-closed resolution of approved local audiovisual tools."""

from __future__ import annotations

import hashlib
import json
import shutil
from functools import lru_cache
from pathlib import Path
from typing import Callable


PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOCK_PATH = PROJECT_ROOT / "config" / "ffmpeg-lock.json"
LOCAL_ROOT = PROJECT_ROOT / ".local-tools" / "ffmpeg"
SUPPORTED_TOOLS = {"ffmpeg", "ffprobe"}


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
