# Plans.md — Empório da Música (agente Sônia)

Purpose: entregar até **2026-11-02 23:59** um agente de atendimento em Python que roda de ponta a ponta com
`uv run emporio chat` a partir de um clone limpo (uma chave de LLM), com regras de negócio testadas, 3–5 exemplos e
README bilíngue. Extras de escala só depois do núcleo testado.

Fontes de verdade (precedência): `docs/arquitetura.md` + `docs/premissas.md` (contrato de produto) > `docs/pagamento.md`,
`docs/deploy.md` > este arquivo (contrato de tarefas). Não existe `spec.md` na raiz: o contrato de produto é `docs/`.

Convenções:
- Cada tarefa é executada pelo **agente especialista** indicado; Claude orquestra, revisa, roda os testes, faz commit e push por tarefa.
- Tags: `[lane:fast|gate|release]`, `[tdd:required]` / `[tdd:skip:<motivo>]`, `@agente`.
- Gate de qualidade em toda tarefa com código: `uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run lint-imports && uv run pytest` sem erros.
- Testes nunca chamam LLM real nem leem `.env`: usam `LLMFalso` (respostas roteirizadas) e `Settings` montado no teste.
- **Linha de corte**: se a Fase 1 não estiver `cc:完了` até 2026-11-01 18:00, a Fase 2 é reduzida à 2.1 (API) e o resto vai para "Limitações e próximos passos".

### Hierarquia Epic → Story → Task

| Epic | Story | Tasks | Módulos/adapters que fecha |
|---|---|---|---|
| E1 Núcleo | S1.A Fundação | 1.0–1.2 | `portas/`, `composicao.py` (esqueleto) |
| | S1.B Regras de negócio | 1.3–1.6 | `modulos/comercial`, `modulos/politicas`, `adaptadores/csv`, `adaptadores/memoria`, `modulos/{catalogo,pedidos,atendimento}` |
| | S1.C Agente | 1.7–1.11 | `adaptadores/llm`, `adaptadores/motor/nativo` |
| | S1.D Interface e entrega do núcleo | 1.12–1.16 | `adaptadores/cli` |
| E2 Extras | S2.A API e segurança | 2.1, 2.5, 2.6 | `adaptadores/api` |
| | S2.B Infra de dados | 2.2, 2.3 | `adaptadores/db`, `adaptadores/redis` |
| | S2.C Observabilidade e deploy | 2.4, 2.8 | instrumentação |
| | S2.D Motores e pagamento | 2.7, 2.9 | `adaptadores/motor/n8n`, gateway fake |
| E3 Entrega | S3.A Diagramas, revisão e publicação | 3.0–3.2 | — |

GitHub: [Project #4](https://github.com/users/carlosbandelli/projects/4) (Todo → Doing → Done). Issues: epics #1–#3, stories #4–#12, tasks #13–#41 na ordem do plano (1.0 = #13 … 1.16 = #29, 2.1 = #30 … 2.9 = #38, 3.0 = #39 … 3.2 = #41), ligadas como sub-issues. Ao iniciar uma task: issue em Doing; ao concluir: commit com `Closes #N` e item em Done.

### code-review-graph (combinado com o usuário em 2026-11-01)

- **Quando atualizar**: (a) ao concluir uma task que fecha um módulo ou adapter da tabela acima; (b) ao fechar cada Story;
  (c) ao fechar cada Epic. Ferramenta: `build_or_update_graph_tool` (incremental). Em (b) e (c), também
  `run_postprocess_tool` e `get_architecture_overview_tool`.
- **Quando usar**: toda análise de código (revisão de entrega de subagente, `/code-review`, depuração, impacto de mudança,
  conferência das regras C1–C5) começa pelo grafo: `get_minimal_context_tool` / `get_review_context_tool` para o diff,
  `get_impact_radius_tool` antes de mexer em código compartilhado, `detect_changes_tool` ao revisar uma Story. Leitura
  de arquivos só para os trechos que o grafo apontar.
- **Evidência**: o commit que fecha a Story/Epic cita "grafo atualizado" com a contagem de nós e arestas
  (`list_graph_stats_tool`); a revisão da Story registra os achados do grafo (ex.: dependência que viola C1–C5, hubs inesperados).
- Os dados locais do grafo não vão para o repositório (`.gitignore`, task 1.0).

## Spec delta (aplicado na tarefa 1.1, antes do código)

Destino: `docs/arquitetura.md` e `docs/premissas.md`.

1. **arquitetura §3.2 (nova) — Contrato da porta `MotorDeAtendimento`**:
   `async responder(ctx: TenantContext, sessao_id: str, mensagem: MensagemCliente) -> RespostaAtendimento`.
   `RespostaAtendimento`: `texto`, `idioma`, `nivel | None`, `provedor | None`, `tools_chamadas: list[str]`, `degradada: bool`.
   Invariantes que **todo** motor cumpre (suíte de contrato em `tests/contrato/test_motor_atendimento.py`):
   - I1 texto não vazio para qualquer mensagem válida; mensagem vazia/acima do limite → mensagem fixa, sem chamar LLM.
   - I2 histórico: a 2ª mensagem da mesma sessão enxerga a 1ª (via `SessaoStore`).
   - I3 isolamento: mesmo `sessao_id` em tenants diferentes não compartilha histórico nem configuração.
   - I4 nunca lança exceção por falha de LLM/backend: devolve a mensagem fixa traduzida com `degradada=True`.
   - I5 respeita o deadline do tenant (padrão 30 s); estourou → mensagem fixa, `degradada=True`.
   - I6 preço, estoque e status de pedido só aparecem se a tool correspondente foi chamada (espião na fachada de tools).
   - I7 responde no idioma da sessão (pt-BR/en-US).
   - I8 sem estado global: duas instâncias com tenants diferentes em `asyncio.gather` não se contaminam.
2. **arquitetura §3.3 (nova) — Composition root**: `emporio.composicao.montar(settings) -> Aplicacao` é o único lugar que
   instancia adapters. Proibido: singletons de módulo, `global`, cache em variável de módulo (exceto constantes imutáveis).
   CLI e API recebem só `MotorDeAtendimento` (e `Diagnostico`), nunca LLMs, repositórios ou tools.
   Seleção de motor por `MOTOR_ATENDIMENTO` (`nativo` padrão; `n8n` na Fase 2); valor desconhecido → erro de configuração na inicialização.
   **Estrutura de pastas**: monólito modular no meio, hexagonal só na borda.

   ```
   src/emporio/
   ├── modulos/                  ← MONÓLITO MODULAR (Python simples, sem I/O nem SDK)
   │   ├── comum/                shared kernel: Dinheiro e tipos simples usados por todos os módulos
   │   ├── catalogo/             Produto, Categoria → buscar_produtos, detalhar_produto
   │   ├── comercial/            Dinheiro, Parcelamento, PlanoPagamento → simular_pagamento
   │   ├── pedidos/              Pedido, StatusPedido → consultar_pedido (com verificação)
   │   ├── politicas/            HorarioLoja, Frete, JanelaDevolucao → status_loja
   │   └── atendimento/          Solicitacao → registrar_solicitacao
   │       (cada módulo: dominio.py = regras; aplicacao.py = casos de uso/tools, API pública do módulo)
   ├── portas/                   ← HEXAGONAL: contratos (Protocol) e tipos compartilhados (TenantContext)
   ├── adaptadores/              ← HEXAGONAL: implementações trocáveis
   │   ├── cli/                  entrada (Fase 1)        · api/ entrada (Fase 2)
   │   ├── motor/nativo/         AgenteNativo: roteador + prompt CAG + loop de tools
   │   ├── motor/n8n/            AgenteN8n (Fase 2)
   │   ├── llm/                  groq, gemini, claude, falso
   │   ├── csv/, memoria/        dados e estado (Fase 1)
   │   └── db/, redis/           Postgres e Redis (Fase 2)
   └── composicao.py             ← único ponto que liga porta ↔ adapter (lê Settings/.env e MOTOR_ATENDIMENTO)
   config/                       ← na raiz do repositório (o Dockerfile copia /app/config): tenants.yaml, tenants/<id>/politicas.md
   ```

   Regras de dependência (só para dentro), verificadas pelo **import-linter** (`uv run lint-imports`, no gate e no CI):
   - C1 camadas (de fora para dentro): `composicao` → `adaptadores` → `modulos.*.aplicacao` → `portas` → `modulos.*.dominio`
     (+ `modulos.comum`). Os casos de uso dependem das portas; as portas podem usar tipos do domínio; o domínio não importa
     nada do projeto além de `modulos.comum`. Nenhuma camada importa uma camada de fora. (Corrigido em 2026-11-01: a versão
     anterior punha `portas` acima de `modulos`, o que impedia os casos de uso de depender das portas.)
   - C2 `emporio.modulos` não importa `emporio.adaptadores`, `emporio.composicao` nem SDKs externos (`groq`, `google.genai`,
     `anthropic`, `httpx`, `sqlalchemy`, `redis`, `fastapi`, `typer`).
   - C3 entre módulos: um módulo só importa o `aplicacao` de outro, nunca o `dominio` (contrato `forbidden` por módulo);
     `modulos.comum` é permitido para todos.
   - C4 adaptadores de entrada (`cli`, `api`) só importam `portas` e `composicao`; nunca `modulos`, `llm`, `csv`, `db`, `motor`.
   - C5 adaptadores independentes entre si (contrato `independence`): `motor.nativo` não importa `llm.claude`, só a porta
     `LLMProvider`.
   - Consequência: a Fase 2 inteira é "novo adapter + uma linha em `composicao.py`"; `modulos/` não muda.
3. **premissas #19** — Sem nenhuma chave de LLM: `emporio diagnostico` explica o que falta e `emporio chat` encerra com
   mensagem clara (código de saída 2), sem stack trace.
4. **premissas #20** — Manual em texto: o PDF é convertido uma vez para `config/tenants/emporio/politicas.md`
   (revisado à mão e versionado). O CAG lê o `.md`; o PDF fica como origem.
5. **premissas #21** — Modo local (Fase 1): sessão, rate limit e circuito em memória por processo; o histórico some ao
   sair do `chat`. Está documentado como limitação do modo local.

## Fase 1 — Núcleo (Required)

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 1.0 | `[lane:gate]` `[tdd:skip:scaffold]` `@python-pro` **Esqueleto do projeto**: `pyproject.toml` (Python 3.12, src layout `src/emporio`, entry point `emporio = "emporio.adaptadores.cli:app"`), grupos `dev` (pytest, pytest-asyncio, ruff, mypy, import-linter), `uv.lock`, configs de ruff/mypy (strict em `src`), `tests/` com 1 smoke test, `.env.example` revisado (chaves vazias, `MOTOR_ATENDIMENTO=nativo`, `DATA_REFERENCIA=`). Pastas exatamente como a árvore do Spec delta §3.3 (Fase 1: `modulos/*`, `portas/`, `adaptadores/{cli,motor/nativo,llm,csv,memoria}`, `composicao.py`, `config/`), com `__init__.py`. Contratos C1–C5 do import-linter em `pyproject.toml` (`[tool.importlinter]`) e passo `uv run lint-imports` adicionado ao job de lint do `.github/workflows/ci-cd.yml`. `.gitignore` passa a ignorar `.claude/memory/`, `.claude/sessions/`, `.claude/state/`, `var/` e a pasta local do code-review-graph (`.code-review-graph/`; confirmar o nome na primeira execução). Ao fim da task, primeiro `build_or_update_graph_tool`. | `uv sync --locked` ok; ruff check + format --check ok; `mypy src` ok; `uv run lint-imports` com 5 contratos KEPT; prova de violação: um import proibido temporário (`modulos.comercial` → `adaptadores.llm`) faz `lint-imports` falhar (registrado no commit, depois revertido); `pytest` 1 passed; `uv run emporio --help` sai com 0; `git status` não mostra `.claude/state`; CLAUDE.md atualizado com os comandos e a árvore | - | cc:完了 |
| 1.1 | `[lane:gate]` `[tdd:skip:docs-contract]` `@architect-reviewer` **Aplicar o Spec delta** acima em `docs/arquitetura.md` e `docs/premissas.md`. | Seções §3.2, §3.3 (com a árvore e os contratos C1–C5) e premissas #19–#21 presentes; `git diff --check` limpo; nenhuma decisão anterior contradita | 1.0 | cc:完了 |
| 1.2 | `[lane:gate]` `[tdd:required]` `@python-pro` **Portas, TenantContext e Settings**: `Protocol`s de `MotorDeAtendimento`, `LLMProvider`, `Catalogo`, `Pedidos`, `Clientes`, `Solicitacoes`, `SessaoStore`, `RateLimiter`, `EstadoCircuito`, `Relogio`; `TenantContext` imutável (tenant_id, idioma, papel); `Settings` (pydantic-settings, lê `.env` só na composição); `config/tenants.yaml` com o tenant `emporio` (persona Sônia, contatos das premissas #1–#2, idiomas, deadline, limites); loader com validação. | Testes: tenant inexistente → erro claro; yaml inválido → erro com o campo; `TenantContext` é frozen; grep sem `global ` em `src/`; gate ok | 1.0 | cc:完了 |
| 1.3 | `[lane:gate]` `[tdd:required]` `@python-pro` **Domínio Comercial** (+ shared kernel): `Dinheiro` em `modulos/comum` (centavos `int`, `ROUND_HALF_UP`, formatação por locale com `babel`), `PrecoVigente` (promoção ativa não acumula; PIX −5% só sobre preço de tabela), `Parcelamento` (≤3x mín. R$50; 4–6x mín. R$80; 7–12x mín. R$100; sobra na 1ª parcela, premissa #17), `PlanoPagamento` (combinação só acima de R$2.000). | Testes parametrizados cobrindo: PIX sem promo; promo + PIX (sem desconto extra); vários registros de promo (produtos 93/96/99, só `is_active=1`); R$1.000 em 3x = 333,34+333,33+333,33; limite exato de cada faixa; combinação em R$2.000,00 recusada e em R$2.000,01 aceita; `R$ 1.299,00` × `R$1,299.00`; cobertura ≥ 95% em `modulos/comercial` e `modulos/comum` | 1.0 | cc:WIP |
| 1.4 | `[lane:gate]` `[tdd:required]` `@python-pro` **Domínio Políticas** (regras determinísticas): `HorarioLoja` (seg–sex 9–18, sáb 9–13, dom/feriado fechado, feriados nacionais via `holidays`, "próxima abertura"); `Frete` (Campo Grande ≥ R$500 grátis, senão R$35; outras cidades: modalidades e prazos sem valor, premissas #11–#12); `JanelaDevolucao` (arrependimento 7 dias, troca por preferência 7 dias, defeito 30 dias da compra, garantia legal 90 dias do recebimento, premissas #5–#7). Funções puras que recebem `agora` (o adapter de `Relogio` com `DATA_REFERENCIA` fica na 1.5). | Testes: sexta 17:59 aberta / 18:00 fechada com próxima abertura sábado 09:00; sábado 13:00 → segunda 09:00; feriado nacional fechado; R$499,99 × R$500,00 em CG; janela no 7º e no 8º dia; pedido não entregue → janela não iniciada; gate ok | 1.0 | cc:WIP |
| 1.5 | `[lane:gate]` `[tdd:required]` `@data-engineer` **Adapters CSV + limpeza de dados**: `CatalogoCsv`, `PedidosCsv` (com itens), `ClientesCsv`, `SolicitacoesJsonl` (`var/solicitacoes.jsonl`, gitignored). Limpeza: `specs` JSON → dict, `payment_method` (`credit_Nx` → crédito N), datas, status como enum, promoções ativas; categorias 6–8 sem produtos. Todos recebem `TenantContext` e o caminho vem do tenant. Mais os adapters `memoria` (`SessaoStoreMemoria`, `RateLimiterMemoria`, `EstadoCircuitoMemoria`). | Testes: 65 produtos carregados, IDs começam em 81; nenhum `specs` inválido derruba a carga (linha problemática é registrada e pulada); filtros por categoria/preço/status; pedido `shipped` tem rastreio; `SessaoStoreMemoria` isola por tenant; decisões de limpeza listadas em `docs/premissas.md`; gate ok | 1.2 | cc:TODO |
| 1.6 | `[lane:gate]` `[tdd:required]` `@python-pro` **Casos de uso (tools puras, sem LLM)**: `buscar_produtos` (texto, categoria, faixa de preço, só `active` por padrão, sugere alternativas sem estoque), `detalhar_produto` (estoque direto do repositório, preço vigente, PIX, parcelas; descontinuado → equivalentes; promoção expirada → avisa), `simular_pagamento` (recalcula do catálogo, nunca confia em valor vindo do LLM), `consultar_pedido` (exige nº + e-mail ou telefone, premissa #15; sem correspondência → nenhuma informação do pedido), `status_loja`, `registrar_solicitacao` (reclamação, cotação de frete, outros; chave de idempotência). Schemas JSON das tools gerados dos modelos Pydantic. Detecção de acessório fora de escopo como dado (lista de termos no tenant). | Um teste por regra de negócio do CLAUDE.md (horário, PIX, parcelas, devolução, frete, sem estoque, descontinuado, promoção expirada); `consultar_pedido` com e-mail errado não revela status, itens nem nome; idempotência: 2 chamadas com a mesma chave → 1 registro; "violões até R$1000" devolve só violões ativos ≤ R$1.000; gate ok | 1.3, 1.4, 1.5 | cc:TODO |
| 1.7 | `[lane:gate]` `[tdd:skip:content-extraction]` `@llm-architect` **Manual para CAG + system prompt**: converter o PDF para `config/tenants/emporio/politicas.md` (revisado contra o PDF, seções §1–§10 preservadas); template do system prompt: persona Sônia (arquitetura §9.2), regras "o LLM redige, o código calcula", nunca afirmar preço/estoque/prazo sem tool, fora de escopo (acessórios, categorias sem produto, premissas #9–#10), idioma (§10), fechamento; manual no bloco cacheável. | `politicas.md` contém as 10 seções e os valores-chave (horários, 5%, faixas de parcela, 7/30/90 dias, R$500/R$35); teste verifica que o prompt renderizado contém persona, manual e regras para os dois idiomas; revisão humana registrada no commit | 1.2 | cc:TODO |
| 1.8 | `[lane:gate]` `[tdd:required]` `@llm-architect` **Adapters `LLMProvider` + resiliência**: interface única com tool calling (mensagens, tools, `RespostaLLM` com tool_calls e uso de tokens); adapters Groq, Gemini (google-genai), Claude (`claude-opus-5-5`, prompt caching no bloco do manual); SDKs com `max_retries=0`; `tenacity` (`wait_random_exponential(multiplier=0.5, max=4)`, 3 tentativas, só erros transitórios, `Retry-After` ≤ 5 s); circuit breaker e bulkhead com os valores da §5.2; classificação de erro transitório × permanente; `LLMFalso` roteirizável para testes. Provedor sem chave não é registrado. | Testes com `LLMFalso` e fakes HTTP: 401 abre o circuito na hora (10 min); 5 transitórios seguidos abrem; meio-aberto fecha com 1 sucesso; retry não estoura o orçamento do deadline; bulkhead limita concorrência; nenhum teste usa rede; gate ok | 1.2 | cc:TODO |
| 1.9 | `[lane:gate]` `[tdd:required]` `@llm-architect` **Roteador de complexidade**: classificador curto (Groq; sem Groq, o primeiro provedor disponível) → `simples`/`media`/`complexa`; na dúvida sobe de nível; cadeia de fallback (Claude → Gemini → Groq) filtrada pelas chaves presentes; todos fora → mensagem fixa. | Testes: resposta ambígua do classificador → nível maior; só chave Gemini → todos os níveis usam Gemini; Claude com circuito aberto → Gemini; falha do classificador → `complexa`; gate ok | 1.8 | cc:TODO |
| 1.10 | `[lane:gate]` `[tdd:required]` `@llm-architect` + `@python-pro` **Porta `MotorDeAtendimento` + `AgenteNativo` (em `adaptadores/motor/nativo/`) + seleção por configuração**: loop agentic (roteia → monta o prompt CAG do tenant → LLM → executa tools pela fachada de casos de uso → repete até a resposta final, no máximo N iterações), histórico na `SessaoStore`, idioma da sessão (pergunta na 1ª mensagem quando ambíguo), deadline por mensagem, catálogo de mensagens fixas pt-BR/en-US (erro, fallback total, loja fechada, limite). `composicao.montar()` escolhe o motor por `MOTOR_ATENDIMENTO`. Tools entregues ao LLM dependem do papel do `TenantContext` (cliente não recebe tools administrativas). | Testes com `LLMFalso`: pergunta de preço → `detalhar_produto` chamada e valor do texto igual ao da tool; limite de iterações atingido → mensagem fixa; `MOTOR_ATENDIMENTO=xyz` → erro de configuração na inicialização; papel `cliente` não recebe tools admin; gate ok | 1.6, 1.7, 1.9 | cc:TODO |
| 1.11 | `[lane:gate]` `[tdd:required]` `@test-automator` **Suíte de contrato do `MotorDeAtendimento`** (invariantes I1–I8 do Spec delta) em `tests/contrato/`, parametrizada por uma fixture `motor_factory` que recebe a lista de motores registrados; `AgenteNativo` (com `LLMFalso`) passa; `tests/contrato/README.md` explica como um motor novo (ex.: `AgenteN8n`) entra na suíte. | `pytest tests/contrato -v` mostra os 8 invariantes passando para `nativo`; um motor "quebrado" de propósito (fixture interna) falha em I4 e I6, provando que a suíte detecta violações; gate ok | 1.10 | cc:TODO |
| 1.12 | `[lane:gate]` `[tdd:required]` `@cli-developer` **CLI** (typer): `emporio chat [--tenant emporio] [--idioma] [--gravar arquivo.md]` em modo local (mesmo processo); `emporio diagnostico` (provedores com chave, motor ativo, tenant, dados carregados, data de referência; nunca imprime chaves). A CLI importa apenas `composicao` e a porta. `--gravar` salva a conversa em Markdown para os exemplos. | Testes com `CliRunner` + `LLMFalso`: conversa de 2 turnos e `sair`; sem chave → saída 2 com mensagem da premissa #19; `diagnostico` mascara chaves; contrato C4 do import-linter KEPT (CLI só importa `portas` e `composicao`); gate ok | 1.10 | cc:TODO |
| 1.13 | `[lane:gate]` `[tdd:required]` `@test-automator` **Cenários ponta a ponta das regras** (motor + tools + CSV, LLM roteirizado): os 4 cenários do enunciado (violões até R$1000, endereço, Takamine GD20, "me arrependi, posso devolver?"), acessório fora de escopo (cordas), produto sem estoque → alternativa, descontinuado → equivalente, pedido com verificação falha, fora do expediente, frete fora de CG → cotação registrada. Opcional e marcado `@pytest.mark.llm_real` (pulado sem chave): os mesmos cenários contra LLM real com asserções sobre as tools chamadas. | `uv run pytest` sem chaves: tudo passa e os testes `llm_real` aparecem como skipped; 10 cenários verdes; cobertura total de `src` ≥ 85% | 1.11, 1.12 | cc:TODO |
| 1.14 | `[lane:gate]` `[tdd:skip:recorded-artifacts]` `@prompt-engineer` **Exemplos de conversa (3–5)** gravados com `emporio chat --gravar` em `exemplos/`, com provedor e data de referência no cabeçalho: (1) violões até R$1000 com PIX e parcelas; (2) "me arrependi da compra" aplicando a janela de 7 dias a um pedido real com verificação; (3) acessório fora de escopo + endereço/horário; (4) em **inglês**, preço do Takamine GD20; (5) opcional: reclamação registrada. Pelo menos um no nível complexo com **Claude** (antes de a chave expirar). Ajustes de prompt que surgirem voltam para a 1.7 como commit separado. | 3–5 arquivos em `exemplos/`; cada valor citado bate com os CSVs (conferido por `scripts/conferir_exemplos.py` ou checklist no PR); ≥ 1 não trivial (dados ao vivo ou política); 1 em en-US; 1 com Claude | 1.13 | cc:TODO |
| 1.15 | `[lane:gate]` `[tdd:skip:docs-only]` `@technical-writer` → `@content-quality-editor` **README bilíngue** (inclui revisar o modelo de variáveis de ambiente: na 1.0 o usuário pediu para não editá-lo; as variáveis novas têm padrão no código) (`README.md` pt-BR principal, `README.en-US.md`, links de troca no topo): "Como rodar em 5 minutos" (Windows/macOS/Linux, 3 caminhos da §9.1), decisões e justificativas (Python + uv, framework próprio × LangChain, roteamento de LLM, CAG × RAG, prompt, hexagonal nas bordas, porta `MotorDeAtendimento`), premissas (link para `docs/premissas.md` com resumo), limitações e próximos passos (arquitetura §13 + o que ficou da Fase 2), **como usei assistentes de IA** (fluxo plano → subagentes → revisão → commit por tarefa). | Os dois READMEs têm as mesmas seções na mesma ordem; todos os comandos citados existem (verificados na 1.16); seção de uso de IA presente; sem padrões de texto de IA apontados pelo `content-quality-editor` | 1.14 | cc:TODO |
| 1.16 | `[lane:gate]` `[tdd:skip:verification]` `@claude` (orquestrador) **Verificação de clone limpo** (critério de aceite do núcleo): `git clone` numa pasta temporária → `uv sync` → copiar `.env` com só a chave Gemini → `uv run emporio diagnostico` → `uv run emporio chat` (3 perguntas) → `uv run pytest`. Repetir só com a chave Groq. | Log das execuções anexado ao commit/PR; `pytest` verde no clone; chat responde nas duas configurações de chave; nenhum passo fora do README foi necessário | 1.15 | cc:TODO |

## Fase 2 — Extras (só após 1.16 `cc:完了`; ordem = prioridade de valor)

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 2.1 | `[lane:gate]` `[tdd:required]` `@fastapi-developer` **API FastAPI** em `emporio.adaptadores.api.app:app` (nome exigido pelo Dockerfile): `POST /v1/chat` (`session_id`, `mensagem`, `idioma` opcional, `Accept-Language`), resolução de tenant por subdomínio ou `X-Tenant-ID`, `/health`, `/ready`, `429` com `Retry-After`, erros com mensagens fixas traduzidas. Usa só `MotorDeAtendimento` via `composicao`. CLI ganha `--api URL` (modo remoto). | Testes `httpx.AsyncClient`: tenant desconhecido → 404; sem tenant → 400; chat com `LLMFalso` → 200; `/health` 200 sem dependências; suíte de contrato roda também através da API (adapter de teste); gate ok | Fase 1 | cc:TODO |
| 2.2 | `[lane:gate]` `[tdd:required]` `@postgres-pro` **Postgres com schema por tenant**: adapters SQLAlchemy/psycopg dos 4 repositórios com `search_path` por tenant; Alembic em loop por schema; advisory lock; seed dos CSVs no schema `emporio`; módulo `emporio.adaptadores.db.migrar` (exigido por compose e CI). Os mesmos testes de contrato dos repositórios CSV rodam contra o Postgres. | `python -m emporio.adaptadores.db.migrar` idempotente (2 execuções sem erro); contrato dos repositórios verde em CSV e Postgres (testcontainers ou serviço do CI); consulta sem tenant é impossível (teste); 2 processos concorrentes migram 1 vez só | 2.1 | cc:TODO |
| 2.3 | `[lane:gate]` `[tdd:required]` `@backend-developer` **Redis**: `SessaoStoreRedis` (TTL), `RateLimiterRedis` (token bucket em Lua, 4 camadas da §5.1, chaves com tenant), cache-aside do catálogo (estoque nunca do cache), fail-open com bucket local se o Redis cair (timeout 300 ms). | Testes: 429 na camada certa com `Retry-After`; Redis fora → chat segue sem histórico e rate limit local; chaves sempre com prefixo de tenant; contrato de `SessaoStore` verde em memória e Redis | 2.1 | cc:TODO |
| 2.4 | `[lane:gate]` `[tdd:required]` `@sre-engineer` **Observabilidade**: OpenTelemetry (traces de LLM/tool/DB), `/metrics` na porta 8000 com **exatamente** os nomes e rótulos da `docs/deploy.md` §6, logs JSON, scrubbing de PII (e-mail/telefone), Sentry só com `SENTRY_DSN`. | Teste que lê `/metrics` e confere os 13 nomes do contrato; nenhum rótulo `session_id`; teste de log com e-mail/telefone mascarados; dashboard `agente.json` sem painel vazio no compose (verificação manual registrada) | 2.1 | cc:TODO |
| 2.5 | `[lane:gate]` `[tdd:required]` `@security-engineer` **RBAC**: `ProvedorIdentidade` com JWT local (claims `tenant_id`, `roles`), 4 papéis e permissões da §8, `Depends(exige(...))`, dupla checagem de tenant, endpoints admin (`GET/PATCH /v1/admin/solicitacoes`, `PUT /v1/admin/config`), `emporio token` na CLI. | Matriz papel × endpoint testada (permitido/negado); token de outro tenant → 403; JWT expirado → 401; segredo do JWT vem só de `Segredos` | 2.1 | cc:TODO |
| 2.6 | `[lane:gate]` `[tdd:required]` `@fastapi-developer` **Tools via HTTP** `/v1/tools/*` (permissão `tools:usar`), schemas iguais aos da 1.6. Servidor MCP: Optional. | Cada tool acessível por HTTP com o mesmo resultado do caso de uso; sem `tools:usar` → 403 | 2.5 | cc:TODO |
| 2.7 | `[lane:gate]` `[tdd:required]` `@llm-architect` **`AgenteN8n`**: adapter que chama o webhook do n8n (timeout, retry, circuit breaker), o n8n chama de volta `/v1/tools/*`; `MOTOR_ATENDIMENTO=n8n`; workflow de exemplo em `deploy/n8n/`. | **A suíte de contrato da 1.11 passa para `n8n`** usando um n8n simulado (`httpx.MockTransport`) que chama as tools reais; nenhuma regra de negócio dentro do workflow (revisão) | 2.6, 1.11 | cc:TODO |
| 2.8 | `[lane:gate]` `[tdd:skip:infra-smoke]` `@devops-engineer` **Compose e CI verdes**: `docker compose up -d --build` sobe Traefik + 3 réplicas + Postgres + Redis; `curl` em `/v1/chat` via Traefik responde; workflow `ci-cd.yml` verde até o build (deploy pode ficar sem cluster). | Log do smoke anexado; CI verde nos jobs lint, tipos e testes; README atualizado (caminho 2) | 2.2, 2.3, 2.4 | cc:TODO |
| 2.9 | `[lane:gate]` `[tdd:required]` `@fintech-engineer` Optional: **`FakeGatewayPagamento`** + suíte de contrato (`docs/pagamento.md` §8). | Contrato verde; estados conforme §3 do documento | 2.1 | cc:TODO |

## Fase 3 — Entrega (Release)

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 3.0 | `[lane:release]` `[tdd:skip:docs-diagrams]` `@documentation-engineer` **Diagramas com [archify](https://github.com/tt-a1i/archify)** (skill de agente, Node.js ≥ 18, Apache-2.0; ferramenta de documentação, **não** é dependência do `pyproject.toml`). Antes de instalar: ler o `llms.txt` e o `SKILL.md` do repositório e conferir o que o instalador executa; instalar com escopo de projeto, versão fixada. Insumo: `get_architecture_overview_tool` do code-review-graph + código. Gerar em `docs/diagramas/`: (1) arquitetura de runtime (monólito modular + portas/adapters, CLI/API → `MotorDeAtendimento`); (2) sequência de uma mensagem (CLI → motor → roteador → LLM → tools → resposta, com fallback); (3) ERD dos dados (CSV/schema `emporio`); (4) deploy (Traefik → réplicas → Postgres/Redis → observabilidade); exportar PNG de cada um. | 4 HTML autocontidos + 4 PNG em `docs/diagramas/`; cada componente desenhado existe no código ou no deploy (conferido pelo grafo, sem caixa inventada); diagrama 1 respeita C1–C5; os dois READMEs mostram os PNG e linkam os HTML; a skill não vai para o repositório (só os diagramas) | 1.16 (e 2.x concluídas) | cc:TODO |
| 3.1 | `[lane:release]` `[tdd:skip:release-prep]` `@claude` **Revisão final**: grafo atualizado e `detect_changes_tool`/`get_review_context_tool` como ponto de partida, `/code-review high` na `main`, `security-review`, READMEs sincronizados com o que foi entregue (Fase 2 parcial vai para limitações), `.env`/`.mcp.json`/PDF do enunciado fora do histórico (`git log --all -- .env` vazio). | Achados críticos corrigidos ou documentados; varredura de segredos no histórico sem achados | 1.16 | cc:TODO |
| 3.2 | `[lane:release]` `[tdd:skip:release-automation]` `@claude` **Publicar**: repositório e GitHub Project públicos antes do prazo de 2026-11-02 23:59; depois disso, nada muda no repositório. | `gh repo view --json visibility` = PUBLIC; Project público; URL registrada | 3.1 | cc:TODO |

## Reject (fora do plano, com motivo)

- RAG vetorial / Graph RAG / REFRAG: descartados na arquitetura §1.
- OCR/áudio, frete real, gateway de pagamento real, Gateway API do K8s, circuito no Redis, canal WhatsApp: vão para "próximos passos" (arquitetura §13).
- Interface web: não pedida; CLI + API bastam.

## 事前確認 (aprovar uma vez junto com o plano)

- 事項: secret-read — a aplicação lê `.env` ao rodar `uv run emporio chat|diagnostico` (Claude não exibe nem copia o conteúdo)
  理由: gravar os exemplos com LLM real e verificar o clone limpo
  scope: Phase 1 / Task 1.14, 1.16
- 事項: secret-read — copiar `.env` para a pasta temporária do clone limpo (sem exibir)
  理由: o clone limpo precisa de uma chave para o chat
  scope: Phase 1 / Task 1.16
- 事項: external-send — chamadas às APIs de Groq, Gemini e Anthropic
  理由: exemplos reais, testes `llm_real` opcionais e verificação do clone
  scope: Phase 1 / Task 1.13, 1.14, 1.16
- 事項: external-send — `git push origin main` ao fim de cada tarefa
  理由: histórico incremental real (combinado no CLAUDE.md)
  scope: Phase 1–3 / todas
- 事項: external-send — `docker compose pull/build` (registries públicos) e CI no GitHub Actions
  理由: smoke do compose e CI verde
  scope: Phase 2 / Task 2.8
- 事項: external-send — `gh repo edit --visibility public` e tornar o GitHub Project público
  理由: requisito de entrega
  scope: Phase 3 / Task 3.2
- 事項: destructive — `docker compose down -v` (volumes locais de Postgres/Redis) e remoção da pasta temporária do clone limpo
  理由: reiniciar o smoke do zero; limpar a verificação
  scope: Phase 1 / Task 1.16, Phase 2 / Task 2.8
- 事項: external-send — baixar e instalar a skill archify (`npx skills ...` / clone de `github.com/tt-a1i/archify`) — **PENDENTE de aprovação**
  理由: gerar os diagramas da task 3.0; código de terceiros executado localmente (cadeia de suprimentos), por isso o instalador é lido antes
  scope: Phase 3 / Task 3.0

## Registro de fechamento de Stories

- **S1.A Fundação** (2026-11-01, `c90ee50`): grafo 118 nós / 462 arestas, 7 comunidades, 1 fluxo; review context risco baixo, 0 nós impactados fora do escopo; 26 lacunas de teste apontadas são Protocols e formas de dados sem lógica (cobertas pelos testes de contrato/adapters nas tasks 1.5–1.11); import-linter 12/12 KEPT.
