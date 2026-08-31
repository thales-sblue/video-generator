# Workflows

Workflows coordenam capacidades reutilizáveis a partir de um `EditPlan`. A etapa
editorial anterior pertence ao agente orquestrador, que traduz `VideoRequest` e
`VideoBrief` em um plano persistido. O primeiro workflow implementado executa um
recorte temporal já decidido; ele não toma decisões editoriais.

A prioridade é um único caminho incremental para `dark-video`. Workflows de
creator/talking-head, Shorts derivados de gravações e outros casos não devem ser
construídos antes de `dark-video` v1.

## `segment-extract`

Esse workflow oferece o menor caminho audiovisual completo disponível:

```text
EditPlan persistido -> preflight -> FFmpeg stream copy -> ffprobe -> RenderManifest
```

O plano deve declarar exatamente um source e uma operação `extract_segment` com
início e fim. `parameters` vazio preserva os streams por cópia rápida. Para um
recorte temporal preciso, `parameters` pode ser `{"mode": "precise"}` e o
`output_path` deve terminar em `.mp4`; esse modo reencoda o primeiro vídeo em
H.264 (`libopenh264`) e o primeiro áudio em AAC. Declarar `mode=copy` é recusado
para manter a representação canônica como objeto vazio. Planos com outros
parâmetros, múltiplas operações, sources extras ou kinds desconhecidos são
recusados antes de qualquer escrita.

O resultado separa execução de aprovação: um artifact que falha na validação
técnica permanece disponível para diagnóstico, mas o relatório é inválido. O
workflow persiste um `RenderManifest` com checksums, ferramentas e falhas
técnicas, mas não compõe timeline e registra a revisão visual/auditiva como não
realizada.

## `video-sequence`

Esse é o primeiro workflow que transforma múltiplas decisões temporais em uma
timeline renderizada:

```text
EditPlan com sequence_clip / image_clip ordenados -> preflight -> FFmpeg concat filter
              + captions opcionais
              + music opcional
              + narration opcional   -> ffprobe -> RenderManifest -> MP4
```

O plano deve declarar pelo menos dois segmentos de timeline e ao menos um
`sequence_clip`. Um `sequence_clip` tem source, início e fim. Um `image_clip`
tem source (imagem local) e `parameters={"duration_seconds": N}`, sem início ou
fim: a imagem é exibida por `N` segundos (limite de 600 s). A ordem das operações
é a ordem da timeline. Todos os sources declarados devem ser usados; parâmetros
inesperados e outputs diferentes de MP4 são recusados. Os `sequence_clip`
definem o canvas: precisam ter um stream de vídeo e dimensões iguais entre si;
clipes que não batem são recusados antes de qualquer render. Cada `image_clip` só
precisa de um stream de vídeo legível — o adapter o escala para caber e
letter-boxes (barras pretas) no canvas dos clipes.

FFmpeg recorta os clipes, zera os timestamps, concatena e reencoda o resultado em
H.264. Cada `image_clip` entra como um input `-loop 1 -t N`, é ajustado ao canvas
por `scale`/`pad` e, quando há qualquer imagem na timeline, todos os segmentos
são normalizados para 30 fps e `yuv420p` para manter o concat determinístico. O
áudio original dos clipes não entra na timeline. Depois dos segmentos, uma
operação opcional `captions` representa uma faixa inteira. Os cues vêm de uma de
duas formas:

- **inline:** sem source, `parameters` com `style=bottom_box` e de 1 a 500 itens
  com texto e tempos relativos à timeline;
- **arquivo:** `source` aponta um `.srt` ou `.vtt` local (declarado como source
  do plano, portanto com fingerprint no `RenderManifest`) e `parameters` contém
  apenas `style=bottom_box`. O arquivo é lido como UTF-8 e convertido em cues;
  tags de estilo e posicionamento (`<`, `>`, `{`, `}`) são recusadas.

Em ambos os casos cada texto tem até 160 caracteres; cues são ordenados, não
sobrepostos, duram ao menos 1 ms e são limitados à duração visual. O adapter cria
um SRT temporário, queima captions brancas em uma caixa escura via FFmpeg/libass
e sempre remove o arquivo intermediário. O texto nunca compõe o filter graph,
evitando que conteúdo editorial seja interpretado como sintaxe do FFmpeg.

Uma operação final opcional `narration`, sem tempos e com
`duration_policy=match_timeline`, adiciona voz a partir de `t=0`. Duas formas:

- **arquivo:** `source` aponta um áudio local; `parameters` só tem
  `duration_policy`. A duração deve bater com a soma dos trechos dentro da
  tolerância (150 ms por padrão); diferença maior falha antes da composição.
- **texto:** sem `source`; `parameters` adiciona `text` (obrigatório) e,
  opcionalmente, `voice`, `speed`, `lang`. O workflow chama
  `synthesize_narration` (Kokoro opcional) para um WAV temporário, recusa uma
  narração mais longa que a timeline e descarta o WAV depois; o
  `RenderManifest` guarda o SHA-256 do texto (voz/velocidade/idioma resolvidos
  no artifact), sem persistir o áudio intermediário. Kokoro ausente falha só
  esta operação.

Em ambos os casos a narração é normalizada para estéreo/48 kHz, recebe silêncio
final ou trim até a duração visual e é codificada em AAC. A validação exige
exatamente um stream H.264 e, quando há áudio planejado, exatamente um stream
AAC. Sem narração nem música, a timeline silenciosa anterior continua suportada.
Sucesso técnico não é revisão auditiva.

Antes da narração, uma operação opcional `music` declara um source de áudio
local, sem tempos próprios, e
`parameters={"duration_policy":"loop_to_timeline","gain_db":N}`. O ganho
aceita valores de -60 a 0 dB. O source deve ter exatamente um stream de áudio e
duração positiva; não precisa corresponder à timeline, pois FFmpeg o repete e
corta no fim visual. Música e voz são convertidas para estéreo/48 kHz, mixadas
com `normalize=0` e limitadas a 0,95 antes da codificação AAC. Sem voz, a música
sozinha ocupa a faixa AAC. O áudio original dos clipes nunca entra no mix.

Esse escopo prova `EditPlan -> timeline -> captions/áudio -> composição -> MP4`
sem substituir HyperFrames, que permanece o compositor planejado para imagens,
layout, motion e captions avançadas. O próximo gap deve ampliar este caminho
rumo a um vídeo dark completo, não criar outro workflow.

Quando o plano persiste `output_path` com o nome canônico `final.mp4`, o comando
`execute-final-sequence-plan` renderiza primeiro em staging no mesmo diretório.
Somente um resultado tecnicamente válido é publicado, de forma exclusiva e sem
overwrite; o arquivo publicado é validado novamente antes do `RenderManifest`.
Se qualquer validação falhar, staging e eventual final recém-criado são
removidos; falha na construção ou publicação do manifest também remove o final
sem rastreabilidade. O comando comum recusa o nome reservado `final.mp4`. Essa fronteira é
QA técnico, não aprovação editorial: `editorial_review=not_performed` permanece
explícito.

## Evolução planejada do `dark-video`

Capacidades que ainda faltam para `dark-video` v1:

- derivar captions do timing da narração, não de timestamps manuais;
- executar a primeira produção real completa e registrar sua revisão humana.

Já disponível:

- `image_clip` insere imagens locais com duração explícita na timeline de
  `video-sequence`; qualquer dimensão é aceita e a imagem é escalada e
  letter-boxed no canvas dos clipes (sem movimento/Ken Burns nesta v1);
- `captions` aceita um `.srt`/`.vtt` local como source, além dos itens inline —
  precursor de "captions a partir da narração", que passará a emitir esse arquivo.
- narração local a partir de texto: Kokoro opcional (extra op-in `tts`, assets
  fail-closed em `.local-tools/kokoro/`, check no `doctor`), o adapter
  `synthesize_narration`, a CLI de baixo nível `narrate` e o **modo texto da
  operação `narration`** no `video-sequence` (`text` + `voice`/`speed`/`lang`
  opcionais; WAV temporário, SHA-256 do texto no `RenderManifest`). Kokoro
  ausente falha só essa capacidade. **Pendente:** revisão auditiva real.

### Ordem recomendada dos próximos incrementos

Sequência sugerida (cada item ainda deve passar pela pergunta do menor
incremento; nada aqui autoriza pular testes ou camadas):

1. **Captions a partir da narração** — gerar um `.srt` a partir do alinhamento do
   TTS (ou de Whisper local) e alimentá-lo pela operação `captions` já existente,
   respeitando `VIDEO_LANGUAGE.md` (timing por unidades de significado).
2. **Primeiro adapter HyperFrames** — provar `EditPlan -> cena HTML -> MP4` com um
   title card / camada de captions. Avaliar se movimento (Ken Burns), fit
   configurável e composição visual rica saem mais baratos por HTML do que por
   `zoompan`/`tpad` no FFmpeg; se sim, HyperFrames passa a ser o caminho de
   imagens ricas e captions avançadas, deixando o `image_clip` atual (escala +
   letterbox estático) para o caso simples. HyperFrames exige
   Node e Chrome headless e deve receber um lock de proveniência análogo a
   `config/ffmpeg-lock.json`.
3. **Primeira produção real completa** com registro de revisão humana — fecha
   `dark-video` v1.

Pesquisa, roteiro, storyboard e assets automáticos vêm depois do primeiro vídeo
completo. O workflow de creator/talking-head permanece futuro e deverá reutilizar
o mesmo motor.

## Contrato de workflows

Um fluxo editorial completo deve declarar inputs necessários, capacidades
opcionais, formato de output, validações e limitações. Ele deve:

1. fazer o agente orquestrador receber `VideoRequest` e `VideoBrief` válidos;
2. persistir um `EditPlan` antes de iniciar o workflow operacional;
3. fazer o workflow aceitar somente o shape de plano que realmente suporta;
4. não modificar sources;
5. falhar claramente quando uma ferramenta opcional estiver ausente;
6. registrar decisões e parâmetros reproduzíveis;
7. usar adapters em vez de shell improvisado;
8. separar validação técnica de julgamento editorial.

## Sequência de implementação

O próximo incremento deve ser escolhido pela pergunta: “qual é o menor
incremento funcional que mais nos aproxima do primeiro vídeo dark completo?”.
Não criar novos workflows quando ampliar `video-sequence` resolve o gargalo.
