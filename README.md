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

Nenhuma dependência Python de runtime é necessária. Duas capacidades locais são
opt-in e falham fechado, sem afetar contratos, planejamento ou diagnóstico:

- **Narração** — `pip install -e .[tts]` instala `kokoro-onnx` e `soundfile`, e os
  arquivos do modelo (`kokoro-v1.0.onnx`, `voices-v1.0.bin`) vão em
  `.local-tools/kokoro/` (ou no diretório de `KOKORO_HOME`).
- **Alinhamento de legendas** — `pip install -e .[align]` instala
  `faster-whisper`, e um modelo Whisper convertido para CTranslate2 (os quatro
  arquivos `model.bin`, `config.json`, `tokenizer.json`, `vocabulary.txt`) vai em
  `.local-tools/whisper/<modelo>/` (ou no diretório de `WHISPER_HOME`). O modelo é
  carregado com download desabilitado: nada sai da máquina em tempo de render.

Enquanto faltarem, `doctor` reporta `Kokoro` / `Aligner` como `missing` e nada
mais é afetado.

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
python -m video_generator narrate output\narration.wav --text-file inputs\script.txt --lang pt-br --voice pm_alex --prosody --lead-in-seconds 0.6 --units-out output\narration-units.json --json
python -m video_generator align-captions output\narration.wav --text-file inputs\script.txt --out output\captions.srt --language pt --words-out output\narration-words.json --json
python -m video_generator review-cuts videos\2026-09-10.mp4 --project canal_dev_01 --language pt --json
python -m video_generator plan-scenes --from-text assets\desumanizando\video_01\roteiro_narracao.txt --total-duration 270 --target 1920x1080:cover --seed 20260902 --out-dir output
python -m video_generator plan-scenes --from-text assets\desumanizando\video_01\roteiro_narracao.txt --total-duration 270 --target 1920x1080:cover --seed 20260902 --overrides projects\desumanizando_01\shot-overrides.json --out-dir output --force
python -m video_generator plan-scenes --from-text output\preview\roteiro.txt --total-duration 68.347 --target 1920x1080:cover --seed 20260903 --semantic --hook-seconds 40 --text-events output\preview\text-events.json --out-dir output\preview\plan
python -m video_generator resolve-assets --shot-plan output\shot-plan.json --asset-requirements output\asset-requirements.json --library assets\library --out-dir output\resolved-assets --json
python -m video_generator resolve-assets --shot-plan output\shot-plan.json --asset-requirements output\asset-requirements.json --library assets\library --out-dir output\resolved-assets --providers local,pexels,pixabay --require-complete --force
python -m video_generator render-screens --deck projects\canal_dev_01\screens.json --out-dir output\canal-dev-01\screens --layout-only
python -m video_generator render-screens --deck projects\canal_dev_01\screens.json --out-dir output\canal-dev-01\screens --force --json
python -m video_generator curate-visuals --set projects\canal_dev_01\visual-curation-set.json --review-out output\canal-dev-01\visual-curation\review.html
python -m video_generator visual-lock --set projects\canal_dev_01\visual-curation-set.json --approvals approvals.txt --approved-by "seu nome" --out projects\canal_dev_01\visual-lock.json
python -m video_generator verify-visual-lock --lock projects\canal_dev_01\visual-lock.json --json
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

`narrate --prosody` fala o roteiro **unidade por unidade** em vez de sintetizar o
texto inteiro como um bloco homogêneo. `domain/prosody.py` decide as unidades a
partir da pontuação e do formato dos parágrafos: um parágrafo curto isolado vira
um *beat* (falado mais devagar, com silêncio antes e depois, para a frase de
impacto aterrissar), uma sequência de frases curtas vira uma *run* (falada um
pouco mais rápido, porque uma lista perde o ritmo se cada fragmento for
sintetizado e preenchido separadamente), e o resto recebe a pausa de frase ou de
parágrafo. O adapter FFmpeg junta as unidades com exatamente o silêncio pedido
(`join_audio_segments`) e `--lead-in-seconds` abre a faixa com silêncio, para a
timeline começar numa imagem antes da voz. `--units-out` grava onde cada unidade
caiu no WAV final, **medido** com ffprobe, não estimado.

`align-captions` cronometra o roteiro contra a narração já renderizada. Transcreve
o WAV com o Whisper local (`word_timestamps`), casa a sequência de palavras
reconhecidas com a do roteiro por diff de blocos iguais — acentos e caixa são
normalizados — e ancora cada legenda no span medido da sua primeira e da sua
última palavra. Trechos que a transcrição não alcançou são interpolados entre as
âncoras vizinhas, então uma falha de reconhecimento não desloca nada fora dela.
Uma legenda segura até 1,2 s dentro da pausa seguinte em vez de piscar. Como um
decoder costuma reportar a primeira palavra em 0,0 mesmo quando a gravação abre
em silêncio, o comando mede o silêncio inicial com `silencedetect` e nunca põe a
primeira legenda antes da voz. Diferente de `captions_from_text`, isto não modela
o ritmo — lê o ritmo do áudio.

`review-cuts` é a primeira camada da **edição assistida de vídeos gravados por
você**: recebe um vídeo (ou áudio) bruto, extrai o WAV (`extract-audio`),
transcreve a fala em frases com timestamps pelo Whisper local
(`transcribe_segments`, mesmo modelo travado do `align-captions`, offline) e mede
os silêncios (`silencedetect`). O `domain/takes.py` — puro, sem I/O — lê essas
medições e marca cada trecho `KEEP`, `REVIEW` ou `CUT` com motivo legível e
confiança em `0..1`. Heurísticas v1: pausa longa (vira uma linha `CUT` própria),
frase repetida logo em seguida (corta a tentativa anterior), recomeço de
explicação ("na verdade…", "deixa eu…"), frase iniciada e abandonada, e vício de
linguagem em excesso (só `REVIEW`). **Nada é removido e nenhum render acontece**:
`CUT` é sugestão, a decisão é sua. A saída vai para `projects/<slug>/` com
`--project` (ou `--out-dir <dir>`): `cut-review.json` (contrato publicado
`schemas/cut-review-v1.schema.json`), `cut-review.md` (uma folha legível, um bloco
por trecho) e `source-audio.wav`. Recusa sobrescrever qualquer um dos três.
`--language` (padrão `pt`), `--model` e `--long-silence` (padrão 1,5 s) ajustam a
passada. Cortes, zoom, B-roll, legendas e motion typography sobre a sua gravação
são camadas futuras.

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

`--semantic` liga a camada editorial (`domain/editorial.py`): cada fatia de
narração é lida como um **`NarrationBeat`** — conceito, entidades, emoção,
intenção visual, papel editorial e uma lista ordenada de queries candidatas — e
essa leitura passa a decidir o `visual_query` do shot, as alternativas
(`asset_queries`) e o `shot_type` (como *viés* no sorteio, nunca como imposição:
os limites de repetição continuam garantindo variedade). Duas escolhas
deliberadas: **as queries saem em inglês** enquanto narração e `visual_intent`
ficam no idioma do roteiro, porque todo banco gratuito que o projeto alcança
indexa em inglês e uma query em português é comparada contra metadata inglesa e
pontua perto de zero; e **um termo que aparece em mais de 25% dos beats nunca
lidera uma query**, porque ele é o assunto do vídeo inteiro, não daquele trecho —
é isso que impede que todo shot de um vídeo sobre Einstein peça "Einstein". Sem
`--semantic` o planner é byte-a-byte o de antes, e os campos novos ficam ausentes.

`--visual-relevance` (exige `--semantic`) liga a **Semantic Visual Relevance
v1** (`domain/relevance.py`), a camada entre o beat editorial e a escolha de
asset. A camada semântica já dizia *sobre o que* é o corte; esta diz *por que o
espectador está olhando aquilo*, em três passos determinísticos:

1. **`visual_intent_class`** — que tipo de imagem o beat pede: `literal`,
   `metaphorical`, `emotional`, `scientific`, `evidence_or_archive`,
   `everyday_human` ou `tension_or_suspense`.
2. **`visual_role`** — o que a imagem tem de fazer pelo argumento: `explain`,
   `symbolize`, `shock`, `humanize`, `contextualize`, `build_tension` ou
   `support_claim`.
3. **`refined_query`** — a query em inglês reescrita como *tema + intenção
   editorial* (`empty classroom` → `moody dark empty classroom`). No máximo um
   modificador por eixo e só enquanto a query couber em
   `RelevancePolicy.max_query_words` (7): a busca pontua por sobreposição de
   termos, então cada palavra que não carrega significado derruba o rank das
   que carregam. A query original continua como fallback — refinar nunca torna
   um beat impossível de buscar.

Os dois rótulos viajam no `Shot` e no `AssetRequirement`, e por isso o
`resolve-assets` passa a ranquear e a **recusar** candidatos por adequação
editorial, não só por vocabulário (ver abaixo). Sem `--visual-relevance` nada
disso acontece: os campos ficam `null`, e queries e ritmo continuam byte-a-byte
os mesmos.

`--editorial-translation` (exige `--visual-relevance`) liga a **Editorial
Visual Translation v1** (`domain/visual_concept.py`), a camada que faltava
*antes* da query. A relevância dizia que tipo de imagem o beat pede; esta
responde a pergunta anterior — **que coisa concreta poderia ser filmada para
comunicar este pensamento, neste tom, neste momento?** — e é a resposta que vai
para o provider.

O motivo é medido. No corte de Visual Direction v1, 17 dos 59 shots não tinham
conceito filmável nenhum (o léxico é indexado por substantivo isolado, e uma
frase de ensaio não tem nenhum), e a query que chegava ao Pexels era um
substantivo português solto — `momento`, `informacao`, `gostamos`. Outros 13
pediam o **objeto clichê pelo nome**, porque é isso que o léxico guarda:
`verdade` → *magnifying glass over a document* (seis lupas num corte, uma
delas na mão de um homem diante de um espelho), `inteligencia` → *chess board*
(quatro tabuleiros), `pergunta` → *question mark chalked on a board* (giz
colorido na calçada sob a frase mais forte do fecho).

A camada tem duas metades:

- **Lado do beat.** `translate_beat` devolve um `FilmableConcept`: uma cena
  inglesa, concreta e autorada, escolhida num banco de **campos semânticos** —
  conjuntos de vocabulário português que nomeiam um movimento recorrente do
  argumento (autoengano, crença e identidade, atenção algorítmica, escrutínio
  da evidência…), cada um oferecendo várias cenas diferentes que podem
  carregá-lo. A escada é **léxico primeiro** (a resposta concreta do
  substantivo que a fala realmente disse é mais fiel que qualquer campo) e o
  campo assume nos três casos em que o léxico é o problema: não há acerto,
  a resposta é um objeto clichê que o beat nunca citou, ou aquela mesma cena
  já respondeu a um beat anterior. Abaixo do campo vêm a cena de intenção e o
  piso de tom. **Todo degrau é inglês autorado**, e é isso que remove a query
  em português por construção, não por filtro.
- **Lado do candidato.** `assess_editorial_fit` lê o candidato
  *positivamente*: presença humana, plausibilidade documental, afinidade com o
  conceito escolhido — e, do lado negativo, metáfora de banco de imagens que o
  beat não pediu, registro lúdico e aparência de estúdio. Um sinal que os
  metadados não sustentam é reportado como **unknown**, nunca fabricado. As
  duas recusas novas (`stock_metaphor_cliche`, `playful_register_conflict`)
  são **comparativas**: só valem enquanto existir uma alternativa sóbria de
  pé, então um beat cujo resultado inteiro é clichê ainda recebe imagem.

Isso complementa os vetos duros, não os substitui: o veto diz que a imagem é
inutilizável em qualquer lugar; esta camada decide entre as que sobraram. Sem
`--editorial-translation` nada disso acontece e o plano continua byte-a-byte o
mesmo.

`--hook-seconds N` dá aos primeiros N segundos um teto de duração mais curto e
proíbe reuso de asset ali (o piso e o jitter continuam: abertura cortada num
metrônomo de 2 s é monotonia, não ritmo). `--text-events <path>` escreve a camada
de **ênfase editorial** — que não é legenda: a legenda transcreve a voz, o
`TextEvent` levanta um número, uma data ou uma virada do argumento na tela, com
categoria, hierarquia, posição e animação próprias, sempre com palavras
literais da narração. Poucos beats viram evento: é preciso importância
suficiente **e** distância do evento anterior. Com `--emit-edit-plan`, a camada
entra no plano como uma operação `text_events` carregando o `VisualStyle`
(`dark-documentary-v1`: fundo escuro, texto claro, um único accent `#E5A33C`,
tipografia e margens de segurança). A identidade veste o asset escolhido — ela
nunca escolhe o asset: a ordem é relevância semântica primeiro, estética depois.

`--motion-typography <path>` (exige `--semantic`) liga a **Editorial Motion
Typography v1** (`domain/typography.py`) **no lugar** de `--text-events`: o
texto na tela deixa de ser uma linha de ênfase e passa a ser uma composição
tipográfica. O pipeline é
`narração -> ênfase -> conceito de texto -> papel -> layout -> motion -> timing`:

- **papéis** — `hook`, `keyword`, `contrast`, `statement`, `question`;
- **layouts** — `dominant_word`, `stacked_hierarchy`, `small_plus_massive`,
  `split_statement`, `edge_aligned`, `centered_poster`, `contrast_pair`;
- **motions** — `fade_rise`, `scale_in`, `masked_reveal`, `stagger_rise`
  (um bloco por linha de `Dialogue`, então o stagger é real: a linha pequena já
  está legível quando a palavra grande chega);
- **pesos** — `micro`, `small`, `large`, `massive`, e nenhum evento de mais de
  um bloco pode usar um peso só. O contraste de escala é a camada.

A voz carrega a informação, então a camada é **rara**: ~18 intervenções em
209 s, não uma por frase. E o texto é **derivado**, nunca recortado: uma palavra
dominante mais um conector licenciado (só dispara se a própria deixa está na
frase) ou uma segunda palavra de conteúdo; `_is_derived` recusa qualquer
composição que reproduza um trecho contíguo da narração. Com
`--narration-captions <srt>` cada evento é cravado no instante medido em que a
palavra-âncora é falada, em vez de no shot que a contém. `--typography-policy`
traz a policy do canal (cadência, piso de importância) e, num objeto `style`,
as duas fontes — uma display pesada para a palavra, uma leve para a linha de
apoio. Sem a flag nada muda: o plano continua emitindo `text_events`.

`--visual-direction <policy.json>` (exige `--semantic`) liga a **Visual
Direction v1** (`domain/direction.py`): a camada que decide **como** o asset
escolhido aparece. A relevância semântica escolhe o material; esta escreve a
apresentação dele, quatro decisões por shot, cada uma um vocabulário fechado e
serializado em `visual-direction.json`:

- **`composition`** — `fullscreen`, `extreme_crop`, `inset`, `layered`, `split`
  ou `text_focus` (ver [docs/VIDEO_LANGUAGE.md](docs/VIDEO_LANGUAGE.md));
- **`motion`** — `static_hold`, `slow_push_in`, `slow_pull_out`,
  `lateral_drift` ou `detail_push`, com deslocamento pequeno e determinístico;
- **`grade`** — a intensidade (`none`/`subtle`/`standard`/`strong`) do
  tratamento do canal aplicada **àquele arquivo**, escolhida a partir da
  luminância medida dele com um decode de um pixel (`--no-luma` desliga a
  medição e usa o default da policy);
- **`emphasis`** + **`visual_motif`** + uma `rationale` que nomeia o papel
  visual, o portão e a trava de repetição que produziram cada valor.

Mesma entrada, mesma policy e mesma seed dão o mesmo plano. Com
`--emit-edit-plan` cada segmento do `EditPlan` passa a carregar
`composition`/`crop_bias`/`text_zone`/`grade`/`motion`, e o plano ganha uma
operação `visual_direction` com os números do tratamento — uma vez para a
timeline inteira, em vez dos mesmos vinte números em sessenta segmentos. O
adapter FFmpeg traduz isso em filtros reais (`crop` deslocado, `split`+`gblur`+
`overlay` para inset/layered/split, `drawbox` escalonado para o scrim de texto,
`hue`+`curves`+`colorbalance`+`noise`+`vignette` para a grade, `zoompan` para o
movimento). Sem a flag nada muda: o plano sai byte-a-byte como antes.

A policy é do projeto, não do domínio —
`projects/desumanizando_01/visual-direction-v1.json` traz a paleta de
tratamento, os pesos de composição e movimento por `visual_role`, a geometria
das composições e os motifs. Um segundo canal é um segundo arquivo.

```powershell
python -m video_generator plan-scenes --from-text projects\desumanizando_01\roteiro-v2.txt `
  --total-duration 209.261 --policy projects\desumanizando_01\rhythm-policy-v2.json `
  --seed 0 --target 1920x1080:cover --semantic --visual-relevance `
  --visual-direction projects\desumanizando_01\visual-direction-v1.json `
  --overrides projects\desumanizando_01\shot-overrides-visual-direction-v1.json `
  --text-events output\visual-direction-v1\text-events.json `
  --assets output\visual-direction-v1\resolved\asset-bindings.json `
  --emit-edit-plan output\visual-direction-v1\edit-plan-base.json `
  --out-dir output\visual-direction-v1\plan --force
```

Um `--overrides` é um **patch local**: ele muda o campo nomeado do shot nomeado
e nada mais. O shot corrigido mantém `visual_intent_class`, `visual_role`,
`refined_query`, `editorial_role` e `beat_concept`, e a `visual_query` autorada
apenas passa a liderar a lista de fallbacks daquele shot. (Até este ciclo,
qualquer override apagava a leitura semântica do plano **inteiro**.)

`resolve-assets` consome `--shot-plan` + `--asset-requirements` e resolve cada
requirement num arquivo local concreto com procedência. Passos separados:
revisão semântica de reuse (um reuse estruturalmente válido só sobrevive se o
shot compartilhar termos de conteúdo suficientes com o shot âncora; senão vira
requirement próprio) → sanitização lexical da query (queries pobres como
"outras / palavras" viram `needs_editorial_override` em vez de virar busca de
lixo) → `providers.search` **por query candidata, na ordem, parando na primeira
que encontra candidato com significado compartilhado** → ranking com
`score_breakdown` inspecionável →
seleção → `acquire` **só do escolhido** → SHA-256 + validação de
tipo/dimensão/duração → `AssetProvenance`. `--library` aponta o
`LocalAssetProvider` (offline, com sidecars `<arquivo>.json`); `--providers
local,pexels,pixabay` adiciona fontes gratuitas com chave em
`PEXELS_API_KEY`/`PIXABAY_API_KEY` (sem chave, some do run). Escreve
`asset-resolution-plan.json`, `asset-provenance.json`,
`revised-asset-requirements.json` e `asset-bindings.json` (`{asset_id: path}`,
consumido direto por `shot_plan_to_edit_plan`), mais os arquivos em
`<out-dir>/files/`. Cada `ResolvedAsset` registra `matched_query` (qual das
queries candidatas achou o arquivo) e `sanitized_query` (o que de fato foi
pedido ao provider), então "por que esse shot ficou assim?" tem resposta de uma
linha. Um requirement sem `queries` se comporta exatamente como antes; nesse
caso, e só nesse, o `purpose` continua enriquecendo a query — uma query
candidata já é uma frase visual deliberada e misturar o vocabulário da narração
nela só dilui a busca. Nunca sobrescreve um asset existente com bytes diferentes;
`--require-complete` sai com código 3 se sobrar requirement não resolvido. Sem
API paga, sem scraping, sem download arbitrário de vídeo, sem remoção de
watermark.

Quando o requirement carrega os rótulos da Semantic Visual Relevance, o ranking
ganha componentes de **adequação editorial** — afinidade com o `visual_intent_class`,
afinidade com o `visual_role` e força visual — e passa a **recusar** o candidato
editorialmente ruim, mesmo que ele pontue alto no lexical:

| motivo | o que ele pega |
| --- | --- |
| `keyword_only_match` | a relação com o shot é uma palavra solta, e nada do `purpose` |
| `generic_stock` | catálogo genérico (`business`, `handshake`, `corporate`, `mockup`…) |
| `tone_conflict` | imagem alegre num beat sombrio, de tensão ou de choque |
| `abstract_cgi_mismatch` | CGI abstrato de cérebro/neurônio/rede num beat humano ou emocional |
| `visual_language_repetition` | a mesma *família visual* pela terceira vez seguida |

Recusa é diferente de desqualificação: `disqualified_reasons` diz que o arquivo
não **serve** (tipo, duração, resolução); `rejection_reasons` diz que ele não
**pertence** àquele beat. Se todos os candidatos caírem por recusa editorial, o
requirement fica `editorially_rejected` — a correção é outra imagem, não outra
query. O resumo do comando traz `relevance_rejections` com a contagem por regra.
Um requirement sem rótulos ranqueia exatamente como antes.

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

## `render-screens` — o material do developer-video

O workflow `dark-video` responde "que imagem vai aqui?" com um provedor de
assets. Um vídeo de desenvolvedor sobre um projeto real responde com o próprio
repositório: uma linha de código, uma sessão de terminal, um fragmento de JSON,
uma comparação, um número.

`render-screens` consome um **screen deck** (`schemas/screen-deck-v1.schema.json`)
e renderiza cada card como um still 1920x1080. O layout é puro
(`domain/screens.py`): ele decide onde cada glifo cai e **recusa** um card que
não caberia no quadro, em vez de renderizar um quadro truncado. O adapter
(`adapters/screens.py`) só desenha — cada bloco de texto é escrito em um arquivo
UTF-8 próprio dentro de um diretório temporário e o FFmpeg roda com esse
diretório como working directory, de modo que **nenhum texto de card entra no
filtergraph**.

Oito tipos de card: `code`, `terminal`, `json`, `statement`, `chain`, `compare`,
`stat` e `list`.

Os PNGs resultantes entram no `EditPlan` como `image_clip` comuns, ao lado de
`sequence_clip` recortados de renders anteriores. Nenhuma mudança foi
necessária no renderer: uma timeline sem `narration` e sem `music` já é
validada como silenciosa (`unexpected_non_video_streams`), então um corte
visual-only não precisa de WAV silencioso.

O primeiro caso real é `scripts/build-canal-dev-01.py`, que monta o vídeo
`canal_dev_01` — a história deste repositório contada com material dele mesmo.

## `curate-visuals` / `visual-lock` — a curadoria antes do render

O ciclo antigo era `render -> descobrir escolhas ruins -> refazer`. A etapa de
curadoria visual troca isso por
`planejamento -> candidatos -> aprovação humana -> visual lock -> render`.

`domain/curation.py` (puro, stdlib) responde três perguntas:

1. **quais sequências exigem um humano?** Material real do projeto e diagramas
   feitos a partir dele são evidência e entram direto. Asset externo, stock,
   metáfora, composição gráfica e escolha entre frames plausíveis são decisão.
2. **o candidato é editorialmente relevante ou só semanticamente relacionado?**
   `editorial_gate` recusa query em português, query que é palavra-chave e não
   cena, metadado técnico virando imagem (`PT-BR` → bandeira do Brasil) e os
   fallbacks genéricos proibidos por nome.
3. **o que o humano aprovou de fato?** `parse_approvals` lê `SEQ 03 -> B` e
   `build_visual_lock` congela; sequência não respondida ou marcada
   `regenerar` **impede** o lock.

```powershell
$env:PYTHONPATH = "src"
python scripts\build-canal-dev-01-curation.py
python -m video_generator curate-visuals --set projects\canal_dev_01\visual-curation-set.json --review-out output\canal-dev-01\visual-curation\review.html
# abrir o review.html, responder uma linha por sequência em approvals.txt
python -m video_generator visual-lock --set projects\canal_dev_01\visual-curation-set.json --approvals approvals.txt --approved-by "seu nome" --out projects\canal_dev_01\visual-lock.json
python -m video_generator verify-visual-lock --lock projects\canal_dev_01\visual-lock.json --json
```

O `review.html` é um arquivo único e autossuficiente (previews embutidos como
data URI), para não ser preciso abrir dezenas de imagens uma a uma. Os contratos
públicos são `schemas/visual-curation-set-v1.schema.json` e
`schemas/visual-lock-v1.schema.json`.

Depois de aprovado, o asset não é substituído em silêncio:
`verify-visual-lock` compara o SHA-256 de cada arquivo aprovado e sai com 1 se
algum sumiu ou mudou.


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
schemas/                        contratos JSON públicos v1 (request, brief, edit-plan, manifest, narrative-script,
                                scene-plan, shot-plan, asset-requirements, screen-deck, visual-curation-set, visual-lock,
                                cut-review)
src/video_generator/domain/     modelos e invariantes puros; planning.py é o Scene/Shot Planner,
                                editorial.py a camada semântica (beats, ênfases, identidade)
                                screens.py o layout dos screen cards do developer-video,
                                takes.py as sugestões de corte para vídeo gravado
                                e curation.py o checkpoint humano antes do render
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
