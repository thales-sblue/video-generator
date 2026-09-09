"""Build the visual-curation set for canal_dev_01, before any render.

The cut this reviews already exists as a plan: ``projects/canal_dev_01/edit-plan.json``
carries 80 shots. This script does four things over that plan and stops.

1. **Audit.** Every shot that quotes one of this repository's own renders is
   traced back through *that render's* plan to the asset actually on screen, and
   through the resolver to the query that fetched it. That is how the Brazilian
   flag in the opening frame was found: shot 1 quotes ``v0`` at 44.0 s, which is
   ``asset_scene_05_01.mp4``, which Pexels returned for the Portuguese query
   ``mao apagando setas num quadro branco...``. A locale leaked into a picture.

2. **Sequence map.** Consecutive shots that share one visual decision are
   grouped, so the author is asked 39 questions instead of 80.

3. **Candidates.** Only the subjective sequences get options, up to three, each
   one built from material already on this disk. Every candidate goes through
   :func:`video_generator.domain.curation.editorial_gate`; what the gate throws
   out is recorded next to the sequence rather than hidden.

4. **Review sheet.** One self-contained ``review.html`` with every option side
   by side, and a ``visual-curation-set.json`` the ``visual-lock`` command turns
   into a lock once the author has answered.

Nothing here renders the video, downloads anything or touches ``inputs/``.

Usage (from the repository root, with PYTHONPATH=src):

    python scripts/build-canal-dev-01-curation.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from video_generator.curation import (  # noqa: E402
    write_curation_set,
    write_review_sheet,
)
from video_generator.domain.curation import (  # noqa: E402
    CurationAsset,
    CurationDecision,
    CurationOption,
    CurationSet,
    editorial_gate,
    group_consecutive,
)
from video_generator.tooling import resolve_media_tool  # noqa: E402

PROJECT_DIR = REPOSITORY_ROOT / "projects" / "canal_dev_01"
OUT_DIR = REPOSITORY_ROOT / "output" / "canal-dev-01" / "visual-curation"
PREVIEW_DIR = OUT_DIR / "previews"
SCREENS_DIR = REPOSITORY_ROOT / "output" / "canal-dev-01" / "screens"

TITLE = (
    "Tentei automatizar vídeos para YouTube com IA — gerar o vídeo foi a parte fácil"
)

# The renders this cut quotes, and the plan that says what is on screen at any
# second of each of them. Tracing a quoted second back to its asset is the whole
# audit: a frame of our own render is still a picture somebody chose once.
SOURCE_KEY = {
    "output/video_final.backup-retention-v0.mp4": "v0",
    "output/video_final.mp4": "v2",
    "output/visual-direction-v1/final.mp4": "vd",
    "output/editorial-translation-v1/final.mp4": "tr",
    "output/motion-typography-v1/final.mp4": "mt",
    "output/remotion-spike/spike.mp4": "spike",
    "output/visual-direction-v1/resolved/files/asset_scene_15_02.mp4": "lupa_asset",
}
SOURCE_PATH = {key: path for path, key in SOURCE_KEY.items()}
SOURCE_NOTE = {
    "v0": "render de retenção v0 — 270 s, 68 shots",
    "v2": "render de retenção v2 — 209 s, legendas alinhadas ao áudio",
    "vd": "Visual Direction v1 — o corte com as 6 lupas",
    "tr": "Editorial Visual Translation v1 — queries reescritas",
    "mt": "Editorial Motion Typography v1 — tipografia no lugar da legenda",
    "spike": "spike do Remotion",
    "lupa_asset": "o asset que a query da lupa devolveu",
}
SOURCE_PLAN = {
    "v0": "output/edit-plan-desumanizando-postable.json",
    "v2": "projects/desumanizando_01/edit-plan-v3.json",
    "vd": "output/visual-direction-v1/edit-plan.json",
    "tr": "output/editorial-translation-v1/edit-plan.json",
    "mt": "output/motion-typography-v1/edit-plan.json",
}
SOURCE_RESOLUTION = {
    "v0": "output/resolved-assets/asset-resolution-plan.json",
    "v2": "output/resolved-v3/asset-resolution-plan.json",
    "vd": "output/visual-direction-v1/resolved/asset-resolution-plan.json",
    "tr": "output/editorial-translation-v1/resolved/asset-resolution-plan.json",
    "mt": "output/editorial-translation-v1/resolved/asset-resolution-plan.json",
}

# A screen card quoting real repository content is evidence; a card whose whole
# body is a sentence I wrote is a graphic composition, and that is taste.
CARD_MATERIAL = {
    "code": "project_material",
    "terminal": "project_material",
    "json": "project_material",
    "chain": "project_diagram",
    "compare": "project_diagram",
    "stat": "project_diagram",
    "list": "project_diagram",
    "statement": "graphic_composition",
}

BLOCK_TITLES = {
    "01_hook": "Hook",
    "02_ideia": "A ideia inicial",
    "03_funcionava": "Tecnicamente funcionava",
    "04_slideshow": "O primeiro problema: parecia slideshow",
    "05_cortes": "Mais cortes não resolveram",
    "06_imagens": "O resolver e a nota alta",
    "07_lupa": "A lupa",
    "08_pergunta": "A pergunta mudou",
    "09_texto": "Legenda não é tipografia",
    "10_arquitetura": "A decisão arquitetural",
    "11_aberto": "O que continua aberto",
    "12_final": "Fecho",
}

# Vocabulary the narration itself uses, per block. The metadata guard only lets
# a flag, a map or a country through when the story is explicitly about one, and
# none of these blocks is.
NARRATIVE_TERMS = {
    "04_slideshow": ("asset", "ritmo", "corte", "legenda", "repeticao"),
    "06_imagens": ("resolver", "query", "candidato", "semantica", "nota"),
    "07_lupa": ("lupa", "documento", "xadrez", "espelho"),
}


def clip(key: str, start: float, seconds: float) -> dict:
    return {"kind": "clip", "key": key, "start": start, "seconds": seconds}


def card(card_id: str) -> dict:
    return {"kind": "card", "card_id": card_id}


# ---------------------------------------------------------------------------
# The authored decisions. Everything not named here is evidence and is used
# directly; these are the sequences where more than one answer is defensible.
# The key is "<block_id>#<first shot number>".
# ---------------------------------------------------------------------------

DECISIONS = {
    "01_hook#1": {
        "reason": "frame_choice",
        "recommend": "B",
        "notes": (
            "Duas coisas para olhar antes de escolher. (1) O corte atual abre com "
            "a bandeira do Brasil: o primeiro frame do vídeo é o asset que o Pexels "
            "devolveu para uma query escrita em português. (2) O terceiro shot de B "
            "mostra a interface do DeepSeek num laptop — é marca de terceiro em "
            "tela nos primeiros 9 s; se isso incomodar, peça regenerar."
        ),
        "why": (
            "A narração diz que o resultado parecia exatamente o que era: um vídeo "
            "feito automaticamente. B é literalmente essa frase — as três placas de "
            "IA genérica que o próprio corte v0 escolheu sozinho — e ainda prepara "
            "os blocos 06 e 08. A e C são bonitas, mas explicam menos."
        ),
        "options": [
            {
                "id": "A",
                "concept": (
                    "três imagens plausíveis e arbitrárias que o corte automático "
                    "escolheu: um diagrama de quadro branco, uma lupa sobre papel, "
                    "alguém rolando uma loja de apps"
                ),
                "why": (
                    "abre neutro, sem clichê e sem símbolo nacional; o espectador "
                    "julga o ritmo antes de julgar as imagens"
                ),
                "shots": [clip("v0", 32.6, 3.53), clip("v0", 39.6, 2.9), clip("v0", 105.1, 2.5)],
            },
            {
                "id": "B",
                "concept": (
                    "as três placas de inteligência artificial genérica que o v0 "
                    "escolheu sozinho, uma atrás da outra"
                ),
                "why": (
                    "é o defeito na primeira imagem do vídeo, dito sem narração: "
                    "o corte automático ilustrou IA com stock de IA"
                ),
                "shots": [clip("v0", 139.4, 3.53), clip("v0", 172.2, 2.9), clip("v0", 235.9, 2.5)],
            },
            {
                "id": "C",
                "concept": (
                    "material sobre o próprio ofício: um processo de edição de "
                    "vídeo, alguém no computador à noite, alguém editando vídeo"
                ),
                "why": (
                    "on-topic e mais bonito, mas descreve o assunto em vez de "
                    "mostrar o problema — vira abertura de vídeo institucional"
                ),
                "shots": [clip("v0", 192.9, 3.53), clip("v0", 218.8, 2.9), clip("mt", 83.1, 2.5)],
            },
        ],
    },
    "01_hook#6": {
        "reason": "frame_choice",
        "recommend": "A",
        "why": (
            "Estes dois shots carregam a tipografia GERAR O VÍDEO / FOI A PARTE "
            "FÁCIL, então a imagem precisa ser escura e sem detalhe no centro. A "
            "entrega isso e troca o programador de moletom e o corredor de "
            "escritório — os dois clichês de banco de imagens do corte atual."
        ),
        "options": [
            {
                "id": "A",
                "concept": "alguém escrevendo num quadro negro, depois alguém editando vídeo tarde da noite",
                "why": "fundo escuro e limpo para a tipografia, e conta 'mesmo roteiro, seis versões depois'",
                "shots": [clip("mt", 3.8, 3.73), clip("mt", 83.1, 3.53)],
            },
            {
                "id": "B",
                "concept": "alguém ainda trabalhando cansada, depois uma montagem stop-motion numa rua",
                "why": "mais movimento, mas o stop-motion disputa atenção com a tipografia",
                "shots": [clip("mt", 34.8, 3.73), clip("mt", 62.7, 3.53)],
            },
            {
                "id": "C",
                "concept": "escrita num quarto mal iluminado, depois uma conversa noturna sob luz de cidade",
                "why": "o tom mais próximo do canal, mas o segundo shot tem rosto e rosto rouba a legenda",
                "shots": [clip("mt", 19.3, 3.73), clip("mt", 90.7, 3.53)],
            },
        ],
    },
    "02_ideia#9": {
        "reason": "frame_choice",
        "recommend": "B",
        "why": (
            "O bloco é sobre um plano escrito antes da primeira linha de código. "
            "Alguém escrevendo num quadro é isso; folhear um livro (o shot atual) "
            "é decoração no meio de seis cards de arquitetura."
        ),
        "options": [
            {
                "id": "A",
                "concept": "um diagrama de caixas e setas desenhado à mão num quadro branco",
                "why": "o mesmo pipeline do card ao lado, uma vez à mão e uma vez pela máquina",
                "shots": [clip("v0", 32.6, 3.27)],
            },
            {
                "id": "B",
                "concept": "alguém escrevendo num quadro negro",
                "why": "regra escrita antes do código, sem repetir o diagrama que o card já mostra",
                "shots": [clip("mt", 3.8, 3.27)],
            },
            {
                "id": "C",
                "concept": "peças de quebra-cabeça sobre fundo amarelo",
                "why": "a metáfora de montagem é legível, mas é o clichê que o vídeo critica",
                "shots": [clip("v0", 122.7, 3.27)],
            },
        ],
    },
    "03_funcionava#18": {
        "reason": "multiple_plausible_solutions",
        "recommend": "A",
        "why": (
            "Prioridade 1: material real do projeto. O bloco é uma sequência de "
            "evidências (testes, plano, manifest); cortar para uma silhueta de "
            "banco de imagens no meio dela enfraquece a evidência. A segura o card "
            "anterior mais 2.9 s e não gasta imagem nenhuma."
        ),
        "options": [
            {
                "id": "A",
                "concept": "segurar o card do shot-plan em JSON por mais 2.9 s, sem corte para foto",
                "why": "o bloco é evidência; o respiro pode ser dentro da própria evidência",
                "shots": [card("worked_shot")],
            },
            {
                "id": "B",
                "concept": "uma lupa sobre papel impresso",
                "why": "respiro visual com sentido de inspeção, coerente com validar a cadeia",
                "shots": [clip("v0", 39.6, 2.9)],
            },
            {
                "id": "C",
                "concept": "um caderno aberto com caneta sob luz natural",
                "why": "neutro e calmo, mas não diz nada que o bloco esteja dizendo",
                "shots": [clip("mt", 52.7, 2.9)],
            },
        ],
    },
    "03_funcionava#21": {
        "reason": "multiple_plausible_solutions",
        "recommend": "A",
        "why": (
            "Mesmo argumento do respiro anterior: o card `worked_validate` termina "
            "com exit zero, que é o fecho do bloco. Sair dele para uma foto de "
            "alguém segurando um caderno joga fora a única frase que importa."
        ),
        "options": [
            {
                "id": "A",
                "concept": "segurar o card de validate-project por mais 3.3 s no exit zero",
                "why": "o fecho do bloco é o exit zero; ele merece o tempo de tela",
                "shots": [card("worked_validate")],
            },
            {
                "id": "B",
                "concept": "uma lupa sobre papel impresso, mais fechada",
                "why": "repete o motivo de inspeção do respiro anterior e cria rima",
                "shots": [clip("v0", 129.9, 3.33)],
            },
            {
                "id": "C",
                "concept": "páginas de um livro sendo viradas",
                "why": "movimento suave, mas é o mesmo tipo de decoração que o vídeo critica",
                "shots": [clip("mt", 49.3, 3.33)],
            },
        ],
    },
    "04_slideshow#22": {
        "reason": "frame_choice",
        "recommend": "A",
        "notes": (
            "Todo frame citado do v0 e do v2 traz a legenda queimada daquele render. "
            "Aqui isso ajuda: as três ocorrências do mesmo asset aparecem com "
            "legendas diferentes, o que prova que é o mesmo plano em três momentos "
            "do roteiro. Em outras sequências a legenda antiga pode distrair."
        ),
        "why": (
            "A narração afirma um fato verificável: o mesmo asset três vezes, com "
            "45 s de distância. No v0, `asset_scene_16_01` aparece em 133.9 s, "
            "178.9 s e 222.5 s — exatamente isso. A mantém a tripla que prova a "
            "frase e troca só o par final, que hoje é a bandeira do Brasil duas "
            "vezes, por uma reutilização real e neutra (a lupa, 2× em 90 s)."
        ),
        "options": [
            {
                "id": "A",
                "concept": (
                    "a tripla que a narração descreve — o mesmo asset em 133.9 s, "
                    "178.9 s e 222.5 s — seguida de uma segunda reutilização real, "
                    "a lupa sobre papel em 39.6 s e 129.9 s"
                ),
                "why": "cada corte é uma reutilização que existe de verdade no v0; a frase fica literal",
                "shots": [
                    clip("v0", 135.0, 3.83),
                    clip("v0", 180.0, 3.5),
                    clip("v0", 223.5, 3.5),
                    clip("v0", 39.6, 2.17),
                    clip("v0", 129.9, 2.17),
                ],
            },
            {
                "id": "B",
                "concept": (
                    "a mesma tripla, seguida do jornal reutilizado em 84.7 s e 95.4 s"
                ),
                "why": "outra reutilização verdadeira; o jornal é mais neutro, mas menos legível em movimento",
                "shots": [
                    clip("v0", 135.0, 3.83),
                    clip("v0", 180.0, 3.5),
                    clip("v0", 223.5, 3.5),
                    clip("v0", 84.7, 2.17),
                    clip("v0", 95.4, 2.17),
                ],
            },
            {
                "id": "C",
                "concept": (
                    "trocar a tripla pelo jornal, que o v0 usa três vezes (59.7 s, "
                    "84.7 s, 95.4 s), e fechar com o quadro branco reutilizado"
                ),
                "why": "o assunto some completamente e sobra só a repetição — mais limpo, menos concreto",
                "shots": [
                    clip("v0", 59.7, 3.83),
                    clip("v0", 84.7, 3.5),
                    clip("v0", 95.4, 3.5),
                    clip("v0", 32.6, 2.17),
                    clip("v0", 152.7, 2.17),
                ],
            },
        ],
    },
    "04_slideshow#28": {
        "reason": "graphic_composition",
        "recommend": "A",
        "why": (
            "O veredicto do bloco é uma frase minha, não um dado do repositório. "
            "Em card ela respira; como tipografia sobre o último shot ela compete "
            "com a repetição que acabou de ser demonstrada."
        ),
        "options": [
            {
                "id": "A",
                "concept": "card de declaração: NADA DISSO ESTÁ QUEBRADO / TUDO ISSO ESTÁ ERRADO",
                "why": "corta o ritmo de propósito e deixa a frase sozinha",
                "shots": [card("slideshow_verdict")],
            },
            {
                "id": "B",
                "concept": "a mesma frase como tipografia sobre o último shot da repetição",
                "why": "não para o vídeo, mas coloca dois sistemas tipográficos no mesmo quadro",
                "shots": [clip("v0", 223.5, 3.0)],
            },
        ],
    },
    "05_cortes#31": {
        "reason": "frame_choice",
        "recommend": "A",
        "why": (
            "A frase é 'cinquenta e nove imagens genéricas continuam sendo imagens "
            "genéricas'. A escolhe três placas do v2 que são exatamente isso — "
            "papel, estante, jornais — em cortes rápidos. O corte atual usa uma "
            "formatura e um teste de gravidez, que o espectador tenta interpretar."
        ),
        "options": [
            {
                "id": "A",
                "concept": "três placas genéricas do v2 em corte rápido: papel em branco, estante de livros, jornais",
                "why": "genéricas de propósito; a velocidade é o argumento, não o assunto",
                "shots": [clip("v2", 27.8, 3.37), clip("v2", 39.9, 3.1), clip("v2", 46.7, 3.1)],
            },
            {
                "id": "B",
                "concept": "três planos de papel do v2: fragmentos num quadro, papel pautado, alguém olhando impressões",
                "why": "família visual única, corte quase invisível — bom ritmo, argumento mais fraco",
                "shots": [clip("v2", 94.2, 3.37), clip("v2", 113.9, 3.1), clip("v2", 125.9, 3.1)],
            },
            {
                "id": "C",
                "concept": "os três shots atuais: formatura, teste de gravidez duas vezes",
                "why": "mantém o corte que existe, mas o segundo asset repete e o espectador procura significado",
                "shots": [clip("v2", 96.0, 3.37), clip("v2", 120.5, 3.1), clip("v2", 173.0, 3.1)],
            },
        ],
    },
    "05_cortes#34": {
        "reason": "graphic_composition",
        "recommend": "A",
        "why": (
            "Mesmo caso do veredicto anterior: é opinião, e opinião fica melhor "
            "isolada num card do que sobreposta a uma imagem genérica."
        ),
        "options": [
            {
                "id": "A",
                "concept": "card de declaração fechando o bloco de ritmo",
                "why": "a frase precisa de silêncio visual para não virar legenda",
                "shots": [card("cuts_verdict")],
            },
            {
                "id": "B",
                "concept": "a mesma frase como tipografia sobre o card de contagem de shots",
                "why": "junta número e veredicto num quadro só, ao custo de densidade",
                "shots": [card("cuts_count")],
            },
        ],
    },
    "06_imagens#39": {
        "reason": "multiple_plausible_solutions",
        "recommend": "B",
        "why": (
            "O bloco explica que o candidato passava em tipo, orientação e "
            "resolução e zerava em sobreposição semântica. B mostra duas placas de "
            "IA seguidas: dois assets tecnicamente perfeitos e editorialmente "
            "vazios, um atrás do outro. É a frase inteira em 6.4 s."
        ),
        "options": [
            {
                "id": "A",
                "concept": "a placa de IA que o v0 escolheu, depois um jornal em close",
                "why": "é o corte atual; funciona, mas o jornal dilui o exemplo",
                "shots": [clip("v0", 202.0, 3.33), clip("v0", 60.5, 3.1)],
            },
            {
                "id": "B",
                "concept": "duas placas de inteligência artificial genérica em sequência",
                "why": "o defeito duas vezes seguidas: nota alta em tudo, zero no que importa",
                "shots": [clip("v0", 172.2, 3.33), clip("v0", 139.4, 3.1)],
            },
            {
                "id": "C",
                "concept": "a placa de IA, depois o quadro branco com o diagrama vermelho",
                "why": "contrapõe o asset vazio a um asset que de fato ilustra — mas explica demais",
                "shots": [clip("v0", 202.0, 3.33), clip("v0", 152.7, 3.1)],
            },
        ],
    },
    "07_lupa#48": {
        "reason": "graphic_composition",
        "recommend": "A",
        "why": (
            "'O algoritmo não errou. Ele fez exatamente o que eu pedi.' é a frase "
            "central do vídeo. Ela não divide quadro com nada."
        ),
        "options": [
            {
                "id": "A",
                "concept": "card de declaração isolado, depois das seis lupas",
                "why": "a frase é o ponto de virada; qualquer imagem atrás dela compete",
                "shots": [card("lupa_verdict")],
            },
            {
                "id": "B",
                "concept": "a mesma frase como tipografia sobre a contagem de lupas",
                "why": "mantém o número na tela, mas transforma a virada em nota de rodapé",
                "shots": [card("lupa_count")],
            },
        ],
    },
    "08_pergunta#54": {
        "reason": "frame_choice",
        "recommend": "A",
        "why": (
            "O bloco é sobre trocar a pergunta, e os três shots são o depois: o "
            "corte com as queries reescritas. A ordem atual (frustração → corredor "
            "vazio → prédio) termina num prédio de apartamentos que não diz nada. "
            "A troca o terceiro por um homem lendo um documento enquanto anda, que "
            "é o conceito 'ler antes de buscar'."
        ),
        "options": [
            {
                "id": "A",
                "concept": "frustração com o papel amassado, corredor escuro, alguém lendo um documento enquanto anda",
                "why": "os dois primeiros são o problema, o terceiro é a mudança de método",
                "shots": [clip("tr", 96.0, 3.3), clip("tr", 40.5, 3.07), clip("tr", 162.2, 3.3)],
            },
            {
                "id": "B",
                "concept": "os três shots atuais: papel amassado, corredor escuro, prédio de apartamentos",
                "why": "mantém o corte existente; o terceiro shot é o elo fraco",
                "shots": [clip("tr", 96.0, 3.3), clip("tr", 40.5, 3.07), clip("tr", 120.5, 3.3)],
            },
            {
                "id": "C",
                "concept": "papel amassado, alguém consultando um fichário de biblioteca, cabos num patch panel",
                "why": "'procurar melhor' fica explícito, mas o patch panel é infraestrutura, não método",
                "shots": [clip("tr", 96.0, 3.3), clip("tr", 110.2, 3.07), clip("tr", 159.2, 3.3)],
            },
        ],
    },
    "11_aberto#72": {
        "reason": "frame_choice",
        "recommend": "B",
        "why": (
            "A narração nomeia o defeito: 'ainda tem asset com cara de banco de "
            "imagens'. B mostra três desses assets, do próprio projeto, enquanto a "
            "frase é dita. É a única sequência do vídeo em que o clichê é o "
            "conteúdo, e ele é honesto porque é autocrítica."
        ),
        "options": [
            {
                "id": "A",
                "concept": "os quatro shots atuais: corredor escuro, folhear livro, caderno na mesa, papel amassado",
                "why": "tom certo, mas ilustra o arrependimento em vez de mostrar o defeito",
                "shots": [
                    clip("mt", 40.0, 3.23),
                    clip("v0", 246.5, 3.0),
                    clip("v2", 60.0, 3.0),
                    clip("mt", 96.0, 3.0),
                ],
            },
            {
                "id": "B",
                "concept": "três assets com cara de banco de imagens que este projeto usou, e o papel amassado no fim",
                "why": "a frase e a imagem dizem a mesma coisa ao mesmo tempo",
                "shots": [
                    clip("v0", 139.4, 3.23),
                    clip("v0", 64.6, 3.0),
                    clip("v2", 75.8, 3.0),
                    clip("mt", 96.0, 3.0),
                ],
            },
            {
                "id": "C",
                "concept": "corredor escuro, placa de IA, alguém digitando num laptop, papel amassado",
                "why": "alterna tom e defeito; fica ambíguo sobre o que está sendo criticado",
                "shots": [
                    clip("mt", 40.0, 3.23),
                    clip("v0", 172.2, 3.0),
                    clip("v0", 249.9, 3.0),
                    clip("mt", 96.0, 3.0),
                ],
            },
        ],
    },
    "12_final#77": {
        "reason": "graphic_composition",
        "recommend": "A",
        "why": (
            "Os dois cards são a tese do canal em duas frases. Como tipografia "
            "sobre imagem elas viram legenda de encerramento; como cards elas são "
            "o encerramento."
        ),
        "options": [
            {
                "id": "A",
                "concept": "os dois cards de declaração, um depois do outro",
                "why": "duas frases, dois quadros, nenhuma imagem competindo",
                "shots": [card("end_one"), card("end_two")],
            },
            {
                "id": "B",
                "concept": "as duas frases como tipografia sobre o card final do repositório",
                "why": "termina no endereço do repositório, mas empilha três coisas num quadro",
                "shots": [card("end_card")],
            },
        ],
    },
    "12_final#79": {
        "reason": "frame_choice",
        "recommend": "A",
        "why": (
            "O último plano antes do card de fecho é 'editar bem continua sendo um "
            "problema de decisão'. Alguém editando vídeo tarde da noite é essa "
            "frase. O shot atual é uma pessoa limpando uma mesa com um lenço."
        ),
        "options": [
            {
                "id": "A",
                "concept": "alguém editando vídeo no computador, tarde da noite",
                "why": "o ofício do vídeo, no último plano de um vídeo sobre o ofício",
                "shots": [clip("mt", 83.1, 3.2)],
            },
            {
                "id": "B",
                "concept": "um processo de edição de vídeo, em plano fechado",
                "why": "mais literal e mais frio; funciona se o fecho tiver de ser técnico",
                "shots": [clip("v0", 192.9, 3.2)],
            },
            {
                "id": "C",
                "concept": "alguém escrevendo num quarto mal iluminado, com papéis",
                "why": "rima com a abertura do bloco 02 e fecha o círculo — mas fala de escrever, não de editar",
                "shots": [clip("mt", 19.3, 3.2)],
            },
        ],
    },
}


# ---------------------------------------------------------------------------
# Reading what is actually on screen
# ---------------------------------------------------------------------------


def load_json(relative: str):
    path = REPOSITORY_ROOT / relative
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def source_track(relative: str):
    """(start, end, operation_id, source) for every visible shot of a render."""

    plan = load_json(relative)
    if plan is None:
        return []
    clock = 0.0
    rows = []
    for operation in plan["operations"]:
        if operation["kind"] == "image_clip":
            seconds = operation["parameters"]["duration_seconds"]
        elif operation["kind"] == "sequence_clip":
            seconds = (operation["end_seconds"] or 0.0) - (operation["start_seconds"] or 0.0)
        else:
            continue
        rows.append((clock, clock + seconds, operation["operation_id"], operation["source"]))
        clock += seconds
    return rows


def resolution_index(relative: str):
    plan = load_json(relative)
    if plan is None:
        return {}
    index = {}
    for entry in plan.get("resolved", []):
        name = Path(str(entry.get("local_path", ""))).name
        index[name] = {
            "query": (entry.get("requirement") or {}).get("query") or "",
            "purpose": (entry.get("requirement") or {}).get("purpose") or "",
            "url": (entry.get("provenance") or {}).get("source_url") or "",
        }
    return index


TRACKS = {key: source_track(path) for key, path in SOURCE_PLAN.items()}
RESOLUTIONS = {key: resolution_index(path) for key, path in SOURCE_RESOLUTION.items()}


def underlying_asset(key: str, second: float):
    """What the viewer is really looking at, and the query that fetched it."""

    for start, end, _operation_id, source in TRACKS.get(key, []):
        if start <= second < end:
            name = Path(str(source).replace("\\", "/")).name
            info = RESOLUTIONS.get(key, {}).get(name, {})
            return {"file": name, **info}
    return {}


# ---------------------------------------------------------------------------
# Previews
# ---------------------------------------------------------------------------

FFMPEG = resolve_media_tool("ffmpeg")
PREVIEW_WIDTH = 440


def run_ffmpeg(arguments: list[str]) -> None:
    if FFMPEG is None:
        raise SystemExit("ffmpeg is not available; cannot build previews")
    completed = subprocess.run(
        [FFMPEG, "-nostdin", "-hide_banner", "-loglevel", "error", *arguments],
        cwd=str(REPOSITORY_ROOT),
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise SystemExit(f"ffmpeg failed: {completed.stderr.strip()[:400]}")


def frame_preview(source: Path, second: float, target: Path) -> None:
    if target.is_file():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(
        [
            "-y",
            "-ss",
            f"{second:.3f}",
            "-i",
            str(source),
            "-frames:v",
            "1",
            "-vf",
            f"scale={PREVIEW_WIDTH}:-2",
            "-q:v",
            "4",
            str(target),
        ]
    )


def still_preview(source: Path, target: Path) -> None:
    if target.is_file():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(
        ["-y", "-i", str(source), "-vf", f"scale={PREVIEW_WIDTH}:-2", "-q:v", "4", str(target)]
    )


def filmstrip(parts: list[Path], target: Path) -> None:
    """Lay up to five previews side by side, so one image is one option."""

    if target.is_file():
        return
    if len(parts) == 1:
        target.write_bytes(parts[0].read_bytes())
        return
    inputs: list[str] = []
    for part in parts:
        inputs.extend(["-i", str(part)])
    run_ffmpeg(
        ["-y", *inputs, "-filter_complex", f"hstack=inputs={len(parts)}", "-q:v", "4", str(target)]
    )


def option_preview(sequence_id: str, option_id: str, shots: list[dict]) -> str:
    parts: list[Path] = []
    for index, shot in enumerate(shots[:5]):
        part = PREVIEW_DIR / "parts" / f"{sequence_id}_{option_id}_{index}.jpg"
        if shot["kind"] == "card":
            still_preview(SCREENS_DIR / f"{shot['card_id']}.png", part)
        else:
            source = REPOSITORY_ROOT / SOURCE_PATH[shot["key"]]
            offset = min(0.5, max(shot["seconds"] / 2.0, 0.1))
            frame_preview(source, shot["start"] + offset, part)
        parts.append(part)
    strip = PREVIEW_DIR / f"{sequence_id}_{option_id}.jpg"
    filmstrip(parts, strip)
    return f"previews/{strip.name}"


# ---------------------------------------------------------------------------
# Building the set
# ---------------------------------------------------------------------------


def shot_assets(shots: list[dict]) -> tuple[CurationAsset, ...]:
    assets = []
    for shot in shots:
        if shot["kind"] == "card":
            assets.append(
                CurationAsset(
                    path=f"output/canal-dev-01/screens/{shot['card_id']}.png",
                    role="card",
                    origin="composição gráfica gerada de conteúdo real do repositório",
                )
            )
        else:
            key = shot["key"]
            found = underlying_asset(key, shot["start"])
            origin = SOURCE_NOTE[key]
            if found.get("url"):
                origin += f" — em {shot['start']:.1f} s, sobre {found['file']} ({found['url']})"
            elif found.get("file"):
                origin += f" — em {shot['start']:.1f} s, sobre {found['file']}"
            else:
                origin += f" — em {shot['start']:.1f} s"
            assets.append(
                CurationAsset(
                    path=SOURCE_PATH[key],
                    role="clip",
                    start_seconds=shot["start"],
                    seconds=shot["seconds"],
                    origin=origin,
                )
            )
    return tuple(assets)


def option_material_kind(shots: list[dict], card_kinds: dict[str, str]) -> str:
    kinds = set()
    for shot in shots:
        if shot["kind"] == "card":
            kinds.add(CARD_MATERIAL[card_kinds[shot["card_id"]]])
        else:
            kinds.add("project_material")
    # The weakest material in the option is what the option is.
    order = ["external_asset", "graphic_composition", "motion_typography", "project_diagram", "project_material"]
    for candidate in order:
        if candidate in kinds:
            return candidate
    return "project_material"


def audit_rejections(shots: list[dict], narrative_terms: tuple[str, ...]):
    """What the current cut is showing that the editorial gate would refuse."""

    rejections = []
    for shot in shots:
        if shot["kind"] != "clip":
            continue
        found = underlying_asset(shot["key"], shot["start"])
        query = found.get("query") or ""
        if not query and not found.get("url"):
            continue
        probe = CurationOption(
            option_id="Z",
            material_kind="external_asset",
            visual_concept=(found.get("url") or found.get("file") or "asset").replace(
                "https://www.pexels.com/", ""
            ),
            justification="candidato herdado do render anterior",
            assets=(CurationAsset(path=SOURCE_PATH[shot["key"]], role="clip"),),
            query=query or None,
        )
        reasons = editorial_gate(probe, narrative_terms=narrative_terms)
        if reasons:
            label = f"{shot['key']}@{shot['start']:.1f}s {found.get('file', '?')}"
            rejections.append((label, reasons))
    return tuple(rejections)


def main() -> int:
    plan = load_json("projects/canal_dev_01/edit-plan.json")
    if plan is None:
        raise SystemExit("run scripts/build-canal-dev-01.py first")
    deck = load_json("projects/canal_dev_01/screens.json")
    card_kinds = {entry["card_id"]: entry["kind"] for entry in deck["cards"]}

    shots = []
    clock = 0.0
    for number, operation in enumerate(
        [op for op in plan["operations"] if op["kind"] in ("image_clip", "sequence_clip")],
        start=1,
    ):
        source = str(operation["source"]).replace("\\", "/")
        block_id = operation["operation_id"].rsplit("_", 1)[0]
        if operation["kind"] == "image_clip":
            seconds = operation["parameters"]["duration_seconds"]
            card_id = Path(source).stem
            material = CARD_MATERIAL[card_kinds[card_id]]
            shot = {
                "number": number,
                "block_id": block_id,
                "kind": "card",
                "card_id": card_id,
                "seconds": seconds,
                "start": clock,
                "material": material,
                "family": "statement" if material == "graphic_composition" else "card",
            }
        else:
            seconds = operation["end_seconds"] - operation["start_seconds"]
            shot = {
                "number": number,
                "block_id": block_id,
                "kind": "clip",
                "key": SOURCE_KEY[source],
                "start_in_source": operation["start_seconds"],
                "seconds": seconds,
                "start": clock,
                "material": "project_material",
                "family": "clip",
            }
        shots.append(shot)
        clock += seconds

    groups = group_consecutive(shots, key=lambda shot: (shot["block_id"], shot["family"]))

    decisions = []
    for index, ((block_id, family), members) in enumerate(groups, start=1):
        sequence_id = f"seq_{index:02d}"
        first = members[0]
        key = f"{block_id}#{first['number']}"
        spec = DECISIONS.get(key)
        shot_indexes = tuple(shot["number"] for shot in members)
        seconds = round(sum(shot["seconds"] for shot in members), 3)
        narrative_terms = NARRATIVE_TERMS.get(block_id, ())
        current = [
            (
                {"kind": "card", "card_id": shot["card_id"]}
                if shot["kind"] == "card"
                else {"kind": "clip", "key": shot["key"], "start": shot["start_in_source"], "seconds": shot["seconds"]}
            )
            for shot in members
        ]
        title = BLOCK_TITLES.get(block_id, block_id)

        if spec is None:
            material = "project_diagram" if family == "card" and any(
                shot["material"] == "project_diagram" for shot in members
            ) else "project_material"
            if family == "card":
                concept = "cards construídos com conteúdo real do repositório"
                justification = (
                    "código, terminal, JSON, manifest e números do próprio projeto: "
                    "é a evidência do que está sendo explicado, não uma escolha de gosto"
                )
            else:
                concept = "trechos de renders que este repositório produziu"
                justification = (
                    "o corte cita o próprio resultado como prova; o segundo exato "
                    "não carrega argumento além de 'foi isto que saiu'"
                )
            option = CurationOption(
                option_id="A",
                material_kind=material,
                visual_concept=concept,
                justification=justification,
                assets=shot_assets(current),
                preview=option_preview(sequence_id, "A", current),
                origin=title,
            )
            decisions.append(
                CurationDecision(
                    sequence_id=sequence_id,
                    block_id=block_id,
                    title=title,
                    shot_indexes=shot_indexes,
                    start_seconds=round(first["start"], 3),
                    seconds=seconds,
                    options=(option,),
                    needs_approval=False,
                    narrative_terms=narrative_terms,
                )
            )
            continue

        options = []
        for entry in spec["options"]:
            option_shots = entry["shots"]
            option = CurationOption(
                option_id=entry["id"],
                material_kind=option_material_kind(option_shots, card_kinds),
                visual_concept=entry["concept"],
                justification=entry["why"],
                assets=shot_assets(option_shots),
                preview=option_preview(sequence_id, entry["id"], option_shots),
                origin=title,
            )
            blocked = editorial_gate(option, narrative_terms=narrative_terms)
            if blocked:
                raise SystemExit(
                    f"{sequence_id} option {entry['id']} fails the editorial gate: "
                    + ", ".join(blocked)
                )
            options.append(option)

        decisions.append(
            CurationDecision(
                sequence_id=sequence_id,
                block_id=block_id,
                title=title,
                shot_indexes=shot_indexes,
                start_seconds=round(first["start"], 3),
                seconds=seconds,
                options=tuple(options),
                needs_approval=True,
                decision_reason=spec["reason"],
                recommended_option_id=spec["recommend"],
                recommendation_reason=spec["why"],
                narrative_terms=narrative_terms,
                notes=spec.get("notes", ""),
                gate_rejections=audit_rejections(current, narrative_terms),
            )
        )

    curation_set = CurationSet(
        set_id="canal_dev_01-visual-curation-v1",
        project_id="canal_dev_01",
        video_title=TITLE,
        decisions=tuple(decisions),
    )

    set_path = write_curation_set(curation_set, PROJECT_DIR / "visual-curation-set.json")
    review_path = write_review_sheet(
        curation_set, OUT_DIR / "review.html", preview_root=OUT_DIR
    )

    summary = curation_set.summary()
    print(f"sequences        {summary['sequences']}")
    print(f"need approval    {summary['needing_approval']}")
    print(f"real material    {summary['real_material_only']}")
    print(f"external assets  {summary['external_assets']}")
    print(f"shots covered    {summary['shots']}")
    print(f"set              {set_path}")
    print(f"review           {review_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
