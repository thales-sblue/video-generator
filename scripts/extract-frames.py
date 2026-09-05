"""Pull inspection frames out of a render.

Two sets: a fixed time grid across the whole piece, and one frame in the middle
of every planned emphasis event and of one shot per composition and per grade
intensity the direction plan used. Technical validation cannot see a bad crop
or an illegible word, so this is what makes the editorial review of a cut an
actual review rather than a claim.

Usage::

    python scripts/extract-frames.py <video.mp4> <out-dir> [visual-direction.json] [text-events.json]
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from video_generator.tooling import resolve_media_tool  # noqa: E402

GRID = (0.6, 5.0, 10.0, 20.0, 30.0, 45.0, 60.0, 90.0, 120.0, 150.0, 180.0, 205.0)


def _shot_timeline(direction_path: Path):
    """``shot_id -> (start, end, composition, grade)`` for every planned shot."""

    plan_path = direction_path.parent / "shot-plan.json"
    shot_plan = json.loads(plan_path.read_text(encoding="utf-8"))
    direction = json.loads(direction_path.read_text(encoding="utf-8"))
    by_shot = {d["shot_id"]: d for d in direction["directions"]}
    cursor = 0.0
    out = {}
    for shot in shot_plan["shots"]:
        end = cursor + shot["duration_seconds"]
        item = by_shot.get(shot["shot_id"])
        if item is not None:
            out[shot["shot_id"]] = (cursor, end, item["composition"], item["grade"])
        cursor = end
    return out


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    video = Path(argv[0]).expanduser().resolve()
    out_dir = Path(argv[1]).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    ffmpeg = resolve_media_tool("ffmpeg", path_lookup=shutil.which)
    if ffmpeg is None:
        print("ffmpeg is not available", file=sys.stderr)
        return 1

    wanted: list[tuple[str, float]] = [
        (f"grid-{int(round(t)):03d}s", t) for t in GRID
    ]

    if len(argv) >= 4:
        direction_path = Path(argv[2])
        events_path = Path(argv[3])
        timeline = _shot_timeline(direction_path)
        seen_composition: set[str] = set()
        seen_grade: set[str] = set()
        for shot_id, (start, end, composition, grade) in timeline.items():
            middle = start + (end - start) / 2
            if composition not in seen_composition:
                seen_composition.add(composition)
                wanted.append((f"composition-{composition}-{shot_id}", middle))
            if grade not in seen_grade:
                seen_grade.add(grade)
                wanted.append((f"grade-{grade}-{shot_id}", middle))
        events = json.loads(events_path.read_text(encoding="utf-8"))["events"]
        for index, event in enumerate(events, start=1):
            middle = (event["start_seconds"] + event["end_seconds"]) / 2
            wanted.append((f"emphasis-{index:02d}", middle))

    for name, timestamp in wanted:
        target = out_dir / f"{name}.png"
        command = [
            ffmpeg, "-v", "error", "-nostdin", "-y",
            "-ss", format(max(0.0, timestamp), ".3f"),
            "-i", str(video),
            "-frames:v", "1",
            "-vf", "scale=1280:-2",
            str(target),
        ]
        subprocess.run(command, check=False, capture_output=True, timeout=120)
    print(f"{len(list(out_dir.glob('*.png')))} frames -> {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
