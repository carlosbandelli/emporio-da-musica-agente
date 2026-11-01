# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

This is a technical challenge (Artefact, AI Engineer 2026). Phase 1 (core) is implemented through task 1.15: agent engine, LLM
adapters, CLI, contract and end-to-end tests, 3 recorded examples, bilingual README. Remaining: 1.16 clean-clone check,
then Phase 2 extras.
The brief PDF is local only (gitignored). Source of truth for decisions:

- `docs/arquitetura.md`: architecture (agentic + CAG, LLM routing Groq/Gemini/Claude, modular monolith, hexagonal at
  the edges, schema-per-tenant Postgres, Redis, resilience values, Traefik, observability, RBAC, i18n, persona
  "Sônia", how the evaluator runs the project, future improvements).
- `docs/premissas.md`: assumptions for every ambiguity (67 so far). Add new ones there.
- `docs/pagamento.md` (payment port design) and `docs/deploy.md` (deploy artifacts and the metrics contract the code
  must expose). The deploy files assume module/port/env names listed there: follow them.

## Commands

Python 3.12 via uv (`.python-version`); the CLI is the `emporio` script (`emporio.adaptadores.cli:app`).

- Setup: `uv sync --all-groups`
- Run: `uv run emporio --help`
- Tests: `uv run pytest` (marker `llm_real` = tests that call a real LLM, skipped without a key)
- Full gate: `uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run lint-imports && uv run pytest`

## Source tree (`src/emporio/`)

- `modulos/`: modular monolith, pure Python, no I/O or SDKs. `comum/` (shared kernel), `catalogo/`, `comercial/`,
  `pedidos/`, `politicas/`, `atendimento/`; each has `dominio.py` (rules) and `aplicacao.py` (use cases/tools, the
  module's public API).
- `portas/`: Protocol contracts and shared types (e.g. `TenantContext`).
- `adaptadores/`: swappable implementations: `cli/` (entry), `motor/nativo/` (agent engine), `llm/`, `csv/`,
  `memoria/`. Phase 2 adds `api/`, `motor/n8n/`, `db/`, `redis/`.
- `composicao.py`: composition root, the only place that wires ports to adapters.
- `config/` (repo root): per-tenant config (`tenants.yaml`, policy manual in text), copied by the Dockerfile.

Dependency rules (inward only), enforced by import-linter in `pyproject.toml` (`uv run lint-imports`, also in CI).
Layer order: `composicao` > `adaptadores` > `modulos.*.aplicacao` > `portas` > `modulos.*.dominio` (+ `comum`).
- C1: no layer imports an outer one (`dominio`/`comum` import nothing from the project but `comum`).
- C2: `modulos` and `portas` import no external SDK/framework (groq, google, anthropic, httpx, sqlalchemy, redis, fastapi, typer).
- C3: a module never imports another module's `dominio` (only its `aplicacao`); `comum` is allowed.
- C4: entry adapters (`cli`, later `api`) import only `portas` and `composicao`.
- C5: adapters are independent of each other.

## How we work (agreed with the user)

- **Never start execution without explicit approval**: no implementation, plan execution, or background agents
  until the user says so. Propose first (what, which agent, which files), then wait.
- The plan is written together; **tasks are executed by specialist subagents** (e.g. `python-pro`, `postgres-pro`,
  `llm-architect`, `fastapi-developer`, `test-automator`). Claude orchestrates, reviews each delivery, runs tests and
  commits per task.
- **Core first, extras later**: the core (agent working end to end via `uv run emporio chat` from a clean clone with
  one LLM key) must be complete before scaling/infra extras.
- Git: commit and push per step without asking; real incremental history, never squash or force-push.
- Repo `carlosbandelli/emporio-da-musica-agente` stays **private until delivery**, then must be made public (the
  GitHub Project board too). Delivery deadline: **2026-11-02 23:59** (America/Campo_Grande). Nothing changes in the repo after it.
- CI may stay red until code exists; fix it at the end if needed (not a delivery blocker).
- Secrets live in `.env` / `.mcp.json` (gitignored). Never print, copy or commit them. The Anthropic key expires
  around 2026-11-07: record Claude example conversations before that; without the key, the complex tier falls back
  to Gemini.
- **code-review-graph**: update the graph (`build_or_update_graph_tool`) whenever a task closes a module/adapter and
  at the end of every Story and Epic (hierarchy in `Plans.md`). Every code analysis (subagent delivery review,
  `/code-review`, debugging, change impact, C1–C5 import rules) starts from the graph tools; read files only where the
  graph points. Cite node/edge counts in the commit that closes a Story/Epic.
- **archify** (https://github.com/tt-a1i/archify): agent skill (Node), not a Python dependency. Used at the end
  (Plans task 3.0) to generate architecture/sequence/ERD/deploy diagrams into `docs/diagramas/`. Read its installer
  before running it.
- Planning/execution plugins: `/harness:harness-plan` then `/harness:harness-work <range>` (run in ranges, review
  between them).

## The task

Build a **Python** text-message customer-service agent for **Empório da Música**, a fictional musical-instrument
store in Campo Grande/MS. User-facing text defaults to **Brazilian
Portuguese**. The README is bilingual: `README.md` (pt-BR, primary) and `README.en-US.md` (en-US), kept in sync,
each linking to the other at the top. Architecture decisions live in `docs/arquitetura.md`; assumptions about
ambiguities in `docs/premissas.md`. The agent must:

- Use a persona matching the store's tone. Manual §7.1: informal but professional, like a friend who knows music,
  not robotic.
- Decide when to query **data** (prices, stock, order status, promotions) and when to consult **policies**
  (returns, hours, payment, shipping, warranty).
- Handle out-of-scope requests. The store sells only instruments, **no accessories** (strings, picks, cables, cases,
  pedals, amps). Redirect these politely.

Python is the only hard constraint. Framework, LLM provider, interface (CLI/notebook/API/UI), history
persistence and data cleaning are free choices, but each one **must be justified in README.md**.

### Deliverables (these shape how to work)

- A public GitHub repo with **real incremental commit history**. Do not squash everything into one commit or force-push.
- A README.md with: setup/run instructions (env, model provider, commands), justification of technical decisions
  (framework, LLM, retrieval architecture, prompt strategy), known limitations and what you'd do with more time,
  and **how AI coding assistants were used** (workflow).
- 3–5 example conversations (`.md`/`.txt`/image). At least one must be non-trivial: live data lookup or applying a
  policy rule. Suggested scenarios: violões up to R$1000, store address, price of "Takamine GD20", "me arrependi da
  compra, posso devolver?".
- If the brief is ambiguous, pick a reasonable interpretation and **document the assumption in the README**.

## Data (`data/`)

The CSV filenames contain spaces and a common prefix: `desafio_tecnico_ai_eng - <table>.csv`. Quote paths.

- `products.csv` (65 rows): `product_id, price_brl, name, category_id, description, stock_quantity, status, specs, created_at`.
  `status` ∈ {active, discontinued, coming_soon}. `specs` is a JSON string (top, strings, electronics, color, …).
  IDs start at 81, not 1.
- `categories.csv`: 9 categories, but products exist only in 1–5 and 9. Categories 6–8 (sopros, cordas orquestrais)
  have **no products**.
- `promotions.csv`: `product_id, discount_percent, description, is_active`. Most are inactive. A product can have
  several rows (e.g. 93, 96, 99). Only `is_active=1` applies.
- `orders.csv`: `status` ∈ {pending, confirmed, shipped, delivered, cancelled}. `tracking_code`/`estimated_delivery`
  exist only once shipped. `notes` holds the cancellation reasons. `payment_method` is encoded (`pix`, `debit`,
  `boleto`, `credit_Nx`).
- `order_items.csv`: `order_id, quantity, product_id` (no unit price).
- `customers.csv`: name, phone, email, city (PII, so think about how an order lookup verifies identity).

`data/políticas_da_loja.pdf` is an 8-page policy manual (v2.1). It is short enough to fit fully in a prompt, so
weigh that against chunked RAG. Business rules the agent must apply correctly:

- Hours: Mon–Fri 09–18, Sat 09–13, Sun/holidays closed. Outside business hours, tell the customer when the store reopens.
- PIX gives a 5% discount, **but not on top of a promotional price**. Promotions don't stack. Show promo prices
  alongside the original price and the % off.
- Installments: up to 12x interest-free with tiered minimums (≤3x; 4–6x min R$80; 7–12x min R$100). Payment methods
  can be combined above R$2000.
- Returns: 7 days after receipt (online, regret). Defects: 30 days. Preference swap: 7 days. Non-eligible items listed in §4.4.
- Shipping: free in metro Campo Grande above R$500, otherwise R$35. Other cities use PAC/SEDEX/Jadlog.
- Never state price, stock or delivery times without querying the data. Out of stock: suggest alternatives.
  Discontinued: offer equivalents. Expired promo: say so and give the current price.

Known inconsistencies in the source material (document whichever choice you make):

- Phone: (67) 3341-4444 on the cover/§1.2 vs WhatsApp (67) 3321-4500 in §7.
- Email: `contato@emporiodamusica.com.br` (cover) vs `contato@empóriodamusica.com.br` (§1.2, with accent).
- Order dates run up to 2026-03, so "today" affects return-window and hours answers.
