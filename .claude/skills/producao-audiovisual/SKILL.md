---
name: producao-audiovisual
description: >-
  Produzir, montar ou renderizar um vídeo dark real (faceless: narração sobre
  vídeos/imagens, com captions e música) a partir de assets locais neste
  repositório video-generator. Use quando o pedido for gerar/compor/renderizar um
  vídeo, montar uma timeline, adicionar narração/captions/música a clipes locais,
  ou levar um EditPlan até final.mp4. Não use para dúvidas de arquitetura nem
  para o segundo workflow (edição de creator/talking-head), que ainda não existe.
---

# Protocolo de produção audiovisual

Segue o "Protocolo de produção audiovisual" da constituição do projeto. Leia
antes: [`AGENTS.md`](../../../AGENTS.md), [`docs/WORKFLOWS.md`](../../../docs/WORKFLOWS.md),
[`docs/VIDEO_LANGUAGE.md`](../../../docs/VIDEO_LANGUAGE.md). Esta skill organiza a
execução; ela não substitui essas regras nem autoriza pular testes.

## Premissas que não se negociam

- **Inputs são imutáveis.** Nunca sobrescreva, mova ou apague vídeo, imagem,
  áudio ou texto do usuário. Todo output novo vai para `output/` ou
  `projects/<id>/`, e nenhum path de output pode coincidir com um input.
- **Local-first, sem API paga.** Nenhuma chamada a API de geração (Anthropic,
  OpenAI, ElevenLabs, Runway, HeyGen…). Ferramentas locais: FFmpeg/ffprobe em
  `.local-tools`, e futuramente Whisper e Kokoro locais.
- **Sucesso técnico ≠ aprovação editorial.** `editorial_review` permanece
  `not_performed` até haver revisão humana real com evidência. Nunca afirme que
  revisou visual/auditivamente sem ter feito.
- **Reprodutibilidade.** Decisões relevantes vivem em contratos persistidos com
  `schema_version`; seeds e versões de ferramentas são persistidas quando
  influenciam o resultado.

## Ambiente

```powershell
$py = ".\.venv\Scripts\python.exe"
& $py -m video_generator doctor            # confere FFmpeg/ffprobe/Node
$env:PYTHONPATH = "src"; & $py -m unittest discover -s tests -v   # 121 testes, antes e depois
```

## Passos

1. **Identificar e preservar** os arquivos relevantes em `inputs/` / `assets/`.
   Confirme que nenhum será escrito.
2. **Inspeção técnica** de cada source:
   `& $py -m video_generator inspect <source> --json`.
3. **Entender** roteiro, intenção, audiência e plataforma. Registre o que guiou
   as decisões — isso vira `VideoRequest` e `VideoBrief`.
4. **Escolher o menor escopo executável** dentro de `dark-video`. Não crie um
   workflow novo se ampliar `video-sequence` resolve. Hoje o motor suporta:
   `segment-extract` (um recorte) e `video-sequence` (≥2 segmentos ordenados, com
   ≥1 `sequence_clip` e, opcionalmente, `image_clip` com `duration_seconds` +
   `captions`/`music`/`narration` opcionais).
5. **Fala local quando relevante**: `extract-audio` para separar faixa; narração
   a partir de texto (Kokoro) ainda não está implementada — se o pedido exigir,
   pare e trate como próximo incremento (ver `docs/WORKFLOWS.md`).
6. **Persistir contratos** em `projects/<id>/`: `video-request.json`,
   `video-brief.json`, `edit-plan.json` conforme os schemas em `schemas/`.
7. **Validar o plano** antes de qualquer escrita de mídia:
   `& $py -m video_generator preflight projects\<id>\edit-plan.json --json`
   (recusa operação temporal sem source, fora da duração, ou output == input).
8. **Executar** via workflow, nunca shell FFmpeg improvisado:
   - `execute-segment-plan <edit-plan.json> --manifest <...> --json`
   - `execute-sequence-plan <edit-plan.json> --manifest <...> --json`
   O nome `final.mp4` é recusado aqui de propósito.
9. **Render de trabalho** quando precisar inspecionar antes do final.
10. **Inspecionar o resultado** técnica e editorialmente. Use a skill `run` ou
    abra o MP4 se houver como assistir. Registre limitações de inspeção — nunca
    as disfarce de aprovação.
11. **Corrigir o detectável e renderizar o final** com
    `execute-final-sequence-plan` (exige `output_path` terminando em `final.mp4`;
    faz staging, valida, publica sem overwrite e revalida).
12. **Validar o final e persistir `RenderManifest`**:
    `validate-manifest` e `validate-project` devem retornar `0`. Só então conclua.

## Ao terminar

Relate: o que foi produzido, workflow e escopo escolhidos, testes executados,
validações manuais **pendentes** (com o motivo), limitações, próximo gargalo para
`dark-video` v1, e progresso aproximado. Não faça commit sem o usuário pedir.
