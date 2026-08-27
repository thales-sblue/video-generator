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
operações planejadas e um novo caminho de output.

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
natural e não escondem decisões editoriais.

### Adapters e renderers

Adapters encapsularão subprocessos locais com argumentos estruturados:

- ffprobe para inspeção técnica;
- FFmpeg para cortes, concatenação, áudio, codecs e transformações;
- Whisper local para transcrição;
- Kokoro local para TTS;
- HyperFrames como compositor principal para timeline, layout, motion, captions
  e renderização.

ComfyUI é uma possibilidade futura opcional e não pertence ao MVP. Dependências
opcionais ausentes devem degradar capacidades específicas, não o núcleo.

### Validação

Validação ocorre em fronteiras sucessivas: contratos, preflight de paths e
ferramentas, probes dos outputs e, quando possível, inspeção técnica do render.
Revisão editorial visual/auditiva permanece explícita e não pode ser inferida
apenas de testes automatizados.

## Persistência e segurança

Inputs em `inputs/` e `assets/` são referências imutáveis. Estado reproduzível de
uma produção ficará em `projects/<id>/`; artifacts novos ficarão em `output/` ou
no diretório do projeto. Esses diretórios são ignorados pelo Git, preservando
apenas arquivos sentinela.

Antes de execução, o sistema deverá resolver paths e recusar output igual a
qualquer input. Futuro `RenderManifest` registrará tool versions, inputs,
checksums, plano aplicado, outputs e resultados de validação.

## Direção da CLI

A interface deve evoluir gradualmente para:

```text
video-generator doctor
video-generator inspect
video-generator transcribe
video-generator plan
video-generator render
video-generator validate
```

Somente `doctor` existe agora. Novos comandos entram quando houver uma operação
reutilizável e testada por trás deles.
