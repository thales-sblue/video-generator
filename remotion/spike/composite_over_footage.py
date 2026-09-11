"""Composite the spike alpha overlay over real footage plates.

Pulls clean frames (moments between type events) from the ASS baseline render,
lays the Remotion overlay.mov on top with FFmpeg, and writes side-by-side
comparison frames so the two typographic treatments can be judged on the same
picture.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(r"C:\GIT\video-generator")
FFMPEG = ROOT / ".local-tools/ffmpeg/ffmpeg-n8.1-latest-win64-lgpl-shared-8.1/bin/ffmpeg.exe"
BASELINE = ROOT / "output/motion-typography-v1/final.mp4"
OVERLAY = ROOT / "output/remotion-spike/overlay.mov"
OUT = ROOT / "output/remotion-spike/composite"

# (plate time in baseline, overlay time where an event is on screen, label)
SHOTS = [
    (30.0, 2.1, "a-contrast-pair"),   # case_contrast_pair mid
    (74.0, 10.1, "b-dominant-word"),  # case_dominant_word mid
    (150.0, 6.1, "c-hidden"),         # unused pairing, kept for a 3rd plate
    (108.0, 30.1, "d-split-contrast"),
    (168.0, 18.1, "e-question"),
]


def _run(args: list[str]) -> None:
    print("  $", " ".join(str(a) for a in args[:2]), "...")
    subprocess.run(args, check=True)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for plate_t, ov_t, label in SHOTS:
        plate = OUT / f"plate-{label}.png"
        composite = OUT / f"composite-{label}.png"
        _run([str(FFMPEG), "-y", "-v", "error", "-ss", str(plate_t), "-i", str(BASELINE),
              "-frames:v", "1", str(plate)])
        _run([
            str(FFMPEG), "-y", "-v", "error",
            "-loop", "1", "-i", str(plate),
            "-ss", str(ov_t), "-i", str(OVERLAY),
            "-filter_complex", "[0:v][1:v]overlay=format=auto:shortest=1[o]",
            "-map", "[o]", "-frames:v", "1", str(composite),
        ])
        print(f"  -> {composite.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
