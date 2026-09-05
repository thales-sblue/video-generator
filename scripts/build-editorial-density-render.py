"""Assemble the Editorial Density render plan for ``desumanizando_01``.

Same voice, same 59 assets, same Visual Direction as the motion-graphics cut.
What changes is the typographic layer: this script re-runs
:func:`video_generator.domain.planning.plan_shot_motion_typography` with the
denser editorial planner — intensity levels, editorial-moment intents, built
``statement_build`` chains, a coverage pass — against the already-planned
scenes/shots and the force-aligned ``captions-v2.srt``, then carries the result
as a Remotion ``motion_graphics`` scene the way the previous cut did.

Read-only with respect to every input; everything it writes lands under
``output/editorial-density-v1/``.
"""

from __future__ import annotations

import json
import shutil
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from video_generator.domain import (  # noqa: E402
    EditOperation,
    EditPlan,
    TargetFormat,
    VideoBrief,
    VideoRequest,
)
from video_generator.domain.motion_graphics import (  # noqa: E402
    build_motion_graphics_scene,
)
from video_generator.domain.planning import (  # noqa: E402
    ScenePlan,
    ShotPlan,
    plan_shot_motion_typography,
    shot_timeline,
)
from video_generator.subtitles import parse_subtitle_cues  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output" / "editorial-density-v1"
BASE_PLAN = ROOT / "output" / "motion-typography-v1" / "edit-plan-base.json"
PLAN_DIR = ROOT / "output" / "motion-typography-v1" / "plan"
BASELINE_PLAN = ROOT / "output" / "editorial-translation-v1" / "edit-plan.json"
PREVIOUS_TYPO = ROOT / "output" / "motion-typography-v1" / "typography.json"
CAPTIONS = ROOT / "projects" / "desumanizando_01" / "captions-v2.srt"
MUSIC = "assets/desumanizando/video_01/audio/musica_ambiental.wav"
NARRATION = "projects/desumanizando_01/audio/narration-v2.wav"
OUTPUT_VIDEO = OUT / "final.mp4"

TARGET_DURATION = 209.261
REQUEST_ID = "desumanizando-01-editorial-density-request"
BRIEF_ID = "desumanizando-01-editorial-density-brief"
PLAN_ID = "desumanizando-01-editorial-density-edit-plan"


def _coverage_fraction(events, total: float) -> float:
    covered = sorted((e.start_seconds, e.end_seconds) for e in events)
    filled = 0.0
    cursor = 0.0
    for start, end in covered:
        start = max(start, cursor)
        if end > start:
            filled += end - start
            cursor = end
    return filled / total if total else 0.0


def _longest_gap(events, total: float) -> float:
    covered = sorted((e.start_seconds, e.end_seconds) for e in events)
    cursor = 0.0
    longest = 0.0
    for start, end in covered:
        longest = max(longest, start - cursor)
        cursor = max(cursor, end)
    return max(longest, total - cursor)


def _metrics(events, total: float) -> dict:
    layouts = [e.layout for e in events]
    consecutive_repeats = sum(1 for a, b in zip(layouts, layouts[1:]) if a == b)
    chains = Counter(e.chain_id for e in events if e.chain_id is not None)
    return {
        "events": len(events),
        "events_per_minute": round(len(events) / (total / 60.0), 2),
        "coverage_fraction": round(_coverage_fraction(events, total), 4),
        "longest_dark_gap_seconds": round(_longest_gap(events, total), 2),
        "intensity": dict(Counter(e.intensity for e in events)),
        "intent": dict(Counter(e.intent for e in events)),
        "role": dict(Counter(e.role for e in events)),
        "layout": dict(Counter(layouts)),
        "motion": dict(Counter(e.motion for e in events)),
        "surface": dict(Counter(e.surface for e in events)),
        "consecutive_layout_repeats": consecutive_repeats,
        "statement_build_chains": len(chains),
        "chained_events": sum(chains.values()),
        "hook_events_0_30s": sum(1 for e in events if e.start_seconds < 30.0),
        "accent_events": sum(1 for e in events if any(b.accent for b in e.blocks)),
    }


def main() -> int:
    base = EditPlan.from_dict(json.loads(BASE_PLAN.read_text(encoding="utf-8")))
    baseline = json.loads(BASELINE_PLAN.read_text(encoding="utf-8"))
    music = next(op for op in baseline["operations"] if op["kind"] == "music")

    scene_plan = ScenePlan.from_dict(
        json.loads((PLAN_DIR / "scene-plan.json").read_text(encoding="utf-8"))
    )
    shot_plan = ShotPlan.from_dict(
        json.loads((PLAN_DIR / "shot-plan.json").read_text(encoding="utf-8"))
    )
    caption_cues = parse_subtitle_cues(
        CAPTIONS.read_text(encoding="utf-8"), source_format="srt"
    )

    events = plan_shot_motion_typography(
        scene_plan, shot_plan, caption_cues=caption_cues
    )
    if not events:
        raise SystemExit("the editorial density planner produced no events")

    timeline_end = max(v[1] for v in shot_timeline(shot_plan).values())
    scene = build_motion_graphics_scene(
        events,
        width=1920,
        height=1080,
        fps=30,
        duration_seconds=TARGET_DURATION,
    )

    segments = [op for op in base.operations if op.kind in ("image_clip", "sequence_clip")]
    direction = next(op for op in base.operations if op.kind == "visual_direction")
    if any(op.kind == "captions" for op in base.operations):
        raise SystemExit("the editorial density cut must not carry a captions operation")

    motion_graphics = EditOperation(
        operation_id="motion_graphics", kind="motion_graphics", parameters=scene
    )
    operations = (
        tuple(segments)
        + (direction, motion_graphics)
        + (
            EditOperation(
                operation_id="music_bed",
                kind="music",
                source=MUSIC,
                parameters=dict(music["parameters"]),
            ),
            EditOperation(
                operation_id="narration",
                kind="narration",
                source=NARRATION,
                parameters={"duration_policy": "match_timeline"},
            ),
        )
    )

    sources = list(base.sources) + [MUSIC, NARRATION]
    plan = EditPlan(
        plan_id=PLAN_ID,
        brief_id=BRIEF_ID,
        sources=tuple(sources),
        output_path=str(OUTPUT_VIDEO),
        operations=operations,
        target_format=TargetFormat(1920, 1080, "cover"),
    )

    request = VideoRequest(
        request_id=REQUEST_ID,
        intent=(
            "Recortar 'Desumanizando 01' com edicao editorial densa: a mesma "
            "narracao, os mesmos assets e a mesma direcao visual, com uma "
            "camada de motion typography muito mais presente — niveis de "
            "intensidade, momentos editoriais, frases construidas em etapas e "
            "uma passada de cobertura que nao deixa a tela muda por muito tempo."
        ),
        sources=tuple(sources),
        platform="youtube",
        workflow="dark-video",
    )
    brief = VideoBrief(
        brief_id=BRIEF_ID,
        request_id=REQUEST_ID,
        objective=(
            "Um ensaio documental escuro de 209 s em 1920x1080 onde a voz "
            "carrega a informacao e o texto na tela funciona como edicao: "
            "denso no hook, forte em cada virada do argumento, com contraste "
            "de escala real e quase nenhum trecho morto."
        ),
        platform="youtube",
        workflow="dark-video",
        audience="adulto, interessado em psicologia e midia",
        target_duration_seconds=TARGET_DURATION,
        target_format=TargetFormat(1920, 1080, "cover"),
        editorial_notes=(
            "Sem legenda tradicional queimada; o .srt permanece como sidecar.",
            "Densidade variavel por beat: LOW/MEDIUM/HIGH/PEAK.",
            "Frases fortes construidas em 2-4 fragmentos que se tocam.",
            "Hierarquia obrigatoria; nenhuma composicao repetida em sequencia.",
            "Assets, relevancia, traducao editorial e direcao visual herdados.",
        ),
    )

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "edit-plan.json").write_text(plan.to_json(), encoding="utf-8")
    (OUT / "video-request.json").write_text(request.to_json(), encoding="utf-8")
    (OUT / "video-brief.json").write_text(brief.to_json(), encoding="utf-8")
    (OUT / "typography.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "script_id": "desumanizando_01",
                "events": [e.to_dict() for e in events],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    shutil.copyfile(CAPTIONS, OUT / "captions.srt")

    metrics = {"editorial_density_v1": _metrics(events, timeline_end)}
    if PREVIOUS_TYPO.exists():
        prev = json.loads(PREVIOUS_TYPO.read_text(encoding="utf-8"))["events"]

        class _E:  # a thin shim so _metrics can read the old dicts
            def __init__(self, d):
                self.start_seconds = d["start_seconds"]
                self.end_seconds = d["end_seconds"]
                self.layout = d["layout"]
                self.motion = d["motion"]
                self.role = d["role"]
                self.intensity = d.get("intensity", "n/a")
                self.intent = d.get("intent", "n/a")
                self.surface = d.get("surface", "n/a")
                self.chain_id = d.get("chain_id")
                self.blocks = [type("B", (), {"accent": b.get("accent", False)}) for b in d["blocks"]]

        metrics["motion_typography_v1_baseline"] = _metrics(
            [_E(d) for d in prev], timeline_end
        )
    (OUT / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    m = metrics["editorial_density_v1"]
    print(
        f"edit plan: {len(plan.operations)} operations, {len(plan.sources)} sources\n"
        f"events: {m['events']}  ({m['events_per_minute']}/min)  "
        f"coverage {m['coverage_fraction'] * 100:.1f}%  "
        f"longest dark {m['longest_dark_gap_seconds']}s\n"
        f"intensity: {m['intensity']}\n"
        f"intent: {m['intent']}\n"
        f"layout: {m['layout']}\n"
        f"chains: {m['statement_build_chains']} ({m['chained_events']} events)  "
        f"consecutive-repeats: {m['consecutive_layout_repeats']}  "
        f"hook 0-30s: {m['hook_events_0_30s']}\n"
        f"-> {OUT / 'edit-plan.json'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
