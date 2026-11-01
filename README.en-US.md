[Português (pt-BR)](README.md) · **English (en-US)**

# Empório da Música: Sônia, the customer service agent

Text-based customer service agent for **Empório da Música**, a fictional musical instrument store in Campo Grande/MS,
Brazil. The assistant is called **Sônia**. She answers questions about price, stock, orders, opening hours, address,
installments, returns, warranty and shipping. Data comes from tools (CSV files), and rules come from the store's policy
manual. The store does not sell accessories (strings, picks, cables, cases, pedals, amplifiers): Sônia redirects those
requests.

Artefact technical challenge, AI Engineer 2026.

The default language for answers is Brazilian Portuguese. English works with `--idioma en-US` and with language
detection (section 4.10).

**Delivery status:** the core (Phase 1) runs in the terminal with a real LLM. The Phase 2 infrastructure (API, Postgres,
Redis, observability, RBAC, deploy) is not implemented. See section 8.

---

## 1. Run it in 5 minutes

You need [git](https://git-scm.com/) and [uv](https://docs.astral.sh/uv/). uv installs Python 3.12 for you.

| Path | Prerequisites | Commands | What it shows | Status |
|---|---|---|---|---|
| **1. Quick** (default) | git, uv and one LLM key | `uv sync`, `.env`, `uv run emporio chat` | the full agent, on the CSV files, without Docker | works (core) |
| **2. Full** | + Docker | `docker compose up -d` | replicas, Traefik, schema per tenant, rate limit, dashboards | future: Phase 2 not implemented (see below) |
| **3. No run** | none | read `exemplos/`; `uv run pytest` | recorded conversations; rules tested with a simulated LLM, no key | works |

### Path 1: step by step

**1. Install uv.**

Windows (PowerShell):

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

macOS and Linux:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**2. Clone and install the dependencies** (Windows, macOS and Linux):

```bash
git clone https://github.com/carlosbandelli/emporio-da-musica-agente.git
cd emporio-da-musica-agente
uv sync
```

**3. Create the environment file with one key.**

Windows (PowerShell):

```powershell
Copy-Item .env.example .env
```

macOS and Linux:

```bash
cp .env.example .env
```

Open `.env` and fill in **one** key. The recommended one is `GOOGLE_API_KEY` (Gemini, free tier), available at
<https://aistudio.google.com/api-keys>. The others (`GROQ_API_KEY`, `ANTHROPIC_API_KEY`) are optional. Empty keys are
ignored.

**4. Check the configuration and chat.**

```bash
uv run emporio diagnostico
uv run emporio chat
```

- `diagnostico` shows which LLM providers have a key (names only), the engine, the tenant, the loaded data, the manual
  and the reference date. It never shows key values.
- `chat` opens the conversation. Type `sair` (or Ctrl+C) to quit.

**Optional:**

- Simulate "today". The CSV orders run until March 2026, so without this the agent uses the real date. To simulate it,
  uncomment this line in `.env`: `DATA_REFERENCIA=2026-03-10T15:00:00-04:00`.
- Chat in English: `uv run emporio chat --idioma en-US`.
- Save the conversation as Markdown: `uv run emporio chat --gravar minha-conversa.md`.

### No key at all

- `uv run pytest` runs every test without a key. Tests that call a real LLM are skipped.
- `uv run emporio chat` exits with code 2 and a message saying what is missing, with no stack trace.

### Notes

- Run the commands from the repository root. The `.env` file and the `config/` folder are read from the current
  folder.
- Path 2 (Docker) does not start today. The `Dockerfile` and `docker-compose.yml` exist, but the container starts
  `emporio.adaptadores.api.app:app`, which is Phase 2 and does not exist yet. The `deploy/` artifacts have not been run.

---

## 2. Commands

| Command | What it does |
|---|---|
| `uv run emporio --help` | lists the commands |
| `uv run emporio versao` | shows the installed version |
| `uv run emporio chat [--tenant ID] [--idioma pt-BR or en-US] [--gravar ARQUIVO.md]` | terminal conversation, in the same process |
| `uv run emporio diagnostico [--tenant ID]` | configuration status; never shows keys |
| `uv run pytest` | tests (no key needed; `llm_real` tests are skipped) |
| `uv run pytest --cov=src/emporio -q` | tests with coverage |
| `uv run lint-imports` | dependency rules C1 to C5 (12 contracts) |
| `uv run ruff check . && uv run ruff format --check .` | lint and formatting |
| `uv run mypy src` | type checking (strict in `src/`) |
| `EMPORIO_LLM_REAL=1 uv run pytest tests/e2e/test_llm_real.py -v` | tests with a real LLM (needs a key and uses quota) |

In PowerShell, the real-LLM test command is: `$env:EMPORIO_LLM_REAL=1; uv run pytest tests/e2e/test_llm_real.py -v`.

The CLI has no `emporio token` and no `--api URL`: both belong to Phase 2.

---

## 3. Repository layout

```
src/emporio/
  modulos/        pure business rules, no I/O and no SDK: catalogo, comercial, pedidos, politicas, atendimento, comum
  portas/         contracts (Protocol) and shared types (TenantContext, MotorDeAtendimento, LLMProvider...)
  adaptadores/    cli, motor/nativo, llm (groq, gemini, claude, falso), csv, memoria, relogio
  configuracao/   Settings (reads .env) and tenant loading
  composicao.py   the only place that connects ports to adapters
config/           tenants.yaml and the policy manual in Markdown (config/tenants/emporio/politicas.md)
data/             store CSV files and the original manual PDF
docs/             architecture, assumptions, payment (design) and deploy
exemplos/         3 conversations recorded with a real LLM
tests/            unit, contract (I1 to I8) and end-to-end (e2e) tests
deploy/           Phase 2 artifacts (not run)
```

---

## 4. Technical decisions and rationale

### 4.1 Language, environment and dependencies

- **Python 3.12** (`.python-version`) and **uv** with `uv.lock`. The brief only requires Python. uv installs the
  interpreter and resolves dependencies the same way on Windows, macOS and Linux.
- The project declares 11 runtime dependencies: the SDKs for Groq, Gemini and Anthropic, `tenacity` (retry), `pydantic`
  and `pydantic-settings`, `typer` (CLI), `babel` and `holidays` (dates and holidays), `pyyaml` and `tzdata`.

### 4.2 Agent framework: own loop, no LangChain or LangGraph

I wrote the agentic loop myself (route, call the LLM, run tools, repeat) instead of using a framework.

- **Loop control.** The limit of 6 LLM calls per message, the 30 s deadline, the monetary value guard and the provider
  switch must happen at exact points in the flow. In a framework, each one would become a hook to adapt.
- **Specific resilience.** Retry with backoff and jitter, and a circuit breaker and bulkhead per provider
  (architecture §5.2), are custom logic. A generic library would need the same adaptation.
- **Testable tools.** The tools are pure Python use cases (`modulos/*/aplicacao.py`). The engine only calls them, and
  tests run them without an LLM.
- **Cost.** The engine loop (`adaptadores/motor/nativo/agente.py`) is a little over 300 lines, and the project maintains
  it with no external orchestration dependency. In return, there are no ready-made graph or memory features, as in a
  framework. If the flow grows into several cooperating agents, this decision should be revisited.

### 4.3 LLM: three providers, routed by complexity

| Level | Examples | Default provider and model | Reason |
|---|---|---|---|
| simple | greeting, hours, address, out of scope | Groq `openai/gpt-oss-120b` (reasoning `low`) | low latency and tool calling |
| medium | product search, price, order status | Gemini `gemini-2.5-flash` | free tier; answered in 0.8 s in the 2026-11-01 test |
| complex | installments, exchange, returns, warranty, shipping, complaint | Claude `claude-opus-5-5` (effort `low`) | rules with exceptions, where a mistake is costly |

- **Classifier.** One short call to Groq, no tools, at most 5 s. It receives the message and the last three customer
  turns, so it can understand short replies such as "and in 10x?". **When in doubt, go up a level.** Without Groq, it
  uses Gemini and then Claude. If classification fails, the level is `complex`. If all levels share the same chain (one
  key only), the classifier is not called at all (assumptions #48 and #50).
- **Fallback.** Chains, filtered by the keys present (assumption #49):

  | Level | Order |
  |---|---|
  | complex | Claude → Gemini → Groq |
  | medium | Gemini → Claude → Groq |
  | simple | Groq → Gemini → Claude |

- **Resilience per provider** (architecture §5.2):
  - Up to 3 attempts, only for transient errors (timeout, connection, 429, 5xx), with exponential backoff and full
    jitter. The SDKs' internal retries are turned off.
  - Circuit breaker: opens after 5 consecutive transient failures, or 50% failures in 30 s (minimum of 10 calls).
    Authentication errors (401/403) and exhausted credit or quota open it immediately (assumption #53). After that, a
    single probe checks whether the provider is back.
  - Bulkhead: at most 5 simultaneous calls to Claude and 10 to Gemini and Groq.
- **Known models and errors.**
  - `gemini-flash-latest` returned 504 in a real call. That is why the default is `gemini-2.5-flash` (assumption #43).
  - The Groq free tier allows 8,000 tokens per minute. Each turn with tools uses about 6,000 input tokens, so with the
    Groq key alone a turn can exceed the limit and come back degraded (assumption #67).
  - The Anthropic account used in development had no credit on 2026-11-01. So the examples were recorded with Gemini
    and Groq, and the complex level fell back to Gemini (assumption #66). The Claude adapter has tests with fake HTTP
    responses, but no real call has been validated. The key used in development expires around 2026-11-07.

### 4.4 Retrieval: CAG, not RAG

Each kind of knowledge uses the technique that gives an exact answer:

| Knowledge | Technique | How |
|---|---|---|
| Data: stock, price, promotions, orders | tools (function calling) | exact structured queries on the CSV files; the LLM does not read prices from text |
| Policies: 8-page manual (v2.1) | CAG (cache-augmented generation) | the whole manual goes in the system prompt, in the cacheable block |
| Rules with calculations: PIX, installments, shipping, deadlines, hours | Python code (`modulos/*/dominio.py`) | "the LLM decides and writes; the code calculates" |

Why not vector RAG:

- A vector search does not filter by price or stock. Tools do that.
- Chunks split related rules. The PIX rule appears in §3 and §6.2 of the manual; a chunk can bring one and miss the other.
- The manual fits in the context. There is no gain from retrieving only part of it.
- Graph RAG and REFRAG were also evaluated and rejected (architecture §1).

The manual is converted from PDF to `config/tenants/emporio/politicas.md` once, reviewed by hand against the original,
and versioned (assumption #20). The PDF in `data/` is the source and is not read at runtime.

Cost: each call uses about 6,000 input tokens, including the manual. The cacheable block is byte-for-byte identical
across sessions, languages and dates (assumption #42), so the provider's prompt caching works. **I did not measure the
cache hit rate:** the metrics for it (`emporio_prompt_cache_total`) belong to Phase 2.

### 4.5 Prompt strategy

- **Persona Sônia** (architecture §9.2, based on manual §7.1 and §7.2): informal but professional, like a friend who knows
  music. Answer first, 2 to 5 sentences, at most one emoji and none in complaints. She introduces herself as a virtual
  assistant and does not pretend to be human.
- **Prompt in two blocks** (assumption #42):
  - cacheable block: persona, rules in pt-BR and en-US, and the full manual. It depends only on the tenant;
  - dynamic block: the session language and the reference date at day granularity, never the time.
  - "Is the store open right now?" and "when does it reopen?" are always answered by the `status_loja` tool, not by the
    model.
- **The LLM writes, the code calculates.** Price, installment, shipping and deadlines come from the tools. The LLM writes
  the answer around them.
- **Never state without a tool.** Price, stock and order status appear in the text only if the matching tool was called
  (invariant I6 of the port, architecture §3.2).
- **Monetary value guard** (assumption #58). Every `R$` in the final answer must come from a tool result in the session,
  from something the customer said, or from the manual. If not, there is one corrective round. If the model insists, the
  answer is replaced by a fixed message without the value.
- **Tools by role.** The role filter exists in the code, but in Phase 1 all six tools are for customers. Phase 2
  administrative tools will be added without changing the engine.
- **Fixed messages** (error, usage limit, store closed, deadline) come from a translation catalog, not from the LLM. That
  way they work even when every provider is down.

### 4.6 Architecture: modular monolith, hexagonal only at the edges

- **Modular monolith.** One package per business context (`catalogo`, `comercial`, `pedidos`, `politicas`,
  `atendimento`) and the shared kernel `comum` (`Dinheiro`). Each package has `dominio.py` (rules) and `aplicacao.py`
  (use cases, the module's public API).
- **Hexagonal only where there is a real swap.** Ports for the service engine, the LLM providers, the repositories, the
  state (session, rate limit, circuit) and the clock. Swapping one of them changes only its adapter.
- **Composition root.** `composicao.py` is the only place that creates adapters and reads `Settings`. There are no
  singletons, no `global` and no module-level cache.
- **Dependency rules C1 to C5**, checked by import-linter (`uv run lint-imports`, 12 contracts). One exception, recorded:
  the CLI may import the composition root (assumption #65).
- **`MotorDeAtendimento` port contract** (architecture §3.2): `responder(ctx, sessao_id, mensagem)`. The suite in
  `tests/contrato` tests invariants I1 to I8 for each registered engine. A new engine, such as the Phase 2 `AgenteN8n`,
  is accepted only if it passes that suite.

### 4.7 Interface: CLI

- The brief asks for a text-based customer service agent, not a graphical interface. The CLI runs without a server,
  without Docker and without a browser. It calls the engine through the same port that the API will use.
- The `--gravar` option saves the conversation as Markdown, turn by turn. The examples were generated this way.
- Trade-off: there is no visual chat history and no real channel (WhatsApp). A web frontend was not requested and is not
  in the plan.

### 4.8 History and session

- **Phase 1:** history is in memory, per process. It is lost when `chat` exits (assumption #21). A session expires after
  30 minutes without messages (assumption #60).
- **Append-only.** History is never edited. This keeps the prefix of the cached prompt and the reasoning blocks that
  Claude (`thinking`) and Gemini (`thought_signature`) require back. Editing the history breaks the cache and can cause
  a 400 error (assumption #47).
- **Trimming.** Above 40 messages, the oldest whole turns are removed. A `tool_use` never stays without its matching
  `tool_result`: if a turn fails midway, the session keeps only the customer's message and the fixed answer.
- **Phase 2:** Redis, with a TTL, to share sessions between replicas (architecture §5.3).

### 4.9 Data cleaning

The CSV files have real problems, and each one has a documented treatment:

| Problem | Treatment | Assumption |
|---|---|---|
| `total_brl` sometimes in reais (`799.9`), sometimes looks like cents (`11499`) | reais, with optional decimals; converted to integer cents with `ROUND_HALF_UP` | #22 |
| `specs` with invalid JSON (no current cases) | product kept with empty `specs` and a warning with line and id | #23 |
| row with an invalid id, price, category, negative stock or unknown status | row dropped with a warning (file and line number, no personal data) | #24 |
| `payment_method`: `credit_Nx`, `debit`, `pix`, `boleto` | enum; `credit_Nx` becomes credit in N installments | #25 |
| `tracking_code` and `estimated_delivery` empty for orders not shipped | `None`; the code does not assume that only `shipped` orders have tracking | #25 |
| several promotion rows per product; some inactive | only `is_active=1` applies; promotions do not stack, the highest one wins | #26, #28 |
| categories 6 to 8 with no products | kept; the answer says there are no items at the moment, without claiming the store does not sell them | #9, #26 |
| `discontinued` or `coming_soon` products | outside the default search; when asked, equivalents are offered | #26 |
| duplicate product id | the first occurrence is used | #24 |
| `delivered` order with no real delivery date | uses `estimated_delivery` as a substitute and says so in the answer | #6 |

`customers.csv` contains personal data. So `consultar_pedido` requires the order number **and** an email or phone number,
and a failed check always returns the same generic answer, without revealing anything about the order (assumptions #15
and #40).

### 4.10 Language and persona

- The language is detected, not asked: a heuristic based on Portuguese and English words. When the message is
  ambiguous, the channel language is used (`--idioma`, or the tenant default). An explicit request, such as "can we speak
  English?", switches it at any time (assumption #57).
- Values are always in reais (BRL), formatted for the session language.
- The persona is the same in both languages. The manual is not translated: the LLM reads the rules in Portuguese and
  answers in the session language.

---

## 5. Assumptions (ambiguities in the brief)

The brief asks that, when something is ambiguous, a reasonable interpretation is assumed and documented. There are 67
assumptions, each with its decision and reason, in [docs/premissas.md](docs/premissas.md) (in Portuguese). The general
rule is: the more specific rule wins, and when in doubt the agent does not invent, it refers the case to the team.

Some of the most visible ones:

| # | Ambiguity | Decision |
|---|---|---|
| 1 | Phone number differs on the cover (3341-4444) and in §7 (WhatsApp 3321-4500) | gives both: phone and WhatsApp |
| 2 | Email with and without an accent | `contato@emporiodamusica.com.br`, without the accent |
| 4 | Minimum installment "R$ 100" in the table × tiers in §3.1 | §3.1 applies: up to 3x with a minimum of R$ 50 per installment; 4 to 6x, R$ 80; 7 to 12x, R$ 100 |
| 5 | 30-day defect window × 90-day legal warranty | up to 30 days from purchase, exchange at the store; up to 90 days from receipt, legal warranty with the store acting as intermediary |
| 7 | Regret return applies only to online purchases | all orders are treated as online (they have delivery and tracking) |
| 11 | Shipping outside Campo Grande depends on ZIP code, weight and dimensions | gives the carrier options and deadlines, no price, and records a quote for the team |
| 12 | "Metropolitan area" with no list of cities | only Campo Grande; from R$ 500 shipping is free |
| 15 | Order lookup and privacy (LGPD) | order number plus email or phone; without a match, no order data is shown |
| 19 | The evaluator runs without any key | `diagnostico` explains, `chat` exits with code 2, `pytest` runs |
| 22 | `total_brl` in reais or cents | reais (`11499` = R$ 11,499.00) |
| 28 | Two active promotions on the same product | they do not stack: the higher percentage applies |
| 29 | Discount rounding | calculated once, in cents, `ROUND_HALF_UP` (R$ 99.90 with PIX = R$ 94.91) |
| 31 | Combined payment | only above R$ 2,000.00 (R$ 2,000.00 does not qualify); no PIX discount inside a combined plan |
| 36 | Counting return deadlines | the reference day is day 0 (receipt; for defects, the purchase); day 7 still counts, day 8 does not |
| 58 | Unverified monetary value in the answer | one corrective round; if the model insists, a message without the value |

---

## 6. Quality and tests

- **Tests:** `uv run pytest -q` gives about 729 passed and 3 skipped (the `llm_real` tests, without a key). Coverage of
  `src/emporio`: about 98%. Numbers from the last gate run, on 2026-11-01.
- **Static gate:** ruff (lint and formatting), mypy in strict mode on `src/`, and import-linter with 12 contracts, all
  kept.
- **Three test layers:**
  - unit tests per module and adapter, with `LLMFalso` (scripted answers) and the real CSV files;
  - contract tests for the `MotorDeAtendimento` port (I1 to I8), parametrized by the registered engines (`tests/contrato`);
  - end-to-end tests (`tests/e2e/test_cenarios.py`): brief and rule scenarios, with real CSV data and a scripted LLM. One
    of them is "violões até R$ 1,000" (guitars up to R$ 1,000), another is "me arrependi, posso devolver?" (I regret the
    purchase, can I return it?), with the order verified.
- **Real-LLM tests** are in `tests/e2e/test_llm_real.py`, marked `llm_real`. They only run with `EMPORIO_LLM_REAL=1` and
  a key. Minimal real calls showed two problems that the fakes did not catch: the `gemini-flash-latest` alias (504) and
  the Groq tokens-per-minute limit.

---

## 7. Example conversations

Three conversations recorded with a real LLM, using `emporio chat --gravar` itself, with the reference date in the
header. Each answer has a line `level · provider · tools called`. Details in [exemplos/README.md](exemplos/README.md)
(in Portuguese).

| File | Scenario | What it shows |
|---|---|---|
| [01-violoes-ate-1000.md](exemplos/01-violoes-ate-1000.md) | "violões até R$1000" and installments | catalog search and payment simulation with the minimum-installment rule |
| [02-arrependimento-pedido.md](exemplos/02-arrependimento-pedido.md) | "me arrependi da compra, posso devolver?" | real order verified by email, and the 7-day window from the manual applied to the date |
| [03-english-takamine-strings.md](exemplos/03-english-takamine-strings.md) | in English: Takamine GD20, strings and address | price, PIX and 12x in en-US format; out-of-scope accessory; address from the manual |

The values quoted match the CSV files, and the logic behind them is covered by tests in `tests/e2e` (details in
[exemplos/README.md](exemplos/README.md)). A limitation seen in the recording: in example 01, the answer to the second
question gave the installments but left out the PIX price the customer had asked for.

---

## 8. Limitations and next steps

### 8.1 Not implemented (Phase 2 and architecture §13)

Phase 2 is part of the design, but it was not delivered. The files in `deploy/`, the `Dockerfile`, `docker-compose.yml`
and the CI workflow exist, but they have not been run:

- FastAPI API (`POST /v1/chat`, `/health`, `/ready`) and the CLI remote mode (`--api URL`);
- Postgres with one schema per tenant, migrations and seed; Redis for sessions, rate limit and cache;
- layered rate limit (session, user, tenant, provider) and rate limit per IP at the edge;
- observability: the metrics in `docs/deploy.md` §6, traces and JSON logs;
- RBAC with JWT, roles and `emporio token`;
- tools over HTTP (`/v1/tools/*`) and the `AgenteN8n` engine;
- a fake payment gateway (design in `docs/pagamento.md`);
- Traefik, Kubernetes and the deploy stage of the pipeline.

### 8.2 Known limitations

- **Local mode.** Session, rate limit and circuit state are in memory, per process. History is lost when `chat` exits.
- **The guard only checks monetary values** (assumption #58). Stock and order status depend on the prompt instruction and
  on calling the tool, not on a deterministic check.
- **Language by keywords** (assumption #57). Short and ambiguous phrases fall back to the channel language.
- **Claude not validated with a real call** (assumption #66, section 4.3). Groq and Gemini were each validated with one
  minimal call.
- **Free-tier limits** (assumption #67). With only the Groq key, turns with tools can come back degraded (confirmed on the clean clone: `docs/verificacao-clone-limpo.md`).
- **Shipping outside Campo Grande** has no price: there is no ZIP code, weight or dimensions in the data. It becomes a
  recorded quote (assumption #11).
- **Missing data.** There is no real delivery date (assumption #6), no list of "final sale" items (assumption #8), and no
  extended hours on special dates (assumption #13). Carnival and Corpus Christi are not holidays for the agent
  (assumption #33).
- **Divergences in the data and the brief** were decided, not confirmed with the store: phone and email (assumptions #1
  and #2). The total of two orders is lower than the sum of their items (assumption #22).
- **Personal data in the repository.** `data/customers.csv` is versioned. In production, the seed should come from a
  controlled source (`docs/deploy.md` §7).
- **CLI details.** The farewell message is fixed in Portuguese, even with `--idioma en-US`. `--help` may show garbled
  accents on a cp1252 console when the output is redirected.
- **Examples.** There are 3 conversations. The optional complaint example was not recorded. Example 01 has the PIX
  omission described in section 7.

### 8.3 What I would do with more time

In priority order:

1. Validate the complex level with a Claude key that has credit, and record the examples again with it.
2. Implement the Phase 2 API and data layer: FastAPI, Postgres with one schema per tenant, and Redis. Then run Docker
   Compose end to end.
3. Deterministic checks for stock and order status, in addition to monetary values.
4. Observability as in `docs/deploy.md` §6: metrics, traces and cost per tenant.
5. Real shipping (Correios and Jadlog), with weight and dimensions on products, and a real delivery date.
6. A real payment gateway adapter (`docs/pagamento.md`).
7. A WhatsApp channel: a webhook that calls `POST /v1/chat`.
8. Audio and photo input (OCR): invoice, defect, proof of payment. See architecture §13.1.
9. The `AgenteN8n` engine, passing the same contract suite.
10. Real-LLM tests in CI, with a cost limit per run.

---

## 9. How I used AI coding assistants

I used Claude Code as the orchestrator for the whole project. The workflow was:

1. **Plan before execution.** I wrote the plan together with the assistant: `Plans.md`, with phases, tasks, acceptance
   criteria and the specialist agent for each task. Architecture decisions and assumptions are in `docs/`.
2. **Execution by subagents.** Each task was delivered by a specialist subagent. Usually up to three ran in parallel,
   on disjoint sets of files:

   | Tasks | Subagent |
   |---|---|
   | 1.0, 1.2–1.4, 1.6 (scaffold, ports, domains, use cases) | `python-pro` |
   | 1.1 (spec delta in the architecture doc) | `architect-reviewer` |
   | 1.5 (CSV and data cleaning) | `data-engineer` |
   | 1.7–1.10 (prompt/CAG, LLM adapters, router, engine and composition) | `llm-architect` |
   | exponential backoff with jitter in the circuit breaker | `python-pro` |
   | 1.11, 1.13 (contract suite, end-to-end scenarios) | `test-automator` |
   | 1.12 (CLI) | `cli-developer` |
   | 1.14 (recording the examples with a real LLM) | the orchestrator itself |
   | 1.15 (README) | `technical-writer`, reviewed by `content-quality-editor` |
3. **Review and gate.** For each delivery, the orchestrator reviewed the code, ran ruff, mypy, import-linter and pytest,
   recorded new assumptions, and made one commit per task, linked to the issue on the GitHub Project.
4. **Code graph.** When closing each Story, I used code-review-graph to measure impact and check rules C1 to C5. Records
   in `Plans.md`: S1.A with 118 nodes and 462 edges; S1.B with 610 nodes and 4,053 edges; S1.C with 692 nodes and 6,550
   edges.
5. **Human decisions along the way.** Four points changed the direction, and I made each of them, not the assistant:
   - open the provider's circuit immediately when credit runs out (assumption #53);
   - exponential backoff with jitter in the circuit breaker, instead of a fixed wait (assumption #54);
   - spend the minimum number of tokens on real-LLM tests;
   - record the examples without Claude, because the account had no credit (assumption #66).
6. **Real tests.** The fakes passed, but real calls found that the `gemini-flash-latest` alias returned 504 (fixed in
   commit `ca35553`), and the Groq limit of 8,000 tokens per minute (assumption #67).

Limits of the process: the assistant wrote code and text, including this README, but product decisions and assumptions
went through human review. The test and coverage numbers in section 6 come from the last gate run.

---

## Data

The CSV files in `data/` and the manual are material from the challenge brief. The store, the persona and the customer
data are fictional.
