# Workflows

Workflows coordenam capacidades reutilizáveis a partir de um `EditPlan`. A etapa
editorial anterior pertence ao agente orquestrador, que traduz `VideoRequest` e
`VideoBrief` em um plano persistido. O primeiro workflow implementado executa um
recorte temporal já decidido; ele não toma decisões editoriais.

A prioridade é um único caminho incremental para `dark-video`. Workflows de
creator/talking-head, Shorts derivados de gravações e outros casos não devem ser
construídos antes de `dark-video` v1.

## Scene Planner / Shot Planner (antes do `EditPlan`)

Camada pura e determinística que fica **entre `VideoBrief` e `EditPlan`**. Não
toca FFmpeg, não baixa asset, não chama LLM. Vive em
`src/video_generator/domain/planning.py` (stdlib, dependências apontando para
dentro) e realiza os candidatos `Script` / `Storyboard` / `AssetPlan` do
`AGENTS.md`:

```text
roteiro .txt  ──▶  NarrativeScript
                        │  plan_scenes(script, policy, seed)
                        ▼
                   ScenePlan            (cenas: narração, duração, visual_intent, emphasis_offsets)
                        │  plan_shots(scene_plan, policy, seed, orientation)
                        ▼
          ShotPlan  +  AssetRequirements   (shots de 2–6 s; specs de asset)
                        │  apply_overrides(...)   ← refino editorial do agente
                        ▼
          shot_plan_to_edit_plan(shot_plan, asset_bindings, target_format)
                        ▼
                   EditPlan v1  ──▶  workflow `video-sequence`  ──▶  MP4
```

**Modelo híbrido.** `plan_scenes` / `plan_shots` produzem um rascunho completo:
densidade (dezenas de shots curtos por bloco narrativo), rotação determinística
de `shot_type` e de escala (`wide → medium → close → detail`), redistribuição
*bounded* das durações (soma exata da cena, cada shot em
`[min_shot_seconds, soft_max_shot_seconds]`, nenhum shot absorve o resíduo
sozinho), e `visual_query` / `purpose` *derivados* por extração de palavras-chave
do trecho de narração do shot. O agente orquestrador então sobrescreve os campos
fracos via `apply_overrides`, que recebe os **três** documentos e devolve os três
reconstruídos e re-validados; cada campo editorial carrega `provenance`
(`derived` | `authored`). Reprodutível por `script + policy + seed + overrides`.

**Política (`RhythmPolicy`).** Ritmo e densidade são configuração, não constantes
fixas — ver [VIDEO_LANGUAGE.md](VIDEO_LANGUAGE.md). Fica embutida por valor no
`ScenePlan` e no `ShotPlan`; `--policy` mescla um JSON parcial sobre os defaults.

**Validação local × cross-document.** `__post_init__` só checa invariantes
autocontidas. Regras que dependem de outro contrato ficam em
`ScenePlan.validate_against(script)`, `ShotPlan.validate_against(scene_plan)` e
`AssetRequirements.validate_against(shot_plan)` — os planners as chamam antes de
retornar; a CLI as repete após ler os arquivos.

**Ênfase.** Um `NarrativeBlock.emphasis=True` interior a uma cena vira um
`emphasis_offset` (segundos desde o início da cena); o Shot Planner força uma
fronteira de shot ali e marca aquele shot `beat=True`. Cada offset tem
exatamente um shot `beat` na timeline.

**Converter (`shot_plan_to_edit_plan`).** Função pura, sem I/O. `asset_bindings`
mapeia cada `asset_id` para um path local. Emite exatamente os shapes que o
`video-sequence` já aceita: `image_clip` com `parameters ⊆
{duration_seconds, fit, motion}` (sem `start`/`end`), `sequence_clip` com
`start_seconds`/`end_seconds` e `parameters ⊆ {fit}`. `fit` = `cover` para
`close`/`detail`, `contain` para `on_screen_text`/`document`/`simple_graphic`,
senão herda o `target_format.fit`. Shots com `reuse_of` apontam para o **mesmo
`source`** com `motion`/`fit`/`start` diferentes. O converter **assume que cada
asset de vídeo é longo o bastante** para as janelas cumulativas; validar durações
reais é papel do estágio de aquisição de assets. `EditOperation`, `EditPlan`,
`workflows/sequence.py` e `adapters/ffmpeg.py` ficam intactos.

**CLI `plan-scenes`** (ver `README.md`): lê `--from-text` (parágrafos = blocos)
ou `--script` (NarrativeScript JSON), roda os planners com `--seed`, aplica
`--overrides` opcional, e escreve `scene-plan.json`, `shot-plan.json` e
`asset-requirements.json` em `--out-dir`. `--emit-edit-plan` + `--assets`
converte para um `EditPlan`. Read-only sobre `inputs/`/`assets/`; recusa
sobrescrever sem `--force`.

## Asset Resolver / Asset Provenance (entre `AssetRequirements` e o converter)

Consome `asset-requirements.json` + `shot-plan.json` e produz, para cada
requirement, ou um **`ResolvedAsset`** (arquivo local real + `AssetProvenance`
completa) ou um **`UnresolvedRequirement`** com motivo. Cada `asset_id` aparece
exatamente uma vez.

```text
AssetRequirement -> revisão semântica de reuse -> sanitização da query
  -> providers.search -> rank (score_breakdown inspecionável) -> seleção
  -> acquire (só o escolhido) -> SHA-256 + validação -> AssetProvenance
  -> AssetResolutionPlan + asset-bindings.json
```

- **Domínio puro** (`domain/assets.py`, stdlib): contratos + `AssetScoringPolicy`
  (pesos como configuração), `sanitize_query` (lexical, determinística —
  remove stopwords/termos estruturais/genéricos; poucos termos úteis ⇒
  `needs_editorial_override`), `score_candidate`/`rank_candidates` (match de
  query/purpose/intent, tipo, orientação, resolução, duração de vídeo,
  penalização por repetição e por similaridade com shots adjacentes),
  `review_reuse` (um reuse estruturalmente válido só permanece se o shot
  compartilhar ≥ `reuse_semantic_min_shared_terms` termos de conteúdo com o
  shot âncora; senão vira requirement próprio).
- **Providers** (`adapters/asset_providers.py`, podem tocar disco/rede):
  `LocalAssetProvider` indexa `assets/library/**` + sidecars `<arquivo>.json`
  (100% offline); `PexelsProvider`/`PixabayProvider` usam APIs gratuitas com
  chave em `PEXELS_API_KEY`/`PIXABAY_API_KEY` — sem chave, o provider some do run
  sem quebrar nada. Sem API paga, sem scraping, sem download arbitrário de vídeo,
  sem remoção de watermark.
- **Orquestrador** (`resolve.py`, impuro): `search -> rank -> select -> acquire`;
  baixa só o asset escolhido; calcula SHA-256; valida tipo/dimensão/duração via
  ffprobe quando disponível; nunca sobrescreve um arquivo existente com bytes
  diferentes (stage + compara hash).
- **CLI `resolve-assets`** (ver `README.md`): escreve
  `asset-resolution-plan.json`, `asset-provenance.json`,
  `revised-asset-requirements.json` e `asset-bindings.json` (`{asset_id: path}`,
  alimenta `shot_plan_to_edit_plan` sem tocar no `EditPlan`), além dos arquivos
  adquiridos em `<out-dir>/files/`. `--require-complete` sai com 3 se sobrar
  requirement não resolvido; nunca finge sucesso.

100% dos assets resolvidos têm `AssetProvenance`. Não é obrigatório resolver
100% dos requirements neste estágio se a biblioteca/fontes gratuitas não
cobrirem o plano — e é proibido preencher queries ruins com assets irrelevantes
só para bater 100%.

## `segment-extract`

Esse workflow oferece o menor caminho audiovisual completo disponível:

```text
EditPlan persistido -> preflight -> FFmpeg stream copy -> ffprobe -> RenderManifest
```

O plano deve declarar exatamente um source e uma operação `extract_segment` com
início e fim. `parameters` vazio preserva os streams por cópia rápida. Para um
recorte temporal preciso, `parameters` pode ser `{"mode": "precise"}` e o
`output_path` deve terminar em `.mp4`; esse modo reencoda o primeiro vídeo em
H.264 (`libopenh264`) e o primeiro áudio em AAC. Declarar `mode=copy` é recusado
para manter a representação canônica como objeto vazio. Planos com outros
parâmetros, múltiplas operações, sources extras ou kinds desconhecidos são
recusados antes de qualquer escrita.

O resultado separa execução de aprovação: um artifact que falha na validação
técnica permanece disponível para diagnóstico, mas o relatório é inválido. O
workflow persiste um `RenderManifest` com checksums, ferramentas e falhas
técnicas, mas não compõe timeline e registra a revisão visual/auditiva como não
realizada.

## `video-sequence`

Esse é o primeiro workflow que transforma múltiplas decisões temporais em uma
timeline renderizada:

```text
EditPlan com sequence_clip / image_clip ordenados -> preflight -> FFmpeg concat filter
              + captions opcionais
              + fade de abertura/encerramento opcional
              + music opcional
              + narration opcional   -> ffprobe -> RenderManifest -> MP4
```

O plano deve declarar pelo menos dois segmentos de timeline. Um `sequence_clip`
tem source, início e fim. Um `image_clip` tem source (imagem local) e
`parameters={"duration_seconds": N}` (mais `fit`/`motion` opcionais), sem início
ou fim: a imagem é exibida por `N` segundos (limite de 600 s). Pelo menos um
`sequence_clip` é obrigatório **apenas no modo legado** (sem `target_format`),
onde o canvas vem dos clipes de vídeo; com `target_format` declarado a timeline
pode ser inteiramente de `image_clip`. A ordem das operações é a ordem da
timeline. Todos os sources declarados devem ser usados; parâmetros inesperados e
outputs diferentes de MP4 são recusados.

**Canvas — dois modos.** Sem `target_format` no plano (comportamento legado): os
`sequence_clip` definem o canvas, precisam ter um stream de vídeo e dimensões
iguais entre si, e clipes que não batem são recusados antes de qualquer render;
cada `image_clip` só precisa de um stream de vídeo legível e é escalado para
caber com letter-box (barras pretas) no canvas dos clipes. Por isso o modo
legado exige pelo menos um `sequence_clip`.

Com `target_format` (ver [VIDEO_LANGUAGE.md](VIDEO_LANGUAGE.md)): o canvas é a
resolução de entrega declarada (`width`×`height`), a timeline pode ser
inteiramente de `image_clip` (o canvas não depende dos clipes), sources de
resoluções e proporções diferentes podem compor a mesma timeline, e cada
segmento é normalizado deterministicamente para o canvas. Cada segmento resolve seu `fit`
por `operation.parameters["fit"]` (override) ou, na ausência, por
`target_format.fit`:

- `contain` — cabe o quadro inteiro e preenche o resto com preto
  (letterbox/pillarbox); nada é perdido;
- `cover` — ocupa o canvas inteiro e corta o excedente pelo centro; bordas podem
  ser perdidas.

`sequence_clip` aceita apenas um `fit` opcional; `image_clip` aceita
`duration_seconds`, um `fit` opcional e um `motion` opcional. Declarar `fit` sem
`target_format` é erro de planejamento.

**Movimento (Ken Burns) num `image_clip`.** `motion` anima uma imagem parada com
um `zoompan` determinístico, um de `zoom_in`, `zoom_out`, `pan_left`,
`pan_right`, `pan_up`, `pan_down`. Sem `motion` (padrão) o quadro fica
congelado, byte a byte como antes. O deslocamento é fixo — 12% ao longo do clipe
(zooms cobrem 1,0↔1,12; os pans mantêm 1,12 e varrem a margem que o zoom abre) —
e é função pura de `(motion, canvas, duração)`: a imagem é pré-escalada 4× (o
antídoto padrão contra o tremor do `zoompan`), a janela caminha um quadro de
saída por quadro de entrada (`d=1`) e volta à resolução do canvas. O movimento é
aplicado **depois** da cadeia de `fit`, tanto no modo legado quanto com
`target_format`. `plan_sha256` já preserva a reprodutibilidade; o
`RenderManifest` não ganha campo novo (mesmo tratamento do `fit`). Só a revisão
visual humana confirma o ritmo do movimento.

Exemplo — timeline vertical 9:16 com corte central por padrão e um segmento que
prefere preservar o quadro inteiro:

```json
{
  "schema_version": 1,
  "plan_id": "shorts-001",
  "brief_id": "brief-001",
  "sources": ["inputs/wide.mp4", "inputs/card.png"],
  "output_path": "output/short.mp4",
  "target_format": { "width": 1080, "height": 1920, "fit": "cover" },
  "operations": [
    { "operation_id": "s1", "kind": "sequence_clip", "source": "inputs/wide.mp4",
      "start_seconds": 0, "end_seconds": 4 },
    { "operation_id": "s2", "kind": "image_clip", "source": "inputs/card.png",
      "parameters": { "duration_seconds": 3, "fit": "contain", "motion": "zoom_in" } }
  ]
}
```

FFmpeg recorta os clipes, zera os timestamps, concatena e reencoda o resultado em
H.264. Cada `image_clip` entra como um input `-loop 1 -t N`. No modo legado a
imagem é ajustada por `scale`/`pad` e, quando há qualquer imagem na timeline,
todos os segmentos são normalizados para 30 fps e `yuv420p`. Com `target_format`
todos os segmentos terminam na resolução do canvas, `setsar=1`, 30 fps e
`yuv420p` — `contain` via `scale=...:force_original_aspect_ratio=decrease` + `pad`
central, `cover` via `scale=...:increase` + `crop` central. O
áudio original dos clipes não entra na timeline. Depois dos segmentos, uma
operação opcional `captions` representa uma faixa inteira. Os cues vêm de uma de
três formas:

- **inline:** sem source, `parameters` com `style=bottom_box` e de 1 a 500 itens
  com texto e tempos relativos à timeline;
- **arquivo:** `source` aponta um `.srt` ou `.vtt` local (declarado como source
  do plano, portanto com fingerprint no `RenderManifest`) e `parameters` contém
  apenas `style=bottom_box`. O arquivo é lido como UTF-8 e convertido em cues;
  tags de estilo e posicionamento (`<`, `>`, `{`, `}`) são recusadas;
- **da narração:** sem source, `parameters={"style":"bottom_box","from":"narration"}`.
  Exige uma operação `narration` em modo texto no mesmo plano. Depois da síntese,
  o texto é dividido em linhas curtas (até ~50 caracteres, quebrando em fronteiras
  de frase e de oração; um artigo, preposição ou conjunção sozinho no fim de uma
  linha é empurrado para a linha seguinte quando cabe) e distribuído sobre a
  duração real da narração por peso de sílabas e pausas estimadas, com a última
  linha terminando exatamente com a voz. Em seguida as quebras são **ancoradas
  nas pausas medidas**: o workflow roda um `silencedetect` somente leitura sobre
  o WAV sintetizado e cada fronteira interna recebe a pausa mais próxima dentro
  de 0,5 s (a melhor combinação primeiro, uma pausa por fronteira), caindo no
  meio dela para a linha não piscar durante o silêncio. Fronteiras sem pausa por
  perto mantêm a estimativa; o silêncio do começo e do fim do arquivo não vale
  como âncora; o primeiro início e o último fim nunca se movem.
  O timing continua **aproximado** — mede onde a voz parou, não qual palavra foi
  dita; alinhamento por palavra (Whisper local) segue como incremento futuro (ver
  "Alinhamento de captions — decisão de reuso" em [VISION.md](VISION.md)). O
  `RenderManifest` já cobre isso pelo SHA-256 do texto da narração.

Em todos os casos cada texto tem até 160 caracteres; cues são ordenados, não
sobrepostos, duram ao menos 1 ms e são limitados à duração visual. O adapter cria
um `.ass` temporário com `PlayResX/Y` igual ao quadro — de modo que fonte e
margens são pixels reais: captions brancas com contorno e sombra (sem caixa),
uma linha, numa faixa segura de plataforma (MarginV ~10% da altura) afastada das
bordas — queima via
FFmpeg/libass e sempre remove o arquivo intermediário. O texto nunca compõe o
filter graph nem carrega chaves de override, evitando que conteúdo editorial seja
interpretado como sintaxe do FFmpeg ou do libass.

Uma operação final opcional `narration`, sem tempos e com
`duration_policy=match_timeline`, adiciona voz a partir de `t=0`. Duas formas:

- **arquivo:** `source` aponta um áudio local; `parameters` só tem
  `duration_policy`. A duração deve bater com a soma dos trechos dentro da
  tolerância (150 ms por padrão); diferença maior falha antes da composição.
- **texto:** sem `source`; `parameters` adiciona `text` (obrigatório) e,
  opcionalmente, `voice`, `speed`, `lang`, `lead_in_seconds`. O workflow chama
  `synthesize_narration` (Kokoro opcional) para um WAV temporário, recusa uma
  narração mais longa que a timeline e descarta o WAV depois; o
  `RenderManifest` guarda o SHA-256 do texto (voz/velocidade/idioma resolvidos
  no artifact), sem persistir o áudio intermediário. Kokoro ausente falha só
  esta operação.

`lead_in_seconds` (≥ 0, menor que a duração visual, só no modo texto) segura a
voz para que a abertura seja só imagem e trilha: o adapter aplica `adelay` antes
do `apad`/`atrim`, então a narração ainda preenche a timeline até o fim. A soma
`lead_in + duração sintetizada` precisa caber na timeline (dentro da tolerância),
senão o plano é recusado antes da composição. Quando as captions vêm de
`from=narration`, os cues são distribuídos sobre o trecho falado e **deslocados
pelo mesmo lead-in**, de modo que nada aparece antes da voz e a última linha
ainda termina com ela. No modo arquivo o lead-in não existe: um áudio pré-gravado
carrega o próprio silêncio inicial e continua tendo de bater com a timeline. Como
o valor vem do plano, o `plan_sha256` já preserva a reprodutibilidade — o
`RenderManifest` apenas cruza o lead-in do artifact contra a operação
`narration`.

Em ambos os casos a narração é normalizada para estéreo/48 kHz, recebe silêncio
final ou trim até a duração visual e é codificada em AAC. A validação exige
exatamente um stream H.264 e, quando há áudio planejado, exatamente um stream
AAC. Sem narração nem música, a timeline silenciosa anterior continua suportada.
Sucesso técnico não é revisão auditiva.

Antes da narração, uma operação opcional `music` declara um source de áudio
local, sem tempos próprios, e
`parameters={"duration_policy":"loop_to_timeline","gain_db":N}`, com
`fade_in_seconds`, `fade_out_seconds` e `duck_db` opcionais (os fades ≥ 0; a soma
não pode passar a duração visual). O ganho aceita valores de -60 a 0 dB. O source deve ter
exatamente um stream de áudio e duração positiva; não precisa corresponder à
timeline, pois FFmpeg o repete e corta no fim visual. A faixa recebe `afade` de
entrada/saída antes do corte, é convertida para estéreo/48 kHz com a voz e
mixada com `normalize=0`, limitada a 0,95. `duck_db` (de -60 a menos de 0, exige uma operação `narration` no mesmo plano)
abaixa a trilha exatamente esse tanto enquanto a voz fala. O adapter aplica, sobre
o ganho estático e os fades, um envelope `volume` determinístico: a rampa de
`MUSIC_DUCK_RAMP_SECONDS` (0,35 s) termina no início da voz — o `lead_in_seconds`
da narração — e a de volta começa quando a faixa de voz acaba, então a abertura e
o encerramento ficam com a música cheia e só o trecho falado é atenuado. O
workflow informa ao adapter a duração da voz (a síntese Kokoro no modo texto, o
probe do arquivo no modo arquivo); sem essa duração o duck vale até o fim da
timeline. A escolha por envelope em vez de `sidechaincompress` é deliberada: a
queda é sempre o valor declarado, seja qual for o nível do source de voz, e
depende só do plano. Uma narração em arquivo bate com a timeline, então lá o duck
cobre praticamente tudo — ele rende mais no modo texto, com lead-in e cauda. A
mixagem final ainda recebe um
fade-in curto anticlique e normalização de loudness EBU R128 para -14 LUFS
integrado / true peak -1,5 dBTP (reamostrada a 48 kHz) antes da codificação AAC —
o mesmo caminho para voz+música, só voz ou só música. Sem voz, a música sozinha
ocupa a faixa AAC. O áudio original dos clipes nunca entra no
mix. O `RenderManifest` guarda ganho, fades e o nível do duck.

Depois de todos os segmentos da timeline, uma operação opcional `fade` — sem
source e sem tempos próprios — abre a imagem a partir do preto e/ou a fecha no
preto: `parameters={"from_black_seconds":X,"to_black_seconds":Y}`, cada campo
opcional e ≥ 0, ao menos um presente, com `X + Y` ≤ duração visual. No máximo uma
`fade` por plano, e ela deve seguir todos os `sequence_clip`/`image_clip`. O
adapter aplica `fade=t=in` / `fade=t=out` sobre o quadro já concatenado e depois
das captions queimadas, então o texto escurece junto com a imagem; o áudio tem os
próprios fades (via `music` e a normalização final). Como os valores vêm do plano,
o `plan_sha256` e os fingerprints de source já preservam a reprodutibilidade — o
`RenderManifest` apenas cruza os spans do artifact contra a operação `fade`.

Esse escopo prova `EditPlan -> timeline -> captions/áudio -> composição -> MP4`
sem substituir HyperFrames, que permanece o compositor planejado para imagens,
layout, motion e captions avançadas. O próximo gap deve ampliar este caminho
rumo a um vídeo dark completo, não criar outro workflow.

Quando o plano persiste `output_path` com o nome canônico `final.mp4`, o comando
`execute-final-sequence-plan` renderiza primeiro em staging no mesmo diretório.
Somente um resultado tecnicamente válido é publicado, de forma exclusiva e sem
overwrite; o arquivo publicado é validado novamente antes do `RenderManifest`.
Se qualquer validação falhar, staging e eventual final recém-criado são
removidos; falha na construção ou publicação do manifest também remove o final
sem rastreabilidade. O comando comum recusa o nome reservado `final.mp4`. Essa fronteira é
QA técnico, não aprovação editorial: `editorial_review=not_performed` permanece
explícito.

## `developer-video` (segundo modo, começando por `canal_dev_01`)

Um segundo modo conceitual, não um segundo motor. Ele reusa integralmente o
`video-sequence`: mesmo `EditPlan`, mesmo composer, mesma validação técnica. O
que muda é **de onde vem a imagem**.

| | `dark-video` | `developer-video` |
| --- | --- | --- |
| origem da imagem | provedor de assets (Pexels/Pixabay/biblioteca local) | o próprio repositório |
| unidade visual | shot fotográfico com direção | screen card + recorte de render anterior |
| texto | legenda alinhada à voz, depois tipografia editorial | só tipografia editorial; sem legenda alinhada |
| áudio | narração + música com ducking | nenhum, até existir gravação humana |

Sem voz, não existe legenda *alinhada* (isso vem de `align-captions` contra um
WAV real). Um corte silencioso pode, ainda assim, carregar uma **legenda de
leitura**: o texto do roteiro em si, burned-in via a mesma operação `captions`
do `video-sequence` (estilo `bottom_box`, itens inline), timed
proporcionalmente dentro de cada bloco — não para o espectador final, mas para
o autor saber o que dizer e quando, ao gravar. `canal_dev_01` usa isso
(`chunk_narration`/`build_captions` em `scripts/build-canal-dev-01.py`). Ela é
substituída pela legenda alinhada de verdade no passo 8 do ciclo abaixo,
depois que a voz existir.

### Screen cards

`domain/screens.py` transforma um `ScreenCard` (o que a tela diz) em um
`ScreenLayout` (onde cada glifo cai), e recusa o que não couber no quadro.
`adapters/screens.py` rasteriza. `render-screens` é a linha de comando. O deck
é um artefato persistido (`schemas/screen-deck-v1.schema.json`).

A regra editorial que importa: **nada em um card pode ser inventado.** Todo
número, caminho, linha de código e saída de comando tem que existir no
repositório. Um card com um output falso é pior que nenhum card.

### Corte visual-only

Um `EditPlan` sem operação `narration` e sem `music` produz um MP4 sem stream
de áudio, e `validate_sequence_artifact` **exige** que seja assim
(`unexpected_non_video_streams`). Não é preciso fabricar um WAV silencioso para
render de revisão.

### Curadoria visual (antes do render)

O ciclo antigo era `render -> descobrir escolhas ruins -> refazer`. O render é o
passo mais caro do pipeline e era também o primeiro lugar onde o autor via o que
o planner tinha decidido. A etapa `visual-curation` inverte isso:

```
planejamento -> candidatos visuais -> aprovação humana -> visual lock -> render
```

**O que exige decisão e o que não exige.** A maior parte de um vídeo de
desenvolvedor é evidência: código real, terminal real, JSON real, manifest,
métrica, um frame de um render que este repositório produziu. Evidência não
precisa de aprovação, precisa de estar correta — ela é usada direto. Só é
decisão o que é gosto: asset externo, stock, metáfora visual, composição
gráfica, ou a escolha entre vários frames igualmente plausíveis
(`needs_human_approval`).

**Prioridade de material** (`MATERIAL_KINDS`, melhor primeiro):

1. material real do projeto;
2. diagrama baseado no projeto;
3. motion typography;
4. composição gráfica;
5. asset externo — último recurso.

**Gate editorial** (`editorial_gate`). Antes de qualquer asset externo chegar ao
autor:

- existe material real do projeto que comunica isso melhor? (`real_project_material_covers_this`)
- a query está em inglês e descreve uma cena, não uma palavra-chave?
  (`query_not_english`, `query_is_a_keyword_not_a_scene`) — o fluxo é
  `roteiro PT-BR -> intenção visual -> cena concreta -> descrição em inglês -> query`,
  nunca a tradução de palavras soltas do roteiro;
- o candidato veio de metadado técnico virando imagem?
  (`metadata_as_visual_subject`, `national_symbol_without_narrative_reason`,
  `national_palette`) — `PT-BR`, `locale`, `idioma`, extensão e nome de arquivo
  não autorizam bandeira, mapa, verde-e-amarelo ou qualquer símbolo nacional; só
  a narração daquele bloco autoriza, dizendo a palavra;
- é um dos fallbacks genéricos proibidos por nome? (`generic_fallback:` — AI
  robot, glowing brain, hacker, matrix code, futuristic technology, generic
  programmer, pessoa digitando, server room, stock corporativo).

Regra: **semanticamente relacionado ≠ editorialmente relevante.**

**Interface de revisão.** `curate-visuals` escreve um `review.html` único e
autossuficiente: uma sequência por bloco, até 3 opções lado a lado com preview,
fonte/asset, conceito visual, query, justificativa e a recomendação com o
motivo. O autor responde em uma linha por sequência: `SEQ 03 -> B`,
`SEQ 07 -> regenerar`.

**Visual lock.** `visual-lock` congela as respostas em
`schemas/visual-lock-v1.schema.json`: sequência, opção aprovada, assets, origem,
query, conceito visual, overrides manuais e o SHA-256 de cada arquivo. Depois de
aprovado, **o render não substitui asset em silêncio**: `verify-visual-lock`
falha explicitamente (exit 1) se um arquivo sumiu ou mudou.

**O checkpoint não é automatizado.** O sistema pesquisa, gera candidatos,
analisa, ranqueia e recomenda; `build_visual_lock` recusa produzir um lock para
qualquer sequência que o humano não tenha respondido, e `regenerar` não é
aprovação.

### O ciclo

1. `scripts/build-canal-dev-01.py` — a tabela de blocos (texto a gravar +
   shots + tipografia) gera `script.md`, `timeline.md`, `screens.json` e
   `edit-plan.json`. Cada bloco é esticado para o tempo que o seu próprio texto
   levaria a ser falado; toda duração é arredondada para um número inteiro de
   frames a 30 fps, para que o plano e o arquivo concordem.
2. `render-screens` — os stills.
3. `scripts/build-canal-dev-01-curation.py` — o mapa de sequências, a auditoria
   do corte anterior, os candidatos e o `review.html`.
4. **Curadoria humana**: o autor responde `SEQ n -> X` sobre o `review.html`.
5. `visual-lock` — as decisões viram `visual-lock.json`.
6. `execute-final-sequence-plan` — o corte, sem áudio.
7. **Revisão humana com o vídeo na tela.**
8. Só então: gravar a voz, medir os timings reais contra o WAV, remedir os
   shots, sincronizar a tipografia e renderizar a versão publicável.

Enquanto o passo 7 não acontecer, `editorial_review` continua `not_performed`.
Aprovar a curadoria visual **não** é revisão editorial do vídeo: é aprovação das
imagens, antes de existir vídeo.

### Dublagem placeholder (opcional, entre os passos 7 e 8)

Antes de gravar a voz real, o autor pode pedir uma **prévia com dublagem**
para ouvir o ritmo em vez de imaginá-lo: `scripts/build-canal-dev-01-dub-preview.py`
sintetiza o roteiro inteiro com Kokoro local (a configuração que
`scripts/voice-benchmark/` já validou — `pm_alex`/`pt-br`/prosódia
unidade-por-unidade), remede cada bloco contra a duração **real** medida
(não a estimativa de palavras por segundo) e reconstrói as legendas a partir
das mesmas unidades reais em vez de um chute proporcional. O script importa
`BLOCKS` de `build-canal-dev-01.py` em vez de duplicá-lo, então nenhuma
decisão visual precisa ser reescrita — só a duração muda. Os artefatos vão
para `output/canal-dev-01-dub-preview/` e
`projects/canal_dev_01/edit-plan-dub-preview.json`, em paralelo ao corte
silencioso, sem substituí-lo. Não é publicável — é só para ouvir.

## Evolução planejada do `dark-video`

`dark-video` v1 **foi atingido em 2026-08-31**: a produção de referência
`projects/prod/` roda de ponta a ponta pelo pipeline oficial (com `final.mp4`
re-derivado byte-idêntico) e recebeu revisão visual/auditiva humana registrada
nas `editorial_notes` do `video-brief.json` — aprovados hook, ritmo,
atmosfera/trilha, encerramento, legendas e visuais. O `RenderManifest` v1 só
aceita `editorial_review = not_performed`; o veredito humano vive no brief e no
histórico da sessão.

Pendência conhecida e aceita, carregada para além do v1:

- **naturalidade da voz.** O Kokoro TTS local ainda soa sintético em PT-BR;
  mitigado com velocidade menor e reescrita de trechos. Uma voz melhor exige
  outro TTS local (Piper/XTTS/F5-TTS, pelos gates de licença/segurança) ou
  narração gravada por humano. Publicar em um canal real ainda pede footage dark
  real com direitos rastreáveis no lugar do b-roll procedural de placeholder —
  passo editorial/de asset, não lacuna do motor.

Já disponível:

- `image_clip` insere imagens locais com duração explícita na timeline de
  `video-sequence`; qualquer dimensão é aceita e a imagem é escalada e
  letter-boxed no canvas dos clipes. Um `motion` opcional (`zoom_in`,
  `zoom_out`, `pan_left`, `pan_right`, `pan_up`, `pan_down`) anima a parada com
  um `zoompan` determinístico de deslocamento fixo, aplicado após a cadeia de
  `fit`; sem `motion` o quadro continua congelado. **Pendente:** revisão visual
  real do ritmo do movimento;
- `captions` aceita itens inline, um `.srt`/`.vtt` local como source, ou
  `from=narration` (linhas curtas de uma linha, quebra por frase/oração, timing
  por peso de sílabas e pausas estimadas e depois ancorado nas pausas medidas no
  WAV sintetizado por `silencedetect` — ainda aproximado; queimadas de um `.ass`
  com `PlayRes` igual ao quadro, em faixa segura de plataforma). **Pendente:**
  revisão visual real do ritmo das quebras ancoradas;
- narração local a partir de texto: Kokoro opcional (extra op-in `tts`, assets
  fail-closed em `.local-tools/kokoro/`, check no `doctor`), o adapter
  `synthesize_narration`, a CLI de baixo nível `narrate` e o **modo texto da
  operação `narration`** no `video-sequence` (`text` + `voice`/`speed`/`lang`
  opcionais; WAV temporário, SHA-256 do texto no `RenderManifest`). Kokoro
  ausente falha só essa capacidade. **Pendente:** revisão auditiva real;
- `lead_in_seconds` na operação `narration` em modo texto: abre o vídeo só com
  imagem e trilha e só então entra a voz, com as captions derivadas da narração
  deslocadas junto. **Pendente:** revisão auditiva/visual real do ritmo da
  entrada da voz;
- `duck_db` na operação `music`: a trilha cai exatamente N dB enquanto a voz fala
  e volta ao nível cheio na abertura e na cauda, por envelope determinístico
  derivado do plano. **Pendente:** revisão auditiva real do quanto abaixar e de
  como o duck combina com a normalização final;
- operação `fade` no `video-sequence`: abre a imagem do preto e/ou a fecha no
  preto (`from_black_seconds`/`to_black_seconds`, soma ≤ duração visual),
  aplicada depois do concat e das captions queimadas. **Pendente:** revisão
  visual real do ritmo dos fades.

### Ordem recomendada dos próximos incrementos

Sequência sugerida (cada item ainda deve passar pela pergunta do menor
incremento; nada aqui autoriza pular testes ou camadas):

1. **Voz local mais natural** — o v1 fechou com o Kokoro como pendência aceita de
   qualidade. A pesquisa de reuso (ver "Voz local — decisão de reuso" em
   [VISION.md](VISION.md)) descartou Piper (GPL-3.0/arquivado), XTTS v2 e F5-TTS
   (licenças não comerciais) e Chatterbox (5–7 GB de VRAM, inviável em CPU): o
   Kokoro segue como único engine empacotado. O caminho de maior qualidade
   disponível hoje é **narração gravada por humano** via `narration` com `source`
   de áudio local — sem dependência nova. Um segundo engine só entra com uma
   opção permissiva viável em CPU ou decisão explícita de exigir GPU.
2. **Captions com alinhamento por palavra (Whisper local)** — o timing de
   `from=narration` já saiu de "só estimativa" para "estimativa ancorada nas
   pausas medidas" (`silencedetect`, sem dependência nova). O passo seguinte, se
   necessário, é alinhamento por palavra com faster-whisper (MIT), seguindo o
   padrão do Kokoro (spike -> adapter -> wire); ver "Alinhamento de captions —
   decisão de reuso" em [VISION.md](VISION.md). Qualidade, não pré-requisito de v1.
3. **Primeiro adapter HyperFrames** — provar `EditPlan -> cena HTML -> MP4` com um
   title card / camada de captions. O `image_clip` já cobre fit configurável e
   Ken Burns básico (`motion` via `zoompan`); avaliar se composição visual rica
   (múltiplas camadas, texto animado, transições) sai mais barata por HTML do que
   encadeando filtros no FFmpeg. Se sim, HyperFrames passa a ser o caminho de
   imagens ricas e captions avançadas, deixando o `image_clip` atual (escala +
   letterbox + pan/zoom simples) para o caso comum. HyperFrames exige
   Node e Chrome headless e deve receber um lock de proveniência análogo a
   `config/ffmpeg-lock.json`.

Pesquisa, roteiro, storyboard e assets automáticos vêm depois do primeiro vídeo
completo. O workflow de creator/talking-head permanece futuro e deverá reutilizar
o mesmo motor.

## Contrato de workflows

Um fluxo editorial completo deve declarar inputs necessários, capacidades
opcionais, formato de output, validações e limitações. Ele deve:

1. fazer o agente orquestrador receber `VideoRequest` e `VideoBrief` válidos;
2. persistir um `EditPlan` antes de iniciar o workflow operacional;
3. fazer o workflow aceitar somente o shape de plano que realmente suporta;
4. não modificar sources;
5. falhar claramente quando uma ferramenta opcional estiver ausente;
6. registrar decisões e parâmetros reproduzíveis;
7. usar adapters em vez de shell improvisado;
8. separar validação técnica de julgamento editorial.

## Sequência de implementação

O próximo incremento deve ser escolhido pela pergunta: “qual é o menor
incremento funcional que mais nos aproxima do primeiro vídeo dark completo?”.
Não criar novos workflows quando ampliar `video-sequence` resolve o gargalo.
