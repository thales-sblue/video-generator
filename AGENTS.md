# Constituição do projeto

## Direção do produto

`video-generator` é o motor local de produção audiovisual controlado pelo Codex.
O usuário descreve uma intenção e fornece mídia; o Codex interpreta o pedido,
analisa o material e persiste decisões editoriais antes de delegar operações
repetíveis ao projeto. A evolução desejada é:

```text
User intent -> Codex -> VideoRequest -> VideoBrief -> EditPlan
            -> adapters/renderers -> validation -> RenderManifest -> final.mp4
```

Cada incremento deve entregar a menor capacidade audiovisual funcional e
testável. Infraestrutura só deve ser adicionada quando sustentar um caso real.

## Fronteiras arquiteturais

- **Codex/orquestrador:** interpreta linguagem natural, inspeciona assets e
  transcrições, escolhe workflow, toma decisões editoriais, cria os contratos e
  revisa resultados. Não deve acumular comandos FFmpeg descartáveis quando a
  operação for recorrente.
- **Domínio/core:** contém contratos, planejamento estruturado, invariantes e
  validações puras. Não depende de FFmpeg, HyperFrames, Whisper, Kokoro ou I/O.
- **Adapters:** isolam ferramentas locais e traduzem contratos do domínio para
  FFmpeg/ffprobe, Whisper, Kokoro e integrações futuras.
- **Renderers:** transformam `EditPlan` validado em composição e artifacts. O
  HyperFrames é o compositor principal planejado; FFmpeg executa operações de
  mídia de baixo nível.
- **Workflows:** coordenam capacidades reutilizáveis para objetivos como Reel,
  Short ou visualizer. Não escondem decisões editoriais em efeitos colaterais.
- **Validation:** verifica contratos, probes, artifacts e renders sem confundir
  sucesso técnico com aprovação editorial humana.

As dependências apontam para dentro: adapters e renderers podem depender do
domínio; o domínio nunca depende deles. `midi-generator` é referência
conceitual, não dependência nem módulo compartilhado.

## Política local-only e dependências

- Processamento audiovisual é local por padrão e `local_only` deve permanecer
  explícito na configuração e nos manifests.
- Nunca envie mídia, transcrição ou metadata do usuário a terceiros.
- Não introduza serviços pagos ou APIs remotas (incluindo OpenAI API separada,
  HeyGen, ElevenLabs, fal.ai, Replicate, Runway ou equivalentes) como dependência
  operacional.
- Integrações externas futuras exigem autorização explícita, configuração
  opt-in e fronteira de adapter; não podem ser fallback silencioso.
- Dependências opcionais ausentes não devem impedir contratos, planejamento,
  inspeção básica ou diagnóstico.
- Não instale ferramentas pesadas, modelos, ComfyUI ou runtimes globais sem
  solicitação e necessidade concretas. Fixe versões quando forem adicionadas.

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

O fluxo mínimo é `VideoRequest -> VideoBrief -> EditPlan`. `RenderManifest` será
adicionado quando houver execução real. Contratos publicados devem:

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
3. transcrever localmente quando fala for relevante;
4. entender intenção, audiência e plataforma;
5. escolher um workflow suportado;
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

1. leia este `AGENTS.md` e a documentação relevante;
2. inspecione árvore, estado do Git, commits recentes e diff do `HEAD`;
3. execute a suíte completa antes de alterar código;
4. confirme as capacidades existentes para não reimplementá-las;
5. identifique o próximo gap audiovisual real;
6. escolha **um** incremento pequeno, coeso e útil;
7. prefira capacidade funcional a infraestrutura especulativa;
8. implemente na camada correta;
9. crie ou atualize testes;
10. execute novamente toda a suíte;
11. revise o próprio diff, incluindo segurança e não destruição;
12. atualize documentação correspondente;
13. crie um commit coeso;
14. confirme `origin`, branch e ausência de mídia/secrets;
15. envie para `origin/main` somente quando o estado estiver válido.
16. ao encerrar, informe uma estimativa percentual de quanto o projeto avançou
    naquele prompt e de quanto ainda falta para atingir a visão do produto,
    deixando claro que os valores são aproximações baseadas nas capacidades
    implementadas e nos gaps conhecidos.

Não implemente vários workflows de uma vez e não prolongue infraestrutura sem
ganho audiovisual concreto.
