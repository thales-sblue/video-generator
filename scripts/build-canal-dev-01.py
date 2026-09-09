"""Build the developer-video cut: canal_dev_01.

The channel's first video tells the story of this repository, so the material
is the repository: excerpts of the renders it actually produced, its own source
lines, its own plans, its own manifests. Nothing here invents an output.

The block table below is the single source of truth. It carries, per narrative
block, the text the author will record later, the shots that cover it and the
motion typography that lands on them. Running this file writes four artifacts
into ``projects/canal_dev_01/``:

    script.md    the narrative script, per block, with intent and assets
    timeline.md  the sequence map with every shot, its source and its seconds
    screens.json the screen-card deck built from repository material
    edit-plan.json  the silent 1920x1080 EditPlan the renderer executes

Provisional timing: there is no voice yet, so each block is stretched to the
time its own text would take to speak in Brazilian Portuguese at
``WORDS_PER_SECOND``. The authored shot proportions inside a block are kept.

Usage (from the repository root, with PYTHONPATH=src):

    python scripts/build-canal-dev-01.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from video_generator.domain.screens import ScreenCard, ScreenTheme, layout_card  # noqa: E402


PROJECT_DIR = REPOSITORY_ROOT / "projects" / "canal_dev_01"
SCREENS_DIR = REPOSITORY_ROOT / "output" / "canal-dev-01" / "screens"
OUTPUT_PATH = REPOSITORY_ROOT / "output" / "canal-dev-01" / "final.mp4"

# Every segment is normalised to this rate before the concat, so a duration
# that is not a whole number of frames is silently rounded by FFmpeg. Snapping
# here keeps the plan's arithmetic and the rendered file in agreement.
TIMELINE_FPS = 30

# A comfortable Brazilian-Portuguese narration pace, plus a breath per block.
WORDS_PER_SECOND = 2.55
BLOCK_BREATH_SECONDS = 0.9
TITLE = "Tentei automatizar vídeos para YouTube com IA — gerar o vídeo foi a parte fácil"

# Every moving source is a render this repository produced, or an asset it
# resolved. The key is what the block table refers to.
SOURCES = {
    "v0": "output/video_final.backup-retention-v0.mp4",
    "v2": "output/video_final.mp4",
    "vd": "output/visual-direction-v1/final.mp4",
    "tr": "output/editorial-translation-v1/final.mp4",
    "mt": "output/motion-typography-v1/final.mp4",
    "spike": "output/remotion-spike/spike.mp4",
    "lupa_asset": "output/visual-direction-v1/resolved/files/asset_scene_15_02.mp4",
}
SOURCE_NOTES = {
    "v0": "render de retenção v0 — 270 s, 68 shots, legendas queimadas",
    "v2": "render de retenção v2 — 209 s, legendas alinhadas ao áudio real",
    "vd": "Visual Direction v1 — o corte com as 6 lupas",
    "tr": "Editorial Visual Translation v1 — queries reescritas",
    "mt": "Editorial Motion Typography v1 — tipografia no lugar da legenda",
    "spike": "spike do Remotion na worktree remotion-motion-graphics",
    "lupa_asset": "asset_scene_15_02.mp4 — o resultado da query da lupa",
}


def card(card_id, seconds, motion="static_hold"):
    return {"kind": "card", "ref": card_id, "seconds": seconds, "motion": motion}


def clip(source, start, seconds):
    return {"kind": "clip", "ref": source, "start": start, "seconds": seconds}


def type_event(shot_index, seconds, layout, motion, blocks):
    return {
        "shot_index": shot_index,
        "seconds": seconds,
        "layout": layout,
        "motion": motion,
        "blocks": blocks,
    }


def word(text, weight="massive", accent=False):
    return {"text": text, "weight": weight, "accent": accent}


# --------------------------------------------------------------------------
# The screen-card deck. Every line below is quoted from a file in this
# repository or from a command this repository actually runs.
# --------------------------------------------------------------------------

CARDS = [
    ScreenCard(
        card_id="hook_execute",
        kind="terminal",
        title="$ python -m video_generator execute-final-sequence-plan --plan edit-plan.json",
        body=(
            "preflight   ok    59 sources",
            "compose     ok    209.30 s",
            "validate    ok    h264 1920x1080 / aac 48000 Hz",
            "publish     ok    output/final.mp4",
            "",
            "exit 0",
        ),
        highlight=(5,),
    ),
    ScreenCard(
        card_id="hook_tree",
        kind="code",
        title="src/video_generator/domain/",
        body=(
            "planning.py          2621",
            "editorial.py         1636",
            "assets.py            1353",
            "typography.py        1253",
            "visual_concept.py    1230",
            "relevance.py         1108",
            "direction.py         1061",
            "models.py             606",
        ),
    ),
    ScreenCard(
        card_id="idea_pipeline",
        kind="chain",
        title="O pipeline que eu queria",
        body=(
            "ROTEIRO",
            "CENAS",
            "SHOTS",
            "IMAGENS",
            "NARRAÇÃO",
            "TEXTO",
            "RENDER",
        ),
    ),
    ScreenCard(
        card_id="idea_rules",
        kind="list",
        title="AGENTS.md",
        subtitle="as regras que eu escrevi antes da primeira linha de código",
        body=(
            "LOCAL FIRST",
            "SEM API PAGA NO RUNTIME",
            "DETERMINÍSTICO",
            "REPRODUZÍVEL",
        ),
        highlight=(0, 1),
    ),
    ScreenCard(
        card_id="idea_doctor",
        kind="terminal",
        title="$ python -m video_generator doctor",
        body=(
            "Local-only: enabled",
            "External services: disabled",
            "Tools:",
            "  [available] Python       3.12.10",
            "  [available] FFmpeg       n8.1.2 (.local-tools, travado por SHA-256)",
            "  [available] ffprobe      n8.1.2 (.local-tools, travado por SHA-256)",
            "  [missing (optional)] Kokoro     modelo presente, extra tts ausente",
            "  [missing (optional)] Aligner    modelo presente, extra align ausente",
        ),
        highlight=(0, 1),
        footer="config/ffmpeg-lock.json — nada é adicionado ao PATH global",
    ),
    ScreenCard(
        card_id="idea_deps",
        kind="stat",
        title="0",
        body=(
            "dependências de runtime",
            "o domínio inteiro roda com a stdlib do Python",
            "os testes também",
        ),
    ),
    ScreenCard(
        card_id="idea_layers",
        kind="code",
        title="AGENTS.md",
        subtitle="a regra de dependência, escrita antes do primeiro adapter",
        body=(
            "As dependências apontam para dentro: adapters e renderers",
            "podem depender do domínio; o domínio nunca depende deles.",
        ),
        highlight=(0, 1),
    ),
    ScreenCard(
        card_id="idea_git",
        kind="terminal",
        title="$ git log --oneline",
        body=(
            "34ab723  Add the throwaway Kokoro-vs-Chatterbox voice benchmark harness",
            "02e3f25  Set the words on screen as type instead of transcribing the voice",
            "a1851ae  Ask what could be filmed before asking the provider for a picture",
            "6aff3b0  Decide how each chosen asset appears, not only which one it is",
            "36cdc2a  Ask why the viewer is looking at a shot before choosing its picture",
            "...",
            "6904369  Establish local-only video generator foundation",
        ),
        highlight=(6,),
        footer="55 commits desde 27 de agosto",
    ),
    ScreenCard(
        card_id="worked_suite",
        kind="terminal",
        title="$ ls tests/",
        body=(
            "test_planning.py           test_editorial.py",
            "test_asset_resolver.py     test_editorial_translation.py",
            "test_visual_direction.py   test_motion_typography.py",
            "test_sequence_workflow.py  test_manifest_validation.py",
            "test_ffmpeg_adapter.py     test_project_validation.py",
            "",
            "40 arquivos, 826 testes, nenhuma dependencia externa",
        ),
        highlight=(6,),
    ),
    ScreenCard(
        card_id="images_provenance",
        kind="json",
        title="output/resolved-v3/asset-provenance.json",
        body=(
            "{",
            '  "asset_id": "asset_scene_01_01",',
            '  "author": "Ron Lach",',
            '  "license": "Pexels",',
            '  "source_url": "https://www.pexels.com/photo/elderly-man-...",',
            '  "sha256": "604f4098c35e9228b053448d8366a287fc056ae42d31f001b6",',
            '  "acquired_at": "2026-09-03T05:37:06Z"',
            "}",
        ),
        highlight=(2, 3),
        footer="cada imagem que entra no corte sabe de onde veio",
    ),
    ScreenCard(
        card_id="arch_alpha",
        kind="json",
        title="output/remotion-spike/alpha-scene.json",
        subtitle="o Python entrega a decisão; o Remotion resolve o layout",
        body=(
            "{",
            '  "composition": { "width": 1920, "height": 1080, "fps": 30 },',
            '  "events": [ {',
            '      "layout": "small-plus-massive",',
            '      "motion": "stagger_rise",',
            '      "blocks": [ { "text": "INTELIGENTE", "importance": "support" },',
            '                  { "text": "ACREDITA",    "importance": "dominant" } ]',
            "  } ]",
            "}",
        ),
        highlight=(3, 4),
    ),
    ScreenCard(
        card_id="arch_node",
        kind="terminal",
        title="$ ls .local-tools/",
        body=(
            "ffmpeg/    n8.1.2, travado por SHA-256",
            "node/      v24.20.0, portatil, com SHASUMS256.txt",
            "kokoro/    modelo de voz local",
            "whisper/   modelo de alinhamento local",
            "",
            "nada disso entra no PATH global",
        ),
        highlight=(1,),
    ),
    ScreenCard(
        card_id="worked_tests",
        kind="terminal",
        title="$ python -m unittest discover -s tests",
        body=(
            "Ran 826 tests in 15.884s",
            "",
            "OK (skipped=1)",
        ),
        highlight=(2,),
    ),
    ScreenCard(
        card_id="worked_shot",
        kind="json",
        title="output/plan-v3/shot-plan.json",
        body=(
            "{",
            '  "shot_id": "scene_01_shot_01",',
            '  "purpose": "open on a credible human face, not an idea",',
            '  "visual_query": "elderly man reading a newspaper",',
            '  "framing": { "crop_bias": "right", "motion": "zoom_in" },',
            '  "duration_seconds": 3.930235301691553,',
            '  "shot_type": "photography"',
            "}",
        ),
        highlight=(2, 3),
    ),
    ScreenCard(
        card_id="worked_manifest",
        kind="json",
        title="output/motion-typography-v1/final.mp4.manifest.json",
        body=(
            "{",
            '  "local_only": true,',
            '  "outputs": [ {',
            '      "file_size_bytes": 193660990,',
            '      "sha256": "de2c3f7db440699070d77bcceebd53a820fbbffe33afd4064eddb4aa08f275a3"',
            "  } ],",
            '  "plan_sha256": "0ca50376470d5e0e15b600c48cdfd7e63d94ad980a852063a0c911016d4b7041"',
            "}",
        ),
        highlight=(4, 6),
    ),
    ScreenCard(
        card_id="worked_validate",
        kind="terminal",
        title="$ python -m video_generator validate-manifest && validate-project",
        body=(
            "manifest    ok    todos os sources conferem por SHA-256",
            "project     ok    request -> brief -> plan -> manifest",
            "",
            "exit 0",
        ),
        highlight=(3,),
        footer="editorial_review: not_performed",
    ),
    ScreenCard(
        card_id="slideshow_repeat",
        kind="compare",
        title="output/video_final.backup-retention-v0.mp4",
        column_titles=("O QUE O PLANO DIZIA", "O QUE O CORTE MOSTROU"),
        columns=(
            (
                "68 shots",
                "30 cenas",
                "cada shot com a sua query",
                "",
                "270 s",
            ),
            (
                "41 arquivos distintos",
                "19 reusados",
                "8 deles três vezes",
                "",
                "imagem, corte, imagem, legenda",
            ),
        ),
    ),
    ScreenCard(
        card_id="slideshow_verdict",
        kind="statement",
        body=("FUNCIONAVA.", "MAS PARECIA", "AUTOMÁTICO."),
        highlight=(2,),
    ),
    ScreenCard(
        card_id="cuts_planner",
        kind="code",
        title="src/video_generator/domain/planning.py",
        subtitle="o roteiro deixa de ser texto e vira uma estrutura",
        body=(
            "def plan_scenes(script, *, policy, seed=0) -> ScenePlan:",
            "def plan_shots(scene_plan, *, policy, seed=0) -> ShotPlan:",
            "def shot_plan_to_edit_plan(shot_plan, asset_bindings, ...) -> EditPlan:",
        ),
    ),
    ScreenCard(
        card_id="cuts_count",
        kind="chain",
        title="output/plan-v3/",
        body=("1 ROTEIRO", "26 CENAS", "59 SHOTS"),
        highlight=(2,),
    ),
    ScreenCard(
        card_id="cuts_verdict",
        kind="statement",
        body=("59 IMAGENS", "GENÉRICAS", "AINDA SÃO", "GENÉRICAS."),
        highlight=(3,),
    ),
    ScreenCard(
        card_id="images_resolver",
        kind="code",
        title="src/video_generator/domain/assets.py",
        subtitle="o resolver escolhe entre candidatos — e sabe justificar a escolha",
        body=(
            "def sanitize_query(...) -> str:",
            "def score_candidate(requirement, candidate, policy) -> ScoreBreakdown:",
            "def rank_candidates(requirement, candidates, policy) -> list[...]:",
            "def review_reuse(...) -> ReuseReview:",
        ),
    ),
    ScreenCard(
        card_id="images_scores",
        kind="json",
        title="output/relevance-eval/ — score_breakdown",
        body=(
            "{",
            '  "semantic_overlap": 0.0,',
            '  "orientation": 1.0,',
            '  "resolution": 1.0,',
            '  "type_match": 1.0,',
            '  "verdict": "no_semantic_match"',
            "}",
        ),
        highlight=(1, 5),
        footer="passar em tipo, orientação e resolução não é acertar a imagem",
    ),
    ScreenCard(
        card_id="images_verdict",
        kind="compare",
        title="A pergunta que o sistema estava respondendo",
        column_titles=("O QUE ELE FAZIA", "O QUE EU PRECISAVA"),
        columns=(
            (
                "buscar uma imagem",
                "relacionada à palavra",
                "",
                "resposta fiel",
            ),
            (
                "escolher a imagem",
                "editorialmente certa",
                "",
                "resposta útil",
            ),
        ),
    ),
    ScreenCard(
        card_id="lupa_code",
        kind="code",
        title="src/video_generator/domain/editorial.py:253",
        subtitle="o léxico de conceitos — escrito por mim",
        body=(
            "_DEFAULT_CONCEPT_LEXICON = MappingProxyType(",
            "    {",
            '        "verdade": _entry("magnifying glass over a document"),',
            '        "inteligencia": _entry("chess board mid game"),',
            '        "pergunta": _entry("question mark chalked on a board"),',
            "    }",
            ")",
        ),
        highlight=(2,),
    ),
    ScreenCard(
        card_id="lupa_chain",
        kind="chain",
        title="scene_15_shot_02",
        body=("VERDADE", "MAGNIFYING GLASS OVER A DOCUMENT", "?"),
        highlight=(1,),
    ),
    ScreenCard(
        card_id="lupa_count",
        kind="stat",
        title="6",
        body=(
            "lupas no mesmo corte de 209 segundos",
            "4 tabuleiros de xadrez",
            "3 vezes a mesma query dentro de uma cena",
        ),
        footer="output/editorial-translation-v1/REPORT.md",
    ),
    ScreenCard(
        card_id="lupa_verdict",
        kind="statement",
        body=("O ALGORITMO FEZ", "EXATAMENTE", "O QUE EU PEDI."),
        highlight=(1,),
    ),
    ScreenCard(
        card_id="turn_before",
        kind="chain",
        title="Antes",
        body=("TEXTO", "KEYWORD", "ASSET"),
    ),
    ScreenCard(
        card_id="turn_after",
        kind="chain",
        title="Depois",
        body=(
            "TEXTO",
            "INTENÇÃO",
            "CONCEITO VISUAL",
            "PAPEL VISUAL",
            "QUERY",
            "ASSET",
            "DIREÇÃO",
        ),
        highlight=(1, 2, 3),
    ),
    ScreenCard(
        card_id="turn_relevance",
        kind="code",
        title="src/video_generator/domain/relevance.py",
        subtitle="por que o espectador está olhando para este shot",
        body=(
            "VISUAL_INTENTS = (",
            '    "literal", "metaphorical", "emotional", "scientific",',
            '    "evidence_or_archive", "everyday_human", "tension_or_suspense",',
            ")",
            "VISUAL_ROLES = (",
            '    "explain", "symbolize", "shock", "humanize",',
            '    "contextualize", "build_tension", "support_claim",',
            ")",
            "",
            "def refine_query(...) -> str:",
        ),
        highlight=(9,),
    ),
    ScreenCard(
        card_id="turn_concept",
        kind="code",
        title="src/video_generator/domain/visual_concept.py",
        subtitle="o que poderia ser filmado, antes de pedir uma foto",
        body=(
            '# the pipeline asked "which noun is in this sentence?"',
            '# instead of "what could be filmed to communicate this thought?"',
            "",
            "def translate_beat(...) -> VisualTranslation:",
            "def stock_metaphor_props() -> frozenset[str]:",
            "def assess_editorial_fit(...) -> EditorialFit:",
        ),
        highlight=(1, 3),
    ),
    ScreenCard(
        card_id="turn_result",
        kind="compare",
        title="output/editorial-translation-v1/query-comparison.md",
        column_titles=("BASELINE", "DEPOIS DA TRADUÇÃO"),
        columns=(
            (
                "magnifying glass",
                "over a document",
                "",
                "17 shots precisaram de",
                "override escrito à mão",
            ),
            (
                "long row of identical",
                "windows on a facade",
                "",
                "0 overrides",
                "",
            ),
        ),
    ),
    ScreenCard(
        card_id="type_compare",
        kind="compare",
        title="O mesmo segundo, dois cortes",
        column_titles=("LEGENDA", "TIPOGRAFIA"),
        columns=(
            (
                "transcreve a fala",
                "fica embaixo",
                "some quando a voz some",
                "",
                "acompanha",
            ),
            (
                "escolhe uma palavra",
                "ocupa a composição",
                "existe por conta própria",
                "",
                "faz parte",
            ),
        ),
    ),
    ScreenCard(
        card_id="type_event",
        kind="json",
        title="output/motion-typography-v1/edit-plan.json",
        body=(
            "{",
            '  "kind": "motion_typography",',
            '  "blocks": [ { "text": "INTELIGENTE", "weight": "small" },',
            '              { "text": "ACREDITA",    "weight": "massive" } ],',
            '  "layout": "small_plus_massive",',
            '  "motion": "stagger_rise"',
            "}",
        ),
        highlight=(4, 5),
    ),
    ScreenCard(
        card_id="arch_layers",
        kind="chain",
        title="Onde cada decisão mora",
        body=(
            "PYTHON — decisão editorial",
            "REMOTION — composição gráfica",
            "FFMPEG — composição e render final",
        ),
        highlight=(0,),
    ),
    ScreenCard(
        card_id="arch_filtergraph",
        kind="code",
        title="src/video_generator/adapters/ffmpeg.py",
        subtitle="o filtergraph que eu estava fazendo crescer sem parar",
        body=(
            "scale=1920:1080:force_original_aspect_ratio=decrease,",
            "pad=1920:1080:(ow-iw)/2:(oh-ih)/2,",
            "zoompan=z='min(zoom+0.0006,1.12)':d=...,",
            "eq=saturation=0.78:gamma=0.92, vignette=a=0.4,",
            "setsar=1, fps=30, format=yuv420p",
        ),
        footer="tipografia com hierarquia não cabe aqui — e não deveria caber",
    ),
    ScreenCard(
        card_id="open_manifest",
        kind="json",
        title="output/canal-dev-01/final.mp4.manifest.json",
        body=(
            "{",
            '  "local_only": true,',
            '  "editorial_review": "not_performed",',
            '  "outputs": [ { "sha256": "..." } ]',
            "}",
        ),
        highlight=(2,),
        footer="sucesso técnico não é aprovação editorial",
    ),
    ScreenCard(
        card_id="open_defects",
        kind="list",
        title="O que ainda está errado",
        body=(
            "assets que ainda cheiram a banco de imagens",
            "movimento que ainda parece automático",
            "tipografia que às vezes briga com a foto",
            "voz sintética que ninguém pediu",
            "decisão editorial que eu ainda não sei codificar",
        ),
        highlight=(4,),
    ),
    ScreenCard(
        card_id="open_question",
        kind="compare",
        title="Os dois testes",
        column_titles=("TECHNICALLY VALID", "EDITORIALLY GOOD?"),
        columns=(
            (
                "826 testes",
                "SHA-256 em cada source",
                "exit 0",
                "",
                "respondido",
            ),
            (
                "sem métrica",
                "sem teste",
                "sem exit code",
                "",
                "ainda em aberto",
            ),
        ),
    ),
    ScreenCard(
        card_id="end_one",
        kind="statement",
        body=("GERAR VÍDEO", "VIROU ENGENHARIA."),
    ),
    ScreenCard(
        card_id="end_two",
        kind="statement",
        body=("EDITAR BEM", "CONTINUA SENDO", "DECISÃO."),
        highlight=(2,),
    ),
    ScreenCard(
        card_id="end_card",
        kind="list",
        title="video-generator",
        subtitle="o repositório inteiro, incluindo os erros, está aberto",
        body=(
            "55 commits",
            "826 testes",
            "0 dependências de runtime",
            "1 vídeo que ainda não me convence",
        ),
        highlight=(3,),
        footer="este canal documenta esse tipo de construção",
    ),
]


# --------------------------------------------------------------------------
# The blocks. Order is the video.
# --------------------------------------------------------------------------

BLOCKS = [
    {
        "id": "01_hook",
        "title": "Hook",
        "intent": (
            "Abrir dentro do problema, não na apresentação. O espectador vê o "
            "resultado automático antes de ouvir a promessa."
        ),
        "narration": (
            "Eu queria dar um roteiro para um programa e receber um vídeo pronto "
            "para o YouTube. Tecnicamente, eu consegui. O problema é que o "
            "resultado parecia exatamente o que ele era: um vídeo feito "
            "automaticamente. Isso aqui é o primeiro corte que saiu inteiro. E "
            "isso aqui é o mesmo roteiro, seis versões depois."
        ),
        "shots": [
            clip("v0", 44.0, 3.4),
            clip("v0", 135.5, 2.8),
            clip("v0", 158.5, 2.4),
            card("hook_execute", 3.0),
            card("hook_tree", 2.6),
            clip("mt", 68.4, 3.6),
            clip("mt", 155.4, 3.4),
        ],
        "type_events": [
            type_event(0, 2.4, "small_plus_massive", "stagger_rise", [
                word("TENTEI AUTOMATIZAR", "small"),
                word("VÍDEOS PARA O YOUTUBE", "massive"),
            ]),
            type_event(5, 2.6, "contrast_pair", "masked_reveal", [
                word("GERAR O VÍDEO", "large"),
                word("FOI A PARTE FÁCIL", "massive", accent=True),
            ]),
        ],
    },
    {
        "id": "02_ideia",
        "title": "A ideia inicial",
        "intent": (
            "Mostrar que existia um plano de arquitetura, não um experimento "
            "solto: um pipeline local do roteiro até o render."
        ),
        "narration": (
            "A ideia era simples de enunciar e difícil de cumprir: um pipeline "
            "local que fosse do roteiro até o render. Roteiro vira cenas, cenas "
            "viram shots, shots pedem imagens, o texto vira narração, a narração "
            "vira legenda, e no fim o FFmpeg monta tudo. Com três regras que eu "
            "escrevi antes da primeira linha de código: tudo roda na minha "
            "máquina, nenhuma API paga no runtime, e o mesmo plano tem que "
            "produzir o mesmo arquivo."
        ),
        "shots": [
            card("idea_pipeline", 4.6, motion="slow_push_in"),
            clip("v0", 4.5, 2.8),
            card("idea_layers", 3.4, motion="detail_push"),
            card("idea_rules", 4.0),
            card("idea_doctor", 4.4),
            card("idea_git", 3.8),
            card("idea_deps", 3.2, motion="slow_push_in"),
        ],
        "type_events": [
            type_event(1, 2.2, "dominant_word", "scale_in", [word("LOCAL FIRST")]),
        ],
    },
    {
        "id": "03_funcionava",
        "title": "Tecnicamente funcionava",
        "intent": (
            "Provar que a coisa existe: testes, planos, manifests, hashes, exit "
            "zero. Evidência, não propaganda."
        ),
        "narration": (
            "E funcionou. Existe uma suíte de testes que roda em quinze segundos. "
            "Existe um plano de shots em JSON, com a intenção de cada shot escrita "
            "por extenso. Existe um manifest que guarda o SHA-256 de cada arquivo "
            "que entrou no render e do plano que gerou ele. Existe um comando que "
            "valida a cadeia inteira e devolve exit zero. Do ponto de vista de "
            "engenharia, isso está resolvido."
        ),
        "shots": [
            card("worked_tests", 3.4),
            card("worked_suite", 3.4),
            card("worked_shot", 4.6),
            clip("mt", 30.0, 2.6),
            card("worked_manifest", 4.4),
            card("worked_validate", 3.8, motion="slow_push_in"),
            clip("mt", 12.0, 3.0),
        ],
        "type_events": [
            type_event(6, 2.2, "dominant_word", "scale_in", [
                word("TECNICAMENTE VÁLIDO", "large"),
            ]),
        ],
    },
    {
        "id": "04_slideshow",
        "title": "O primeiro problema: parecia slideshow",
        "intent": (
            "Deixar o espectador sentir o defeito sozinho, com tempo de tela "
            "suficiente, antes de nomear o defeito."
        ),
        "narration": (
            "Só que assistir era outra coisa. Olha o ritmo: imagem, movimento "
            "simples, corte, imagem, legenda, imagem. E o mesmo asset aparecendo "
            "três vezes no mesmo vídeo, com quarenta e cinco segundos de distância, "
            "porque para o resolver ele continuava sendo a melhor resposta para "
            "aquela palavra. Nada disso está quebrado. Tudo isso está errado."
        ),
        "shots": [
            clip("v0", 135.0, 4.6),
            clip("v0", 180.0, 4.2),
            clip("v0", 223.5, 4.2),
            clip("v0", 44.0, 2.6),
            clip("v0", 127.5, 2.6),
            card("slideshow_repeat", 4.8),
            card("slideshow_verdict", 3.6, motion="slow_push_in"),
        ],
        "type_events": [
            type_event(2, 2.4, "dominant_word", "fade_rise", [
                word("O MESMO ASSET", "large"),
            ]),
            type_event(4, 2.0, "dominant_word", "fade_rise", [
                word("DE NOVO", "massive", accent=True),
            ]),
        ],
    },
    {
        "id": "05_cortes",
        "title": "Então eu coloquei mais cortes",
        "intent": (
            "Mostrar a primeira tentativa de conserto — densidade — e mostrar que "
            "ela não resolveu o problema."
        ),
        "narration": (
            "Minha primeira hipótese foi ritmo. Se parece parado, corta mais. "
            "Construí um planner que transforma o roteiro em cenas e cenas em "
            "shots, com duração, enquadramento e intenção por shot. Um roteiro "
            "virou vinte e seis cenas e cinquenta e nove shots. O corte ficou mais "
            "rápido. E continuou parecendo automático, porque cinquenta e nove "
            "imagens genéricas continuam sendo imagens genéricas."
        ),
        "shots": [
            card("cuts_planner", 4.2),
            card("cuts_count", 3.8, motion="slow_push_in"),
            clip("v2", 96.0, 2.6),
            clip("v2", 120.5, 2.4),
            clip("v2", 173.0, 2.4),
            card("cuts_verdict", 3.8, motion="slow_push_in"),
        ],
        "type_events": [
            type_event(2, 2.0, "small_plus_massive", "stagger_rise", [
                word("26 CENAS", "small"),
                word("59 SHOTS", "massive", accent=True),
            ]),
        ],
    },
    {
        "id": "06_imagens",
        "title": "O problema das imagens",
        "intent": (
            "Separar 'buscar uma imagem relacionada' de 'escolher a imagem "
            "editorialmente certa'."
        ),
        "narration": (
            "Aí eu fui olhar o resolver. Ele pontua cada candidato: tipo de mídia, "
            "orientação, resolução, sobreposição semântica com a query. E o que eu "
            "vi no relatório foi um monte de asset com nota alta em tudo, menos na "
            "única coisa que importa. Ele passava em tipo, orientação e resolução, "
            "e a sobreposição semântica era zero. Buscar uma imagem relacionada a "
            "uma palavra não é a mesma coisa que escolher a imagem certa para "
            "aquele momento."
        ),
        "shots": [
            card("images_resolver", 4.2),
            card("images_scores", 4.6, motion="slow_push_in"),
            clip("v0", 89.5, 2.8),
            card("images_provenance", 4.2, motion="detail_push"),
            clip("v0", 202.0, 2.8),
            clip("v0", 60.5, 2.6),
            card("images_verdict", 4.6),
        ],
        "type_events": [
            type_event(4, 2.0, "dominant_word", "scale_in", [
                word("SEMANTIC OVERLAP 0.0", "large", accent=True),
            ]),
        ],
    },
    {
        "id": "07_lupa",
        "title": "A lupa",
        "intent": (
            "O ponto de virada. O erro não estava na busca; estava na decisão "
            "anterior, escrita por mim, em uma linha de código."
        ),
        "narration": (
            "E então eu achei a lupa. Eu tinha escrito um léxico que traduz "
            "substantivo em cena filmável. Verdade virou lupa sobre um documento. "
            "Inteligência virou tabuleiro de xadrez. O resolver fez o trabalho "
            "dele perfeitamente: foi buscar uma lupa, e trouxe um homem com uma "
            "lupa no olho, na frente de um espelho, enquanto a narração falava "
            "sobre repetição. Seis lupas no mesmo corte. O algoritmo não errou. "
            "Ele fez exatamente o que eu pedi."
        ),
        "shots": [
            card("lupa_code", 5.0, motion="detail_push"),
            card("lupa_chain", 3.6, motion="slow_push_in"),
            clip("vd", 118.6, 3.0),
            clip("lupa_asset", 2.0, 3.4),
            clip("vd", 113.9, 2.6),
            card("lupa_count", 3.6),
            card("lupa_verdict", 4.0, motion="slow_push_in"),
        ],
        "type_events": [
            type_event(2, 2.4, "contrast_pair", "masked_reveal", [
                word("VERDADE", "large"),
                word("LUPA", "massive", accent=True),
            ]),
            type_event(4, 2.0, "dominant_word", "scale_in", [word("SEIS VEZES")]),
        ],
    },
    {
        "id": "08_pergunta",
        "title": "A mudança de pergunta",
        "intent": (
            "Mostrar que a correção não foi uma blacklist maior, e sim uma cadeia "
            "de decisão mais longa antes da query."
        ),
        "narration": (
            "A correção óbvia seria proibir a lupa. Mas isso é uma blacklist que "
            "só cresce, e ela não pega o caso seguinte. O que mudou foi a pergunta. "
            "Antes: texto, palavra-chave, asset. Depois: qual é a intenção deste "
            "trecho, que conceito filmável corresponde a essa intenção, qual é o "
            "papel visual do shot, só então a query, o asset e a direção de como "
            "ele aparece. Cada uma dessas camadas é um módulo com testes. Os "
            "dezessete shots que precisavam de override escrito à mão foram para "
            "zero."
        ),
        "shots": [
            card("turn_before", 3.2, motion="slow_push_in"),
            card("turn_after", 4.6, motion="slow_push_in"),
            card("turn_relevance", 3.8),
            card("turn_concept", 4.0, motion="detail_push"),
            card("turn_result", 4.6),
            clip("tr", 96.0, 2.6),
            clip("tr", 40.5, 2.4),
            clip("tr", 120.5, 2.6),
        ],
        "type_events": [
            type_event(5, 2.2, "contrast_pair", "masked_reveal", [
                word("17 OVERRIDES", "large"),
                word("ZERO", "massive", accent=True),
            ]),
        ],
    },
    {
        "id": "09_texto",
        "title": "Texto não é legenda",
        "intent": (
            "Uma comparação A/B literal: o mesmo segundo do mesmo corte, com "
            "legenda e com tipografia."
        ),
        "narration": (
            "A segunda coisa que denunciava o vídeo era o texto. Legenda "
            "transcreve a fala e mora embaixo. Ela acompanha. Tipografia escolhe "
            "uma palavra e ocupa a composição. Ela faz parte. Este é o mesmo "
            "segundo do mesmo corte, com as duas abordagens. A diferença não é de "
            "fonte, é de decisão: alguém precisa escolher qual palavra merece "
            "aparecer."
        ),
        "shots": [
            clip("tr", 68.5, 4.0),
            clip("mt", 68.5, 4.0),
            clip("tr", 155.5, 3.4),
            clip("mt", 155.5, 3.4),
            card("type_compare", 4.6),
            card("type_event", 4.2),
        ],
        "type_events": [
            # Only the caption side is labelled: the typography side answers with
            # the render's own composed type, which is the whole point.
            type_event(0, 2.2, "dominant_word", "fade_rise", [
                word("LEGENDA", "large"),
            ]),
        ],
    },
    {
        "id": "10_arquitetura",
        "title": "FFmpeg e Remotion",
        "intent": (
            "Registrar uma decisão arquitetural real: parar de empurrar tudo para "
            "dentro do filtergraph."
        ),
        "narration": (
            "Isso me levou à decisão arquitetural mais chata e mais importante do "
            "projeto. Eu estava fazendo o filtergraph do FFmpeg crescer para dar "
            "conta de layout e hierarquia tipográfica. Isso não cabe ali. Hoje a "
            "divisão é essa: o Python decide o que é dito, o Remotion compõe o "
            "gráfico com alpha, e o FFmpeg volta a fazer o que ele faz bem, que é "
            "compor e renderizar. O spike já roda numa worktree separada."
        ),
        "shots": [
            card("arch_layers", 4.4, motion="slow_push_in"),
            card("arch_filtergraph", 4.6, motion="detail_push"),
            card("arch_alpha", 4.4),
            clip("spike", 4.0, 3.6),
            card("arch_node", 3.4),
            clip("spike", 18.0, 3.4),
            clip("spike", 26.0, 2.8),
        ],
        "type_events": [],
    },
    {
        "id": "11_aberto",
        "title": "E ainda não está pronto",
        "intent": (
            "Não fechar fingindo que acabou. Mostrar o que continua quebrado e o "
            "campo do manifest que continua vazio."
        ),
        "narration": (
            "E eu não vou terminar dizendo que resolvi. O manifest desse vídeo "
            "ainda tem um campo escrito assim: editorial review, not performed. "
            "Ele continua assim porque nenhuma métrica que eu escrevi consegue "
            "dizer se o corte é bom. Ainda tem asset com cara de banco de imagens. "
            "Ainda tem movimento que parece automático. Ainda tem tipografia "
            "brigando com a foto. Tecnicamente válido eu sei medir. Editorialmente "
            "bom eu ainda não sei."
        ),
        "shots": [
            card("open_manifest", 4.6, motion="detail_push"),
            card("open_defects", 4.8),
            clip("mt", 40.0, 2.8),
            clip("v0", 246.5, 2.6),
            clip("v2", 60.0, 2.6),
            clip("mt", 96.0, 2.6),
            card("open_question", 5.0, motion="slow_push_in"),
        ],
        "type_events": [
            type_event(3, 2.0, "dominant_word", "fade_rise", [
                word("NOT PERFORMED", "large", accent=True),
            ]),
            type_event(4, 2.4, "contrast_pair", "masked_reveal", [
                word("TECHNICALLY VALID", "small"),
                word("EDITORIALLY GOOD?", "massive", accent=True),
            ]),
        ],
    },
    {
        "id": "12_final",
        "title": "Final",
        "intent": "Fechar com a tese, não com um CTA.",
        "narration": (
            "Gerar vídeo virou engenharia. Editar bem continua sendo um problema "
            "de decisão. O repositório inteiro está aberto, incluindo os erros que "
            "você acabou de ver. E é exatamente esse tipo de construção que eu vou "
            "documentar aqui."
        ),
        "shots": [
            card("end_one", 3.4, motion="slow_push_in"),
            card("end_two", 3.6, motion="slow_push_in"),
            clip("mt", 200.0, 3.0),
            card("end_card", 4.4),
        ],
        "type_events": [],
    },
]


TYPOGRAPHY_STYLE = {
    "font_name": "Segoe UI",
    "support_font_name": "Bahnschrift",
    "foreground": "&H00EFECE8",
    "accent": "&H003CA3E5",
    "muted": "&H009E948A",
    "safe_margin_fraction": 0.07,
}

VISUAL_DIRECTION = {
    "style_id": "canal-dev-01-screen-v1",
    "push_travel": 0.045,
    "detail_push_travel": 0.075,
    "drift_travel": 0.05,
}


def snap(seconds: float) -> float:
    """Round a duration to a whole number of timeline frames."""

    return round(round(seconds * TIMELINE_FPS) / TIMELINE_FPS, 6)


def speech_seconds(text: str) -> float:
    return len(text.split()) / WORDS_PER_SECOND + BLOCK_BREATH_SECONDS


def build_timeline():
    """Scale every block to its own speech estimate and lay the shots end to end."""

    timeline = []
    clock = 0.0
    for block in BLOCKS:
        authored = sum(shot["seconds"] for shot in block["shots"])
        target = speech_seconds(block["narration"])
        scale = target / authored
        block_start = clock
        placed = []
        for shot in block["shots"]:
            seconds = snap(shot["seconds"] * scale)
            placed.append({**shot, "seconds": seconds, "start": snap(clock)})
            clock = snap(clock + seconds)
        timeline.append(
            {
                "block": block,
                "shots": placed,
                "start": round(block_start, 3),
                "seconds": snap(clock - block_start),
                "speech_seconds": round(target, 2),
                "scale": round(scale, 3),
            }
        )
    return timeline, snap(clock)


def build_type_items(timeline):
    items = []
    for entry in timeline:
        for event in entry["block"]["type_events"]:
            shot = entry["shots"][event["shot_index"]]
            # A screen card is already type. Overlaying more type on one puts two
            # typographic systems in the same frame, and they collide.
            if shot["kind"] != "clip":
                raise SystemExit(
                    f"motion typography on {entry['block']['id']} shot "
                    f"{event['shot_index']} would land on a screen card"
                )
            start = snap(shot["start"] + 0.3)
            span = min(event["seconds"], shot["seconds"] - 0.5)
            if span < 0.4:
                raise SystemExit(
                    f"type event on {entry['block']['id']} shot "
                    f"{event['shot_index']} has no room ({shot['seconds']} s)"
                )
            items.append(
                {
                    "blocks": event["blocks"],
                    "start_seconds": start,
                    "end_seconds": snap(start + span),
                    "layout": event["layout"],
                    "motion": event["motion"],
                }
            )
    previous_end = 0.0
    for item in items:
        if item["start_seconds"] < previous_end:
            raise SystemExit(f"overlapping motion typography at {item['start_seconds']}")
        previous_end = item["end_seconds"]
    return items


def build_edit_plan(timeline, total_seconds):
    sources = []
    operations = []
    for entry in timeline:
        for index, shot in enumerate(entry["shots"]):
            operation_id = f"{entry['block']['id']}_{index + 1:02d}"
            if shot["kind"] == "card":
                source = f"output/canal-dev-01/screens/{shot['ref']}.png"
                operations.append(
                    {
                        "operation_id": operation_id,
                        "kind": "image_clip",
                        "source": source,
                        "start_seconds": None,
                        "end_seconds": None,
                        "parameters": {
                            "duration_seconds": shot["seconds"],
                            "fit": "contain",
                            "motion": shot["motion"],
                        },
                    }
                )
            else:
                source = SOURCES[shot["ref"]]
                operations.append(
                    {
                        "operation_id": operation_id,
                        "kind": "sequence_clip",
                        "source": source,
                        "start_seconds": snap(shot["start_in_source"]),
                        "end_seconds": snap(
                            shot["start_in_source"] + shot["seconds"]
                        ),
                        "parameters": {"fit": "cover"},
                    }
                )
            if source not in sources:
                sources.append(source)

    operations.append(
        {
            "operation_id": "visual_direction",
            "kind": "visual_direction",
            "source": None,
            "start_seconds": None,
            "end_seconds": None,
            "parameters": VISUAL_DIRECTION,
        }
    )
    operations.append(
        {
            "operation_id": "motion_typography",
            "kind": "motion_typography",
            "source": None,
            "start_seconds": None,
            "end_seconds": None,
            "parameters": {
                "items": build_type_items(timeline),
                "style": TYPOGRAPHY_STYLE,
            },
        }
    )
    operations.append(
        {
            "operation_id": "fade",
            "kind": "fade",
            "source": None,
            "start_seconds": None,
            "end_seconds": None,
            "parameters": {"from_black_seconds": 0.4, "to_black_seconds": 1.2},
        }
    )
    return {
        "schema_version": 1,
        "plan_id": "canal-dev-01-edit-plan",
        "brief_id": "canal-dev-01-brief",
        "sources": sources,
        "output_path": str(OUTPUT_PATH),
        "target_format": {"width": 1920, "height": 1080, "fit": "cover"},
        "operations": operations,
    }


def write_script(timeline, total_seconds):
    lines = [
        f"# {TITLE}",
        "",
        "Roteiro narrativo de referência para `projects/canal_dev_01`.",
        "",
        "**Este render não tem áudio.** O texto abaixo é o que será gravado depois.",
        "As durações são provisórias: cada bloco foi esticado para o tempo que seu",
        f"próprio texto levaria a ser falado em PT-BR a {WORDS_PER_SECOND} palavras",
        f"por segundo, mais {BLOCK_BREATH_SECONDS} s de respiro.",
        "",
        f"- Duração total do corte: **{total_seconds:.2f} s** "
        f"(~{int(total_seconds // 60)} min {int(total_seconds % 60)} s)",
        f"- Blocos: **{len(timeline)}**",
        f"- Shots: **{sum(len(entry['shots']) for entry in timeline)}**",
        "",
        "Ao gravar: leia bloco a bloco. Se a sua leitura ficar mais longa ou mais",
        "curta que a duração provisória, isso é esperado — o próximo ciclo remede",
        "os shots contra o seu WAV.",
        "",
        "---",
        "",
    ]
    for entry in timeline:
        block = entry["block"]
        lines += [
            f"## {block['title']}",
            "",
            f"`{block['id']}` — início **{entry['start']:.2f} s**, duração "
            f"**{entry['seconds']:.2f} s** "
            f"(estimativa de fala {entry['speech_seconds']:.2f} s)",
            "",
            "**Intenção.** " + block["intent"],
            "",
            "**Texto para gravar.**",
            "",
            "> " + block["narration"],
            "",
            "**Visual correspondente.**",
            "",
            "| t | dur | material | origem |",
            "| --- | --- | --- | --- |",
        ]
        for shot in entry["shots"]:
            if shot["kind"] == "card":
                material = f"composição gráfica `{shot['ref']}`"
                origin = "gerada a partir de conteúdo real do repo"
            else:
                material = f"`{SOURCES[shot['ref']]}`"
                origin = (
                    f"{SOURCE_NOTES[shot['ref']]} — em "
                    f"{shot['start_in_source']:.1f} s"
                )
            lines.append(
                f"| {shot['start']:.2f} | {shot['seconds']:.2f} | {material} | {origin} |"
            )
        lines.append("")
        if block["type_events"]:
            lines.append("**Tipografia em tela.** ")
            for event in block["type_events"]:
                words = " / ".join(item["text"] for item in event["blocks"])
                lines.append(f"- `{event['layout']}` — {words}")
            lines.append("")
    (PROJECT_DIR / "script.md").write_text("\n".join(lines), encoding="utf-8")


def write_timeline(timeline, total_seconds):
    real = 0.0
    graphic = 0.0
    lines = [
        "# Timeline — canal_dev_01",
        "",
        f"Corte de **{total_seconds:.2f} s**, 1920x1080, 16:9, **sem faixa de áudio**.",
        "",
        "`material real` = frames de um render que este repositório produziu, ou um",
        "asset que ele resolveu. `composição gráfica` = card renderizado por",
        "`render-screens` a partir de conteúdo real (código, terminal, JSON, número).",
        "",
        "| # | t | dur | bloco | tipo | material |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    index = 0
    for entry in timeline:
        for shot in entry["shots"]:
            index += 1
            if shot["kind"] == "card":
                graphic += shot["seconds"]
                kind = "composição gráfica"
                material = f"`{shot['ref']}.png`"
            else:
                real += shot["seconds"]
                kind = "material real"
                material = (
                    f"`{SOURCES[shot['ref']]}` @ {shot['start_in_source']:.1f} s"
                )
            lines.append(
                f"| {index} | {shot['start']:.2f} | {shot['seconds']:.2f} | "
                f"`{entry['block']['id']}` | {kind} | {material} |"
            )
    lines += [
        "",
        "## Proporção",
        "",
        f"- material real: **{real:.1f} s** ({real / total_seconds * 100:.0f}%)",
        f"- composição gráfica: **{graphic:.1f} s** "
        f"({graphic / total_seconds * 100:.0f}%)",
        "",
        "## Fontes usadas",
        "",
        "| chave | arquivo | o que é |",
        "| --- | --- | --- |",
    ]
    for key, path in SOURCES.items():
        lines.append(f"| `{key}` | `{path}` | {SOURCE_NOTES[key]} |")
    lines.append("")
    (PROJECT_DIR / "timeline.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    PROJECT_DIR.mkdir(parents=True, exist_ok=True)

    referenced = {shot["ref"] for block in BLOCKS for shot in block["shots"]
                  if shot["kind"] == "card"}
    known = {card.card_id for card in CARDS}
    missing = referenced - known
    if missing:
        raise SystemExit("blocks reference unknown cards: " + ", ".join(sorted(missing)))
    unused = known - referenced
    if unused:
        raise SystemExit("deck carries unused cards: " + ", ".join(sorted(unused)))

    theme = ScreenTheme()
    for card_spec in CARDS:
        layout_card(card_spec, theme)  # refuse a deck that would overflow the frame

    for key, relative in SOURCES.items():
        if not (REPOSITORY_ROOT / relative).is_file():
            raise SystemExit(f"missing source for {key}: {relative}")

    # Clip in-points are authored against the source; the timeline scale only
    # moves the out-point, never the in-point.
    for block in BLOCKS:
        for shot in block["shots"]:
            if shot["kind"] == "clip":
                shot["start_in_source"] = shot["start"]

    timeline, total = build_timeline()
    deck = {
        "schema_version": 1,
        "deck_id": "canal-dev-01",
        "cards": [card_spec.to_dict() for card_spec in CARDS],
    }
    (PROJECT_DIR / "screens.json").write_text(
        json.dumps(deck, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    plan = build_edit_plan(timeline, total)
    (PROJECT_DIR / "edit-plan.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    write_script(timeline, total)
    write_timeline(timeline, total)

    print(f"blocks     {len(timeline)}")
    print(f"shots      {sum(len(entry['shots']) for entry in timeline)}")
    print(f"cards      {len(CARDS)}")
    print(f"type       {len(plan['operations'][-2]['parameters']['items'])} events")
    print(f"duration   {total:.2f} s")
    print(f"plan       {PROJECT_DIR / 'edit-plan.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
