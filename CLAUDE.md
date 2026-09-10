# CLAUDE.md

Guia de entrada para o **Claude Code** neste repositório. O agente orquestrador
que antes era o Codex agora é o Claude Code; a arquitetura e as fronteiras não
mudam.

## Leitura obrigatória antes de codar

1. **[AGENTS.md](AGENTS.md)** — a constituição do projeto: direção de produto,
   fronteiras arquiteturais, política local-first, contratos, definição de pronto
   e o **Protocolo para `continue`**. Vale integralmente para o Claude Code.
2. **[docs/VISION.md](docs/VISION.md)**, **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**,
   **[docs/WORKFLOWS.md](docs/WORKFLOWS.md)**, **[docs/VIDEO_LANGUAGE.md](docs/VIDEO_LANGUAGE.md)**.
3. **[schemas/](schemas/)** — contratos JSON públicos v1 (`VideoRequest`,
   `VideoBrief`, `EditPlan`, `RenderManifest`). São interface pública: alterar
   exige testes e documentação.

## Prioridade

`dark-video`: o menor incremento funcional que mais aproxima o projeto do
primeiro vídeo dark completo, assistível e reproduzível. Não iniciar o workflow
de creator/talking-head agora. Não adicionar geração de vídeo por IA antes da
composição básica.

## Ambiente de desenvolvimento (esta máquina)

O interpretador é um **Python 3.12 standalone** (python.org, instalado via
`winget install --exact --id Python.Python.3.12`), independente do Codex, em
`C:\Users\Thales\AppData\Local\Programs\Python\Python312\python.exe`. O stub da
Microsoft Store no `PATH` não serve.

```powershell
# criar o venv uma vez (se .venv ainda não existir)
& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

- Python: **3.12.10**. Nenhuma dependência de runtime; testes usam só a stdlib.
- FFmpeg/ffprobe: instalação isolada e travada em `.local-tools/ffmpeg`, conferida
  contra `config/ffmpeg-lock.json`. É ignorada pelo Git — se `.local-tools/`
  sumir, copie de outra checkout ou rebaixe conforme `README.md`. Nada é
  adicionado ao `PATH` global.
- Node e HyperFrames são opcionais e hoje ausentes; sua falta degrada só
  capacidades específicas, nunca contratos/planejamento/diagnóstico.
- Kokoro (TTS local): extra opt-in `pip install -e .[tts]` + arquivos do modelo
  em `.local-tools/kokoro/` (ou `KOKORO_HOME`).
- Aligner (Whisper local, legendas cronometradas no áudio real): extra opt-in
  `pip install -e .[align]` + um modelo CTranslate2 em
  `.local-tools/whisper/<modelo>/` (ou `WHISPER_HOME`), carregado com download
  desabilitado. `doctor` reporta o estado dos dois.

## Comandos essenciais

```powershell
# suíte completa (rodar antes e depois de qualquer alteração)
$env:PYTHONPATH = "src"; .\.venv\Scripts\python.exe -m unittest discover -s tests -v

# diagnóstico somente leitura do ambiente
.\.venv\Scripts\python.exe -m video_generator doctor
```

CLI disponível hoje: `doctor`, `inspect`, `preflight`, `extract-segment`,
`extract-audio`, `narrate` (com `--prosody`), `align-captions`, `review-cuts`
(análise editorial de vídeo gravado: transcrição inteira + silêncios → trechos
`KEEP`/`REVIEW`/`CUT` com categoria semântica, blocos removíveis e resumo de
edição; `provenance` `heuristic`|`agent`; `--from-review` regenera o `.md`; sem
render), `plan-scenes`
(com `--semantic`, `--visual-relevance`, `--visual-direction`,
`--editorial-translation` e `--motion-typography`),
`resolve-assets`, `render-screens`, `curate-visuals`, `visual-lock`,
`verify-visual-lock`, `execute-segment-plan`,
`execute-sequence-plan`, `execute-final-sequence-plan`, `validate-segment`,
`validate-audio`, `validate-manifest`, `validate-project`. Veja `README.md`
para exemplos.

A mesma suíte roda no GitHub Actions (`.github/workflows/ci.yml`) em `push` para
`main` e em pull requests, com Python 3.12 e `PYTHONPATH=src`.

## Regras permanentes (resumo — AGENTS.md prevalece)

- Inputs em `inputs/` e `assets/` são **imutáveis**: nunca sobrescrever, mover ou
  apagar sources do usuário. Todo output novo vai para `output/` ou
  `projects/<id>/`, e o path de output nunca pode coincidir com um input.
- Não versionar mídia, renders, modelos, caches, secrets nem temporários.
- Domínio (`src/video_generator/domain/`) usa só a stdlib e não depende de
  adapters, renderers, FFmpeg, Whisper, Kokoro ou I/O. Dependências apontam para
  dentro.
- Sem APIs pagas de geração como dependência operacional. Integração externa
  exige autorização explícita, opt-in e fronteira de adapter — nunca fallback
  silencioso.
- Subprocessos sempre com argumentos estruturados e paths validados; nunca montar
  shell a partir de texto não confiável (ex.: texto de caption não entra no
  filter graph do FFmpeg).
- Sucesso técnico não é aprovação editorial: `editorial_review` permanece
  `not_performed` até haver revisão humana real com evidência.
- Antes de implementar uma capacidade relevante, decidir build vs reuse
  conscientemente (`gap -> pesquisa -> análise -> decisão`), proporcional ao
  tamanho do incremento, e reportar a decisão no fim do ciclo.
- Nenhuma dependência, código ou componente externo entra sem passar pelos gates
  de licença (compatibilidade comercial, copyleft, atribuição, opção
  proprietária futura) e de segurança (origem oficial, manutenção, sem execução
  remota automática, instalação isolada, fail-closed).
- Assets externos só entram em produção comercial com origem e direito de uso
  comercial rastreáveis; não usar a promessa absoluta "sem copyright".
- Um incremento só está pronto com testes das invariantes/falhas, suíte completa
  passando, diff revisado (segurança, camadas, escopo) e documentação atualizada.

## Ao receber apenas `continue`

Seguir o **Protocolo para `continue`** de [AGENTS.md](AGENTS.md): ler as regras,
inspecionar árvore/Git/commits/diff, rodar a suíte, descobrir onde o último ciclo
parou, escolher **um** incremento coeso rumo a `dark-video` v1, pesquisar reuso
e validar licença/custo/segurança quando o incremento for relevante, implementar
na camada correta, testar, revisar o próprio diff, atualizar docs e commitar.

Mapeamento dos passos para recursos nativos do Claude Code:

| Passo do protocolo | Recurso |
| --- | --- |
| 2 — inspecionar árvore, estado do Git, commits e diff do `HEAD` | subagent **Explore** quando a varredura for ampla |
| 3 e 11 — rodar a suíte completa | `$env:PYTHONPATH = "src"; .\.venv\Scripts\python.exe -m unittest discover -s tests -v` |
| 5 e 9 — pesquisar soluções open source, decidir build vs reuse e validar licença/segurança | **plan mode** + `WebSearch`/subagent **Explore**; skill **`/security-review`** para o gate de segurança |
| 7 — escolher **um** incremento coeso | **plan mode** (desenhar antes de tocar em código) |
| 10 — validações manuais / rodar o app | skill **`run`** quando houver algo assistível |
| 12 — revisar o próprio diff (segurança, camadas, escopo) | skills **`/code-review`** e **`/security-review`** |
| continuidade entre sessões | **memory files** (`C:\Users\Thales\.claude\projects\...\memory\`) |

## Skills do projeto

- **`producao-audiovisual`** (`.claude/skills/producao-audiovisual/`) — encapsula o
  Protocolo de produção audiovisual do [AGENTS.md](AGENTS.md) para pedidos de
  produzir/montar um vídeo dark real a partir de assets locais. Dispara em
  linguagem natural; não substitui a leitura de `AGENTS.md`.
