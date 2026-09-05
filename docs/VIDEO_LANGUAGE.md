# Linguagem audiovisual

Este documento registra princípios editoriais que devem orientar briefs,
planos, workflows e revisões. Ele deve evoluir a partir de experiência real, sem
transformar preferências contextuais em automações universais.

## Hook

- Evitar introduções vazias.
- Os primeiros segundos devem estabelecer interesse, contexto ou promessa.
- Hesitation inicial pode ser removida quando não possui valor expressivo,
  narrativo ou informativo.

## Pacing

- Silêncio não significa automaticamente corte.
- Preservar pausas intencionais, respiração e espaço emocional.
- Ritmo depende da finalidade, plataforma, gênero e audiência.
- Evitar jump cuts sem propósito; continuidade e clareza têm prioridade sobre
  densidade mecânica.

## Captions

- Priorizar legibilidade, contraste e tempo de leitura.
- Blocos curtos: uma ou duas linhas, quebradas em unidades de significado, nunca
  vários períodos agrupados.
- Respeitar safe areas e interfaces sobrepostas de cada plataforma; manter
  distância confortável da borda inferior.
- Evitar cobrir rostos, gestos e informações importantes; não deixar a caption
  dominar o quadro.
- Destaques de palavras devem ser seletivos e semanticamente úteis.
- Timing deve acompanhar unidades de significado, não apenas timestamps brutos;
  nenhuma caption deve permanecer depois da fala correspondente.

## Áudio

- Mixagem final em um alvo de loudness confortável para publicação online, sem
  clipping, com narração inteligível e trilha perceptível como atmosfera sob a
  voz.
- Início e fim com fades naturais; evitar sobra de silêncio ou de imagem sem
  função no encerramento.

## B-roll

- Deve acrescentar informação, contexto, evidência ou atmosfera.
- Não inserir apenas para preencher espaço ou ocultar todo corte.
- Manter relação clara com o que é dito, sentido ou demonstrado.

## Motion

- Movimento deve ter propósito: orientar atenção, explicar, conectar ou marcar
  hierarquia.
- Evitar zoom ou transição automática em cada corte.
- Manter linguagem visual consistente dentro da peça.
- Intensidade e frequência devem respeitar conteúdo, música e plataforma.
- Um `image_clip` aceita `motion` (`zoom_in`, `zoom_out`, `pan_left`,
  `pan_right`, `pan_up`, `pan_down`): um Ken Burns determinístico de
  deslocamento fixo (12% ao longo do clipe) sobre a imagem parada, aplicado
  depois do `fit`. Sem `motion` a imagem fica congelada. É um gesto sutil e
  único por clipe — não um efeito para repetir em todo segmento.

## Densidade e ritmo visual (`RhythmPolicy`)

O Scene/Shot Planner (ver [WORKFLOWS.md](WORKFLOWS.md)) trata ritmo e densidade
como **política/configuração**, não como lei universal — este arquivo registra
os defaults, que devem evoluir a partir de experiência real.

- **Duração de shot:** faixa ideal 2–6 s; mínimo 1,5 s; `soft_max` 8 s
  (ultrapassar exige `justification`); teto duro 10 s. A meta de referência é um
  vídeo de 4–6 min com ~50–100 eventos visuais.
- **Alternância de escala:** ciclo `wide → medium → close → detail`, no máximo 2
  shots seguidos na mesma escala.
- **Variedade de tipo de shot:** paleta ampla (b-roll humano, ambiente, objeto,
  tela, interface, documento, fotografia, gráfico simples, texto na tela,
  close/detalhe, establishing, insert, simbólico), no máximo 1 shot seguido do
  mesmo tipo. Nenhum tipo deve dominar a peça.
- **Clichês visuais desencorajados:** "cérebro", "máscara", "silhueta",
  "labirinto", "marionete" e afins não são tipos de shot — são conceitos que o
  gerador de `visual_query` *derivada* substitui por uma alternativa concreta
  para o rascunho não cair no lugar-comum. Podem existir por decisão editorial,
  mas não por inércia do planner.
- **Reuso de asset:** um mesmo source pode voltar com crop/zoom/enquadramento/
  duração diferentes, respeitando um intervalo mínimo de cenas e um teto de usos
  por source; um piso de assets distintos impede que o reuso vire baixa
  variedade.
- **Ênfase:** pontos marcados no roteiro forçam um corte e um shot `beat` na
  posição exata.

Esses valores vivem no `RhythmPolicy` (embutido no `ScenePlan`/`ShotPlan`) e são
sobrescrevíveis por um JSON parcial via `--policy`.

### Seleção de asset (`AssetScoringPolicy`)

Quando o `resolve-assets` escolhe um arquivo concreto para um shot, o critério é
explícito e auditável, não uma fórmula escondida. Cada candidato recebe um
`score_breakdown` com componentes somáveis: correspondência lexical de
`visual_query`, de `purpose` e de `visual_intent` com título/descrição/tags do
candidato; casamento de tipo (image/video) e de orientação; resolução mínima;
folga de duração para vídeo; e penalizações por repetir o mesmo arquivo e por
similaridade com shots adjacentes. Os pesos são configuração
(`--scoring-policy`), como o `RhythmPolicy`. Um reuse herdado do Shot Planner só
permanece se o shot compartilhar termos de conteúdo suficientes com o shot
âncora; uma `visual_query` pobre demais (ex.: "outras / palavras") vira
`needs_editorial_override` em vez de disparar uma busca por lixo.

### Relevância visual semântica (`RelevancePolicy`)

Um asset pode passar em todos os critérios acima e ainda ser a imagem errada.
A **Semantic Visual Relevance v1** acrescenta a pergunta que faltava — *por que
o espectador está olhando isto?* — em duas etiquetas por beat:

- **intenção visual** (`visual_intent_class`): que tipo de imagem o beat pede —
  `literal`, `metaphorical`, `emotional`, `scientific`, `evidence_or_archive`,
  `everyday_human`, `tension_or_suspense`;
- **papel visual** (`visual_role`): o que a imagem tem de fazer pelo argumento —
  `explain`, `symbolize`, `shock`, `humanize`, `contextualize`, `build_tension`,
  `support_claim`.

As duas refinam a query (*tema + intenção editorial*), somam componentes ao
ranking (afinidade de intenção, afinidade de papel, força visual) e **recusam**
o candidato editorialmente ruim: relação de uma palavra solta só
(`keyword_only_match`), catálogo genérico (`generic_stock`), humor que
contradiz a narração (`tone_conflict`), CGI abstrato onde o beat pede gente
(`abstract_cgi_mismatch`) e a mesma família visual três cortes seguidos
(`visual_language_repetition`).

### Tradução visual editorial (`TranslationPolicy`)

Ainda faltava a pergunta anterior a todas essas: **que coisa concreta poderia
ser filmada para comunicar este pensamento, neste tom, neste momento?** Sem
ela, um beat cuja frase não contém nenhum substantivo do léxico não tinha
conceito nenhum, e um beat abstrato recebia o objeto que o banco de imagens
guarda como símbolo daquela abstração — lupa para investigação, tabuleiro de
xadrez para inteligência, lâmpada para ideia, giz para criatividade.

A **Editorial Visual Translation v1** responde antes de perguntar ao provider:

- **conceito filmável** (`FilmableConcept`): uma cena inglesa concreta e
  sóbria, com o campo semântico que a produziu, as alternativas que poderiam
  tê-la substituído, as abstrações das quais aquele beat não pode receber um
  objeto, e o degrau da escada que respondeu (`semantic_field`,
  `concept_lexicon`, `intent_scene`, `tone_floor`);
- **leitura positiva do candidato** (`EditorialFit`): `human_presence`,
  `documentary_plausibility`, `concept_affinity`, `literalness_risk`,
  `playful_register`, `staged_artifice` — cada um em [0, 1] **ou** declarado
  `unknown` quando os metadados não permitem inferir.

O objeto clichê não é proibido em lugar nenhum. Ele é recusado quando é uma
**substituição preguiçosa**: o beat é abstrato, nunca citou aquele objeto, e
existe no mesmo conjunto de resultados uma alternativa documental de pé. É
essa comparação — e não uma lista de slugs — que tira um homem com lupa diante
de um espelho de um ensaio sério, e um desenho de giz colorido de um fecho
grave sem que a palavra `children` apareça em lugar algum.

Três limites deliberados. **Refinar não pode diluir**: no máximo um modificador
por eixo, e a query original permanece como fallback. **A leitura é lexical e
determinística**, não semântica de verdade: ela erra em ironia, negação e
metáfora original, e os léxicos são dados justamente para que corrigir um erro
seja uma linha. E **recusa não é aprovação**: nenhuma dessas regras substitui a
revisão editorial humana; elas só tiram do caminho o que é obviamente errado.

### Direção de arte (`VisualDirectionPolicy`)

A relevância semântica decide **qual** material entra. A **Visual Direction v1**
decide **como** ele aparece — a diferença entre sessenta boas fotografias e um
vídeo. São quatro decisões por shot, cada uma um vocabulário fechado:

- **composição** — `fullscreen` (o quadro inteiro, a afirmação neutra),
  `extreme_crop` (aproximação dura: tensão, detalhe, desconforto), `inset` (o
  asset menor dentro de uma derivação tratada dele mesmo), `layered` (uma faixa
  2.39:1 do asset sobre uma cópia desfocada e escurecida — a resposta para
  material cuja proporção o `fullscreen` estragaria), `split` (duas regiões do
  mesmo quadro lado a lado, **somente** num beat que é uma comparação) e
  `text_focus` (uma composição construída para receber texto na tela).
- **movimento** — `static_hold`, `slow_push_in`, `slow_pull_out`,
  `lateral_drift` e `detail_push` (só existe dentro de um `extreme_crop`). O
  deslocamento é pequeno de propósito: um movimento que se *vê* mexendo é um
  zoom, e zoom não é direção. **A ausência de movimento é uma decisão**, não
  uma falta.
- **grade** — a intensidade (`none` / `subtle` / `standard` / `strong`) com que
  o tratamento do canal é aplicado *a este asset*. A escolha vem da luminância
  medida do arquivo: uma fotografia já escura recebe `subtle` para não ser
  esmagada, uma clara demais recebe `strong` para ser trazida para dentro da
  peça.
- **ênfase** — se este shot carrega um `text_event`, e portanto se a composição
  precisa ser construída para segurá-lo.

Três garantias. **Determinismo**: mesma entrada + mesma policy + mesma seed →
mesmo resultado. **Controle de repetição**: nem composição nem movimento podem
formar uma sequência mais longa que a policy permite, e o que uma trava proíbe
entra na `rationale` do shot. **Portões antes de sorteio**: `text_focus` só
existe onde há ênfase, `split` só onde o beat é comparação, `extreme_crop`
nunca num quadro que não pode ser cortado (documento, gráfico, tela), e
material em movimento fica com a metade barata da gramática porque já é uma
composição.

A identidade do canal é **dados do projeto**, não código: um arquivo
`visual-direction-v1.json` com a paleta de tratamento (dessaturação, densidade
de sombras, teto de highlights, desvio frio, grain, vignette), os pesos de
composição e de movimento por `visual_role`, a geometria das composições e os
motifs. Um segundo canal é um segundo arquivo.

**Motifs** são recorrência de assunto (corredores, espaços vazios, sombras,
reflexos, mãos, escrita, documentos, telas, multidões, arquitetura, detalhes
humanos), não repetição de asset. São sinal secundário: um motif que volta
recebe um enquadramento diferente, para que a coesão temática não vire a mesma
imagem outra vez.

### Edição da imagem (`EditorialTreatmentPolicy`)

A Visual Direction dá a cada shot **um** enquadramento, **um** movimento e
**uma** grade para toda a sua duração. Isso tira sessenta fotografias do
território de slideshow, mas um asset parado por cinco ou seis segundos ainda
precisa merecer esse tempo: texto na tela é legenda sobre slide, não é edição.
A **Editorial Image Editing v1** é o beat que faltava entre a direção e o
renderer — decide **como o shot é cortado**.

Um `EditorialTreatment` é um de nove movimentos nomeados, expresso como um a três
`TreatmentState` — estados editoriais sucessivos do **mesmo** asset — mais o
tempo que divide a janela do shot entre eles:

- **`static_hold`** — um estado, sem movimento. Estabilidade proposital; o piso
  que a trava de variedade mantém.
- **`slow_push`** — um estado, push/pull lento e legível para uma explicação.
- **`punch_in`** — um estado, crop duro para dentro do quadro: afirmação forte,
  desconforto.
- **`reframe`** — dois estados, mesmo asset, enquadramento trocado num corte
  interno seco.
- **`detail_reveal`** — dois estados: um plano aberto segurado, depois um corte
  para o detalhe que empurra.
- **`freeze_emphasis`** — dois estados: aproximação, depois um quadro mais
  fechado segurado — um fato, um número — sincronizado com um pico tipográfico.
- **`two_state_cut`** — dois estados, aberto e fechado (qualquer ordem), corte
  interno seco, ambos segurados.
- **`split_compare`** — um estado, duas regiões do quadro seguradas uma contra a
  outra; só num beat que é comparação.
- **`graphic_interrupt`** — três estados: normal, um corte curtíssimo para
  dentro, um retorno reenquadrado — coordenado com a camada de tipografia.

O planejador **sorteia** o tratamento como função determinística da
**intensidade** editorial do beat (a mesma escala `low`/`medium`/`high`/`peak`
que a tipografia usa, via `classify_intensity`) e do seu **papel** no argumento,
com portões antes do sorteio (imagem vs vídeo — material em movimento só *hold*
ou corte entre janelas do próprio clipe; quadro que não pode ser cortado; shot
curto demais para um corte interno; momento de leitura puxa o movimento para
trás) e travas de variedade (nenhum tratamento em sequência mais longa que a
policy; um piso de shots deliberadamente calmos; nenhum trecho longo só de
cortes). `peak` **não** significa "mais zoom": significa mudança clara de
estado.

O renderer não inventa nada disso: `treatment_segments` materializa os estados
como operações de segmento consecutivas — cada uma com sua composição, crop,
movimento e grade — e o caminho `video-sequence` existente as renderiza. Um shot
`static_hold`, e qualquer shot sem tratamento, fica **idêntico** ao que a Visual
Direction rendeu. A identidade fica em `editorial-treatment-v1.json` no projeto.

### Ênfase na tela (`TextEvent`)

Legendas transcrevem a voz; a ênfase **argumenta** com ela. As duas camadas são
planejadas e estilizadas em separado, e a ênfase nunca repete a legenda.

- poucas, e escolhidas por importância: a abertura é tomada em ordem de tempo
  (quem ainda não decidiu ficar merece a frase forte cedo), o corpo é tomado
  pelas **melhores** frases do roteiro, não pelas primeiras que couberam;
- uma frase que não se sustenta sozinha não vai para a tela: uma palavra solta
  só passa quando é um número ou uma data;
- o acento (`#E5A33C`) marca **uma** palavra dentro da linha, não a linha
  inteira — destaque, nunca cor dominante;
- safe areas e tipografia vêm do `VisualStyle`, como as legendas.

## Veto editorial duro

Um candidato pode passar em todos os critérios de ranking e ainda destruir a
peça. Além das recusas *comparativas* (`keyword_only_match`, `generic_stock`,
`tone_conflict`, `abstract_cgi_mismatch`, `visual_language_repetition`), existe
uma família `hard_veto_*` de recusas **absolutas**: CGI genérico e render 3D
abstrato, cartoon/ilustração, imagem alegre demais, criança/sala de aula sem
necessidade semântica, neon/sci-fi, fantasia, stock comercial polido, e um
animal que o beat nunca pediu. Um beat metafórico continua recebendo metáfora;
o que ele deixa de receber é o render abstrato mais próximo que o catálogo
tinha.

Cobertura não é qualidade: um `needs_editorial_override` honesto é melhor do
que um `abstract 3D geometric waveform` num beat humano sério.

## Formato de entrega (target format)

- `target_format` no `VideoBrief`/`EditPlan` descreve o canvas final de forma
  estruturada: `width`, `height` (inteiros pares, ≤ 7680) e `fit`
  (`contain` | `cover`, default `contain`). `aspect_ratio` continua aceito, mas é
  legado/soft-deprecated — quando ambos existem, `target_format` é o que orienta
  o pipeline.
- `contain` mantém todo o conteúdo e pode introduzir letterbox/pillarbox;
  `cover` ocupa o quadro inteiro com crop central e pode perder as bordas.
- Escolher `9:16` (`1080×1920`) para shorts/reels e `16:9` (`1920×1080`) para
  YouTube landscape; presets `SHORTS_PORTRAIT` e `YOUTUBE_LANDSCAPE` no domínio.
- `cover` é apropriado quando o assunto está centrado e as bordas são
  descartáveis; `contain` quando nada pode ser cortado (texto, gráficos, planos
  compostos). Um `fit` por segmento sobrepõe o default só quando há razão
  editorial.
- Com `target_format` declarado o canvas não depende dos clipes de vídeo, então
  uma peça pode ser inteiramente de `image_clip` (narração + captions + música
  sobre imagens paradas com Ken Burns) — o formato faceless mais simples do
  `dark-video`. Sem `target_format` continua valendo a regra legada de ≥1
  `sequence_clip`.

## Critério de revisão

Uma renderização tecnicamente válida ainda pode falhar editorialmente. A revisão
deve perguntar: o hook é honesto, o ritmo preserva significado, captions ajudam,
B-roll acrescenta algo e motion serve ao conteúdo? A revisão também confere se a
peça tem originalidade e transformação suficientes para uso comercial (não é
conteúdo repetitivo, massificado ou mera reempacotagem) e se todo asset externo
tem origem e direito de uso comercial rastreáveis. Ausência dessas garantias é
falha editorial, não detalhe técnico. Limitações de inspeção devem ser
registradas, nunca disfarçadas como aprovação.
