# Motor n8n (`MOTOR_ATENDIMENTO=n8n`)

O `AgenteN8n` (`src/emporio/adaptadores/motor/n8n/`) troca **só a orquestração** do atendimento por um workflow n8n
(arquitetura §3.1). O n8n decide quando chamar o LLM e quais tools usar; preço, estoque, pedidos e políticas continuam
nos módulos Python e chegam ao workflow por `POST /v1/tools/{nome}` da API. **Nenhuma regra de negócio mora no
workflow** (sem valores, percentuais, prazos ou cálculos; não há nó Code). Um teste
(`tests/adaptadores/motor/test_workflow_n8n.py`) confere isso a cada mudança no JSON.

```
cliente → API /v1/chat → AgenteN8n ──webhook──▶ n8n: Webhook → AI Agent (LLM) → Respond to Webhook
                            ▲                                   │ HTTP Request tools
                            │                                   ▼
                            └──────── resposta ◀──── API /v1/tools/{nome} (token de atendente do tenant)
```

## O que fica no Python e o que fica no workflow

| Python (`AgenteN8n`) | Workflow n8n |
|---|---|
| Validação de entrada (I1): vazia ou longa → mensagem fixa, sem chamar o n8n | Escolher tools e redigir a resposta (AI Agent + LLM) |
| Histórico da sessão (`SessaoStore`, I2), enviado inteiro a cada turno | Chamar `POST /v1/tools/{nome}` com o token recebido |
| Rate limit por sessão e tenant; idioma da sessão (I7) | Devolver `texto` e as tools chamadas (com o resultado) |
| System prompt CAG (persona, regras, manual de políticas), enviado no corpo | |
| Deadline do tenant (I5), timeout HTTP, retry e circuit breaker (§5.2) | |
| Guarda de valores (I6) com uma rodada corretiva; mensagens fixas traduzidas (I4) | |
| Token de serviço por turno, só do tenant do turno (I3) | |

## Contrato do webhook (versão 1)

`POST <N8N_WEBHOOK_URL>` com `Content-Type: application/json` e, se `N8N_WEBHOOK_SEGREDO` estiver definido,
`Authorization: Bearer <N8N_WEBHOOK_SEGREDO>` (credencial *Header Auth* do nó Webhook).

```json
{
  "versao": 1,
  "tenant_id": "emporio",
  "sessao_id": "abc-123",
  "mensagem": "Quanto custa o Takamine GD20?",
  "idioma": "pt-BR",
  "historico": [
    {"papel": "user", "texto": "oi"},
    {"papel": "assistant", "texto": "Oi! Sou a Sônia, como posso ajudar?"}
  ],
  "sistema": {"cacheavel": "<persona + regras + manual>", "dinamico": "<idioma e data de referência>"},
  "tools": {"url_base": "http://app:8000/v1/tools", "token": "<JWT>", "cabecalho_tenant": "X-Tenant-ID"},
  "prazo_s": 29.9
}
```

- `historico`: só as falas do cliente e as respostas finais (sem tools e sem notas internas). O workflow é
  **stateless**: não use memória do n8n; o histórico verdadeiro está no `SessaoStore` do Python.
- `sistema`: o mesmo prompt do motor nativo (`motor/comum/prompt.py`), para a persona e o manual de cada tenant
  chegarem ao LLM sem o workflow conhecer a configuração das lojas.
- `tools.token`: JWT HS256 de papel `atendente` **só do `tenant_id` do turno**, válido pelo deadline do tenant + 30 s,
  emitido pelo Python a cada turno com o `JWT_SECRET` da API (premissa #121: a credencial do n8n é um token de
  atendente do tenant). O workflow o repassa em `Authorization: Bearer` e manda `X-Tenant-ID: <tenant_id>` e
  `Accept-Language: <idioma>` (as tools formatam dinheiro no idioma da sessão).
- `prazo_s`: o que resta do deadline; acima disso o Python já desistiu e respondeu a mensagem fixa.

Resposta esperada (HTTP 200):

```json
{
  "texto": "O Takamine GD20 sai por R$ 2.199,00 ...",
  "tools_chamadas": [
    {"nome": "detalhar_produto", "argumentos": {"nome": "Takamine GD20"}, "resultado": "<corpo de /v1/tools>"}
  ],
  "nivel": null,
  "provedor": "gemini"
}
```

- `texto` vazio, JSON inválido ou campos com tipo errado → resposta malformada: mensagem fixa (`codigo=erro`).
- `tools_chamadas[].resultado` é o corpo que `/v1/tools/{nome}` devolveu. A guarda de valores do Python só aceita no
  texto final valores em dinheiro que vieram desses resultados, do manual ou do próprio cliente; senão faz uma rodada
  corretiva (segunda chamada ao webhook, com a resposta anterior no `historico` e a nota interna em `mensagem`) e,
  se o valor persistir, responde a mensagem fixa `valor_nao_verificado`. Também aceita só os nomes
  (`["status_loja"]`), mas aí nenhum valor dessas tools fica permitido.

Como o Python trata cada status (circuito `n8n` no `CircuitoStore`, mesma política dos LLMs):

| Situação | Repete? | Circuito | Mensagem fixa |
|---|---|---|---|
| Erro de conexão, 502, 503, 504 | sim, 1 retry dentro do deadline (o workflow não rodou) | falha transitória | `indisponivel` |
| 500 (execução do workflow quebrou) | não (repetir gastaria LLM e repetiria tools) | falha transitória | `indisponivel` |
| 401, 403, 404 (segredo errado, workflow inativo) | não | falha permanente (abre na hora) | `indisponivel` |
| Outro status (400, 422...) | não | sucesso (o n8n está de pé) | `erro` |
| 200 malformado | não | falha transitória | `erro` |
| Deadline estourado | não | não conta | `deadline` |
| Circuito aberto | não chama o webhook | | `indisponivel` |

## Variáveis de ambiente (API)

| Variável | Obrigatória | Para quê |
|---|---|---|
| `MOTOR_ATENDIMENTO=n8n` | sim | Seleciona o `AgenteN8n`. |
| `N8N_WEBHOOK_URL` | sim | URL de produção do nó Webhook (ex.: `http://n8n:5678/webhook/emporio-atendimento`). Sem ela a aplicação não monta (erro de configuração claro). |
| `JWT_SECRET` | sim | O motor emite com ele o token de serviço das tools; a API valida com o mesmo segredo. |
| `N8N_WEBHOOK_SEGREDO` | recomendada | Valor do `Authorization: Bearer` que o nó Webhook confere. Sem ela, o webhook fica aberto (aviso no log). |
| `N8N_URL_API` | não (`http://app:8000`) | Base da API **vista de dentro do n8n**; o workflow chama `<N8N_URL_API>/v1/tools/{nome}`. |

Com `MOTOR_ATENDIMENTO=n8n` a API não exige chave de LLM: o LLM fica na credencial do n8n.

## Importar o workflow de exemplo

1. No n8n (1.x com os nós LangChain), **Workflows → Import from File** e escolha `workflow-atendimento.json`.
2. Credenciais:
   - nó **Webhook**: *Header Auth* com nome `Authorization` e valor `Bearer <N8N_WEBHOOK_SEGREDO>`;
   - nó **Gemini**: chave da Google AI (ou troque o nó de modelo por Groq/Anthropic; o resto não muda).
3. Ative o workflow e copie a *Production URL* do nó Webhook para `N8N_WEBHOOK_URL` da API.
4. Confirme que o n8n alcança a API em `N8N_URL_API` (no compose, mesma rede do serviço `app`).

O workflow tem: **Webhook** (POST, *Respond: Using 'Respond to Webhook' node*) → **AI Agent** (system message =
`sistema.cacheavel` + `sistema.dinamico` + a conversa anterior em texto; `maxIterations` 6;
`returnIntermediateSteps` ligado) → **Respond to Webhook** (monta `texto` e `tools_chamadas` a partir de `output` e
`intermediateSteps`). Uma **HTTP Request tool** por tool da loja, todas `POST {{tools.url_base}}/{nome}` com
`{"argumentos": <decidido pelo LLM via $fromAI>, "session_id": <sessao_id>}` e `neverError` ligado (o corpo de erro
da tool, ex. 422 com o campo inválido, volta ao LLM para ele se corrigir). As descrições das tools no workflow são
curtas e sem valores; a fonte da verdade dos argumentos é `GET /v1/tools`.

## Limitações conhecidas

- O nó AI Agent não recebe uma lista de mensagens sem usar memória do n8n; por isso o exemplo coloca a conversa
  anterior como texto no system message. O bloco `sistema.cacheavel` continua sendo o prefixo (prompt caching do
  provedor ainda pode valer para ele).
- A guarda de valores confia nos resultados que o workflow relata em `tools_chamadas`. Um registro do lado da API
  (resultados de `/v1/tools` por sessão, conferidos pelo motor) fecharia esse ponto; ficou como melhoria futura.
- O exemplo não foi executado num n8n real nesta entrega; a suíte de contrato roda o `AgenteN8n` contra um n8n
  simulado (`tests/contrato/n8n_simulado.py`) que segue este contrato e chama as tools reais da API.
- `nivel` vem `null` do exemplo (o roteamento por complexidade é do motor nativo).
