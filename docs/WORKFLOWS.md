# Workflows

Workflows coordenam capacidades reutilizáveis a partir de um `EditPlan`. A etapa
editorial anterior pertence ao Codex/orquestrador, que traduz `VideoRequest` e
`VideoBrief` em um plano persistido. O primeiro workflow implementado executa um
recorte temporal já decidido; ele não toma decisões editoriais.

## `segment-extract`

Esse workflow oferece o menor caminho audiovisual completo disponível:

```text
EditPlan persistido -> preflight -> FFmpeg stream copy -> ffprobe -> RenderManifest
```

O plano deve declarar exatamente um source e uma operação `extract_segment` com
início e fim. `parameters` deve permanecer vazio na schema v1 e `output_path`
deve apontar para um arquivo novo. Planos com múltiplas operações, sources extras
ou kinds desconhecidos são recusados antes de qualquer escrita.

O resultado separa execução de aprovação: um artifact que falha na validação
técnica permanece disponível para diagnóstico, mas o relatório é inválido. O
workflow persiste um `RenderManifest` com checksums, ferramentas e falhas
técnicas, mas não compõe timeline e registra a revisão visual/auditiva como não
realizada.

## Catálogo planejado

- `reel`, `tiktok` e `short`: peças verticais curtas com hook, pacing e safe
  areas específicos da plataforma;
- `youtube`: edição horizontal ou vertical orientada à intenção e duração;
- `talking-head`: edição de fala preservando significado e pausas intencionais;
- `long-form-to-shorts`: transcrição, seleção semântica e múltiplos recortes;
- `music-video`: montagem guiada pela faixa e linguagem visual;
- `music-teaser`: trecho curto que apresenta uma música com propósito;
- `visualizer`: composição visual temporalmente ligada ao áudio;
- `lyric-video`: lyrics sincronizadas com legibilidade e hierarquia;
- `embedded-captions`: captions queimadas respeitando conteúdo e safe areas.

## Contrato de workflows

Um fluxo editorial completo deve declarar inputs necessários, capacidades
opcionais, formato de output, validações e limitações. Ele deve:

1. fazer o Codex/orquestrador receber `VideoRequest` e `VideoBrief` válidos;
2. persistir um `EditPlan` antes de iniciar o workflow operacional;
3. fazer o workflow aceitar somente o shape de plano que realmente suporta;
4. não modificar sources;
5. falhar claramente quando uma ferramenta opcional estiver ausente;
6. registrar decisões e parâmetros reproduzíveis;
7. usar adapters em vez de shell improvisado;
8. separar validação técnica de julgamento editorial.

## Sequência de implementação

O próximo workflow deve ser escolhido por um caso real e implementado de ponta
a ponta em escopo pequeno. Abstrações comuns devem surgir apenas quando o
`segment-extract` e outro caso concreto demonstrarem a mesma necessidade.
