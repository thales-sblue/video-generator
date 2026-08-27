# video-generator

Fundação de um agente local de produção audiovisual controlado pelo Codex. O
projeto transforma intenção e referências de mídia em contratos persistentes:

```text
VideoRequest -> VideoBrief -> EditPlan -> execução local (futura)
```

Este primeiro incremento não edita nem renderiza vídeo. Ele estabelece o
domínio, os schemas, a política local-only e um diagnóstico do ambiente para que
as capacidades audiovisuais sejam adicionadas incrementalmente e com testes.

## Princípios

- inputs nunca são sobrescritos ou apagados;
- decisões editoriais são persistidas em contratos versionados;
- processamento é local por padrão e não depende de APIs pagas;
- o domínio não depende de renderers nem ferramentas externas;
- FFmpeg/ffprobe cuidam de mídia de baixo nível e HyperFrames será o compositor
  principal quando a renderização for implementada.

## Requisitos

- Python 3.11 ou superior;
- Git para desenvolvimento;
- Node, FFmpeg, ffprobe e HyperFrames são detectados pelo `doctor`, mas ainda são
  opcionais neste incremento.

Nenhuma dependência Python de runtime é necessária.

## Uso local

No PowerShell, a partir da raiz do repositório:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
video-generator doctor
```

Também é possível executar sem instalar o pacote:

```powershell
$env:PYTHONPATH = "src"
python -m video_generator doctor
python -m video_generator doctor --json
```

O diagnóstico apenas inspeciona o computador. Ele não instala ferramentas, não
altera configurações globais e não acessa serviços remotos.

## Testes

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```

A mesma suíte é executada pelo GitHub Actions em pushes e pull requests.

## Estrutura

```text
config/                         configuração segura padrão
docs/                           visão, arquitetura e linguagem audiovisual
schemas/                        contratos JSON públicos v1
src/video_generator/domain/     modelos e invariantes puros
src/video_generator/config.py   leitura de configuração local
src/video_generator/doctor.py   diagnóstico somente leitura
tests/                          testes automatizados
inputs/, assets/                sources locais ignorados pelo Git
projects/, output/              estado e artifacts locais ignorados pelo Git
```

Consulte [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) para as fronteiras e
[AGENTS.md](AGENTS.md) para as regras permanentes de desenvolvimento.
