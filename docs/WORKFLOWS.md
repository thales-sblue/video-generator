# Workflows

Workflows traduzem um `VideoBrief` em operações de um `EditPlan` usando
capacidades reutilizáveis. Esta fundação apenas reserva suas fronteiras; nenhum
workflow de edição está implementado.

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

## Contrato de um workflow futuro

Um workflow deve declarar inputs necessários, capacidades opcionais, formato de
output, validações e limitações. Ele deve:

1. receber `VideoRequest` e `VideoBrief` válidos;
2. produzir um `EditPlan` persistível antes de executar;
3. não modificar sources;
4. falhar claramente quando uma ferramenta opcional estiver ausente;
5. registrar decisões e parâmetros reproduzíveis;
6. usar adapters em vez de shell improvisado;
7. separar validação técnica de julgamento editorial.

## Sequência de implementação

O próximo workflow deve ser escolhido por um caso real e implementado de ponta
a ponta em escopo pequeno. Não criar abstrações comuns até que pelo menos um
workflow revele a necessidade concreta.
