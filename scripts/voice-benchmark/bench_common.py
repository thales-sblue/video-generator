"""Shared helpers for the isolated Kokoro-vs-Chatterbox voice benchmark.

This module is deliberately stdlib-only so it can be imported from both the
project virtualenv (Kokoro path) and the isolated ``.local-tools/chatterbox-venv``
(Chatterbox path). It is NOT part of the ``video_generator`` package and nothing
in ``src/`` imports it. It exists only to make the benchmark reproducible.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import re
import struct
import sys
import wave
from pathlib import Path

# The exact text every engine must speak. Keep this identical across providers --
# a fair comparison depends on it. Provided verbatim by the benchmark request.
BENCHMARK_TEXT = (
    "Durante décadas, uma pergunta intrigou alguns dos maiores cientistas do mundo. "
    "E se tudo aquilo que acreditávamos sobre o universo estivesse errado? "
    "Em 1905, um jovem funcionário de um escritório de patentes publicou uma ideia "
    "que mudaria para sempre a nossa compreensão do espaço e do tempo. "
    "Seu nome era Albert Einstein."
)

REPO_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = REPO_ROOT / "output" / "voice-benchmark"


def text_sha256(text: str = BENCHMARK_TEXT) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def split_sentences(text: str = BENCHMARK_TEXT) -> list[str]:
    """Split into sentence units, mirroring how the project speaks unit by unit.

    Both engines get the same split so neither is helped or hurt by chunking.
    """
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def wav_duration_seconds(path: str | Path) -> float:
    with wave.open(str(path), "rb") as w:
        frames = w.getnframes()
        rate = w.getframerate()
    return frames / float(rate) if rate else 0.0


def wav_info(path: str | Path) -> dict:
    with wave.open(str(path), "rb") as w:
        return {
            "sample_rate": w.getframerate(),
            "channels": w.getnchannels(),
            "sample_width_bytes": w.getsampwidth(),
            "frames": w.getnframes(),
            "duration_seconds": round(w.getnframes() / float(w.getframerate()), 3),
        }


def write_wav_int16_mono(path: str | Path, samples, sample_rate: int) -> None:
    """Write a float32/float64 iterable in [-1, 1] as 16-bit PCM mono WAV."""
    clipped = []
    for s in samples:
        if s > 1.0:
            s = 1.0
        elif s < -1.0:
            s = -1.0
        clipped.append(int(s * 32767.0))
    payload = struct.pack("<%dh" % len(clipped), *clipped)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(sample_rate))
        w.writeframes(payload)


def peak_working_set_bytes() -> int | None:
    """Windows peak working set (RAM high-water mark) for this process, no deps."""
    if sys.platform != "win32":
        return None

    from ctypes import wintypes

    class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    try:
        kernel32 = ctypes.WinDLL("kernel32")
        psapi = ctypes.WinDLL("psapi")
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        get_info = psapi.GetProcessMemoryInfo
        get_info.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(PROCESS_MEMORY_COUNTERS),
            wintypes.DWORD,
        ]
        get_info.restype = wintypes.BOOL
    except (OSError, AttributeError):
        return None

    counters = PROCESS_MEMORY_COUNTERS()
    counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
    if not get_info(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        return None
    return int(counters.PeakWorkingSetSize)


def emit_fragment(fragment: dict) -> None:
    """Print one JSON line the orchestrator can pick out of stdout."""
    print("BENCHMARK_FRAGMENT " + json.dumps(fragment, ensure_ascii=False))
