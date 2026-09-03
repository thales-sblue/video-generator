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
