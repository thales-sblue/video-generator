# video-generator

Fundação de um agente local de produção audiovisual controlado pelo Codex. O
projeto transforma intenção e referências de mídia em contratos persistentes:

```text
VideoRequest -> VideoBrief -> EditPlan -> execução local incremental
```

O projeto já inspeciona mídia local com `ffprobe`, sem alterar o source. Edição e
renderização ainda não estão implementadas. O domínio, os schemas, a política
local-only e o diagnóstico do ambiente sustentam a evolução incremental.

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
python -m video_generator inspect inputs\clip.mp4
python -m video_generator inspect inputs\clip.mp4 --json
python -m video_generator preflight projects\example\edit-plan.json
python -m video_generator preflight projects\example\edit-plan.json --json
```

O diagnóstico apenas inspeciona o computador. Ele não instala ferramentas, não
altera configurações globais e não acessa serviços remotos.

O comando `inspect` exige `ffprobe` no `PATH`, valida que o source seja um
arquivo local e retorna formato, duração, bit rate e streams em uma representação
normalizada. A operação é somente leitura e falha claramente se a dependência
opcional estiver ausente ou a mídia não puder ser analisada.

O comando `preflight` carrega um `EditPlan` persistido, inspeciona todos os
sources declarados e recusa operações temporais sem source ou fora da duração
real. O processo é somente leitura: não cria outputs e retorna código `1` quando
o plano é válido como contrato, mas não está tecnicamente pronto para execução.

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
src/video_generator/adapters/   integrações locais, incluindo ffprobe
src/video_generator/validation/ preflight técnico de planos persistidos
src/video_generator/config.py   leitura de configuração local
src/video_generator/doctor.py   diagnóstico somente leitura
tests/                          testes automatizados
inputs/, assets/                sources locais ignorados pelo Git
projects/, output/              estado e artifacts locais ignorados pelo Git
```

Consulte [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) para as fronteiras e
[AGENTS.md](AGENTS.md) para as regras permanentes de desenvolvimento.
