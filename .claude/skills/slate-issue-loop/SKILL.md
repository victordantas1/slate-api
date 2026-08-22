---
name: slate-issue-loop
description: Use when asked to work the Slate issue queue, run the issue loop, pick up the next issue, or resume an issue left in progress in slate-api or slate-web.
---

# Slate Issue Loop

Uma issue por execução, do GitHub até o PR, com estado **commitado no repositório** para a próxima execução retomar de onde parou.

**Uma issue por execução.** Termine, reporte, encerre. Não emende a próxima.

**Dentro de uma issue não há checkpoint.** Rodar o loop é a aprovação de tudo: desenho, plano, implementação, revisão e PR. A revisão humana acontece no PR, não antes.

**A issue do GitHub é o contrato.** Título, descrição e critérios de aceite são o requisito completo — as issues foram escritas para serem auto-suficientes. Você lê a issue, escreve o plano, implementa, abre o PR.

**Nenhum documento de projeto é pré-requisito.** Se houver uma spec de design no repo, ela é contexto útil. Se não houver, a issue basta. Ausência de documentação **nunca** bloqueia a rodada — ver "Documento ausente não é bloqueio".

## Constantes

| O quê | Valor |
| --- | --- |
| Repos | `<owner>/slate-api`, `<owner>/slate-web` |
| Base dos dois | `main` |
| Forja | GitHub, via `gh` |
| Planos | `<repo>/docs/plans/<numero>-<slug>.md`, um por issue, na branch |
| Spec de design | `slate-api:docs/superpowers/specs/` — **opcional**. Leia se existir, siga sem se não existir. |
| Estado | `<repo>/.claude/loop-state.md`, commitado em `main` |
| Fila | issue aberta, com milestone, **sem** label `status:*`, com dependências satisfeitas |
| Ao reivindicar | label `status:doing` |
| Ao abrir o PR | label `status:review` |
| Ao bloquear | label `status:blocked` + comentário explicando |

Nunca feche issue à mão: quem fecha é o merge, via `Closes #N` no corpo do PR.

Crie os labels de status na primeira execução, idempotente:

```bash
for R in slate-api slate-web; do
  gh label create status:doing   --repo "$OWNER/$R" --color 0E8A16 --force
  gh label create status:review  --repo "$OWNER/$R" --color 1D76DB --force
  gh label create status:blocked --repo "$OWNER/$R" --color B60205 --force
done
```

## Ambiente efêmero: o estado mora no git

**Nada sobrevive à execução exceto o que está commitado e empurrado.** Sem `~/.claude`, sem `node_modules`, sem `.venv`, sem checkout anterior. Um fato que só existe no contexto ou no disco local está perdido no instante em que a execução termina.

Duas consequências, e as duas são regras duras:

**1. O estado é um commit em `main`, não um arquivo local.** `<repo>/.claude/loop-state.md`, atualizado a cada transição de fase, com commit próprio empurrado direto para `main`:

```bash
git checkout main && git pull --rebase origin main
# escreva .claude/loop-state.md
git add .claude/loop-state.md
git commit -m "chore(loop): estado — #<n> fase <fase>"
git push origin main
git checkout <branch-da-issue>
```

Commit de estado **nunca** entra na branch da issue — ele sujaria o diff do PR e sumiria se o PR não fosse mergeado. Sempre em `main`, sempre isolado, sempre com `pull --rebase` antes para não colidir com outra execução.

**2. O GitHub é a fonte primária, o arquivo é o resumo.** Antes de confiar no arquivo de estado, confira o mundo real — labels da issue, PRs abertos, branches remotas:

```bash
gh issue list --repo "$OWNER/$REPO" --label status:doing --json number,title
gh pr list   --repo "$OWNER/$REPO" --state open --json number,headRefName,title
git ls-remote --heads origin
```

Se o arquivo diz `fase: implementacao` mas já existe PR aberto para aquela branch, o PR ganha: a execução anterior morreu depois de abrir o PR e antes de commitar o estado. Retome na fase `ci`.

**3. Toda ação não idempotente é precedida de uma verificação.** Ramificar, commitar, abrir PR, comentar na issue — confira antes se já aconteceu. `git log --oneline origin/main..HEAD`, `gh pr list --head <branch>`, `gh issue view <n> --comments`. Em ambiente efêmero isso não é paranoia, é o caso comum.

## Escolher a issue

```bash
gh issue list --repo "$OWNER/$REPO" --state open --limit 100 \
  --json number,title,labels,milestone,body,createdAt
```

Descarte: as com qualquer label `status:*`, as sem milestone, e as com dependência não satisfeita.

**A ordem é determinística e não se pergunta:**

1. **Repo:** `slate-api` primeiro. Só quando a fila dele está vazia é que se olha `slate-web`. A API é o caminho crítico.
2. **Milestone:** M1 → M2 → M3 → M4.
3. **Prioridade:** `P0` antes de `P1`.
4. **Empate:** menor número de issue.

**Dependências vêm do corpo da issue.** Toda issue que depende de outra tem uma seção `## Depende de` nomeando a issue. Ela só entra na fila quando a dependência está **fechada** (mergeada). Dependência aberta = issue fora da fila nesta rodada.

Três dependências duras, que valem mesmo sem estar escritas no corpo:

| Bloqueio | Regra |
| --- | --- |
| `slate-api` M3 inteiro | Bloqueado até a issue "Suite de testes de integração com testcontainers" estar fechada. Nenhum endpoint antes das três invariantes verdes. |
| `slate-web` M2, M3 | Bloqueados até "Publicar OpenAPI" (`slate-api` M4) estar fechada. Sem contrato tipado, a UI vira retrabalho. |
| Qualquer issue de `slate-web` | Requer `slate-web` M1 completo. |

Isso substitui a árvore de dependências: a ordem já está codificada em milestone + `Depende de`, então não há grafo a montar nem arquivo de árvore a manter.

**Fila vazia nos dois repos → pare.** Reporte "nada elegível" e encerre. Não relaxe filtro, não pegue issue com `status:blocked`, não invente trabalho.

**Escolher entre issues elegíveis não é motivo de parar.** A ordem decidiu. Anuncie em uma linha e siga.

## Sem worktree: branch direto no checkout

Uma issue por execução e ambiente descartável tornam o worktree inútil — não há issue paralela para atropelar nem checkout sujo para herdar.

```bash
git clone https://github.com/$OWNER/$REPO.git && cd $REPO
git checkout main && git pull origin main
git checkout -b <branch>
```

**Nome da branch:** `<tipo>/<numero>-<slug>` — `feat/12-cascata-edicao`, `chore/3-migration-household`. Tipo vem do label: `type:feature` → `feat`, `type:test` → `test`, `area:infra` → `chore`.

Se a branch já existe no remoto, a execução anterior morreu no meio: `git checkout -b <branch> origin/<branch>` e retome pela fase que o estado indica.

**Nunca commite em `main`, exceto o commit de estado.** São as únicas duas coisas que tocam `main`: o arquivo de estado e nada mais.

**Instale as dependências antes de qualquer gate.** Checkout novo não tem nada:

- `slate-api`: `uv sync` (ou `pip install -e ".[dev]"`)
- `slate-web`: `npm ci`

Confirme com o gate mais barato do repo antes de escrever código. Se a instalação falhar, isso é bloqueio ambiental — ver abaixo.

## Decidir sozinho

Toda decisão de desenho sai da primeira fonte que responder:

1. **O corpo da issue e seus comentários** — critérios de aceite são requisitos, não sugestões. Blocos de SQL, tabelas e fórmulas dentro da issue são especificação literal: implemente o que está escrito ali.
2. **Código vizinho no mesmo repo** — copie o padrão do módulo análogo. Cite o caminho do arquivo que serviu de modelo.
3. **`CLAUDE.md` do repo** — comandos, gates, convenções.
4. **Spec de design em `docs/`, se existir** — contexto de arquitetura. Se a issue e a spec divergirem, **a issue ganha**: ela é mais recente e mais específica. Registre a divergência no corpo do PR.
5. **Nada respondeu** → opção mais conservadora: menor diff, nenhuma dependência nova, contrato público inalterado. Registre como **Pressuposto**, uma linha, no corpo do PR e num comentário da issue.

Um pressuposto registrado é a resposta certa.

### Documento ausente não é bloqueio

Spec, ADR, diagrama, README, plano de issue anterior — **a falta de qualquer artefato de documentação nunca impede a rodada.** Se a issue cita uma seção de um documento que não existe no repo, você tem tudo o que precisa: o corpo da issue foi escrito para ser auto-suficiente. Anote uma linha no plano (`spec citada não existe no repo; implementado a partir da issue`) e siga.

Isto vale para todos os cinco bloqueios da seção abaixo: nenhum deles é sobre documentação. Bloqueio é ambiente e permissão, nunca informação de projeto.

### Não há usuário para perguntar

A rotina roda sem ninguém do outro lado. **"Perguntar ao usuário" não é uma ação disponível.** Quando algo realmente bloqueia, o procedimento é:

```bash
gh issue edit <n> --repo "$OWNER/$REPO" --add-label status:blocked --remove-label status:doing
gh issue comment <n> --repo "$OWNER/$REPO" --body "Bloqueado: <o quê> — <o que foi tentado> — <o que precisa de decisão humana>"
```

Depois: commite o estado, empurre a branch se houver trabalho aproveitável, e encerre a execução. **Nunca deixe a issue em `status:doing` ao encerrar bloqueada** — a próxima execução a pegaria como "em andamento" e tentaria retomar algo intratável.

**Os únicos bloqueios legítimos:**

- Ação irreversível fora do diff: apagar ou migrar dado em produção, rotacionar segredo, criar variável de CI sem permissão.
- Credencial ausente ou expirada: sem `SUPABASE_URL` de teste, sem token do `gh`, sem acesso ao registry.
- Gate falha por motivo ambiental que nenhuma correção de código resolve.
- Três rodadas de correção de CI sem verde.
- Ação irreversível não coberta acima que exigiria decisão humana explícita.

Fora desses cinco, decida e siga. **Nenhum deles é sobre documentação, arquivo de projeto ausente ou ambiguidade de requisito** — isso a escada resolve, com pressuposto registrado se necessário.

Bloqueio é sempre de uma **issue específica**. Se você não reivindicou nenhuma issue ainda, não há o que bloquear: ou a fila está vazia (reporte e encerre), ou há issue elegível (pegue e trabalhe).

## Rota por tipo

**Toda issue gera um plano de implementação**, em `docs/plans/<numero>-<slug>.md`, na branch da issue. É o artefato que a rotina produz antes de tocar em código, e é contra ele que a revisão final valida.

**Não chame `superpowers:brainstorming`** — o gate dela ("não implemente até o usuário aprovar") é exatamente o que este loop remove.

| Sinal | Rota | Artefato |
| --- | --- | --- |
| comportamento errado de algo que já existe | `superpowers:systematic-debugging` → plano de correção | causa raiz em comentário na issue **antes** da correção, + plano |
| todo o resto | `superpowers:writing-plans`, parando antes do "Execution Handoff" | `docs/plans/<numero>-<slug>.md` |

O plano tem cinco partes, e sai **da issue**:

1. **Objetivo** — uma frase, do critério de aceite.
2. **Arquivos** — cada caminho que será criado ou tocado.
3. **Tarefas** — a sequência, uma linha cada.
4. **Pressupostos** — cada decisão do degrau 5 da escada, uma linha.
5. **Testes** — o que prova cada critério de aceite da issue.

Sem "TBD", sem alternativa em aberto. O plano registra a decisão tomada.

O plano entra no commit da branch (documentação do trabalho), diferente do estado, que vai para `main`.

## Contagem antes de implementar

Com o plano escrito, conte **no arquivo do plano**: quantas tarefas e quantos arquivos distintos.

| Contagem | Execução |
| --- | --- |
| ≤ 2 tarefas **e** ≤ 4 arquivos | inline, em TDD |
| ≥ 3 tarefas **ou** ≥ 5 arquivos | `superpowers:subagent-driven-development` |

Anuncie em uma frase — "plano com 5 tarefas em 7 arquivos → subagent-driven-development" — escreva no estado, e execute. A contagem é a decisão inteira: não pese "a issue parece simples", não pergunte.

### SDD dentro do loop

- **Não crie worktree.** O checkout é o workspace.
- **O ledger vive em `.superpowers/sdd/<plano>/`**, git-ignored. Nunca entra no `git add`.
- **Toda dispatch carrega os gates do repo como Global Constraints**, copiados literalmente do `CLAUDE.md`, mais: `git add` caminho a caminho, nunca `-A`; nunca `--no-verify`.
- **Especifique o modelo em toda dispatch.**
- **A revisão final de branch dele é a revisão de fechamento.** Não rode duas.
- **Os bloqueios são os cinco desta skill**, não os do SDD.

## Gates

Os gates são do repositório. Leia o `CLAUDE.md` e siga os comandos de lá — ele tem precedência sobre qualquer suposição.

Enquanto ele não existir (issues de bootstrap), o piso é:

| Repo | Gates |
| --- | --- |
| `slate-api` | `ruff check .`, `ruff format --check .`, `mypy app`, `pytest -q` |
| `slate-web` | `npm run lint`, `npm run build`, `npm test -- --watch=false` |

**A issue de bootstrap de cada repo tem uma tarefa implícita: criar o `CLAUDE.md`** com os comandos reais de gate, base, e convenções. Sem ele, toda execução seguinte redescobre os comandos e erra.

**As três invariantes da spec §6.4 são gate permanente do `slate-api`** a partir do momento em que a suite existe. Nenhum PR entra com elas vermelhas, mesmo que o diff não toque no motor.

`pytest` com testcontainers precisa de Docker. Se o runner não tiver, é bloqueio ambiental — não caia para SQLite: a spec §11 é explícita sobre por quê.

## Fechamento

Três passos, nesta ordem, sem pergunta entre eles.

**1. Revisão contra o plano e a issue.** `superpowers:requesting-code-review`: despache um subagente `general-purpose` com o template `code-reviewer.md`. `{PLAN_OR_REQUIREMENTS}` = o caminho do plano **mais os critérios de aceite da issue colados literalmente**. É isso que se valida, não "o código em geral". `BASE_SHA` = `git merge-base origin/main HEAD`. Nunca revise o próprio diff inline. Sob SDD, a revisão final dele já é esta.

**2. Uma onda de correção, e só uma.** Critical/Important vão para **um** subagente com a lista completa, seguido de **uma** re-review escopada no diff da correção. O que sobrar vira linha no corpo do PR (`achado conhecido: <o quê> — <por que fica>`). Minor vai direto para o corpo do PR, sem onda.

**3. `superpowers:finishing-a-development-branch`, opção 2 já escolhida:** push e PR contra `main`. Não apresente o menu — apresentá-lo reintroduz o gate que este loop remove.

```bash
git push -u origin <branch>
gh pr create --repo "$OWNER/$REPO" --base main \
  --title "<tipo>: <título da issue>" \
  --body "Closes #<n>

## Plano
docs/plans/<numero>-<slug>.md

## O que mudou
<resumo em 3–5 linhas>

## Critérios de aceite
<checklist da issue, marcado>

## Pressupostos
<um por linha, ou 'nenhum'>

## Achados conhecidos
<da revisão, ou 'nenhum'>"

gh issue edit <n> --repo "$OWNER/$REPO" \
  --add-label status:review --remove-label status:doing
```

`Closes #<n>` é obrigatório — é o que fecha a issue no merge.

## Fase `ci`

PR aberto não é trabalho entregue. Só escreva `entregue` no estado depois de ler um `pass`.

```bash
gh pr checks <n> --repo "$OWNER/$REPO" --json name,state,bucket,link
```

`bucket`: `pass` | `fail` | `pending` | `skipping`.

Reconsulte em intervalos de 30–60s, nunca em busy-loop. **Teto de 20 minutos** — estourou, comente a URL do run na issue e encerre sem bloquear (o PR fica aberto e a próxima execução retoma na fase `ci`).

```bash
gh run view <run-id> --repo "$OWNER/$REPO" --log-failed | tail -200
```

**Nunca despeje o log inteiro no contexto.** `tail -200`; se faltar, 400.

**Repo ainda sem `.github/workflows/`** pula esta fase em uma linha. Vale para as primeiras issues de M1 — e configurar CI é escopo da issue de deploy, não desta.

Falha do CI em arquivo que você não abriu **é sua**: o CI é a primeira vez que a suíte inteira roda. Reproduza rodando **o teste que o log nomeia**, corrija em TDD, empurre na mesma branch, acompanhe de novo. **Três rodadas no máximo.** Falha de infraestrutura não consome rodada e bloqueia na hora.

Cada rodada vira uma linha em comentário na issue: o que o CI acusou e o que mudou. É o que explica os commits que vieram depois da revisão.

## Se o contexto compactar

Compactação **não para o loop**. Continue de onde estava.

O que ela custa é o detalhe: número do PR, quais commits existem, em que rodada de CI você está, quais tarefas do SDD fecharam. Depois de compactar, **sua lembrança é a última fonte**. A ordem é: GitHub, depois git, depois o arquivo de estado, depois você.

Escreva no estado quando o fato for caro de redescobrir, não só na virada de fase: o número do PR assim que ele existe, o id do run de CI, a rodada de correção.

## Procedimento

1. `git clone` do repo, `main`, `pull`.
2. **Leia `.claude/loop-state.md` e confira contra o GitHub** (`status:doing`, PRs abertos, branches remotas). O mundo real ganha do arquivo.
3. Se o estado registra uma issue com fase diferente de `entregue`, **retome-a**. Não pegue uma nova.
4. **Estado sem issue reivindicada é lixo — descarte e siga.** Um estado que registra bloqueio de rodada, erro de execução anterior ou pré-requisito ausente, sem número de issue e sem label `status:doing` correspondente no GitHub, não descreve trabalho em curso. Sobrescreva-o e continue pelo passo 5. **Nunca encerre a rodada por causa do que o estado anterior diz.**
5. Liste as filas de `slate-api` e depois `slate-web`, aplique os filtros e a ordem. Vazio nos dois → pare.
6. `gh issue view <n> --comments`. O corpo da issue é o requisito completo.
7. `gh issue edit <n> --add-label status:doing`. Comente a rota escolhida.
8. Escreva o estado, commite em `main`, empurre.
9. Crie a branch. Instale dependências. Rode o gate mais barato para confirmar o ambiente.
10. **Escreva o plano** em `docs/plans/<numero>-<slug>.md`, a partir da issue. Commite na branch.
11. **Conte tarefas e arquivos do plano** e anuncie a rota de execução.
12. Implemente em TDD — inline ou por SDD. Rode os gates do `CLAUDE.md`.
13. `git add` **caminho a caminho**, nunca `-A`. Nunca `--no-verify`.
14. Atualize o estado a cada virada de fase: checkout `main`, commit, push, volte para a branch.
15. Revisão contra o plano e os critérios de aceite, depois **uma** onda de correção.
16. Push e PR contra `main`, com `Closes #<n>`. Label `status:review`.
17. Fase `ci` até `pass` (≤ 3 rodadas de correção).
18. Marque `entregue` no estado, commite em `main`, comente o resumo na issue e **encerre a execução**.

## Template do estado

`<repo>/.claude/loop-state.md`. Reescreva inteiro a cada transição.

```markdown
# Slate Issue Loop — estado

- **repo:** slate-api
- **issue:** #12 — Motor: cascata de edição com escopo this/forward/all
- **url:** https://github.com/<owner>/slate-api/issues/12
- **plano:** docs/plans/12-cascata-edicao.md
- **milestone:** M2 · Motor
- **rota:** writing-plans
- **execucao:** 4 tarefas / 5 arquivos → subagent-driven-development
- **fase:** analise | plano | implementacao | gates | revisao | pr | ci | entregue
- **branch:** feat/12-cascata-edicao (base main, sha a1b2c3d)
- **pr:** https://github.com/<owner>/slate-api/pull/9
- **ci:** run 4821 failed (rodada 1 de 3)
- **atualizado:** 2026-08-22T14:30Z

## Onde parei
Invariante 2 escrita e falhando (vermelho esperado). Falta o filtro
`NOT edited_manually` no service de cascata.

## Pressupostos
- Escopo `forward` inclui a competência do próprio mês editado — a spec §6.2
  usa `>=`, não `>`.

## Bloqueios registrados
- nenhum
```

## Erros comuns

| Erro | Correção |
| --- | --- |
| Manter o estado só em disco local | O ambiente é descartável. Estado é commit em `main`, empurrado. |
| Commitar o estado na branch da issue | Suja o diff e some se o PR não for mergeado. Sempre `main`, commit isolado. |
| Confiar no arquivo de estado sem conferir o GitHub | A execução pode ter morrido entre a ação e o commit. Labels e PRs são a verdade. |
| Perguntar ao usuário | Não há usuário. Ou a escada decide, ou é bloqueio → label + comentário + encerrar. |
| Encerrar bloqueado deixando `status:doing` | A próxima execução tentaria retomar. Troque para `status:blocked`. |
| Criar worktree | Uma issue por execução em ambiente efêmero. Branch direto no checkout. |
| **Bloquear porque uma spec ou documento citado não existe** | O corpo da issue é o requisito completo. Anote a linha no plano e implemente. |
| **Encerrar a rodada por causa de um pré-requisito de documentação** | Os cinco bloqueios são ambiente e permissão. Documentação nunca está entre eles. |
| **Bloquear antes de reivindicar issue** | Bloqueio é sempre de uma issue específica. Sem issue reivindicada: ou a fila está vazia, ou pegue uma e trabalhe. |
| **Encerrar porque o estado anterior diz "bloqueado"** | Estado sem issue reivindicada é lixo. Sobrescreva e siga. |
| Pular o plano porque "a issue é simples" | Toda issue gera plano. É o que a revisão final valida e o que a contagem lê. |
| Chamar `superpowers:brainstorming` | O gate dela é o que este loop remove. |
| Pegar issue de M3 do `slate-api` antes da suite de invariantes | Bloqueio duro. Nenhum endpoint antes das três invariantes verdes. |
| Pegar issue de `slate-web` M2 antes do OpenAPI publicado | Bloqueio duro. Sem contrato tipado a UI vira retrabalho. |
| Ignorar `## Depende de` no corpo da issue | Dependência aberta = fora da fila nesta rodada. |
| Perguntar qual issue pegar | A ordem é determinística: repo, milestone, prioridade, número. |
| Cair para SQLite quando falta Docker | A spec §11 é explícita. É bloqueio ambiental. |
| Pular a criação do `CLAUDE.md` na issue de bootstrap | Toda execução seguinte redescobre os gates e erra. |
| Abrir o PR sem `Closes #<n>` | É o que fecha a issue no merge. |
| Fechar a issue à mão | Quem fecha é o merge. |
| `git add -A` | Caminho a caminho. Há artefatos não versionados no checkout. |
| Abrir o PR sem revisão contra o plano | O PR chega revisado, não cru. |
| Segunda onda de correção até zerar achados | Uma onda, uma re-review. O resto vira linha no corpo do PR. |
| Apresentar o menu do `finishing-a-development-branch` | Opção 2 já escolhida. Menu é gate. |
| Marcar `entregue` quando o PR abre | Entregue é CI verde. |
| Descartar falha de CI como "arquivo que não toquei" | O CI é a primeira rodada da suíte inteira. A regressão é sua. |
| Despejar o log inteiro do run | `tail -200`; se faltar, 400. |
| Emendar a próxima issue na mesma execução | Uma issue por execução. Encerre. |
| Confiar na lembrança depois de compactar | GitHub > git > estado > você. |
```
