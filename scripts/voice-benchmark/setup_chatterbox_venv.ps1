# Create the ISOLATED Chatterbox virtualenv for the voice benchmark.
#
# Nothing here touches the project .venv, the project dependencies, or PATH.
# The venv lives under .local-tools/ (already git-ignored, same convention as
# the pinned ffmpeg / kokoro / whisper installs).
#
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\voice-benchmark\setup_chatterbox_venv.ps1
#
# Size on disk: ~6-7 GB (torch CUDA wheels + model cache). Requires `git` on PATH
# (chatterbox-tts pulls resemble-perth from the official Resemble AI git repo).

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path "$PSScriptRoot\..\..").Path
$py312 = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
$venv = Join-Path $repo ".local-tools\chatterbox-venv"
$venvPy = Join-Path $venv "Scripts\python.exe"
$reqs = Join-Path $PSScriptRoot "requirements-chatterbox.txt"

if (-not (Test-Path $py312)) { throw "Python 3.12 standalone not found at $py312" }

if (-not (Test-Path $venvPy)) {
    Write-Host "Creating venv at $venv"
    & $py312 -m venv $venv
}

& $venvPy -m pip install --upgrade pip wheel

# torch + torchaudio from the CUDA 12.4 index (PyPI ships CPU-only wheels on
# Windows). cu124 runtime is forward-compatible with the RTX 3070 driver here.
& $venvPy -m pip install --index-url https://download.pytorch.org/whl/cu124 `
    "torch==2.6.0" "torchaudio==2.6.0"

# The rest of Chatterbox. torch is already satisfied so it will not be replaced.
& $venvPy -m pip install -r $reqs

Write-Host ""
Write-Host "Sanity check:"
& $venvPy -c "import torch, chatterbox; from chatterbox.mtl_tts import ChatterboxMultilingualTTS; print('torch', torch.__version__, 'cuda', torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else '')"

Write-Host ""
Write-Host "Done. Now run:  .\.venv\Scripts\python.exe scripts\voice-benchmark\run_all.py"
