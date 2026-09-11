"""Assemble the Remotion motion-graphics render plan for ``desumanizando_01``.

Takes the same base plan the Editorial Motion Typography cut used (timeline
segments + ``visual_direction`` + ``motion_typography``) and swaps the libass
``motion_typography`` operation for a ``motion_graphics`` operation: the exact
same editorial decisions — which words, when, how important — now carried as a
Remotion scene document and rendered as a transparent overlay that FFmpeg
composites over the finished picture.

Nothing about the voice, the assets or the visual direction changes. As with the
typography cut there is **no captions operation**: the force-aligned ``.srt``
stays a sidecar, never burned.

Read-only with respect to every input; everything it writes lands under
``output/motion-graphics-v1/``.
"""

from __future__ import annotations

import json
import shutil
import sys
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
    scene_from_typography_operation,
)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output" / "motion-graphics-v1"
BASE_PLAN = ROOT / "output" / "motion-typography-v1" / "edit-plan-base.json"
BASELINE_PLAN = ROOT / "output" / "editorial-translation-v1" / "edit-plan.json"
CAPTIONS = ROOT / "projects" / "desumanizando_01" / "captions-v2.srt"
MUSIC = "assets/desumanizando/video_01/audio/musica_ambiental.wav"
NARRATION = "projects/desumanizando_01/audio/narration-v2.wav"
OUTPUT_VIDEO = OUT / "final.mp4"

TARGET_DURATION = 209.261
REQUEST_ID = "desumanizando-01-motion-graphics-request"
BRIEF_ID = "desumanizando-01-motion-graphics-brief"
PLAN_ID = "desumanizando-01-motion-graphics-edit-plan"


def main() -> int:
    base = EditPlan.from_dict(json.loads(BASE_PLAN.read_text(encoding="utf-8")))
    baseline = json.loads(BASELINE_PLAN.read_text(encoding="utf-8"))
    music = next(op for op in baseline["operations"] if op["kind"] == "music")

    segments = [op for op in base.operations if op.kind in ("image_clip", "sequence_clip")]
    direction = next(op for op in base.operations if op.kind == "visual_direction")
    typography = next(op for op in base.operations if op.kind == "motion_typography")
    if any(op.kind == "captions" for op in base.operations):
        raise SystemExit("the motion graphics cut must not carry a captions operation")

    scene = scene_from_typography_operation(
        typography.parameters,
        width=1920,
        height=1080,
        fps=30,
        duration_seconds=TARGET_DURATION,
    )
    motion_graphics = EditOperation(
        operation_id="motion_graphics",
        kind="motion_graphics",
        parameters=scene,
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
            "Recortar 'Desumanizando 01' com a camada de motion graphics do "
            "Remotion: a mesma narracao, os mesmos assets e a mesma direcao "
            "visual, com o texto na tela composto como arte editorial em "
            "movimento e sobreposto como overlay com canal alfa."
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
            "carrega a informacao e o texto aparece poucas vezes como "
            "composicao tipografica em movimento, renderizada pelo Remotion, "
            "que pode ser pausada e lida como peca grafica."
        ),
        platform="youtube",
        workflow="dark-video",
        audience="adulto, interessado em psicologia e midia",
        target_duration_seconds=TARGET_DURATION,
        target_format=TargetFormat(1920, 1080, "cover"),
        editorial_notes=(
            "Sem legenda tradicional queimada; o .srt permanece como sidecar.",
            "O texto na tela e composto pelo Remotion e sobreposto via overlay alfa.",
            "Hierarquia obrigatoria: nenhum evento com dois blocos do mesmo peso.",
            "Assets, relevancia, traducao editorial e direcao visual herdados.",
        ),
    )

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "edit-plan.json").write_text(plan.to_json(), encoding="utf-8")
    (OUT / "video-request.json").write_text(request.to_json(), encoding="utf-8")
    (OUT / "video-brief.json").write_text(brief.to_json(), encoding="utf-8")
    shutil.copyfile(CAPTIONS, OUT / "captions.srt")
    print(
        f"edit plan: {len(plan.operations)} operations, "
        f"{len(plan.sources)} sources, "
        f"{len(scene['events'])} motion-graphics events, "
        f"0 burned captions -> {OUT / 'edit-plan.json'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
