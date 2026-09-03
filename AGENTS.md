# Constituição do projeto

## Direção do produto

`video-generator` é o motor de produção audiovisual local-first controlado por
um agente orquestrador (atualmente o **Claude Code**; antes o Codex). O agente
interpreta o pedido, pesquisa quando autorizado, analisa o material e persiste
decisões editoriais antes de delegar operações repetíveis ao projeto. A evolução
desejada é:

```text
User intent -> agente -> VideoRequest -> VideoBrief -> EditPlan
            -> adapters/renderers -> validation -> RenderManifest -> final.mp4
```

Cada incremento deve entregar a menor capacidade audiovisual funcional e
testável. Infraestrutura só deve ser adicionada quando sustentar um caso real.

A prioridade absoluta é `dark-video`: primeiro produzir um vídeo dark completo,
assistível e reproduzível; depois amadurecer esse workflow. Edição automática de
creator/talking-head é um segundo grande workflow futuro que deverá reutilizar o
mesmo motor, mas não deve ser implementado agora. A pergunta de priorização é:
**qual é o menor incremento funcional que mais nos aproxima do primeiro vídeo
dark completo?**

O objetivo é automatizar a produção de conteúdo original e transformativo, não
gerar spam. Evite arquiteturas que incentivem automaticamente conteúdo
repetitivo, massificado quase idêntico, mera reempacotagem de terceiros ou
produção sem valor editorial. Roteiro, edição, narrativa e seleção de assets
devem permitir originalidade suficiente para uso comercial. Não construa um
"detector de monetização".

## Fronteiras arquiteturais

- **Agente/orquestrador (Claude Code):** interpreta linguagem natural, inspeciona
  assets e transcrições, escolhe workflow, toma decisões editoriais, cria os
  contratos e revisa resultados. Não deve acumular comandos FFmpeg descartáveis
  quando a operação for recorrente.
- **Domínio/core:** contém contratos, planejamento estruturado, invariantes e
  validações puras. Não depende de FFmpeg, HyperFrames, Whisper, Kokoro ou I/O.
- **Adapters:** isolam ferramentas locais e traduzem contratos do domínio para
  FFmpeg/ffprobe, Whisper, Kokoro e integrações futuras.
- **Renderers:** transformam `EditPlan` validado em composição e artifacts. O
  HyperFrames é o compositor principal planejado; FFmpeg executa operações de
  mídia de baixo nível.
- **Workflows:** coordenam capacidades reutilizáveis, começando por
  `dark-video`. Não escondem decisões editoriais em efeitos colaterais.
- **Validation:** verifica contratos, probes, artifacts e renders sem confundir
  sucesso técnico com aprovação editorial humana.

As dependências apontam para dentro: adapters e renderers podem depender do
domínio; o domínio nunca depende deles. `midi-generator` é referência
conceitual, não dependência nem módulo compartilhado.

## Política local-first, internet e dependências

- Processamento audiovisual é local por padrão e `local_only` deve permanecer
  explícito nos manifests para registrar que aquela execução de mídia foi local.
- Internet gratuita é permitida para pesquisa, referências, download autorizado
  de assets, fontes públicas, publicação e análise futura de métricas.
- Nunca envie mídia, transcrição ou metadata do usuário a terceiros sem
  autorização explícita.
- Não introduza APIs pagas de geração (incluindo Anthropic/Claude API, OpenAI
  API, HeyGen, ElevenLabs, Suno, fal.ai, Replicate, Runway, Veo, Kling ou
  equivalentes) como dependência ou fallback operacional. O agente orquestrador
  (Claude Code) é ferramenta de desenvolvimento, não runtime do produto: o motor
  nunca chama a API do próprio orquestrador nem qualquer serviço pago de geração.
  O free tier de um serviço pago não conta como solução gratuita, e um serviço
  hoje gratuito que pode passar a cobrar não pode virar dependência obrigatória.
  A ausência de qualquer serviço externo nunca pode quebrar uma capacidade
  central do produto.
- Integrações externas futuras exigem autorização explícita, configuração
  opt-in e fronteira de adapter; não podem ser fallback silencioso.
- Dependências opcionais ausentes não devem impedir contratos, planejamento,
  inspeção básica ou diagnóstico.
- Não instale ferramentas pesadas, modelos, ComfyUI ou runtimes globais sem
  solicitação e necessidade concretas. Fixe versões quando forem adicionadas.

## Reutilização antes de reimplementar

Antes de implementar uma capacidade relevante — adapter, renderer, workflow,
modelo, subsistema ou qualquer bloco grande — o agente toma uma decisão
consciente de build vs reuse:

```text
gap -> pesquisa de soluções existentes -> análise -> build vs reuse -> implementação
```

1. verifique primeiro as capacidades já existentes no próprio projeto;
2. pesquise soluções open source relevantes (ex.: Code2MP4, MoneyPrinterTurbo,
   OpenMontage, OpenX Flow e outras que surgirem — ver
   [docs/VISION.md](docs/VISION.md));
3. analise licença, custo real, segurança, dependências e compatibilidade com
   esta arquitetura (ver "Licença e segurança de dependências");
4. escolha, nesta ordem de preferência: (a) reutilizar uma dependência segura e
   compatível; (b) adaptar uma implementação existente; (c) reaproveitar apenas
   conceitos ou arquitetura; (d) implementar do zero somente quando as anteriores
   não servirem.

O objetivo não é obrigar reutilização, e sim obrigar a decisão explícita. A
análise deve ser proporcional ao tamanho do incremento: ajustes triviais não
viram pesquisa. Em cada ciclo relevante, o relatório final resume os
projetos/soluções avaliados, o componente pertinente encontrado, a decisão
(reutilizar / adaptar / usar como referência / implementar internamente) e a
justificativa.

## Licença e segurança de dependências

Antes de incorporar qualquer biblioteca, código ou componente externo relevante:

- **Licença:** confirme licença explícita; avalie compatibilidade com uso
  comercial, obrigações de atribuição, obrigações de redistribuição ou
  divulgação de source, risco de copyleft e a possibilidade de manter o
  `video-generator` privado/proprietário no futuro. Favoreça licenças permissivas
  (MIT, Apache-2.0, BSD) quando forem tecnicamente adequadas. Não incorpore
  código sem licença clara; em caso de dúvida, use apenas como referência
  conceitual até uma análise posterior.
- **Segurança:** verifique origem oficial; evite binários ou scripts obscuros;
  confira atividade/manutenção quando pertinente; não execute código remoto
  automaticamente; mantenha instalações locais isoladas e fixadas por lock quando
  possível; preserve a política fail-closed. Uma dependência não deve ser
  adicionada apenas porque outro projeto open source a utiliza.

Isto complementa, não substitui, as regras de "Segurança, imutabilidade e
artifacts".

## Direitos sobre assets externos

- Nenhum asset externo (vídeo, imagem, música, efeito sonoro, fonte, voz/modelo
  com restrição, elemento gráfico) pode ser usado em produção comercial sem
  origem e direito de uso comercial suficientemente rastreáveis.
- Não use a expressão ou promessa absoluta "conteúdo sem copyright": isso não é
  tecnicamente garantível.
- Um asset não é seguro só porque foi encontrado na internet. A licença de um
  banco (Pexels, Pixabay e equivalentes) pode permitir uso comercial, mas não
  cobre direitos adicionais sobre marcas, logos, pessoas, propriedade privada ou
  obras protegidas presentes na mídia.
- Se a licença ou a origem não puder ser determinada de forma aceitável, o
  agente não utiliza o asset automaticamente.
- O `resolve-assets` já obtém assets automaticamente de uma biblioteca local e
  de fontes gratuitas opt-in (Pexels/Pixabay por chave). A partir daí a
  proveniência (`AssetProvenance`: origem, licença, autor, `acquired_at`,
  SHA-256) é **persistida obrigatoriamente** — nenhum asset resolvido sem ela —
  e a compatibilidade comercial da licença permanece um gate de revisão
  editorial humana (ver "Contratos e evolução").

## Segurança, imutabilidade e artifacts

- Inputs são imutáveis. Nunca sobrescreva, mova ou apague vídeo, imagem, música,
  voz, texto ou qualquer source do usuário.
- Toda operação gera novos arquivos sob diretórios de projeto ou `output/`.
- Não versione mídia, renders, modelos, caches, secrets ou arquivos temporários.
- Caminhos de output não podem coincidir com caminhos de input; valide isso
  antes de executar qualquer ferramenta.
- Projetos devem ser reproduzíveis. Intenção e decisões relevantes pertencem a
  contratos persistidos, com `schema_version`, e não apenas ao raciocínio
  temporário do agente.
- Operações devem ser determinísticas quando possível. Seeds e versões de
  ferramentas devem ser persistidas quando influenciarem o resultado.
- Toda chamada de subprocesso deve usar argumentos estruturados, validar paths
  e propagar falhas com contexto, sem montar shell a partir de texto não confiável.

## Contratos e evolução

O fluxo mínimo publicado é `VideoRequest -> VideoBrief -> EditPlan ->
RenderManifest`. `Script`, `Storyboard` e `AssetPlan` são candidatos futuros,
não contratos obrigatórios: só devem existir separadamente quando um caso real
exigir invariantes ou checkpoints que a representação atual não preserve.

`AssetProvenance` — origem/URL ou identificador, licença e URL da licença,
autor, `acquired_at`, SHA-256 do arquivo, `candidate_id` e `local_path` — **é
contrato publicado** (`schemas/asset-resolution-plan-v1.schema.json`,
`domain/assets.py`) desde que o `resolve-assets` passou a adquirir assets
automaticamente. Todo `ResolvedAsset` carrega uma `AssetProvenance` completa; o
`RenderManifest` continua fixando o SHA-256 de cada source e a compatibilidade
comercial da licença permanece um gate de revisão editorial.
Contratos publicados devem:

- ter versão explícita e representação JSON determinística;
- recusar campos ou estados inválidos cedo;
- usar paths como referências, nunca incorporar bytes de mídia;
- preservar IDs que permitam rastrear request, brief, plan e render;
- evoluir de forma compatível ou por uma nova versão documentada.

JSON Schemas em `schemas/` são interfaces públicas. Alterá-los exige testes e
documentação correspondente.

## Validação e definição de pronto

Um incremento só está pronto quando:

1. invariantes e falhas relevantes possuem testes;
2. a suíte completa passa;
3. o diff foi revisado por segurança, separação de camadas e escopo;
4. documentação e exemplos refletem o comportamento real;
5. nenhum source, mídia grande, secret ou endpoint pago foi incluído;
6. validações manuais são descritas como pendentes até existir evidência real.

Testes técnicos não garantem qualidade editorial. Quando avaliação visual ou
auditiva humana for necessária, registre essa fronteira claramente.

## Protocolo de produção audiovisual

Quando houver capacidade suficiente para atender um pedido real:

1. identificar os arquivos relevantes e preservar os originals;
2. executar inspeção técnica com ferramentas locais;
3. entender roteiro, intenção, audiência e plataforma;
4. escolher o workflow `dark-video` suportado e o menor escopo executável;
5. transcrever ou narrar localmente quando fala for relevante;
6. persistir `VideoRequest`, `VideoBrief` e `EditPlan`;
7. validar o plano, inclusive conflitos entre inputs e outputs;
8. executar operações reutilizáveis por adapters/renderers;
9. produzir render de trabalho quando necessário;
10. inspecionar resultado técnica e editorialmente quando possível;
11. corrigir problemas detectáveis e renderizar o final;
12. validar o final e persistir `RenderManifest` antes de concluir.

Nunca represente uma revisão visual/auditiva como realizada sem evidência.

## Protocolo para `continue`

Ao receber apenas uma instrução curta para continuar:

1. leia este `AGENTS.md`, o `CLAUDE.md` e a documentação relevante;
2. inspecione árvore, estado do Git, commits recentes e diff do `HEAD`;
3. execute a suíte completa antes de alterar código;
4. descubra nos commits, diff e documentos onde o último ciclo parou;
5. confirme as capacidades existentes no projeto para não reimplementá-las e,
   quando o incremento for relevante, pesquise soluções open source e decida
   build vs reuse (ver "Reutilização antes de reimplementar");
6. identifique o próximo gargalo real para `dark-video` v1;
7. escolha **um** incremento coeso usando a pergunta: “qual é o menor
   incremento funcional que mais nos aproxima do primeiro vídeo dark completo?”;
8. prefira capacidade funcional a infraestrutura especulativa;
9. valide licença, custo e segurança de qualquer dependência nova (ver "Licença
   e segurança de dependências") e implemente na camada correta;
10. crie ou atualize testes e faça validações manuais relevantes;
11. execute novamente toda a suíte;
12. revise o próprio diff, incluindo segurança e não destruição;
13. atualize documentação correspondente;
14. crie um commit coeso;
15. confirme `origin`, branch e ausência de mídia/secrets;
16. envie para `origin/main` somente quando o estado estiver válido;
17. informe o que foi implementado, por que foi escolhido, a decisão de build vs
    reuse quando o incremento foi relevante (projetos avaliados, componente
    encontrado, decisão, justificativa), testes, validações manuais, limitações,
    próximo gargalo e percentuais aproximados de progresso até `dark-video` v1 e
    até a visão madura do agente produtor.

Não use `continue` para implementar vários workflows, refatorar por estética,
antecipar integrações distantes, prolongar infraestrutura sem ganho audiovisual
concreto ou adicionar geração de vídeo por IA antes da composição básica.

O `CLAUDE.md` mapeia cada passo deste protocolo para os recursos nativos do
Claude Code (plan mode, subagents, skills `run`/`code-review`, memory files).
