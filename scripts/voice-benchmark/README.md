# Voice benchmark — Kokoro (current) vs Chatterbox Multilingual V3

Isolated, throwaway experiment. **Nothing here is wired into the pipeline.**
It renders the *same* Brazilian-Portuguese passage with both engines so you can
listen and decide. No `TTSProvider` refactor, no change to Kokoro behaviour, no
new runtime dependency in the project `.venv`.

## What it produces

```
output/voice-benchmark/
  kokoro.wav                    # project's current config (render_prosodic_narration)
  chatterbox-default.wav        # Chatterbox V3, official defaults
  chatterbox-documentary.wav    # calm/dark: low exaggeration, steady
  chatterbox-expressive.wav     # more drama, faster pace (for contrast)
  chatterbox.wav                # copy of chatterbox-documentary.wav
  benchmark.json                # provider, model, language, durations, timings,
                                # realtime factor, device, VRAM/RAM peaks, params, paths
```

The text (identical for every render):

> Durante décadas, uma pergunta intrigou alguns dos maiores cientistas do mundo.
> E se tudo aquilo que acreditávamos sobre o universo estivesse errado? Em 1905,
> um jovem funcionário de um escritório de patentes publicou uma ideia que
> mudaria para sempre a nossa compreensão do espaço e do tempo. Seu nome era
> Albert Einstein.

## Kokoro config used (the fair "current" baseline)

Taken verbatim from `output/preview-einstein/narration-units.json` — the most
recent prosodic narration of this exact Einstein material:

| param | value |
|---|---|
| engine | `video_generator.narration.render_prosodic_narration` (unit-by-unit) |
| voice | `pm_alex` |
| lang | `pt-br` |
| base_speed | `0.95` |
| lead_in_seconds | `0.6` |
| device | CPU (only `CPUExecutionProvider` is installed for onnxruntime) |

## Chatterbox setup (isolated)

Official project only: <https://github.com/resemble-ai/chatterbox>, package
`chatterbox-tts` (MIT). The venv lives at `.local-tools/chatterbox-venv/`
(git-ignored, same convention as the pinned ffmpeg/kokoro/whisper installs).

```powershell
powershell -ExecutionPolicy Bypass -File scripts\voice-benchmark\setup_chatterbox_venv.ps1
```

~6–7 GB on disk (Torch CUDA wheels + HF model cache under `~/.cache/huggingface`).
Needs `git` on PATH (chatterbox-tts pulls `resemble-perth` from Resemble's git repo).

### Model / API

* `ChatterboxMultilingualTTS.from_pretrained(device="cuda", t3_model="v3")` — **V3**.
* `model.generate(text, language_id="pt", exaggeration=…, cfg_weight=…, temperature=…)`.
* Portuguese in the general V3 model is `language_id="pt"`. A dedicated Brazilian
  finetune exists as its own model — `ResembleAI/Chatterbox-Multilingual-pt-br`
  (Single Language Pack) — but it is a *separate* HF repo, not reachable through
  this class's `from_pretrained`, so it is out of scope for this first pass.
* **Voice:** the HF repo ships a built-in default speaker (`conds.pt`). This
  benchmark uses only that — **no third-party voice is downloaded or cloned.**
* **Accent note:** the built-in speaker is not Brazilian. If PT-BR pronunciation
  sounds anglicised, the official mitigation is `cfg_weight=0`, or supply your
  own local reference clip:
  ```powershell
  .\.venv\Scripts\python.exe scripts\voice-benchmark\run_all.py --audio-prompt C:\path\to\your_ptbr_10s.wav
  ```
  Do not point `--audio-prompt` at someone else's voice without their consent.
* Every Chatterbox output carries Resemble's inaudible "Perth" watermark.

### The 3 Chatterbox configs

| label | exaggeration | cfg_weight | temperature | rep. penalty | intent |
|---|---|---|---|---|---|
| default | 0.5 | 0.5 | 0.8 | 1.2 | official baseline |
| documentary | 0.3 | 0.5 | 0.6 | 1.3 | calm, low emotion, steady — dark-channel fit |
| expressive | 0.6 | 0.35 | 0.8 | 1.2 | more drama, more deliberate pace — contrast |

## Run the benchmark

```powershell
.\.venv\Scripts\python.exe scripts\voice-benchmark\run_all.py
```

Useful flags: `--skip-chatterbox`, `--skip-kokoro`, `--device cpu`,
`--audio-prompt <local.wav>`.

If the Chatterbox venv is missing, `run_all.py` still produces `kokoro.wav` and
records why Chatterbox was skipped in `benchmark.json`.
