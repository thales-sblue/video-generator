r"""Orchestrate the Kokoro-vs-Chatterbox voice benchmark and write benchmark.json.

Runs in the PROJECT virtualenv (stdlib only). It shells out to:
  * run_kokoro.py      -- project venv, PYTHONPATH=src
  * run_chatterbox.py  -- isolated .local-tools/chatterbox-venv (if present)

Outputs (all under output/voice-benchmark/, which is git-ignored):
  kokoro.wav
  chatterbox-default.wav
  chatterbox-documentary.wav
  chatterbox-expressive.wav
  chatterbox.wav              (copy of the documentary config -- best brief fit)
  benchmark.json

Repro:  .\.venv\Scripts\python.exe scripts\voice-benchmark\run_all.py
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
OUTPUT_DIR = REPO_ROOT / "output" / "voice-benchmark"
PROJECT_PY = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
CHATTERBOX_PY = REPO_ROOT / ".local-tools" / "chatterbox-venv" / "Scripts" / "python.exe"

FRAGMENT_RE = re.compile(r"^BENCHMARK_FRAGMENT (\{.*\})\s*$", re.MULTILINE)

CHATTERBOX_CONFIGS = [
    # label,        out filename,                 exaggeration cfg_weight temperature rep_pen
    ("default", "chatterbox-default.wav", 0.5, 0.5, 0.8, 1.2),
    ("documentary", "chatterbox-documentary.wav", 0.3, 0.5, 0.6, 1.3),
    ("expressive", "chatterbox-expressive.wav", 0.6, 0.35, 0.8, 1.2),
]


def run(cmd: list[str], *, env: dict | None = None, cwd: Path = HERE) -> tuple[int, str]:
    print(f"\n$ {' '.join(str(c) for c in cmd)}")
    proc = subprocess.run(
        [str(c) for c in cmd],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
    )
    if proc.stdout:
        print(proc.stdout)
    if proc.stderr:
        print(proc.stderr, file=sys.stderr)
    return proc.returncode, proc.stdout + "\n" + proc.stderr


def parse_fragments(blob: str) -> list[dict]:
    return [json.loads(m.group(1)) for m in FRAGMENT_RE.finditer(blob)]


def nvidia_smi() -> dict:
    try:
        out = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,driver_version",
                "--format=csv,noheader",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        ).stdout.strip()
        name, mem, driver = (p.strip() for p in out.split(",")[:3])
        return {"name": name, "memory_total": mem, "driver_version": driver}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


def python_version(py: Path) -> str:
    try:
        return subprocess.run(
            [str(py), "-c", "import sys;print(sys.version.split()[0])"],
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout.strip()
    except Exception as exc:  # noqa: BLE001
        return f"unknown ({exc})"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-kokoro", action="store_true")
    ap.add_argument("--skip-chatterbox", action="store_true")
    ap.add_argument(
        "--device", default="auto", choices=["auto", "cuda", "cpu", "mps"],
        help="Chatterbox device (default: auto -> cuda if available)",
    )
    ap.add_argument("--audio-prompt", default=None, help="optional LOCAL reference wav for Chatterbox")
    args = ap.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    entries: list[dict] = []
    notes: list[str] = []

    # Carry over entries for any provider skipped this run, so a targeted re-run
    # (e.g. --skip-kokoro) does not drop the other half of the comparison.
    prev_report_path = OUTPUT_DIR / "benchmark.json"
    prev_entries: list[dict] = []
    if prev_report_path.exists():
        try:
            prev_entries = json.loads(prev_report_path.read_text(encoding="utf-8")).get(
                "entries", []
            )
        except (OSError, ValueError):
            prev_entries = []

    def carry_over(provider: str) -> None:
        kept = [e for e in prev_entries if e.get("provider") == provider]
        if kept:
            entries.extend(kept)
            notes.append(f"{provider} entries carried over from the previous benchmark.json.")

    # --- Kokoro (project current config) ------------------------------------
    if not args.skip_kokoro:
        env = os.environ.copy()
        env["PYTHONPATH"] = os.pathsep.join(
            [str(REPO_ROOT / "src"), str(HERE), env.get("PYTHONPATH", "")]
        )
        code, blob = run([PROJECT_PY, HERE / "run_kokoro.py"], env=env)
        frags = parse_fragments(blob)
        if code == 0 and frags:
            entries.extend(frags)
        else:
            notes.append(f"Kokoro run failed (exit {code}); see console output above.")
    else:
        notes.append("Kokoro skipped by flag.")
        carry_over("kokoro")

    # --- Chatterbox (isolated venv) ---------------------------------------------
    if args.skip_chatterbox:
        notes.append("Chatterbox skipped by flag.")
        carry_over("chatterbox")
    elif not CHATTERBOX_PY.exists():
        notes.append(
            f"Chatterbox venv not found at {CHATTERBOX_PY.relative_to(REPO_ROOT)} -- "
            "run scripts/voice-benchmark/setup_chatterbox_venv.ps1 first."
        )
    else:
        env = os.environ.copy()
        env["PYTHONPATH"] = os.pathsep.join([str(HERE), env.get("PYTHONPATH", "")])
        for label, out_name, exagg, cfg, temp, rep in CHATTERBOX_CONFIGS:
            cmd = [
                CHATTERBOX_PY, HERE / "run_chatterbox.py",
                "--label", label, "--out", out_name,
                "--exaggeration", str(exagg), "--cfg-weight", str(cfg),
                "--temperature", str(temp), "--repetition-penalty", str(rep),
                "--t3-model", "v3", "--language-id", "pt",
                "--device", args.device,
            ]
            if args.audio_prompt:
                cmd += ["--audio-prompt", args.audio_prompt]
            code, blob = run(cmd, env=env)
            frags = parse_fragments(blob)
            if code == 0 and frags:
                entries.extend(frags)
            else:
                notes.append(f"Chatterbox '{label}' failed (exit {code}); see console output.")

        doc = OUTPUT_DIR / "chatterbox-documentary.wav"
        if doc.exists():
            shutil.copyfile(doc, OUTPUT_DIR / "chatterbox.wav")
            notes.append("chatterbox.wav is a copy of chatterbox-documentary.wav.")

    # --- Assemble benchmark.json ---------------------------------------------
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "benchmark_text": _text(),
        "text_sha256": _sha(),
        "host": {
            "os": platform.platform(),
            "processor": platform.processor(),
            "project_python": python_version(PROJECT_PY),
            "chatterbox_python": python_version(CHATTERBOX_PY)
            if CHATTERBOX_PY.exists()
            else None,
            "gpu": nvidia_smi(),
        },
        "entries": entries,
        "notes": notes,
    }
    report_path = OUTPUT_DIR / "benchmark.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nWrote {report_path}")
    print(f"WAVs in {OUTPUT_DIR}")


def _text() -> str:
    from bench_common import BENCHMARK_TEXT

    return BENCHMARK_TEXT


def _sha() -> str:
    from bench_common import text_sha256

    return text_sha256()


if __name__ == "__main__":
    main()
