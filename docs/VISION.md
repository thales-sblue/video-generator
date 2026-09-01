# Visão

## Produto

`video-generator` será um agente produtor audiovisual controlado por um agente
orquestrador (atualmente o Claude Code). Esse agente é o cérebro editorial e
orquestrador; o projeto é o motor operacional previsível, auditável e
reproduzível.

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

`dark-video` v1 **foi atingido em 2026-08-31**. De maneira reproduzível, o
projeto já consegue:

- receber roteiro e múltiplos vídeos e/ou imagens;
- receber ou gerar narração local;
- representar e montar uma sequência temporal;
- sincronizar visuais, narração e captions;
- adicionar música opcional e ajustar volumes básicos;
- produzir e validar tecnicamente um MP4 novo;
- persistir um `RenderManifest`, preservar todos os sources e reproduzir a
  produção a partir dos contratos.

O vídeo inicial precisa ser completo e assistível, não cinematográfico.

O motor recebe uma narração local com duração correspondente à sequência,
queima captions temporizadas, repete e mistura música com ganho persistido e
produz H.264/AAC. O modo final publica `final.mp4` apenas após validação em
staging e uma segunda validação do arquivo publicado. A produção de referência
está em `projects/prod/` (roteiro PT-BR -> narração Kokoro -> timeline ->
captions `from=narration` -> música -> `final.mp4` H.264/AAC), com o pipeline
oficial re-executado e o `final.mp4` re-derivado byte-idêntico.

A revisão visual/auditiva humana dessa produção foi realizada em 2026-08-31 e
aprovou hook, ritmo, atmosfera/trilha, encerramento, legendas e visuais. O
`RenderManifest` v1 só aceita `editorial_review = not_performed`, então o
veredito humano fica registrado nas `editorial_notes` do `video-brief.json` e no
histórico da sessão; promover `editorial_review` a um veredito com evidência no
próprio contrato é um incremento futuro.

**Pendência conhecida e aceita do v1 — naturalidade da voz.** O Kokoro TTS local
ainda soa sintético em PT-BR mesmo a 0,88 com roteiro enxuto; foi mitigado com
velocidade menor e reescrita de trechos. Uma voz melhor exige outro TTS local
(Piper/XTTS/F5-TTS, pelos gates de licença/segurança) ou narração gravada por
humano — próximo incremento, fora do escopo do v1. Publicar em um canal real
também pede trocar o b-roll procedural de placeholder por footage dark real com
direitos rastreáveis: passo editorial/de asset, não lacuna do motor.

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
- APIs pagas de geração — Anthropic/Claude API, OpenAI API, ElevenLabs, Suno,
  Runway, Veo, Kling, fal.ai, Replicate, HeyGen e equivalentes — não podem ser
  dependências nem fallback operacional; o agente orquestrador é ferramenta de
  desenvolvimento, não runtime do produto, e o motor nunca chama a API dele; o
  free tier de um serviço pago não conta como gratuito, e um serviço hoje
  gratuito que pode passar a cobrar não vira dependência obrigatória;
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

Qualquer aquisição automática de assets externos exige, antes de uso em produção
comercial, origem e direito de uso comercial rastreáveis e a persistência dessa
proveniência (ver "Direitos sobre assets externos" e "Contratos e evolução" em
[AGENTS.md](../AGENTS.md)).

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
adota código ou arquitetura sem um problema concreto local. Antes de qualquer
reutilização, aplicar a decisão de build vs reuse e os gates de licença e
segurança de [AGENTS.md](../AGENTS.md).

Papel adequado de cada referência:

- **Code2MP4 (Apache-2.0):** licença permissiva, é o candidato mais aberto a
  reutilização direta quando houver um gap concreto (storyboard, motion source,
  integração com HyperFrames, composição, workflow orientado a agentes). Ainda
  assim, nada é copiado automaticamente.
- **MoneyPrinterTurbo (MIT):** pode ter partes úteis, mas integra muitos serviços
  e APIs externas e pagas. MIT não valida os providers: reutilizar apenas partes
  compatíveis com a política; providers pagos não entram como dependência nem
  fallback.
- **OpenMontage (AGPLv3):** forte como referência conceitual e arquitetural.
  Cuidado com reutilização direta de código — o copyleft do AGPL pode ser
  incompatível com uma estratégia proprietária futura. Não copiar código AGPL
  sem uma decisão explícita e justificada sobre as implicações de licença.
- **OpenX Flow:** avaliar licença, arquitetura, dependências e custo real antes
  de qualquer reutilização.

Open source não significa automaticamente gratuito para executar, livre de
dependências externas, seguro ou adequado a esta arquitetura.
