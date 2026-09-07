"""Render the benchmark text with Chatterbox Multilingual V3 (Resemble AI).

Isolated experiment. Runs ONLY inside ``.local-tools/chatterbox-venv`` and never
touches the ``video_generator`` package. Official project only:
https://github.com/resemble-ai/chatterbox  (package: ``chatterbox-tts``).

API confirmed from the official ``src/chatterbox/mtl_tts.py`` @ master:
    from chatterbox.mtl_tts import ChatterboxMultilingualTTS
    m = ChatterboxMultilingualTTS.from_pretrained(device="cuda", t3_model="v3")
    wav = m.generate(text, language_id="pt", exaggeration=.., cfg_weight=.., temperature=..)
    m.sr  # sample rate

Notes:
* V3 multilingual exposes Portuguese as ``language_id="pt"`` (there is no
  separate ``pt-br`` id in the general V3 model; a dedicated ``pt-br`` finetune
  exists as its own HF model -- see README of this folder).
* A built-in default voice ships in the HF repo (``conds.pt``), so NO third-party
  reference clip is downloaded or used. Pass ``--audio-prompt path.wav`` to use
  your own local reference later.
* Every Chatterbox output carries Resemble's inaudible "Perth" watermark.
* Text is spoken sentence by sentence (same split as the Kokoro path) and the
  pieces are concatenated with a short silence -- a fair match to the project's
  unit-by-unit narration.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import torch

from bench_common import (
    BENCHMARK_TEXT,
    OUTPUT_DIR,
    emit_fragment,
    peak_working_set_bytes,
    split_sentences,
    text_sha256,
    wav_info,
    write_wav_int16_mono,
)

from chatterbox.mtl_tts import ChatterboxMultilingualTTS

GAP_SECONDS = 0.35


def pick_device(requested: str) -> str:
    if requested != "auto":
        return requested
    if torch.cuda.is_available():
        return "cuda"
    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True, help="e.g. default / documentary / expressive")
    ap.add_argument("--out", required=True, help="output wav filename under output/voice-benchmark/")
    ap.add_argument("--exaggeration", type=float, default=0.5)
    ap.add_argument("--cfg-weight", type=float, default=0.5)
    ap.add_argument("--temperature", type=float, default=0.8)
    ap.add_argument("--repetition-penalty", type=float, default=1.2)
    ap.add_argument("--t3-model", default="v3", choices=["v2", "v3"])
    ap.add_argument("--language-id", default="pt")
    ap.add_argument("--device", default="auto", choices=["auto", "cuda", "cpu", "mps"])
    ap.add_argument("--audio-prompt", default=None, help="optional LOCAL reference wav path")
    ap.add_argument("--seed", type=int, default=1234)
    args = ap.parse_args()

    device = pick_device(args.device)
    torch.manual_seed(args.seed)
    np.random.seed(args.seed)
    if device == "cuda":
        torch.cuda.manual_seed_all(args.seed)
        torch.cuda.reset_peak_memory_stats()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUTPUT_DIR / args.out
    if out.exists():
        out.unlink()

    load_started = time.perf_counter()
    try:
        model = ChatterboxMultilingualTTS.from_pretrained(device=device, t3_model=args.t3_model)
        effective_t3 = args.t3_model
    except TypeError:
        # Older PyPI releases ship only the V2 multilingual checkpoint and take
        # no t3_model argument.
        model = ChatterboxMultilingualTTS.from_pretrained(device=device)
        effective_t3 = "v2 (release default; t3_model arg unavailable)"
    load_seconds = time.perf_counter() - load_started

    sentences = split_sentences(BENCHMARK_TEXT)
    sr = int(model.sr)
    gap = np.zeros(int(sr * GAP_SECONDS), dtype=np.float32)

    gen_kwargs = dict(
        language_id=args.language_id,
        exaggeration=args.exaggeration,
        cfg_weight=args.cfg_weight,
        temperature=args.temperature,
        repetition_penalty=args.repetition_penalty,
    )
    if args.audio_prompt:
        gen_kwargs["audio_prompt_path"] = args.audio_prompt

    pieces: list[np.ndarray] = []
    gen_started = time.perf_counter()
    for i, sentence in enumerate(sentences):
        wav = model.generate(sentence, **gen_kwargs)
        arr = wav.squeeze(0).detach().cpu().numpy().astype(np.float32)
        pieces.append(arr)
        if i != len(sentences) - 1:
            pieces.append(gap)
    generation_seconds = time.perf_counter() - gen_started

    audio = np.concatenate(pieces) if pieces else np.zeros(1, dtype=np.float32)
    raw_peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    # Peak-normalise to -1 dBFS only if it would otherwise clip. Done once on the
    # full concatenation so relative loudness between sentences is preserved.
    target_peak = 10 ** (-1.0 / 20.0)  # ~0.891
    if raw_peak > target_peak:
        audio = audio * (target_peak / raw_peak)
    write_wav_int16_mono(out, audio.tolist(), sr)

    info = wav_info(out)
    audio_seconds = info["duration_seconds"]
    peak_rss = peak_working_set_bytes()
    vram_alloc = vram_reserved = None
    if device == "cuda":
        vram_alloc = round(torch.cuda.max_memory_allocated() / 1024 / 1024, 1)
        vram_reserved = round(torch.cuda.max_memory_reserved() / 1024 / 1024, 1)

    emit_fragment(
        {
            "provider": "chatterbox",
            "config_label": args.label,
            "model": f"Chatterbox Multilingual {str(effective_t3).upper()} "
            f"(chatterbox-tts, ResembleAI/chatterbox)",
            "language": args.language_id,
            "voice": "built-in default (conds.pt)" if not args.audio_prompt else args.audio_prompt,
            "pipeline": "sentence-by-sentence generate(), concatenated with "
            f"{GAP_SECONDS}s silence",
            "device": device,
            "params": {
                "exaggeration": args.exaggeration,
                "cfg_weight": args.cfg_weight,
                "temperature": args.temperature,
                "repetition_penalty": args.repetition_penalty,
                "seed": args.seed,
                "t3_model": effective_t3,
                "sentences": len(sentences),
            },
            "audio_seconds": audio_seconds,
            "raw_peak_dbfs": round(20 * float(np.log10(raw_peak)), 2) if raw_peak else None,
            "peak_normalized_to_dbfs": -1.0 if raw_peak > target_peak else None,
            "load_seconds": round(load_seconds, 3),
            "generation_seconds": round(generation_seconds, 3),
            "realtime_factor": round(generation_seconds / audio_seconds, 3)
            if audio_seconds
            else None,
            "peak_working_set_mb": round(peak_rss / 1024 / 1024, 1) if peak_rss else None,
            "vram_peak_allocated_mb": vram_alloc,
            "vram_peak_reserved_mb": vram_reserved,
            "wav": info,
            "wav_path": str(Path("output/voice-benchmark") / args.out),
            "text_sha256": text_sha256(),
            "watermark": "Resemble Perth (implicit neural watermark, always on)",
        }
    )


if __name__ == "__main__":
    main()
