"""Assemble the Editorial Motion Typography v1 render plan for ``desumanizando_01``.

Takes the plan ``plan-scenes`` emitted (timeline segments + ``visual_direction``
+ ``motion_typography``) and adds the audio this cut inherits unchanged: the
same narration WAV, the same music bed, the same ducking. Nothing about the
voice, the assets or the visual direction is touched in this cycle.

The one deliberate subtraction: **no captions operation**. This cut states its
text as composed typography or not at all, so a burned subtitle band would be
exactly the thing the layer replaces. The force-aligned ``.srt`` is still an
input — it is what times the type — and remains available as a sidecar for
platforms that want one.

Read-only with respect to every input; everything it writes lands under
``output/motion-typography-v1/``.
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

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output" / "motion-typography-v1"
BASE_PLAN = OUT / "edit-plan-base.json"
BASELINE_PLAN = ROOT / "output" / "editorial-translation-v1" / "edit-plan.json"
CAPTIONS = ROOT / "projects" / "desumanizando_01" / "captions-v2.srt"
NARRATION = "projects/desumanizando_01/audio/narration-v2.wav"
MUSIC = "assets/desumanizando/video_01/audio/musica_ambiental.wav"
OUTPUT_VIDEO = OUT / "final.mp4"

REQUEST_ID = "desumanizando-01-motion-typography-request"
BRIEF_ID = "desumanizando-01-motion-typography-brief"
PLAN_ID = "desumanizando-01-motion-typography-edit-plan"


def main() -> int:
    base = EditPlan.from_dict(json.loads(BASE_PLAN.read_text(encoding="utf-8")))
    baseline = json.loads(BASELINE_PLAN.read_text(encoding="utf-8"))
    music = next(
        operation for operation in baseline["operations"] if operation["kind"] == "music"
    )

    segments = [op for op in base.operations if op.kind in ("image_clip", "sequence_clip")]
    direction = next(op for op in base.operations if op.kind == "visual_direction")
    typography = next(op for op in base.operations if op.kind == "motion_typography")
    if any(op.kind == "captions" for op in base.operations):
        raise SystemExit("the motion typography cut must not carry a captions operation")

    operations = (
        tuple(segments)
        + (direction, typography)
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
            "Recortar 'Desumanizando 01' com Editorial Motion Typography v1: "
            "a mesma narracao, os mesmos assets e a mesma direcao visual, com "
            "o texto na tela composto como arte editorial em movimento no "
            "lugar da legenda queimada."
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
            "carrega a informacao e o texto na tela aparece poucas vezes, "
            "como composicao tipografica que pode ser pausada e lida como "
            "peca grafica."
        ),
        platform="youtube",
        workflow="dark-video",
        audience="adulto, interessado em psicologia e midia",
        target_duration_seconds=209.261,
        target_format=TargetFormat(1920, 1080, "cover"),
        editorial_notes=(
            "Sem legenda tradicional queimada; o .srt permanece como sidecar.",
            "Dezoito intervencoes tipograficas em 209 s, nao uma por frase.",
            "Hierarquia obrigatoria: nenhum evento com dois blocos do mesmo peso.",
            "Assets, relevancia, traducao editorial e direcao visual herdados.",
        ),
    )

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "edit-plan.json").write_text(plan.to_json(), encoding="utf-8")
    (OUT / "video-request.json").write_text(request.to_json(), encoding="utf-8")
    (OUT / "video-brief.json").write_text(brief.to_json(), encoding="utf-8")
    # the subtitles keep existing as an auxiliary deliverable, just not burned
    shutil.copyfile(CAPTIONS, OUT / "captions.srt")
    print(
        f"edit plan: {len(plan.operations)} operations, "
        f"{len(plan.sources)} sources, "
        f"{len(typography.parameters['items'])} typographic events, "
        f"0 burned captions -> {OUT / 'edit-plan.json'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
