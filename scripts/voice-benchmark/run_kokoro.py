"""Render the benchmark text with the project's CURRENT Kokoro configuration.

This imports the real ``video_generator`` code path -- nothing is reimplemented --
so the WAV is exactly what the pipeline would produce today. Run it with the
project virtualenv and ``PYTHONPATH=src`` (the orchestrator does this for you).

Current config, taken from ``output/preview-einstein/narration-units.json`` (the
most recent prosodic narration of this very Einstein material):
    voice=pm_alex, lang=pt-br, base_speed=0.95, lead_in_seconds=0.6,
    engine = render_prosodic_narration (unit-by-unit synthesis + planned pauses).
"""

from __future__ import annotations

import time
from pathlib import Path

from bench_common import (
    BENCHMARK_TEXT,
    OUTPUT_DIR,
    emit_fragment,
    peak_working_set_bytes,
    text_sha256,
    wav_info,
)

from video_generator.narration import render_prosodic_narration

VOICE = "pm_alex"
LANG = "pt-br"
BASE_SPEED = 0.95
LEAD_IN_SECONDS = 0.6


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUTPUT_DIR / "kokoro.wav"
    if out.exists():
        out.unlink()

    started = time.perf_counter()
    narration = render_prosodic_narration(
        BENCHMARK_TEXT,
        out,
        voice=VOICE,
        lang=LANG,
        base_speed=BASE_SPEED,
        lead_in_seconds=LEAD_IN_SECONDS,
    )
    generation_seconds = time.perf_counter() - started

    info = wav_info(out)
    audio_seconds = info["duration_seconds"]
    peak = peak_working_set_bytes()

    emit_fragment(
        {
            "provider": "kokoro",
            "model": "kokoro-v1.0 ONNX (kokoro-onnx)",
            "language": LANG,
            "voice": VOICE,
            "pipeline": "video_generator.narration.render_prosodic_narration (unit-by-unit)",
            "device": "cpu",  # onnxruntime: only CPUExecutionProvider is installed
            "params": {
                "base_speed": BASE_SPEED,
                "lead_in_seconds": LEAD_IN_SECONDS,
                "prosody_units": len(narration.units),
                "spoken_seconds": narration.spoken_seconds,
                "planned_pause_seconds": narration.pause_seconds,
            },
            "audio_seconds": audio_seconds,
            "generation_seconds": round(generation_seconds, 3),
            "realtime_factor": round(generation_seconds / audio_seconds, 3)
            if audio_seconds
            else None,
            "peak_working_set_mb": round(peak / 1024 / 1024, 1) if peak else None,
            "vram_peak_mb": None,
            "wav": info,
            "wav_path": str(Path("output/voice-benchmark") / "kokoro.wav"),
            "text_sha256": text_sha256(),
            "watermark": "none",
        }
    )


if __name__ == "__main__":
    main()
