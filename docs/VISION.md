# Visão

## Produto

`video-generator` deve permitir que uma pessoa forneça vídeos, imagens, músicas,
voz, textos e uma intenção em linguagem natural — por exemplo, “transforme esta
gravação em três Shorts” — e receba artifacts audiovisuais reproduzíveis sem
enviar sua mídia a terceiros.

O Codex funciona como cérebro editorial: entende o pedido, inspeciona assets,
analisa transcrições, escolhe o workflow e registra decisões. O projeto funciona
como motor operacional: valida contratos e executa capacidades previsíveis por
meio de ferramentas locais.

## Fluxo-alvo

```text
intenção + sources
       ↓
     Codex
       ↓
VideoRequest -> VideoBrief -> EditPlan
       ↓
workflows + adapters + renderer local
       ↓
validação -> RenderManifest -> final.mp4
```

No estado maduro, o sistema deverá inspecionar mídia, transcrever fala, analisar
conteúdo, selecionar trechos, criar captions e motion graphics, trabalhar áudio,
renderizar, verificar o resultado e corrigir problemas detectáveis.

## Critérios permanentes

- local-only é o padrão e o único custo recorrente esperado é o acesso ao Codex;
- originals são imutáveis e todos os resultados são novos artifacts;
- decisões importantes sobrevivem ao turno em contratos persistidos;
- qualidade editorial importa tanto quanto sucesso técnico;
- evolução ocorre por incrementos audiovisuais pequenos e utilizáveis.

## Fora do MVP atual

Não fazem parte da fundação: editor completo, GUI, MCP, OpenCut, ComfyUI,
geração de vídeo por IA, download de modelos grandes e provedores remotos pagos.
Integrações opcionais futuras nunca poderão enviar mídia sem autorização
explícita.
