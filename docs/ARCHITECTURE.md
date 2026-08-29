# Arquitetura

## Visão geral

```text
User intent
    ↓
Codex (interpretação e decisões editoriais)
    ↓
VideoRequest -> VideoBrief -> EditPlan
    ↓
workflows
    ├─> adapters: ffprobe / FFmpeg / Whisper / Kokoro
    └─> renderer: HyperFrames
    ↓
validation -> RenderManifest -> artifacts
```

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

### Configuração

`config/default.toml` declara `local_only = true` e desabilita serviços externos.
A configuração falha fechada: valores inseguros ou ausentes não são aceitos. Uma
integração futura só poderá relaxar essa política por autorização e desenho
explícitos, nunca por fallback.

### Workflows

Workflows coordenarão capacidades para um objetivo audiovisual. Eles recebem
contratos validados e chamam interfaces de execução; não interpretam linguagem
natural e não escondem decisões editoriais. `segment-extract` é o primeiro
workflow operacional: exige um plano com um único recorte temporal, executa
preflight, extração e validação técnica, e recusa qualquer shape não suportado
antes de escrever mídia.

### Adapters e renderers

Adapters encapsulam subprocessos locais com argumentos estruturados. O adapter
de ffprobe oferece inspeção técnica somente leitura e saída normalizada. O
adapter de FFmpeg oferece extração temporal por stream copy para um arquivo novo,
com publicação sem overwrite e limpeza de artifacts parciais; os demais entram
conforme casos funcionais exigirem:

- ffprobe para inspeção técnica;
- FFmpeg para cortes, concatenação, áudio, codecs e transformações;
- Whisper local para transcrição;
- Kokoro local para TTS;
- HyperFrames como compositor principal para timeline, layout, motion, captions
  e renderização.

No Windows, `video_generator.tooling` resolve primeiro a instalação isolada em
`.local-tools/ffmpeg`. Antes de devolver um executável, compara o conjunto exato
de EXEs/DLLs, tamanhos e hashes SHA-256 com o lock versionado em
`config/ffmpeg-lock.json`. Uma instalação local presente mas divergente é
recusada; somente quando ela não existe o resolver consulta o `PATH`. Assim, uma
dependência local corrompida ou adulterada não vira fallback silencioso.

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
`segment-extract` coordena essa operação a partir de um `EditPlan`, valida o
artifact e publica o `RenderManifest`, mas não promove o recorte a render final.
Composição por renderer e avaliação editorial ainda permanecem necessárias antes
de expor um render final.

## Direção da CLI

A interface deve evoluir gradualmente para:

```text
video-generator doctor
video-generator inspect <source> [--json]
video-generator preflight <edit-plan.json> [--json]
video-generator extract-segment <source> <output> --start-seconds N --end-seconds N [--json]
video-generator execute-segment-plan <edit-plan.json> [--json]
video-generator validate-manifest <render-manifest.json> --plan <edit-plan.json> [--json]
video-generator validate-project --request <video-request.json> --brief <video-brief.json> --plan <edit-plan.json> --manifest <render-manifest.json> [--json]
video-generator transcribe
video-generator plan
video-generator render
video-generator validate
```

`doctor`, `inspect`, `preflight`, `extract-segment`, `execute-segment-plan`,
`validate-segment`, `validate-manifest` e `validate-project` existem agora. Novos
comandos entram quando houver uma operação reutilizável e testada por trás deles.
