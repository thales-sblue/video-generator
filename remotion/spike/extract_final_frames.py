"""Pull one frame per motion-graphics event from a finished render, for review."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(r"C:\GIT\video-generator")
FFMPEG = ROOT / ".local-tools/ffmpeg/ffmpeg-n8.1-latest-win64-lgpl-shared-8.1/bin/ffmpeg.exe"


def main() -> int:
    render = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "output/motion-graphics-v1/final.mp4"
    scene_path = ROOT / "output/motion-graphics-v1/edit-plan.json"
    plan = json.loads(scene_path.read_text(encoding="utf-8"))
    mg = next(op for op in plan["operations"] if op["kind"] == "motion_graphics")
    out = render.parent / "frames"
    out.mkdir(parents=True, exist_ok=True)
    for i, event in enumerate(mg["parameters"]["events"], start=1):
        mid = event["start"] + event["duration"] / 2
        name = f"{i:02d}-{event['layout']}-{event['blocks'][-1]['text'][:16]}.png"
        subprocess.run(
            [str(FFMPEG), "-y", "-v", "error", "-ss", f"{mid:.3f}", "-i", str(render),
             "-frames:v", "1", str(out / name)],
            check=True,
        )
        print(f"  {mid:7.2f}s  {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
