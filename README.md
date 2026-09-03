# video-generator

Motor local-first de um agente produtor audiovisual controlado por um agente
orquestrador (atualmente o Claude Code). A
prioridade atual é `dark-video`: construir incrementalmente o menor produtor
capaz de gerar um vídeo dark completo, assistível e reproduzível. O projeto
transforma intenção e referências de mídia em contratos persistentes:

```text
VideoRequest -> VideoBrief -> EditPlan -> execução local -> RenderManifest
```

O projeto já inspeciona mídia com `ffprobe`, extrai segmentos e áudio com FFmpeg
e executa workflows estritos a partir de `EditPlan`. `video-sequence` monta
trechos de vídeo e imagens locais com duração fixa em ordem, pode queimar
captions, incorporar uma narração e música local e abrir/encerrar a imagem no
preto, produzindo H.264/AAC validado e registrado em `RenderManifest`. Um modo final separado publica `final.mp4`
somente após QA técnico. Imagens de qualquer dimensão são escaladas e
letter-boxed no canvas dos clipes e podem receber um Ken Burns determinístico
(`motion`: zoom ou pan) sobre a parada.

## Princípios

- inputs nunca são sobrescritos ou apagados;
- decisões editoriais são persistidas em contratos versionados;
- processamento é local-first; internet gratuita é permitida para pesquisa e
  assets autorizados, sem dependência de APIs pagas de geração;
- capacidades relevantes passam por decisão explícita de build vs reuse e por
  gates de licença e segurança antes de qualquer dependência nova; assets
  externos exigem origem e direito de uso comercial rastreáveis (ver
  [AGENTS.md](AGENTS.md));
- o domínio não depende de renderers nem ferramentas externas;
- FFmpeg/ffprobe cuidam de mídia de baixo nível e HyperFrames permanece planejado
  como compositor principal para layout, motion e composição visual rica.

## Requisitos

- Python 3.11 ou superior;
- Git para desenvolvimento;
- Node, FFmpeg, ffprobe, HyperFrames e Kokoro são detectados pelo `doctor`;
  FFmpeg é necessário apenas para operações de mídia e ffprobe para inspeção,
  preflight e validação técnica.

Nenhuma dependência Python de runtime é necessária. A narração local a partir de
texto (futura) é opt-in: `pip install -e .[tts]` instala `kokoro-onnx` e
`soundfile`, e os arquivos do modelo (`kokoro-v1.0.onnx`, `voices-v1.0.bin`) vão
em `.local-tools/kokoro/` (ou no diretório de `KOKORO_HOME`). Enquanto faltarem,
`doctor` reporta Kokoro como `missing` e nada mais é afetado.

O Windows x64 deste ambiente usa o build `n8.1.2-50-g1a748fe2cd-20260829` do
ramo estável 8.1, LGPL/shared, instalado somente em `.local-tools/` e ignorado
pelo Git. A origem é o projeto
[BtbN/FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds), um dos provedores
de binários Windows indicados pela página oficial de
[downloads do FFmpeg](https://ffmpeg.org/download.html). O arquivo
`config/ffmpeg-lock.json` fixa a origem, versão, arquitetura, SHA-256 do ZIP e os
hashes/tamanhos de todos os executáveis e DLLs. O projeto prefere essa instalação
local, confere integralmente o lock uma vez por processo e falha fechado diante
de arquivo ausente, adicional ou alterado; nada é adicionado ao `PATH` global.

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
python -m video_generator extract-segment inputs\clip.mp4 output\precise.mp4 --start-seconds 0.25 --end-seconds 5.25 --mode precise --json
python -m video_generator extract-audio inputs\clip.mp4 output\audio.wav --json
python -m video_generator narrate output\narration.wav --text "Primeira linha do roteiro." --voice af_heart --json
python -m video_generator narrate output\narration.wav --text-file inputs\script.txt --lang pt-br --json
python -m video_generator plan-scenes --from-text assets\desumanizando\video_01\roteiro_narracao.txt --total-duration 270 --target 1920x1080:cover --seed 20260902 --out-dir output
python -m video_generator plan-scenes --from-text assets\desumanizando\video_01\roteiro_narracao.txt --total-duration 270 --target 1920x1080:cover --seed 20260902 --overrides projects\desumanizando_01\shot-overrides.json --out-dir output --force
python -m video_generator resolve-assets --shot-plan output\shot-plan.json --asset-requirements output\asset-requirements.json --library assets\library --out-dir output\resolved-assets --json
python -m video_generator resolve-assets --shot-plan output\shot-plan.json --asset-requirements output\asset-requirements.json --library assets\library --out-dir output\resolved-assets --providers local,pexels,pixabay --require-complete --force
python -m video_generator execute-segment-plan projects\example\edit-plan.json --manifest projects\example\render-manifest.json --json
python -m video_generator execute-sequence-plan projects\example\sequence-plan.json --manifest projects\example\sequence-manifest.json --json
python -m video_generator execute-final-sequence-plan projects\example\final-plan.json --manifest projects\example\final-manifest.json --json
python -m video_generator validate-segment output\segment.mp4 --source inputs\clip.mp4 --start-seconds 0 --end-seconds 5 --file-size-bytes 123456 --json
python -m video_generator validate-audio output\audio.wav --source inputs\clip.mp4 --file-size-bytes 123456 --json
python -m video_generator validate-manifest projects\example\render-manifest.json --plan projects\example\edit-plan.json --json
python -m video_generator validate-project --request projects\example\video-request.json --brief projects\example\video-brief.json --plan projects\example\edit-plan.json --manifest projects\example\render-manifest.json --json
```

O diagnóstico apenas inspeciona o computador. Ele não instala ferramentas, não
altera configurações globais e não acessa serviços remotos.

Internet não é proibida pelo produto: o agente orquestrador pode pesquisar e
obter referências ou assets gratuitos quando autorizado. O runtime de mídia
permanece local e falha fechado para serviços externos; nenhuma API paga de
geração é dependência.

O comando `inspect` exige `ffprobe` no `PATH`, valida que o source seja um
arquivo local e retorna formato, duração, bit rate e streams em uma representação
normalizada. A operação é somente leitura e falha claramente se a dependência
opcional estiver ausente ou a mídia não puder ser analisada.

O comando `preflight` carrega um `EditPlan` persistido, inspeciona todos os
sources declarados e recusa operações temporais sem source ou fora da duração
real. O processo é somente leitura: não cria outputs e retorna código `1` quando
o plano é válido como contrato, mas não está tecnicamente pronto para execução.

O adapter `extract_segment` é a primeira operação audiovisual de baixo nível.
Por padrão, ele copia streams de um intervalo temporal para um novo arquivo;
com `mode=precise`, decodifica o intervalo e produz MP4 com H.264 por
`libopenh264` e áudio AAC. O modo preciso posiciona o seek após a leitura do
input, evitando o ajuste do início ao keyframe anterior, mas custa mais tempo e
recodifica apenas o primeiro stream de vídeo e de áudio. Ambos os modos recusam
outputs existentes ou iguais ao source e removem artifacts parciais quando
FFmpeg falha. O comando
`extract-segment` expõe essa capacidade e retorna metadata do artifact, incluindo
paths absolutos, intervalo e tamanho do arquivo, em texto ou JSON. A operação
ainda não é um workflow nem um render final e não implica revisão
visual/auditiva.

`extract-audio` separa a primeira faixa de áudio de uma mídia local em um novo
WAV PCM 16-bit, estéreo, a 48 kHz. O formato fixo fornece um artifact previsível
para escuta, análise e etapas locais futuras, sem modificar o source. O comando
recusa outputs existentes ou fora de `.wav`, remove arquivos parciais em caso de
falha e retorna paths absolutos, formato e tamanho em texto ou JSON. Ele ainda é
uma operação de baixo nível, sem `EditPlan`, `RenderManifest` ou afirmação de
revisão auditiva.

`validate-audio` verifica posteriormente se o WAV ainda tem o tamanho registrado,
container WAV, um único stream PCM 16-bit, 48 kHz, dois canais e duração positiva.
A validação é somente leitura e não representa aprovação auditiva.

`narrate` sintetiza narração local a partir de texto (`--text` ou `--text-file`
UTF-8) usando o Kokoro opcional. Valida a voz, a velocidade (0,5–2,0), o idioma e
o output `.wav` antes de tocar no modelo; resolve os assets de `.local-tools/kokoro/`
e falha fechado quando eles ou o extra `tts` faltam. A saída float do modelo é
normalizada pelo FFmpeg travado para o mesmo WAV 48 kHz/estéreo/PCM 16-bit que a
operação `narration` consome. O artifact registra voz, velocidade, idioma e o
SHA-256 do texto (o Kokoro é determinístico para essas entradas). Sucesso técnico
não é revisão auditiva: ela permanece `not_performed`.

`plan-scenes` transforma um roteiro narrado em um plano visual denso **antes** do
`EditPlan`, de forma pura e determinística (sem FFmpeg, sem download de asset, sem
LLM). Lê `--from-text` (parágrafos separados por linha em branco viram blocos) ou
`--script` (um `NarrativeScript` JSON), roda o Scene Planner e o Shot Planner com
`--seed`, e escreve `scene-plan.json`, `shot-plan.json` e
`asset-requirements.json` em `--out-dir`. O rascunho já traz dezenas de shots de
2–6 s por cena, com `shot_type`/escala alternados, durações redistribuídas de
forma *bounded* (soma exata da cena, sem shot único absorvendo o resíduo) e
`visual_query`/`purpose` *derivados* do trecho de narração de cada shot; cada
campo editorial carrega `provenance`. Um `--overrides` JSON incorpora o refino
editorial do agente (só `visual_query`/`purpose`/`shot_type`/`motion` por shot e
`visual_intent` por cena) e reescreve os três documentos. `--target WxH[:fit]`
define a orientação dos assets; `--policy` mescla um `RhythmPolicy` parcial;
`--emit-edit-plan` + `--assets` (um JSON `asset_id -> path`) converte o shot plan
para um `EditPlan` v1 pronto para o `video-sequence`. É read-only sobre
`inputs/`/`assets/` e recusa sobrescrever sem `--force`. Não renderiza MP4 e não
adquire assets — isso pertence ao `resolve-assets`.

`resolve-assets` consome `--shot-plan` + `--asset-requirements` e resolve cada
requirement num arquivo local concreto com procedência. Passos separados:
revisão semântica de reuse (um reuse estruturalmente válido só sobrevive se o
shot compartilhar termos de conteúdo suficientes com o shot âncora; senão vira
requirement próprio) → sanitização lexical da query (queries pobres como
"outras / palavras" viram `needs_editorial_override` em vez de virar busca de
lixo) → `providers.search` → ranking com `score_breakdown` inspecionável →
seleção → `acquire` **só do escolhido** → SHA-256 + validação de
tipo/dimensão/duração → `AssetProvenance`. `--library` aponta o
`LocalAssetProvider` (offline, com sidecars `<arquivo>.json`); `--providers
local,pexels,pixabay` adiciona fontes gratuitas com chave em
`PEXELS_API_KEY`/`PIXABAY_API_KEY` (sem chave, some do run). Escreve
`asset-resolution-plan.json`, `asset-provenance.json`,
`revised-asset-requirements.json` e `asset-bindings.json` (`{asset_id: path}`,
consumido direto por `shot_plan_to_edit_plan`), mais os arquivos em
`<out-dir>/files/`. Nunca sobrescreve um asset existente com bytes diferentes;
`--require-complete` sai com código 3 se sobrar requirement não resolvido. Sem
API paga, sem scraping, sem download arbitrário de vídeo, sem remoção de
watermark.

`execute-sequence-plan` aceita ao menos dois segmentos de timeline ordenados. Um
`sequence_clip` (source, início e fim) recorta um vídeo local; um `image_clip`
mostra uma imagem local por `duration_seconds` (até 600 s) sem início ou fim. Sem
`target_format` no plano, os `sequence_clip` precisam ter as mesmas dimensões e
definem o canvas (portanto pelo menos um `sequence_clip` é obrigatório); imagens
de qualquer tamanho são escaladas e letter-boxed nele e, havendo qualquer imagem,
os segmentos são normalizados para 30 fps. Com `target_format`
(`{"width":W,"height":H,"fit":"contain"|"cover"}`; W/H pares ≤ 7680, `fit` default
`contain`) o canvas é a resolução de entrega declarada — e a timeline pode ser
inteiramente de `image_clip` (nenhum `sequence_clip`), já que o canvas não
depende mais dos clipes —, e sources de resoluções/proporções diferentes podem
compor a mesma timeline: cada
segmento é escalado deterministicamente para o canvas — `contain` mantém todo o
conteúdo e adiciona letterbox/pillarbox, `cover` preenche o canvas e corta pelo
centro. `sequence_clip` e `image_clip` aceitam um `fit` opcional que sobrepõe o
default; `fit` sem `target_format` é recusado. Um `image_clip` também aceita
`motion` (`zoom_in`, `zoom_out`, `pan_left`, `pan_right`, `pan_up`, `pan_down`):
um Ken Burns determinístico via `zoompan`, deslocamento fixo, aplicado depois do
`fit`; sem `motion` a parada fica congelada. Planos sem `target_format`
serializam e fazem fingerprint exatamente como antes. Após os segmentos, uma
operação opcional `captions` pode persistir uma faixa com estilo fixo
`bottom_box`. Os cues vêm de itens inline (texto, início e fim relativos à
timeline), de um `.srt`/`.vtt` local apontado por `source` e declarado entre os
sources, ou de `{"style":"bottom_box","from":"narration"}` — que divide o texto
da narração (modo texto) em linhas curtas (até ~50 caracteres, quebra em
fronteiras de frase e oração e não deixa artigo, preposição ou conjunção sozinho
no fim da linha) e as distribui sobre a duração real da narração por
peso de sílabas e pausas estimadas, de modo que a última linha termina exatamente
com a voz. As quebras são então ancoradas nas pausas reais: um `silencedetect`
somente leitura sobre o WAV sintetizado e cada fronteira interna puxada para a
pausa mais próxima dentro de 0,5 s (uma pausa por fronteira, caindo no meio dela;
o silêncio inicial e final não conta). O timing segue **aproximado** — mede onde
a voz parou, não qual palavra foi dita; alinhamento por palavra com Whisper local
continua futuro. Em todos os casos os cues devem estar ordenados,
não podem se sobrepor, duram ao menos 1 ms, usam no máximo 160 caracteres,
recusam markup de subtitles e não podem ultrapassar o vídeo. O texto é escrito em
um `.ass` temporário com `PlayResX/Y` igual ao quadro — assim o corpo da fonte e
as margens são pixels reais e a caption fica em uma linha, numa faixa segura de
plataforma (MarginV ~10% da altura) longe das bordas — queimado via
FFmpeg/libass e removido após a execução; ele não é interpolado no filter graph.

Opcionalmente, a última operação pode ser uma `narration` com
`duration_policy=match_timeline`. Com `source` local, ela começa em zero e deve
ter duração igual à soma dos clipes dentro da tolerância (150 ms por padrão);
diferenças maiores são recusadas antes do render. Sem `source`, `parameters`
traz `text` (obrigatório) e opcionalmente `voice`/`speed`/`lang`: o workflow
sintetiza via Kokoro para um WAV temporário, recusa uma narração mais longa que
a timeline, descarta o WAV e grava o SHA-256 do texto no `RenderManifest`.
Ainda no modo texto, `lead_in_seconds` (≥ 0, menor que a timeline) atrasa a voz
para abrir só com imagem e trilha; `lead_in + duração sintetizada` precisa caber
na timeline e as captions `from=narration` são deslocadas pelo mesmo valor.
Em ambos os casos a faixa é normalizada para estéreo/48 kHz, preenchida ou
cortada até a timeline e codificada como AAC. Sem essa operação, o comportamento
silencioso anterior só é preservado quando também não há música. O MP4 final
exige exatamente um stream H.264 e, quando há áudio planejado, exatamente um
stream AAC. Preflight, fingerprints, validação e `RenderManifest` fazem parte da
mesma execução; `editorial_review` permanece `not_performed`.

Entre captions e narração, uma operação opcional `music` declara um source local
distinto e `{"duration_policy":"loop_to_timeline","gain_db":N}`, com
`fade_in_seconds`/`fade_out_seconds` opcionais (≥ 0, soma ≤ duração visual). O
ganho fica entre -60 e 0 dB. A faixa é repetida até a duração visual, recebe
`afade` de entrada/saída, é convertida para estéreo/48 kHz e, quando há voz,
mixada sem normalização de somatório; um limiter evita picos acima de 0,95.
Música sem narração também é suportada. Com uma narração no mesmo plano,
`duck_db` (de -60 a menos de 0) abaixa a trilha exatamente esse tanto enquanto a
voz fala, com rampa de 0,35 s em cada borda: a abertura e o encerramento ficam
com a música cheia e só o trecho falado é atenuado. Toda a mixagem final passa
por um fade-in curto anticlique e por normalização de loudness EBU R128 para um
alvo de publicação online (-14 LUFS integrado, true peak -1,5 dBTP), reamostrada
de volta a 48 kHz. O áudio original dos clipes permanece excluído e o output continua
contendo uma única faixa AAC.

Após todos os segmentos da timeline, uma operação opcional `fade` (sem source e
sem tempos) abre a imagem a partir do preto e/ou a fecha no preto:
`{"from_black_seconds":X,"to_black_seconds":Y}`, cada um opcional e ≥ 0, ao menos
um presente, soma ≤ duração visual. O fade é aplicado sobre o quadro já
concatenado e sobre as captions queimadas, então elas escurecem junto com a
imagem. O áudio tem os próprios fades (via `music` e a normalização final).

Para publicação, `execute-final-sequence-plan` exige que o `output_path`
persistido termine exatamente em `final.mp4`. O render é criado em um diretório
de staging ao lado do destino, passa pela validação técnica, é publicado sem
overwrite e é validado novamente no caminho final antes da criação do
`RenderManifest`. Falhas removem staging e qualquer final recém-publicado; um
`final.mp4` também é removido se seu manifest não puder ser construído ou
publicado, e um final preexistente nunca é substituído. `execute-sequence-plan` recusa esse
nome reservado para evitar que um render de trabalho seja apresentado como
final. Essa promoção não representa revisão visual ou auditiva humana.

`validate-manifest` verifica posteriormente, sem escrever arquivos, se o plano,
sources e outputs ainda correspondem aos IDs e fingerprints registrados. O
comando não exige FFmpeg/ffprobe: retorna `0` somente quando a integridade atual e
a validação técnica registrada são válidas, `1` quando há divergência ou falha
técnica registrada e `2` para contratos inválidos. A saída usa
`technically_ready` e mantém `editorial_review` separado; sucesso nunca significa
aprovação visual ou auditiva.

`validate-project` amplia essa verificação para a cadeia persistida completa:
`VideoRequest -> VideoBrief -> EditPlan -> RenderManifest`. Sem modificar mídia
ou contratos e sem exigir ferramentas externas, ele confere os vínculos por ID,
a plataforma e o workflow editorial quando declarados no request, e se todos os
sources do plano vieram do pedido. O workflow operacional do manifest permanece
separado do workflow editorial do brief. O comando retorna `0` apenas quando a
rastreabilidade, a integridade atual e a validação técnica registrada são
válidas; `editorial_review` continua explícito e independente.

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

`execute-segment-plan` é o primeiro workflow operacional. Ele aceita somente um
`EditPlan` com um source e uma operação `extract_segment`, com `start_seconds` e
`end_seconds`. `parameters` vazio usa stream copy; `{"mode": "precise"}`
seleciona o reencode preciso e exige output `.mp4`. Outros parâmetros são
recusados. O workflow executa preflight, extração
e validação nessa ordem; qualquer preflight inválido impede a criação do output.
Retorna código `0` quando o artifact passa na validação técnica, `1` quando o
artifact foi criado mas não passou e `2` quando o plano não pode ser executado.
Um artifact tecnicamente inválido é preservado para diagnóstico e nunca promovido
a render final. Tanto o sucesso quanto a falha técnica geram um `RenderManifest`
com fingerprints SHA-256 do plano, source e output, versões/caminhos de FFmpeg e
ffprobe e os códigos de validação. Sem `--manifest`, o destino padrão é
`<output>.manifest.json`. O arquivo é publicado sem overwrite e registra
`editorial_review` como `not_performed`; ele não representa aprovação
visual/auditiva.

## Testes

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```

A mesma suíte é executada pelo GitHub Actions em pushes e pull requests.

## Estrutura

```text
config/                         configuração segura padrão
config/ffmpeg-lock.json         proveniência e integridade do FFmpeg local aprovado
docs/                           visão, arquitetura e linguagem audiovisual
schemas/                        contratos JSON públicos v1 (request, brief, edit-plan, manifest, narrative-script, scene-plan, shot-plan, asset-requirements)
src/video_generator/domain/     modelos e invariantes puros; planning.py é o Scene/Shot Planner
src/video_generator/adapters/   integrações locais, incluindo ffprobe e FFmpeg
src/video_generator/workflows/  recorte e primeira timeline sequencial
src/video_generator/validation/ preflight, mídia, integridade e rastreabilidade read-only
src/video_generator/config.py   leitura de configuração local
src/video_generator/doctor.py   diagnóstico somente leitura
tests/                          testes automatizados
inputs/, assets/                sources locais ignorados pelo Git
projects/, output/              estado e artifacts locais ignorados pelo Git
```

Consulte [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) para as fronteiras e
[AGENTS.md](AGENTS.md) para as regras permanentes de desenvolvimento.
