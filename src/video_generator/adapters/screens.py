"""Rasterise a laid-out screen card to a still frame through local FFmpeg.

The domain decided where every glyph goes; this adapter only draws it. The
frame is a solid ground plus a ``drawbox`` per rectangle and a ``drawtext`` per
text block, in that order, so the result is a pure function of the layout.

No card text ever reaches a filter-graph string. Each text block is written to
its own UTF-8 file inside a private temporary directory, FFmpeg runs with that
directory as its working directory, and the graph references the file by a
generated ASCII name. That keeps quotes, colons, backslashes, accents and drive
letters out of the graph entirely -- the one place where FFmpeg's own escaping
rules would otherwise have to be trusted with content taken from the repository.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from video_generator.domain.screens import ScreenLayout
from video_generator.tooling import ToolResolutionError, resolve_media_tool


class ScreenRenderError(RuntimeError):
    """Raised when a local render cannot produce the requested still."""


MAX_TEXT_BLOCKS = 64
MAX_BOXES = 64


@dataclass(frozen=True, slots=True)
class ScreenArtifact:
    """One rendered card and the identity it was rendered from."""

    card_id: str
    kind: str
    output_path: str
    width: int
    height: int
    file_size_bytes: int

    def to_dict(self) -> dict[str, object]:
        return {
            "card_id": self.card_id,
            "kind": self.kind,
            "output_path": self.output_path,
            "width": self.width,
            "height": self.height,
            "file_size_bytes": self.file_size_bytes,
        }


def _colour(value: str) -> str:
    # The domain already constrains these to 0xRRGGBB; re-check at the boundary
    # because everything below this line ends up inside a filter graph.
    if (
        not isinstance(value, str)
        or len(value) != 8
        or not value.startswith("0x")
        or any(character not in "0123456789abcdefABCDEF" for character in value[2:])
    ):
        raise ScreenRenderError(f"colour must be a 0xRRGGBB literal, got {value!r}")
    return value


def _font(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ScreenRenderError("font must be a non-empty family name")
    for character in value:
        if not (character.isascii() and (character.isalnum() or character in " ._-")):
            raise ScreenRenderError(f"font family must be plain ASCII, got {value!r}")
    return value


def build_filter_chain(layout: ScreenLayout, text_files: Sequence[str]) -> str:
    """The ``-vf`` chain for ``layout``, referencing already-written text files."""

    if len(text_files) != len(layout.texts):
        raise ScreenRenderError("one text file is required per text block")
    steps: list[str] = []
    for box in layout.boxes:
        steps.append(
            "drawbox="
            f"x={int(box.x)}:y={int(box.y)}:"
            f"w={int(box.width)}:h={int(box.height)}:"
            f"color={_colour(box.colour)}:t=fill"
        )
    for block, name in zip(layout.texts, text_files):
        if not name.isascii() or not name.replace(".", "").replace("_", "").isalnum():
            raise ScreenRenderError(f"text file name must be plain ASCII: {name!r}")
        steps.append(
            "drawtext="
            f"font={_font(block.font)}:"
            f"textfile={name}:"
            f"fontcolor={_colour(block.colour)}:"
            f"fontsize={int(block.size)}:"
            f"x={int(block.x)}:y={int(block.y)}:"
            f"line_spacing={int(block.line_spacing)}"
        )
    if not steps:
        return "null"
    return ",".join(steps)


def build_command(
    layout: ScreenLayout, filter_chain: str, output_name: str
) -> list[str]:
    """The full argv, with the source colour and the single-frame PNG sink."""

    return [
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c={_colour(layout.background)}:s={int(layout.width)}x{int(layout.height)}",
        "-vf",
        filter_chain,
        "-frames:v",
        "1",
        "-update",
        "1",
        "-pix_fmt",
        "rgb24",
        output_name,
    ]


def render_screen_card(
    layout: ScreenLayout,
    output_path: str | Path,
    *,
    timeout_seconds: float = 60,
    ffmpeg_path: str | Path | None = None,
    runner=subprocess.run,
) -> ScreenArtifact:
    """Draw ``layout`` into a PNG at ``output_path`` without overwriting it."""

    if not isinstance(layout, ScreenLayout):
        raise ScreenRenderError("layout must be a ScreenLayout")
    if len(layout.texts) > MAX_TEXT_BLOCKS or len(layout.boxes) > MAX_BOXES:
        raise ScreenRenderError("screen card exceeds the drawable block budget")
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or timeout_seconds <= 0
    ):
        raise ScreenRenderError("timeout_seconds must be a positive number")

    output = Path(output_path).expanduser().resolve()
    if output.suffix.lower() != ".png":
        raise ScreenRenderError("a screen card renders to a .png output_path")
    if output.exists():
        raise ScreenRenderError(f"screen card output already exists: {output}")
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ScreenRenderError(f"could not create output directory: {output.parent}") from exc

    if ffmpeg_path is None:
        try:
            executable = str(resolve_media_tool("ffmpeg"))
        except ToolResolutionError as exc:
            raise ScreenRenderError(str(exc)) from exc
    else:
        executable = str(ffmpeg_path)

    with tempfile.TemporaryDirectory(prefix=f".screen-{layout.card_id}-") as workspace:
        work = Path(workspace)
        names: list[str] = []
        for index, block in enumerate(layout.texts):
            name = f"t{index:03d}.txt"
            (work / name).write_text("\n".join(block.lines), encoding="utf-8")
            names.append(name)
        chain = build_filter_chain(layout, names)
        rendered = "card.png"
        command = [executable, *build_command(layout, chain, rendered)]
        try:
            completed = runner(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=float(timeout_seconds),
                shell=False,
                cwd=str(work),
            )
        except subprocess.TimeoutExpired as exc:
            raise ScreenRenderError(f"ffmpeg timed out rendering card {layout.card_id}") from exc
        except OSError as exc:
            raise ScreenRenderError(
                f"ffmpeg could not render card {layout.card_id}: {type(exc).__name__}"
            ) from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            suffix = f": {detail}" if detail else ""
            raise ScreenRenderError(
                f"ffmpeg exited with {completed.returncode} rendering "
                f"card {layout.card_id}{suffix}"
            )
        produced = work / rendered
        if not produced.is_file() or produced.stat().st_size == 0:
            raise ScreenRenderError(
                f"ffmpeg reported success without a frame for card {layout.card_id}"
            )
        try:
            os.link(produced, output)
        except FileExistsError as exc:
            raise ScreenRenderError(f"screen card output already exists: {output}") from exc
        except OSError:
            # A staging directory on another volume cannot be hard-linked from.
            output.write_bytes(produced.read_bytes())

    return ScreenArtifact(
        card_id=layout.card_id,
        kind=layout.kind,
        output_path=str(output),
        width=layout.width,
        height=layout.height,
        file_size_bytes=output.stat().st_size,
    )


__all__ = [
    "MAX_BOXES",
    "MAX_TEXT_BLOCKS",
    "ScreenArtifact",
    "ScreenRenderError",
    "build_command",
    "build_filter_chain",
    "render_screen_card",
]
