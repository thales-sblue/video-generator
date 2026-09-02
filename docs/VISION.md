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

## Voz local — decisão de reuso (2026-09-01)

A naturalidade da voz é a pendência conhecida e aceita do v1 (Kokoro soa
sintético em PT-BR). Pesquisa de alternativas locais, avaliada pelos gates de
licença, segurança/manutenção, custo e ajuste local-first:

- **XTTS v2** e **F5-TTS** — descartados: licenças não comerciais (Coqui Public
  Model License "non-commercial"; F5-TTS CC-BY-NC 4.0). Coqui encerrou em 2024
  sem relicenciar.
- **Piper** — o `rhasspy/piper` MIT foi arquivado (somente leitura, out/2025); o
  fork mantido é **GPL-3.0**. Copyleft conflita com a opção de manter o
  `video-generator` proprietário no futuro; a versão MIT sem manutenção falha no
  gate de segurança. Descartado como engine (vozes PT-BR isoladas em MIT, ex.
  Razo/faber, não resolvem a licença do runtime).
- **Chatterbox Multilingual** (Resemble AI, código MIT, tem modelo dedicado
  PT-BR) — descartado por ora: pede 5–7 GB de VRAM e é "painfully slow" em CPU,
  além de marca d'água embutida em todo output. Runtime pesado desproporcional
  ao incremento e a esta máquina; reabrir só com decisão explícita de exigir GPU.
- **Kokoro** (Apache-2.0) — permanece o único TTS local empacotado. Já isolado e
  travado em `.local-tools/kokoro/`, CPU-viável, com `pf_dora`/`pm_alex` em
  PT-BR.

**Decisão: reutilizar — manter só o Kokoro; não adicionar um segundo engine
agora.** O caminho de maior qualidade já é suportado sem dependência nova:
**narração gravada por humano** via `narration` com `source` de áudio local. Um
segundo engine só entra quando surgir uma opção permissiva e viável em CPU, ou
mediante decisão explícita de adotar runtime com GPU.

## Alinhamento de captions — decisão de reuso (2026-09-02)

O timing de `from=narration` era estimado por peso de sílabas. Pesquisa de
alternativas locais para medir a fala, pelos mesmos gates:

- **kokoro-onnx `create_timed`** (já instalado, 0.6.1) — devolveria o instante de
  cada fonema, de graça e sem dependência nova. Bloqueado pelo asset: o export
  `kokoro-v1.0.onnx` que temos publica só a saída `audio`, sem `duration`
  (verificado via onnxruntime), então `has_timings` é falso. Reabrir exigiria
  re-exportar o modelo (PyTorch + pesos originais) e travar um asset novo — é o
  melhor caminho futuro, não um incremento pequeno.
- **faster-whisper** (MIT, CTranslate2, int8 viável em CPU) — alinhamento por
  palavra de verdade, mas é um caminho de vários commits: dependência,
  download/lock de modelo, check no `doctor`, adapter e wiring. Segue como porta
  aberta para alinhamento por palavra, não para este incremento.
- **aeneas** — descartado: AGPL-3.0, copyleft incompatível com a opção de manter
  o `video-generator` proprietário.
- **Montreal Forced Aligner** (MIT) — descartado por ora: pilha Kaldi/conda e
  modelos grandes, desproporcional ao ganho.
- **FFmpeg `silencedetect`** — já travado em `.local-tools/ffmpeg` (LGPL), zero
  dependência nova, passagem somente leitura.

**Decisão: reutilizar o FFmpeg — ancorar as quebras de caption nas pausas
medidas** (`detect_silences` + `align_cues_to_silences`). Mede onde a voz parou,
não qual palavra foi dita: afia a estimativa sem prometer alinhamento forçado.
Whisper local continua o próximo passo se o alinhamento por palavra virar
necessário.

O objetivo maduro é permitir um pedido como “produza o próximo vídeo do canal”
e fazer o agente cuidar da maior parte da produção com checkpoints auditáveis.

## Bancos de stock (Pexels / Pixabay / Openverse) — decisão de reuso (2026-09-02)

Levantados como fornecedores de B-roll para o passo `busca/seleção de assets` do
fluxo-alvo, pelos gates de licença e segurança:

- **Pexels API** — grátis hoje, exige API key, quota ~200 req/h e 20 000 req/mês,
  atribuição “apreciada” mas não obrigatória. Uso comercial permitido pela
  licença do banco, que **não** cobre marcas, logos, pessoas reconhecíveis,
  propriedade privada ou obras protegidas presentes na mídia. Termos e limites
  podem mudar sem aviso.
- **Pixabay API** — grátis, exige API key, rate limit ~100 req/min, resultados em
  cache por 24 h por termos. Content License própria (2019+), sem atribuição
  obrigatória, com as mesmas exclusões de marcas/pessoas/propriedade e uma
  cláusula que veda redistribuir os assets “as-is”.
- **Openverse** (WordPress Foundation) — agregador CC/domínio público; a licença
  **varia por resultado** (CC0 até CC BY-NC-SA e marcas). Cada item traz
  `license`, `license_version`, `attribution` e `source`; a checagem tem de ser
  por asset, não por provedor. API sem key para uso básico, com throttle.

Nenhum dos três garante direitos sobre marcas, pessoas ou propriedade privada no
quadro — isso continua sendo revisão editorial. Todos exigiriam persistir
proveniência por asset (origem, URL/ID, licença, uso comercial, atribuição,
data, SHA-256) antes de qualquer uso em produção comercial.

**Decisão deste incremento: NÃO implementar adapter externo agora.** O consumidor
real desses provedores é o par `AssetPlan` + `AssetProvenance` + catálogo local
rastreável, que ainda não existe e é o próximo incremento recomendado. Sem esse
contrato local, um adapter de banco entregaria assets sem lugar para gravar
licença e proveniência. Preço, quota e termos atuais **não são garantias
permanentes** e devem ser revalidados quando um adapter for de fato desenvolvido;
Openverse em particular exige tratamento de licença por resultado.

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
