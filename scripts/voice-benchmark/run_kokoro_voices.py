"""Render the benchmark text with several Kokoro voice / speed options.

Same text, same prosodic pipeline as run_kokoro.py -- only the voice token and
base_speed change, so the WAVs are directly comparable. Kokoro v1.0 ships three
Brazilian-Portuguese voices: pf_dora, pm_alex (the project's current choice),
pm_santa.

Output:
  output/voice-benchmark/kokoro-<voice>-<speed>.wav
  output/voice-benchmark/kokoro-voices.json
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from bench_common import (
    BENCHMARK_TEXT,
    OUTPUT_DIR,
    peak_working_set_bytes,
    text_sha256,
    wav_info,
)

from video_generator.narration import render_prosodic_narration

LANG = "pt-br"
LEAD_IN_SECONDS = 0.6

# (voice, base_speed) -- kept small on purpose.
COMBOS = [
    ("pm_alex", 0.95),   # current project config (same as kokoro.wav)
    ("pm_alex", 0.90),   # same voice, slower -> darker pacing
    ("pm_alex", 1.00),   # same voice, natural speed
    ("pf_dora", 0.95),   # the pt-br female voice
    ("pf_dora", 0.90),
    ("pm_santa", 0.95),  # the other pt-br male voice (fuller / older timbre)
]


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    results = []
    for voice, speed in COMBOS:
        out = OUTPUT_DIR / f"kokoro-{voice}-{speed:.2f}.wav"
        if out.exists():
            out.unlink()
        started = time.perf_counter()
        narration = render_prosodic_narration(
            BENCHMARK_TEXT,
            out,
            voice=voice,
            lang=LANG,
            base_speed=speed,
            lead_in_seconds=LEAD_IN_SECONDS,
        )
        gen = time.perf_counter() - started
        info = wav_info(out)
        peak = peak_working_set_bytes()
        entry = {
            "provider": "kokoro",
            "voice": voice,
            "base_speed": speed,
            "lang": LANG,
            "lead_in_seconds": LEAD_IN_SECONDS,
            "prosody_units": len(narration.units),
            "spoken_seconds": narration.spoken_seconds,
            "planned_pause_seconds": narration.pause_seconds,
            "audio_seconds": info["duration_seconds"],
            "generation_seconds": round(gen, 3),
            "realtime_factor": round(gen / info["duration_seconds"], 3)
            if info["duration_seconds"]
            else None,
            "peak_working_set_mb": round(peak / 1024 / 1024, 1) if peak else None,
            "wav": info,
            "wav_path": str(Path("output/voice-benchmark") / out.name),
        }
        results.append(entry)
        print(
            f"{voice:9s} @ {speed:.2f}  ->  {info['duration_seconds']:6.2f}s audio  "
            f"gen {gen:6.2f}s  RTF {entry['realtime_factor']}"
        )

    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "benchmark_text": BENCHMARK_TEXT,
        "text_sha256": text_sha256(),
        "pipeline": "video_generator.narration.render_prosodic_narration (unit-by-unit)",
        "device": "cpu",
        "entries": results,
    }
    path = OUTPUT_DIR / "kokoro-voices.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nWrote {path}")


if __name__ == "__main__":
    main()
