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

## Critério de revisão

Uma renderização tecnicamente válida ainda pode falhar editorialmente. A revisão
deve perguntar: o hook é honesto, o ritmo preserva significado, captions ajudam,
B-roll acrescenta algo e motion serve ao conteúdo? A revisão também confere se a
peça tem originalidade e transformação suficientes para uso comercial (não é
conteúdo repetitivo, massificado ou mera reempacotagem) e se todo asset externo
tem origem e direito de uso comercial rastreáveis. Ausência dessas garantias é
falha editorial, não detalhe técnico. Limitações de inspeção devem ser
registradas, nunca disfarçadas como aprovação.
