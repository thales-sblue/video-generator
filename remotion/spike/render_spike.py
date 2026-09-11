"""Render the Remotion motion-graphics spike: 8 stills + a short clip.

Standalone proof harness — not wired into the pipeline. Shells out to the
vendored Node + the local Remotion project, writing everything under
output/remotion-spike/.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REMOTION_DIR = HERE.parent
NODE_DIR = Path(r"C:\GIT\video-generator\.local-tools\node\node-v24.20.0-win-x64")
OUT = Path(r"C:\GIT\video-generator\output\remotion-spike")
SCENE = HERE / "spike-scene.json"

CASES = [
    ("1-hook", 63),
    ("2-contrast-pair", 183),
    ("3-dominant-word", 303),
    ("4-statement", 423),
    ("5-question", 543),
    ("6-closing", 668),
    ("7-statement-edge", 783),
    ("8-contrast-split", 903),
]


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env["PATH"] = str(NODE_DIR) + os.pathsep + env.get("PATH", "")
    return env


def _npx(*args: str) -> None:
    cmd = [str(NODE_DIR / "npx.cmd"), "remotion", *args]
    print("  $", " ".join(cmd[1:]))
    subprocess.run(cmd, cwd=REMOTION_DIR, env=_env(), check=True)


def main() -> int:
    stills = OUT / "stills"
    stills.mkdir(parents=True, exist_ok=True)
    scene = json.loads(SCENE.read_text(encoding="utf-8"))
    print(f"scene: {len(scene['events'])} events, "
          f"{scene['composition']['durationInSeconds']}s @ {scene['composition']['fps']}fps")

    for name, frame in CASES:
        _npx(
            "still", "src/index.ts", "MotionGraphics",
            str(stills / f"{name}.png"),
            f"--props={SCENE}", f"--frame={frame}", "--log=error",
        )
        print(f"  -> {name}.png (f{frame})")

    # a short H.264 clip over the eval backdrop, for motion review
    _npx(
        "render", "src/index.ts", "MotionGraphics",
        str(OUT / "spike.mp4"),
        f"--props={SCENE}", "--codec=h264", "--log=error",
    )
    print(f"  -> spike.mp4")

    # the alpha overlay the pipeline would actually composite (transparent bg)
    alpha_scene = json.loads(SCENE.read_text(encoding="utf-8"))
    alpha_scene["theme"]["background"] = None
    alpha_path = OUT / "alpha-scene.json"
    alpha_path.write_text(json.dumps(alpha_scene, ensure_ascii=False, indent=2), encoding="utf-8")
    _npx(
        "render", "src/index.ts", "MotionGraphics",
        str(OUT / "overlay.mov"),
        f"--props={alpha_path}", "--codec=prores", "--prores-profile=4444",
        "--pixel-format=yuva444p10le", "--log=error",
    )
    print("  -> overlay.mov (alpha)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
