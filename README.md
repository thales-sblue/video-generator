# video-generator

Fundação de um agente local de produção audiovisual controlado pelo Codex. O
projeto transforma intenção e referências de mídia em contratos persistentes:

```text
VideoRequest -> VideoBrief -> EditPlan -> execução local incremental
```

O projeto já inspeciona mídia local com `ffprobe` e possui uma operação interna
de extração de segmentos por stream copy com FFmpeg, sempre criando um novo
artifact. Workflows e renderização final ainda não estão implementados. O
domínio, os schemas, a política local-only e o diagnóstico do ambiente sustentam
a evolução incremental.

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
- Node, FFmpeg, ffprobe e HyperFrames são detectados pelo `doctor`; FFmpeg é
  necessário apenas para extração de segmentos e ffprobe apenas para inspeção e
  preflight.

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
python -m video_generator extract-segment inputs\clip.mp4 output\segment.mp4 --start-seconds 0 --end-seconds 5 --json
python -m video_generator validate-segment output\segment.mp4 --source inputs\clip.mp4 --start-seconds 0 --end-seconds 5 --file-size-bytes 123456 --json
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

O adapter `extract_segment` é a primeira operação audiovisual de baixo nível.
Ele copia streams de um intervalo temporal para um novo arquivo, recusa outputs
existentes ou iguais ao source e remove artifacts parciais quando FFmpeg falha.
Por usar stream copy, o início efetivo pode ser ajustado ao keyframe anterior;
essa limitação deve ser considerada por futuros renderers. O comando
`extract-segment` expõe essa capacidade e retorna metadata do artifact, incluindo
paths absolutos, intervalo e tamanho do arquivo, em texto ou JSON. A operação
ainda não é um workflow nem um render final e não implica revisão
visual/auditiva.

`validate_segment_artifact` executa a verificação técnica posterior com ffprobe.
Ela recusa outputs indisponíveis, alterados, sem streams, sem duração ou cuja
duração diverge do intervalo solicitado além da tolerância explícita. Essa etapa
torna detectável a imprecisão de keyframes do stream copy; aprovação editorial
continua sendo uma avaliação humana separada.

O mesmo comportamento está disponível em `validate-segment`. Informe o caminho
do artifact e os valores registrados por `extract_segment` (`source`, intervalo e
`file-size-bytes`); a saída JSON de `extract-segment` fornece esses valores sem
reinspecionar ou inferir o artifact. O comando de validação não modifica mídia e
retorna código `1` para um artifact tecnicamente inválido, ou `2` para argumentos
inválidos. `ffprobe` é necessário para a inspeção técnica.

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
