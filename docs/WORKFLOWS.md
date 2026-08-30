# Workflows

Workflows coordenam capacidades reutilizáveis a partir de um `EditPlan`. A etapa
editorial anterior pertence ao Codex/orquestrador, que traduz `VideoRequest` e
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
EditPlan com sequence_clip ordenados -> preflight -> FFmpeg concat filter
                                      -> ffprobe -> RenderManifest -> MP4
```

O plano deve declarar pelo menos dois `sequence_clip`, cada um com source,
início e fim. A ordem das operações é a ordem da timeline. Todos os sources
declarados devem ser usados; parâmetros e outputs diferentes de MP4 são
recusados. A v1 exige um stream de vídeo e dimensões iguais entre sources.

FFmpeg recorta, zera os timestamps, concatena e reencoda o resultado em H.264. O
artifact é propositalmente silencioso: áudio dos sources não é carregado para a
timeline. A validação exige um único stream de vídeo, nenhum stream adicional,
tamanho inalterado e duração igual à soma dos trechos dentro da tolerância.

Esse escopo prova `EditPlan -> timeline -> composição -> MP4` sem substituir
HyperFrames, que permanece o compositor planejado para imagens, layout, motion,
captions e composição visual mais rica. O próximo gap deve ampliar este caminho
rumo a um vídeo dark completo, não criar outro workflow.

## Evolução planejada do `dark-video`

- aceitar imagens com duração explícita na timeline;
- receber ou gerar narração local e sincronizá-la à sequência;
- gerar e queimar captions legíveis;
- adicionar música opcional e mixar volumes básicos;
- promover o render validado a `final.mp4` com QA técnico.

Pesquisa, roteiro, storyboard e assets automáticos vêm depois do primeiro vídeo
completo. O workflow de creator/talking-head permanece futuro e deverá reutilizar
o mesmo motor.

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

O próximo incremento deve ser escolhido pela pergunta: “qual é o menor
incremento funcional que mais nos aproxima do primeiro vídeo dark completo?”.
Não criar novos workflows quando ampliar `video-sequence` resolve o gargalo.
