# Suíte de contrato da porta `MotorDeAtendimento`

Garante que **todo** motor de atendimento cumpre os invariantes I1-I8 de `docs/arquitetura.md` §3.2. Cada teste de
`test_motor_atendimento.py` roda uma vez por motor registrado: `nativo` (`AgenteNativo`), `api` (o mesmo `AgenteNativo` através de `POST /v1/chat`, via `MotorViaApi`
em `motor_via_api.py`, por ASGI em memória) e `n8n` (`AgenteN8n`, task 2.7, contra o n8n simulado de
`n8n_simulado.py`), todos sem mudar nenhum teste.

```
uv run pytest tests/contrato -v
```

Nenhum teste usa rede nem `.env`: o LLM é o `LLMFalso` roteirizável e os dados são os CSVs de `data/`.

## Arquivos

| Arquivo | Papel |
|---|---|
| `kit.py` | `KitMotor`, `EspiaoTools`, o tipo `FabricaMotor` e os tenants de teste (`tenants_ab`). |
| `motor_via_api.py` | `MotorViaApi`: implementa a porta chamando a API FastAPI (usado pela fábrica `api`). |
| `n8n_simulado.py` | `N8nSimulado`: o workflow de `deploy/n8n/` num `httpx.MockTransport` (LLM falso + tools reais por HTTP). |
| `fabricas.py` | Fábricas dos motores e o registro `MOTORES` (nome → fábrica). **Único arquivo que muda para um motor novo.** |
| `conftest.py` | Fixture `motor_factory`, parametrizada por `MOTORES`. |
| `invariantes.py` | `verificar_i1` ... `verificar_i8`: a lógica de cada invariante, só pela porta. |
| `test_motor_atendimento.py` | Um teste por invariante e por motor, mais o teste meta do motor quebrado. |

## Invariantes

| | Verificação |
|---|---|
| I1 | Mensagem válida → texto não vazio. Vazia, só espaços ou acima de `tamanho_max_mensagem` → texto fixo e **zero chamadas ao LLM**. |
| I2 | A 2ª mensagem da sessão chega ao LLM junto com a 1ª e a resposta anterior (e fica no `SessaoStore`, se o kit o expõe). |
| I3 | Tenants `emporio` e `loja-teste` (cópia com outro nome, persona e WhatsApp, num `tenants.yaml` temporário) usam o mesmo `sessao_id`: sem histórico cruzado, e cada um recebe a própria configuração (prompt e contatos nas mensagens fixas). |
| I4 | Falha transitória, exceção inesperada do LLM, roteiro esgotado e backend de tools fora do ar: nunca lança; `degradada=True`, texto fixo sem detalhe interno. |
| I5 | Deadline do tenant de 0,05 s com LLM que dormiria 30 s: volta rápido com texto fixo e `degradada=True`. |
| I6 | Com `detalhar_produto` chamada, o preço do texto é o da tool. Sem tool e com o LLM insistindo em um preço inventado, o valor não aparece no texto final. |
| I7 | Sessão em pt-BR e outra em en-US: `RespostaAtendimento.idioma` correto e mensagem fixa traduzida, também nas mensagens seguintes. |
| I8 | Duas instâncias (tenants diferentes) conversam em `asyncio.gather` com o mesmo `sessao_id`: nada de uma aparece na outra. |

## Como um motor novo (ex.: `AgenteN8n`) entra

1. Escreva uma fábrica em `fabricas.py` com a assinatura `FabricaMotor`:

   ```python
   def fabrica_n8n(tenants: Mapping[str, ConfigTenant], dir_estado: Path) -> KitMotor: ...
   ```

2. Registre em `MOTORES` (`"n8n": fabrica_n8n`). A fixture `motor_factory` passa a rodar os 8 invariantes também para ele.
3. Rode `uv run pytest tests/contrato -v` e corrija o motor, não os testes.

O que a fábrica **precisa prover** (campos de `KitMotor`):

- `motor`: a implementação, usada só pela porta `responder(ctx, sessao_id, mensagem)`. Deve respeitar os `tenants`
  recebidos (deadline, limites, contatos) e usar a data `kit.DATA_REFERENCIA` e os CSVs reais de `data/`.
- `llm`: um `LLMFalso` **roteirizável**. O motor chama este falso onde chamaria o LLM real; os testes colocam o
  roteiro (`llm.roteirizar(...)`) e leem `llm.requisicoes`. No `n8n`, o n8n simulado (`httpx.MockTransport`) repassa
  cada passo de LLM do workflow a este falso.
- `tools`: um `EspiaoTools` envolvendo a fachada real de tools. Registra os nomes executados (`tools.chamadas`) e, com
  `tools.falha` definida, simula o backend fora do ar. No `n8n`, o espião fica no lado servidor de `/v1/tools/*`.
- `sessoes` (opcional): o `SessaoStore` do motor, para verificações extras de I2.
- **Controle de deadline**: não há botão no kit; o teste passa `tenants` com `deadline_mensagem_s=0.05` e o motor
  deve usar esse valor como está (não pode ter um piso próprio maior).

A fábrica cria tudo novo a cada chamada (sem estado compartilhado), pois I8 depende disso.

## Prova de que a suíte detecta violações

`test_motor_atendimento.py` define `MotorQuebrado` (lança a exceção do LLM e devolve o preço inventado sem chamar
tool) e roda `verificar_i4` e `verificar_i6` contra ele, exigindo `AssertionError`. Esse motor **não** está em
`MOTORES`; se estivesse, a suíte ficaria vermelha.

## Limitações

- **I6 cobre só preço.** No `nativo`, a guarda de valores (premissa #58) verifica apenas valores monetários contra o
  que veio das tools, do manual ou do cliente. Estoque e status de pedido dependem do prompt ("nunca afirmar sem
  tool"), então não há como o contrato exigir isso de forma determinística; cobrir exigiria uma guarda equivalente no
  motor. O contrato foi mantido onde o `nativo` dá garantia verificável.
- I3 e I8 usam o conteúdo do prompt (nome da loja) e os contatos das mensagens fixas como sinal de configuração; um
  motor que não coloque o nome da loja no prompt do LLM falha I3 e deve ajustar a fábrica ou o prompt.
- I5 mede só que o motor volta em menos de 3 s com deadline de 0,05 s (sem relógio falso), com folga para máquinas lentas.

- **Fábrica `n8n`.** O `AgenteN8n` chama o webhook simulado; o simulado repassa cada passo de LLM ao `LLMFalso` do
  kit e executa as tools por `POST /v1/tools/{nome}` na app FastAPI real (em memória), com o token de atendente que o
  motor emite por turno. O espião de tools fica atrás da API, então `tools.chamadas` vê as chamadas HTTP. Falha do
  "workflow" vira HTTP 500, como no n8n.
- **Fábrica `api`.** Roda I1-I8 sem nenhuma exceção: o cliente envia `X-Tenant-ID` e o idioma do canal no corpo, e reconstrói a `RespostaAtendimento` do JSON (inclusive `codigo` e `retry_after_s`). Erros de borda (400/404/422) viram `RuntimeError` no teste, porque a suíte só envia requisições válidas.
