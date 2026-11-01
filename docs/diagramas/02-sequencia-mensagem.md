# 02 · Sequência de uma mensagem

*EN: Sequence of one customer message: entry, motor, rate limit, router, LLM with fallback, tools, value guard, reply.*

Uma mensagem do cliente = um turno do `AgenteNativo` (`MotorDeAtendimento`). Mostra o caminho feliz e as
ramificações de falha. A parte de fallback e degradação está detalhada depois do diagrama. Base:
[`docs/arquitetura.md`](../arquitetura.md) §2, §3.2, §5.1 e §5.2; fluxo real em
`src/emporio/adaptadores/motor/nativo/agente.py` (`AgenteNativo._responder`).

```mermaid
sequenceDiagram
    autonumber
    actor Cliente
    participant CLI as cli (modo local)
    participant API as api (FastAPI)
    participant M as AgenteNativo
    participant S as SessaoStore (Redis ou memória)
    participant L as RateLimiter (Redis ou memória)
    participant R as Roteador
    participant P as ProvedorResiliente (Groq, Gemini, Claude)
    participant T as FerramentasLoja
    participant V as Serviços dos módulos (aplicacao)
    participant D as Repositórios (Postgres ou CSV)
    participant G as Guarda de valores (motor/comum)

    alt modo local (padrão)
        Cliente->>CLI: mensagem no terminal
        CLI->>M: responder(ctx, sessao_id, mensagem)
    else modo remoto (--api ou POST /v1/chat)
        Cliente->>API: POST /v1/chat
        API->>API: tenant pelo header ou subdomínio, papel pelo token opcional, idioma
        API->>M: responder(ctx, sessao_id, mensagem)
    end

    M->>M: config e manual do tenant (desconhecido vira mensagem fixa)
    M->>S: obter sessão (histórico e idioma)
    M->>M: validar entrada (I1): vazia ou longa vira mensagem fixa, sem LLM e sem gravar
    M->>L: consumir na camada sessão e depois na camada tenant
    Note over M,L: negado vira mensagem fixa LIMITE (HTTP 429 com Retry-After na API)
    M->>M: idioma da sessão (I7) e deadline do tenant (30 s, I5)
    M->>R: classificar a mensagem (Groq, depois Gemini, depois Claude)
    Note over R: falha ou resposta irreconhecível vira COMPLEXA (na dúvida, sobe de nível)
    R-->>M: nível simples, média ou complexa (nenhum se a cadeia é a mesma para todos)
    M->>M: montar prompt CAG (persona, manual de políticas, data de hoje)

    loop no máximo 6 chamadas ao LLM por mensagem
        M->>R: gerar(nível, histórico, tools do papel)
        R->>P: provedor 1 da cadeia do nível
        Note over P,R: fallback na cadeia, detalhado abaixo
        P-->>R: resposta ou erro
        R-->>M: resposta e nome do provedor que respondeu

        alt o LLM pediu tools
            M->>T: executar as tools pedidas (em paralelo)
            T->>V: caso de uso (por exemplo detalhar_produto ou consultar_pedido)
            V->>D: consulta exata (preço, estoque, status do pedido)
            D-->>V: dados
            V-->>T: resultado ok, ou erro de negócio como ok falso
            T-->>M: resultados em JSON, devolvidos ao LLM como mensagens de tool
        else o LLM devolveu texto final
            M->>G: valores em R$ e percentuais conferidos contra tools, manual e cliente
            alt todos os valores foram verificados
                G-->>M: sem suspeitos
                M-->>Cliente: texto final (sai do loop)
            else valor não verificado
                G-->>M: suspeitos
                M->>R: rodada corretiva, uma única vez
                Note over M,G: se o valor persistir, a resposta vira VALOR_NAO_VERIFICADO (mensagem fixa)
            end
        end
    end

    M->>S: gravar o turno (TTL de 30 min, no máximo 40 mensagens)
    M-->>API: RespostaAtendimento (texto, idioma, nível, provedor, tools chamadas, degradada)
    M-->>CLI: mesma resposta, impressa no terminal
    API-->>Cliente: RespostaChat (persona da loja no texto)
```

> Com `MOTOR_ATENDIMENTO=n8n` o passo "LLM" vira um `POST` ao webhook do n8n, e as tools voltam pela API em
> `/v1/tools/{nome}`. A guarda de valores e as mensagens fixas continuam no Python (`motor/n8n/agente.py`).

## Fallback da cadeia de LLMs (`Roteador` e `ProvedorResiliente`)

Cadeias por nível (`CADEIAS` em `motor/nativo/roteador.py`): simples = Groq, Gemini, Claude. Média = Gemini, Claude,
Groq. Complexa = Claude, Gemini, Groq. Provedores sem chave saem da cadeia. Se a cadeia tiver um provedor só, o
fallback não existe, e o caminho de falha é o mesmo.

```mermaid
flowchart LR
    A["chamada ao provedor da cadeia"] --> B{"circuito aberto?"}
    B -->|"sim"| F["próximo provedor da cadeia"]
    B -->|"não"| C["tentativa (bulkhead e timeout)"]
    C -->|"ok"| OK["resposta ao motor"]
    C -->|"transitório: timeout, conexão, 429 ou 5xx"| D{"ainda cabe nas 3 tentativas e no prazo?"}
    D -->|"sim, espera com backoff e jitter"| C
    D -->|"não"| F
    C -->|"permanente: 400, 401, 403 ou sem crédito"| F
    F --> G{"há próximo provedor?"}
    G -->|"sim"| A
    G -->|"não"| H["ErroSemProvedor: mensagem fixa INDISPONIVEL, degradada"]
```

- Erro permanente (`401`/`403`, chave expirada, sem crédito) **abre o circuito na hora** e não repete.
- Erro transitório repete no máximo 3 vezes, com `wait_random_exponential` (base 0,5 s, teto 4 s).
- Circuito: fechado → aberto → meio-aberto, em memória por réplica (`CircuitoStoreMemoria`).
- Prazo esgotado (`ErroPrazoEsgotado`) para na hora e vira mensagem fixa `DEADLINE`.

## Degradação: o que o cliente recebe

| Situação | O que acontece | `codigo` na resposta | `degradada` |
|---|---|---|---|
| Mensagem vazia ou acima de `tamanho_max_mensagem` | Mensagem fixa, sem LLM, sem gravar na sessão | `entrada_invalida` | não |
| Rate limit (sessão, tenant ou LLM) | Mensagem fixa; a API responde 429 com `Retry-After` | `limite` | sim |
| Prazo do tenant (30 s) estourado | Turno cancelado, a mensagem fixa é gravada no histórico | `deadline` | sim |
| Todos os provedores da cadeia falharam ou estão com circuito aberto | Mensagem fixa de indisponibilidade | `indisponivel` | sim |
| Tool com erro de negócio (ex.: pedido não encontrado) | Erro volta ao LLM como resultado de tool, que responde ao cliente sem inventar | segue normal | não |
| Guarda de valores pegou um preço ou percentual sem fonte | Uma rodada corretiva; se persistir, mensagem fixa | `erro` | sim |
| Limite de 6 chamadas ao LLM atingido | Mensagem fixa | `erro` | sim |
| Falha inesperada no turno ou fora dele (rede de segurança, `responder` nunca lança) | Mensagem fixa genérica | `erro` | sim |
| Redis indisponível | Rate limit com bucket local (fail-open); sessão sem histórico (`GuardaRedis`) | segue normal | não |
| Postgres indisponível | A tool captura a exceção e devolve `ok` falso com erro interno (`FerramentasLoja.executar`). O LLM recebe o erro e responde sem inventar o dado. Se a falha sair do turno, vira mensagem fixa `erro` | segue normal, ou `erro` | não, ou sim |

Os textos das mensagens fixas vêm de `motor/comum/mensagens.py` (`ChaveMensagem`), traduzidos pelo idioma da sessão.
Eles não passam pelo LLM, então funcionam mesmo com todos os provedores fora.

## Componentes e onde existem

| Componente | Onde está |
|---|---|
| `cli` (modo local e `--api`), `api` (`POST /v1/chat`) | `src/emporio/adaptadores/cli/__init__.py`, `src/emporio/adaptadores/api/app.py` |
| `AgenteNativo.responder` (I1 a I8, deadline, guarda, gravação) | `src/emporio/adaptadores/motor/nativo/agente.py` |
| `RateLimiter` (camadas `sessao` e `tenant`) | `src/emporio/adaptadores/memoria/rate_limit.py` e `src/emporio/adaptadores/redis/rate_limit.py` |
| `SessaoStore` (histórico, idioma, TTL de 30 min) | `src/emporio/adaptadores/memoria/sessao.py` e `src/emporio/adaptadores/redis/sessao.py` |
| `Roteador` (classificador, `CADEIAS`, `ORDEM_CLASSIFICADOR`) | `src/emporio/adaptadores/motor/nativo/roteador.py` |
| `ProvedorResiliente` (circuito, bulkhead, timeout, tenacity) | `src/emporio/adaptadores/llm/resiliencia.py` |
| `CircuitoStoreMemoria` | `src/emporio/adaptadores/memoria/circuito.py` |
| Prompt CAG (`montar_prompt`) | `src/emporio/adaptadores/motor/comum/prompt.py` |
| `FerramentasLoja` (6 tools: `buscar_produtos`, `detalhar_produto`, `simular_pagamento`, `consultar_pedido`, `status_loja`, `registrar_solicitacao`) | `src/emporio/modulos/atendimento/aplicacao.py` |
| Casos de uso (`ServicoCatalogo`, `ServicoComercial`, `ServicoPedidos`, `ServicoPoliticas`, `ServicoAtendimento`) | `src/emporio/modulos/*/aplicacao.py` |
| Repositórios (`CatalogoPostgres`…, `CatalogoCsv`…) e cache (`CatalogoComCache`) | `src/emporio/adaptadores/db/repositorios.py`, `src/emporio/adaptadores/csv/`, `src/emporio/adaptadores/redis/cache_catalogo.py` |
| Guarda de valores (`valores_nao_verificados`, `nota_correcao`) | `src/emporio/adaptadores/motor/comum/guarda.py` |
| Métricas do turno (`emporio_roteamento_total`, `emporio_llm_fallback_total` etc.) | `src/emporio/adaptadores/observabilidade/telemetria.py` |
