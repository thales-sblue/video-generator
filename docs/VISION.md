# Visão

## Produto

`video-generator` será um agente produtor audiovisual controlado pelo Codex. O
Codex é o cérebro editorial e orquestrador; o projeto é o motor operacional
previsível, auditável e reproduzível.

A visão madura inclui pesquisa, roteiro, storyboard, assets, narração, timeline,
captions, áudio, render, validação, revisão e correção de problemas detectáveis.
Ela não é o escopo de um único ciclo: cada incremento deve entregar a menor
capacidade funcional que reduza a distância até um vídeo completo.

## Prioridade: `dark-video`

A prioridade absoluta é produzir vídeos dark completos. O primeiro marco é um
vídeo assistível e reproduzível a partir de roteiro, assets e configurações
fornecidos manualmente:

```text
roteiro + assets + configuração
        ↓
narração -> timeline -> captions -> áudio
        ↓
render -> validação -> RenderManifest -> final.mp4
```

Pesquisa automática, roteiro automático, busca de stock, geração local de
assets e escolhas editoriais mais autônomas vêm depois que a composição básica
funcionar. Não são requisitos do primeiro vídeo.

`dark-video` v1 estará atingido quando o projeto puder, de maneira reproduzível:

- receber roteiro e múltiplos vídeos e/ou imagens;
- receber ou gerar narração local;
- representar e montar uma sequência temporal;
- sincronizar visuais, narração e captions;
- adicionar música opcional e ajustar volumes básicos;
- produzir e validar tecnicamente um MP4 novo;
- persistir um `RenderManifest`, preservar todos os sources e reproduzir a
  produção a partir dos contratos.

O vídeo inicial precisa ser completo e assistível, não cinematográfico.

O motor já recebe uma narração local com duração correspondente à sequência,
queima captions temporizadas, repete e mistura música com ganho persistido e
produz H.264/AAC. O modo final publica `final.mp4` apenas após validação em
staging e uma segunda validação do arquivo publicado, mantendo a revisão humana
como `not_performed`. Geração local de voz e imagens continuam futuras; para o
primeiro caso alimentado por clipes, voz e música fornecidos, o próximo marco é
executar uma produção real completa e obter revisão visual/auditiva humana.

## Fluxo-alvo

```text
tema -> pesquisa -> roteiro -> storyboard -> plano de assets
     -> obtenção/geração de assets -> narração -> EditPlan/timeline
     -> captions -> música/áudio -> render -> validação -> revisão -> final.mp4
```

O fluxo contratual pode evoluir para `VideoRequest -> VideoBrief -> Script ->
Storyboard -> AssetPlan -> EditPlan`, mas `Script`, `Storyboard` e `AssetPlan` só
devem virar contratos próprios quando um caso real exigir sua persistência e
invariantes. Até lá, a menor representação rastreável é preferível.

## Política econômica, internet e privacidade

O princípio é **local-first + internet permitida + nenhuma dependência de APIs
pagas de geração**.

- voz, transcrição, composição e render devem ser locais sempre que possível;
- internet gratuita pode apoiar pesquisa, referências, downloads de assets,
  fontes públicas, publicação e análise futura de métricas;
- OpenAI API separada, ElevenLabs, Runway, Veo, Kling, fal.ai, Replicate,
  HeyGen e equivalentes pagos não podem ser dependências operacionais;
- integração externa requer autorização explícita, configuração opt-in e uma
  fronteira de adapter; nunca é fallback silencioso;
- mídia, transcrição ou metadata privada não são enviadas a terceiros sem
  autorização explícita.

`local_only` continua registrando que a execução de mídia e o render do manifest
foram locais. Ele não significa que o projeto inteiro precisa funcionar offline
nem proíbe pesquisa ou aquisição autorizada de assets pela internet.

## Depois do v1

Após `dark-video` v1, a evolução pode incluir pesquisa, roteiro e storyboard
automáticos; `AssetPlan`; stock gratuito; geração local de imagem e vídeo;
B-roll, pacing, transitions, motion graphics, captions avançadas, sound design,
estilos, QA editorial, autocorreção, `ChannelProfile`, títulos, thumbnails,
publicação, analytics e feedback de performance.

O objetivo maduro é permitir um pedido como “produza o próximo vídeo do canal”
e fazer o agente cuidar da maior parte da produção com checkpoints auditáveis.

## Segundo workflow futuro: edição de creator

Edição automática de vídeos gravados pelo usuário permanece parte da visão, mas
não é prioridade atual e não deve ser implementada agora:

```text
vídeo bruto -> inspeção -> transcrição -> compreensão -> seleção/cortes
            -> reframing -> B-roll -> captions -> motion -> áudio -> render
```

Esse workflow deverá reutilizar FFmpeg, HyperFrames, timeline, captions, áudio,
assets, render, validação, QA, contratos e infraestrutura criados para
`dark-video`.

## Referências conceituais

OpenMontage, Code2MP4, MoneyPrinterTurbo e, quando pertinente, OpenX Flow podem
inspirar storyboard, separação entre planejamento e execução, checkpoints e
ferramentas controladas pelo agente. O projeto não é clone de nenhum deles e não
adota código ou arquitetura sem um problema concreto local.
