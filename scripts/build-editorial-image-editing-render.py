"""Assemble the Editorial Image Editing v1 render plan for ``desumanizando_01``.

Same voice, same 59 assets, same Visual Direction and same denser typographic
layer as the Editorial Density cut. What changes is that every non-static shot
is now *cut*: :func:`plan_shot_editorial_treatment` decides a treatment per shot
from the beat's editorial intensity and role, and
:func:`shot_plan_to_edit_plan` expands each one into the two or three
consecutive segment operations its states describe — an internal cut from a
wide plate to a detail, a reframe, a freeze on a fact. A long static image now
earns its seconds with an edit, not only with text over it.

Read-only with respect to every input; everything it writes lands under
``output/editorial-image-editing-v1/``.
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
from video_generator.domain.direction import VisualDirection  # noqa: E402
from video_generator.domain.motion_graphics import (  # noqa: E402
    build_motion_graphics_scene,
)
from video_generator.domain.planning import (  # noqa: E402
    ScenePlan,
    ShotPlan,
    plan_shot_editorial_treatment,
    plan_shot_motion_typography,
    shot_timeline,
)
from video_generator.domain.treatment import treatment_metrics  # noqa: E402
from video_generator.subtitles import parse_subtitle_cues  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output" / "editorial-image-editing-v1"
BASE_PLAN = ROOT / "output" / "motion-typography-v1" / "edit-plan-base.json"
PLAN_DIR = ROOT / "output" / "motion-typography-v1" / "plan"
DENSITY_PLAN = ROOT / "output" / "editorial-density-v1" / "edit-plan.json"
DENSITY_TYPO = ROOT / "output" / "editorial-density-v1" / "typography.json"
CAPTIONS = ROOT / "projects" / "desumanizando_01" / "captions-v2.srt"
MUSIC = "assets/desumanizando/video_01/audio/musica_ambiental.wav"
NARRATION = "projects/desumanizando_01/audio/narration-v2.wav"
OUTPUT_VIDEO = OUT / "final.mp4"

TARGET_DURATION = 209.261
REQUEST_ID = "desumanizando-01-editorial-image-editing-request"
BRIEF_ID = "desumanizando-01-editorial-image-editing-brief"
PLAN_ID = "desumanizando-01-editorial-image-editing-edit-plan"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _direction_from_segment(op: dict) -> VisualDirection:
    """Rebuild the per-shot Visual Direction from a base segment operation.

    The base plan is the authority on what Visual Direction chose; reading it
    back is exact and needs no re-run with a matching seed.
    """

    params = op["parameters"]
    return VisualDirection(
        shot_id=op["operation_id"],
        composition=params.get("composition", "fullscreen"),
        motion=params.get("motion", "static_hold"),
        grade=params.get("grade", "standard"),
        crop_bias=params.get("crop_bias", "center"),
        text_zone=params.get("text_zone"),
    )


def _metrics_from_typography(events, total: float) -> dict:
    covered = sorted((e.start_seconds, e.end_seconds) for e in events)
    filled = 0.0
    cursor = 0.0
    for start, end in covered:
        start = max(start, cursor)
        if end > start:
            filled += end - start
            cursor = end
    return {
        "events": len(events),
        "events_per_minute": round(len(events) / (total / 60.0), 2),
        "coverage_fraction": round(filled / total, 4) if total else 0.0,
    }


def main() -> int:
    base_raw = _load(BASE_PLAN)
    base = EditPlan.from_dict(base_raw)
    density = _load(DENSITY_PLAN)
    music_op = next(op for op in density["operations"] if op["kind"] == "music")
    direction_params = next(
        op["parameters"] for op in base_raw["operations"]
        if op["kind"] == "visual_direction"
    )

    scene_plan = ScenePlan.from_dict(_load(PLAN_DIR / "scene-plan.json"))
    shot_plan = ShotPlan.from_dict(_load(PLAN_DIR / "shot-plan.json"))
    caption_cues = parse_subtitle_cues(
        CAPTIONS.read_text(encoding="utf-8"), source_format="srt"
    )

    base_segments = [
        op for op in base.operations if op.kind in ("image_clip", "sequence_clip")
    ]
    if len(base_segments) != len(shot_plan.shots):
        raise SystemExit(
            f"{len(base_segments)} base segments for {len(shot_plan.shots)} shots"
        )
    direction_op = next(op for op in base.operations if op.kind == "visual_direction")

    directions: dict[str, VisualDirection] = {}
    bindings: dict[str, str] = {}
    for shot, seg in zip(shot_plan.shots, base_segments):
        directions[shot.shot_id] = _direction_from_segment(seg.to_dict())
        bindings[shot.asset_id] = seg.source

    # --- the typographic layer, identical to the density cut ---------------- #
    events = plan_shot_motion_typography(
        scene_plan, shot_plan, caption_cues=caption_cues
    )
    if not events:
        raise SystemExit("the typography planner produced no events")
    scene = build_motion_graphics_scene(
        events, width=1920, height=1080, fps=30, duration_seconds=TARGET_DURATION
    )

    # --- the editorial treatment: how each shot is cut --------------------- #
    treatment_plan = plan_shot_editorial_treatment(
        scene_plan,
        shot_plan,
        directions=directions,
        motion_events=events,
        seed=shot_plan.seed,
    )

    from video_generator.domain.planning import shot_plan_to_edit_plan

    expanded = shot_plan_to_edit_plan(
        shot_plan,
        bindings,
        plan_id=PLAN_ID,
        brief_id=BRIEF_ID,
        output_path=str(OUTPUT_VIDEO),
        target_format=TargetFormat(1920, 1080, "cover"),
        directions=directions,
        treatments=treatment_plan.by_shot(),
    )
    segments = [
        op for op in expanded.operations if op.kind in ("image_clip", "sequence_clip")
    ]

    motion_graphics = EditOperation(
        operation_id="motion_graphics", kind="motion_graphics", parameters=scene
    )
    operations = (
        tuple(segments)
        + (
            EditOperation(
                operation_id=direction_op.operation_id,
                kind="visual_direction",
                parameters=direction_params,
            ),
            motion_graphics,
            EditOperation(
                operation_id="music_bed",
                kind="music",
                source=MUSIC,
                parameters=dict(music_op["parameters"]),
            ),
            EditOperation(
                operation_id="narration",
                kind="narration",
                source=NARRATION,
                parameters={"duration_policy": "match_timeline"},
            ),
        )
    )

    seg_sources: list[str] = []
    for op in segments:
        if op.source not in seg_sources:
            seg_sources.append(op.source)
    sources = tuple(seg_sources + [MUSIC, NARRATION])

    plan = EditPlan(
        plan_id=PLAN_ID,
        brief_id=BRIEF_ID,
        sources=sources,
        output_path=str(OUTPUT_VIDEO),
        operations=operations,
        target_format=TargetFormat(1920, 1080, "cover"),
    )

    request = VideoRequest(
        request_id=REQUEST_ID,
        intent=(
            "Recortar 'Desumanizando 01' com edicao de imagem editorial: a "
            "mesma narracao, os mesmos assets, a mesma direcao visual e a mesma "
            "camada de tipografia, mas agora cada plano nao-estatico e cortado "
            "por dentro — push, punch, reframe, revelacao de detalhe, freeze — "
            "para que um asset parado deixe de depender so do texto para ter "
            "atividade visual."
        ),
        sources=sources,
        platform="youtube",
        workflow="dark-video",
    )
    brief = VideoBrief(
        brief_id=BRIEF_ID,
        request_id=REQUEST_ID,
        objective=(
            "Um ensaio documental escuro de 209 s em 1920x1080 onde a voz "
            "carrega a informacao, o texto na tela funciona como edicao e a "
            "propria imagem e cortada: estabilidade proposital onde o beat "
            "pede calma, aproximacao gradual na tensao, corte interno decisivo "
            "no payoff, sem virar um festival de zoom."
        ),
        platform="youtube",
        workflow="dark-video",
        audience="adulto, interessado em psicologia e midia",
        target_duration_seconds=TARGET_DURATION,
        target_format=TargetFormat(1920, 1080, "cover"),
        editorial_notes=(
            "Tratamento editorial por shot, decidido pela intensidade do beat.",
            "Assets estaticos longos ganham corte interno, nao so texto.",
            "Piso de planos deliberadamente estaveis; sem run de um tratamento.",
            "Video respeita o movimento proprio: so hold ou corte entre janelas.",
            "Tipografia, densidade, relevancia e direcao visual herdadas.",
        ),
    )

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "edit-plan.json").write_text(plan.to_json(), encoding="utf-8")
    (OUT / "video-request.json").write_text(request.to_json(), encoding="utf-8")
    (OUT / "video-brief.json").write_text(brief.to_json(), encoding="utf-8")
    (OUT / "treatment.json").write_text(
        treatment_plan.to_json(), encoding="utf-8"
    )
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

    timeline = shot_timeline(shot_plan)
    shot_seconds = {sid: (end - start) for sid, (start, end) in timeline.items()}
    asset_types = {s.shot_id: s.asset_type for s in shot_plan.shots}
    reuse_of = {s.shot_id: s.reuse_of for s in shot_plan.shots}
    metrics = {
        "editorial_image_editing_v1": treatment_metrics(
            treatment_plan,
            shot_seconds=shot_seconds,
            asset_types=asset_types,
            reuse_of=reuse_of,
        ),
        "typography_layer": _metrics_from_typography(
            events, max(v[1] for v in timeline.values())
        ),
        "segments": {
            "shots": len(shot_plan.shots),
            "rendered_segments": len(segments),
            "extra_from_internal_cuts": len(segments) - len(shot_plan.shots),
            "image_segments": sum(1 for s in segments if s.kind == "image_clip"),
            "video_segments": sum(1 for s in segments if s.kind == "sequence_clip"),
        },
    }
    # a like-for-like baseline: the density cut treats no shot at all
    density_typo = _load(DENSITY_TYPO)["events"]
    metrics["editorial_density_v1_baseline"] = {
        "shots": len(shot_plan.shots),
        "treated_shots": 0,
        "internal_state_changes": 0,
        "editorial_events": 0,
        "deliberately_static_shots": len(shot_plan.shots),
        "typography_events": len(density_typo),
    }
    (OUT / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    m = metrics["editorial_image_editing_v1"]
    tre = Counter(t.treatment for t in treatment_plan.treatments)
    print(
        f"edit plan: {len(plan.operations)} operations, {len(plan.sources)} sources\n"
        f"segments: {len(segments)} for {len(shot_plan.shots)} shots "
        f"(+{len(segments) - len(shot_plan.shots)} from internal cuts)\n"
        f"treated: {m['treated_shots']}/{m['shots']} "
        f"({m['treated_fraction'] * 100:.0f}%)  "
        f"deliberately static: {m['deliberately_static_shots']}\n"
        f"internal state changes: {m['internal_state_changes']}  "
        f"editorial events/min: {m['editorial_events_per_minute']}\n"
        f"by treatment: {dict(tre)}\n"
        f"by intensity: {m['by_intensity']}\n"
        f"consecutive-same: {m['consecutive_same_treatment']}  "
        f"longest calm run: {m['longest_calm_run_shots']} shots "
        f"/ {m['longest_calm_run_seconds']}s\n"
        f"long static images: {m['static_image_shots_over_threshold']}\n"
        f"editorial reuse of same asset: {m['editorial_reuse_of_same_asset']}\n"
        f"-> {OUT / 'edit-plan.json'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
