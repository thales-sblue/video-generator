"""Render the editorial-typography layer as a transparent overlay with Remotion.

This adapter is opt-in and fails closed with :class:`RemotionError` when the
portable Node runtime or the ``remotion/`` project's dependencies are missing,
so contracts, planning, inspection, ``doctor`` and the libass text path all keep
working without it.

It owns no editorial judgement: it takes the scene document produced by
:func:`video_generator.domain.motion_graphics.build_motion_graphics_scene` and
returns a ``.mov``/``.webm`` clip the FFmpeg adapter composites over the video.
Technical success here is not an editorial review — a human still owes the frame
a look before the type is treated as approved.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from types import MappingProxyType

from video_generator.domain.motion_graphics import scene_to_json


def _plain(value: Any) -> Any:
    """Recursively coerce mappingproxy/tuple trees (as an EditPlan hands them
    back) into the plain dict/list types ``json.dumps`` accepts."""

    if isinstance(value, (dict, MappingProxyType)):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value
from video_generator.tooling import (
    REMOTION_PROJECT_DIR,
    ToolResolutionError,
    resolve_node_bin,
)

COMPOSITION_ID = "MotionGraphics"
ENTRY = "src/index.ts"

# codec name -> the extra flags that make Remotion keep an alpha channel
_CODECS: Mapping[str, tuple[str, ...]] = {
    "prores": ("--codec=prores", "--prores-profile=4444", "--pixel-format=yuva444p10le"),
    "vp9": ("--codec=vp9", "--pixel-format=yuva420p"),
    "vp8": ("--codec=vp8", "--pixel-format=yuva420p"),
}
_CODEC_SUFFIX = {"prores": ".mov", "vp9": ".webm", "vp8": ".webm"}


class RemotionError(RuntimeError):
    """Raised when the Remotion overlay cannot be produced."""


@dataclass(frozen=True, slots=True)
class RemotionOverlay:
    output_path: str
    event_count: int
    width: int
    height: int
    fps: float
    duration_seconds: float
    scene_sha256: str
    file_size_bytes: int


def remotion_available(project_dir: Path | None = None) -> bool:
    """Whether a Remotion render can be attempted right now (no exceptions)."""

    project = project_dir or REMOTION_PROJECT_DIR
    try:
        node = resolve_node_bin()
    except ToolResolutionError:
        return False
    if node is None:
        return False
    cli = project / "node_modules" / "@remotion" / "cli" / "remotion-cli.js"
    return cli.is_file()


def _validate_scene(scene: Mapping[str, Any]) -> tuple[int, int, float, float, int]:
    if not isinstance(scene, Mapping):
        raise RemotionError("scene must be a mapping")
    if scene.get("schema_version") != 1:
        raise RemotionError("scene schema_version must be 1")
    composition = scene.get("composition")
    if not isinstance(composition, Mapping):
        raise RemotionError("scene is missing its composition block")
    try:
        width = int(composition["width"])
        height = int(composition["height"])
        fps = float(composition["fps"])
        duration = float(composition["durationInSeconds"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RemotionError(f"scene composition is malformed: {exc}") from exc
    events = scene.get("events")
    if isinstance(events, (str, bytes)) or not isinstance(events, (list, tuple)):
        raise RemotionError("scene events must be a list")
    if not events:
        raise RemotionError(
            "scene has no events: the caller should skip the overlay entirely "
            "rather than render an empty one"
        )
    if width <= 0 or height <= 0 or fps <= 0 or duration <= 0:
        raise RemotionError("scene composition values must all be positive")
    return width, height, fps, duration, len(events)


def render_motion_overlay(
    scene: Mapping[str, Any],
    output_path: str | Path,
    *,
    project_dir: Path | None = None,
    node_bin: str | None = None,
    codec: str = "prores",
    timeout_seconds: float = 900.0,
    runner=subprocess.run,
) -> RemotionOverlay:
    """Render ``scene`` to a transparent overlay clip and return its artifact.

    Fails closed: a missing Node runtime, missing project dependencies, a
    malformed scene, an FFmpeg-less environment inside Remotion, a non-zero exit
    or an empty output all raise :class:`RemotionError`. Never silently produces
    a blank overlay.
    """

    if codec not in _CODECS:
        raise RemotionError(f"codec must be one of {', '.join(sorted(_CODECS))}")
    scene = _plain(scene)
    width, height, fps, duration, event_count = _validate_scene(scene)

    project = (project_dir or REMOTION_PROJECT_DIR).resolve()
    cli = project / "node_modules" / "@remotion" / "cli" / "remotion-cli.js"
    if not cli.is_file():
        raise RemotionError(
            f"the Remotion project is not installed: {cli} is missing "
            "(run `npm install` in remotion/)"
        )

    if node_bin is None:
        try:
            node_bin = resolve_node_bin()
        except ToolResolutionError as exc:
            raise RemotionError(str(exc)) from exc
    if not node_bin:
        raise RemotionError(
            "Node is not available: install the portable runtime under "
            ".local-tools/node (see config/node-lock.json) or set REMOTION_NODE_BIN"
        )

    output = Path(output_path).expanduser().resolve()
    if output.suffix.lower() != _CODEC_SUFFIX[codec]:
        raise RemotionError(
            f"codec {codec} writes a {_CODEC_SUFFIX[codec]} file, not {output.suffix}"
        )
    if output.exists():
        raise RemotionError(f"overlay output already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)

    wire = scene_to_json(scene)
    scene_sha = hashlib.sha256(wire.encode("utf-8")).hexdigest()

    props_file: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix=f".{output.stem}-scene-",
            suffix=".json",
            dir=output.parent,
            delete=False,
        ) as handle:
            props_file = Path(handle.name)
            handle.write(wire)

        command = [
            node_bin,
            str(cli),
            "render",
            ENTRY,
            COMPOSITION_ID,
            str(output),
            f"--props={props_file}",
            "--log=error",
            *_CODECS[codec],
        ]
        try:
            completed = runner(
                command,
                cwd=str(project),
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RemotionError("Remotion timed out rendering the overlay") from exc
        except OSError as exc:
            raise RemotionError(
                f"could not launch Remotion: {type(exc).__name__}"
            ) from exc

        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            raise RemotionError(
                f"Remotion exited with {completed.returncode}"
                + (f": {detail[-800:]}" if detail else "")
            )
    finally:
        if props_file is not None:
            try:
                props_file.unlink()
            except OSError:
                pass

    if not output.is_file() or output.stat().st_size == 0:
        raise RemotionError("Remotion reported success without writing the overlay")

    return RemotionOverlay(
        output_path=str(output),
        event_count=event_count,
        width=width,
        height=height,
        fps=fps,
        duration_seconds=duration,
        scene_sha256=scene_sha,
        file_size_bytes=output.stat().st_size,
    )
