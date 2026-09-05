"""Assemble the Editorial Visual Translation v1 render plan for ``desumanizando_01``.

Takes the directed EditPlan that ``plan-scenes`` emitted (timeline segments +
``visual_direction`` + ``text_events``) and adds the audio and caption layers
this cut inherits unchanged from the previous one: the same narration WAV, the
same force-aligned captions, the same music bed and ducking. Nothing about the
voice is touched in this cycle.

It also writes the ``VideoRequest`` / ``VideoBrief`` pair that declares this
cut's own sources, so ``validate-project`` has a complete contract chain to
walk rather than one borrowed from an earlier render.

Read-only with respect to every input; everything it writes lands under
``output/visual-direction-v1/``.
"""

from __future__ import annotations

import json
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

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output" / "editorial-translation-v1"
BASE_PLAN = OUT / "edit-plan-base.json"
BASELINE_PLAN = ROOT / "output" / "visual-direction-v1" / "edit-plan.json"
NARRATION = "projects/desumanizando_01/audio/narration-v2.wav"
MUSIC = "assets/desumanizando/video_01/audio/musica_ambiental.wav"
OUTPUT_VIDEO = OUT / "final.mp4"

REQUEST_ID = "desumanizando-01-editorial-translation-request"
BRIEF_ID = "desumanizando-01-editorial-translation-brief"
PLAN_ID = "desumanizando-01-editorial-translation-edit-plan"


def main() -> int:
    base = EditPlan.from_dict(json.loads(BASE_PLAN.read_text(encoding="utf-8")))
    baseline = json.loads(BASELINE_PLAN.read_text(encoding="utf-8"))

    captions = next(
        operation
        for operation in baseline["operations"]
        if operation["kind"] == "captions"
    )
    music = next(
        operation for operation in baseline["operations"] if operation["kind"] == "music"
    )

    segments = [op for op in base.operations if op.kind in ("image_clip", "sequence_clip")]
    direction = next(op for op in base.operations if op.kind == "visual_direction")
    text_events = next(op for op in base.operations if op.kind == "text_events")

    # The one order the workflow and the manifest validator both accept.
    operations = (
        tuple(segments)
        + (direction,)
        + (EditOperation.from_dict(captions),)
        + (text_events,)
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
            "Recortar 'Desumanizando 01' com Editorial Visual Translation "
            "v1: a mesma narracao, as mesmas legendas e a mesma direcao "
            "visual, com o material escolhido a partir do que poderia ser "
            "filmado para comunicar cada fala, e nao do substantivo que ela "
            "contem."
        ),
        sources=tuple(sources),
        platform="youtube",
        workflow="dark-video",
    )
    brief = VideoBrief(
        brief_id=BRIEF_ID,
        request_id=REQUEST_ID,
        objective=(
            "Um ensaio documental escuro de 209 s em 1920x1080 cujo material "
            "responde ao sentido editorial de cada fala, sem metafora de "
            "banco de imagens e sem query escrita a mao."
        ),
        platform="youtube",
        workflow="dark-video",
        audience="adulto, interessado em psicologia e midia",
        target_duration_seconds=209.261,
        target_format=TargetFormat(1920, 1080, "cover"),
        editorial_notes=(
            "Editorial Visual Translation v1: conceito filmavel por beat.",
            "Query sempre em ingles autorado; zero override manual neste corte.",
            "Recusa comparativa de metafora de stock e de registro ludico.",
            "Direcao visual, narracao, legendas, musica e ducking herdados.",
        ),
    )

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "edit-plan.json").write_text(plan.to_json(), encoding="utf-8")
    (OUT / "video-request.json").write_text(request.to_json(), encoding="utf-8")
    (OUT / "video-brief.json").write_text(brief.to_json(), encoding="utf-8")
    print(
        f"edit plan: {len(plan.operations)} operations, "
        f"{len(plan.sources)} sources -> {OUT / 'edit-plan.json'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
