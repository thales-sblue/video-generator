"""Pull inspection frames out of the Editorial Density render.

Three sets, because the claim being checked is "this cut looks more edited":

* a tight grid over the hook (0-30 s), one frame every ~1.6 s;
* one frame in the middle of every planned motion-typography event;
* a coarse grid over the rest of the piece.

Usage::

    python scripts/editorial-density-frames.py \
        output/editorial-density-v1/final.mp4 \
        output/editorial-density-v1/frames \
        output/editorial-density-v1/typography.json
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from video_generator.tooling import resolve_media_tool  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2
    video = Path(argv[0]).expanduser().resolve()
    out_dir = Path(argv[1]).expanduser()
    typography = json.loads(Path(argv[2]).read_text(encoding="utf-8"))
    if not video.is_file():
        print(f"no render at {video}", file=sys.stderr)
        return 1
    out_dir.mkdir(parents=True, exist_ok=True)
    ffmpeg = resolve_media_tool("ffmpeg")

    events = sorted(typography["events"], key=lambda e: e["start_seconds"])
    shots: list[tuple[str, float]] = []
    t = 0.6
    while t < 30.0:
        shots.append((f"hook_{t:05.1f}", t))
        t += 1.6
    t = 34.0
    while t < 208.0:
        shots.append((f"body_{t:05.1f}", t))
        t += 8.0
    for i, event in enumerate(events, start=1):
        mid = (event["start_seconds"] + event["end_seconds"]) / 2.0
        tag = event.get("intent", "evt")
        shots.append((f"evt_{i:03d}_{mid:06.1f}_{tag}", mid))

    for name, when in shots:
        target = out_dir / f"{name}.png"
        subprocess.run(
            [
                ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                "-ss", f"{max(0.0, when):.3f}", "-i", str(video),
                "-frames:v", "1", "-q:v", "3", str(target),
            ],
            check=True,
        )
    print(f"{len(shots)} frames -> {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
