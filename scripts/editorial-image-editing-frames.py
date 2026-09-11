"""Pull inspection frames out of the Editorial Image Editing render.

The claim under test is "the image is now cut, not just captioned". So the
frame sets are chosen to catch a cut in the act:

* a tight grid over the hook (0-30 s), one frame every ~2 s;
* two frames straddling every internal state change the treatment plan
  scheduled — one just before, one just after — so a reframe / detail reveal /
  freeze is visible as a change, not guessed at;
* one frame per ``peak`` treatment;
* a coarse grid over the rest.

Usage::

    python scripts/editorial-image-editing-frames.py \
        output/editorial-image-editing-v1/final.mp4 \
        output/editorial-image-editing-v1/frames \
        output/editorial-image-editing-v1/treatment.json \
        output/motion-typography-v1/plan/shot-plan.json
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from video_generator.domain.planning import ShotPlan, shot_timeline  # noqa: E402
from video_generator.domain.treatment import EditorialTreatmentPlan  # noqa: E402
from video_generator.tooling import resolve_media_tool  # noqa: E402


def _grab(ffmpeg: str, video: Path, when: float, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            ffmpeg, "-nostdin", "-y", "-ss", f"{max(when, 0.0):.3f}", "-i", str(video),
            "-frames:v", "1", "-q:v", "2", str(dest),
        ],
        check=True,
        capture_output=True,
    )


def main(argv: list[str]) -> int:
    if len(argv) < 4:
        print(__doc__)
        return 2
    video = Path(argv[0]).expanduser().resolve()
    out_dir = Path(argv[1]).expanduser()
    treatment_plan = EditorialTreatmentPlan.from_dict(
        json.loads(Path(argv[2]).read_text(encoding="utf-8"))
    )
    shot_plan = ShotPlan.from_dict(
        json.loads(Path(argv[3]).read_text(encoding="utf-8"))
    )
    if not video.is_file():
        print(f"no render at {video}", file=sys.stderr)
        return 1
    ffmpeg = resolve_media_tool("ffmpeg")
    timeline = shot_timeline(shot_plan)
    by_shot = treatment_plan.by_shot()

    shots: list[tuple[float, str]] = []
    # hook grid
    t = 0.0
    while t < 30.0:
        shots.append((t, f"hook_{t:05.1f}s"))
        t += 2.0
    # coarse grid
    for t in (40.0, 55.0, 75.0, 95.0, 120.0, 150.0, 175.0, 200.0, 207.0):
        shots.append((t, f"grid_{t:05.1f}s"))
    # around every internal state change + one per peak
    for shot in shot_plan.shots:
        treatment = by_shot.get(shot.shot_id)
        if treatment is None:
            continue
        start, end = timeline[shot.shot_id]
        span = end - start
        offset = start
        for i, state in enumerate(treatment.states):
            seg = span * state.duration_fraction
            if i > 0:
                shots.append((offset - 0.4, f"{shot.shot_id}_{treatment.treatment}_pre{i}"))
                shots.append((offset + 0.4, f"{shot.shot_id}_{treatment.treatment}_post{i}"))
            offset += seg
        if treatment.intensity == "peak":
            shots.append((start + span / 2.0, f"{shot.shot_id}_PEAK_{treatment.treatment}"))

    seen: set[str] = set()
    for when, name in sorted(shots):
        key = f"{when:.2f}"
        if key in seen:
            continue
        seen.add(key)
        _grab(ffmpeg, video, when, out_dir / f"{when:07.2f}__{name}.jpg")
    print(f"wrote {len(seen)} frames to {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
