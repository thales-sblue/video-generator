"""Synthesise a placeholder Kokoro narration for canal_dev_01 and re-cut the
video against its real timing — a preview to hear how the pacing feels, not
the publishable cut.

This is *not* the author's voice. It is the local Kokoro TTS voice this
project already benchmarked and settled on (``pm_alex``, ``pt-br``, prosodic
unit-by-unit synthesis — see ``scripts/voice-benchmark/``), standing in for a
real recording so the author can judge pacing and shot length with actual
audio instead of imagining it. Nothing here is published: `editorial_review`
stays `not_performed`, and the real cycle (docs/WORKFLOWS.md, "O ciclo") is
still: the author records the real voice, timings get remeasured against
*that* WAV, and this preview is thrown away.

The BLOCKS table in ``build-canal-dev-01.py`` is not duplicated here — it is
imported, so a visual decision made once (the visual-lock in that file) never
needs to be re-authored to get a dub preview. What changes is timing only:

1. ``render_prosodic_narration`` speaks the whole script — every block's
   narration text, joined with a blank line so each block is its own
   paragraph — unit by unit (sentence or short-sentence run), and reports
   exactly where every unit landed in the joined WAV. It is not estimated:
   the caller gets the same numbers the listener will hear.
2. Each block's real span is read straight off its own units' real
   timestamps (first unit's start to the next block's first unit's start;
   the last block ends at the WAV's own measured length), and
   ``build_timeline(block_targets=...)`` re-scales that block's shots to it,
   exactly the way the silent build scales them to a word-count guess.
3. Captions are rebuilt from the same real units instead of a proportional
   guess: one cue per unit (wrapped into readable lines when a single
   sentence would not fit), timed to when that unit is actually spoken, blank
   during the pause between units — a real subtitle rhythm, not a fabricated
   even split.
4. The pre-synthesised WAV becomes the plan's ``narration`` operation as a
   *source* (``duration_policy=match_timeline``): the engine never
   re-synthesises it, it only composes what has already been measured.

Usage (from the repository root, with PYTHONPATH=src; needs the local Kokoro
extra installed — see README "Narração"):

    python scripts/build-canal-dev-01-dub-preview.py
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from video_generator.domain.prosody import plan_narration_units  # noqa: E402
from video_generator.narration import (  # noqa: E402
    NarrationError,
    ProsodicNarration,
    render_prosodic_narration,
)

_SPEC = importlib.util.spec_from_file_location(
    "build_canal_dev_01", REPOSITORY_ROOT / "scripts" / "build-canal-dev-01.py"
)
bc = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bc)  # type: ignore[union-attr]

OUT_DIR = REPOSITORY_ROOT / "output" / "canal-dev-01-dub-preview"
NARRATION_PATH = OUT_DIR / "narration.wav"
PLAN_PATH = REPOSITORY_ROOT / "projects" / "canal_dev_01" / "edit-plan-dub-preview.json"
OUTPUT_PATH = OUT_DIR / "final.mp4"

# The project's own benchmarked baseline (scripts/voice-benchmark/README.md,
# "Kokoro config used"): pm_alex / pt-br / unit-by-unit prosody / 0.95 base
# speed / 0.6 s of silence before the first word.
VOICE = "pm_alex"
LANG = "pt-br"
BASE_SPEED = 0.95
LEAD_IN_SECONDS = 0.6

CAPTION_MAX_CHARS = bc.CAPTION_MAX_CHARS


def synthesize() -> ProsodicNarration:
    full_text = "\n\n".join(block["narration"] for block in bc.BLOCKS)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if NARRATION_PATH.exists():
        NARRATION_PATH.unlink()
    try:
        return render_prosodic_narration(
            full_text,
            NARRATION_PATH,
            voice=VOICE,
            lang=LANG,
            base_speed=BASE_SPEED,
            lead_in_seconds=LEAD_IN_SECONDS,
        )
    except NarrationError as exc:
        raise SystemExit(
            f"could not synthesise the dub preview narration: {exc}\n"
            "Kokoro is an opt-in local extra — see README 'Narração' "
            "(pip install -e .[tts] + model files in .local-tools/kokoro/)."
        ) from exc


def block_spans(narration: ProsodicNarration) -> dict[str, tuple[float, float]]:
    """Map each block id to its real (start, end) in the synthesised WAV.

    Verified once, structurally, in this project's own dev session: calling
    ``plan_narration_units`` on each block's text alone yields exactly the
    same unit count and text as that block's slice of the joined call (the
    grouping/beat/speed decisions are local to a paragraph; only the *pause*
    on a paragraph's last unit can depend on its neighbour, which does not
    affect where a unit starts).
    """

    spans: dict[str, tuple[float, float]] = {}
    offset = 0
    starts = [unit.start_seconds for unit in narration.units]
    for block in bc.BLOCKS:
        count = len(plan_narration_units(block["narration"]))
        if count == 0:
            raise SystemExit(f"block {block['id']} produced no narration units")
        next_offset = offset + count
        block_end = starts[next_offset] if next_offset < len(starts) else narration.total_seconds
        spans[block["id"]] = (starts[offset], block_end)
        offset = next_offset
    if offset != len(narration.units):
        raise SystemExit("narration units did not partition cleanly across blocks")
    # Block 0 owns the lead-in silence too, so the spans sum to the whole WAV.
    first_id = bc.BLOCKS[0]["id"]
    spans[first_id] = (0.0, spans[first_id][1])
    return spans


def build_real_captions(narration: ProsodicNarration) -> list[dict]:
    """One cue per spoken unit, wrapped to readable width, silent on the pauses.

    Real timing, not a proportional guess: each unit's window is exactly when
    Kokoro is speaking it, measured with ffprobe by ``render_prosodic_narration``.
    """

    items: list[dict] = []
    for unit in narration.units:
        lines = bc.chunk_narration(unit.text, CAPTION_MAX_CHARS)
        if not lines:
            continue
        weights = [len(line) for line in lines]
        total_weight = sum(weights)
        cursor = unit.start_seconds
        span_end = unit.start_seconds + unit.spoken_seconds
        for index, (line, weight) in enumerate(zip(lines, weights)):
            end = span_end if index == len(lines) - 1 else cursor + unit.spoken_seconds * (
                weight / total_weight
            )
            if end <= cursor:
                end = cursor + 0.05
            items.append(
                {"text": line, "start_seconds": round(cursor, 3), "end_seconds": round(end, 3)}
            )
            cursor = end
    return items


def main() -> int:
    print(f"synthesising narration ({VOICE}/{LANG}, base speed {BASE_SPEED})...")
    narration = synthesize()
    print(
        f"narration.wav  {narration.total_seconds:.2f}s total, "
        f"{narration.spoken_seconds:.2f}s spoken, {len(narration.units)} units"
    )

    bc.prepare_blocks()
    spans = block_spans(narration)
    block_targets = {block_id: end - start for block_id, (start, end) in spans.items()}
    timeline, total = bc.build_timeline(block_targets)

    # Every shot duration was snapped to the nearest 1/30s frame, so the sum
    # of shots can drift a little from the WAV's own measured length; the
    # render's match_timeline check compares against exactly that WAV length,
    # so this is the tolerance the render command needs to be told to accept.
    drift = abs(total - narration.total_seconds)
    if drift > 1.0:
        raise SystemExit(
            f"video timeline ({total:.2f}s) drifted too far from the narration "
            f"({narration.total_seconds:.2f}s) — investigate before rendering"
        )

    plan = bc.build_edit_plan(
        timeline,
        total,
        narration_source=NARRATION_PATH,
        output_path=OUTPUT_PATH,
        plan_id="canal-dev-01-dub-preview-edit-plan",
    )
    for operation in plan["operations"]:
        if operation["kind"] == "captions":
            operation["parameters"]["items"] = build_real_captions(narration)

    PLAN_PATH.parent.mkdir(parents=True, exist_ok=True)
    PLAN_PATH.write_text(
        json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    caption_cues = next(
        op for op in plan["operations"] if op["kind"] == "captions"
    )["parameters"]["items"]

    print(f"blocks       {len(timeline)}")
    print(f"video total  {total:.2f}s (narration {narration.total_seconds:.2f}s, drift {drift:.3f}s)")
    print(f"captions     {len(caption_cues)} cues, real-timed to the spoken units")
    print(f"narration    {NARRATION_PATH}")
    print(f"plan         {PLAN_PATH}")
    print(
        "next: python -m video_generator preflight "
        f'"{PLAN_PATH}" && python -m video_generator execute-final-sequence-plan '
        f'"{PLAN_PATH}" --manifest "{OUT_DIR / "final.mp4.manifest.json"}" '
        f"--duration-tolerance-seconds {max(0.15, drift + 0.1):.2f} --timeout-seconds 600"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
