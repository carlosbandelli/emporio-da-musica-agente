**Português (pt-BR)** · [English (en-US)](README.en-US.md)

# Empório da Música: agente de atendimento Sônia

Agente de atendimento por texto da **Empório da Música**, loja fictícia de instrumentos musicais em Campo Grande/MS.
A assistente se chama **Sônia**. Ela responde sobre preço, estoque, pedido, horário, endereço, parcelamento, devolução,
garantia e frete. Os dados vêm de ferramentas (CSVs) e as regras, do manual de políticas. A loja não vende acessórios
(cordas, palhetas, cabos, cases, pedais, amplificadores): a Sônia redireciona esses pedidos.

Desafio técnico Artefact, AI Engineer 2026.

O idioma padrão das respostas é o português do Brasil. O inglês funciona com `--idioma en-US` e com a detecção de
idioma (seção 4.10).

**Estado da entrega:** o núcleo (Fase 1) roda no terminal com LLM real. A API (task 2.1) já roda local; o restante da Fase 2 (Postgres,
Redis, observabilidade, RBAC, deploy) não está implementada. Veja a seção 8.

---

## 1. Como rodar em 5 minutos

Você precisa de [git](https://git-scm.com/) e do [uv](https://docs.astral.sh/uv/). O uv instala o Python 3.12 sozinho.

| Caminho | Pré-requisitos | Comandos | O que mostra | Situação |
|---|---|---|---|---|
| **1. Rápido** (padrão) | git, uv e uma chave de LLM | `uv sync`, `.env`, `uv run emporio chat` | o agente completo, sobre os CSVs, sem Docker | funciona (núcleo) |
| **2. Completo** | + Docker | `docker compose up -d` | réplicas, Traefik, schema por tenant, rate limit, dashboards | funciona (smoke executado) |
| **3. Sem rodar** | nenhum | ler `exemplos/`; `uv run pytest` | conversas gravadas; regras testadas com LLM simulado, sem chave | funciona |

### Caminho 1: passo a passo

**1. Instale o uv.**

Windows (PowerShell):

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

macOS e Linux:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**2. Clone e instale as dependências** (Windows, macOS e Linux):

```bash
git clone https://github.com/carlosbandelli/emporio-da-musica-agente.git
cd emporio-da-musica-agente
uv sync
```

**3. Crie o arquivo de ambiente com uma chave.**

Windows (PowerShell):

```powershell
Copy-Item .env.example .env
```

macOS e Linux:

```bash
cp .env.example .env
```

Abra o `.env` e preencha **uma** chave. A recomendada é `GOOGLE_API_KEY` (Gemini, plano grátis), obtida em
<https://aistudio.google.com/api-keys>. As outras (`GROQ_API_KEY`, `ANTHROPIC_API_KEY`) são opcionais. Chaves vazias
são ignoradas.

**4. Confira a configuração e converse.**

```bash
uv run emporio diagnostico
uv run emporio chat
```

- `diagnostico` mostra quais provedores têm chave (só os nomes), o motor, o tenant, os dados carregados, o manual e a
  data de referência. Nunca mostra valores de chave.
- `chat` abre a conversa. Digite `sair` (ou Ctrl+C) para encerrar.

**Opcional:**

- Simular a data de "hoje". Os pedidos do CSV vão até março de 2026, então, sem isso, o agente usa a data real. Para
  simular, descomente no `.env`: `DATA_REFERENCIA=2026-03-10T15:00:00-04:00`.
- Conversar em inglês: `uv run emporio chat --idioma en-US`.
- Gravar a conversa em Markdown: `uv run emporio chat --gravar minha-conversa.md`.

### Sem nenhuma chave

- `uv run pytest` roda todos os testes sem chave. Os testes que chamam LLM real ficam pulados.
- `uv run emporio chat` encerra com código de saída 2 e uma mensagem dizendo o que falta, sem stack trace.

### Observações

- Rode os comandos na raiz do repositório. O `.env` e a pasta `config/` são lidos a partir da pasta atual.
- A API (task 2.1) roda local: `uv run uvicorn emporio.adaptadores.api.app:app --port 8000` (`GET /health`, `GET /ready`,
  `POST /v1/chat` com `X-Tenant-ID` ou subdomínio do `Host`; `Accept-Language` escolhe pt-BR ou en-US). Sem chave de LLM ela
  sobe, mas `/ready` e `/v1/chat` devolvem 503. O caminho 2 (Docker) está na próxima seção.

### Caminho 2: Docker (Traefik, 3 réplicas, Postgres, Redis, Prometheus)

Pré-requisito: Docker com Compose v2 e o arquivo `.env` com ao menos uma chave de LLM (o compose o lê por `env_file`).

```bash
docker compose up -d --build        # migração (serviço "migrate") -> 3 réplicas da app -> Traefik
docker compose ps                   # app x3 healthy, postgres, redis, traefik, prometheus
curl localhost/health
curl localhost/ready
curl -X POST localhost/v1/chat -H "X-Tenant-ID: emporio" -H "Content-Type: application/json" \
  -d '{"session_id":"demo-1","mensagem":"Ola, qual o horario de funcionamento da loja?"}'
docker compose down -v              # derruba e apaga os volumes
```

Smoke executado (Docker Engine 29, Windows 11): `migrate` terminou com código 0; as 3 réplicas ficaram `healthy`;
`/health` e `/ready` via Traefik responderam 200 (10 chamadas a `/ready` caíram nas 3 réplicas); `/metrics` não passa pela
borda (404) e o Prometheus raspou os 4 alvos (3 réplicas e o Traefik); uma chamada a `POST /v1/chat` respondeu 200
(nível `simples`, provedor `gemini`, tool `status_loja`). Variáveis úteis: `APP_REPLICAS` (padrão 3) e
`POSTGRES_PASSWORD`.

Observações do caminho 2:

- O Traefik é v3.6 porque as versões anteriores não falam com o Docker Engine 29 (API mínima 1.44).
- No Windows, se o caminho do repositório tiver acentos (como `Técnico`), o BuildKit falha com `x-docker-expose-session-sharedkey
  contains value with non-printable ASCII characters`. Clone para um caminho só com ASCII, ou construa a imagem de uma cópia
  ASCII (`docker build --target runtime -t emporio:local .`) e suba com `docker compose up -d --no-build`.
- Grafana e Sentry ficaram para o futuro. O Sentry agruparia exceções com stack trace e contexto, alertaria regressões e
  rastrearia releases. O Grafana montaria painéis sobre as métricas Prometheus (latência p95 por provedor, taxa de
  fallback/degradação, circuito aberto, tokens e custo por tenant) e alertas. O dashboard de partida está em
  `deploy/observabilidade/grafana/` (sem serviço no compose). OTel Collector, Loki e Alloy seguem como perfil opcional
  (`--profile observabilidade`, com `OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4318`).
- CI (`.github/workflows/ci-cd.yml`): lint (ruff e import-linter), tipos (mypy) e testes (pytest, com Postgres e Redis
  como serviços do Actions) em cadeia, depois build da imagem no GHCR. Scan e deploy em Kubernetes são exemplo: o deploy
  só roda com a variável de repositório `DEPLOY_ENABLED=true` e o secret `KUBECONFIG_PRODUCAO`; não há cluster nesta entrega.

---

## 2. Comandos

| Comando | O que faz |
|---|---|
| `uv run emporio --help` | lista os comandos |
| `uv run emporio versao` | mostra a versão instalada |
| `uv run emporio chat [--tenant ID] [--idioma pt-BR ou en-US] [--gravar ARQUIVO.md]` | conversa no terminal, no mesmo processo |
| `uv run emporio chat --api http://localhost:8000 [--tenant ID]` | mesma conversa, mas cada mensagem vai para `POST /v1/chat` da API |
| `uv run emporio diagnostico [--tenant ID]` | estado da configuração; não mostra chaves |
| `uv run pytest` | testes (sem chave; os `llm_real` ficam pulados) |
| `uv run pytest --cov=src/emporio -q` | testes com cobertura |
| `uv run lint-imports` | regras de dependência C1 a C5 (12 contratos) |
| `uv run ruff check . && uv run ruff format --check .` | lint e formatação |
| `uv run mypy src` | checagem de tipos (strict em `src/`) |
| `EMPORIO_LLM_REAL=1 uv run pytest tests/e2e/test_llm_real.py -v` | testes com LLM real (precisa de chave e gasta cota) |

No PowerShell, o teste com LLM real fica: `$env:EMPORIO_LLM_REAL=1; uv run pytest tests/e2e/test_llm_real.py -v`.

A CLI ainda não tem `emporio token`: é da Fase 2.

---

## 3. Estrutura do repositório

```
src/emporio/
  modulos/        regras de negócio puras, sem I/O nem SDK: catalogo, comercial, pedidos, politicas, atendimento, comum
  portas/         contratos (Protocol) e tipos compartilhados (TenantContext, MotorDeAtendimento, LLMProvider...)
  adaptadores/    cli, motor/nativo, llm (groq, gemini, claude, falso), csv, memoria, relogio
  configuracao/   Settings (lê o .env) e carregamento de tenants
  composicao.py   único lugar que liga portas a adaptadores
config/           tenants.yaml e o manual de políticas em Markdown (config/tenants/emporio/politicas.md)
data/             CSVs da loja e o PDF original do manual
docs/             arquitetura, premissas, pagamento (desenho) e deploy
exemplos/         3 conversas gravadas com LLM real
tests/            unitários, contrato (I1 a I8) e ponta a ponta (e2e)
deploy/           artefatos da Fase 2 (não exercitados)
```

---

## 4. Decisões técnicas e justificativas

### 4.1 Linguagem, ambiente e dependências

- **Python 3.12** (`.python-version`) e **uv** com `uv.lock`. O enunciado só exige Python. O uv instala o interpretador e
  resolve as dependências do mesmo jeito em Windows, macOS e Linux.
- O projeto declara 11 dependências de runtime: os SDKs de Groq, Gemini e Anthropic, `tenacity` (retry), `pydantic` e
  `pydantic-settings`, `typer` (CLI), `babel` e `holidays` (datas e feriados), `pyyaml` e `tzdata`.

### 4.2 Framework do agente: loop próprio, sem LangChain ou LangGraph

Escrevi o loop agentic (roteia, chama o LLM, executa tools, repete) em vez de usar um framework.

- **Controle do loop.** O limite de 6 chamadas ao LLM por mensagem, o deadline de 30 s, a guarda de valores monetários
  e a troca de provedor precisam acontecer em pontos exatos do fluxo. Num framework, cada um viraria um gancho a adaptar.
- **Resiliência específica.** Retry com backoff e jitter, circuit breaker e bulkhead por provedor (arquitetura §5.2)
  são lógica própria. Uma biblioteca genérica exigiria a mesma adaptação.
- **Tools testáveis.** As tools são casos de uso Python puros (`modulos/*/aplicacao.py`). O motor só as chama, e os
  testes as executam sem LLM.
- **Custo.** O loop do motor (`adaptadores/motor/nativo/agente.py`) tem pouco mais de 300 linhas, e o projeto o mantém
  sem depender de nenhum orquestrador externo. Em troca, não há grafos nem memória prontos, como num framework. Se o
  fluxo virar vários agentes cooperando, esta decisão deve ser reavaliada.

### 4.3 LLM: três provedores, roteados por complexidade

| Nível | Exemplos | Provedor e modelo padrão | Motivo |
|---|---|---|---|
| simples | saudação, horário, endereço, fora de escopo | Groq `openai/gpt-oss-120b` (raciocínio `low`) | latência baixa e tool calling |
| média | busca de produto, preço, status de pedido | Gemini `gemini-2.5-flash` | plano grátis; respondeu em 0,8 s no teste de 2026-11-01 |
| complexa | parcelas, troca, devolução, garantia, frete, reclamação | Claude `claude-opus-5-5` (esforço `low`) | regras com exceções, onde errar custa caro |

- **Classificador.** Uma chamada curta ao Groq, sem tools, com no máximo 5 s. Recebe a mensagem e as três últimas falas
  da conversa, para entender respostas curtas como "e em 10x?". **Na dúvida, sobe de nível.** Sem Groq, usa Gemini e
  depois Claude. Se a classificação falha, vai para `complexa`. Se todos os níveis têm a mesma cadeia (uma só chave),
  o classificador nem é chamado (premissas #48 e #50).
- **Fallback.** Cadeias, filtradas pelas chaves presentes (premissa #49):

  | Nível | Ordem |
  |---|---|
  | complexa | Claude → Gemini → Groq |
  | média | Gemini → Claude → Groq |
  | simples | Groq → Gemini → Claude |

- **Resiliência por provedor** (arquitetura §5.2):
  - Até 3 tentativas, só para erros transitórios (timeout, conexão, 429, 5xx), com backoff exponencial e full jitter.
    Os retries internos dos SDKs ficam desligados.
  - Circuit breaker: abre após 5 falhas transitórias seguidas ou 50% de falhas em 30 s (mínimo de 10 chamadas).
    Autenticação inválida (401/403) e crédito ou cota esgotados abrem na hora (premissa #53). Depois, uma única sonda
    testa a volta do provedor.
  - Bulkhead: no máximo 5 chamadas simultâneas ao Claude e 10 ao Gemini e ao Groq.
- **Modelos e erros conhecidos.**
  - `gemini-flash-latest` respondeu com 504 em chamada real. Por isso o padrão é `gemini-2.5-flash` (premissa #43).
  - O plano grátis do Groq tem 8 mil tokens por minuto. Cada turno com tool gasta cerca de 6 mil tokens de entrada,
    então só com a chave Groq um turno pode estourar o limite e sair degradado (premissa #67).
  - A conta Anthropic usada no desenvolvimento estava sem crédito em 2026-11-01. Por isso, os exemplos foram gravados
    com Gemini e Groq, e o nível complexo caiu para Gemini pelo fallback (premissa #66). O adaptador do Claude tem
    testes com fakes HTTP, mas nenhuma chamada real validada. A chave usada no desenvolvimento expira por volta de
    2026-11-07.

### 4.4 Recuperação: CAG, não RAG

Cada tipo de conhecimento usa a técnica que dá resposta exata:

| Conhecimento | Técnica | Como |
|---|---|---|
| Dados: estoque, preço, promoções, pedidos | tools (function calling) | consulta estruturada exata nos CSVs; o LLM não lê preço de texto |
| Políticas: manual de 8 páginas (v2.1) | CAG (cache-augmented generation) | o manual inteiro vai no system prompt, no bloco cacheável |
| Regras com cálculo: PIX, parcelas, frete, prazos, horário | código Python (`modulos/*/dominio.py`) | "o LLM decide e redige; o código calcula" |

Por que não RAG vetorial:

- Um vetor não filtra preço nem estoque. Para isso servem as tools.
- Os chunks separam regras relacionadas. A regra do PIX aparece em §3 e §6.2 do manual; um chunk pode trazer uma e
  perder a outra.
- O manual cabe no contexto. Não há ganho de recuperar só parte dele.
- Graph RAG e REFRAG também foram avaliados e descartados (arquitetura §1).

O manual é convertido do PDF para `config/tenants/emporio/politicas.md` uma vez, revisado à mão contra o original, e
versionado (premissa #20). O PDF em `data/` é a origem e não é lido em tempo de execução.

Custo: cada chamada leva cerca de 6 mil tokens de entrada, com o manual. O bloco cacheável é idêntico byte a byte entre
sessões, idiomas e datas (premissa #42), para o prompt caching do provedor funcionar. **Não medi a taxa de acerto do
cache:** as métricas para isso (`emporio_prompt_cache_total`) são da Fase 2.

### 4.5 Estratégia de prompt

- **Persona Sônia** (arquitetura §9.2, a partir do manual §7.1 e §7.2): informal, mas profissional, como uma amiga que
  entende de música. Resposta primeiro, 2 a 5 frases, no máximo um emoji e nenhum em reclamação. Se apresenta como
  assistente virtual e não finge ser humana.
- **Prompt em dois blocos** (premissa #42):
  - bloco cacheável: persona, regras em pt-BR e en-US e o manual completo. Depende só do tenant;
  - bloco dinâmico: idioma da sessão e data de referência em granularidade de dia, nunca a hora.
  - "A loja está aberta agora?" e "quando reabre?" são sempre respondidos pela tool `status_loja`, não pelo modelo.
- **O LLM redige, o código calcula.** Preço, parcela, frete e prazo vêm das tools. O LLM escreve a resposta em torno deles.
- **Nunca afirmar sem tool.** Preço, estoque e status de pedido só aparecem no texto se a tool correspondente foi
  chamada (invariante I6 da porta, arquitetura §3.2).
- **Guarda de valores monetários** (premissa #58). Todo `R$` da resposta final tem que vir de um resultado de tool da
  sessão, de uma fala do cliente ou do manual. Se não, há uma rodada corretiva. Se o modelo insistir, a resposta sai
  com a mensagem fixa, sem o valor.
- **Tools por papel.** O filtro por papel já existe no código, mas na Fase 1 as seis tools são de cliente. As tools
  administrativas da Fase 2 entram sem mudar o motor.
- **Mensagens fixas** (erro, limite de uso, loja fechada, deadline) vêm de um catálogo de traduções, não do LLM. Assim
  funcionam mesmo com todos os provedores fora.

### 4.6 Arquitetura: monólito modular e hexagonal só nas bordas

- **Monólito modular.** Um pacote por contexto (`catalogo`, `comercial`, `pedidos`, `politicas`, `atendimento`) e o
  shared kernel `comum` (`Dinheiro`). Cada pacote tem `dominio.py` (regras) e `aplicacao.py` (casos de uso, a API
  pública do módulo).
- **Hexagonal só onde há troca real.** Portas para o motor de atendimento, os provedores de LLM, os repositórios, o
  estado (sessão, rate limit, circuito) e o relógio. Trocar um deles muda só o adapter.
- **Composition root.** `composicao.py` é o único lugar que instancia adapters e lê o `Settings`. Não há singletons,
  `global` nem cache em variável de módulo.
- **Regras de dependência C1 a C5**, verificadas pelo import-linter (`uv run lint-imports`, 12 contratos). Uma exceção,
  registrada: a CLI pode importar a composição (premissa #65).
- **Contrato da porta `MotorDeAtendimento`** (arquitetura §3.2): `responder(ctx, sessao_id, mensagem)`. A suíte
  `tests/contrato` testa os invariantes I1 a I8 para cada motor registrado. Um motor novo, como o `AgenteN8n` da Fase 2,
  só entra se passar nela.

### 4.7 Interface: CLI

- O enunciado pede um agente de atendimento por texto, e não uma interface gráfica. A CLI roda sem servidor, sem Docker
  e sem navegador. Ela chama o motor pela mesma porta que a API vai usar.
- A opção `--gravar` salva a conversa em Markdown, turno a turno. Foi assim que os exemplos foram gerados.
- Trade-off: não há histórico visual nem canal real (WhatsApp). Um frontend web não foi pedido e não entrou no plano.

### 4.8 Histórico e sessão

- **Fase 1:** histórico em memória, por processo. Ele some ao sair do `chat` (premissa #21). A sessão expira em 30 min
  sem mensagens (premissa #60).
- **Append-only.** O histórico não é editado. Isso preserva o prefixo do prompt cacheado e os blocos de raciocínio que
  o Claude (`thinking`) e o Gemini (`thought_signature`) exigem de volta. Editar o histórico quebra o cache e pode gerar
  erro 400 (premissa #47).
- **Corte.** Acima de 40 mensagens, saem os turnos inteiros mais antigos. Nunca fica um `tool_use` sem o `tool_result`
  correspondente: se o turno falha no meio, a sessão guarda só a mensagem do cliente e a resposta fixa.
- **Fase 2:** Redis, com TTL, para compartilhar a sessão entre réplicas (arquitetura §5.3).

### 4.9 Limpeza de dados

Os CSVs têm problemas reais, e cada um tem um tratamento documentado:

| Problema | Tratamento | Premissa |
|---|---|---|
| `total_brl` ora em reais (`799.9`), ora parece em centavos (`11499`) | reais, com casas opcionais; convertido para centavos inteiros com `ROUND_HALF_UP` | #22 |
| `specs` com JSON inválido (não há casos atuais) | produto mantido com `specs` vazias e aviso com linha e id | #23 |
| linha com id, preço, categoria, estoque negativo ou status desconhecido | linha descartada com aviso (arquivo e número da linha, sem dados pessoais) | #24 |
| `payment_method`: `credit_Nx`, `debit`, `pix`, `boleto` | enum; `credit_Nx` vira crédito em N parcelas | #25 |
| `tracking_code` e `estimated_delivery` vazios em pedidos sem envio | `None`; não se presume que só pedido `shipped` tenha rastreio | #25 |
| várias linhas de promoção por produto; algumas inativas | só `is_active=1` aplica; promoções não acumulam, vale a maior | #26, #28 |
| categorias 6 a 8 sem produtos | mantidas; a resposta diz que não há itens no momento, sem afirmar que a loja não vende | #9, #26 |
| produto `discontinued` ou `coming_soon` | fora da busca padrão; quando consultado, oferece equivalentes | #26 |
| id de produto duplicado | vale a primeira ocorrência | #24 |
| pedido `delivered` sem data real de recebimento | usa `estimated_delivery` como substituto e diz isso na resposta | #6 |

O `customers.csv` tem dados pessoais. Por isso, `consultar_pedido` exige número do pedido **e** e-mail ou telefone, e
uma falha de verificação devolve sempre a mesma resposta genérica, sem revelar nada do pedido (premissas #15 e #40).

### 4.10 Idioma e persona

- O idioma é detectado, não perguntado: heurística por palavras em português e inglês. Quando a mensagem é ambígua,
  vale o idioma do canal (`--idioma`, padrão do tenant). Um pedido explícito, como "can we speak English?", troca o
  idioma a qualquer momento (premissa #57).
- Os valores são sempre em reais (BRL), formatados conforme o idioma da sessão.
- A persona é a mesma nos dois idiomas. O manual não é traduzido: o LLM lê as regras em português e responde no idioma
  da sessão.

---

## 5. Premissas (ambiguidades do enunciado)

O enunciado pede que, em caso de ambiguidade, seja assumida uma interpretação razoável e documentada. São 67
premissas, com a decisão e o motivo de cada uma, em [docs/premissas.md](docs/premissas.md). A regra geral é: a regra mais
específica vence, e na dúvida o agente não inventa, encaminha para a equipe.

Algumas das mais visíveis:

| # | Ambiguidade | Decisão |
|---|---|---|
| 1 | Telefone diferente na capa (3341-4444) e na §7 (WhatsApp 3321-4500) | informa os dois: telefone e WhatsApp |
| 2 | E-mail com e sem acento | `contato@emporiodamusica.com.br`, sem acento |
| 4 | Parcela mínima "R$ 100" na tabela × faixas na §3.1 | vale a §3.1: até 3x com mínimo de R$ 50 por parcela; 4 a 6x, R$ 80; 7 a 12x, R$ 100 |
| 5 | Defeito em 30 dias × garantia legal de 90 dias | até 30 dias da compra, troca na loja; até 90 dias do recebimento, garantia legal com a loja intermediando |
| 7 | Arrependimento vale só para compra online | todos os pedidos são tratados como online (têm entrega e rastreio) |
| 11 | Frete fora de Campo Grande depende de CEP, peso e dimensões | informa modalidades e prazos, não informa valor, e registra cotação para a equipe |
| 12 | "Região metropolitana" sem lista de cidades | só Campo Grande; a partir de R$ 500 o frete é grátis |
| 15 | Consulta de pedido e LGPD | número do pedido e e-mail ou telefone; sem correspondência, nenhum dado do pedido |
| 19 | Avaliador roda sem nenhuma chave | `diagnostico` explica, `chat` sai com código 2, `pytest` roda |
| 22 | `total_brl` em reais ou centavos | reais (`11499` = R$ 11.499,00) |
| 28 | Duas promoções ativas no mesmo produto | não acumulam: vale a de maior percentual |
| 29 | Arredondamento do desconto | calculado uma vez, em centavos, `ROUND_HALF_UP` (R$ 99,90 no PIX = R$ 94,91) |
| 31 | Pagamento combinado | só acima de R$ 2.000,00 (R$ 2.000,00 não combina); sem desconto PIX dentro do combinado |
| 36 | Contagem de prazos de devolução | o dia de referência é o dia 0 (recebimento; para defeito, a compra); o 7º dia ainda vale, o 8º não |
| 58 | Valor monetário não verificado na resposta | rodada corretiva; se insistir, mensagem sem o valor |

---

## 6. Qualidade e testes

- **Testes:** `uv run pytest -q` resulta em cerca de 729 passados e 3 pulados (os `llm_real`, sem chave). Cobertura
  de `src/emporio`: cerca de 98%. Números da última execução do gate, em 2026-11-01.
- **Gate estático:** ruff (lint e formatação), mypy em modo strict em `src/`, import-linter com 12 contratos, todos
  mantidos.
- **Três camadas de teste:**
  - unitários por módulo e adapter, com `LLMFalso` (respostas roteirizadas) e CSVs reais;
  - contrato da porta `MotorDeAtendimento` (I1 a I8), parametrizado pelos motores registrados (`tests/contrato`);
  - ponta a ponta (`tests/e2e/test_cenarios.py`): cenários do enunciado e das regras, com CSV real e LLM roteirizado. Um
    deles é o "violões até R$ 1.000", outro o "me arrependi, posso devolver?", com o pedido verificado.
- **Testes com LLM real** ficam em `tests/e2e/test_llm_real.py`, marcados `llm_real`. Só rodam com `EMPORIO_LLM_REAL=1` e
  uma chave. Chamadas reais mínimas mostraram dois problemas que os fakes não pegavam: o alias `gemini-flash-latest`
  (504) e o limite de tokens por minuto do Groq.

---

## 7. Conversas de exemplo

Três conversas gravadas com LLM real, pelo próprio `emporio chat --gravar`, com a data de referência no cabeçalho. Cada
resposta traz a linha `nível · provedor · tools chamadas`. Detalhes em [exemplos/README.md](exemplos/README.md).

| Arquivo | Cenário | O que mostra |
|---|---|---|
| [01-violoes-ate-1000.md](exemplos/01-violoes-ate-1000.md) | "violões até R$1000" e parcelamento | busca no catálogo e simulação de pagamento com a regra de parcela mínima |
| [02-arrependimento-pedido.md](exemplos/02-arrependimento-pedido.md) | "me arrependi da compra, posso devolver?" | pedido real verificado por e-mail e janela de 7 dias do manual aplicada à data |
| [03-english-takamine-strings.md](exemplos/03-english-takamine-strings.md) | em inglês: Takamine GD20, cordas e endereço | preço, PIX e 12x em formato en-US; acessório fora de escopo; endereço do manual |

Os valores citados batem com os CSVs, e a lógica deles está coberta por testes em `tests/e2e` (detalhes em
[exemplos/README.md](exemplos/README.md)). Limitação vista na gravação: no exemplo 01, a resposta à segunda pergunta trouxe
o parcelamento, mas omitiu o PIX que o cliente tinha pedido.

---

## 8. Limitações e próximos passos

### 8.1 Não implementado (Fase 2 e arquitetura §13)

A Fase 2 faz parte do desenho, mas não foi entregue. Os arquivos de `deploy/`, o `Dockerfile` e o `docker-compose.yml` foram
exercitados (seção 1, caminho 2); Kubernetes e o deploy da pipeline não:

- Postgres com um schema por tenant, migrações e seed; Redis para sessão, rate limit e cache;
- rate limit em camadas (sessão, usuário, tenant, provedor) e rate limit por IP na borda;
- observabilidade: as métricas de `docs/deploy.md` §6, traces e logs JSON;
- RBAC com JWT, papéis e `emporio token`;
- tools via HTTP (`/v1/tools/*`) e o motor `AgenteN8n`;
- gateway de pagamento fake (desenho em `docs/pagamento.md`);
- Traefik, Kubernetes e o deploy da pipeline.

### 8.2 Limitações conhecidas

- **Modo local.** Sessão, rate limit e circuito ficam em memória, por processo. O histórico some ao sair do `chat`.
- **Guarda só verifica valores monetários** (premissa #58). Estoque e status de pedido dependem da instrução no prompt
  e do uso da tool, não de uma verificação determinística.
- **Idioma por palavras** (premissa #57). Frases curtas e ambíguas caem no idioma do canal.
- **Claude não validado com chamada real** (premissa #66, seção 4.3). Groq e Gemini foram validados com uma chamada mínima
  cada.
- **Limites dos planos grátis** (premissa #67). Só com a chave Groq, turnos com tool podem sair degradados (confirmado no clone limpo: `docs/verificacao-clone-limpo.md`).
- **Frete fora de Campo Grande** não tem valor: não há CEP, peso nem dimensões nos dados. Vira cotação registrada
  (premissa #11).
- **Dados ausentes.** Não há data real de recebimento (premissa #6), nem lista de "venda final" (premissa #8), nem
  horários estendidos em datas especiais (premissa #13). Carnaval e Corpus Christi não são feriados do agente
  (premissa #33).
- **Divergências nos dados e no enunciado** foram decididas, não confirmadas com a loja: telefone e e-mail (premissas #1
  e #2). O total de dois pedidos é menor que a soma dos itens (premissa #22).
- **Dados pessoais no repositório.** `data/customers.csv` está versionado. Em produção, o seed deveria vir de uma fonte
  controlada (`docs/deploy.md` §7).
- **Detalhes da CLI.** A mensagem de despedida é fixa em português, mesmo em `--idioma en-US`. O `--help` pode sair com
  acentos trocados em console cp1252 quando a saída é redirecionada.
- **Exemplos.** São 3 conversas. A de reclamação, opcional, não foi gravada. O exemplo 01 tem a omissão do PIX descrita
  na seção 7.

### 8.3 O que eu faria com mais tempo

Em ordem de prioridade:

1. Validar o nível complexo com uma chave Claude com crédito e regravar os exemplos com ele.
2. Implementar a Fase 2 de API e dados: FastAPI, Postgres com schema por tenant e Redis. Depois, exercitar o Docker
   Compose, ponta a ponta.
3. Verificação determinística de estoque e status de pedido, além dos valores monetários.
4. Observabilidade conforme `docs/deploy.md` §6: métricas, traces e custo por tenant.
5. Frete real (Correios e Jadlog), com peso e dimensões nos produtos, e data real de recebimento.
6. Adapter de gateway de pagamento real (`docs/pagamento.md`).
7. Canal WhatsApp: um webhook que chama `POST /v1/chat`.
8. Entrada de áudio e de foto (OCR): nota fiscal, defeito, comprovante. Ver arquitetura §13.1.
9. Motor `AgenteN8n`, passando na mesma suíte de contrato.
10. Testes com LLM real no CI, com limite de custo por execução.

---

## 9. Como usei assistentes de IA

Usei o Claude Code como orquestrador do projeto inteiro. O fluxo foi este:

1. **Plano antes da execução.** Escrevi o plano junto com o assistente: `Plans.md`, com fases, tasks, critérios de
   aceite e o agente especialista de cada task. As decisões de arquitetura e as premissas ficaram em `docs/`.
2. **Execução por subagentes.** Cada task foi entregue por um subagente especialista. Em geral, até três rodaram em
   paralelo, em conjuntos de arquivos disjuntos:

   | Tasks | Subagente |
   |---|---|
   | 1.0, 1.2–1.4, 1.6 (esqueleto, portas, domínios, casos de uso) | `python-pro` |
   | 1.1 (spec delta na arquitetura) | `architect-reviewer` |
   | 1.5 (CSV e limpeza de dados) | `data-engineer` |
   | 1.7–1.10 (prompt/CAG, adapters de LLM, roteador, motor e composição) | `llm-architect` |
   | backoff com jitter no circuit breaker | `python-pro` |
   | 1.11, 1.13 (suíte de contrato, cenários ponta a ponta) | `test-automator` |
   | 1.12 (CLI) | `cli-developer` |
   | 1.14 (gravação dos exemplos com LLM real) | o próprio orquestrador |
   | 1.15 (README) | `technical-writer`, revisado pelo `content-quality-editor` |
3. **Revisão e gate.** Em cada entrega, o orquestrador revisou o código, rodou ruff, mypy, import-linter e pytest,
   registrou as premissas novas e fez um commit por task, com a issue correspondente no GitHub Project.
4. **Grafo de código.** Na revisão de cada Story, usei o code-review-graph para medir impacto e checar as regras C1 a C5.
   Registros do `Plans.md`: S1.A com 118 nós e 462 arestas; S1.B com 610 nós e 4.053 arestas; S1.C com 692 nós e 6.550
   arestas.
5. **Decisões humanas no meio do caminho.** Quatro pontos mudaram o rumo, e eu decidi cada um deles, não o assistente:
   - abrir o circuito do provedor na hora quando acaba o crédito (premissa #53);
   - backoff exponencial com jitter no circuit breaker, em vez de um tempo fixo (premissa #54);
   - gastar o mínimo de tokens nos testes com LLM real;
   - gravar os exemplos sem Claude, porque a conta estava sem crédito (premissa #66).
6. **Testes reais.** Os fakes passavam, mas chamadas reais acharam o alias `gemini-flash-latest` respondendo 504
   (corrigido no commit `ca35553`) e o limite de 8 mil tokens por minuto do Groq (premissa #67).

Limites do processo: o assistente escreveu código e textos, inclusive este README, mas as decisões de produto e as
premissas passaram por revisão humana. Os números de testes e de cobertura da seção 6 são da última execução do gate.

---

## Dados

Os CSVs em `data/` e o manual são material do enunciado do desafio. A loja, a persona e os dados de clientes são fictícios.
