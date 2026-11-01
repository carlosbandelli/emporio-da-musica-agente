# Arquitetura — decisões

Documento vivo. Cada decisão vira uma seção do README com sua justificativa.
Princípio geral: **pronto para escalar, mesmo sem escalar** — multi-tenant, réplicas, rate limit, resiliência e
observabilidade ficam prontos e ligados, rodando com 1 réplica e 1 loja. Escalar vira configuração, não reescrita.

## 1. Abordagem do agente (IA)

| Conhecimento | Técnica | Como |
|---|---|---|
| Políticas (manual, ~8 páginas, estático) | **CAG** | Manual inteiro no system prompt, com prompt caching do provedor. |
| Dados (catálogo, pedidos, promoções) | **Agentic / function calling** | O LLM decide qual tool chamar; tools fazem consultas estruturadas exatas. |
| Regras de negócio (PIX, parcelas, frete, prazos, horário) | **Código determinístico** | "O LLM decide e redige; o código calcula." |

Avaliadas e descartadas: RAG vetorial (não filtra preço/estoque; chunks separam regras relacionadas, ex. PIX §3 × §6.2),
Graph RAG (relações já são joins), REFRAG (otimização de pesquisa para contextos enormes, exige modelo treinado).

Tools previstas: `buscar_produtos`, `detalhar_produto`, `simular_pagamento`, `consultar_pedido`, `status_loja`,
`registrar_solicitacao` (reclamação, cotação de frete, outros).

**As tools são casos de uso puros** (camada de aplicação), sem nenhuma dependência de LLM. O agente apenas as chama.
Isso permite expô-las para qualquer outro orquestrador (ver §3.1).

## 2. Roteamento de LLM por complexidade

| Nível | Provedor | Exemplos |
|---|---|---|
| Simples | Groq | saudação, horário, endereço, fora de escopo |
| Média | Gemini | busca de produto, preço, status de pedido |
| Complexa | Claude (`claude-opus-5-5`) | pagamento/parcelas, troca, devolução, garantia, reclamação |

- Classificador curto (Groq) devolve o nível; **na dúvida, sobe de nível**.
- **Circuit breaker por provedor** + cadeia de fallback (ex.: Claude indisponível/chave expirada → Gemini).
- Chave Anthropic é temporária (expira ~2026-11-07): exemplos gravados antes; README documenta o fallback.

## 3. Arquitetura de software

- **Monólito modular**: um deploy, módulos por contexto (Catálogo, Pedidos, Comercial, Políticas, Atendimento).
- **DDD leve**: contextos delimitados + value objects (`Dinheiro`, `Parcelamento`) onde moram as regras. Sem
  agregados/eventos/CQRS.
- **SOLID**: injeção de dependência, uma responsabilidade por módulo.
- **Hexagonal apenas nas bordas**: portas/adapters só para o que é externo. O núcleo (domínio e casos de uso) é
  Python simples, sem abstrações desnecessárias. Trocar LLM, banco, observabilidade ou o próprio motor do agente muda
  só o adapter — os módulos do monólito não são tocados.

| Porta | Adapters |
|---|---|
| `MotorDeAtendimento` | `AgenteNativo` (loop Python: roteador + LLMs + tools); `AgenteN8n` (webhook do n8n, evolução) |
| `LLMProvider` | Groq, Gemini, Claude |
| Repositórios (`Catalogo`, `Pedidos`, `Clientes`, `Solicitacoes`) | Postgres (schema por tenant); CSV (modo local sem infra) |
| `SessaoStore` | Redis; memória (dev/testes) |
| `RateLimiter` | Redis (distribuído); memória |
| `EstadoCircuito` | memória por pod (padrão); Redis (evolução, ver §5.3) |
| `GatewayPagamento` | Fake/simulado (padrão); real como evolução — ver `docs/pagamento.md` |
| `Segredos` | variáveis de ambiente / `.env`; Secret do K8s em produção |
| `ProvedorIdentidade` | JWT local (hoje); Keycloak/Auth0/Cognito (evolução) |
| Observabilidade | OpenTelemetry (o próprio padrão faz o papel de porta) → Sentry / Grafana (ver §7) |

| Adapter de entrada | Uso |
|---|---|
| API FastAPI | chat, admin, health |
| Tools via HTTP (`/v1/tools/*`) ou servidor **MCP** | outros orquestradores (n8n, Claude Desktop...) usam as tools da loja |
| CLI | conversar no terminal, gerar tokens de teste |

### 3.1 Trocar o agente (ex.: por n8n)

```
API → rate limit → RBAC → tenant → MotorDeAtendimento.responder(tenant, sessao, mensagem)
                                      ├─ AgenteNativo  (padrão)
                                      └─ AgenteN8n     (chama webhook; o n8n chama de volta /v1/tools/* ou MCP)
```

- A troca é uma variável de configuração. API, segurança, multi-tenant e observabilidade continuam iguais.
- O n8n substitui só a **orquestração** ("quem decide chamar o quê"). As **regras de negócio** continuam nos módulos
  Comercial/Pedidos/Catálogo e são acessadas pelas tools.
- **Regra**: nenhuma regra de negócio é reescrita dentro de fluxos do n8n — isso duplicaria lógica e quebraria a
  arquitetura.

### 3.2 Contrato da porta `MotorDeAtendimento`

É a única porta que os adapters de entrada usam para conversar. O contrato fica em `portas/`; os motores, em
`adaptadores/motor/`.

```python
async def responder(ctx: TenantContext, sessao_id: str, mensagem: MensagemCliente) -> RespostaAtendimento
```

`RespostaAtendimento`: `texto`, `idioma`, `nivel | None` (`None` se nenhum LLM foi chamado ou o motor não usa o
roteador), `provedor | None`, `tools_chamadas: list[str]`, `degradada: bool` (resposta veio do catálogo de mensagens
fixas por falha ou limite).

Invariantes que **todo** motor cumpre:

| # | Invariante |
|---|---|
| I1 | Texto não vazio para qualquer mensagem válida. Mensagem vazia ou acima do limite do tenant → mensagem fixa, sem chamar LLM. |
| I2 | Histórico: a 2ª mensagem da mesma sessão enxerga a 1ª (via `SessaoStore`). |
| I3 | Isolamento: o mesmo `sessao_id` em tenants diferentes não compartilha histórico nem configuração. |
| I4 | Nunca lança exceção por falha de LLM ou backend: devolve a mensagem fixa traduzida com `degradada=True`. |
| I5 | Respeita o deadline do tenant (padrão 30 s, §5.2); estourou → mensagem fixa, `degradada=True`. |
| I6 | Preço, estoque e status de pedido só aparecem no texto se a tool correspondente foi chamada (espião na fachada de tools). |
| I7 | Responde no idioma da sessão (pt-BR/en-US, §10). |
| I8 | Sem estado global: duas instâncias com tenants diferentes em `asyncio.gather` não se contaminam. |

A suíte de contrato fica em `tests/contrato/test_motor_atendimento.py`, parametrizada pelos motores registrados e
com LLM simulado (sem rede, sem chave). Qualquer motor futuro (ex.: `AgenteN8n`) só entra no registro de
`MOTOR_ATENDIMENTO` se passar nela; `tests/contrato/README.md` explica como incluí-lo.

### 3.3 Composition root e estrutura de pastas

- **Monólito modular** = `modulos/`: um pacote por contexto (§3), Python simples, sem I/O nem SDK. Cada módulo tem
  `dominio.py` (regras e value objects) e `aplicacao.py` (casos de uso = tools; é a API pública do módulo).
  `modulos/comum` é o shared kernel (`Dinheiro` e tipos simples usados por todos).
- **Hexagonal** = `portas/` (contratos `Protocol` e tipos compartilhados, como `TenantContext`) + `adaptadores/`
  (implementações trocáveis, de entrada e de saída). Só o que é externo ganha porta.
- **Composition root**: `emporio.composicao.montar(settings) -> Aplicacao` é o único lugar que instancia adapters e
  liga porta ↔ adapter. Só ali se leem `Settings` (`.env`) e `config/`.
- **Proibido** em `src/`: singletons de módulo, `global`, cache ou estado em variável de módulo (constantes imutáveis
  são permitidas). O estado por pod previsto na §5 (cache L1 de config/manual, single-flight, circuito, bulkhead) é
  atributo de objetos criados em `montar()`.
- A CLI e o `POST /v1/chat` recebem só `MotorDeAtendimento` (e `Diagnostico`), nunca LLMs, repositórios ou tools.
  Entradas da Fase 2 que precisam de mais (`/v1/tools/*` e MCP, admin) recebem a porta correspondente (fachada de
  tools, `Solicitacoes`, `ProvedorIdentidade`), também injetada por `montar()`.
- **Seleção do motor** por `MOTOR_ATENDIMENTO` (`nativo`, padrão; `n8n` na Fase 2). Valor desconhecido → erro de
  configuração na inicialização, não na primeira mensagem.

```
src/emporio/
├── modulos/                  ← MONÓLITO MODULAR (Python simples, sem I/O nem SDK)
│   ├── comum/                shared kernel: Dinheiro e tipos simples usados por todos os módulos
│   ├── catalogo/             Produto, Categoria → buscar_produtos, detalhar_produto
│   ├── comercial/            PrecoVigente, Parcelamento, PlanoPagamento → simular_pagamento
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

Regras de dependência (só para dentro), verificadas pelo **import-linter** (`[tool.importlinter]` no
`pyproject.toml`; `uv run lint-imports` no gate de qualidade e no CI):

- **C1** camadas (de fora para dentro): `composicao` → `adaptadores` → `modulos.*.aplicacao` → `portas` →
  `modulos.*.dominio` (+ `modulos.comum`). Os casos de uso dependem das portas; as portas podem usar tipos do domínio;
  o domínio não importa nada do projeto além de `modulos.comum`. Nenhuma camada importa uma camada de fora.
- **C2** `emporio.modulos` não importa `emporio.adaptadores`, `emporio.composicao` nem SDKs externos (`groq`,
  `google.genai`, `anthropic`, `httpx`, `sqlalchemy`, `redis`, `fastapi`, `typer`).
- **C3** entre módulos: um módulo só importa o `aplicacao` de outro, nunca o `dominio`; `modulos.comum` é permitido
  para todos.
- **C4** adaptadores de entrada (`cli`, `api`) só importam `portas` e `composicao`; nunca `modulos`, `llm`, `csv`,
  `db`, `motor`.
- **C5** adaptadores independentes entre si: `motor.nativo` não importa `llm.claude`, só a porta `LLMProvider`.

Consequência: a Fase 2 inteira é "novo adapter + uma linha em `composicao.py`"; `modulos/` não muda.

## 4. Multi-tenancy — schema por tenant (modelo 02)

- Um banco Postgres; **um schema por loja** (`emporio`, ...). Tabelas idênticas em cada schema.
- `TenantContext` por requisição (subdomínio ou header `X-Tenant-ID` na API, flag na CLI).
- Repositórios recebem a conexão já com `search_path` do tenant — não existe consulta sem tenant.
- `config/tenants.yaml`: schema, manual de políticas, persona, contatos, limites por tenant.
- Manual (CAG) e cache de prompt **por tenant**; chaves Redis com prefixo do tenant.
- Rate limit (token bucket) **por tenant** → mitiga "noisy neighbor".
- Migrations (Alembic) aplicadas em loop por schema; seed importa os CSVs para o schema `emporio`.
- Trade-offs aceitos (da referência): migrations multiplicadas por schema e relatórios cross-tenant mais difíceis.
  Evolução possível para banco por tenant trocando só o adapter.

## 5. Escalabilidade e resiliência

- **Stateless**: nenhum estado de negócio em memória do processo; sessão e rate limit no Redis (no modo local,
  em memória por processo — premissa #21).
- **Horizontal**: N réplicas atrás do Traefik; K8s `Deployment` + **HPA**. Sem sticky sessions.
- **Vertical**: requests/limits de CPU/memória configuráveis; **VPA** de exemplo.
- **Single-flight**: carregamento de config/manual por tenant uma vez por pod, mesmo com requisições concorrentes.
- **Lock**: advisory lock do Postgres para que só uma réplica rode migrations/seed no startup.

### 5.1 Rate limit em camadas (token bucket) — na aplicação

Regras que dependem do negócio. O limite por **IP** fica na borda (Traefik, §6).
Cada mensagem precisa passar em **todas** as camadas; a primeira sem tokens devolve `429` + `Retry-After`.

| Camada | Chave Redis | Protege contra |
|---|---|---|
| Sessão | `rl:{tenant}:sessao:{id}` | uma conversa em loop |
| Usuário (autenticado) | `rl:{tenant}:usuario:{id}` | funcionário/integração abusando da API |
| Tenant | `rl:{tenant}:global` | uma loja consumindo a cota das outras (noisy neighbor) |
| Saída por provedor | `rl:llm:{provedor}` | estourar o limite do tier grátis de Groq/Gemini |

- Token bucket em **script Lua no Redis** (atômico, sem condição de corrida entre réplicas).
- Capacidade e taxa de reposição configuráveis por tenant em `config/tenants.yaml`.

### 5.2 Resiliência nas chamadas externas (LLMs, gateway de pagamento)

Ordem de aplicação por chamada:

```
deadline da requisição → bulkhead → timeout → retry (backoff exponencial + jitter) → circuit breaker → fallback
```

- **Timeout** por chamada e **deadline total** por mensagem do cliente: os retries nunca estouram o tempo de
  resposta aceitável do chat.
- **Retry com backoff exponencial + full jitter**: `espera = random(0, min(teto, base × 2^tentativa))`. O jitter evita
  que várias réplicas tentem de novo no mesmo instante (thundering herd). `tenacity.wait_random_exponential`.
  - Só para erros **transitórios**: timeout, conexão, `429`, `5xx`. Respeita o `Retry-After` quando vier.
  - **Nunca** para erros permanentes (`400`, `401`/`403`, chave expirada): vai direto para o fallback.
  - Poucas tentativas (2–3): em chat, falhar rápido para o próximo provedor é melhor que insistir.
  - Retries internos dos SDKs **desligados** (`max_retries=0`) para não multiplicar tentativas.
- **Circuit breaker** por provedor (fechado → aberto → meio-aberto). Erro permanente abre o circuito na hora; erros
  transitórios abrem ao passar de um limiar.
- **Fallback**: cadeia de provedores do roteador (ex.: Claude → Gemini → Groq). Se todos falharem, resposta
  amigável pré-definida + registro para a equipe.
- **Bulkhead**: limite de chamadas simultâneas por provedor, para um provedor lento não prender todos os workers.
- **Idempotência**: chamadas de LLM não têm efeito colateral (retry seguro); `registrar_solicitacao` e operações de
  pagamento usam idempotency key.
- Lib: `tenacity` para retry/backoff/jitter; circuit breaker e bulkhead implementados no próprio adapter.

#### Valores iniciais (configuráveis; calibrar com as métricas do Grafana)

**LLMs**

| Parâmetro | Valor |
|---|---|
| Deadline total por mensagem do cliente | 30 s |
| Timeout do classificador (Groq) | 5 s |
| Timeout por nível | simples (Groq) 10 s · média (Gemini) 15 s · complexa (Claude) 25 s |
| Tentativas | no máximo 3 (1 + 2 retries) |
| Backoff + full jitter | base 0,5 s, ×2, teto 4 s → 1º retry `U(0; 0,5 s)`, 2º retry `U(0; 1 s)` — `wait_random_exponential(multiplier=0.5, max=4)` |
| `Retry-After` | respeitado se ≤ 5 s **e** couber no deadline; senão, fallback imediato |
| Orçamento | só tenta de novo se o tempo restante do deadline ≥ timeout da chamada |

**Circuit breaker (por provedor)**

| Parâmetro | Valor |
|---|---|
| Abre após | 5 falhas transitórias seguidas **ou** ≥ 50% de falhas em janela de 30 s (mínimo 10 chamadas) |
| Erro de autenticação (`401`/`403`) ou crédito/cota esgotados | abre imediatamente |
| Tempo aberto | backoff exponencial com jitter "equal": `d = min(teto, base × 2^n)`, n = reaberturas consecutivas (zera quando a sonda passa); tempo efetivo `d/2 + U(0, d/2)`. Transitório: base 30 s, teto 5 min · autenticação/crédito: base 10 min, teto 1 h |
| Meio-aberto | 1 única chamada de teste (singleflight: reserva atômica no asyncio; na Fase 2, `SET NX PX` no Redis): sucesso fecha, falha reabre com o próximo degrau do backoff; sonda que não reporta é liberada após 30 s |

**Bulkhead (chamadas simultâneas por pod)**: Groq 10 · Gemini 10 · Claude 5.

**Demais dependências**

| Dependência | Timeout | Retry | Se cair |
|---|---|---|---|
| Postgres | 2 s | 2 tentativas, base 0,1 s, teto 0,5 s (só leituras/erros de conexão) | erro amigável ao cliente |
| Redis | 300 ms | 1 tentativa | rate limit **fail-open** com bucket local de emergência; sessão degradada (conversa segue sem histórico) |
| Gateway de pagamento | 10 s | 3 tentativas, base 1 s, teto 8 s (só operações idempotentes) | encaminha para a equipe (sem trocar de gateway) |

### 5.3 Redis — o que guarda e por quê

O Redis guarda o que precisa ser **compartilhado entre réplicas**:

| Uso | Tipo | Por quê |
|---|---|---|
| Sessões (histórico da conversa, com TTL) | dado efêmero (fonte da verdade) | permite a app ser stateless |
| Token buckets do rate limit | dado efêmero (fonte da verdade) | o limite tem que ser um só para todas as réplicas |
| Cache de leitura do catálogo | **cache-aside** | reduz carga no Postgres com muitas réplicas/tenants |

**Padrão de cache: cache-aside** (a app lê do cache; se não houver, lê do banco e grava no cache com TTL).

| Padrão | Decisão | Motivo |
|---|---|---|
| **Cache-aside** | ✅ | Leitura pesada e escrita rara (catálogo); se o Redis cair, a app lê direto do banco. |
| Write-through | ❌ | O agente não escreve no catálogo; não há escrita para sincronizar. |
| Write-behind | ❌ | Grava no banco de forma assíncrona: risco de **perder** solicitações/reclamações se o Redis cair. Inaceitável para dados de cliente. |

Cuidados:
- Chave com tenant: `cache:{tenant}:produtos:{consulta}`; TTL curto (ex.: 60 s).
- **Estoque nunca vem do cache** quando a resposta confirma disponibilidade (manual §7.1/§7.3: "nunca confirmar
  disponibilidade sem verificar o estoque atual"). `detalhar_produto` lê estoque direto do banco.
- Invalidação explícita do cache do tenant quando o seed/admin altera dados.
- Config e manual do tenant: cache **em memória por pod** (L1) com single-flight; não vão para o Redis.
- **Sem cache de respostas do LLM**: cada conversa é única e cachear preços/estoque em respostas prontas é arriscado.
  A economia de tokens vem do prompt caching do provedor (CAG).

O que **não** fica no Redis:
- **Circuit breaker**: estado em memória por pod. É peça de resiliência, então não depende do Redis (se o Redis cair,
  continua funcionando). Custo: cada réplica aprende sozinha que um provedor caiu — mínimo, pois erro permanente abre
  o circuito na primeira falha. Porta `EstadoCircuito` permite um adapter Redis se houver muitas réplicas.
- **Lock**: advisory lock do Postgres. **Single-flight**: memória por pod.

No **modo local** (sem Docker), Redis é substituído por adapters em memória.

## 6. Borda — Traefik (load balancer, API Gateway, Ingress Controller)

| Ambiente | Papel |
|---|---|
| docker-compose | load balancer/reverse proxy; descobre as réplicas da app via labels do Docker |
| Kubernetes | **Ingress Controller**, lendo recursos `Ingress` padrão |
| Ambos | **API Gateway**: ponto único de entrada com middlewares |

Middlewares na borda: TLS (Let's Encrypt), roteamento (`/v1/chat`, `/v1/admin`, `/v1/tools`, `/grafana` protegido;
`/metrics` nunca exposto), **rate limit por IP**, headers de segurança, CORS, compressão, IP allowlist opcional em
`/v1/admin`.

Divisão de responsabilidades (sem duplicar regras):
- **Borda**: o que é genérico — TLS, rate limit por IP, roteamento, headers.
- **App**: o que depende do negócio — rate limit por tenant/sessão/usuário, RBAC, validação do JWT, resolução do tenant.

Decisões:
- **Sem retry no gateway** para `POST /v1/chat`: cada tentativa custa uma chamada de LLM; a resiliência fica na app.
- **Tenant por subdomínio** (`emporio.api.exemplo.com`): o gateway repassa o `Host`; a app resolve o tenant.
- `Ingress` **padrão** do Kubernetes (middlewares do Traefik por annotation): trocar o controller (ex.: NGINX) não
  invalida o roteamento. Evolução: **Gateway API**, sucessora do Ingress, já suportada pelo Traefik.

## 7. Observabilidade

**OpenTelemetry** como abstração: a app é instrumentada uma vez; trocar o destino é configuração de exportador.

| Ferramenta | Papel |
|---|---|
| **Sentry** | erros (stack trace) e **performance** (traces: tempo de LLM, banco e cada tool) |
| **Prometheus + Grafana** | métricas e dashboards |
| **Loki** | logs estruturados (JSON), pesquisáveis por tenant/sessão |

Fontes: app (OTel), Traefik (métricas e traces da borda).

Métricas do agente (diferencial de AI Engineer): tokens e custo por provedor/tenant; distribuição do roteamento
(simples/média/complexa); taxa de fallback e estado do circuit breaker por provedor; latência por nível e por tool;
cache hit do prompt (economia do CAG); `429` por camada de rate limit.

Cuidados:
- Toda telemetria leva `tenant_id`.
- **LGPD**: conteúdo das mensagens e dados de clientes **não** vão para Sentry nem logs (`send_default_pii=False`,
  scrubbing de e-mail/telefone). Só metadados (tempo, tokens, tool, nível).
- Sentry opcional: sem `SENTRY_DSN` no `.env`, não é ativado.

## 8. RBAC (controle de acesso por papel)

| Papel | Permissões |
|---|---|
| `cliente` | conversar no chat do próprio tenant; pedidos só após verificação (nº do pedido + e-mail/telefone) |
| `atendente` | ver e resolver solicitações (reclamações, cotações) e conversas |
| `gerente` | tudo do atendente + editar configuração da loja (persona, manual, limites) |
| `admin_plataforma` | criar tenants, métricas de todos os tenants |

- **JWT** com claims `tenant_id` e `roles`, via porta `ProvedorIdentidade`.
- Permissões finas (`solicitacoes:ler`, `solicitacoes:resolver`, `config:editar`, `tenants:criar`, `tools:usar`)
  mapeadas por papel; no FastAPI: `Depends(exige("solicitacoes:ler"))`.
- **Dupla checagem de tenant**: `tenant_id` do token deve bater com o tenant da requisição (soma-se ao schema).
- **RBAC também para o LLM**: as tools entregues ao agente dependem do papel de quem conversa. No chat do cliente o
  LLM nem recebe tools administrativas → prompt injection não tem o que escalar (menor privilégio aplicado à IA).
- `/v1/tools/*` e MCP exigem token com `tools:usar` (ex.: o n8n usa uma credencial de serviço por tenant).
- Escopo no protótipo: 4 papéis, JWT local, endpoints `GET /v1/admin/solicitacoes`,
  `PATCH /v1/admin/solicitacoes/{id}`, `PUT /v1/admin/config`, e comando de CLI para gerar tokens de teste.

## 9. Interface

- **API FastAPI**: `POST /v1/chat` (`session_id`, mensagem; tenant pelo subdomínio/header), endpoints admin,
  `/v1/tools/*`, `/health`, `/ready`, `/metrics` (interno).
- **CLI** (`uv run emporio ...`, entry point de `emporio.adaptadores.cli`) com dois modos:
  - **local (padrão)**: chama o núcleo **no mesmo processo** (via `composicao.montar()` e a porta
    `MotorDeAtendimento`, §3.3), sem API no ar — CSVs + adapters em memória;
  - **remoto** (`--api URL`): consome a API (usado com o docker-compose).
- Comandos: `chat`, `diagnostico` (chaves encontradas, modo ativo, dados carregados), `token` (gera JWT de teste).

### 9.1 Como o avaliador roda o projeto (requisito)

A primeira seção do README é "Como rodar em 5 minutos", com comandos para Windows, macOS e Linux.

| Caminho | Pré-requisitos | Comandos | O que mostra |
|---|---|---|---|
| **1. Rápido** (padrão) | git + uv (o uv instala o Python 3.12) | `git clone` → `uv sync` → `cp .env.example .env` (uma chave) → `uv run emporio chat` | o agente completo, sobre os CSVs, sem Docker/Postgres/Redis |
| **2. Completo** | + Docker | `docker compose up -d` (+ `--profile observabilidade`) | réplicas, Traefik, schema por tenant, rate limit, dashboards |
| **3. Sem rodar** | nenhum | ler `exemplos/`; `uv run pytest` | conversas gravadas; regras de negócio testadas **sem chave de API** (LLMs simulados) |

- **Basta uma chave de LLM** (Gemini grátis recomendado): o roteador usa só os provedores com chave; sem a chave do
  Claude, o nível complexo cai no Gemini.
- Nenhuma chave nossa vai para o repositório. Opcional: chave com limite de gasto enviada no e-mail de entrega,
  fora do repositório.
- Critério de aceite do núcleo: um clone limpo, seguindo só o README, conversa com o agente no caminho 1.

### 9.2 Persona: Sônia

A assistente virtual se chama **Sônia** (nome comum no Brasil, com eco de "som"). Base no manual: §7.1 ("informal mas
profissional, como um amigo que entende de música, evitar linguagem robotizada") e §7.2 ("clara e objetiva").
Resumo: **simpática e direta ao ponto**.

| Regra | Na prática |
|---|---|
| Resposta primeiro | perguntou preço → o preço vem na primeira frase |
| Curta | 2 a 5 frases; listas só para comparar produtos |
| Calorosa sem enrolar | chama pelo nome quando sabe; nada de "Prezado cliente" |
| Entende de música | explica rapidamente (ex.: nylon × aço para iniciante) quando ajuda |
| Emoji com moderação | no máximo 1; nenhum em reclamação |
| Tom em reclamação | menos descontraída, mais empática e resolutiva |
| Honesta | "não tenho essa informação" + encaminha; nunca inventa |
| Fechamento (§7.2) | pergunta se pode ajudar em algo mais só ao fim do assunto |
| Inglês | mesmo tom: friendly and straight to the point |
| Identidade | se apresenta como assistente virtual da loja (não finge ser humana) |

Exemplo:
> ❌ Robótico: "Prezado cliente, informamos que o produto Takamine GD20 Dreadnought Natural encontra-se disponível pelo
> valor de R$ 2.199,00. Permanecemos à disposição."
> ❌ Informal demais: "Opaaa, meu consagrado!!! 🎸🔥 Esse violão é TOP demais, cara!!!"
> ✅ Sônia: "O Takamine GD20 sai por **R$ 2.199,00** e tem em estoque! No PIX fica R$ 2.089,05 (5% off), ou em até 12x
> de R$ 183,25 sem juros. É um dreadnought com tampo sólido de cedro, ótimo pra dedilhado. Quer comparar com outro
> modelo?"

(Valores do exemplo são ilustrativos; na conversa real vêm sempre das tools.)

## 10. Idiomas do atendimento (pt-BR / en-US)

- **Idioma detectado, não perguntado** (revisado em 2026-11-01, premissa #57): heurística por palavras pt × en na
  mensagem; ambíguo → idioma do canal (`--idioma` na CLI, `idioma`/`Accept-Language` na API, senão o padrão do
  tenant), sem fixar na sessão. Perguntar custaria um turno e perderia a pergunta original.
- Idioma salvo na **sessão** (Redis); o cliente pode trocar a qualquer momento.
- API: campo opcional `idioma` em `POST /v1/chat` ou header `Accept-Language` (canal que já sabe o idioma pula a
  pergunta).
- **Manual não é traduzido**: o LLM lê as regras em PT e responde no idioma da sessão (CAG inalterado).
- Nomes de produto mantidos; descrições traduzidas pelo LLM na resposta.
- Preços sempre em **BRL**, formatados por locale com `babel` (`R$ 1.299,00` × `R$1,299.00`).
- **Mensagens fixas** (erros, `429`, fallback total, loja fechada) vêm de um catálogo de traduções, não do LLM —
  funcionam mesmo com todos os provedores fora.
- Persona da §7.1 nos dois idiomas.
- Multi-tenant: `idiomas_suportados` e `idioma_padrao` por loja em `config/tenants.yaml`.
- Uma das conversas de exemplo é em inglês.

## 11. Documentação bilíngue

- `README.md` em **pt-BR** (principal: o desafio e a loja são brasileiros) e `README.en-US.md` em **en-US**, com
  link de troca de idioma no topo de cada um. Conteúdo equivalente nos dois; o código e os identificadores ficam
  iguais.
- Os demais documentos de `docs/` ficam em pt-BR.

## 12. Deploy

- Dockerfile **multi-stage**; `docker-compose` (Traefik + app em réplicas + Postgres + Redis + Prometheus + Grafana +
  Loki); manifestos K8s com kustomize (Deployment, Service, Ingress com Traefik, HPA, VPA, PDB, ConfigMap, Secret de
  exemplo, Job de migração); pipeline CI/CD multi-stage. Detalhes em `docs/deploy.md`.

## 13. Melhorias futuras (vão para a seção "Limitações e próximos passos" do README)

### 13.1 Entrada multimodal: OCR e transcrição de áudio

Clientes de WhatsApp mandam muito **áudio** e **foto** (nota fiscal, comprovante de pagamento, foto de defeito,
foto de um instrumento). A ideia é converter mídia em texto **antes** do agente, sem mudar o núcleo:

```
mensagem com mídia → PreProcessadorDeMidia → texto → MotorDeAtendimento (fluxo normal)
                       ├─ TranscritorAudio → Whisper no Groq · Gemini (áudio)
                       └─ LeitorOCR        → visão do Gemini/Claude · Tesseract (local) · Google Vision
```

- Duas novas portas de saída (`TranscritorAudio`, `LeitorOCR`) → trocar o provedor é trocar o adapter.
- Reaproveita as chaves já usadas (Groq e Gemini oferecem transcrição; Gemini e Claude leem imagens).
- Casos de uso: foto da nota fiscal para iniciar troca/devolução; foto do defeito anexada à reclamação; áudio
  transcrito como mensagem comum.
- Cuidados: limite de tamanho/duração por tenant (rate limit e custo); **LGPD** — mídia pode conter dados pessoais,
  então é processada e descartada, sem armazenar; o texto extraído passa pelo mesmo scrubbing de PII da telemetria.

### 13.2 Outras melhorias já identificadas

- Cotação real de frete (APIs dos Correios/Jadlog) + peso e dimensões nos produtos.
- Data real de recebimento do pedido (hoje usamos `estimated_delivery` como substituto).
- Adapter real de gateway de pagamento (ver `docs/pagamento.md`).
- `AgenteN8n` como motor alternativo de atendimento.
- Estado do circuit breaker compartilhado no Redis para muitas réplicas.
- Gateway API do Kubernetes no lugar do Ingress.
- Integração com o canal WhatsApp (webhook → `/v1/chat`).
