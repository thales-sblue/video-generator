# Arquitetura

## Visão geral

```text
User intent
    ↓
agente orquestrador / Claude Code (interpretação e decisões editoriais)
    ↓
VideoRequest -> VideoBrief -> EditPlan
    ↓
workflows
    ├─> adapters: ffprobe / FFmpeg / Whisper / Kokoro
    └─> renderer: HyperFrames
    ↓
validation -> RenderManifest -> artifacts
```

A direção madura pode inserir `Script`, `Storyboard` e `AssetPlan` entre brief e
plano, mas esses contratos não serão criados antes de haver necessidade concreta
de checkpoint ou invariantes. `AssetProvenance` (origem, licença, uso comercial,
atribuição, data, SHA-256, restrições) segue a mesma regra: só vira contrato
quando o motor obtiver assets externos automaticamente; até lá é gate de revisão
editorial (ver [AGENTS.md](../AGENTS.md)). A prioridade arquitetural atual é
atravessar `EditPlan -> timeline -> composição -> MP4` para `dark-video`.

O fluxo herda do `midi-generator` a separação entre intenção, plano, integração e
execução, mas não importa código nem cria acoplamento entre os repositórios.

## Camadas

### Domínio

`video_generator.domain` define modelos imutáveis, serialização e invariantes.
Ele usa apenas a biblioteca padrão. `VideoRequest` registra o pedido e os
sources; `VideoBrief` traduz intenção em direção editorial; `EditPlan` descreve
operações planejadas e um novo caminho de output; `RenderManifest` registra uma
execução local e seus artifacts sem afirmar aprovação editorial.

Os JSON Schemas em `schemas/` são a fronteira interoperável v1. Os modelos
produzem JSON-safe dictionaries equivalentes, com chaves ordenadas na
serialização textual.

`video_generator.domain.planning` acrescenta a camada de planejamento editorial
que fica antes do `EditPlan`: `NarrativeScript`, `ScenePlan`, `ShotPlan` e
`AssetRequirements` realizam os candidatos `Script` / `Storyboard` / `AssetPlan`
do `AGENTS.md`. É stdlib pura, sem I/O, e só depende de `domain.models` (para
`EditPlan`/`EditOperation`/`TargetFormat`); nada em `models` depende dela. As
funções `plan_scenes` / `plan_shots` geram um rascunho determinístico,
`apply_overrides` incorpora o refino do agente sobre os três documentos, e
`shot_plan_to_edit_plan` converte um `ShotPlan` resolvido para o `EditPlan`
existente sem alterar renderer, workflow ou `models`. Invariantes que dependem
de outro contrato ficam em métodos `validate_against`, não no `__post_init__`.
Ver [WORKFLOWS.md](WORKFLOWS.md) e [VIDEO_LANGUAGE.md](VIDEO_LANGUAGE.md).

`video_generator.domain.assets` fecha a lacuna entre `AssetRequirements` e o
converter: os contratos `AssetCandidate`, `AssetProvenance`, `ResolvedAsset` e
`AssetResolutionPlan` (mais `AssetScoringPolicy` com pesos como configuração),
`sanitize_query` lexical, `score_candidate`/`rank_candidates` e `review_reuse`
são stdlib puros e determinísticos, sem rede. A obtenção real vive fora do
domínio: `adapters.asset_providers` (`LocalAssetProvider` offline;
`Pexels`/`Pixabay` opt-in por chave de API gratuita, isolados e removíveis) e o
orquestrador impuro `video_generator.resolve` (copy/stage, SHA-256, ffprobe,
fail-closed contra sobrescrita). `AssetProvenance` — origem, licença, autor,
`acquired_at`, SHA-256 — deixa de ser candidato e passa a contrato agora que o
motor adquire assets externos automaticamente; `asset-bindings.json`
(`{asset_id: path}`) liga o resultado ao `shot_plan_to_edit_plan` sem mudar o
`EditPlan`.

`video_generator.domain.editorial` responde a pergunta que o planejador de
ritmo nunca fez: *o que este trecho está tentando dizer, e o que o espectador
deveria estar olhando enquanto ele é dito?* `NarrationBeat` é a leitura
semântica de uma fatia de narração (conceito, entidades, emoção, intenção
visual, queries candidatas, importância, papel editorial); `TextEvent` é a
camada de ênfase na tela, deliberadamente **separada da legenda** — a legenda
transcreve a voz, o evento levanta uma ideia; `HookPolicy` dá à abertura um teto
próprio; `VisualStyle` é o brand kit reutilizável (`dark-documentary-v1`).
Tudo é heurística lexical determinística sobre léxicos que são **dados** na
`EditorialPolicy` — sem modelo, sem embedding, sem rede — para que um mapeamento
ruim seja uma linha de edição e toda decisão fique auditável depois. O módulo é
stdlib puro e não importa nenhum irmão do domínio: `planning` depende dele,
nunca o contrário.

Três regras dessa camada valem registro porque não são óbvias:

- **As queries de asset saem em inglês; narração e `visual_intent` ficam no
  idioma do roteiro.** Todo banco gratuito que o projeto alcança indexa em
  inglês, então uma query em português é comparada contra metadata inglesa e
  pontua perto de zero. Traduzir na fronteira da busca é a diferença entre
  "uma foto do Einstein" e "uma foto de uma prova".
- **Um termo presente em mais de 25% dos beats nunca lidera uma query**: ele é o
  assunto do vídeo, não daquele trecho.
- **Relevância antes de estética.** A identidade visual veste o asset escolhido
  (tipografia, accent, margens, composição); ela nunca participa da escolha do
  asset. `vignette_strength` é o único tratamento que toca a imagem e está
  limitado a 0,35 justamente para que consistência não vire repintura.

O tempo continua tendo dono único: um beat não guarda timing. `shot_timeline` e
`narration_slices` *derivam* offsets e fatias do `ShotPlan` e do `ScenePlan`, e
os `TextEvent` são cronometrados contra essa derivação — nunca contra uma
segunda cópia que pode divergir.

### Configuração

`config/default.toml` declara que processamento e render são locais e desabilita
serviços externos no runtime de mídia. A configuração falha fechada: valores
inseguros ou ausentes não são aceitos. Internet gratuita pode ser usada pelo
orquestrador para pesquisa e aquisição autorizada de assets sem mudar o caráter
local do render; qualquer integração que receba dados requer autorização e
adapter explícitos. APIs pagas de geração não são dependências operacionais.

### Workflows

Workflows coordenarão capacidades para um objetivo audiovisual. Eles recebem
contratos validados e chamam interfaces de execução; não interpretam linguagem
natural e não escondem decisões editoriais. `segment-extract` é o primeiro
workflow operacional: exige um plano com um único recorte temporal, executa
preflight, extração e validação técnica, e recusa qualquer shape não suportado
antes de escrever mídia.

`video-sequence` compõe ao menos dois segmentos ordenados no `EditPlan` — com ao
menos um `sequence_clip` (trecho de vídeo) e, opcionalmente, `image_clip`
(imagem local com `duration_seconds` e um `motion` opcional que anima a parada
com um Ken Burns determinístico via `zoompan`). Pode queimar uma faixa
`captions` — cues
inline, de um `.srt`/`.vtt` local, ou derivados do texto da narração
(`from=narration`, timing aproximado) —, repetir uma faixa `music` com ganho e
fades opcionais e receber uma operação final `narration` (de um áudio local ou
sintetizada de `text` via Kokoro, com o SHA-256 do texto no manifest). O
workflow executa preflight, confere dimensões, tempos, streams e políticas
persistidas, recorta e concatena os segmentos, queima os cues, normaliza e
mistura áudio e produz MP4 H.264/AAC. Sem opções, preserva o render silencioso
anterior. Sem `target_format` no plano, os clipes definem o canvas e devem
compartilhar dimensões; imagens de qualquer tamanho são escaladas e
letter-boxed nele. Com `target_format` (contrato `TargetFormat`: `width`,
`height` pares ≤ 7680, `fit` `contain`/`cover`), o canvas passa a ser a
resolução de entrega declarada, sources heterogêneos compõem a mesma timeline e
cada segmento é normalizado deterministicamente para o canvas (fit por segmento
ou herdado do `target_format`). O campo é omit-when-None: planos e briefs
legados serializam e fazem fingerprint exatamente como antes. Um Ken Burns
simples (`image_clip.motion`: zoom/pan de deslocamento fixo) já existe;
transitions e composição visual rica com múltiplas camadas continuam fora do
escopo, reservadas ao compositor rico.

### Adapters e renderers

Adapters encapsulam subprocessos locais com argumentos estruturados. O adapter
de ffprobe oferece inspeção técnica somente leitura e saída normalizada. O
adapter de FFmpeg oferece extração temporal por stream copy ou, quando a decisão
está persistida como `mode=precise`, reencode MP4 H.264/AAC com seek após o input.
Ele também separa a primeira faixa de áudio em WAV PCM 16-bit, 48 kHz estéreo,
como artifact local previsível para análise e processamento posterior. Todas as
operações publicam um arquivo novo sem overwrite e limpam artifacts parciais;
para captions, gera um `.ass` temporário a partir de cues já validados, com
`PlayResX/Y` igual ao quadro (fonte e margens em pixels reais, uma linha, faixa
segura de plataforma), e remove o intermediário em sucesso ou falha. A mixagem
de áudio termina com fade-in anticlique e normalização de loudness EBU R128 para
um alvo de publicação (-14 LUFS / true peak -1,5 dBTP). Os demais entram conforme
casos funcionais exigirem:

- ffprobe para inspeção técnica;
- FFmpeg para cortes, concatenação, áudio, codecs e transformações;
- Whisper local para transcrição;
- Kokoro local para TTS;
- HyperFrames como compositor principal para timeline, layout, motion, captions
  e renderização.

FFmpeg pode executar montagem sequencial e normalização de baixo nível quando o
caso é estrito. Isso não substitui HyperFrames como compositor principal para
layout, motion, captions e composição visual de `dark-video`.

No Windows, `video_generator.tooling` resolve primeiro a instalação isolada em
`.local-tools/ffmpeg`. Antes de devolver um executável, compara o conjunto exato
de EXEs/DLLs, tamanhos e hashes SHA-256 com o lock versionado em
`config/ffmpeg-lock.json`. Uma instalação local presente mas divergente é
recusada; somente quando ela não existe o resolver consulta o `PATH`. Assim, uma
dependência local corrompida ou adulterada não vira fallback silencioso.

O Kokoro (TTS local, futuro) segue o mesmo princípio: `tooling.resolve_kokoro_assets`
procura `kokoro-v1.0.onnx` e `voices-v1.0.bin` em `.local-tools/kokoro/` (ou
`KOKORO_HOME`), devolve `None` quando o diretório inexiste e falha fechado quando
um arquivo está ausente, vazio ou não é regular. O `doctor` combina isso com a
presença do pacote `kokoro-onnx` (extra op-in `tts`) e reporta o estado sem
instalar nada.

ComfyUI é uma possibilidade futura opcional e não pertence ao MVP. Dependências
opcionais ausentes devem degradar capacidades específicas, não o núcleo.

### Validação

Validação ocorre em fronteiras sucessivas: contratos, preflight de paths e
ferramentas, probes dos outputs e, quando possível, inspeção técnica do render.
Revisão editorial visual/auditiva permanece explícita e não pode ser inferida
apenas de testes automatizados.

O preflight disponível carrega um `EditPlan`, inspeciona seus sources com
ffprobe e falha fechado quando uma operação temporal não declara source, excede
a mídia ou sua duração não pode ser determinada. Ele não executa nem modifica
artifacts.

A validação posterior de segmentos compara o artifact publicado com o intervalo
solicitado, incluindo path, tamanho, presença de streams e duração dentro de uma
tolerância explícita. Assim, desvios causados por alinhamento de keyframes não
são promovidos silenciosamente a sucesso técnico.

Essa validação também é uma fronteira de CLI (`validate-segment`): ela recebe os
metadados imutáveis registrados logo após a extração, executa apenas ffprobe no
artifact publicado e retorna um relatório técnico. Não publica arquivos nem
substitui aprovação editorial humana.

`validate-manifest` oferece uma segunda verificação read-only que não depende de
FFmpeg/ffprobe. Ela recarrega `RenderManifest` e `EditPlan`, confere IDs e o hash
canônico do plano e recalcula os fingerprints de sources e outputs. Integridade,
validação técnica registrada e revisão editorial permanecem campos separados; o
comando relata apenas `technically_ready`.

`validate-project` compõe essa verificação com a rastreabilidade dos quatro
contratos persistidos. Ele valida os IDs entre request, brief, plano e manifest,
a direção editorial solicitada e a proveniência dos sources do plano. O workflow
editorial de `VideoBrief` não é comparado ao workflow operacional do manifest:
por exemplo, um brief `music-teaser` pode ser executado pela capacidade
`segment-extract`. O relatório mantém `trace_valid`, integridade do manifest,
resultado técnico registrado e `editorial_review` como dimensões distintas.

## Persistência e segurança

Inputs em `inputs/` e `assets/` são referências imutáveis. Estado reproduzível de
uma produção ficará em `projects/<id>/`; artifacts novos ficarão em `output/` ou
no diretório do projeto. Esses diretórios são ignorados pelo Git, preservando
apenas arquivos sentinela.

Antes de execução, o sistema resolve paths e recusa output igual a qualquer
input. O `RenderManifest` v1 registra versões e caminhos das ferramentas,
fingerprints SHA-256 de inputs, plano e outputs, além dos resultados de validação,
`local_only = true` e `editorial_review = not_performed`. O manifest é publicado
exclusivamente e nunca substitui estado existente. O fingerprint de cada source
é capturado após o preflight, imediatamente antes da execução, e conferido outra
vez antes da publicação para detectar alterações concorrentes.

A extração de segmento isolada continua sendo uma capacidade de baixo nível. O
`segment-extract` coordena essa operação a partir de um `EditPlan`, escolhe entre
cópia e recorte preciso somente pelo parâmetro persistido, valida o
artifact e publica o `RenderManifest`, mas não promove o recorte a render final.
Composição por renderer e avaliação editorial ainda permanecem necessárias antes
de expor um render final.

O workflow `video-sequence` já produz um MP4 composto a partir de múltiplos
trechos, captions temporizadas, narração e música com ganho básico. O modo final
usa staging no mesmo filesystem, publica `final.mp4` por hard link exclusivo
somente após validação e valida novamente o caminho publicado antes do manifest.
Para o primeiro caso baseado apenas em vídeo, imagens permanecem opcionais; a
fronteira restante é executar uma produção real e registrar a revisão humana
que ainda não foi realizada.

## Direção da CLI

A interface deve evoluir gradualmente para:

```text
video-generator doctor
video-generator inspect <source> [--json]
video-generator preflight <edit-plan.json> [--json]
video-generator extract-segment <source> <output> --start-seconds N --end-seconds N [--json]
video-generator extract-audio <source> <output.wav> [--json]
video-generator narrate <output.wav> (--text T | --text-file F) [--voice V] [--speed S] [--lang L] [--json]
video-generator execute-segment-plan <edit-plan.json> [--json]
video-generator execute-sequence-plan <edit-plan.json> [--json]
video-generator execute-final-sequence-plan <edit-plan.json> [--json]
video-generator validate-manifest <render-manifest.json> --plan <edit-plan.json> [--json]
video-generator validate-project --request <video-request.json> --brief <video-brief.json> --plan <edit-plan.json> --manifest <render-manifest.json> [--json]
video-generator transcribe
video-generator plan
video-generator render
video-generator validate
```

`doctor`, `inspect`, `preflight`, `extract-segment`, `extract-audio`, `narrate`,
`execute-segment-plan`, `execute-sequence-plan`, `execute-final-sequence-plan`,
`validate-segment`, `validate-manifest` e `validate-project` existem agora. Novos
comandos entram quando houver uma operação reutilizável e testada por trás deles.

`narrate` é uma operação de baixo nível, como `extract-audio`: sem `EditPlan` nem
`RenderManifest` e sem afirmar revisão auditiva. Depende do Kokoro opcional e
falha fechado quando ele não está disponível.

`transcribe`, `render` e `validate` são atalhos operacionais previstos sobre
capacidades locais já testadas. `plan`, quando existir, é um utilitário
**determinístico** de scaffold e validação de `EditPlan` — emitir um esqueleto,
conferir shape e vínculos — e **não** gera decisões editoriais: interpretar
intenção, escolher workflow e montar o plano continua sendo papel do agente
orquestrador, não da CLI.
